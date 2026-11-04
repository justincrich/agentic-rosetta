#!/usr/bin/env python3
"""test_versions.py — pins the version decisions extracted from provenance.py.

Every case asserts a CONCRETE expected value, transcribed from the behaviour of
provenance.py before versions.py existed. Those values are the contract: a change to
any of them changes how a knowledge base is versioned, so it must be deliberate.

Run: python3 scripts/rosetta/test_versions.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import versions as V  # noqa: E402


def main() -> int:
    failures: list[str] = []

    def check(name: str, got, want) -> None:
        if got != want:
            failures.append(f"{name}\n     got:  {got!r}\n     want: {want!r}")

    # ── text → tuple ───────────────────────────────────────────────
    for raw, want in [
        (None, None),
        ("", None),
        ("junk", None),
        ("1", (1, 0, 0)),
        ("v2.1", (2, 1, 0)),
        ("2.1.3", (2, 1, 3)),
        ("v1.2.3-rc1", (1, 2, 3)),
        ("  v3.0.0  ", (3, 0, 0)),
        (0.5, (0, 5, 0)),
        (7, (7, 0, 0)),
    ]:
        check(f"parse_semver({raw!r})", V.parse_semver(raw), want)

    for raw, want in [("v1.4-rc2", "1.4.0"), ("abc", None), ("2", "2.0.0"), ("0.9", "0.9.0")]:
        check(f"looks_like_version({raw!r})", V.looks_like_version(raw), want)

    # ── tuple → text ───────────────────────────────────────────────
    for parts, idx, want in [
        ([1, 2, 3], 0, "2.0.0"),
        ([1, 2, 3], 1, "1.3.0"),
        ([1, 2, 3], 2, "1.2.4"),
        ([1], 2, "1.0.1"),
        ([1, 5], 1, "1.6.0"),
        ([0, 0, 0], 0, "1.0.0"),
    ]:
        check(f"increment({parts}, {idx})", V.increment(parts, idx), want)

    for raw, want in [
        ("2.1.0", [2, 1, 0]),
        ("1.0.0", [1, 0, 0]),
        ("3", [3]),
        ("", [1, 0, 0]),
        ("2.1.0-rc1", [2, 1, 0]),
        ("x.y.z", [0, 0, 0]),
    ]:
        check(f"parse_kb({raw!r})", V.parse_kb(raw), want)

    # ── graph diff → bump ──────────────────────────────────────────
    base: dict = {"v": {}, "w": {}, "r": {}, "a": {}, "x": {}, "t": {}}

    def diff(**overrides) -> dict:
        graph = {k: dict(v) for k, v in base.items()}
        for section, value in overrides.items():
            graph[section] = value
        return graph

    cases: list[tuple[str, dict, dict, object, object, str]] = [
        ("upstream major moved", diff(), diff(), "1.0.0", "2.0.0", "2.0.0"),
        ("upstream minor moved", diff(), diff(), "1.0.0", "1.1.0", "1.1.0"),
        ("upstream unchanged", diff(), diff(), "1.0.0", "1.0.0", "1.0.1"),
        (
            "rule flipped to REJECT",
            diff(r={"no-fs": {"verdict": "r"}}),
            diff(r={"no-fs": {"verdict": "REJECT"}}),
            "1.0.0",
            "1.0.0",
            "2.0.0",
        ),
        (
            "rule still REJECT is not a flip",
            diff(r={"no-fs": {"verdict": "REJECT"}}),
            diff(r={"no-fs": {"verdict": "REJECT"}}),
            "1.0.0",
            "1.0.0",
            "1.0.1",
        ),
        ("contract changed", diff(t={"kb": "KVNamespace"}), diff(t={"kb": "DurableObject"}), None, None, "2.0.0"),
        ("contract removed", diff(t={"kb": "KVNamespace"}), diff(t={}), None, None, "2.0.0"),
        ("fact key added", diff(), diff(w={"kv": {"t": "binding", "i": "kv"}}), None, None, "1.1.0"),
        ("fact value changed", diff(a={"get(k)": "Promise"}), diff(a={"get(k)": "Promise<K>"}), None, None, "1.0.1"),
        (
            "v bookkeeping is not a content change",
            diff(v={"kb_version": "1.0.0"}),
            diff(v={"kb_version": "1.0.0", "pinned_date": "2026-11-04", "supersedes": "0.9.0"}),
            None,
            None,
            "1.0.1",
        ),
    ]

    for name, old_graph, new_graph, old_up, new_up, want in cases:
        got = V.bump_kb_version("1.0.0", old_graph, new_graph, old_up, new_up)
        check(f"bump[{name}]", got, want)

    # A legacy two-part label must bump cleanly rather than raise.
    check("legacy two-part label", V.bump_kb_version("1.5", diff(), diff(), "1.5", "1.5"), "1.5.1")

    # A bookkeeping-only change must never produce a MAJOR bump.
    got = V.bump_kb_version(
        "2.0.0",
        diff(v={"version": "1.0.0"}),
        diff(v={"version": "1.0.0", "version_source": "package.json"}),
        None,
        None,
    )
    check("bookkeeping never majors", got.startswith("2."), True)

    # ── composed editions: recorded copy vs the original's current one ──
    def borrower(recorded: str = "3.1.0", facts: dict | None = None) -> dict:
        borrowed = facts if facts is not None else {
            "v": {"pkg": "rust"},
            "r": {"unwrap_in_library_code": "REJECT"},
        }
        return {
            "v": {"kb_version": "1.2.0", "pkg": "herdrdev"},
            V.COMPOSED_KEY: {
                "rust": {
                    "kb_version": recorded,
                    "fingerprint": V.fact_fingerprint(borrowed),
                    "facts": borrowed,
                },
            },
            "_meta": {"composed_with": [{"graph": "rust", "kb_version": recorded}]},
        }

    rust_310 = {"v": {"kb_version": "3.1.0"}}
    rust_400 = {"v": {"kb_version": "4.0.0"}}

    fresh = V.composed_freshness(borrower(), rust_310, "rust")
    check("composed fresh", fresh["status"], V.COMPOSED_FRESH)
    check("composed fresh recorded edition", fresh["recorded_kb_version"], "3.1.0")
    check("composed fresh current edition", fresh["current_kb_version"], "3.1.0")
    check("composed fresh copy intact", fresh["copy_intact"], True)
    check("composed versions match", V.composed_versions_match(borrower(), rust_310, "rust"), True)

    stale = V.composed_freshness(borrower(), rust_400, "rust")
    check("composed stale", stale["status"], V.COMPOSED_STALE)
    check("composed stale current edition", stale["current_kb_version"], "4.0.0")
    check("composed stale version mismatch",
          V.composed_versions_match(borrower(), rust_400, "rust"), False)

    # A local edit to the copy diverges. It reports THAT, and it outranks the edition.
    edited = borrower()
    edited[V.COMPOSED_KEY]["rust"]["facts"]["r"]["unwrap_in_library_code"] = "nr"
    diverged = V.composed_freshness(edited, rust_310, "rust")
    check("composed diverged", diverged["status"], V.COMPOSED_DIVERGED)
    check("composed diverged copy not intact", diverged["copy_intact"], False)
    check("composed diverged is never a match",
          V.composed_versions_match(edited, rust_310, "rust"), False)

    check("composed missing copy",
          V.composed_freshness({"v": {"kb_version": "1"}}, rust_310, "rust")["status"],
          V.COMPOSED_MISSING_COPY)
    check("composed missing original",
          V.composed_freshness(borrower(), None, "rust")["status"], V.COMPOSED_MISSING_ORIGINAL)
    check("composed declared slugs drop junk",
          V.declared_composed_slugs({"v": {}, "_meta": {
              "composed_with": [{"graph": "rust", "kb_version": "1"}, "junk"]}}),
          ["rust"])
    check("composed slugs union declared and copied", V.composed_slugs(borrower()), ["rust"])
    check("composed entries tolerate a missing container", V.composed_entries({"v": {}}), {})

    # The fingerprint ignores key order and formatting, but not content.
    check("fingerprint is order insensitive",
          V.fact_fingerprint({"a": 1, "b": 2}), V.fact_fingerprint({"b": 2, "a": 1}))
    check("fingerprint is content sensitive",
          V.fact_fingerprint({"a": 1}) != V.fact_fingerprint({"a": 2}), True)

    if failures:
        print(f"FAILED — {len(failures)} case(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"ok — versions.py pins {len(cases)} bump rules, 10 semver forms, 6 kb-label forms, "
          f"4 composed-copy states")
    return 0


if __name__ == "__main__":
    sys.exit(main())
