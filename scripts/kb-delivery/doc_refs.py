#!/usr/bin/env python3
"""doc_refs.py — vendor the documents a prompt cites, and rewrite the citations.

WHY THIS EXISTS
  Agent prompts cite documents: ``brain/docs/mcp-rules/security.md``,
  ``~/Projects/brain/docs/kotlin/compose-ui.md``, ``.rosetta/docs/n8n/…``, ``kb/…``. On a
  machine that syncs its agents from the hosted service there is no consumer checkout, so every
  one of those citations dangles. At publish time this module

  1. resolves each citation against the consumer checkout the publisher was pointed at,
  2. copies the cited document (or the cited directory) into the snapshot's ``docs/`` tree, and
  3. rewrites the citation in the staged prompt to ``~/.rosetta/share/brain/<path>``, which
     ``sync_agents.py`` writes to ``$HOME/.rosetta/share/brain/<path>`` on the device.

  A citation that cannot be honoured is never dropped: it stays in the prompt and is listed by
  name in the manifest's ``unresolved`` list, with the reason. Silence is the failure mode this
  module exists to prevent.

WHY THE CONSUMER'S RESOLVER DECIDES
  Which file a citation means — absolute-home aliases (``~/Projects/brain``,
  ``~/.claude/brain``), repo-relative forms, stale-root fixups (``.rosetta/docs/convex`` →
  ``docs/convex-rules``), agent-relative fallback for ``kb/…`` — already has one home: the
  consumer's ``scripts/rogueone_bundle.py`` (``transform_body``). This module imports that file
  by path (never a hardcoded checkout) and fails closed when its signature or its output shape is
  not the one the plan documents: a private function that moved is a fact nobody here guessed.

WHAT IT DELIBERATELY DOES NOT DO
  ``transform_body`` also rewrites the body for the rogueone profile: it strips foreign
  orchestrator sections, detunes shouted headers, drops stale "Source of Truth" pointer lines and
  rewrites bare ``Read foo.md`` filenames. A hosted prompt must stay the prompt the device's own
  projector produced, apart from citations that now resolve, so this module keeps only the
  *decisions* (``meta["docs"]`` / ``meta["unresolved"]`` / ``meta["unbundlable"]``) and re-applies
  them to the staged text token by token. It uses the consumer's own reference pattern, so what
  counts as a citation is not a second opinion either.

WHERE THE DOCUMENTS LAND
  stage:   ``<stage>/docs/brain/<consumer-relative path>``
  device:  ``$HOME/.rosetta/share/brain/<consumer-relative path>``
  prompt:  ``~/.rosetta/share/brain/<consumer-relative path>``

  The publisher writes the mapping into ``manifest.json`` as ``share`` (``dest`` +
  ``prefix``), which is how a device knows where to put them without knowing this module.
"""
from __future__ import annotations

import importlib.util
import inspect
import shutil
import sys
from pathlib import Path
from types import ModuleType

# The device-side share root a rewritten citation points at, and where the sync puts it.
SHARE_HOME = "~/.rosetta/share"
SHARE_DEST = ".rosetta/share"
STAGE_DIR = "docs"
# A citation that already names the device share root (ours, or one a prompt was written with).
SHARE_PREFIXES = (f"{SHARE_HOME}/", f"{SHARE_DEST}/")

RESOLVER_RELPATH = Path("scripts") / "rogueone_bundle.py"
# The private contract this adapter depends on. Anything else fails closed.
RESOLVER_SIGNATURE = ("agent", "vendor_jobs", "base", "brain_root")
RESOLVER_PATTERN_ATTR = "_BRAIN_REF_RE"
RESOLVER_META_KEYS = ("docs", "unresolved", "unbundlable")


class DocRefsError(RuntimeError):
    """A failure this module reports and refuses to work around."""


# ── the consumer's resolver, imported by path ──────────────────────────


