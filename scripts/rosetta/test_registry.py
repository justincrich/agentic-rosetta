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
        R.write(R.scan())  # restore

        # CLI against the fixture root
        cli = subprocess.run(
            [sys.executable, str(HERE / "registry.py"), "--root", str(root), "validate"],
            capture_output=True, text=True)
        check("cli-validate-exit0", cli.returncode == 0, f"(rc={cli.returncode} {cli.stderr})")

        # No path in the written registry escapes the target project.
        for slug, g in R.load()["graphs"].items():
            p = R.REPO_ROOT / g["path"]
            check(f"path-inside-target-{slug}",
                  p.resolve() == p.resolve() and str(p).startswith(str(R.REPO_ROOT)),
                  f"({p})")

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
    print("ok — registry scanner + mapping + validate on a real fixture project")
    return 0


if __name__ == "__main__":
    sys.exit(main())
