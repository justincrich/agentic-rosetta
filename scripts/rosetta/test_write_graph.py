#!/usr/bin/env python3
"""Selftests for write-graph.py against a real temp project + real ledger files."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
WG = HERE / "write-graph.py"


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(WG), *args],
        capture_output=True, text=True, cwd=cwd,
    )


CLOSED = {
    "run_id": "run-abc",
    "tech": "toy",
    "source_mode": "repo",
    "decoder_ring_hash": "deadbeefcafebabe",
    "waves": 1,
    "censused": 1,
    "closed": 1,
    "rows": [{
        "id": "t01", "territory": "README.md", "class": "orientation",
        "owner": "t01", "status": "closed", "fact_count": 1, "reason": None,
    }],
    "gaps": [],
}

NRRA = """---
artifact: rosetta-research
tech: "Toy"
tech_slug: "toy"
status: "complete"
run_id: "run-abc"
decoder_ring_hash: "deadbeefcafebabe"
---

# Rosetta Research: Toy

## 9. Fact-Graph Seed (draft JSON, ≥ MEDIUM confidence only)
```json
{
  "v": {"pkg": "toy", "version": "1.2.3"},
  "w": {"Widget": {"t": "sl", "i": "toy", "note": "a box"}},
  "r": {"no-stub": {"verdict": "REJECT", "rule": "no stubs"}},
  "a": {"make": "makeWidget()"},
  "x": {"use div": "use Widget"},
  "t": {}
}
```
"""


def main() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        if not ok:
            failures.append(f"{name} {detail}")

    tmp = Path(tempfile.mkdtemp(prefix="rosetta-wg-"))
    root = tmp / "proj"
    (root / ".git").mkdir(parents=True)
    nrra = tmp / "nrra.md"
    ledger = tmp / "ledger.json"
    nrra.write_text(NRRA)
    ledger.write_text(json.dumps(CLOSED))

    # closed + matching ids → create
    r = run("--root", str(root), "--create", "toy", "--nrra", str(nrra), "--ledger", str(ledger))
    check("create-closed", r.returncode == 0, f"{r.returncode} {r.stderr}")
    dest = root / ".rosetta" / "docs" / "toy" / "fact-graph.json"
    check("create-wrote", dest.is_file(), str(dest))
    if dest.is_file():
        g = json.loads(dest.read_text())
        check("create-kb", g.get("v", {}).get("kb_version") == "1.0.0")
        check("create-stamp", bool((g.get("_meta") or {}).get("assimilation", {}).get("graph_sha256")))

    # create refuses if exists
    r2 = run("--root", str(root), "--create", "toy", "--nrra", str(nrra), "--ledger", str(ledger))
    check("create-refuses-existing", r2.returncode != 0)
    check("create-refuses-names-check", "overwrite" in r2.stderr.lower() or "existing" in r2.stderr.lower())

    # --check green on freshly stamped
    c = run("--root", str(root), "--check", "toy")
    check("check-fresh", c.returncode == 0, c.stderr)

    # open ledger rejected
    open_l = dict(CLOSED, censused=2, rows=CLOSED["rows"] + [{
        "id": "t02", "territory": "x", "class": "reference",
        "owner": None, "status": "open", "fact_count": 0, "reason": None,
    }])
    open_p = tmp / "open.json"
    open_p.write_text(json.dumps(open_l))
    other = tmp / "other-docs"
    r3 = run("--root", str(root), "--create", "open-toy", "--nrra", str(nrra), "--ledger", str(open_p),
             "--out", str(tmp / "should-not-exist.json"))
    check("open-rejected", r3.returncode != 0)
    check("open-names-check", "ledger-closed" in r3.stderr)
    check("open-wrote-nothing", not (tmp / "should-not-exist.json").exists())

    # run_id mismatch
    bad_nrra = tmp / "bad.md"
    bad_nrra.write_text(NRRA.replace("run-abc", "run-OTHER"))
    r4 = run("--root", str(root), "--create", "mismatch", "--nrra", str(bad_nrra), "--ledger", str(ledger),
             "--out", str(tmp / "nope.json"))
    check("mismatch-rejected", r4.returncode != 0)
    check("mismatch-names-check", "run_id" in r4.stderr)
    check("mismatch-wrote-nothing", not (tmp / "nope.json").exists())

    # forged / missing hash
    nohash = dict(CLOSED, decoder_ring_hash="")
    nh = tmp / "nohash.json"
    nh.write_text(json.dumps(nohash))
    nrra2 = tmp / "nrra2.md"
    nrra2.write_text(NRRA.replace("decoder_ring_hash: \"deadbeefcafebabe\"", "decoder_ring_hash: \"\""))
    r5 = run("--root", str(root), "--create", "forged", "--nrra", str(nrra2), "--ledger", str(nh),
             "--out", str(tmp / "forged.json"))
    check("forged-rejected", r5.returncode != 0)
    check("forged-names-hash", "decoder_ring_hash" in r5.stderr)
    check("forged-wrote-nothing", not (tmp / "forged.json").exists())

    # --update snapshots then mutates
    cs = tmp / "cs.json"
    cs.write_text(json.dumps({
        "added": {"r": {"new-rule": {"verdict": "r", "rule": "be real"}}},
        "changed": {},
        "deprecated": {},
        "needs_review": {"x": {"should-not-land": "nope"}},
    }))
    u = run("--root", str(root), "--update", "toy", "--changeset", str(cs),
            "--ledger", str(ledger), "--nrra", str(nrra))
    check("update-ok", u.returncode == 0, u.stderr)
    hist = list((root / ".rosetta" / "docs" / "toy" / ".history").glob("*.json"))
    check("update-snapshot", len(hist) == 1, str(hist))
    g2 = json.loads(dest.read_text())
    check("update-bumped", g2["v"]["kb_version"] == "1.0.1")
    check("update-added", "new-rule" in g2["r"])
    check("update-no-needs-review", "should-not-land" not in (g2.get("x") or {}))

    # legacy unstamped --check warns, does not fail
    legacy_root = tmp / "legacy"
    dest_l = legacy_root / ".rosetta" / "docs" / "old" / "fact-graph.json"
    dest_l.parent.mkdir(parents=True)
    dest_l.write_text(json.dumps({
        "v": {"pkg": "old", "version": "0", "kb_version": "1.0.0"},
        "w": {}, "r": {}, "a": {}, "x": {}, "t": {},
    }))
    lg = run("--root", str(legacy_root), "--check", "old")
    check("legacy-warn-exit0", lg.returncode == 0, lg.stderr)
    check("legacy-warn-text", "legacy" in (lg.stdout + lg.stderr).lower())

    if failures:
        for f in failures:
            print("FAIL:", f, file=sys.stderr)
        return 1
    print("ok — write-graph.py create/update/check + open/mismatch/forged rejected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
