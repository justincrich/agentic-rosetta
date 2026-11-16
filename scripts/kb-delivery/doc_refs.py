#!/usr/bin/env python3
"""doc_refs.py — vendor the documents a prompt cites, and rewrite the citations.

WHY THIS EXISTS
  Agent prompts cite documents: ``notes/mcp-rules/security.md``, ``~/checkout/notes/x.md``,
  ``<paths.docs>/n8n/…``, ``kb/…``. On a machine that syncs its agents from the hosted service
  there is no consumer checkout, so every one of those citations dangles. At publish time this
  module

  1. resolves each citation against the consumer checkout the publisher was pointed at,
  2. copies the cited document (or the cited directory) into the snapshot's ``docs/`` tree, and
  3. rewrites the citation in the staged prompt to ``~/.rosetta/share/<share>/<path>``, which
     ``sync_agents.py`` writes to ``$HOME/.rosetta/share/<share>/<path>`` on the device.

  A citation that cannot be honoured is never dropped: it stays in the prompt and is listed by
  name in the manifest's ``unresolved`` list, with the reason. Silence is the failure mode this
  module exists to prevent.

WHY THE PROJECT CONFIG DECIDES
  Which file a citation means is a property of the repo that wrote it, so the repo declares it:
  ``citation_aliases`` in ``<repo>/.rosetta/config.json`` maps a citation prefix to the
  directory it really lives in, and ``paths.docs`` names the knowledge-base directory. This
  module resolves every citation through that table and the repo-relative reading, in that
  order, and it never consults a second resolver: a prefix nobody declared is an unresolved
  citation with a reason, not a guess.

  (Until 2026-11-15 this delegated to the consumer's ``scripts/rogueone_bundle.py``. That
  resolver was retired by its own repo, which named this module as the owner of the job — so the
  table moved here, into the config, where a repo can change it without editing code.)

WHERE THE DOCUMENTS LAND
  stage:   ``<stage>/docs/<share>/<consumer-relative path>``
  device:  ``$HOME/.rosetta/share/<share>/<consumer-relative path>``
  prompt:  ``~/.rosetta/share/<share>/<consumer-relative path>``

  ``<share>`` is the device-side root the consumer's resolver names; this module only moves the
  bytes and records where they went, so nothing here has to know its spelling.

  The publisher writes the mapping into ``manifest.json`` as ``share`` (``dest`` +
  ``prefix``), which is how a device knows where to put them without knowing this module.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "rosetta"))
import roots  # noqa: E402  (the consumer's declared layout)

# The device-side share root a rewritten citation points at, and where the sync puts it.
SHARE_HOME = "~/.rosetta/share"
SHARE_DEST = ".rosetta/share"
STAGE_DIR = "docs"
#: The folder inside the share that vendored documents live in. One name, used by the stage
#: tree, the device path and the rewritten citation, so a device needs no table of its own.
SHARE_SUBDIR = "shared"
# A citation that already names the device share root (ours, or one a prompt was written with).
SHARE_PREFIXES = (f"{SHARE_HOME}/", f"{SHARE_DEST}/")

#: A path-shaped citation: at least one '/', and a document-ish extension. A bare filename
#: ("Read foo.md") is not a path this module rewrites — it resolves beside the prompt it is in.
CITATION_RE = re.compile(
    r"(?<![\w/])[~/@.\w][\w./~@-]*/[\w./~@-]*\.(?:md|markdown|json|ya?ml|toml|txt|py|ts|tsx|js|sh)\b"
)
UNRESOLVABLE_REASON = (
    "the project config declares no citation alias for this prefix and the file is not "
    "repo-relative, so it was left in the prompt"
)
DEVICE_LOCAL_REASON = "an absolute or home-relative path on the author's machine; a file bundle cannot carry it"


class DocRefsError(RuntimeError):
    """A failure this module reports and refuses to work around."""


# ── one citation's meaning ─────────────────────────────────────────────


class Citations:
    """The project's citation table: alias prefixes, docs directory, and the repo they sit in."""

    def __init__(self, consumer_root: Path) -> None:
        self.root = consumer_root
        # Both raise SystemExit(2) with the resolver's message when the repo declares no layout.
        self.docs_dir = roots.resolved_dir("docs", consumer_root)
        self.agents_dir = roots.resolved_dir("agents", consumer_root)
        self.aliases = roots.resolved_citation_aliases(consumer_root)

    def resolve(self, token: str) -> tuple[str, str] | tuple[None, str]:
        """``(repo-relative path, "")`` for a citation this project carries, else ``(None, why)``."""
        clean = token.strip()
        if clean.startswith(SHARE_PREFIXES):
            return None, "already points at the device share root"
        if clean.startswith(("http://", "https://", "file:", "//")):
            return None, "a URL, not a local document"
        if clean.startswith(("~", "/")):
            return None, DEVICE_LOCAL_REASON
        for prefix in sorted(self.aliases, key=len, reverse=True):
            if clean == prefix or clean.startswith(prefix + "/"):
                rest = clean[len(prefix):].lstrip("/")
                candidate = self.aliases[prefix] / rest
                if candidate.is_file() or candidate.is_dir():
                    return self._relative(candidate), ""
                return None, (
                    f"the config maps {prefix!r} to {self.aliases[prefix]} but {rest!r} is not there"
                )
        candidate = self.root / clean
        if candidate.is_file() or candidate.is_dir():
            return self._relative(candidate), ""
        return None, UNRESOLVABLE_REASON

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()


