#!/usr/bin/env python3
"""publish_snapshot.py — stage a deployable snapshot of a consumer's agents and KBs.

WHY THIS EXISTS
  The MCP service serves Claude Code and Codex agents plus Rosetta knowledge graphs from a
  static asset tree. Today every device needs a full consumer checkout (for this machine:
  ``~/.claude/agents`` is a symlink into the consumer's ``agents/``), because the files only
  exist there. This script turns one consumer checkout into one immutable, inspectable
  *snapshot* that a Worker can serve and a device can sync, with no consumer checkout on the
  device.

WHAT IT WRITES (the stage is Wrangler's ``assets.directory``)
  ``manifest.json``         snapshot identity + agent/KB catalog + excluded counts
  ``files.json``            sha256 + bytes for every staged payload file (device sync reads this)
  ``scan-result.json``      the credential-shaped-string scan record for the owner
  ``targets/claude/<agent>/...``   the consumer's own Claude projection, verbatim
  ``targets/codex/<agent>.toml``   the consumer's own Codex projection, path line normalized
  ``kb/<slug>/fact-graph.json``    each graph from the consumer, byte for byte

WHY THE CONSUMER'S OWN PROJECTOR RUNS
  The projection rules (frontmatter, tool mapping, model policy, TOML escaping) already have
  one home: the *consumer's* ``scripts/agentshare.py``. This script never re-implements them
  and never hardcodes a consumer path — ``--root`` names the checkout and ``scripts/
  agentshare.py`` inside it is required, or the run fails closed. agentshare is invoked once
  per target with an explicit ``--target`` (never bare, which would project all six harnesses
  and their side effects), writing into a temporary ``HOME`` so nothing lands in the real
  ``~/.claude`` or ``~/.codex``.

WHY THE CODEX PATH LINE IS NORMALIZED
  agentshare stamps ``# Generated from <absolute source path>`` at the top of every Codex TOML
  (``agentshare.py:661``). That path differs per checkout, so an unnormalized line makes the
  same commit produce different snapshot ids and ships a home path to devices. The line is
  rewritten to the consumer-relative path (``agents/<name>``); the provenance comment stays.

WHY THE SCAN GATES THE STAGE
  Prompts leave the machine. Before a stage can be deployed it is scanned for
  credential-shaped strings (``sk-``, ``ghp_``, ``AKIA``, ``BEGIN PRIVATE KEY``). Every staged
  payload file is read, so documents vendored into ``docs/`` later are covered with no extra
  wiring. A finding is recorded in ``scan-result.json``, named on stderr, and *invalidates*
  the stage (``manifest.json``/``files.json`` are removed) so no deploy can proceed from it.
  ``--scan-only`` re-runs exactly that gate over an existing stage.

AC-5 NOTE: only paths this tool injects are rewritten. Absolute paths that already exist in
prompt bodies are content, and rewriting them here would break byte parity with agentshare;
vendored-document references are a separate task's contract.

Usage:
    python3 publish_snapshot.py --root <checkout> --targets claude,codex --stage worker/content
    python3 publish_snapshot.py --stage worker/content --scan-only
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "rosetta"))
from version_label import kb_edition  # noqa: E402  (shared home for the kb_version label)

SCHEMA = 1
DEFAULT_STAGE = "worker/content"
DEFAULT_TARGETS = ("claude", "codex")
# Only the two targets whose output is a plain file tree. opencode/grok/droid have harness side
# effects (opencode.json model unpin, ~/.grok/agents cleanup) that a file mirror cannot
# reproduce, so they are refused rather than silently approximated.
SUPPORTED_TARGETS = ("claude", "codex")
TARGET_DEST = {"claude": ".claude/agents", "codex": ".codex/agents"}
EXCLUDE_SUFFIXES = ("*.bak", "*.log")
EXCLUDE_DIRS = (".history",)
KB_RELDIR = Path(".rosetta/docs")
GENERATED_LINE = "# Generated from "
METADATA_FILES = ("manifest.json", "files.json", "scan-result.json")
PAYLOAD_DIRS = ("targets", "kb", "docs")

# Credential-shaped strings. Each rule demands *key material*, not just a lookalike prefix:
#   sk-      a provider secret needs 20+ consecutive alnum characters, so prose such as
#            "risk-assessment-methodology" is not a finding.
#   ghp_     GitHub personal access tokens are 36 alnum characters.
#   AKIA     AWS access key ids are exactly AKIA + 16 upper-case characters.
#   PRIVATE KEY  a PEM *header* alone is documentation ("-----BEGIN PRIVATE KEY----- / KEY
#            DATA GOES HERE"); a real block carries 60+ base64 characters of body.
CREDENTIAL_PATTERNS = ("sk-", "ghp_", "AKIA", "BEGIN PRIVATE KEY")
CREDENTIAL_RULES: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("sk-", re.compile(rb"sk-[A-Za-z0-9]{20,}")),
    ("sk-", re.compile(rb"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("sk-", re.compile(rb"sk-proj-[A-Za-z0-9_-]{20,}")),
    ("ghp_", re.compile(rb"ghp_[A-Za-z0-9]{20,}")),
    ("AKIA", re.compile(rb"AKIA[0-9A-Z]{16}")),
    (
        "BEGIN PRIVATE KEY",
        re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----[\s\\]{0,80}[A-Za-z0-9+/]{60,}"),
    ),
)


class PublishError(RuntimeError):
    """A failure this tool reports and refuses to work around."""


# ── small helpers ──────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _log(message: str) -> None:
    print(f"[publish] {message}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stage_path(raw: str) -> Path:
    stage = Path(raw).expanduser()
    if not stage.is_absolute():
        stage = Path.cwd() / stage
    return stage.resolve()


def _relative_to_root(path: Path, root: Path) -> str | None:
    for base in (root, root.resolve()):
        try:
            return path.relative_to(base).as_posix()
        except ValueError:
            continue
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def consumer_ref(root: Path) -> str | None:
    """Short git sha of the consumer checkout, or None when it is not a git work tree."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    sha = proc.stdout.strip()
    return sha if proc.returncode == 0 and sha else None