def load_resolver(consumer_root: Path) -> ModuleType:
    """Import the consumer's ``scripts/rogueone_bundle.py`` or refuse to continue."""
    path = consumer_root / RESOLVER_RELPATH
    if not path.is_file():
        raise DocRefsError(
            f"{path}: doc vendoring resolves citations with the consumer's own resolver "
            f"({RESOLVER_RELPATH}) and will not guess a resolution"
        )
    spec = importlib.util.spec_from_file_location("_consumer_rogueone_bundle", path)
    if spec is None or spec.loader is None:
        raise DocRefsError(f"{path}: cannot be imported as a module")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - any import failure is the same refusal
        raise DocRefsError(f"{path}: import failed ({type(exc).__name__}: {exc})") from exc

    transform = getattr(module, "transform_body", None)
    if not callable(transform):
        raise DocRefsError(f"{path}: has no transform_body(), so citations cannot be resolved")
    parameters = tuple(inspect.signature(transform).parameters)
    if parameters != RESOLVER_SIGNATURE:
        raise DocRefsError(
            f"{path}: transform_body{parameters} is not the documented "
            f"({', '.join(RESOLVER_SIGNATURE)}); this adapter fails closed rather than guess"
        )
    pattern = getattr(module, RESOLVER_PATTERN_ATTR, None)
    if not hasattr(pattern, "sub") or not hasattr(pattern, "fullmatch"):
        raise DocRefsError(
            f"{path}: {RESOLVER_PATTERN_ATTR} is missing or is not a compiled pattern, so this "
            "module cannot recognise a citation"
        )
    return module


# ── one agent's decisions ──────────────────────────────────────────────


def source_text(agent: dict) -> str:
    """The agent's source file as written: frontmatter included.

    ``transform_body`` sees only the body, but a prompt ships its frontmatter too — a
    ``description`` that names a brain document is a citation a device will try to follow — so
    resolution reads the whole file and the rewrite is applied to the staged text.
    """
    source = Path(agent["source_path"])
    path = source / "agent.md" if source.is_dir() else source
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DocRefsError(f"{path}: cannot be read as UTF-8 text ({exc})") from exc


def resolve_agent(
    *,
    resolver: ModuleType,
    agent: dict,
    consumer_root: Path,
    base: str = SHARE_HOME,
) -> tuple[dict[str, str], list[dict], list[dict]]:
    """``(rewrites, resolved, unresolved)`` for one agent.

    ``rewrites``  ``{citation as written: vendored path}`` for citations that resolve.
    ``resolved``  the same, with the consumer-relative source, for the manifest.
    ``unresolved`` every citation that stays in the prompt, by name, with a reason.

    Only path-shaped citations are handled: the consumer's pattern decides what that is, and a
    bare filename ("Read foo.md") is not a path this module rewrites.
    """
    pattern = resolver._BRAIN_REF_RE  # noqa: SLF001 - the consumer's own definition
    jobs: dict[str, Path] = {}
    subject = dict(agent)
    subject["body"] = source_text(agent)
    _body, meta = resolver.transform_body(subject, jobs, base, consumer_root)
    if not isinstance(meta, dict) or any(key not in meta for key in RESOLVER_META_KEYS):
        raise DocRefsError(
            f"{agent.get('name')!r}: transform_body returned meta without "
            f"{'/'.join(RESOLVER_META_KEYS)}; the adapter cannot tell a resolved citation from a "
            "dropped one"
        )

    rewrites: dict[str, str] = {}
    resolved: list[dict] = []
    for entry in meta["docs"]:
        ref = str(entry.get("ref") or "")
        if not pattern.fullmatch(ref):
            continue  # a bare filename: vendored by the consumer's plan, not a citation we rewrite
        rewrites[ref] = str(entry["vendored"])
        resolved.append({"ref": ref, "source": str(entry["source"]), "vendored": str(entry["vendored"])})

    unresolved: list[dict] = []
    for entry in meta["unresolved"]:
        ref = str(entry.get("ref") or "")
        if not pattern.fullmatch(ref):
            continue
        unresolved.append({"ref": ref, "kind": "unresolved", "reason": str(entry.get("reason") or "")})
    for entry in meta["unbundlable"]:
        # Agent self-references, holocron ids and device-local home paths: real citations that a
        # file bundle cannot carry. They stay in the prompt and are named, never dropped.
        unresolved.append(
            {
                "ref": str(entry.get("ref") or ""),
                "kind": str(entry.get("kind") or "unbundlable"),
                "reason": str(entry.get("reason") or ""),
            }
        )
    return rewrites, resolved, unresolved


