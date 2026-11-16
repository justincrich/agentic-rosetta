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

    # ── composition: copy by value, reverse query, confirm-gated propagation ──────────────
    def run_no_tty(*args: str) -> subprocess.CompletedProcess[str]:
        """A genuinely non-interactive run: stdin is /dev/null, so isatty() is False."""
        return subprocess.run(
            [sys.executable, str(WG), *args],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )

    compose_root = tmp / "compose-proj"
    (compose_root / ".git").mkdir(parents=True)
    cdocs = compose_root / ".rosetta" / "docs"

    def graph_at(tech: str) -> dict:
        return json.loads((cdocs / tech / "fact-graph.json").read_text())

    def kb_of(tech: str) -> str:
        return graph_at(tech)["v"]["kb_version"]

    # the original, one real fact at a time, all through the writer
    r = run("--root", str(compose_root), "--create", "rust", "--nrra", str(nrra),
            "--ledger", str(ledger))
    check("compose-rust-create", r.returncode == 0, r.stderr)
    rust_step1 = tmp / "rust-step1.json"
    rust_step1.write_text(json.dumps({
        "added": {"r": {"unwrap_in_library_code": {"verdict": "REJECT", "rule": "no unwrap"}}},
        "changed": {}, "deprecated": {},
    }))
    r = run("--root", str(compose_root), "--update", "rust", "--changeset", str(rust_step1),
            "--ledger", str(ledger), "--nrra", str(nrra))
    check("compose-rust-step1", r.returncode == 0, r.stderr)
    rust_kb_a = kb_of("rust")

    for borrower_tech in ("herdrdev", "mobile"):
        r = run("--root", str(compose_root), "--create", borrower_tech, "--nrra", str(nrra),
                "--ledger", str(ledger), "--composed-with", "rust")
        check(f"compose-{borrower_tech}-create", r.returncode == 0, r.stderr)

    herdrdev_path = cdocs / "herdrdev" / "fact-graph.json"
    mobile_path = cdocs / "mobile" / "fact-graph.json"
    hg = graph_at("herdrdev")
    check("compose-body-copies-rule",
          hg["composed"]["rust"]["facts"]["r"]["unwrap_in_library_code"]["rule"] == "no unwrap",
          repr(hg.get("composed")))
    check("compose-body-records-edition", hg["composed"]["rust"]["kb_version"] == rust_kb_a,
          repr(hg["composed"]["rust"].get("kb_version")))
    check("compose-meta-mirrors-body",
          hg["_meta"]["composed_with"] == [{"graph": "rust", "kb_version": rust_kb_a}],
          repr(hg["_meta"].get("composed_with")))
    check("compose-borrower-own-body-unchanged",
          "unwrap_in_library_code" not in (hg.get("r") or {})
          and hg["v"]["kb_version"] == "1.0.0", repr(list(hg.get("r") or {})))

    # AC-1: after the original moves, one command lists every borrower with both editions
    rust_step2 = tmp / "rust-step2.json"
    rust_step2.write_text(json.dumps({
        "added": {"r": {"use_question_mark": {"verdict": "r", "rule": "propagate with ?"}}},
        "changed": {}, "deprecated": {},
    }))
    r = run("--root", str(compose_root), "--update", "rust", "--changeset", str(rust_step2),
            "--ledger", str(ledger), "--nrra", str(nrra))
    check("compose-rust-step2", r.returncode == 0, r.stderr)
    rust_kb_b = kb_of("rust")
    before_listing = herdrdev_path.read_text()

    # AC-5: non-interactive, no --confirm → refuse, write nothing, but still list borrowers
    r = run_no_tty("--root", str(compose_root), "--propagate", "rust")
    check("propagate-refuses-without-confirm", r.returncode != 0, f"(rc={r.returncode})")
    check("propagate-names-confirm-check",
          "confirmation required" in r.stderr, r.stderr)
    check("propagate-lists-borrowers",
          "herdrdev" in r.stdout and "mobile" in r.stdout, r.stdout)
    check("propagate-lists-recorded-and-current",
          f"recorded={rust_kb_a}" in r.stdout and f"current={rust_kb_b}" in r.stdout,
          r.stdout)
    check("propagate-refused-wrote-nothing", herdrdev_path.read_text() == before_listing)

    # AC-1: a named subset moves only that borrower
    r = run("--root", str(compose_root), "--propagate", "rust", "--confirm",
            "--borrowers", "mobile")
    check("propagate-subset-exit0", r.returncode == 0, r.stdout + r.stderr)
    check("propagate-subset-moved-mobile", "use_question_mark" in graph_at("mobile")["composed"]["rust"]["facts"]["r"],
          repr(list(graph_at("mobile")["composed"]["rust"]["facts"]["r"])))
    check("propagate-subset-left-herdrdev",
          herdrdev_path.read_text() == before_listing)

    # AC-1: the default is update ALL — every stale borrower moves, and one already current
    # is reported as current rather than churned.
    # The refresh rewrites the sibling files too. Plant a README and a research pin first so
    # the assertions below pin what propagate DOES to files it did not create in this test.
    hd_dir = herdrdev_path.parent
    (hd_dir / "README.md").write_text(
        "# herdrdev\n\n- **KB version**: 1.0.0 · **upstream**: undetermined\n")
    hd_prov_path = hd_dir / "provenance.json"
    hd_pin = {"kind": "local", "locator": "README.md", "status": "current"}
    hd_prov = json.loads(hd_prov_path.read_text())
    hd_prov["sources"] = [hd_pin]
    hd_prov_path.write_text(json.dumps(hd_prov, indent=2) + "\n")
    hd_fresh_kb = graph_at("herdrdev")["v"]["kb_version"]

    r = run("--root", str(compose_root), "--propagate", "rust", "--confirm")
    check("propagate-all-exit0", r.returncode == 0, r.stdout + r.stderr)
    check("propagate-all-names-every-borrower",
          f"— 2 borrower(s)" in r.stdout
          and "propagated rust -> herdrdev" in r.stdout
          and "SKIP mobile: already current at" in r.stdout,
          r.stdout)
    hg = graph_at("herdrdev")
    check("propagate-all-copied-new-rule",
          "use_question_mark" in hg["composed"]["rust"]["facts"]["r"],
          repr(list(hg["composed"]["rust"]["facts"]["r"])))
    check("propagate-all-meta-edition",
          hg["_meta"]["composed_with"] == [{"graph": "rust", "kb_version": rust_kb_b}],
          repr(hg["_meta"].get("composed_with")))
    check("propagate-all-bumped-host-edition", hg["v"]["kb_version"] != "1.0.0", repr(hg["v"]))
    # Composition is a content change: the host moves by the patch rule, never a judgment call.
    check("propagate-all-bumps-host-edition-by-patch",
          hd_fresh_kb == "1.0.0" and hg["v"]["kb_version"] == "1.0.1",
          f"{hd_fresh_kb} -> {hg['v']['kb_version']}")
    check("propagate-all-supersedes-the-old-edition",
          hg["v"].get("supersedes") == hd_fresh_kb, repr(hg["v"].get("supersedes")))
    # The siblings the refresh rewrites, pinned the same way the --compose case pins them.
    check("propagate-provenance-written", hd_prov_path.is_file())
    hd_prov_after = json.loads(hd_prov_path.read_text())
    check("propagate-provenance-status",
          hd_prov_after.get("status") == "propagated", repr(hd_prov_after.get("status")))
    check("propagate-provenance-kb",
          hd_prov_after.get("kb_version") == hg["v"]["kb_version"],
          repr(hd_prov_after.get("kb_version")))
    check("propagate-provenance-keeps-pins",
          hd_prov_after.get("sources") == [hd_pin], repr(hd_prov_after.get("sources")))
    check("propagate-keeps-fact-sources", (hd_dir / "fact-sources.json").is_file())
    check("propagate-readme-kb-line",
          f"KB version**: {hg['v']['kb_version']}" in (hd_dir / "README.md").read_text(),
          (hd_dir / "README.md").read_text())

    # AC-1's named action: `--propagate <slug> --list` is side-effect free — no prompt, no write
    before_list = herdrdev_path.read_text()
    mobile_before_list = mobile_path.read_text()
    r = run_no_tty("--root", str(compose_root), "--propagate", "rust", "--list")
    check("propagate-list-exit0", r.returncode == 0, r.stdout + r.stderr)
    check("propagate-list-shows-borrowers",
          "herdrdev" in r.stdout and "mobile" in r.stdout, r.stdout)
    check("propagate-list-shows-both-editions",
          f"recorded={rust_kb_b}" in r.stdout and f"current={rust_kb_b}" in r.stdout, r.stdout)
    check("propagate-list-does-not-prompt", "Update all" not in r.stdout, r.stdout)
    check("propagate-list-wrote-nothing",
          herdrdev_path.read_text() == before_list
          and mobile_path.read_text() == mobile_before_list)

    # no-op churn guard: re-propagating a project that is already current skips and does not bump
    hg_before = graph_at("herdrdev")["v"]["kb_version"]
    r = run("--root", str(compose_root), "--propagate", "rust", "--confirm")
    check("propagate-fresh-exit0", r.returncode == 0, r.stdout + r.stderr)
    check("propagate-fresh-skips",
          "SKIP herdrdev: already current at" in r.stdout
          and "SKIP mobile: already current at" in r.stdout, r.stdout)
    check("propagate-fresh-no-bump",
          graph_at("herdrdev")["v"]["kb_version"] == hg_before
          and graph_at("mobile")["v"]["kb_version"] == hg_before,
          f"{hg_before} -> {graph_at('herdrdev')['v']['kb_version']}")
    check("propagate-fresh-wrote-nothing", herdrdev_path.read_text() == before_list)

    # --force gives a deliberate path past the guard
    r = run("--root", str(compose_root), "--propagate", "rust", "--confirm", "--force",
            "--borrowers", "mobile")
    check("propagate-force-exit0", r.returncode == 0, r.stdout + r.stderr)
    check("propagate-force-rewrote",
          "propagated rust -> mobile" in r.stdout
          and graph_at("mobile")["v"]["kb_version"] != hg_before,
          f"{r.stdout} / {graph_at('mobile')['v']['kb_version']}")

    # AC-6: the refresh ran through the writer, so the stamp is current; a hand edit is not
    c = run("--root", str(compose_root), "--check", "herdrdev")
    check("propagate-check-green", c.returncode == 0, c.stderr)
    # AC-8: the composed-version check answers staleness offline
    cc = run("--root", str(compose_root), "--check-composed", "herdrdev")
    check("check-composed-green", cc.returncode == 0, cc.stdout + cc.stderr)

    hand = json.loads(herdrdev_path.read_text())
    hand["composed"]["rust"]["facts"]["r"]["use_question_mark"]["rule"] = "hand edited"
    herdrdev_path.write_text(json.dumps(hand, indent=2) + "\n")
    c = run("--root", str(compose_root), "--check", "herdrdev")
    check("hand-edit-fails-check", c.returncode != 0, f"(rc={c.returncode})")
    check("hand-edit-names-stamp", "stamp" in c.stderr, c.stderr)

    # AC-4: a refresh whose recorded fingerprint no longer matches stops and reports THAT
    hand_bytes = herdrdev_path.read_text()
    rust_step3 = tmp / "rust-step3.json"
    rust_step3.write_text(json.dumps({
        "added": {"r": {"no_unsafe_without_approval": {"verdict": "REJECT", "rule": "no unsafe"}}},
        "changed": {}, "deprecated": {},
    }))
    r = run("--root", str(compose_root), "--update", "rust", "--changeset", str(rust_step3),
            "--ledger", str(ledger), "--nrra", str(nrra))
    check("compose-rust-step3", r.returncode == 0, r.stderr)
    rust_kb_c = kb_of("rust")
    r = run("--root", str(compose_root), "--propagate", "rust", "--confirm")
    check("propagate-diverged-exit10", r.returncode == 10, f"(rc={r.returncode}) {r.stdout}")
    check("propagate-diverged-reported", "diverged" in (r.stdout + r.stderr), r.stdout + r.stderr)
    check("propagate-diverged-fingerprint-wording",
          "copy fingerprint mismatch" in r.stderr, r.stderr)
    check("propagate-diverged-does-not-name-the-fact",
          "use_question_mark" not in (r.stdout + r.stderr), r.stdout + r.stderr)
    check("propagate-diverged-wrote-nothing", herdrdev_path.read_text() == hand_bytes)
    check("propagate-per-borrower-outcome",
          "propagated rust -> mobile" in r.stdout
          and graph_at("mobile")["composed"]["rust"]["kb_version"] == rust_kb_c,
          r.stdout)

    # AC-4 on the OTHER documented refresh path. `--update <host> --composed-with <orig>` is
    # the research-refresh route the upgrade skill names, and it used to overwrite a diverged
    # copy with exit 0 (verified: the local edit vanished). It must refuse like --compose.
    update_diverged_bytes = herdrdev_path.read_text()
    r = run("--root", str(compose_root), "--update", "herdrdev",
            "--changeset", str(rust_step3), "--ledger", str(ledger),
            "--composed-with", "rust")
    check("update-composed-diverged-exit10", r.returncode == 10,
          f"(rc={r.returncode}) {r.stdout}{r.stderr}")
    check("update-composed-diverged-wrote-nothing",
          herdrdev_path.read_text() == update_diverged_bytes)
    check("update-composed-diverged-names-mismatch",
          "copy fingerprint mismatch" in r.stderr, r.stderr)

    # AC-8: a fresh copy that is merely behind fails the composed-version check as stale
    rust_step4 = tmp / "rust-step4.json"
    rust_step4.write_text(json.dumps({
        "added": {"r": {"prefer_result": {"verdict": "r", "rule": "return Result"}}},
        "changed": {}, "deprecated": {},
    }))
    r = run("--root", str(compose_root), "--update", "rust", "--changeset", str(rust_step4),
            "--ledger", str(ledger), "--nrra", str(nrra))
    check("compose-rust-step4", r.returncode == 0, r.stderr)
    rust_kb_d = kb_of("rust")
    cc = run("--root", str(compose_root), "--check-composed", "mobile")
    check("check-composed-stale-exit10", cc.returncode == 10, f"(rc={cc.returncode})")
    check("check-composed-reports-recorded-and-current",
          f"recorded={rust_kb_c}" in cc.stdout and f"current={rust_kb_d}" in cc.stdout,
          cc.stdout)
    check("check-composed-reports-stale", "stale" in (cc.stdout + cc.stderr), cc.stdout + cc.stderr)
    check("check-composed-stale-line", "[write-graph] STALE" in cc.stderr, cc.stderr)

    # the same code covers a diverged copy (not current, and not a schema error)
    cc = run("--root", str(compose_root), "--check-composed", "herdrdev")
    check("check-composed-diverged-exit10", cc.returncode == 10, f"(rc={cc.returncode})")
    check("check-composed-diverged-status", "diverged" in cc.stdout, cc.stdout)

    # a real error is not the stale code: no graph at all → 2; nothing composed → 1
    cc = run("--root", str(compose_root), "--check-composed", "no-such-graph")
    check("check-composed-missing-graph-exit2", cc.returncode == 2, f"(rc={cc.returncode})")
    cc = run("--root", str(compose_root), "--check-composed", "rust")
    check("check-composed-nothing-exit1", cc.returncode == 1, f"(rc={cc.returncode})")

    # ── --update honours --composed-with: composing two graphs that already exist ──────────
    # The real use is `--update herdrdev --composed-with rust` (both graphs predate the
    # composition). Before this, the flag was accepted and silently ignored: exit 0, normal
    # refresh, no composition — the exact silent fallback composition exists to prevent.
    empty_cs = tmp / "empty-cs.json"
    empty_cs.write_text(json.dumps({"added": {}, "changed": {}, "deprecated": {}}))

    mobile_before_update = mobile_path.read_text()
    r = run("--root", str(compose_root), "--update", "mobile", "--changeset", str(empty_cs),
            "--ledger", str(ledger), "--nrra", str(nrra), "--composed-with", "ghost")
    check("update-composed-unknown-exit", r.returncode != 0, f"(rc={r.returncode}) {r.stdout}")
    check("update-composed-unknown-names-check", "no fact-graph" in r.stderr, r.stderr.strip())
    check("update-composed-unknown-wrote-nothing",
          mobile_path.read_text() == mobile_before_update)

    mobile_kb_before = graph_at("mobile")["v"]["kb_version"]
    r = run("--root", str(compose_root), "--update", "mobile", "--changeset", str(empty_cs),
            "--ledger", str(ledger), "--nrra", str(nrra), "--composed-with", "rust")
    check("update-composed-ok", r.returncode == 0, r.stdout + r.stderr)
    mg = graph_at("mobile")
    rust_copy = (mg.get("composed") or {}).get("rust") or {}
    copied_facts = rust_copy.get("facts") or {}
    check("update-composed-copied-by-value",
          ((copied_facts.get("r") or {}).get("prefer_result") or {}).get("rule") == "return Result",
          repr(copied_facts.get("r")))
    check("update-composed-records-edition", rust_copy.get("kb_version") == rust_kb_d,
          repr(rust_copy.get("kb_version")))
    check("update-composed-meta-mirrors",
          (mg.get("_meta") or {}).get("composed_with")
          == [{"graph": "rust", "kb_version": rust_kb_d}],
          repr((mg.get("_meta") or {}).get("composed_with")))
    check("update-composed-bumps-host",
          mg["v"]["kb_version"] != mobile_kb_before, mg["v"]["kb_version"])
    check("update-composed-own-body-untouched", "prefer_result" not in (mg.get("r") or {}),
          repr(list(mg.get("r") or {})))
    check("update-composed-reported",
          "composed" in r.stdout and "rust" in r.stdout, r.stdout)
    cu = run("--root", str(compose_root), "--check", "mobile")
    check("update-composed-check-green", cu.returncode == 0, cu.stderr)
    cc = run("--root", str(compose_root), "--check-composed", "mobile")
    check("update-composed-fresh", cc.returncode == 0, cc.stdout + cc.stderr)

    # an update WITHOUT the flag changes no composition — today's behaviour, still true
    before_plain = graph_at("mobile")["composed"]
    r = run("--root", str(compose_root), "--update", "mobile", "--changeset", str(empty_cs),
            "--ledger", str(ledger), "--nrra", str(nrra))
    check("update-without-composed-ok", r.returncode == 0, r.stderr)
    check("update-without-composed-keeps-copy", graph_at("mobile")["composed"] == before_plain,
          "composition changed on a plain update")

    # the flag fails closed on every verb that cannot honour it, instead of being ignored
    for verb in (("--check", "mobile"), ("--adopt", "mobile"), ("--backfill", "mobile"),
                 ("--check-composed", "mobile"), ("--propagate", "rust", "--list"),
                 ("--apply-nrra", "mobile")):
        r = run("--root", str(compose_root), *verb, "--composed-with", "rust",
                "--nrra", str(nrra), "--ledger", str(ledger), "--changeset", str(empty_cs))
        check(f"composed-with-rejected-{verb[0].lstrip('-')}",
              r.returncode == 2 and "usage" in r.stderr, f"(rc={r.returncode}) {r.stderr}")
        check(f"composed-with-rejected-names-verbs-{verb[0].lstrip('-')}",
              "--create" in r.stderr and "--update" in r.stderr, r.stderr)

    # ── --compose: compose into an EXISTING graph with no census ledger ───────────────────
    # Composing Rust into an already-researched host is maintenance, not research. --create
    # and --update both demand a closed census ledger, so manufacturing one just to compose
    # would be a fake research artifact. --compose has no ledger and never creates a graph.
    cv_root = tmp / "compose-verb-proj"
    (cv_root / ".git").mkdir(parents=True)
    cvdocs = cv_root / ".rosetta" / "docs"

    def cv_graph(tech: str) -> dict:
        return json.loads((cvdocs / tech / "fact-graph.json").read_text())

    r = run("--root", str(cv_root), "--create", "rust", "--nrra", str(nrra),
            "--ledger", str(ledger))
    check("cv-rust-create", r.returncode == 0, r.stderr)
    r = run("--root", str(cv_root), "--update", "rust", "--changeset", str(rust_step1),
            "--ledger", str(ledger), "--nrra", str(nrra))
    check("cv-rust-step1", r.returncode == 0, r.stderr)
    rust_cv_kb = cv_graph("rust")["v"]["kb_version"]
    rust_cv_path = cvdocs / "rust" / "fact-graph.json"
    rust_cv_bytes = rust_cv_path.read_text()

    r = run("--root", str(cv_root), "--create", "herdrdev", "--nrra", str(nrra),
            "--ledger", str(ledger))
    check("cv-host-create", r.returncode == 0, r.stderr)
    host_path = cvdocs / "herdrdev" / "fact-graph.json"
    host_kb_before = cv_graph("herdrdev")["v"]["kb_version"]
    # A real KB directory carries a README; the writer updates its KB line in place.
    (host_path.parent / "README.md").write_text(
        "# herdrdev\n\n- **KB version**: 1.0.0 · **upstream**: undetermined\n")

    # the verb under test: no --ledger, no --changeset, no --nrra
    r = run("--root", str(cv_root), "--compose", "herdrdev", "--from", "rust")
    check("compose-ok", r.returncode == 0, r.stdout + r.stderr)
    hg = cv_graph("herdrdev")
    check("compose-copied-by-value",
          ((hg.get("composed") or {}).get("rust", {}).get("facts", {})
           .get("r", {}).get("unwrap_in_library_code", {}).get("rule")) == "no unwrap",
          repr(hg.get("composed")))
    check("compose-records-edition",
          (hg.get("composed") or {}).get("rust", {}).get("kb_version") == rust_cv_kb,
          repr((hg.get("composed") or {}).get("rust")))
    check("compose-meta-mirrors-body",
          (hg.get("_meta") or {}).get("composed_with")
          == [{"graph": "rust", "kb_version": rust_cv_kb}],
          repr((hg.get("_meta") or {}).get("composed_with")))
    check("compose-bumps-host", hg["v"]["kb_version"] != host_kb_before, hg["v"]["kb_version"])
    # Composition is a content change: the host moves by the patch rule, never a judgment call.
    check("compose-bumps-host-edition-by-patch",
          host_kb_before == "1.0.0" and hg["v"]["kb_version"] == "1.0.1",
          f"{host_kb_before} -> {hg['v']['kb_version']}")
    check("compose-supersedes-the-old-edition",
          hg["v"].get("supersedes") == host_kb_before, repr(hg["v"].get("supersedes")))
    check("compose-own-body-untouched", "unwrap_in_library_code" not in (hg.get("r") or {}),
          repr(list(hg.get("r") or {})))
    check("compose-does-not-touch-source", rust_cv_path.read_text() == rust_cv_bytes)
    c = run("--root", str(cv_root), "--check", "herdrdev")
    check("compose-check-green", c.returncode == 0, c.stderr)
    check("compose-keeps-research-stamp",
          bool((hg.get("_meta") or {}).get("assimilation", {}).get("run_id")),
          repr((hg.get("_meta") or {}).get("assimilation")))
    check("compose-provenance-written", (host_path.parent / "provenance.json").is_file())
    prov_cv = json.loads((host_path.parent / "provenance.json").read_text())
    check("compose-provenance-status", prov_cv.get("status") == "composed",
          str(prov_cv.get("status")))
    check("compose-provenance-kb", prov_cv.get("kb_version") == hg["v"]["kb_version"],
          str(prov_cv.get("kb_version")))
    readme_cv = (host_path.parent / "README.md").read_text()
    check("compose-readme-kb-line", f"KB version**: {hg['v']['kb_version']}" in readme_cv,
          readme_cv)
    cc = run("--root", str(cv_root), "--check-composed", "herdrdev")
    check("compose-fresh-after", cc.returncode == 0, cc.stdout + cc.stderr)

    # idempotent: a host already current at that edition is a no-op, byte for byte
    compose_current = host_path.read_text()
    r = run("--root", str(cv_root), "--compose", "herdrdev", "--from", "rust")
    check("compose-fresh-exit0", r.returncode == 0, r.stdout + r.stderr)
    check("compose-fresh-skips",
          f"SKIP herdrdev: already current at {rust_cv_kb}" in r.stdout, r.stdout)
    check("compose-fresh-wrote-nothing", host_path.read_text() == compose_current)

    # --force is the deliberate way past that no-op guard
    r = run("--root", str(cv_root), "--compose", "herdrdev", "--from", "rust", "--force")
    check("compose-force-exit0", r.returncode == 0, r.stdout + r.stderr)
    check("compose-force-rewrote", host_path.read_text() != compose_current)
    check("compose-force-check-green",
          run("--root", str(cv_root), "--check", "herdrdev").returncode == 0)

    # several sources in one run: the current one is skipped, the new one is copied
    r = run("--root", str(cv_root), "--create", "kotlin", "--nrra", str(nrra),
            "--ledger", str(ledger))
    check("cv-kotlin-create", r.returncode == 0, r.stderr)
    kotlin_cv_kb = cv_graph("kotlin")["v"]["kb_version"]
    r = run("--root", str(cv_root), "--compose", "herdrdev", "--from", "rust,kotlin")
    check("compose-multi-exit0", r.returncode == 0, r.stdout + r.stderr)
    check("compose-multi-skips-fresh",
          f"SKIP herdrdev: already current at {rust_cv_kb}" in r.stdout, r.stdout)
    mg_cv = cv_graph("herdrdev")
    check("compose-multi-copied-second",
          (mg_cv.get("composed") or {}).get("kotlin", {}).get("kb_version") == kotlin_cv_kb,
          repr(sorted((mg_cv.get("composed") or {}))))
    check("compose-multi-meta-mirrors-both",
          sorted(e["graph"] for e in (mg_cv.get("_meta") or {}).get("composed_with") or [])
          == ["kotlin", "rust"],
          repr((mg_cv.get("_meta") or {}).get("composed_with")))

    # --compose never creates a graph: no host ⇒ exit 2, naming the path
    r = run("--root", str(cv_root), "--compose", "no-such-host", "--from", "rust")
    check("compose-host-missing-exit2", r.returncode == 2, f"(rc={r.returncode}) {r.stderr}")
    check("compose-host-missing-names-path", "no graph at" in r.stderr, r.stderr)
    check("compose-never-creates", not (cvdocs / "no-such-host").exists())

    # an unknown source dies naming the slug and the path it looked for
    before_unknown = host_path.read_text()
    r = run("--root", str(cv_root), "--compose", "herdrdev", "--from", "ghost")
    check("compose-unknown-exit2", r.returncode == 2, f"(rc={r.returncode})")
    check("compose-unknown-names-slug", "ghost" in r.stderr, r.stderr)
    check("compose-unknown-names-path", "no fact-graph" in r.stderr, r.stderr)
    check("compose-unknown-wrote-nothing", host_path.read_text() == before_unknown)

    # a diverged copy is refused without --force: exit 10, name the slug, write nothing
    hand_cv = json.loads(host_path.read_text())
    if (hand_cv.get("composed") or {}).get("rust"):
        hand_cv["composed"]["rust"]["facts"]["r"]["unwrap_in_library_code"]["rule"] = "hand edited"
    host_path.write_text(json.dumps(hand_cv, indent=2) + "\n")
    diverged_bytes = host_path.read_text()
    r = run("--root", str(cv_root), "--compose", "herdrdev", "--from", "rust")
    check("compose-diverged-exit10", r.returncode == 10, f"(rc={r.returncode}) {r.stdout}")
    check("compose-diverged-fingerprint",
          "copy fingerprint mismatch" in r.stderr, r.stderr)
    check("compose-diverged-names-slug", "rust" in r.stderr, r.stderr)
    check("compose-diverged-wrote-nothing", host_path.read_text() == diverged_bytes)
    # --force is the deliberate overwrite, and it restores the copy the original holds
    r = run("--root", str(cv_root), "--compose", "herdrdev", "--from", "rust", "--force")
    check("compose-force-over-diverged-exit0", r.returncode == 0, r.stdout + r.stderr)
    fg_cv = cv_graph("herdrdev")
    check("compose-force-restored-copy",
          ((fg_cv.get("composed") or {}).get("rust", {}).get("facts", {})
           .get("r", {}).get("unwrap_in_library_code", {}).get("rule")) == "no unwrap",
          repr((fg_cv.get("composed") or {}).get("rust")))
    check("compose-force-diverged-check-green",
          run("--root", str(cv_root), "--check", "herdrdev").returncode == 0)

    # A compose is ONE atomic write to ONE host, so every source is checked before anything
    # is written: a not-yet-composed source listed FIRST must not land when a later source's
    # copy diverged. "new source first" is what makes the ordering the thing under test.
    r = run("--root", str(cv_root), "--create", "scala", "--nrra", str(nrra),
            "--ledger", str(ledger))
    check("cv-scala-create", r.returncode == 0, r.stderr)
    hand_multi = json.loads(host_path.read_text())
    hand_multi["composed"]["rust"]["facts"]["r"]["unwrap_in_library_code"]["rule"] = "hand edited"
    host_path.write_text(json.dumps(hand_multi, indent=2) + "\n")
    multi_bytes = host_path.read_text()
    r = run("--root", str(cv_root), "--compose", "herdrdev", "--from", "scala,rust")
    check("compose-multi-diverged-exit10", r.returncode == 10, f"(rc={r.returncode}) {r.stdout}")
    check("compose-multi-diverged-wrote-nothing", host_path.read_text() == multi_bytes)
    check("compose-multi-diverged-wrote-no-source",
          "scala" not in (cv_graph("herdrdev").get("composed") or {}),
          repr(sorted((cv_graph("herdrdev").get("composed") or {}))))
    # --force is the deliberate overwrite; it composes both sources and restores the copy.
    r = run("--root", str(cv_root), "--compose", "herdrdev", "--from", "scala,rust", "--force")
    check("compose-multi-diverged-force-exit0", r.returncode == 0, r.stdout + r.stderr)
    check("compose-multi-diverged-force-composes-both",
          sorted((cv_graph("herdrdev").get("composed") or {})) == ["kotlin", "rust", "scala"],
          repr(sorted((cv_graph("herdrdev").get("composed") or {}))))
    check("compose-multi-diverged-force-check-green",
          run("--root", str(cv_root), "--check", "herdrdev").returncode == 0)

    # --compose takes no census inputs and no other verb's flags
    for extra in (("--ledger", str(ledger)), ("--changeset", str(empty_cs)),
                  ("--nrra", str(nrra)), ("--out", str(tmp / "nope.json"))):
        r = run("--root", str(cv_root), "--compose", "herdrdev", "--from", "rust", *extra)
        check(f"compose-rejects-{extra[0].lstrip('-')}",
              r.returncode == 2 and "usage" in r.stderr, f"(rc={r.returncode}) {r.stderr}")
    # --from is not a flag of any other verb
    r = run("--root", str(cv_root), "--check", "herdrdev", "--from", "rust")
    check("from-without-compose-exit2",
          r.returncode == 2 and "usage" in r.stderr, f"(rc={r.returncode}) {r.stderr}")
    # --compose cannot ride along with another verb
    r = run("--root", str(cv_root), "--compose", "herdrdev", "--from", "rust",
            "--check", "herdrdev")
    check("compose-plus-other-verb-exit2",
          r.returncode == 2 and "usage" in r.stderr, f"(rc={r.returncode}) {r.stderr}")
    # and --composed-with stays restricted to --create / --update
    r = run("--root", str(cv_root), "--compose", "herdrdev", "--from", "rust",
            "--composed-with", "rust")
    check("compose-rejects-composed-with",
          r.returncode == 2 and "--create" in r.stderr and "--update" in r.stderr,
          f"(rc={r.returncode}) {r.stderr}")

    # a legacy host (no _meta.assimilation) composes, and --check keeps its WARN path
    legacy_host = cvdocs / "legacyhost"
    legacy_host.mkdir(parents=True)
    (legacy_host / "fact-graph.json").write_text(json.dumps({
        "v": {"pkg": "legacyhost", "kb_version": "1.0.0"},
        "w": {}, "r": {}, "a": {}, "x": {}, "t": {},
    }))
    r = run("--root", str(cv_root), "--compose", "legacyhost", "--from", "rust")
    check("compose-legacy-host-ok", r.returncode == 0, r.stdout + r.stderr)
    lg_cv = cv_graph("legacyhost")
    check("compose-legacy-copied",
          "unwrap_in_library_code" in (
              (lg_cv.get("composed") or {}).get("rust", {}).get("facts", {}).get("r") or {}),
          repr(lg_cv.get("composed")))
    check("compose-legacy-no-invented-stamp",
          not (lg_cv.get("_meta") or {}).get("assimilation"),
          repr(lg_cv.get("_meta")))
    c = run("--root", str(cv_root), "--check", "legacyhost")
    check("compose-legacy-check-warns-not-fails",
          c.returncode == 0 and "legacy" in (c.stdout + c.stderr).lower(),
          f"(rc={c.returncode}) {c.stdout}{c.stderr}")

    # AC-3: a declaration whose original left the project fails closed, writing nothing
    absent_root = tmp / "absent-proj"
    (absent_root / ".git").mkdir(parents=True)
    adocs = absent_root / ".rosetta" / "docs"
    r = run("--root", str(absent_root), "--create", "rust", "--nrra", str(nrra),
            "--ledger", str(ledger))
    check("absent-rust-create", r.returncode == 0, r.stderr)
    r = run("--root", str(absent_root), "--create", "orphan", "--nrra", str(nrra),
            "--ledger", str(ledger), "--composed-with", "rust")
    check("absent-orphan-create", r.returncode == 0, r.stderr)
    orphan_path = adocs / "orphan" / "fact-graph.json"
    orphan_before = orphan_path.read_text()
    (adocs / "rust" / "fact-graph.json").unlink()

    orphan_cs = tmp / "orphan-cs.json"
    orphan_cs.write_text(json.dumps({
        "added": {"r": {"orphan-rule": {"verdict": "r", "rule": "a rule"}}},
        "changed": {}, "deprecated": {},
    }))
    r = run("--root", str(absent_root), "--update", "orphan", "--changeset", str(orphan_cs),
            "--ledger", str(ledger), "--nrra", str(nrra))
    check("absent-original-rejected", r.returncode != 0, f"(rc={r.returncode})")
    check("absent-original-names-check", "composed-with" in r.stderr, r.stderr)
    check("absent-original-names-the-graph", "rust" in r.stderr, r.stderr)
    check("absent-original-wrote-nothing", orphan_path.read_text() == orphan_before)
    cc = run("--root", str(absent_root), "--check-composed", "orphan")
    check("absent-original-check-composed-exit10", cc.returncode == 10, f"(rc={cc.returncode})")
    check("absent-original-check-names-missing", "missing-original" in (cc.stdout + cc.stderr),
          cc.stdout + cc.stderr)

    # the selection rule itself: default all, a subset, skip, and an unknown name dies
    import importlib.util

    spec = importlib.util.spec_from_file_location("wg_selection_under_test", WG)
    W = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(W)
    names = ["herdrdev", "mobile"]
    check("selection-default-is-all", W.parse_borrower_selection("", names) == names, "default")
    check("selection-all", W.parse_borrower_selection("all", names) == names, "all")
    check("selection-named-subset",
          W.parse_borrower_selection("mobile", names) == ["mobile"], "subset")
    check("selection-skip", W.parse_borrower_selection("skip", names) == [], "skip")
    unknown_died = False
    try:
        W.parse_borrower_selection("nope", names)
    except SystemExit:
        unknown_died = True
    check("selection-unknown-name-dies", unknown_died, True)

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
    print("ok — write-graph.py create/apply-nrra/update/check + open/mismatch/forged rejected + "
          "composition copied by value, listed (--propagate --list), confirm-gated, "
          "fingerprint-guarded, fresh copies skipped, --check-composed exits 10 when stale + "
          "--compose into an existing graph with no ledger, diverged copy refused (10), "
          "--force the deliberate overwrite")
    return 0


if __name__ == "__main__":
    sys.exit(main())
