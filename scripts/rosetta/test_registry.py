#!/usr/bin/env python3
"""test_registry.py — selftest for the rosetta registry scanner/API.

Validates the "can't miss a graph" property, consumer discovery (including
mastra-evals→mastra, hetzner→hetzner-cloud, cmux-specialist→cmux,
cloudflare-rust shared), and the query API.

Runs against a REAL materialized project built from committed fact-graph
fixtures (not mocks). Optionally, if ROSETTA_LIVE_ROOT points at a full
fleet (e.g. ~/Projects/brain), the original live-fleet floor checks also run.

Run: python3 scripts/rosetta/test_registry.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PLUGIN / "scripts" / "testdata"))
import materialize as M  # noqa: E402
import registry as R  # noqa: E402


def main() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        if not ok:
            failures.append(f"{name} {detail}")

    tmp = Path(tempfile.mkdtemp(prefix="rosetta-registry-"))
    try:
        root = M.materialize(tmp / "proj")
        R.set_repo_root(root)
        os.environ["ROSETTA_PROJECT"] = str(root)

        # --- can't-miss: scanner count == raw filesystem count ---
        reg = R.scan()
        scan_slugs = set(reg["graphs"])
        ground_truth: set[str] = set()
        for _path, slug in R._iter_fact_graphs():
            ground_truth.add(slug)
        check("scan-finds-all-on-disk", scan_slugs == ground_truth,
              f"(scan={len(scan_slugs)} walk={len(ground_truth)} missing={ground_truth - scan_slugs})")

        required = {
            "cloudflare-workers", "flyio", "rust", "hetzner-cloud", "mastra",
            "ai-sdk", "spacetime-db", "cmux", "morph-cloud",
        }
        check("required-graphs-present", required <= scan_slugs, f"(missing={required - scan_slugs})")

        cases = {
            "mastra-evals-implementer": "mastra",
            "mastra-implementer": "mastra",
            "hetzner-implementer": "hetzner-cloud",
            "aisdk-implementer": "ai-sdk",
            "spacetimedb-implementer": "spacetime-db",
            "cmux-specialist": "cmux",
            "cloudflare-rust-planner": "cloudflare-workers",
            "cloudflare-workers-implementer": "cloudflare-workers",
            "flyio-reviewer": "flyio",
        }
        known = scan_slugs
        for agent, want in cases.items():
            got = R._slug_for_agent(agent, known)
            check(f"slug-{agent}", got == want, f"(got={got} want={want})")

        check("agent-count-floor", len(reg["agents"]) >= 10,
              f"(got {len(reg['agents'])}, want >=10)")
        for name, entry in reg["agents"].items():
            check(f"{name}-has-one-graph", len(entry["graphs"]) == 1, f"({entry})")

        cw = reg["graphs"]["cloudflare-workers"]["consumers"]
        for cr in ("cloudflare-rust-implementer", "cloudflare-rust-planner", "cloudflare-rust-reviewer"):
            check(f"{cr}-shares-cw", cr in cw, f"(cw={cw})")

        R.write(reg)
        res = R.validate()
        check("validate-clean", res["ok"], f"({res['errors'][:3]})")

        # validate catches a missing graph
        bogus = {"graphs": {"no-such-slug": {"path": "docs/nope/fact-graph.json", "consumers": []}},
                 "agents": {}, "manual_bindings": {}}
        R.write(bogus)
        res2 = R.validate()
        check("validate-catches-missing", not res2["ok"] and any("no-such-slug" in e for e in res2["errors"]),
              f"({res2})")
        # stored kb_version disagrees with a recompute → drift
        drifted = R.scan()
        slug = next(iter(drifted["graphs"]))
        drifted["graphs"][slug]["kb_version"] = "DRIFT-SENTINEL"
        R.write(drifted)
        res3 = R.validate()
        check("validate-kb-version-drift", not res3["ok"] and any("kb_version drift" in e for e in res3["errors"]),
              f"({res3})")
        R.write(R.scan())  # restore

        # CLI against the fixture root
        cli = subprocess.run(
            [sys.executable, str(HERE / "registry.py"), "--root", str(root), "validate"],
            capture_output=True, text=True)
        check("cli-validate-exit0", cli.returncode == 0, f"(rc={cli.returncode} {cli.stderr})")

        # _composed_with: mirror _meta.composed_with slugs (tolerant on junk/corrupt)
        cw_file = root / ".rosetta" / "docs" / "rust" / "fact-graph.json"
        orig_cw = cw_file.read_text()
        g = json.loads(orig_cw)
        g.setdefault("_meta", {})["composed_with"] = [
            {"graph": "mastra", "kb_version": "1.0.0"},
            "junk-entry",
        ]
        cw_file.write_text(json.dumps(g))
        cw_parsed, _raw = R._read_graph(cw_file)
        check("composed-with-mirror", R._composed_with(cw_parsed) == ["mastra"],
              repr(R._composed_with(cw_parsed)))
        scan_cw = {s: gg["composed_with"] for s, gg in R.scan()["graphs"].items()}
        check("composed-with-in-scan", scan_cw.get("rust") == ["mastra"],
              repr(scan_cw.get("rust")))
        cw_file.write_text(orig_cw)

        # multi-slug register binds ALL slugs (single-slug register would drop the primary)
        agent_dir = root / "agents" / "polyglot-implementer"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.md").write_text("# polyglot test agent\n")
        cli_reg = subprocess.run(
            [sys.executable, str(HERE / "registry.py"), "--root", str(root),
             "register", "polyglot-implementer", "rust", "mastra"],
            capture_output=True, text=True)
        check("register-multi-exit0", cli_reg.returncode == 0,
              f"(rc={cli_reg.returncode} {cli_reg.stderr})")
        reg2 = R.load()
        check("register-multi-bound",
              reg2.get("manual_bindings", {}).get("polyglot-implementer") == ["rust", "mastra"],
              repr(reg2.get("manual_bindings")))
        check("register-multi-agent-graphs",
              reg2["agents"].get("polyglot-implementer", {}).get("graphs") == ["rust", "mastra"],
              repr(reg2["agents"].get("polyglot-implementer")))
        # unknown slug fails closed without binding
        cli_reg_bad = subprocess.run(
            [sys.executable, str(HERE / "registry.py"), "--root", str(root),
             "register", "polyglot-implementer", "rust", "no-such-graph"],
            capture_output=True, text=True)
        check("register-unknown-dies", cli_reg_bad.returncode != 0
              and "no-such-graph" in cli_reg_bad.stderr, cli_reg_bad.stderr.strip())


        # ── the repo's config decides the layout (AC-2 / AC-8 / AC-9) ────────────────
        # A fixture's layout comes from its own config: point the device home at an empty
        # directory so no machine-wide file can take part.
        os.environ["ROSETTA_HOME"] = str(tmp / "device-home")
        (tmp / "device-home").mkdir()

        # AC-2: an unconfigured repo is refused before anything is written.
        bare = tmp / "bare-repo"
        bare.mkdir()
        subprocess.run(["git", "init", "-q", str(bare)], check=True, capture_output=True)
        for cmd in ("scan", "list"):
            cli_bare = subprocess.run(
                [sys.executable, str(HERE / "registry.py"), "--root", str(bare), cmd],
                capture_output=True, text=True, env=dict(os.environ),
            )
            check(f"bare-{cmd}-refused", cli_bare.returncode != 0, f"(rc={cli_bare.returncode})")
            check(f"bare-{cmd}-names-config",
                  str(bare / ".rosetta" / "config.json") in cli_bare.stderr, cli_bare.stderr)
        check("bare-writes-no-registry", (bare / ".rosetta" / "registry.json").exists() is False,
              str(sorted(p.name for p in bare.iterdir())))

        # AC-8: a layout named only by the config is indexed where it is, agents included.
        custom = M.materialize(tmp / "custom", docs="kb/graphs", agents="prompts")
        cli_custom = subprocess.run(
            [sys.executable, str(HERE / "registry.py"), "--root", str(custom), "scan"],
            capture_output=True, text=True, env=dict(os.environ),
        )
        check("custom-scan-exit0", cli_custom.returncode == 0,
              cli_custom.stdout + cli_custom.stderr)
        custom_reg = json.loads((custom / ".rosetta" / "registry.json").read_text())
        check("custom-graph-path",
              custom_reg["graphs"].get("rust", {}).get("path") == "kb/graphs/rust/fact-graph.json",
              str(custom_reg["graphs"].get("rust")))
        # A graph may also live in the configured agents directory (an agent-local KB).
        outside = [g["path"] for g in custom_reg["graphs"].values()
                   if not g["path"].startswith(("kb/graphs/", "prompts/"))]
        check("custom-every-path-under-docs", outside == [], str(outside))
        custom_consumers = custom_reg["graphs"]["cloudflare-workers"]["consumers"]
        check("custom-agents-from-config",
              "cloudflare-rust-implementer" in custom_consumers, str(custom_consumers))
        cli_list = subprocess.run(
            [sys.executable, str(HERE / "registry.py"), "--root", str(custom), "list"],
            capture_output=True, text=True, env=dict(os.environ),
        )
        check("custom-list-shows-path", "kb/graphs/rust/fact-graph.json" in cli_list.stdout,
              cli_list.stdout)
        check("custom-created-no-legacy-docs", (custom / ".rosetta" / "docs").exists() is False,
              str(sorted(p.name for p in (custom / ".rosetta").iterdir())))
        check("custom-created-no-agents-dir", (custom / "agents").exists() is False,
              str(sorted(p.name for p in custom.iterdir())))

        # AC-9: changing ONLY paths.docs moves every path the index records.
        before = {g["path"] for g in custom_reg["graphs"].values()}
        config_path = custom / ".rosetta" / "config.json"
        cfg = json.loads(config_path.read_text())
        cfg["paths"]["docs"] = "kb/other"
        config_path.write_text(json.dumps(cfg, indent=2) + "\n")
        moved = subprocess.run(
            [sys.executable, str(HERE / "registry.py"), "--root", str(custom), "scan"],
            capture_output=True, text=True, env=dict(os.environ),
        )
        check("docs-move-scan-exit0", moved.returncode == 0, moved.stdout + moved.stderr)
        after_reg = json.loads((custom / ".rosetta" / "registry.json").read_text())
        after = {g["path"] for g in after_reg["graphs"].values()}
        stale = [p for p in after if not p.startswith(("kb/other/", "prompts/"))]
        check("docs-move-uses-new-root", stale == [], str(stale))
        kept = sorted(p for p in after if p.startswith("kb/graphs/"))
        check("docs-move-drops-old-root", kept == [], str(kept))
        check("docs-move-changed-paths", sorted(after) != sorted(before), str(sorted(after)[:3]))
        check("docs-move-created-no-other-dir", (custom / "kb" / "other").exists() is False,
              str(sorted(p.name for p in (custom / "kb").iterdir())))


        for slug, g in R.load()["graphs"].items():
            p = R.repo_root() / g["path"]
            check(f"path-inside-target-{slug}",
                  p.resolve() == p.resolve() and str(p).startswith(str(R.repo_root())),
                  f"({p})")

        # ── borrow-by-value reverse query ───────────────────────────
        # Build the borrower with the REAL shape writer (factgraph), not a hand-rolled dict,
        # so the query is exercised against the container the writer actually produces.
        import factgraph as FG  # noqa: E402
        import version_label as VL  # noqa: E402
        import versions as V  # noqa: E402

        rust_graph = json.loads(
            (root / ".rosetta" / "docs" / "rust" / "fact-graph.json").read_text())
        rust_edition = VL.kb_edition(rust_graph)
        borrower_doc = {
            "v": {"pkg": "herdrdev", "kb_version": "1.0.0", "pinned_date": "2026-11-04"},
            "w": {}, "r": {}, "a": {}, "x": {}, "t": {},
            "_meta": {"technology": "Herdr"},
        }
        borrowed = FG.sync_composed_with(FG.compose_by_value(borrower_doc, rust_graph, "rust"))
        borrower_dir = root / ".rosetta" / "docs" / "herdrdev"
        borrower_dir.mkdir(parents=True, exist_ok=True)
        borrower_path = borrower_dir / "fact-graph.json"
        borrower_path.write_text(json.dumps(borrowed))

        found = R.find_borrowers("rust")
        names = [b["graph"] for b in found["borrowers"]]
        check("borrowers-found", names == ["herdrdev"], repr(names))
        check("borrowers-recorded-edition",
              found["borrowers"][0]["recorded_kb_version"] == rust_edition,
              repr(found["borrowers"]))
        check("borrowers-current-edition",
              found["borrowers"][0]["current_kb_version"] == rust_edition,
              repr(found["borrowers"]))
        check("borrowers-fresh", found["borrowers"][0]["status"] == V.COMPOSED_FRESH,
              repr(found["borrowers"]))
        check("borrowers-original-edition", found["kb_version"] == rust_edition,
              repr(found["kb_version"]))

        cli_bw = subprocess.run(
            [sys.executable, str(HERE / "registry.py"), "--root", str(root),
             "borrowers", "rust"],
            capture_output=True, text=True)
        check("cli-borrowers-exit0", cli_bw.returncode == 0, cli_bw.stderr)
        check("cli-borrowers-lists-borrower", "herdrdev" in cli_bw.stdout, cli_bw.stdout)
        check("cli-borrowers-shows-both-editions",
              "recorded=" in cli_bw.stdout and "current=" in cli_bw.stdout, cli_bw.stdout)
        cli_none = subprocess.run(
            [sys.executable, str(HERE / "registry.py"), "--root", str(root),
             "borrowers", "mastra"],
            capture_output=True, text=True)
        check("cli-borrowers-none-exit1", cli_none.returncode == 1,
              f"(rc={cli_none.returncode} {cli_none.stdout})")

        # The registry index keeps mirroring the DECLARATION (no new index shape).
        rescanned = R.scan()
        check("borrower-declared-in-scan",
              rescanned["graphs"].get("herdrdev", {}).get("composed_with") == ["rust"],
              repr(rescanned["graphs"].get("herdrdev")))

        # Optional live-fleet floor (the original brain-shaped check).
        live = os.environ.get("ROSETTA_LIVE_ROOT")
        if live and (Path(live) / ".rosetta" / "docs" / "cloudflare-workers" / "fact-graph.json").is_file():
            R.set_repo_root(live)
            live_reg = R.scan()
            live_required = {
                "cloudflare-workers", "flyio", "modal", "openhands", "tauri", "morph-cloud",
                "hetzner-cloud", "mastra", "rust", "kotlin", "swift", "spacetime-db",
                "electron", "ai-sdk", "cmux",
            }
            check("live-required-graphs", live_required <= set(live_reg["graphs"]),
                  f"(missing={live_required - set(live_reg['graphs'])})")
            check("live-agent-floor", len(live_reg["agents"]) >= 47,
                  f"(got {len(live_reg['agents'])})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        for f in failures:
            print("FAIL:", f, file=sys.stderr)
        return 1
    print("ok — registry scanner + mapping + validate on a real fixture project + "
          "borrow-by-value reverse query (find_borrowers + borrowers CLI)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
