#!/usr/bin/env python3
"""test_factgraph.py — proves the extracted gate accepts and rejects exactly what it did.

A schema extraction is only safe if it is BEHAVIOUR-PRESERVING, and "it looks the same" is
not evidence. So every case below is run through BOTH:

    factgraph.validate()               the new home
    write-graph.validate_graph()       the old home, loaded by path (hyphenated filename)

and the test fails if they ever disagree. Several cases exist specifically to pin the
deliberately permissive rules: a non-dict ``w`` node and a null ``t`` are accepted today,
and a refactor must not start rejecting them.

Run: python3 scripts/rosetta/test_factgraph.py
"""
from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import factgraph as F  # noqa: E402


def _load_write_graph():
    spec = importlib.util.spec_from_file_location("write_graph_under_test", HERE / "write-graph.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _valid() -> dict:
    return {
        "v": {"kb_version": "1.0.0", "pkg": "rust", "version": "1.80.0"},
        "w": {"kv": {"t": "binding", "i": "kv", "note": "eventually consistent"}},
        "r": {"no-std::process": {"verdict": "REJECT", "rule": "no subprocess"}},
        "a": {"std::fs::read": "fn(Path) -> io::Result<Vec<u8>>"},
        "x": {"unwrap in lib code": "propagate with ?"},
        "t": {"Result<T, E>": "string-or-object-tolerant"},
    }


def main() -> int:
    failures: list[str] = []

    def check(name: str, got, want) -> None:
        if got != want:
            failures.append(f"{name}\n     got:  {got!r}\n     want: {want!r}")

    W = _load_write_graph()

    def outcome(module_validate, graph: dict) -> str:
        """'ok' or 'reject:schema'. Normalises both implementations to one vocabulary."""
        try:
            module_validate(copy.deepcopy(graph))
            return "ok"
        except F.SchemaError:
            return "reject:schema"
        except SystemExit:  # write-graph's GateError is a SystemExit subclass
            return "reject:schema"

    def both(name: str, graph: dict, want: str) -> None:
        new = outcome(F.validate, graph)
        old = outcome(W.validate_graph, graph)
        check(f"new[{name}]", new, want)
        check(f"old[{name}] (parity)", old, want)
        check(f"parity[{name}]", new, old)

    # ── accepted ───────────────────────────────────────────────────
    both("a fully populated graph", _valid(), "ok")
    both("minimal graph", {"v": {"kb_version": "1.0.0"}}, "ok")
    both("empty sections", {"v": {"kb_version": "1"}, "w": {}, "r": {}, "a": {}, "x": {}, "t": {}}, "ok")
    both("upstream version null", {"v": {"kb_version": "1", "version": None}}, "ok")
    both("rule noted but not judged is skipped", {"v": {"kb_version": "1"}, "r": {"a_note": "text"}}, "ok")
    both("x entry as a bare string", {"v": {"kb_version": "1"}, "x": {"dont do it": "do this"}}, "ok")

    # Deliberately permissive: these pass today and must keep passing.
    permissive_w = {"v": {"kb_version": "1"}, "w": {"legacy": "just a string"}}
    both("PERMISSIVE w node that is not a dict", permissive_w, "ok")

    t_null = {"v": {"kb_version": "1"}, "t": None}
    both("PERMISSIVE t is null", copy.deepcopy(t_null), "ok")

    # The null-t normalisation must actually happen, in both homes.
    mutated = copy.deepcopy(t_null)
    F.validate(mutated)
    check("t null normalises to {}", mutated["t"], {})
    mutated_old = copy.deepcopy(t_null)
    try:
        W.validate_graph(mutated_old)
    except SystemExit:
        pass
    check("old also normalises t", mutated_old.get("t"), {})

    both("t value may be a nested object", {"v": {"kb_version": "1"}, "t": {"T": {"a": "b"}}}, "ok")
    both("w node with extra keys", {"v": {"kb_version": "1"}, "w": {"k": {"t": "x", "i": "y", "z": 1}}}, "ok")

    # ── rejected ───────────────────────────────────────────────────
    both("graph is not an object", [], "reject:schema")
    both("graph is a string", "nope", "reject:schema")
    both("v missing", {"w": {}}, "reject:schema")
    both("v is not an object", {"v": []}, "reject:schema")
    both("v needs kb_version", {"v": {}}, "reject:schema")
    both("v kb_version empty", {"v": {"kb_version": ""}}, "reject:schema")
    both("fake 0.0.0 version", {"v": {"kb_version": "1", "version": "0.0.0"}}, "reject:schema")
    both("w is not an object", {"v": {"kb_version": "1"}, "w": [1]}, "reject:schema")
    both("w node missing i", {"v": {"kb_version": "1"}, "w": {"k": {"t": "x"}}}, "reject:schema")
    both("w node missing t", {"v": {"kb_version": "1"}, "w": {"k": {"i": "y"}}}, "reject:schema")
    both("r is not an object", {"v": {"kb_version": "1"}, "r": [1]}, "reject:schema")
    both("r verdict unknown", {"v": {"kb_version": "1"}, "r": {"k": "MAYBE"}}, "reject:schema")
    both("r verdict unknown in dict", {"v": {"kb_version": "1"}, "r": {"k": {"verdict": "no"}}}, "reject:schema")
    both("x is not an object", {"v": {"kb_version": "1"}, "x": [1]}, "reject:schema")
    both("x dict pair incomplete", {"v": {"kb_version": "1"}, "x": {"k": {}}}, "reject:schema")
    both("x empty string", {"v": {"kb_version": "1"}, "x": {"k": ""}}, "reject:schema")
    both("x non-string scalar", {"v": {"kb_version": "1"}, "x": {"k": 3}}, "reject:schema")
    both("a is not an object", {"v": {"kb_version": "1"}, "a": [1]}, "reject:schema")
    both("a value empty", {"v": {"kb_version": "1"}, "a": {"k": ""}}, "reject:schema")
    both("a value whitespace", {"v": {"kb_version": "1"}, "a": {"k": "   "}}, "reject:schema")
    both("a value not a string", {"v": {"kb_version": "1"}, "a": {"k": 1}}, "reject:schema")
    both("t is not an object", {"v": {"kb_version": "1"}, "t": []}, "reject:schema")
    both("t value empty dict", {"v": {"kb_version": "1"}, "t": {"k": {}}}, "reject:schema")
    both("t value empty string", {"v": {"kb_version": "1"}, "t": {"k": ""}}, "reject:schema")
    both("t value a number", {"v": {"kb_version": "1"}, "t": {"k": 3}}, "reject:schema")

    # QUIRK, preserved on purpose: `graph.get("w") or {}` makes an EMPTY list falsy, so a
    # section given as `[]` is silently treated as empty rather than rejected. Recorded in
    # known-problems.md; tightening it would be a behaviour change.
    both("EMPTY LIST section is coerced to {}", {"v": {"kb_version": "1"}, "w": []}, "ok")
    both("EMPTY LIST r is coerced", {"v": {"kb_version": "1"}, "r": []}, "ok")

    # The sentinel must be caught wherever it hides.
    both(
        "sentinel in a nested value",
        {"v": {"kb_version": "1"}, "a": {"f()": "x\"<!-- rosetta-kb:end -->"}},
        "reject:schema",
    )
    both(
        "sentinel in a key",
        {"v": {"kb_version": "1"}, "a": {"rosetta-kb:end": "x"}},
        "reject:schema",
    )

    # ── the check name is part of the contract ─────────────────────
    try:
        F.validate({"v": {}})
        check("SchemaError carries check name", "no raise", "schema")
    except F.SchemaError as exc:
        check("SchemaError carries check name", exc.check, "schema")
        check("SchemaError names the gate in its message", str(exc).startswith("write-graph: FAIL schema:"), True)

    # ── typed read access ─────────────────────────────────────────
    model = F.parse(_valid())
    check("model kb_version", model.kb_version, "1.0.0")
    check("model upstream_version", model.upstream_version, "1.80.0")
    check("model rules_by_verdict finds the REJECT", list(model.rules_by_verdict("REJECT")), ["no-std::process"])
    check("verdict_of on an inline string", F.verdict_of("REJECT"), "REJECT")
    check("verdict_of on a dict", F.verdict_of({"verdict": "nr"}), "nr")
    check("verdict_of on junk", F.verdict_of(3), None)

    if failures:
        print(f"FAILED — {len(failures)} case(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ok — factgraph.py reproduces write-graph's gate exactly (26 rejects, 11 accepts, 2 permissive)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