def plugin_version() -> str:
    manifest = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
    try:
        return str(json.loads(manifest.read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError):
        return "unknown"


def load_agentshare(root: Path) -> ModuleType:
    """Import the consumer's projector so frontmatter rules are not duplicated here."""
    path = root / "scripts" / "agentshare.py"
    if not path.is_file():
        raise PublishError(
            f"{root}: no scripts/agentshare.py — the publisher projects agents with the "
            "consumer's own projector (expected at scripts/agentshare.py) and will not guess "
            "a projection"
        )
    spec = importlib.util.spec_from_file_location("_consumer_agentshare", path)
    if spec is None or spec.loader is None:
        raise PublishError(f"{path}: cannot be imported as a module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "discover_agents"):
        raise PublishError(f"{path}: does not expose discover_agents(), so agents cannot be catalogued")
    return module


# ── staging ────────────────────────────────────────────────────────────


def reset_stage(stage: Path) -> None:
    """Clear only what this tool owns, so a stale target tree can never ride along."""
    stage.mkdir(parents=True, exist_ok=True)
    for name in PAYLOAD_DIRS:
        victim = stage / name
        if victim.exists():
            shutil.rmtree(victim)
    for name in METADATA_FILES:
        victim = stage / name
        if victim.exists():
            victim.unlink()


def _copy_projection(src: Path, dest: Path) -> tuple[int, dict[str, int]]:
    """Copy one projected tree, excluding the entries the manifest counts as excluded."""
    excluded = {glob: 0 for glob in EXCLUDE_SUFFIXES}
    excluded.update({f"{name}/": 0 for name in EXCLUDE_DIRS})
    copied = 0
    for dirpath, dirnames, filenames in os.walk(src):
        here = Path(dirpath)
        rel = here.relative_to(src)
        keep: list[str] = []
        for name in sorted(dirnames):
            if name in EXCLUDE_DIRS:
                excluded[f"{name}/"] += 1
                continue
            keep.append(name)
        dirnames[:] = keep
        for name in sorted(filenames):
            sink = next((glob for glob in EXCLUDE_SUFFIXES if name.endswith(glob[1:])), None)
            if sink is not None:
                excluded[sink] += 1
                continue
            target = dest / rel / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(here / name, target)
            copied += 1
    return copied, excluded


def normalize_generated_paths(codex_dir: Path, root: Path) -> int:
    """Rewrite ``# Generated from <abs path>`` to a consumer-relative path.

    Returns how many files were rewritten. The line is the only path this tool injects into
    the snapshot, and an absolute one would both leak a checkout location and make the
    snapshot id depend on where the consumer was cloned.
    """
    rewritten = 0
    for toml_path in sorted(codex_dir.glob("*.toml")):
        text = toml_path.read_text(encoding="utf-8")
        lines = text.split("\n")
        if not lines or not lines[0].startswith(GENERATED_LINE):
            continue
        original = lines[0][len(GENERATED_LINE) :].strip()
        relative = _relative_to_root(Path(original), root)
        if relative is None:
            # Outside the consumer (should not happen): keep the comment, drop the location.
            relative = f"agents/{Path(original).name or toml_path.stem}"
        lines[0] = GENERATED_LINE + relative
        toml_path.write_text("\n".join(lines), encoding="utf-8")
        rewritten += 1
    return rewritten


def stage_target(
    *,
    root: Path,
    target: str,
    agentshare: Path,
    stage: Path,
    home: Path,
) -> tuple[int, dict[str, int], int]:
    """Run the consumer's projector for one target and copy its tree into the stage."""
    argv = [sys.executable, str(agentshare), "--target", target]
    _log(f"agentshare: {' '.join(argv)}")
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["BRAIN_ROOT"] = str(root)
    proc = subprocess.run(argv, cwd=str(root), env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        raise PublishError(
            f"agentshare --target {target} exited {proc.returncode}: "
            f"{(proc.stderr or proc.stdout).strip().splitlines()[-1:] or 'no output'}"
        )
    produced = home / TARGET_DEST[target]
    if not produced.is_dir():
        raise PublishError(
            f"agentshare --target {target} exited 0 but wrote no tree at {TARGET_DEST[target]}; "
            "refusing to publish a snapshot with zero agents"
        )
    dest = stage / "targets" / target
    copied, excluded = _copy_projection(produced, dest)
    if copied == 0:
        raise PublishError(
            f"agentshare --target {target} produced zero agents; refusing to publish an empty snapshot"
        )
    rewritten = normalize_generated_paths(dest, root) if target == "codex" else 0
    _log(
        f"staged target {target}: {copied} files "
        + " ".join(f"{key}={value}" for key, value in excluded.items())
        + (f" normalized_path_lines={rewritten}" if target == "codex" else "")
    )
    return copied, excluded, rewritten


def stage_kbs(root: Path, stage: Path) -> list[dict]:
    """Copy every fact-graph in the consumer's registry layout, byte for byte."""
    docs = root / KB_RELDIR
    entries: list[dict] = []
    if not docs.is_dir():
        print(f"[publish] WARNING: {docs} is absent; the snapshot carries no knowledge bases", file=sys.stderr)
        return entries
    for graph_path in sorted(docs.glob("*/fact-graph.json")):
        slug = graph_path.parent.name
        relative = Path("kb") / slug / "fact-graph.json"
        dest = stage / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(graph_path, dest)
        try:
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PublishError(f"{graph_path}: not readable as JSON ({exc})") from exc
        entries.append(
            {
                "slug": slug,
                "kb_version": kb_edition(graph),
                "bytes": dest.stat().st_size,
                "path": relative.as_posix(),
            }
        )
    _log(f"staged {len(entries)} knowledge base(s) from {KB_RELDIR}")
    return entries


# ── inventory + snapshot identity ──────────────────────────────────────


def payload_files(stage: Path) -> dict[str, Path]:
    """Every staged file that ships to a device, keyed by stage-relative POSIX path."""
    found: dict[str, Path] = {}
    for name in PAYLOAD_DIRS:
        base = stage / name
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file():
                found[path.relative_to(stage).as_posix()] = path
    return dict(sorted(found.items()))


def snapshot_identity(files: dict[str, Path]) -> tuple[str, dict[str, dict]]:
    """``(snapshot_id, inventory)`` — id is the first 12 hex of sha256 over sorted lines.

    Path-independent by construction: the same content staged at any location, from a
    consumer cloned anywhere, hashes the same.
    """
    inventory: dict[str, dict] = {}
    lines: list[str] = []
    for relative, path in files.items():
        digest = sha256_file(path)
        size = path.stat().st_size
        inventory[relative] = {"sha256": digest, "bytes": size}
        lines.append(f"{relative} {digest}\n")
    snapshot_id = hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()[:12]
    return snapshot_id, inventory


# ── credential scan ────────────────────────────────────────────────────


def _mask(match: bytes) -> str:
    """Never write a second copy of a secret into the scan record."""
    head = match[:4].decode("utf-8", "replace")
    return f"{head}{'*' * 8} ({len(match)} bytes)"


def scan_stage(stage: Path, snapshot_id: str | None) -> dict:
    """Scan every staged payload file for credential-shaped strings; record the result."""
    files = payload_files(stage)
    findings: list[dict] = []
    for relative, path in files.items():
        raw = path.read_bytes()
        for pattern, rule in CREDENTIAL_RULES:
            for match in rule.finditer(raw):
                findings.append(
                    {
                        "path": relative,
                        "pattern": pattern,
                        "line": raw.count(b"\n", 0, match.start()) + 1,
                        "match_preview": _mask(match.group(0)),
                    }
                )
    result = {
        "schema": SCHEMA,
        "snapshot_id": snapshot_id,
        "scanned_at": _now(),
        "patterns": list(CREDENTIAL_PATTERNS),
        "rules": [{"pattern": pattern, "regex": rule.pattern.decode("utf-8")} for pattern, rule in CREDENTIAL_RULES],
        "files_scanned": len(files),
        "findings": findings,
        "status": "findings" if findings else "clean",
        "metadata_excluded_from_scan": list(METADATA_FILES),
    }
    stage.mkdir(parents=True, exist_ok=True)
    (stage / "scan-result.json").write_text(
        json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _log(
        f"credential scan: {len(CREDENTIAL_PATTERNS)} patterns, {len(files)} files, "
        f"{len(findings)} finding(s) -> scan-result.json"
    )
    return result


def report_findings(findings: list[dict]) -> None:
    for finding in findings:
        print(
            f"[publish] CREDENTIAL-SHAPED {finding['pattern']} in "
            f"{finding['path']}:{finding['line']}",
            file=sys.stderr,
        )
    print(
        f"[publish] {len(findings)} credential-shaped string(s) found; the stage is NOT deployable",
        file=sys.stderr,
    )


def invalidate_stage(stage: Path) -> None:
    """Remove the deployable pointer so a scan failure cannot be deployed by accident."""
    for name in ("manifest.json", "files.json"):
        victim = stage / name
        if victim.exists():
            victim.unlink()


# ── publish ────────────────────────────────────────────────────────────


def _parse_targets(raw: str) -> tuple[str, ...]:
    requested = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not requested:
        raise PublishError("--targets is empty; name at least one of " + ", ".join(SUPPORTED_TARGETS))
    unsupported = [name for name in requested if name not in SUPPORTED_TARGETS]
    if unsupported:
        raise PublishError(
            f"unsupported target(s) {', '.join(unsupported)}: this publisher stages only "
            f"{', '.join(SUPPORTED_TARGETS)} because the other targets carry harness side "
            "effects a file mirror cannot reproduce"
        )
    return requested


def _agent_catalog(
    *,
    agents: list[dict],
    targets: tuple[str, ...],
    stage: Path,
) -> list[dict]:
    catalog: list[dict] = []
    for agent in sorted(agents, key=lambda item: str(item["name"])):
        name = str(agent["name"])
        if "claude" in targets:
            relative = Path("targets") / "claude" / name / "agent.md"
        else:
            relative = Path("targets") / "codex" / f"{name}.toml"
        staged = stage / relative
        if not staged.is_file():
            raise PublishError(
                f"agentshare staged no file for agent {name!r} (expected {relative.as_posix()}); "
                "the projection and the source catalog disagree"
            )
        catalog.append(
            {
                "name": name,
                "description": str(agent.get("description") or "")[:300],
                "model": str(agent.get("model") or ""),
                "bytes": staged.stat().st_size,
                "path": relative.as_posix(),
            }
        )
    return catalog


def publish(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser()
    if not root.is_dir():
        raise PublishError(f"--root {root}: not a directory")
    root = root.resolve()
    targets = _parse_targets(args.targets)
    stage = _stage_path(args.stage)

    _log(f"root: {root}")
    _log(f"stage: {stage}")
    agentshare = root / "scripts" / "agentshare.py"
    module = load_agentshare(root)
    source_agents = module.discover_agents(root / "agents")
    if not source_agents:
        raise PublishError(
            f"{root}: agentshare reports zero agents under agents/; refusing to publish an "
            "empty snapshot"
        )
    _log(f"source agents: {len(source_agents)}")

    reset_stage(stage)
    totals: dict[str, dict] = {}
    excluded: dict[str, int] = {glob: 0 for glob in EXCLUDE_SUFFIXES}
    excluded.update({f"{name}/": 0 for name in EXCLUDE_DIRS})
    normalized_lines = 0
    with tempfile.TemporaryDirectory(prefix="publish-home-") as home_str:
        home = Path(home_str)
        for target in targets:
            copied, per_target, rewritten = stage_target(
                root=root, target=target, agentshare=agentshare, stage=stage, home=home
            )
            totals[target] = {"dest": TARGET_DEST[target], "files": copied}
            for key, value in per_target.items():
                excluded[key] += value
            normalized_lines += rewritten

    if "claude" in targets:
        staged_dirs = [p for p in (stage / "targets" / "claude").iterdir() if (p / "agent.md").is_file()]
        if len(staged_dirs) != len(source_agents):
            raise PublishError(
                f"agentshare staged {len(staged_dirs)} claude agents but the source has "
                f"{len(source_agents)}; refusing to publish a partial snapshot"
            )

    kbs = stage_kbs(root, stage)
    files = payload_files(stage)
    if not files:
        raise PublishError(f"{stage}: nothing was staged")
    snapshot_id, inventory = snapshot_identity(files)
    scan = scan_stage(stage, snapshot_id)

    if scan["status"] != "clean":
        report_findings(scan["findings"])
        invalidate_stage(stage)
        _log(f"snapshot_id: {snapshot_id} (scan failed; no manifest written)")
        return 1

    files_doc = {"schema": SCHEMA, "snapshot_id": snapshot_id, "files": inventory}
    (stage / "files.json").write_text(
        json.dumps(files_doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    published_at = _now()
    manifest = {
        "schema": SCHEMA,
        "snapshot_id": snapshot_id,
        "published_at": published_at,
        "consumer_ref": consumer_ref(root),
        "plugin_version": plugin_version(),
        "targets": totals,
        "agents": _agent_catalog(agents=source_agents, targets=targets, stage=stage),
        "kbs": kbs,
        "excluded": excluded,
        "scan": {
            "status": scan["status"],
            "path": "scan-result.json",
            "patterns": scan["patterns"],
            "files_scanned": scan["files_scanned"],
            "findings": len(scan["findings"]),
            "scanned_at": scan["scanned_at"],
        },
        "files_count": len(files),
        "generated": list(METADATA_FILES),
        "normalized_path_lines": normalized_lines,
    }
    (stage / "manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    _log(f"snapshot_id: {snapshot_id}")
    _log(f"agents: {len(manifest['agents'])}")
    _log(f"kbs: {len(manifest['kbs'])}")
    _log(f"files: {len(files)}")
    _log("excluded: " + json.dumps(excluded, sort_keys=False))
    _log(f"manifest: {stage / 'manifest.json'}")
    return 0


def scan_only(args: argparse.Namespace) -> int:
    stage = _stage_path(args.stage)
    if not stage.is_dir():
        raise PublishError(f"--stage {stage}: not a directory; nothing to scan")
    files = payload_files(stage)
    if not files:
        raise PublishError(f"{stage}: no staged payload files to scan")
    recorded = None
    manifest_path = stage / "manifest.json"
    if manifest_path.is_file():
        try:
            recorded = json.loads(manifest_path.read_text(encoding="utf-8")).get("snapshot_id")
        except ValueError:
            recorded = None
    payload_id, _ = snapshot_identity(files)
    _log(f"stage: {stage}")
    scan = scan_stage(stage, recorded or payload_id)
    if scan["status"] != "clean":
        report_findings(scan["findings"])
        invalidate_stage(stage)
        return 1
    if recorded == payload_id:
        _log(f"stage is deployable: {payload_id}")
    else:
        _log(
            f"scan clean: {payload_id}"
            + ("" if recorded is None else f" (manifest records {recorded})")
            + "; no deployable manifest matches this payload — rerun the publisher"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--root",
        help="consumer checkout to publish (the folder holding scripts/agentshare.py, agents/, "
        ".rosetta/docs/); required unless --scan-only",
    )
    parser.add_argument(
        "--targets",
        default=",".join(DEFAULT_TARGETS),
        help=f"comma-separated projection targets (default {','.join(DEFAULT_TARGETS)})",
    )
    parser.add_argument(
        "--stage",
        default=DEFAULT_STAGE,
        help=f"asset stage directory written for the Worker (default {DEFAULT_STAGE})",
    )
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="re-run the credential scan over an existing stage and exit (the pre-deploy gate)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.scan_only:
            return scan_only(args)
        if not args.root:
            parser.error("--root is required unless --scan-only")
        return publish(args)
    except PublishError as exc:
        print(f"[publish] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