def substitute(text: str, rewrites: dict[str, str], pattern) -> tuple[str, int]:
    """Rewrite every citation in ``text`` that ``rewrites`` names; return ``(text, hits)``."""
    hits = 0

    def _replace(match) -> str:  # noqa: ANN001 - re.Match
        nonlocal hits
        token = match.group(0)
        clean = token.rstrip(".,;:")
        trailing = token[len(clean) :]
        replacement = rewrites.get(clean)
        if replacement is None:
            return token
        hits += 1
        return replacement + trailing

    return pattern.sub(_replace, text), hits


def unaccounted(text: str, *, rewrites: dict[str, str], known: set[str], pattern) -> list[str]:
    """Citations still in ``text`` that nobody has decided about yet.

    The resolver sees the agent's body after its own section strips, so a citation that sits in
    a part it does not rewrite (a ``description`` line, a stripped foreign-config section) can
    otherwise survive with no entry anywhere. Anything this returns is listed as unresolved:
    the prompt is honest about a citation no one resolved, and the accounting has no holes.
    """
    found: list[str] = []
    for match in pattern.finditer(text):
        token = match.group(0).rstrip(".,;:")
        if token in rewrites or token in known:
            continue
        if token.startswith(SHARE_PREFIXES):  # already rewritten to the device share root
            continue
        if token not in found:
            found.append(token)
    return found


# ── the staged target tree ────────────────────────────────────────────


def _agent_name_for(path: Path, target_dir: Path, target: str) -> str | None:
    """Which agent a staged file belongs to.

    ``claude`` is the one directory-per-agent target (``<name>/agent.md`` plus the agent's own
    payload files); every other target is flat (``<name>.md`` / ``<name>.toml``).
    """
    relative = path.relative_to(target_dir)
    if target == "claude" and len(relative.parts) > 1:
        return relative.parts[0]
    return path.stem


def _validated_relative(raw: str, *, label: str) -> tuple[str, ...]:
    """Split a consumer-relative path, or refuse it: it must stay inside the stage."""
    if raw.startswith("/") or raw.startswith("\\"):
        raise DocRefsError(f"{label}: {raw!r} is absolute; a vendored document is a relative path")
    parts = tuple(part for part in raw.split("/") if part)
    for part in parts:
        if part in (".", "..") or "\\" in part or "\x00" in part:
            raise DocRefsError(f"{label}: {raw!r} has a {part!r} segment and can escape the stage")
    if not parts:
        raise DocRefsError(f"{label}: empty path")
    return parts


def _copy_document(src: Path, dest: Path, *, seen: set[Path]) -> tuple[int, int]:
    """Copy one file or one directory tree; ``(new files, bytes)``.

    ``seen`` makes the count unique: a cited directory and a cited file inside it are both
    vendored, and the file underneath must not be counted twice.
    """
    if src.is_dir():
        files = 0
        size = 0
        for path in sorted(src.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            sink = dest / path.relative_to(src)
            if sink in seen:
                continue
            sink.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, sink)
            seen.add(sink)
            files += 1
            size += sink.stat().st_size
        return files, size
    if dest in seen:
        return 0, 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    seen.add(dest)
    return 1, dest.stat().st_size


# ── entry point ───────────────────────────────────────────────────────


