#!/usr/bin/env python3
"""test_jsonfile.py — pins the JSON read/write/hash decisions.

The digest cases matter most: ``body_digest`` must agree BYTE FOR BYTE with
``write-graph.py``'s existing ``graph_sha``, including for non-ASCII content, or every
fact-graph already stamped on disk fails its next ``--check``.

Run: python3 scripts/rosetta/test_jsonfile.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import jsonfile as J  # noqa: E402
import provenance as P  # noqa: E402  (transition cross-check)


def _load_write_graph():
    """write-graph.py has a hyphen, so it cannot be imported by name. Load it by path."""
    spec = importlib.util.spec_from_file_location("write_graph_under_test", HERE / "write-graph.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    failures: list[str] = []

    def check(name: str, got, want) -> None:
        if got != want:
            failures.append(f"{name}\n     got:  {got!r}\n     want: {want!r}")

    W = _load_write_graph()

    # ── canonical_json: identical to provenance's, including non-ASCII ──
    samples = [
        {"b": 1, "a": [1, 2, {"z": None}]},
        {"zh": "知识图谱", "emoji": "🧭"},
        {},
        [3, 1, 2],
        {"nested": {"y": True, "x": False}},
    ]
    for i, value in enumerate(samples):
        check(f"canonical_json[{i}]", J.canonical_json(value), P.canonical_json(value))

    # ── body_digest: must equal write-graph.graph_sha exactly ──
    graphs = [
        {"v": {"kb_version": "1.0.0"}, "w": {}, "r": {}, "a": {}, "x": {}, "t": {}},
        {"v": {"kb_version": "2.1.0", "pkg": "x"}, "r": {"no-fs": {"verdict": "REJECT"}}},
        # Non-ASCII is the case that proves the ensure_ascii difference was honoured.
        {"v": {"kb_version": "1.0.0", "note": "知识图谱 🧭"}, "a": {"调用()": "Promise"}},
        # _meta must be excluded: re-stamping must not change identity.
        {"v": {"kb_version": "1.0.0"}, "_meta": {"technology": "x", "assimilation": {"run_id": "a"}}},
    ]
    for i, graph in enumerate(graphs):
        check(f"body_digest==graph_sha[{i}]", J.body_digest(graph), W.graph_sha(graph))

    # _meta exclusion is not accidental: same body, different meta, same digest.
    bare = {"v": {"kb_version": "1.0.0"}}
    stamped = {"v": {"kb_version": "1.0.0"}, "_meta": {"assimilation": {"graph_sha256": "deadbeef"}}}
    check("meta is not part of identity", J.body_digest(bare), J.body_digest(stamped))

    # A changed fact MUST change the digest.
    check(
        "a changed fact changes identity",
        J.body_digest({"v": {"kb_version": "1.0.0"}, "a": {"f()": "x"}})
        != J.body_digest({"v": {"kb_version": "1.0.0"}, "a": {"f()": "y"}}),
        True,
    )

    # ── write_json: atomic, sorted, trailing newline, no stray temp files ──
    tmp = Path(tempfile.mkdtemp(prefix="jsonfile-test-"))
    try:
        target = tmp / "nested" / "artifact.json"
        J.write_json(target, {"b": 2, "a": 1})
        check("creates parent dirs", target.is_file(), True)
        check("contents round-trip", json.loads(target.read_text()), {"a": 1, "b": 2})
        check("keys are sorted", target.read_text().index('"a"') < target.read_text().index('"b"'), True)
        check("trailing newline", target.read_text().endswith("\n"), True)
        check("no temp files left", sorted(p.name for p in target.parent.iterdir()), ["artifact.json"])

        # Overwriting an existing file must replace it, not append or fail.
        J.write_json(target, {"a": 9})
        check("overwrite replaces", json.loads(target.read_text()), {"a": 9})

        # A directory that already exists is fine.
        J.write_json(target, {"a": 10})
        check("rewrite in place", json.loads(target.read_text()), {"a": 10})

        # write_text is atomic too and preserves exact bytes.
        text_target = tmp / "README.md"
        J.write_text(text_target, "# title\n\nbody\n")
        check("write_text round-trip", text_target.read_text(), "# title\n\nbody\n")
        check(
            "write_text leaves no temp",
            sorted(p.name for p in text_target.parent.iterdir()),
            ["README.md", "nested"],
        )

        # read_json is strict: a missing file and malformed JSON both raise.
        try:
            J.read_json(tmp / "absent.json")
            check("read_json raises on missing", "no raise", "FileNotFoundError")
        except FileNotFoundError:
            check("read_json raises on missing", "ok", "ok")
        broken = tmp / "broken.json"
        broken.write_text("{not json")
        try:
            J.read_json(broken)
            check("read_json raises on malformed", "no raise", "JSONDecodeError")
        except json.JSONDecodeError:
            check("read_json raises on malformed", "ok", "ok")
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print(f"FAILED — {len(failures)} case(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ok — jsonfile.py digest-compatible with write-graph, atomic, sorted, temp-clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
