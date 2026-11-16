#!/usr/bin/env python3
"""publish_snapshot.py — stage a deployable snapshot of a consumer's agents and KBs.

WHY THIS EXISTS
  The MCP service serves Claude Code and Codex agents plus Rosetta knowledge graphs from a
  static asset tree. Today every device needs a full consumer checkout (for this machine:
  ``~/.claude/agents`` is a symlink into the consumer's ``agents/``), because the files only
  exist there. This script turns one consumer checkout into one immutable, inspectable
  *snapshot* that a Worker can serve and a device can sync, with no consumer checkout on the
  device.

WHAT IT WRITES (the stage is Wrangler's ``assets.directory`` and the upload source)
  ``manifest.json``         snapshot identity + agent/KB catalog + excluded counts + side effects
  ``files.json``            sha256 + bytes for every staged payload file (device sync reads this)
  ``scan-result.json``      the credential-shaped-string scan record for the owner
  ``targets/claude/<agent>/...``   the consumer's own Claude projection, verbatim
  ``targets/codex/<agent>.toml``   the consumer's own Codex projection, path line normalized
  ``targets/<t>/<agent>.*``        the OpenCode, Grok and Droid projections (flat files)
  ``kb/<slug>/fact-graph.json``    each graph from the consumer, byte for byte
  ``docs/brain/<path>``            every document a prompt cites, with ``--vendor-docs``

WHAT IT PUBLISHES TO R2 (``--r2-bucket``)
  ``blobs/<sha256>``                        every payload file, content-addressed, shared by snapshots
  ``snapshots/<snapshot_id>/files.json``    per-path sha256 inventory
  ``snapshots/<snapshot_id>/manifest.json``
  ``current.json``                          the pointer, written **last**

  A vendored ``docs/`` tree is payload like any other: its files are blobs, its paths are in the
  per-snapshot inventory, and ``current.json`` counts them, so ``--r2-bucket`` and
  ``--vendor-docs`` compose with no extra wiring.

WHY THE POINTER IS WRITTEN LAST
  The Worker follows ``current.json`` into one snapshot, so the pointer flip is the swap: R2 keeps
  a write once the put resolves, which makes the flip atomic. A reader during a publish therefore
  sees the old snapshot complete or the new snapshot complete, never a manifest naming a blob that
  does not exist yet. A publish writes objects and never touches the Worker, so a prompt-only
  publish creates no Worker version. The upload shells out to ``wrangler r2 object put`` because
  R2 has no bulk command; R2 S3 keys are never read from a file, never logged, never staged and
  never handed to the child process (wrangler carries its own OAuth credential).

WHY THE CONSUMER'S OWN PROJECTOR RUNS
  The projection rules (frontmatter, tool mapping, model policy, TOML escaping) already have
  one home: the *consumer's* ``scripts/agentshare.py``. This script never re-implements them
  and never hardcodes a consumer path — ``--root`` names the checkout and ``scripts/
  agentshare.py`` inside it is required, or the run fails closed. agentshare is invoked once
  per target with an explicit ``--target`` (never bare, which would project all six harnesses
  and their side effects), writing into a temporary ``HOME`` so nothing lands in the real
  ``~/.claude`` or ``~/.codex``.

HARNESS SIDE EFFECTS ARE DECLARED, NEVER SILENT
  Two targets do more on a device than write a file tree: the OpenCode target unpins
  ``agent.<name>.model`` in ``opencode.json``, and the Grok target rewrites
  ``[subagents.models]`` in ``config.toml`` and deletes its own stale profiles from the folder
  Grok scans. Each is declared in ``manifest.json`` under ``targets.<t>.side_effects`` with a
  ``handling`` of ``sync-applies`` (the device sync repeats it) or ``not-applied`` (the device
  is told, in the sync summary, that it was not done and why). ``grok`` is staged into
  ``.grok/agent-library`` — never ``.grok/agents``, the folder Grok scans.

WHY DOC VENDORING IS OPT-IN
  ``--vendor-docs`` vendors the documents prompts cite and rewrites the citations to
  ``~/.rosetta/share/brain/…`` (see ``doc_refs.py``), which clears the dangling references a
  brain-less device otherwise inherits. It is a flag because it changes the prompt text: a
  plain publish stays byte-identical to the consumer's own projection, which is what the
  parity proof pins. Vendored documents are payload files, so the credential scan covers them
  with no extra wiring.

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
    python3 publish_snapshot.py --root <checkout> --targets claude,grok --vendor-docs --stage /tmp/stage
    python3 publish_snapshot.py --root <checkout> --stage worker/content --r2-bucket <bucket> --r2-local
    python3 publish_snapshot.py --stage worker/content --r2-bucket <bucket> --upload-only
    python3 publish_snapshot.py --stage worker/content --scan-only
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "rosetta"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from version_label import kb_edition  # noqa: E402  (shared home for the kb_version label)
import doc_refs  # noqa: E402  (sibling: doc vendoring + citation rewrite)

SCHEMA = 1
DEFAULT_STAGE = "worker/content"
DEFAULT_TARGETS = ("claude", "codex")
# Every target agentshare can project into a plain file tree. Each `dest` is what agentshare
# itself uses, relative to $HOME, so a synced device lands files where its harness looks.
# grok's dest is `agent-library`, NOT `agents`: Grok scans `~/.grok/agents/` and lists only the
# first 64 agents it finds there, so agentshare writes to the library folder instead.
SUPPORTED_TARGETS = ("claude", "codex", "opencode", "droid", "grok")
TARGET_DEST = {
    "claude": ".claude/agents",
    "codex": ".codex/agents",
    "opencode": ".config/opencode/agents",
    "droid": ".factory/droids",
    "grok": ".grok/agent-library",
}
TARGET_SUFFIX = {"claude": ".md", "codex": ".toml", "opencode": ".md", "droid": ".md", "grok": ".md"}
# What a target's projector does BESIDES writing its file tree. A file snapshot cannot carry a
# config rewrite, so each one is declared and either repeated by the device sync
# (`sync-applies`) or reported to the device as not done (`not-applied`). Nothing here is silent.
TARGET_SIDE_EFFECTS: dict[str, tuple[dict[str, str], ...]] = {
    "opencode": (
        {
            "name": "opencode-agent-model-unpin",
            "path": ".config/opencode/opencode.json",
            "handling": "sync-applies",
            "reason": (
                "agentshare deletes agent.<name>.model for every published agent so OpenCode "
                "subagents inherit the parent session's model; the device sync repeats exactly "
                "that key removal on the device's own opencode.json"
            ),
        },
    ),
    "grok": (
        {
            "name": "grok-subagents-models-table",
            "path": ".grok/config.toml",
            "handling": "not-applied",
            "reason": (
                "agentshare rewrites the [subagents.models] table of the device's grok "
                "config.toml; the snapshot carries agent profiles only, so the device's config "
                "keeps its own tables (re-run agentshare locally to pin per-type models)"
            ),
        },
        {
            "name": "grok-legacy-agents-cleanup",
            "path": ".grok/agents",
            "handling": "not-applied",
            "reason": (
                "agentshare deletes its own stale profiles from the folder Grok scans "
                "(~/.grok/agents); the sync never deletes a file it did not write, and writing "
                "there is what the 64-agent scan limit punishes"
            ),
        },
    ),
}
EXCLUDE_SUFFIXES = ("*.bak", "*.log")
EXCLUDE_DIRS = (".history",)
KB_RELDIR = Path(".rosetta/docs")
GENERATED_LINE = "# Generated from "
METADATA_FILES = ("manifest.json", "files.json", "scan-result.json")
PAYLOAD_DIRS = ("targets", "kb", "docs")

# ── R2 layout (the Worker's store; see worker/src/store.ts) ────────────
#   blobs/<sha256>                     immutable payload bytes, shared by snapshots
#   snapshots/<snapshot_id>/files.json per-path sha256 inventory
#   snapshots/<snapshot_id>/manifest.json
#   current.json                       the pointer, written LAST
R2_POINTER_KEY = "current.json"
R2_BLOB_PREFIX = "blobs/"
R2_SNAPSHOT_PREFIX = "snapshots/"
R2_SCHEMA = 1
# Credentials that must never be read from a file, logged, staged, or handed to a
# child process: the upload shells out to wrangler, which holds its own OAuth
# credential, so the S3 keys an operator exports are never needed here.
R2_SECRET_ENV = ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")

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


def _clear_immutable_flags(path: Path) -> None:
    """Drop macOS ``uchg`` flags under ``path`` so the temp HOME can be removed.

    The OpenCode target plants an immutable sentinel (``chflags uchg``) to stop other tools
    recreating a bridge symlink over its agent folder. Reading and copying it is fine; removing
    the temporary tree it lives in is not, so the flag is cleared on the throwaway copy.
    """
    if sys.platform != "darwin" or not path.exists():
        return
    subprocess.run(["chflags", "-R", "nouchg", str(path)], check=False, capture_output=True)


def _side_effect_records(target: str, home: Path) -> list[dict]:
    """The manifest's record of what this target's projector did beyond the file tree."""
    records: list[dict] = []
    for declared in TARGET_SIDE_EFFECTS.get(target, ()):
        observed = (home / declared["path"]).exists()
        record = dict(declared)
        record["observed_in_projector_home"] = observed
        records.append(record)
    return records


def stage_target(
    *,
    root: Path,
    target: str,
    agentshare: Path,
    stage: Path,
    home: Path,
) -> dict:
    """Run the consumer's projector for one target and copy its tree into the stage."""
    argv = [sys.executable, str(agentshare), "--target", target]
    _log(f"agentshare: {' '.join(argv)}")
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["BRAIN_ROOT"] = str(root)
    # A projector target that honours an extra config-dir variable must still land inside the
    # sandbox HOME: an inherited GROK_HOME would silently stage the machine's real ~/.grok.
    for name in ("GROK_HOME", "ROGUEONE_SHARE"):
        env.pop(name, None)
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
    return {
        "files": copied,
        "excluded": excluded,
        "normalized": rewritten,
        "side_effects": _side_effect_records(target, home),
    }


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
            f"unsupported target(s) {', '.join(unsupported)}: this publisher stages "
            f"{', '.join(SUPPORTED_TARGETS)}"
        )
    return requested


