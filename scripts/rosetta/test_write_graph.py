#!/usr/bin/env python3
"""Selftests for write-graph.py against a real temp project + real ledger files."""
from __future__ import annotations

import json
import os
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
        check("create-no-fake-version", g.get("v", {}).get("version") != "0.0.0")
        check("create-version-source", g.get("v", {}).get("version_source") == "undetermined")
        check("create-pinned-date", bool(g.get("v", {}).get("pinned_date")))
        check("create-provenance", (dest.parent / "provenance.json").is_file())
        check("create-fact-sources", (dest.parent / "fact-sources.json").is_file())
        check("create-history-manifest", (dest.parent / ".history" / "manifest.json").is_file())

    # create refuses if exists
    r2 = run("--root", str(root), "--create", "toy", "--nrra", str(nrra), "--ledger", str(ledger))
    check("create-refuses-existing", r2.returncode != 0)
    check("create-refuses-names-check", "overwrite" in r2.stderr.lower() or "existing" in r2.stderr.lower())

    # --check green on freshly stamped
    c = run("--root", str(root), "--check", "toy")
    check("check-fresh", c.returncode == 0, c.stderr)

    # --composed-with: stamp _meta.composed_with with the composed graph's kb_version
    comp_root = tmp / "comp-proj"
    (comp_root / ".git").mkdir(parents=True)
    comp_docs = comp_root / ".rosetta" / "docs"
    (comp_docs / "rust").mkdir(parents=True)
    (comp_docs / "rust" / "fact-graph.json").write_text(
        json.dumps({"v": {"pkg": "rust", "kb_version": "3.1.0"}}))
    rcw = run("--root", str(comp_root), "--create", "toy", "--nrra", str(nrra),
              "--ledger", str(ledger), "--composed-with", "rust")
    check("composed-with-create", rcw.returncode == 0, f"{rcw.returncode} {rcw.stderr}")
    comp_dest = comp_docs / "toy" / "fact-graph.json"
    if comp_dest.is_file():
        cg = json.loads(comp_dest.read_text())
        got_cw = (cg.get("_meta") or {}).get("composed_with")
        check("composed-with-stamped",
              got_cw == [{"graph": "rust", "kb_version": "3.1.0"}], repr(got_cw))
        cc = run("--root", str(comp_root), "--check", "toy")
        check("composed-with-check-green", cc.returncode == 0, cc.stderr)
    else:
        check("composed-with-wrote", False, str(comp_dest))
    # unknown slug fails closed — a claimed composition must be backed by a real graph
    rcb = run("--root", str(comp_root), "--create", "toy2", "--nrra", str(nrra),
              "--ledger", str(ledger), "--composed-with", "no-such-graph")
    check("composed-with-unknown-dies", rcb.returncode != 0 and "no fact-graph" in rcb.stderr,
          rcb.stderr.strip())

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
    hist = list((root / ".rosetta" / "docs" / "toy" / ".history").glob("*-v*.json"))
    check("update-snapshot", len(hist) == 1, str(hist))
    g2 = json.loads(dest.read_text())
    check("update-bumped", g2["v"]["kb_version"] == "1.1.0")  # keys added → minor
    check("update-added", "new-rule" in g2["r"])
    check("update-no-needs-review", "should-not-land" not in (g2.get("x") or {}))

    # v.version from a real package.json
    repo = tmp / "pkg-repo"
    repo.mkdir()
    (repo / "package.json").write_text(json.dumps({"name": "toy", "version": "4.5.6"}))
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "v"],
        cwd=repo, check=True, capture_output=True,
    )
    pkg_root = tmp / "pkg-proj"
    rpkg = run("--root", str(pkg_root), "--create", "pkgtoy", "--nrra", str(nrra),
               "--ledger", str(ledger), "--source-repo", str(repo),
               "--out", str(pkg_root / ".rosetta" / "docs" / "pkgtoy" / "fact-graph.json"))
    check("pkg-create", rpkg.returncode == 0, rpkg.stderr)
    gpkg = json.loads((pkg_root / ".rosetta" / "docs" / "pkgtoy" / "fact-graph.json").read_text())
    check("pkg-version", gpkg["v"]["version"] == "4.5.6", str(gpkg["v"]))
    check("pkg-source", "package.json" in str(gpkg["v"].get("version_source")))

    # r verdict flip to REJECT → major
    cs_maj = tmp / "cs-maj.json"
    cs_maj.write_text(json.dumps({
        "added": {},
        "changed": {"r": {"new-rule": {"verdict": "REJECT", "rule": "be real"}}},
        "deprecated": {},
    }))
    before = json.loads(dest.read_text())["v"]["kb_version"]
    um = run("--root", str(root), "--update", "toy", "--changeset", str(cs_maj),
             "--ledger", str(ledger), "--nrra", str(nrra))
    check("major-update-ok", um.returncode == 0, um.stderr)
    after = json.loads(dest.read_text())["v"]["kb_version"]
    check("major-bump", after.split(".")[0] != before.split(".")[0], f"{before} → {after}")

    # note-only edit → patch
    cs_note = tmp / "cs-note.json"
    cs_note.write_text(json.dumps({
        "added": {},
        "changed": {"w": {"Widget": {"t": "sl", "i": "toy", "note": "a slightly different box"}}},
        "deprecated": {},
    }))
    before2 = json.loads(dest.read_text())["v"]["kb_version"]
    un = run("--root", str(root), "--update", "toy", "--changeset", str(cs_note),
             "--ledger", str(ledger), "--nrra", str(nrra))
    check("patch-update-ok", un.returncode == 0, un.stderr)
    after2 = json.loads(dest.read_text())["v"]["kb_version"]
    bp, ap = before2.split("."), after2.split(".")
    check("patch-bump", bp[0] == ap[0] and bp[1] == ap[1] and int(ap[2]) == int(bp[2]) + 1,
          f"{before2} → {after2}")

    # source field lands in fact-sources.json
    cs_src = tmp / "cs-src.json"
    cs_src.write_text(json.dumps({
        "added": {"a": {"open": {"value": "open()", "source": "README.md:1", "confidence": "HIGH"}}},
        "changed": {},
        "deprecated": {},
    }))
    us = run("--root", str(root), "--update", "toy", "--changeset", str(cs_src),
             "--ledger", str(ledger), "--nrra", str(nrra))
    check("source-update-ok", us.returncode == 0, us.stderr)
    fs = json.loads((dest.parent / "fact-sources.json").read_text())
    check("source-mapped", fs.get("a:open", {}).get("source") == "README.md:1", str(fs))
    gsrc = json.loads(dest.read_text())
    check("source-not-in-graph-value", gsrc["a"].get("open") == "open()", str(gsrc.get("a")))

    # deprecation prepends MIGRATION with Sources
    cs_dep = tmp / "cs-dep.json"
    cs_dep.write_text(json.dumps({
        "added": {},
        "changed": {},
        "deprecated": {"x": {"use div": "use Widget"}},
    }))
    ud = run("--root", str(root), "--update", "toy", "--changeset", str(cs_dep),
             "--ledger", str(ledger), "--nrra", str(nrra))
    check("dep-update-ok", ud.returncode == 0, ud.stderr)
    mig = (dest.parent / "MIGRATION.md").read_text()
    check("mig-prepend-sources", "**Sources:**" in mig)
    check("mig-starts-heading", mig.lstrip().startswith("## "))

    # --- --apply-nrra: dest missing → create persist; dest present → add/change only ---
    apply_root = tmp / "apply-proj"
    (apply_root / ".git").mkdir(parents=True)
    apply_dest = apply_root / ".rosetta" / "docs" / "toy" / "fact-graph.json"
    r_ac = run("--root", str(apply_root), "--apply-nrra", "toy",
               "--nrra", str(nrra), "--ledger", str(ledger))
    check("apply-create-ok", r_ac.returncode == 0, r_ac.stderr)
    check("apply-create-wrote", apply_dest.is_file(), str(apply_dest))
    if apply_dest.is_file():
        ga = json.loads(apply_dest.read_text())
        check("apply-create-stamp", bool((ga.get("_meta") or {}).get("assimilation", {}).get("graph_sha256")))
        check("apply-create-siblings", (apply_dest.parent / "provenance.json").is_file())
        check("apply-create-fact-sources", (apply_dest.parent / "fact-sources.json").is_file())
        check("apply-create-history", (apply_dest.parent / ".history" / "manifest.json").is_file())
        check("apply-create-kb", ga.get("v", {}).get("kb_version") == "1.0.0")

    planted = json.loads(apply_dest.read_text())
    planted["v"]["pkg"] = "toy"
    planted["v"]["version"] = "1.2.3"
    planted["r"]["keep-me"] = {"verdict": "r", "rule": "unrelated planted key"}
    planted["w"]["KeepWidget"] = {"t": "sl", "i": "toy", "note": "must survive"}
    apply_dest.write_text(json.dumps(planted, indent=2) + "\n")
    apply_nrra2 = tmp / "apply-nrra2.md"
    apply_nrra2.write_text(NRRA.replace(
        '"v": {"pkg": "toy", "version": "1.2.3"}',
        '"v": {"pkg": "toy-moved", "version": "9.9.9"}',
    ).replace(
        '"r": {"no-stub": {"verdict": "REJECT", "rule": "no stubs"}}',
        '"r": {"no-stub": {"verdict": "REJECT", "rule": "no stubs"}, '
        '"new-from-apply": {"verdict": "r", "rule": "added by apply"}}',
    ).replace(
        '"t": {}',
        '"t": {},\n  "deprecated": {"r": {"keep-me": "gone"}},\n'
        '  "needs_review": {"x": {"should-not-land": "nope"}}',
    ).replace(
        '"note": "a box"',
        '"note": "a slightly different box"',
    ))
    r_au = run("--root", str(apply_root), "--apply-nrra", "toy",
               "--nrra", str(apply_nrra2), "--ledger", str(ledger))
    check("apply-update-ok", r_au.returncode == 0, r_au.stderr)
    gu = json.loads(apply_dest.read_text())
    check("apply-no-delete-r", "keep-me" in (gu.get("r") or {}), str(gu.get("r")))
    check("apply-no-delete-w", "KeepWidget" in (gu.get("w") or {}), str(gu.get("w")))
    check("apply-added", "new-from-apply" in (gu.get("r") or {}), str(gu.get("r")))
    check("apply-changed-note", (gu.get("w") or {}).get("Widget", {}).get("note") == "a slightly different box",
          str((gu.get("w") or {}).get("Widget")))
    check("apply-pin-pkg-unwritten", gu.get("v", {}).get("pkg") == "toy", str(gu.get("v")))
    check("apply-pin-version-unwritten", gu.get("v", {}).get("version") == "1.2.3", str(gu.get("v")))
    pin_out = (r_au.stdout + r_au.stderr).lower()
    check("apply-pin-reported-pkg", "pkg" in pin_out and "toy-moved" in (r_au.stdout + r_au.stderr),
          r_au.stdout + r_au.stderr)
    check("apply-pin-reported-version", "9.9.9" in (r_au.stdout + r_au.stderr), r_au.stdout + r_au.stderr)
    check("apply-no-needs-review", "should-not-land" not in (gu.get("x") or {}), str(gu.get("x")))

    # fail-closed: open ledger / mismatch / missing hash write nothing
    apply_open_out = tmp / "apply-open-should-not-exist.json"
    r_ao = run("--root", str(apply_root), "--apply-nrra", "open-toy",
               "--nrra", str(nrra), "--ledger", str(open_p), "--out", str(apply_open_out))
    check("apply-open-rejected", r_ao.returncode != 0, r_ao.stderr)
    check("apply-open-names-check", "ledger-closed" in r_ao.stderr, r_ao.stderr)
    check("apply-open-wrote-nothing", not apply_open_out.exists())

    before_apply = apply_dest.read_text()
    r_ao2 = run("--root", str(apply_root), "--apply-nrra", "toy",
                "--nrra", str(nrra), "--ledger", str(open_p))
    check("apply-open-existing-rejected", r_ao2.returncode != 0)
    check("apply-open-existing-unchanged", apply_dest.read_text() == before_apply)

    apply_mismatch_out = tmp / "apply-mismatch.json"
    r_am = run("--root", str(apply_root), "--apply-nrra", "mismatch",
               "--nrra", str(bad_nrra), "--ledger", str(ledger), "--out", str(apply_mismatch_out))
    check("apply-mismatch-rejected", r_am.returncode != 0)
    check("apply-mismatch-names", "run_id" in r_am.stderr, r_am.stderr)
    check("apply-mismatch-wrote-nothing", not apply_mismatch_out.exists())

    apply_hash_out = tmp / "apply-forged.json"
    r_ah = run("--root", str(apply_root), "--apply-nrra", "forged",
               "--nrra", str(nrra2), "--ledger", str(nh), "--out", str(apply_hash_out))
    check("apply-forged-rejected", r_ah.returncode != 0)
    check("apply-forged-names", "decoder_ring_hash" in r_ah.stderr, r_ah.stderr)
    check("apply-forged-wrote-nothing", not apply_hash_out.exists())

    # --create still refuses overwrite (existing dest from first create in this file)
    r_cr = run("--root", str(root), "--create", "toy", "--nrra", str(nrra), "--ledger", str(ledger))
    check("create-still-refuses-after-apply-tests", r_cr.returncode != 0)
    check("create-still-names-overwrite", "overwrite" in r_cr.stderr.lower() or "existing" in r_cr.stderr.lower())

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

    # --- --backfill: real HTTP + real ls-remote + real local file ---
    import http.server
    import threading

    class _BackfillHandler(http.server.BaseHTTPRequestHandler):
        body = b"backfill-docs-v1"
        etag = '"bf-v1"'

        def log_message(self, fmt: str, *args: object) -> None:  # noqa: ARG002
            return

        def do_GET(self) -> None:  # noqa: N802
            inm = self.headers.get("If-None-Match")
            if inm == self.etag:
                self.send_response(304)
                self.send_header("ETag", self.etag)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("ETag", self.etag)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(self.body)))
            self.end_headers()
            self.wfile.write(self.body)

    httpd = http.server.HTTPServer(("127.0.0.1", 0), _BackfillHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    host, port = httpd.server_address[:2]
    docs_url = f"http://{host}:{port}/guide"
    try:
        bf_root = tmp / "bf-proj"
        # docs graph
        docs_kb = bf_root / ".rosetta" / "docs" / "docsish"
        docs_kb.mkdir(parents=True)
        (docs_kb / "fact-graph.json").write_text(json.dumps({
            "v": {"pkg": "docsish", "version": "3.2.1", "kb_version": "1.0.0", "pinned": "2026-06"},
            "_meta": {"sources": [docs_url]},
            "w": {}, "r": {}, "a": {}, "x": {}, "t": {},
        }))
        (docs_kb / "README.md").write_text("# Docsish\n\n- **KB version**: 1.0.0 · **corpus**: 4f53cda1\n")
        bf = run("--root", str(bf_root), "--backfill", "docsish")
        check("backfill-docs-cli", bf.returncode == 0, bf.stdout + bf.stderr)
        if not (docs_kb / "provenance.json").is_file():
            check("backfill-docs-wrote", False, bf.stdout + bf.stderr)
            httpd.shutdown()
            if failures:
                for f in failures:
                    print("FAIL:", f, file=sys.stderr)
                return 1
            return 1
        prov = json.loads((docs_kb / "provenance.json").read_text())
        check("backfill-status", prov.get("status") == "backfill-now", str(prov.get("status")))
        check("backfill-kb-untouched", json.loads((docs_kb / "fact-graph.json").read_text())["v"]["kb_version"] == "1.0.0")
        check("backfill-version-kept", json.loads((docs_kb / "fact-graph.json").read_text())["v"]["version"] == "3.2.1")
        check("backfill-not-legacy", not any(
            (p.get("kind") == "legacy") or (p.get("status") == "legacy-unknown")
            for p in (prov.get("sources") or [])
        ), str(prov.get("sources")))
        units = []
        for p in prov.get("sources") or []:
            units.extend(p.get("unitPins") or [])
        check("backfill-etag", any(u.get("etag") == _BackfillHandler.etag or p.get("etag") == _BackfillHandler.etag
                                   for p in (prov.get("sources") or []) for u in ([p] + (p.get("unitPins") or []))),
              str(prov.get("sources")))
        check("backfill-unit-hash", any(u.get("sha256") for u in units), str(units))
        check("backfill-corpus-not-empty", (prov.get("corpus_sha256") or "")[:8] != "4f53cda1",
              str(prov.get("corpus_sha256")))
        readme = (docs_kb / "README.md").read_text()
        check("backfill-readme-corpus", "4f53cda1" not in readme or "corpus**: 4f53cda1" not in readme)

        import provenance as _P  # noqa: E402
        probe = HERE / "provenance.py"
        def run_probe(*a: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run([sys.executable, str(probe), *a], capture_output=True, text=True)
        p1 = run_probe("--root", str(bf_root), "--check-stale", "docsish")
        p2 = run_probe("--root", str(bf_root), "--check-stale", "docsish")
        check("backfill-stale-0a", p1.returncode == 0, p1.stdout + p1.stderr)
        check("backfill-stale-0b", p2.returncode == 0 and p1.stdout == p2.stdout, p2.stdout)

        _BackfillHandler.body = b"backfill-docs-CHANGED"
        _BackfillHandler.etag = '"bf-v2"'
        p3 = run_probe("--root", str(bf_root), "--check-stale", "docsish")
        p4 = run_probe("--root", str(bf_root), "--check-stale", "docsish")
        check("backfill-stale-10a", p3.returncode == 10, p3.stdout)
        check("backfill-stale-10b", p4.returncode == 10)
        check("backfill-stale-names", docs_url in p3.stdout, p3.stdout)

        # repo graph via file:// bare remote
        work = tmp / "repo-work"
        work.mkdir()
        (work / "README.md").write_text("repo\n")
        subprocess.run(["git", "init"], cwd=work, check=True, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=work, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "i"],
            cwd=work, check=True, capture_output=True,
        )
        remote = tmp / "repo-remote.git"
        subprocess.run(["git", "clone", "--bare", str(work), str(remote)], check=True, capture_output=True)
        head = subprocess.check_output(["git", "-C", str(work), "rev-parse", "HEAD"], text=True).strip()
        remote_url = remote.resolve().as_uri()
        repo_kb = bf_root / ".rosetta" / "docs" / "repoish"
        repo_kb.mkdir(parents=True)
        (repo_kb / "fact-graph.json").write_text(json.dumps({
            "v": {"pkg": "repoish", "version": "9.0.0", "kb_version": "1.0.0", "repo": remote_url},
            "w": {}, "r": {}, "a": {}, "x": {}, "t": {},
        }))
        br = run("--root", str(bf_root), "--backfill", "repoish")
        check("backfill-repo-cli", br.returncode == 0, br.stdout + br.stderr)
        rprov = json.loads((repo_kb / "provenance.json").read_text())
        repo_pins = [p for p in (rprov.get("sources") or []) if p.get("kind") == "repo"]
        check("backfill-repo-commit", any(p.get("commit") == head for p in repo_pins), str(repo_pins))

        # local-only graph
        local_kb = bf_root / ".rosetta" / "docs" / "localish"
        local_kb.mkdir(parents=True)
        local_doc = local_kb / "concept.md"
        local_doc.write_text("local concept v1\n")
        (local_kb / "fact-graph.json").write_text(json.dumps({
            "v": {"pkg": "localish", "kb_version": "1.0.0"},
            "w": {"Thing": {"t": "sl", "i": "x", "src": "brain/docs/localish/concept.md"}},
            "r": {}, "a": {}, "x": {}, "t": {},
        }))
        bl = run("--root", str(bf_root), "--backfill", "localish")
        check("backfill-local-cli", bl.returncode == 0, bl.stdout + bl.stderr)
        lprov = json.loads((local_kb / "provenance.json").read_text())
        check("backfill-local-kind", any(p.get("kind") == "local" for p in (lprov.get("sources") or [])),
              str(lprov.get("sources")))
        lfs = json.loads((local_kb / "fact-sources.json").read_text())
        check("backfill-local-fact-src", "w:Thing" in lfs, str(lfs))
        lp1 = run_probe("--root", str(bf_root), "--check-stale", "localish")
        lp2 = run_probe("--root", str(bf_root), "--check-stale", "localish")
        check("backfill-local-current-1", lp1.returncode == 0, lp1.stdout + lp1.stderr)
        check("backfill-local-current-2", lp2.returncode == 0)
        local_doc.write_text("local concept CHANGED\n")
        lp3 = run_probe("--root", str(bf_root), "--check-stale", "localish")
        check("backfill-local-stale", lp3.returncode == 10, lp3.stdout)

        # prose-only: fact-sources entry, no invented http, undeterminable or recorded reason
        prose_kb = bf_root / ".rosetta" / "docs" / "proseish"
        prose_kb.mkdir(parents=True)
        (prose_kb / "fact-graph.json").write_text(json.dumps({
            "v": {"pkg": "proseish", "kb_version": "1.0.0"},
            "w": {"Zed": {"t": "sl", "i": "z", "src": "see the holocron note on widgets"}},
            "r": {}, "a": {}, "x": {}, "t": {},
        }))
        (prose_kb / "README.md").write_text("Sources: holocron id xyz, no url here.\n")
        bp = run("--root", str(bf_root), "--backfill", "proseish")
        check("backfill-prose-cli", bp.returncode == 0, bp.stdout + bp.stderr)
        pfs = json.loads((prose_kb / "fact-sources.json").read_text())
        check("backfill-prose-fact-src", len(pfs) >= 1, str(pfs))
        pprov = json.loads((prose_kb / "provenance.json").read_text())
        http_pins = [
            p for p in (pprov.get("sources") or [])
            if str(p.get("url") or "").startswith("http")
        ]
        check("backfill-prose-no-invented-http", http_pins == [], str(http_pins))

        # judged overlay supplies the official source when the graph has none
        ov_kb = bf_root / ".rosetta" / "docs" / "overlayish"
        ov_kb.mkdir(parents=True)
        (ov_kb / "fact-graph.json").write_text(json.dumps({
            "v": {"pkg": "overlayish", "kb_version": "1.0.0"},
            "w": {}, "r": {}, "a": {}, "x": {}, "t": {},
        }))
        ov_path = tmp / "sources.json"
        ov_path.write_text(json.dumps({
            "overlayish": {"docs_url": docs_url, "version": "8.1.0", "version_source": docs_url},
        }))
        bo = run("--root", str(bf_root), "--backfill", "overlayish", "--sources", str(ov_path))
        check("backfill-overlay-cli", bo.returncode == 0, bo.stdout + bo.stderr)
        oprov = json.loads((ov_kb / "provenance.json").read_text())
        check("backfill-overlay-status", oprov.get("status") == "backfill-now", str(oprov))
        check("backfill-overlay-docs", any(
            docs_url in str(p.get("url") or "") or any(docs_url in str(u.get("locator") or "") for u in (p.get("unitPins") or []))
            for p in (oprov.get("sources") or [])
        ), str(oprov.get("sources")))
        og = json.loads((ov_kb / "fact-graph.json").read_text())
        check("backfill-overlay-version", og["v"].get("version") == "8.1.0", str(og.get("v")))

        # --all walks .rosetta/docs/*/fact-graph.json
        extra = bf_root / ".rosetta" / "docs" / "allish"
        extra.mkdir(parents=True)
        (extra / "fact-graph.json").write_text(json.dumps({
            "v": {"pkg": "allish", "kb_version": "1.0.0", "docs_url": docs_url},
            "w": {}, "r": {}, "a": {}, "x": {}, "t": {},
        }))
        ba = run("--root", str(bf_root), "--backfill", "--all")
        check("backfill-all-cli", ba.returncode == 0, ba.stdout + ba.stderr)
        check("backfill-all-wrote", (extra / "provenance.json").is_file())
    finally:
        httpd.shutdown()

    # ── storage root comes from config, not from cwd ──────────────────────────────────────────
    # REGRESSION: with no --root the writer used Path.cwd(), so the KB landed in whatever repo
    # the session happened to be standing in and NO config setting could move it.
    cfg_home = tmp / "cfg-home"
    (cfg_home / "bin").mkdir(parents=True)
    central = tmp / "central-kb"
    central.mkdir()
    elsewhere = tmp / "elsewhere"
    (elsewhere / ".git").mkdir(parents=True)

    def run_in(cwd: Path, env_extra: dict, *args: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.pop("ROSETTA_PROJECT", None)
        env.pop("ROSETTA_CONFIG", None)
        env.update({k: str(v) for k, v in env_extra.items()})
        return subprocess.run([sys.executable, str(WG), *args],
                              capture_output=True, text=True, cwd=str(cwd), env=env)

    (cfg_home / "config.json").write_text(json.dumps(
        {"version": 1, "roots": {"default": "project", "docs": str(central)}}))

    r = run_in(elsewhere, {"ROSETTA_HOME": cfg_home},
               "--create", "cfgtoy", "--nrra", str(nrra), "--ledger", str(ledger))
    check("cfg-create-ok", r.returncode == 0, r.stdout + r.stderr)
    check("cfg-goes-to-configured-root",
          (central / ".rosetta" / "docs" / "cfgtoy" / "fact-graph.json").is_file(),
          r.stdout + r.stderr)
    check("cfg-does-not-touch-cwd",
          not (elsewhere / ".rosetta").exists(),
          str(list(elsewhere.rglob("*"))))

    # roots.docs = "device": the configured root IS the .rosetta dir — no nested .rosetta.
    (cfg_home / "config.json").write_text(json.dumps(
        {"version": 1, "roots": {"default": "project", "docs": "device"}}))
    r = run_in(elsewhere, {"ROSETTA_HOME": cfg_home},
               "--create", "devtoy", "--nrra", str(nrra), "--ledger", str(ledger))
    check("cfg-device-ok", r.returncode == 0, r.stdout + r.stderr)
    check("cfg-device-layout", (cfg_home / "docs" / "devtoy" / "fact-graph.json").is_file(),
          r.stdout + r.stderr)

    # An explicit --root is an instruction and must still beat the config.
    pinned = tmp / "pinned"
    pinned.mkdir()
    r = run_in(elsewhere, {"ROSETTA_HOME": cfg_home},
               "--root", str(pinned), "--create", "pintoy",
               "--nrra", str(nrra), "--ledger", str(ledger))
    check("cfg-root-flag-wins",
          (pinned / ".rosetta" / "docs" / "pintoy" / "fact-graph.json").is_file(),
          r.stdout + r.stderr)
    check("cfg-root-flag-skips-config",
          not (cfg_home / "docs" / "pintoy").exists(), r.stdout + r.stderr)

    # $ROSETTA_PROJECT is also an explicit pin and beats the config.
    envproj = tmp / "envproj"
    envproj.mkdir()
    r = run_in(elsewhere, {"ROSETTA_HOME": cfg_home, "ROSETTA_PROJECT": envproj},
               "--create", "envtoy", "--nrra", str(nrra), "--ledger", str(ledger))
    check("cfg-env-project-wins",
          (envproj / ".rosetta" / "docs" / "envtoy" / "fact-graph.json").is_file(),
          r.stdout + r.stderr)

    if failures:
        for f in failures:
            print("FAIL:", f, file=sys.stderr)
        return 1
    print("ok — write-graph.py create/apply-nrra/update/check + open/mismatch/forged rejected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
