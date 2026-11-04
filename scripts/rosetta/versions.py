#!/usr/bin/env python3
"""versions.py — one home for every version decision in the KB pipeline.

WHY THIS EXISTS
  Semver parsing, increment, and the kb_version bump rule were scattered through
  provenance.py next to network pinning and Markdown rewriting. A caller that wanted
  "what patch should this KB be" had to read a 1000-line module about HTTP.

  Three separate concerns live here, and only because they are one decision each:
    - TEXT → TUPLE      parse_semver / looks_like_version / parse_kb
    - TUPLE → TEXT      increment (major / minor / patch, zeroing the right tail)
    - GRAPH DIFF → BUMP which part of the number moves, and never a model's opinion

WHAT THIS MODULE DOES NOT DO
  It does not read or write files, and it does not fetch anything. A KB version is a
  pure function of the old label, the old graph, and the new graph.

COMPOSED EDITIONS
  A knowledge base may borrow another's facts *by value*. The borrower's body then
  carries ``composed[<slug>] = {kb_version, fingerprint, facts}``: the edition it copied,
  a digest of the exact facts it took, and the facts themselves. That is an edition
  identity, so its shape and its freshness rule live here — not in ``factgraph.py``.

  WHY THE CONTAINER KEY IS HERE AND NOT IN factgraph.py
    ``factgraph.py`` requires pydantic. The renderer (``kb-delivery``) and the registry
    must read the container to render and to answer "who borrowed Rust", and neither may
    import pydantic, so the key and the readers live in this stdlib-only module.

  ``_meta.composed_with`` is a MIRROR of this container, rebuilt by ``factgraph`` from the
  body. It is never an independent record; a declaration with no body copy is a bug.
"""
from __future__ import annotations

import re
from typing import Any

from jsonfile import canonical_json, sha256_text
from version_label import kb_edition

#: Three numeric components, optional leading "v", trailing junk tolerated (``1.2.3-rc1``).
VERSION_RE = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")

#: ``v`` keys that are bookkeeping rather than facts. A bump caused by one of these
#: being added is not a content change, so it must not move the minor number.
V_BOOKKEEPING = frozenset({
    "kb_version", "pinned_date", "pinned", "version_source", "supersedes", "version",
})

#: Graph sections, in the order every writer iterates them.
SECTIONS = ("v", "w", "r", "a", "x", "t")


def parse_semver(value: Any) -> tuple[int, int, int] | None:
    """``"v2.1"`` → ``(2, 1, 0)``. ``None`` for anything unparseable — never a fake 0.0.0."""
    if value is None:
        return None
    m = VERSION_RE.match(str(value).strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0)


def looks_like_version(text: str) -> str | None:
    """``"v1.4-rc2"`` → ``"1.4.0"``. Used for git-describe tags and docs markers."""
    parsed = parse_semver(text)
    if parsed is None:
        return None
    return ".".join(str(p) for p in parsed)


def format_semver(parts: tuple[int, int, int]) -> str:
    return ".".join(str(p) for p in parts)


def increment(parts: list[int], index: int) -> str:
    """Bump ``parts[index]``, zero everything after it, and render three components.

    ``increment([1,2,3], 1)`` → ``"1.3.0"``. Missing components are treated as 0, so a
    legacy two-part label (``"1.5"``) bumps to ``"1.6.0"`` rather than raising.
    """
    out = list(parts)
    while len(out) < 3:
        out.append(0)
    out[index] += 1
    for j in range(index + 1, len(out)):
        out[j] = 0
    return format_semver((out[0], out[1], out[2]))


def parse_kb(kb: str) -> list[int]:
    """``"2.1.0-rc1"`` → ``[2, 1, 0]``. Falls back to ``[1, 0, 0]`` for an empty label."""
    bits: list[int] = []
    for part in str(kb or "1.0.0").split("."):
        try:
            bits.append(int(re.sub(r"\D.*", "", part) or "0"))
        except ValueError:
            bits.append(0)
    return bits or [1, 0, 0]


# ─────────────────────────── graph diff → bump ───────────────────────────

def verdict_of(value: Any) -> str | None:
    """A rule value may carry its verdict inline (``"REJECT"``) or as a field."""
    if isinstance(value, dict):
        found = value.get("verdict")
        return str(found) if found is not None else None
    if isinstance(value, str):
        return value
    return None


def _rule_flipped_to_reject(old: dict | None, new: dict | None) -> bool:
    old = old or {}
    new = new or {}
    for key, new_value in new.items():
        if str(key).endswith("_note"):
            continue
        if verdict_of(new_value) == "REJECT" and verdict_of(old.get(key)) != "REJECT":
            return True
    return False


def _contract_changed(old: dict | None, new: dict | None) -> bool:
    old = old or {}
    new = new or {}
    if set(old) - set(new):
        return True
    return any(old[key] != new[key] for key in set(old) & set(new))


def _keys_added(old_graph: dict, new_graph: dict) -> bool:
    for section in SECTIONS:
        old_keys = set(old_graph.get(section) or {})
        new_keys = set(new_graph.get(section) or {})
        if section == "v":
            old_keys -= V_BOOKKEEPING
            new_keys -= V_BOOKKEEPING
        if new_keys - old_keys:
            return True
    return False