def source_text(agent: dict) -> str:
    """The agent's source file as written: frontmatter included.

    A prompt ships its frontmatter too — a ``description`` that names a document is a citation a
    device will try to follow — so resolution reads the whole file.
    """
    source = Path(agent["source_path"])
    path = source / "agent.md" if source.is_dir() else source
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DocRefsError(f"{path}: cannot be read as UTF-8 text ({exc})") from exc


def vendored_relative(relative: str) -> str:
    """Where a repo-relative document lands inside the share."""
    return f"{SHARE_SUBDIR}/{relative}"


def resolve_agent(
    *,
    citations: Citations,
    agent: dict,
    base: str = SHARE_HOME,
) -> tuple[dict[str, str], list[dict], list[dict]]:
    """``(rewrites, resolved, unresolved)`` for one agent.

    ``rewrites``  ``{citation as written: the path it becomes in the prompt}``.
    ``resolved``  the same, with the repo-relative source, for the manifest.
    ``unresolved`` every citation that stays in the prompt, by name, with a reason.
    """
    text = source_text(agent)
    rewrites: dict[str, str] = {}
    resolved: list[dict] = []
    unresolved: list[dict] = []
    for match in CITATION_RE.finditer(text):
        token = match.group(0).rstrip(".,;:")
        if token in rewrites or any(entry["ref"] == token for entry in unresolved):
            continue
        relative, reason = citations.resolve(token)
        if relative is None:
            kind = "unbundlable" if reason == DEVICE_LOCAL_REASON else "unresolved"
            unresolved.append({"ref": token, "kind": kind, "reason": reason})
            continue
        target = f"{base}/{vendored_relative(relative)}"
        rewrites[token] = target
        resolved.append({"ref": token, "source": relative, "vendored": target})
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

    A checkout that declares no layout is refused before anything is vendored: the docs and
    agents locations, and the citation table, are the checkout's own answer.
    """
    citations = Citations(consumer_root)
    pattern = CITATION_RE

    per_agent: dict[str, dict[str, str]] = {}
    known_refs: dict[str, set[str]] = {}
    documents: dict[str, dict] = {}
    unresolved: dict[str, dict] = {}
    citing: set[str] = set()

    for agent in agents:
        name = str(agent["name"])
        rewrites, resolved, missing = resolve_agent(
            citations=citations, agent=agent, base=base
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
        files, size = _copy_document(
            src, stage / STAGE_DIR / SHARE_SUBDIR / Path(*parts), seen=written
        )
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
