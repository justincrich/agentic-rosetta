#!/usr/bin/env python3
"""test_versions.py — pins the version decisions extracted from provenance.py.

Every case asserts a CONCRETE expected value, transcribed from the behaviour of
provenance.py before versions.py existed. While provenance.py still owns its own
copies (until the repoint lands), each case is also cross-checked against it, so a
transcription slip fails loudly instead of silently changing how KBs are versioned.

Run: python3 scripts/rosetta/test_versions.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import provenance as P  # noqa: E402  (transition cross-check only)
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
        check(f"parse_semver-provenance({raw!r})", P.parse_semver(raw), want)

    for raw, want in [("v1.4-rc2", "1.4.0"), ("abc", None), ("2", "2.0.0"), ("0.9", "0.9.0")]:
        check(f"looks_like_version({raw!r})", V.looks_like_version(raw), want)
        check(f"looks_like_version-provenance({raw!r})", P._looks_like_version(raw), want)

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
        check(f"increment-provenance({parts}, {idx})", P._inc(parts, idx), want)

    for raw, want in [
        ("2.1.0", [2, 1, 0]),
        ("1.0.0", [1, 0, 0]),
        ("3", [3]),
        ("", [1, 0, 0]),
        ("2.1.0-rc1", [2, 1, 0]),
        ("x.y.z", [0, 0, 0]),
    ]:
        check(f"parse_kb({raw!r})", V.parse_kb(raw), want)
        check(f"parse_kb-provenance({raw!r})", P.parse_kb(raw), want)

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
        check(
            f"bump-provenance[{name}]",
            P.bump_kb_version("1.0.0", old_graph, new_graph, old_up, new_up),
            want,
        )

    # A legacy two-part label must bump cleanly rather than raise.
    check("legacy two-part label", V.bump_kb_version("1.5", diff(), diff(), "1.5", "1.5"), "1.5.1")
    check(
        "legacy two-part label-provenance",
        P.bump_kb_version("1.5", diff(), diff(), "1.5", "1.5"),
        "1.5.1",
    )

    # A bookkeeping-only change must never produce a MAJOR bump.
    got = V.bump_kb_version(
        "2.0.0",
        diff(v={"version": "1.0.0"}),
        diff(v={"version": "1.0.0", "version_source": "package.json"}),
        None,
        None,
    )
    check("bookkeeping never majors", got.startswith("2."), True)

    if failures:
        print(f"FAILED — {len(failures)} case(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"ok — versions.py pins {len(cases)} bump rules, 10 semver forms, 6 kb-label forms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
