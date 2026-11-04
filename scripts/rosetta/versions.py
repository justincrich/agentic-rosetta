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
"""
from __future__ import annotations

import re
from typing import Any

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