def bump_kb_version(
    old_kb: str,
    old_graph: dict,
    new_graph: dict,
    old_upstream: Any = None,
    new_upstream: Any = None,
) -> str:
    """major / minor / patch from the section diff — never a model choice.

    major:  the upstream major moved, or a rule flipped to REJECT, or a type contract changed.
    minor:  a fact key was added.
    patch:  anything else (a value changed, a fact removed).
    """
    parts = parse_kb(old_kb)
    old_semver = parse_semver(old_upstream)
    new_semver = parse_semver(new_upstream)
    if old_semver and new_semver and new_semver[0] != old_semver[0]:
        return increment(parts, 0)
    if _rule_flipped_to_reject(old_graph.get("r"), new_graph.get("r")):
        return increment(parts, 0)
    if _contract_changed(old_graph.get("t"), new_graph.get("t")):
        return increment(parts, 0)
    if _keys_added(old_graph, new_graph):
        return increment(parts, 1)
    if old_semver and new_semver and new_semver != old_semver:
        return increment(parts, 1)
    return increment(parts, 2)


# ─────────────────────────── composed editions ───────────────────────────

#: Body key holding borrowed facts by value. See the module docstring for why it is here.
COMPOSED_KEY = "composed"

#: A copy is current: its recorded edition and its facts both agree with the original.
COMPOSED_FRESH = "fresh"
#: The original's ``v.kb_version`` moved on; the copy is behind.
COMPOSED_STALE = "stale"
#: The recorded fingerprint no longer matches the facts in the copy (it was edited).
COMPOSED_DIVERGED = "diverged"
#: The graph declares the source but carries no copy at all.
COMPOSED_MISSING_COPY = "missing-copy"
#: The declared original has no fact-graph in this project.
COMPOSED_MISSING_ORIGINAL = "missing-original"

#: Statuses that mean "this copy must not be overwritten silently".
COMPOSED_PROBLEMS = frozenset({COMPOSED_STALE, COMPOSED_DIVERGED,
                              COMPOSED_MISSING_COPY, COMPOSED_MISSING_ORIGINAL})


def fact_fingerprint(facts: Any) -> str:
    """Digest of an exact set of copied facts. Key order and whitespace do not matter."""
    return sha256_text(canonical_json(facts))


def composed_entries(graph: Any) -> dict[str, dict]:
    """The body container as ``{slug: entry}``, tolerant of a legacy/absent container."""
    if not isinstance(graph, dict):
        return {}
    container = graph.get(COMPOSED_KEY)
    if not isinstance(container, dict):
        return {}
    return {str(slug): entry for slug, entry in container.items() if isinstance(entry, dict)}


def declared_composed(graph: Any) -> list[dict]:
    """``[{graph, kb_version}]`` from ``_meta.composed_with``, junk entries dropped."""
    if not isinstance(graph, dict):
        return []
    meta = graph.get("_meta")
    if not isinstance(meta, dict):
        return []
    entries = meta.get("composed_with")
    if not isinstance(entries, list):
        return []
    out: list[dict] = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("graph"):
            out.append({
                "graph": str(entry["graph"]),
                "kb_version": str(entry.get("kb_version") or ""),
            })
    return out


def declared_composed_slugs(graph: Any) -> list[str]:
    """Sorted unique slugs the graph declares as compositional sources."""
    return sorted({entry["graph"] for entry in declared_composed(graph)})


def composed_slugs(graph: Any) -> list[str]:
    """Every composed source the graph knows about: declared or actually copied."""
    return sorted(set(declared_composed_slugs(graph)) | set(composed_entries(graph)))


def composed_freshness(borrower: Any, original: Any, slug: str) -> dict:
    """Compare one copy against its original. Offline: both graphs are already in hand.

    The divergence check runs FIRST and reports THAT the copy differs, never which fact
    differs — a whole-block refresh has no three-way view of the clash.
    """
    entry = composed_entries(borrower).get(slug)
    facts = (entry or {}).get("facts")
    recorded = str((entry or {}).get("kb_version") or "")
    # Same label rule the registry and the renderer stamp, so a legacy graph's edition is
    # compared as "unknown" rather than as an empty string on one side only.
    current = kb_edition(original) if isinstance(original, dict) else ""
    report = {
        "graph": slug,
        "recorded_kb_version": recorded,
        "current_kb_version": current,
        "copy_intact": False,
        "status": COMPOSED_MISSING_COPY,
    }
    if entry is None or not isinstance(facts, dict):
        return report
    report["copy_intact"] = fact_fingerprint(facts) == str(entry.get("fingerprint") or "")
    if not report["copy_intact"]:
        report["status"] = COMPOSED_DIVERGED
        return report
    if not isinstance(original, dict):
        report["status"] = COMPOSED_MISSING_ORIGINAL
        return report
    report["status"] = COMPOSED_STALE if recorded != current else COMPOSED_FRESH
    return report


def composed_versions_match(borrower: Any, original: Any, slug: str) -> bool:
    """True only when the recorded edition is the original's current ``v.kb_version``."""
    return composed_freshness(borrower, original, slug)["status"] == COMPOSED_FRESH