def vendor_documents(
    *,
    consumer_root: Path,
    stage: Path,
    targets: tuple[str, ...],
    agents: list[dict],
    base: str = SHARE_HOME,
) -> dict:
    """Vendor every cited document into ``<stage>/docs`` and rewrite the staged prompts.

    Runs after the target trees are staged and before the inventory is hashed, so the credential
    scan sees the vendored documents too (they are payload files like any other).
    """
    resolver = load_resolver(consumer_root)
    pattern = resolver._BRAIN_REF_RE  # noqa: SLF001

    per_agent: dict[str, dict[str, str]] = {}
    known_refs: dict[str, set[str]] = {}
    documents: dict[str, dict] = {}
    unresolved: dict[str, dict] = {}
    citing: set[str] = set()

    for agent in agents:
        name = str(agent["name"])
        rewrites, resolved, missing = resolve_agent(
            resolver=resolver, agent=agent, consumer_root=consumer_root, base=base
        )
        per_agent[name] = rewrites
        known_refs[name] = set(rewrites) | {entry["ref"] for entry in missing}
        if rewrites or missing:
            citing.add(name)
        for entry in resolved:
            document = documents.setdefault(
                entry["source"], {"source": entry["source"], "vendored": entry["vendored"], "refs": []}
            )
            if entry["ref"] not in document["refs"]:
                document["refs"].append(entry["ref"])
        for entry in missing:
            record = unresolved.setdefault(
                entry["ref"], {"ref": entry["ref"], "kind": entry["kind"], "reason": entry["reason"], "agents": set()}
            )
            record["agents"].add(name)

    vendored_files = 0
    vendored_bytes = 0
    written: set[Path] = set()
    for source_rel, document in sorted(documents.items()):
        parts = _validated_relative(source_rel, label=f"vendored document {source_rel!r}")
        src = consumer_root.joinpath(*parts)
        if not src.exists():
            raise DocRefsError(
                f"{source_rel}: the resolver reported it as cited and resolvable, but it is not on disk"
            )
        files, size = _copy_document(src, stage / STAGE_DIR / "brain" / Path(*parts), seen=written)
        vendored_files += files
        vendored_bytes += size
        document["files"] = files

    rewritten_files = 0
    rewritten_refs = 0
    rewritten_agents: set[str] = set()
    for target in targets:
        target_dir = stage / "targets" / target
        if not target_dir.is_dir():
            continue
        for path in sorted(target_dir.rglob("*")):
            if not path.is_file():
                continue
            name = _agent_name_for(path, target_dir, target) or ""
            rewrites = per_agent.get(name, {})
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise DocRefsError(f"{path}: cannot be read as UTF-8 text ({exc})") from exc
            updated, hits = substitute(text, rewrites, pattern) if rewrites else (text, 0)
            stragglers = unaccounted(updated, rewrites=rewrites, known=known_refs.get(name, set()), pattern=pattern)
            for token in stragglers:
                record = unresolved.setdefault(
                    token,
                    {
                        "ref": token,
                        "kind": "not-considered",
                        "reason": (
                            "the consumer's resolver did not report this citation (it sits outside "
                            "the body that resolver rewrites), so it was neither vendored nor rewritten"
                        ),
                        "agents": set(),
                    },
                )
                record["agents"].add(name or "")
            if not hits:
                continue
            path.write_text(updated, encoding="utf-8")
            rewritten_files += 1
            rewritten_refs += hits
            rewritten_agents.add(name)

    print(
        f"[doc-refs] {len(documents)} document(s), {vendored_files} file(s), "
        f"{vendored_bytes} bytes vendored; {rewritten_refs} citation(s) rewritten in "
        f"{rewritten_files} file(s); {len(unresolved)} unresolved citation(s); "
        f"{len(citing)} agent(s) cite something"
    )
    return {
        "base": base,
        "stage_dir": STAGE_DIR,
        "vendored_docs": len(documents),
        "vendored_files": vendored_files,
        "vendored_bytes": vendored_bytes,
        "rewritten_files": rewritten_files,
        "rewritten_refs": rewritten_refs,
        "rewritten_agents": len(rewritten_agents),
        "agents_scanned": len(per_agent),
        "citing_agents": len(citing),
        "documents": [documents[key] for key in sorted(documents)],
        "unresolved": [
            {
                "ref": record["ref"],
                "kind": record["kind"],
                "reason": record["reason"],
                "agents": len(record["agents"]),
            }
            for record in sorted(unresolved.values(), key=lambda item: item["ref"])
        ],
    }


def main(argv: list[str] | None = None) -> int:
    """No CLI: this module is a library the publisher calls (kept importable on purpose)."""
    print(
        "doc_refs is the publisher's vendoring step: "
        "publish_snapshot.py --root <checkout> --vendor-docs --stage <dir>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
