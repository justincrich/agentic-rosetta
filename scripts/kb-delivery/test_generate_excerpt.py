#!/usr/bin/env python3
"""test_generate_excerpt.py — generator tests against a REAL cloudflare-workers fact-graph.

The graph is the committed fixture (copied from a live KB), materialized into a
temp project. Run: python3 scripts/kb-delivery/test_generate_excerpt.py
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


def main() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        if not ok:
            failures.append(f"{name} {detail}")

    tmp = Path(tempfile.mkdtemp(prefix="rosetta-excerpt-"))
    try:
        root = M.materialize(tmp / "proj")
        os.environ["ROSETTA_PROJECT"] = str(root)
        cw_graph = root / ".rosetta" / "docs" / "cloudflare-workers" / "fact-graph.json"

        def run(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, str(GEN), "--root", str(root), *args],
                capture_output=True, text=True)

        ver = run("--domain", "cloudflare-workers", "--print-version").stdout.strip()
        check("version-format", bool(re.search(r"sha:[0-9a-f]{8}$", ver)), f"({ver})")
        check("version-has-wrangler", "wrangler" in ver, f"({ver})")

        cond = run("--domain", "cloudflare-workers", "--kb-source", "condensed").stdout
        check("condensed-title", "FACT GRAPH (condensed bootstrap)" in cond)
        check("condensed-runtime", "Runtime" in cond and "V8 isolates" in cond)
        check("condensed-entry", "export default" in cond)
        check("condensed-stamps-version", "kb_version:" in cond and "kb_source: condensed" in cond)

        for role, marker in [("planner", "Components / bindings"),
                             ("implementer", "API signatures"),
                             ("reviewer", "Anti-pattern checklist")]:
            full = run("--domain", "cloudflare-workers", "--kb-source", "full", "--role", role).stdout
            check(f"full-{role}-reject", "REJECT" in full, "(missing REJECT)")
            check(f"full-{role}-marker", marker in full, f"(missing {marker})")
            check(f"full-{role}-no-global-mutable",
                  "global mutable state" in full.lower() or "no_global_mutable_state" in full)
            check(f"full-{role}-stamps-version", "kb_source: full" in full)

        missing = run("--domain", "no-such-domain", "--print-version")
        check("missing-domain-exit", missing.returncode == 2, f"(rc={missing.returncode})")
        check("missing-domain-msg", "fact-graph not found" in missing.stderr)

        emb = run("--agent", "cloudflare-workers-implementer", "--embed").stdout
        emb_lines = emb.splitlines()
        check("embed-begin", "rosetta-kb:begin" in emb)
        check("embed-end", bool(emb_lines) and emb_lines[-1] == "<!-- rosetta-kb:end -->")
        check("embed-domain", 'domain="cloudflare-workers"' in emb)
        check("embed-role", 'role="implementer"' in emb)
        check("embed-fmt-json", bool(emb_lines) and 'fmt="json"' in emb_lines[0])
        check("embed-version-in-marker", bool(emb_lines) and 'kb_version="' in emb_lines[0])
        check("embed-upstream-attr", bool(emb_lines) and "upstream=" in emb_lines[0])
        check("embed-corpus-attr", bool(emb_lines) and "corpus=" in emb_lines[0])
        check("embed-single-begin", emb.count("rosetta-kb:begin") == 1)
        check("embed-single-end", emb.count("rosetta-kb:end") == 1)
        check("embed-three-lines", len(emb_lines) == 3, f"(got {len(emb_lines)} lines)")
        src_graph = json.loads(cw_graph.read_bytes())
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
              emb == run("--agent", "cloudflare-workers-implementer", "--embed").stdout)
        plan_lines = run("--agent", "cloudflare-workers-planner", "--embed").stdout.splitlines()
        check("embed-role-independent-body", len(plan_lines) == 3 and plan_lines[1] == body)

        md = run("--agent", "cloudflare-workers-implementer", "--embed",
                 "--embed-format", "markdown").stdout
        md_lines = md.splitlines()
        check("embed-md-marker", "## API signatures" in md)
        check("embed-md-no-fmt-json", bool(md_lines) and 'fmt="json"' not in md_lines[0])
        check("embed-md-begin", "rosetta-kb:begin" in md)

        mc = run("--agent", "morph-cloud-implementer", "--embed").stdout
        check("agent-morph-domain", 'domain="morph-cloud"' in mc)
        check("agent-morph-version", "kb_version=" in mc.splitlines()[0] if mc else False)
        cr = run("--agent", "cloudflare-rust-implementer", "--print-version").stdout.strip()
        cw = run("--domain", "cloudflare-workers", "--print-version").stdout.strip()
        check("agent-shared-graph", cr == cw, f"({cr} != {cw})")

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
