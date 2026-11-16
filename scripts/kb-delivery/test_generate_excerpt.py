#!/usr/bin/env python3
"""test_generate_excerpt.py — generator tests against the committed neutral fact-graphs.

The graphs are the fixtures in ``scripts/testdata/graphs`` (written by the real writer from
committed NRRAs, see ``test_write_graph.py``), materialized into a temp project. Run:
python3 scripts/kb-delivery/test_generate_excerpt.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[1]
GEN = HERE / "generate_excerpt.py"
sys.path.insert(0, str(PLUGIN / "scripts" / "testdata"))
import materialize as M  # noqa: E402

#: The two literals AC-18 forbids in the generator. Assembled from fragments so this file is
#: not itself a carrier of the strings it searches for.
FORBIDDEN_LITERALS = ("V8 " + "isolates", "wran" + "gler")


def main() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        if not ok:
            failures.append(f"{name} {detail}")

    tmp = Path(tempfile.mkdtemp(prefix="rosetta-excerpt-"))
    try:
        root = M.materialize(tmp / "proj")
        os.environ["ROSETTA_PROJECT"] = str(root)
        platform_graph = root / ".rosetta" / "docs" / "widget-platform" / "fact-graph.json"

        def run(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, str(GEN), "--root", str(root), *args],
                capture_output=True, text=True)

        # ── AC-18: the generator carries no product advice and no default domain ─────────
        source = GEN.read_text(encoding="utf-8")
        for literal in FORBIDDEN_LITERALS:
            check(f"no-literal[{literal}]", literal not in source,
                  "(generate_excerpt.py still hardcodes it)")
        no_domain = run()
        check("no-domain-exit", no_domain.returncode != 0, f"(rc={no_domain.returncode})")
        check("no-domain-names-flag", "--domain" in no_domain.stderr, no_domain.stderr)
        check("no-domain-renders-nothing", no_domain.stdout == "", no_domain.stdout)

        ver = run("--domain", "widget-platform", "--print-version").stdout.strip()
        check("version-format", bool(re.search(r"sha:[0-9a-f]{8}$", ver)), f"({ver})")
        check("version-carries-kb-edition", ver.startswith("1.0.0 @"), f"({ver})")

        cond = run("--domain", "widget-platform", "--kb-source", "condensed").stdout
        check("condensed-title", "FACT GRAPH (condensed bootstrap)" in cond)
        check("condensed-runtime-from-graph",
              "**Runtime**: portable worker runtime" in cond, cond)
        check("condensed-entry-from-graph",
              "**Entry Points**: export default { fetch }" in cond, cond)
        check("condensed-package-from-graph",
              "**Package**: widgetctl 4.2.0" in cond, cond)
        check("condensed-rules-from-graph",
              "no_blocking_io" in cond and "no_global_mutable_state" in cond, cond)
        check("condensed-limits-from-graph", "10 ms of CPU per request by default" in cond, cond)
        check("condensed-api-from-graph", "fetch_handler" in cond, cond)
        check("condensed-stamps-version", "kb_version:" in cond and "kb_source: condensed" in cond)

        # The condensed render is derived PER GRAPH: a second graph's bootstrap carries its own
        # values and none of the first's, which is what "derived from graph fields" must mean.
        vm_cond = run("--agent", "toy-vm-implementer", "--kb-source", "condensed").stdout
        check("condensed-second-graph-own-package", "**Package**: toyvm 1.4.0" in vm_cond, vm_cond)
        check("condensed-second-graph-own-entry", "**Entry Points**: toyvm" in vm_cond, vm_cond)
        check("condensed-second-graph-no-borrowed-runtime",
              "portable worker runtime" not in vm_cond, vm_cond)
        check("condensed-second-graph-no-borrowed-rules",
              "no_blocking_io" not in vm_cond, vm_cond)

        for role, marker in [("planner", "Components / bindings"),
                             ("implementer", "API signatures"),
                             ("reviewer", "Anti-pattern checklist")]:
            full = run("--domain", "widget-platform", "--kb-source", "full", "--role", role).stdout
            check(f"full-{role}-reject", "REJECT" in full, "(missing REJECT)")
            check(f"full-{role}-marker", marker in full, f"(missing {marker})")
            check(f"full-{role}-no-global-mutable",
                  "global mutable state" in full.lower() or "no_global_mutable_state" in full)
            check(f"full-{role}-stamps-version", "kb_source: full" in full)

        missing = run("--domain", "no-such-domain", "--print-version")
        check("missing-domain-exit", missing.returncode == 2, f"(rc={missing.returncode})")
        check("missing-domain-msg", "fact-graph not found" in missing.stderr)

        emb = run("--agent", "widget-platform-implementer", "--embed").stdout
        emb_lines = emb.splitlines()
        check("embed-begin", "rosetta-kb:begin" in emb)
        check("embed-end", bool(emb_lines) and emb_lines[-1] == "<!-- rosetta-kb:end -->")
        check("embed-domain", 'domain="widget-platform"' in emb)
        check("embed-role", 'role="implementer"' in emb)
        check("embed-fmt-json", bool(emb_lines) and 'fmt="json"' in emb_lines[0])
        check("embed-version-in-marker", bool(emb_lines) and 'kb_version="' in emb_lines[0])
        check("embed-upstream-attr", bool(emb_lines) and "upstream=" in emb_lines[0])
        check("embed-corpus-attr", bool(emb_lines) and "corpus=" in emb_lines[0])
        check("embed-single-begin", emb.count("rosetta-kb:begin") == 1)
        check("embed-single-end", emb.count("rosetta-kb:end") == 1)
        check("embed-three-lines", len(emb_lines) == 3, f"(got {len(emb_lines)} lines)")
        src_graph = json.loads(platform_graph.read_bytes())
        src_minus_meta = {k: v for k, v in src_graph.items() if k != "_meta"}
        body = emb_lines[1] if len(emb_lines) == 3 else ""
        parsed = None
        try:
            parsed = json.loads(body)
        except Exception as e:  # noqa: BLE001
            check("embed-body-valid-json", False, f"({e})")
        check("embed-body-no-meta", parsed is not None and "_meta" not in parsed)
        check("embed-body-minus-meta-roundtrip", parsed == src_minus_meta)
        check("embed-body-minified",
              parsed is not None and body == json.dumps(parsed, separators=(",", ":"), ensure_ascii=False))
        check("embed-deterministic",
              emb == run("--agent", "widget-platform-implementer", "--embed").stdout)
        plan_lines = run("--agent", "widget-platform-planner", "--embed").stdout.splitlines()
        check("embed-role-independent-body", len(plan_lines) == 3 and plan_lines[1] == body)

        md = run("--agent", "widget-platform-implementer", "--embed",
                 "--embed-format", "markdown").stdout
        md_lines = md.splitlines()
        check("embed-md-marker", "## API signatures" in md)
        check("embed-md-no-fmt-json", bool(md_lines) and 'fmt="json"' not in md_lines[0])
        check("embed-md-begin", "rosetta-kb:begin" in md)

        # an agent-local graph wins over the shared one
        mc = run("--agent", "toy-vm-implementer", "--embed").stdout
        check("agent-local-domain", 'domain="toy-vm"' in mc)
        check("agent-local-version", "kb_version=" in mc.splitlines()[0] if mc else False)
        # an agent whose config declares the alias consumes the sibling's graph, same bytes
        shared = run("--agent", "widget-kit-implementer", "--print-version").stdout.strip()
        own = run("--domain", "widget-platform", "--print-version").stdout.strip()
        check("agent-shared-graph", shared == own, f"({shared} != {own})")

        # ── AC-2: a composed KB's embed carries the original's facts BY VALUE ──
        # The borrower is produced by the REAL writer (write-graph.py --create
        # --composed-with core-lib), not by pasting a container here, so what is rendered is
        # what the writer actually writes. A green `--check` would prove nothing: it hashes
        # the body only.
        WG = PLUGIN / "scripts" / "rosetta" / "write-graph.py"
        nrra = M.NRRA / "toy-stack.md"
        created = subprocess.run(
            [sys.executable, str(WG), "--root", str(root), "--create", "demo-borrower",
             "--nrra", str(nrra), "--ledger", str(M.LEDGER), "--as-of", M.FIXTURE_DATE,
             "--composed-with", "core-lib"],
            capture_output=True, text=True)
        check("compose-create-exit0", created.returncode == 0, created.stdout + created.stderr)

        core_graph = json.loads(
            (root / ".rosetta" / "docs" / "core-lib" / "fact-graph.json").read_text())
        sys.path.insert(0, str(PLUGIN / "scripts" / "rosetta"))
        import version_label as VL  # noqa: E402

        core_edition = VL.kb_edition(core_graph)
        core_rules = [k for k in (core_graph.get("r") or {}) if not k.endswith("_note")]
        check("compose-fixture-has-core-rules", bool(core_rules), repr(list(core_graph.get("r") or {})))

        composed_embed = run("--agent", "demo-borrower-implementer", "--embed").stdout
        composed_lines = composed_embed.splitlines()
        check("compose-embed-three-lines", len(composed_lines) == 3, f"(got {len(composed_lines)})")
        composed_body = None
        try:
            composed_body = json.loads(composed_lines[1]) if len(composed_lines) == 3 else None
        except json.JSONDecodeError as e:  # noqa: BLE001
            check("compose-embed-json", False, f"({e})")
        borrowed = (composed_body or {}).get("composed", {}).get("core-lib", {})
        check("compose-embed-carries-container", bool(borrowed), repr(composed_body)[:200])
        check("compose-embed-carries-edition",
              borrowed.get("kb_version") == core_edition, repr(borrowed.get("kb_version")))
        check("compose-embed-carries-a-rule-key",
              any(key in (borrowed.get("facts", {}).get("r") or {}) for key in core_rules),
              repr(list((borrowed.get("facts", {}).get("r") or {})))[:200])
        check("compose-embed-text-visible",
              bool(core_rules) and core_rules[0] in composed_embed,
              f"({core_rules[:1]} not in embed)")
        check("compose-embed-no-meta", "_meta" not in (composed_body or {}))

        composed_md = run("--agent", "demo-borrower-implementer", "--embed",
                          "--embed-format", "markdown").stdout
        check("compose-md-banner", "[composed: core-lib]" in composed_md, composed_md[:400])

        # ── fail closed: a declaration with no copy, and a copy with no original ──
        declared_only = tmp / "declared-only.json"
        declared_only.write_text(json.dumps({
            "v": {"kb_version": "1.0.0", "pkg": "demo-borrower"},
            "w": {}, "r": {}, "a": {}, "x": {}, "t": {},
            "_meta": {"composed_with": [{"graph": "core-lib", "kb_version": "1.0.0"}]},
        }))
        no_copy = run("--graph", str(declared_only), "--embed")
        check("compose-missing-copy-exit", no_copy.returncode == 2, f"(rc={no_copy.returncode})")
        check("compose-missing-copy-named", "composed-copy-missing" in no_copy.stderr, no_copy.stderr)
        check("compose-missing-copy-no-render", "rosetta-kb:begin" not in no_copy.stdout, no_copy.stdout)

        ghost_copy = tmp / "ghost-copy.json"
        ghost_copy.write_text(json.dumps({
            "v": {"kb_version": "1.0.0", "pkg": "demo-borrower"},
            "w": {}, "r": {}, "a": {}, "x": {}, "t": {},
            "composed": {"ghost": {"kb_version": "1.0.0", "fingerprint": "deadbeef",
                                   "facts": {"v": {}, "w": {}, "r": {}, "a": {}, "x": {}, "t": {}}}},
            "_meta": {"composed_with": [{"graph": "ghost", "kb_version": "1.0.0"}]},
        }))
        no_original = run("--graph", str(ghost_copy), "--embed")
        check("compose-missing-original-exit", no_original.returncode == 2,
              f"(rc={no_original.returncode})")
        check("compose-missing-original-named",
              "composed-original-missing" in no_original.stderr, no_original.stderr)
        check("compose-missing-original-names-source",
              "ghost" in no_original.stderr, no_original.stderr)
        check("compose-missing-original-names-path",
              str(root / ".rosetta" / "docs" / "ghost" / "fact-graph.json") in no_original.stderr,
              no_original.stderr)

        # --graph without --domain is labelled from the graph's own identity (no default domain)
        hinted = run("--graph", str(platform_graph), "--embed").stdout.splitlines()
        check("graph-without-domain-labels-itself",
              bool(hinted) and 'domain="widget-platform"' in hinted[0], str(hinted[:1]))

        # v.pinned (no pinned_date) still fills the @ date half
        pinned_only = tmp / "pinned-only.json"
        pinned_only.write_text(json.dumps({
            "v": {"kb_version": "1.0.0", "pinned": "2026-03", "pkg": "toy"},
            "w": {}, "r": {}, "a": {}, "x": {}, "t": {},
        }))
        sys.path.insert(0, str(PLUGIN / "scripts" / "kb-delivery"))
        import generate_excerpt as GE  # noqa: E402
        raw_p = pinned_only.read_bytes()
        label = GE.kb_version(json.loads(raw_p), raw_p)
        check("pinned-fallback-date", " @ 2026-03 " in label, f"({label})")

        # ── the config decides the layout (AC-2 / AC-8 / AC-9) ──────────────────────────
        os.environ["ROSETTA_HOME"] = str(tmp / "device-home")
        (tmp / "device-home").mkdir()

        # AC-2: an unconfigured repo is refused, naming the file, and renders nothing.
        bare = tmp / "bare-gen"
        (bare / ".git").mkdir(parents=True)
        bare_run = subprocess.run(
            [sys.executable, str(GEN), "--root", str(bare), "--domain", "widget-platform",
             "--embed"],
            capture_output=True, text=True, env=dict(os.environ),
        )
        check("bare-refused", bare_run.returncode != 0, f"(rc={bare_run.returncode})")
        check("bare-names-config", str(bare / ".rosetta" / "config.json") in bare_run.stderr,
              bare_run.stderr)
        check("bare-renders-nothing", bare_run.stdout == "", bare_run.stdout)
        check("bare-creates-nothing", (bare / ".rosetta").exists() is False,
              str(sorted(p.name for p in bare.iterdir())))

        # AC-8: a graph under the configured docs directory renders with the same bytes as the
        # standard layout — the layout name is the only difference.
        custom = M.materialize(tmp / "custom", docs="kb/graphs", agents="prompts")
        standard_embed = run("--agent", "widget-platform-implementer", "--embed").stdout
        custom_run = subprocess.run(
            [sys.executable, str(GEN), "--root", str(custom),
             "--agent", "widget-platform-implementer", "--embed"],
            capture_output=True, text=True, env=dict(os.environ),
        )
        check("custom-embed-exit0", custom_run.returncode == 0, custom_run.stderr)
        check("custom-embed-same-render", custom_run.stdout == standard_embed,
              custom_run.stderr)
        check("custom-uses-configured-docs",
              (custom / "kb" / "graphs" / "widget-platform" / "fact-graph.json").is_file(),
              str(sorted(p.as_posix() for p in (custom / "kb").rglob("*"))[:4]))
        check("custom-makes-no-legacy-docs", (custom / ".rosetta" / "docs").exists() is False,
              str(sorted(p.name for p in (custom / ".rosetta").iterdir())))

        # AC-9: changing only paths.docs moves where the generator reads.
        cfg_path = custom / ".rosetta" / "config.json"
        cfg = json.loads(cfg_path.read_text())
        cfg["paths"]["docs"] = "kb/other"
        cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
        moved_before = subprocess.run(
            [sys.executable, str(GEN), "--root", str(custom),
             "--agent", "widget-platform-implementer", "--embed"],
            capture_output=True, text=True, env=dict(os.environ),
        )
        check("docs-move-reads-nothing-at-old-root", moved_before.returncode == 2,
              f"(rc={moved_before.returncode})")
        check("docs-move-names-new-path",
              "kb/other" in moved_before.stderr or "no fact-graph" in moved_before.stderr,
              moved_before.stderr)
        (custom / "kb" / "other").mkdir(parents=True)
        shutil.copytree(custom / "kb" / "graphs" / "widget-platform",
                        custom / "kb" / "other" / "widget-platform")
        moved_after = subprocess.run(
            [sys.executable, str(GEN), "--root", str(custom),
             "--agent", "widget-platform-implementer", "--embed"],
            capture_output=True, text=True, env=dict(os.environ),
        )
        check("docs-move-reads-new-root", moved_after.returncode == 0, moved_after.stderr)
        check("docs-move-same-render", moved_after.stdout == standard_embed, moved_after.stderr)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        for f in failures:
            print("FAIL:", f, file=sys.stderr)
        return 1
    print(f"ok — generate_excerpt.py both cohorts + per-role emphasis + version stamp ({ver})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
