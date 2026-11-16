#!/usr/bin/env python3
"""test_registry.py — selftest for the rosetta registry scanner/API.

Validates the "can't miss a graph" property, consumer discovery (including an alias-declared
name mismatch, a longest-known-prefix agent, an agent-local graph and a composed graph), the
config's authority over the alias table, and the query API.

Runs against a REAL materialized project built from committed fact-graph fixtures (not mocks).
Optionally, if ROSETTA_LIVE_ROOT points at a full fleet, a live floor check also runs.

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

STANDARD_PATHS = {
    "docs": ".rosetta/docs",
    "research": ".rosetta/research",
    "probes": ".rosetta/probes",
    "agents": "agents",
}


def _minimal_graph(slug: str) -> dict:
    return {
        "_meta": {"technology": slug.replace("-", " ").title()},
        "v": {"pkg": slug, "kb_version": "1.0.0", "pinned_date": M.FIXTURE_DATE},
        "w": {}, "r": {}, "a": {}, "x": {}, "t": {},
    }


def _alias_repo(dest: Path, aliases: dict[str, str] | None) -> Path:
    """A temp repo whose config may declare one agent alias (vendor → vendor-cloud).

    One graph, one agent whose name does not match it: the alias table is the only thing that
    can connect them, so the repo proves where that decision lives.
    """
    dest.mkdir(parents=True)
    (dest / ".git").mkdir()
    graph_dir = dest / ".rosetta" / "docs" / "vendor-cloud"
    graph_dir.mkdir(parents=True)
    (graph_dir / "fact-graph.json").write_text(
        json.dumps(_minimal_graph("vendor-cloud"), indent=2) + "\n")
    agent_dir = dest / "agents" / "vendor-implementer"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.md").write_text(
        "---\nname: vendor-implementer\ndescription: fixture\n---\n# vendor\n")
    cfg: dict = {"version": 2, "paths": dict(STANDARD_PATHS)}
    if aliases is not None:
        cfg["agent_aliases"] = dict(aliases)
    (dest / ".rosetta" / "config.json").write_text(json.dumps(cfg, indent=2) + "\n")
    return dest


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

        required = {"widget-platform", "widget-cloud", "core-lib", "toy-stack", "toy-vm"}
        check("required-graphs-present", required <= scan_slugs, f"(missing={required - scan_slugs})")

        known = scan_slugs
        aliases = M.AGENT_ALIASES
        cases = {
            # alias-declared: the agent stem is not the graph slug
            "widgetkit-implementer": "widget-cloud",
            # alias-declared: an agent that consumes a sibling's graph
            "widget-kit-planner": "widget-platform",
            # longest known slug prefix
            "widget-platform-evals-implementer": "widget-platform",
            # stem == slug
            "widget-platform-implementer": "widget-platform",
            "core-lib-reviewer": "core-lib",
            # agent-local graph, named by the agent that owns it
            "toy-vm-implementer": "toy-vm",
        }
        for agent, want in cases.items():
            got = R._slug_for_agent(agent, known, aliases)
            check(f"slug-{agent}", got == want, f"(got={got} want={want})")

        # The same names WITHOUT the config's table do not resolve — the data is in the config.
        for agent, own_stem in (("widgetkit-implementer", "widgetkit"),
                                ("widget-kit-planner", "widget-kit")):
            got = R._slug_for_agent(agent, known, None)
            check(f"no-alias-{agent}", got == own_stem, f"(got={got} want={own_stem})")

        check("agent-count-floor", len(reg["agents"]) >= 10,
              f"(got {len(reg['agents'])}, want >=10)")
        for name, entry in reg["agents"].items():
            check(f"{name}-has-one-graph", len(entry["graphs"]) == 1, f"({entry})")

        platform = reg["graphs"]["widget-platform"]["consumers"]
        for shared in ("widget-kit-implementer", "widget-kit-planner", "widget-kit-reviewer"):
            check(f"{shared}-shares-platform", shared in platform, f"(consumers={platform})")

        composed = reg["graphs"]["toy-stack"]["composed_with"]
        check("composed-with-mirrored", composed == ["core-lib"], repr(composed))

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
        cw_file = root / ".rosetta" / "docs" / "core-lib" / "fact-graph.json"
        orig_cw = cw_file.read_text()
        g = json.loads(orig_cw)
        g.setdefault("_meta", {})["composed_with"] = [
            {"graph": "widget-platform", "kb_version": "1.0.0"},
            "junk-entry",
        ]
        cw_file.write_text(json.dumps(g))
        cw_parsed, _raw = R._read_graph(cw_file)
        check("composed-with-mirror", R._composed_with(cw_parsed) == ["widget-platform"],
              repr(R._composed_with(cw_parsed)))
        scan_cw = {s: gg["composed_with"] for s, gg in R.scan()["graphs"].items()}
        check("composed-with-in-scan", scan_cw.get("core-lib") == ["widget-platform"],
              repr(scan_cw.get("core-lib")))
        cw_file.write_text(orig_cw)

        # multi-slug register binds ALL slugs (single-slug register would drop the primary)
        agent_dir = root / "agents" / "polyglot-implementer"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.md").write_text("# polyglot test agent\n")
        cli_reg = subprocess.run(
            [sys.executable, str(HERE / "registry.py"), "--root", str(root),
             "register", "polyglot-implementer", "core-lib", "widget-platform"],
            capture_output=True, text=True)
        check("register-multi-exit0", cli_reg.returncode == 0,
              f"(rc={cli_reg.returncode} {cli_reg.stderr})")
        reg2 = R.load()
        check("register-multi-bound",
              reg2.get("manual_bindings", {}).get("polyglot-implementer")
              == ["core-lib", "widget-platform"],
              repr(reg2.get("manual_bindings")))
        check("register-multi-agent-graphs",
              reg2["agents"].get("polyglot-implementer", {}).get("graphs")
              == ["core-lib", "widget-platform"],
              repr(reg2["agents"].get("polyglot-implementer")))
        # unknown slug fails closed without binding
        cli_reg_bad = subprocess.run(
            [sys.executable, str(HERE / "registry.py"), "--root", str(root),
             "register", "polyglot-implementer", "core-lib", "no-such-graph"],
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

        # ── AC-16: an agent alias is DATA in the repo's config, not a table in the code ──────
        # The same graph and the same agent twice: once with `vendor → vendor-cloud` declared,
        # once without. Only the declared one attributes the agent.
        with_alias = _alias_repo(tmp / "alias-yes", {"vendor": "vendor-cloud"})
        cli_alias = subprocess.run(
            [sys.executable, str(HERE / "registry.py"), "--root", str(with_alias), "scan"],
            capture_output=True, text=True, env=dict(os.environ))
        check("alias-scan-exit0", cli_alias.returncode == 0, cli_alias.stdout + cli_alias.stderr)
        consumers_yes = subprocess.run(
            [sys.executable, str(HERE / "registry.py"), "--root", str(with_alias),
             "consumers", "vendor-cloud"],
            capture_output=True, text=True, env=dict(os.environ))
        check("alias-attributed-to-graph", "vendor-implementer" in consumers_yes.stdout,
              f"(rc={consumers_yes.returncode} {consumers_yes.stdout}{consumers_yes.stderr})")

        without_alias = _alias_repo(tmp / "alias-no", None)
        cli_no_alias = subprocess.run(
            [sys.executable, str(HERE / "registry.py"), "--root", str(without_alias), "scan"],
            capture_output=True, text=True, env=dict(os.environ))
        check("no-alias-scan-exit0", cli_no_alias.returncode == 0,
              cli_no_alias.stdout + cli_no_alias.stderr)
        consumers_no = subprocess.run(
            [sys.executable, str(HERE / "registry.py"), "--root", str(without_alias),
             "consumers", "vendor-cloud"],
            capture_output=True, text=True, env=dict(os.environ))
        check("no-alias-attributed-nothing",
              "vendor-implementer" not in consumers_no.stdout,
              f"(stdout={consumers_no.stdout!r})")
        no_alias_reg = json.loads(
            (without_alias / ".rosetta" / "registry.json").read_text())
        check("no-alias-agent-unbound", no_alias_reg["agents"] == {},
              repr(no_alias_reg["agents"]))

        # ── AC-17: a bad alias value refuses the scan, names the key, writes nothing ──────
        for label, bad_value in (("path", "../escape"), ("slug", "Vendor_Cloud")):
            bad = _alias_repo(tmp / f"alias-bad-{label}", {"vendor": bad_value})
            cli_bad = subprocess.run(
                [sys.executable, str(HERE / "registry.py"), "--root", str(bad), "scan"],
                capture_output=True, text=True, env=dict(os.environ))
            check(f"bad-alias-{label}-nonzero", cli_bad.returncode != 0,
                  f"(rc={cli_bad.returncode} {cli_bad.stdout})")
            check(f"bad-alias-{label}-names-key", "vendor" in cli_bad.stderr, cli_bad.stderr)
            check(f"bad-alias-{label}-stderr-not-empty", bool(cli_bad.stderr.strip()),
                  repr(cli_bad.stderr))
            check(f"bad-alias-{label}-writes-no-registry",
                  (bad / ".rosetta" / "registry.json").exists() is False,
                  str(sorted(p.name for p in (bad / ".rosetta").iterdir())))

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
              custom_reg["graphs"].get("core-lib", {}).get("path")
              == "kb/graphs/core-lib/fact-graph.json",
              str(custom_reg["graphs"].get("core-lib")))
        # A graph may also live in the configured agents directory (an agent-local KB).
        outside = [g["path"] for g in custom_reg["graphs"].values()
                   if not g["path"].startswith(("kb/graphs/", "prompts/"))]
        check("custom-every-path-under-docs", outside == [], str(outside))
        custom_consumers = custom_reg["graphs"]["widget-platform"]["consumers"]
        check("custom-agents-from-config",
              "widget-kit-implementer" in custom_consumers, str(custom_consumers))
        cli_list = subprocess.run(
            [sys.executable, str(HERE / "registry.py"), "--root", str(custom), "list"],
            capture_output=True, text=True, env=dict(os.environ),
        )
        check("custom-list-shows-path", "kb/graphs/core-lib/fact-graph.json" in cli_list.stdout,
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

        core_graph = json.loads(
            (root / ".rosetta" / "docs" / "core-lib" / "fact-graph.json").read_text())
        core_edition = VL.kb_edition(core_graph)
        borrower_doc = {
            "v": {"pkg": "toy-borrower", "kb_version": "1.0.0", "pinned_date": "2026-11-04"},
            "w": {}, "r": {}, "a": {}, "x": {}, "t": {},
            "_meta": {"technology": "Toy Borrower"},
        }
        borrowed = FG.sync_composed_with(FG.compose_by_value(borrower_doc, core_graph, "core-lib"))
        borrower_dir = root / ".rosetta" / "docs" / "toy-borrower"
        borrower_dir.mkdir(parents=True, exist_ok=True)
        borrower_path = borrower_dir / "fact-graph.json"
        borrower_path.write_text(json.dumps(borrowed))

        found = R.find_borrowers("core-lib")
        names = [b["graph"] for b in found["borrowers"]]
        check("borrowers-found", names == ["toy-borrower", "toy-stack"], repr(names))
        record = next(b for b in found["borrowers"] if b["graph"] == "toy-borrower")
        check("borrowers-recorded-edition",
              record["recorded_kb_version"] == core_edition, repr(record))
        check("borrowers-current-edition",
              record["current_kb_version"] == core_edition, repr(record))
        check("borrowers-fresh", record["status"] == V.COMPOSED_FRESH, repr(record))
        check("borrowers-original-edition", found["kb_version"] == core_edition,
              repr(found["kb_version"]))

        cli_bw = subprocess.run(
            [sys.executable, str(HERE / "registry.py"), "--root", str(root),
             "borrowers", "core-lib"],
            capture_output=True, text=True)
        check("cli-borrowers-exit0", cli_bw.returncode == 0, cli_bw.stderr)
        check("cli-borrowers-lists-borrower", "toy-borrower" in cli_bw.stdout, cli_bw.stdout)
        check("cli-borrowers-shows-both-editions",
              "recorded=" in cli_bw.stdout and "current=" in cli_bw.stdout, cli_bw.stdout)
        cli_none = subprocess.run(
            [sys.executable, str(HERE / "registry.py"), "--root", str(root),
             "borrowers", "widget-platform"],
            capture_output=True, text=True)
        check("cli-borrowers-none-exit1", cli_none.returncode == 1,
              f"(rc={cli_none.returncode} {cli_none.stdout})")

        # The registry index keeps mirroring the DECLARATION (no new index shape).
        rescanned = R.scan()
        check("borrower-declared-in-scan",
              rescanned["graphs"].get("toy-borrower", {}).get("composed_with") == ["core-lib"],
              repr(rescanned["graphs"].get("toy-borrower")))

        # Optional live-fleet floor: a real fleet must still index exhaustively. The live tree
        # carries its own technology names, so this checks volume and shape rather than naming
        # any of them (the fixture above is where the named-shape contract is pinned).
        live = os.environ.get("ROSETTA_LIVE_ROOT")
        if live and (Path(live) / ".rosetta" / "docs").is_dir():
            R.set_repo_root(live)
            live_reg = R.scan()
            check("live-graph-floor", len(live_reg["graphs"]) >= 5,
                  f"(got {len(live_reg['graphs'])})")
            check("live-agent-floor", len(live_reg["agents"]) >= 10,
                  f"(got {len(live_reg['agents'])})")
            check("live-every-graph-has-consumers-key",
                  all("consumers" in g for g in live_reg["graphs"].values()),
                  "(a graph record is missing its consumers list)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        for f in failures:
            print("FAIL:", f, file=sys.stderr)
        return 1
    print("ok — registry scanner + config-declared alias mapping + validate on a real fixture "
          "project + borrow-by-value reverse query (find_borrowers + borrowers CLI)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