def target_agent_path(name: str, target: str) -> Path:
    """Where one agent's projection lands inside ``targets/<target>/``."""
    if target == "claude":
        return Path("targets") / "claude" / name / "agent.md"
    return Path("targets") / target / f"{name}{TARGET_SUFFIX[target]}"


def staged_agent_count(stage: Path, target: str) -> int:
    """How many agents a staged target tree holds (claude is one directory per agent)."""
    base = stage / "targets" / target
    if target == "claude":
        return sum(1 for path in base.iterdir() if (path / "agent.md").is_file())
    return sum(1 for path in base.glob(f"*{TARGET_SUFFIX[target]}") if path.is_file())


def _agent_catalog(
    *,
    agents: list[dict],
    targets: tuple[str, ...],
    stage: Path,
) -> list[dict]:
    """One entry per agent: its prompt for every staged target, plus a primary path.

    ``path`` stays the Claude prompt when Claude is published (that is the prompt the MCP
    ``get_agent`` tool serves); ``paths`` names the prompt in each staged target so a device and
    an operator can both find what they need.
    """
    catalog: list[dict] = []
    primary = "claude" if "claude" in targets else targets[0]
    for agent in sorted(agents, key=lambda item: str(item["name"])):
        name = str(agent["name"])
        paths: dict[str, str] = {}
        for target in targets:
            relative = target_agent_path(name, target)
            if not (stage / relative).is_file():
                raise PublishError(
                    f"agentshare staged no file for agent {name!r} (expected {relative.as_posix()}); "
                    "the projection and the source catalog disagree"
                )
            paths[target] = relative.as_posix()
        staged = stage / paths[primary]
        catalog.append(
            {
                "name": name,
                "description": str(agent.get("description") or "")[:300],
                "model": str(agent.get("model") or ""),
                "target": primary,
                "bytes": staged.stat().st_size,
                "path": paths[primary],
                "paths": paths,
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
        try:
            for target in targets:
                staged = stage_target(
                    root=root, target=target, agentshare=agentshare, stage=stage, home=home
                )
                totals[target] = {
                    "dest": TARGET_DEST[target],
                    "files": staged["files"],
                    "side_effects": staged["side_effects"],
                }
                for key, value in staged["excluded"].items():
                    excluded[key] += value
                normalized_lines += staged["normalized"]
        finally:
            # The OpenCode target plants an immutable sentinel; clear the flag before the
            # throwaway HOME is removed, or the cleanup fails with EPERM.
            _clear_immutable_flags(home)

    for target in targets:
        staged_agents = staged_agent_count(stage, target)
        if staged_agents != len(source_agents):
            raise PublishError(
                f"agentshare staged {staged_agents} {target} agents but the source has "
                f"{len(source_agents)}; refusing to publish a partial snapshot"
            )

    kbs = stage_kbs(root, stage)
    vendoring = None
    if args.vendor_docs:
        vendoring = doc_refs.vendor_documents(
            consumer_root=root, stage=stage, targets=targets, agents=source_agents
        )
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
    if vendoring is not None:
        share_files = sum(1 for path in files if path.startswith(f"{doc_refs.STAGE_DIR}/"))
        manifest["share"] = {
            "dest": doc_refs.SHARE_DEST,
            "prefix": f"{doc_refs.STAGE_DIR}/",
            "files": share_files,
        }
        manifest["vendored_docs"] = vendoring["vendored_docs"]
        manifest["unresolved"] = vendoring["unresolved"]
        manifest["doc_vendoring"] = {
            key: vendoring[key]
            for key in (
                "base",
                "stage_dir",
                "vendored_docs",
                "vendored_files",
                "vendored_bytes",
                "rewritten_files",
                "rewritten_refs",
                "rewritten_agents",
                "agents_scanned",
                "citing_agents",
            )
        }
        manifest["doc_vendoring"]["documents"] = vendoring["documents"]
    (stage / "manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    _log(f"snapshot_id: {snapshot_id}")
    _log(f"agents: {len(manifest['agents'])}")
    _log(f"kbs: {len(manifest['kbs'])}")
    _log(f"files: {len(files)}")
    if vendoring is not None:
        _log(
            f"vendored docs: {vendoring['vendored_docs']} document(s) / "
            f"{vendoring['vendored_files']} file(s) -> {manifest['share']['dest']}; "
            f"unresolved citations: {len(vendoring['unresolved'])}"
        )
    _log("excluded: " + json.dumps(excluded, sort_keys=False))
    _log(f"manifest: {stage / 'manifest.json'}")
    if args.r2_bucket:
        return upload_snapshot(args, stage)
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


# ── R2 publish ─────────────────────────────────────────────────────────


def resolve_wrangler(explicit: str | None) -> str:
    """The wrangler binary: the flag, then $WRANGLER_BIN, then the Worker install, then PATH."""
    if explicit:
        candidate = Path(explicit).expanduser()
        # The upload runs with cwd=worker/, so a path given here must be pinned now.
        if candidate.exists() or os.sep in explicit:
            return str(candidate.resolve())
        return explicit
    from_env = os.environ.get("WRANGLER_BIN")
    if from_env:
        return str(Path(from_env).expanduser().resolve())
    installed = PLUGIN_ROOT / "worker" / "node_modules" / ".bin" / "wrangler"
    if installed.is_file():
        return str(installed)
    return "wrangler"


def upload_env() -> dict[str, str]:
    """The child environment with the R2 S3 secrets removed.

    ``wrangler`` authenticates from its own credential store, so no child process
    needs those values. Keeping them out of every child means a subprocess
    cannot write them into a log line, an error message or a temp file.
    """
    env = dict(os.environ)
    for name in R2_SECRET_ENV:
        env.pop(name, None)
    return env


class R2Uploader:
    """One ``wrangler r2 object put`` per object: R2 has no bulk upload command."""

    def __init__(self, *, bucket: str, local: bool, persist_to: Path | None, wrangler: str, jobs: int) -> None:
        if local and persist_to is None:
            raise PublishError("a local R2 upload needs --persist-to so the dev server reads the same storage")
        self.bucket = bucket
        self.local = local
        self.persist_to = persist_to
        self.wrangler = wrangler
        self.jobs = max(1, int(jobs))
        self.env = upload_env()
        self.written = 0

    def target(self) -> str:
        return "local" if self.local else "remote"

    def _argv(self, key: str, path: Path) -> list[str]:
        argv = [self.wrangler, "r2", "object", "put", f"{self.bucket}/{key}", "--file", str(path)]
        if self.local:
            argv.extend(["--local", "--persist-to", str(self.persist_to)])
        else:
            argv.append("--remote")
        return argv

    def _put_once(self, key: str, path: Path) -> None:
        try:
            proc = subprocess.run(
                self._argv(key, path),
                cwd=str(PLUGIN_ROOT / "worker"),
                env=self.env,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise PublishError(f"cannot run {self.wrangler!r} for the R2 upload: {exc}") from exc
        if proc.returncode != 0:
            lines = (proc.stderr or proc.stdout).strip().splitlines()
            detail = " | ".join(lines[-3:]) if lines else "no output"
            raise PublishError(f"r2 put {key} exited {proc.returncode}: {detail}")

    def _put(self, key: str, path: Path) -> None:
        failure: PublishError | None = None
        for attempt in range(1, 7):
            try:
                self._put_once(key, path)
                self.written += 1
                return
            except PublishError as exc:
                failure = exc
                # A local R2 store is one SQLite file, so two writers can
                # collide (SQLITE_BUSY surface) and the CLI does not retry.
                time.sleep(0.25 * attempt)
        raise PublishError(str(failure))

    def put_many(self, items: list[tuple[str, Path]]) -> int:
        if not items:
            return 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.jobs) as pool:
            futures = [pool.submit(self._put, key, path) for key, path in items]
            for future in concurrent.futures.as_completed(futures):
                future.result()  # the first failure fails the publish
        return len(items)


def upload_snapshot(args: argparse.Namespace, stage: Path) -> int:
    """Upload a staged snapshot: blobs, then its metadata, then ``current.json`` last."""
    manifest_path = stage / "manifest.json"
    files_path = stage / "files.json"
    for required in (manifest_path, files_path):
        if not required.is_file():
            raise PublishError(f"{required} is missing; publish the stage before uploading it")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files_doc = json.loads(files_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise PublishError(f"{stage}: manifest.json or files.json is not valid JSON ({exc})") from exc

    snapshot_id = manifest.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise PublishError(f"{manifest_path}: no snapshot_id; rerun the publisher")
    if files_doc.get("snapshot_id") != snapshot_id:
        raise PublishError(
            f"{files_path} names snapshot {files_doc.get('snapshot_id')!r} but {manifest_path} names {snapshot_id!r}; "
            "the stage is mid-publish — rerun the publisher"
        )

    files = payload_files(stage)
    inventory = files_doc.get("files")
    if not isinstance(inventory, dict) or not inventory:
        raise PublishError(f"{files_path}: no file inventory; rerun the publisher")
    if set(files) != set(inventory):
        missing = sorted(set(files) - set(inventory))
        stale = sorted(set(inventory) - set(files))
        raise PublishError(
            f"{stage}: the stage and files.json disagree (unstaged={missing[:3]}, stale={stale[:3]}); rerun the publisher"
        )
    for relative, path in files.items():
        recorded = inventory[relative].get("sha256")
        actual = sha256_file(path)
        if recorded != actual:
            raise PublishError(f"{relative}: files.json records {recorded} but the staged bytes hash to {actual}")

    uploader = R2Uploader(
        bucket=args.r2_bucket,
        local=args.r2_local,
        persist_to=_stage_path(args.persist_to) if args.persist_to else None,
        wrangler=resolve_wrangler(args.wrangler),
        jobs=args.r2_jobs if args.r2_jobs else (4 if args.r2_local else 6),
    )

    # 1. every blob. Keyed by sha256, so identical files are written once and a
    #    blob another snapshot already wrote is written again with the same bytes.
    blobs: dict[str, Path] = {}
    for relative, path in files.items():
        blobs.setdefault(str(inventory[relative]["sha256"]), path)
    written = uploader.put_many([(f"{R2_BLOB_PREFIX}{sha}", path) for sha, path in sorted(blobs.items())])
    _log(f"r2 {args.r2_bucket} ({uploader.target()}): blobs {written} objects for {len(files)} files")

    # 2. the snapshot's own metadata, which every blob above already backs.
    uploader.put_many(
        [
            (f"{R2_SNAPSHOT_PREFIX}{snapshot_id}/files.json", files_path),
            (f"{R2_SNAPSHOT_PREFIX}{snapshot_id}/manifest.json", manifest_path),
        ]
    )
    _log(f"r2 {args.r2_bucket}: snapshot {snapshot_id} files.json manifest.json")

    # 3. the pointer last: this single object decides which snapshot a reader sees.
    pointer = {
        "schema": R2_SCHEMA,
        "snapshot_id": snapshot_id,
        "manifest_sha256": sha256_file(manifest_path),
        "published_at": manifest.get("published_at"),
        "files": len(files),
    }
    with tempfile.TemporaryDirectory(prefix="r2-pointer-") as tmp:
        pointer_path = Path(tmp) / R2_POINTER_KEY
        pointer_path.write_text(json.dumps(pointer, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        uploader.put_many([(R2_POINTER_KEY, pointer_path)])
    _log(
        f"r2 {args.r2_bucket}: {R2_POINTER_KEY} last -> "
        + json.dumps({key: pointer[key] for key in ("snapshot_id", "manifest_sha256", "files")})
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
    parser.add_argument(
        "--vendor-docs",
        action="store_true",
        help="vendor every document the prompts cite into the snapshot's docs/ tree and rewrite the "
        "citations to ~/.rosetta/share/brain/... (changes prompt text; off by default so a plain "
        "publish stays byte-identical to the consumer's own projection)",
    )
    parser.add_argument(
        "--r2-bucket",
        help="publish the staged snapshot to this R2 bucket (blobs, then snapshot metadata, then current.json last)",
    )
    parser.add_argument(
        "--r2-local",
        action="store_true",
        help="upload to the local (miniflare) R2 storage used by `wrangler dev --local`; needs --persist-to",
    )
    parser.add_argument(
        "--persist-to",
        default=str(PLUGIN_ROOT / "worker" / ".wrangler" / "state"),
        help="local R2 storage directory shared with `wrangler dev --local --persist-to` "
        "(default worker/.wrangler/state)",
    )
    parser.add_argument(
        "--r2-jobs",
        type=int,
        default=0,
        help="parallel `wrangler r2 object put` processes; R2 has no bulk command "
        "(default 4 local, 6 remote)",
    )
    parser.add_argument(
        "--wrangler",
        help="wrangler binary for the R2 upload (default $WRANGLER_BIN, then worker/node_modules/.bin/wrangler)",
    )
    parser.add_argument(
        "--upload-only",
        action="store_true",
        help="upload the existing --stage to R2 without re-staging it (needs manifest.json, files.json and --r2-bucket)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.upload_only:
            if not args.r2_bucket:
                parser.error("--upload-only needs --r2-bucket <name>")
            stage = _stage_path(args.stage)
            if not stage.is_dir():
                raise PublishError(f"--stage {stage}: not a directory; nothing to upload")
            return upload_snapshot(args, stage)
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
