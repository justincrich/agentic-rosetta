#!/usr/bin/env python3
"""test_naming.py — pins agent naming, slug resolution, and graph layout detection.

The point of this file is the DIVERGENCE: ``registry`` said ``"specialist"`` and
``generate_excerpt`` said ``"implementer"`` for an agent whose role is not in its name.
Both readings are now explicit arguments, and every case below proves the extracted
module reproduces BOTH old answers rather than picking one.

Run: python3 scripts/rosetta/test_naming.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import naming as N  # noqa: E402
import registry as R  # noqa: E402  (transition cross-check)

# generate_excerpt lives in kb-delivery and reaches rosetta/ by path surgery.
sys.path.insert(0, str(HERE.parent / "kb-delivery"))
import generate_excerpt as G  # noqa: E402  (transition cross-check)


def main() -> int:
    failures: list[str] = []

    def check(name: str, got, want) -> None:
        if got != want:
            failures.append(f"{name}\n     got:  {got!r}\n     want: {want!r}")

    # ── split_agent_name: the two old answers, both preserved ──────
    cases = [
        # (input, stem, role-when-known)
        ("cloudflare-workers-implementer", "cloudflare-workers", "implementer"),
        ("rust-planner", "rust", "planner"),
        ("mastra-evals-reviewer", "mastra-evals", "reviewer"),
        ("hetzner-reviewer", "hetzner", "reviewer"),
        ("cloudflare-workers", "cloudflare-workers", None),
        ("cmux-specialist", "cmux-specialist", None),
        ("aisdk", "aisdk", None),
    ]
    for name, want_stem, want_role in cases:
        specialist = (want_stem, want_role if want_role else "specialist")
        implementer = (want_stem, want_role if want_role else "implementer")
        check(
            f"split[{name}] as registry",
            N.split_agent_name(name, default_role="specialist"),
            specialist,
        )
        check(
            f"split[{name}] as generate_excerpt",
            N.split_agent_name(name, default_role="implementer"),
            implementer,
        )
        check(f"split-provenance-registry[{name}]", R._split_name_role(name), specialist)
        check(f"split-provenance-excerpt[{name}]", G._split_name_role(name), implementer)

    # A bad default_role must fail loudly, not silently pick one.
    try:
        N.split_agent_name("x", default_role="specializer")
        check("rejects unknown default_role", "no raise", "ValueError")
    except ValueError:
        check("rejects unknown default_role", "ok", "ok")

    # ── slug_for_agent ─────────────────────────────────────────────
    known = {"rust", "ai-sdk", "hetzner-cloud", "cloudflare-workers", "mastra", "cmux"}
    for name, want in [
        ("cloudflare-rust-implementer", "cloudflare-workers"),  # shared graph
        ("hetzner-planner", "hetzner-cloud"),  # spelling remap
        ("aisdk-reviewer", "ai-sdk"),  # spelling remap
        ("spacetimedb-implementer", "spacetime-db"),  # spelling remap
        ("mastra-evals-implementer", "mastra"),  # longest known prefix
        ("cmux-specialist", "cmux"),  # longest known prefix
        ("rust-reviewer", "rust"),  # direct
    ]:
        check(f"slug_for_agent[{name}]", N.slug_for_agent(name, known), want)
        check(f"slug_for_agent-provenance[{name}]", R._slug_for_agent(name, known), want)

    # Without known_slugs, only the shared and remap cases resolve.
    check("no known_slugs: stray stem returns itself", N.slug_for_agent("zod-implementer"), "zod")
    check(
        "no known_slugs agrees with provenance",
        N.slug_for_agent("mastra-evals-implementer"),
        R._slug_for_agent("mastra-evals-implementer"),
    )

    # ── legacy docs domain: deliberately a DIFFERENT table ─────────
    for stem, want in [
        ("hetzner", "hetzner-cloud"),
        ("mastra", "mastra"),
        ("rust", "rust"),
        ("kotlin", "kotlin"),
        ("swift", "swift"),
        ("spacetimedb", "spacetime-db"),
        ("electron", "electron"),
        ("aisdk", "ai-sdk"),
        ("cmux", "cmux"),
        ("mastra-evals", "mastra"),
        ("zod", None),
        ("", None),
    ]:
        check(f"legacy_docs_domain[{stem}]", N.legacy_docs_domain(stem), want)
        check(f"legacy_docs_domain-provenance[{stem}]", G._docs_domain_for(stem), want)

    # The two tables are NOT interchangeable; the legacy one is a strict superset.
    check(
        "legacy table is a superset of the modern table",
        set(N.AGENT_PREFIX_TO_SLUG) <= set(N.LEGACY_DOCS_DOMAIN_BY_PREFIX),
        True,
    )
    # The one map that WAS genuinely shared now has one identity, not two agreeing copies.
    check(
        "shared-graph map has one home",
        N.SHARED_GRAPHS,
        {"cloudflare-rust": "cloudflare-workers"},
    )

    # ── graph layout → slug ────────────────────────────────────────
    root = Path("/tmp/does-not-need-to-exist")
    for rel, want in [
        (".rosetta/docs/rust/fact-graph.json", "rust"),
        ("docs/kotlin/fact-graph.json", "kotlin"),
        ("agents/morph-cloud-implementer/kb/fact-graph.json", "morph-cloud"),
        ("agents/morph-cloud/kb/fact-graph.json", "morph-cloud"),
        ("somewhere/else/fact-graph.json", None),
        (".rosetta/research/x.json", None),
    ]:
        check(f"slug_from_graph_path[{rel}]", N.slug_from_graph_path(root / rel, root), want)
        check(
            f"slug_from_graph_path-provenance[{rel}]",
            _registry_slug(rel),
            want,
        )

    # ── graph_path_for ─────────────────────────────────────────────
    check(
        "graph_path_for",
        N.graph_path_for(root, "rust"),
        root / ".rosetta" / "docs" / "rust" / "fact-graph.json",
    )

    if failures:
        print(f"FAILED — {len(failures)} case(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ok — naming.py preserves both role defaults, 7 slug rules, 9 legacy domains, 6 layouts")
    return 0


def _registry_slug(rel: str) -> str | None:
    """Call registry's version against a synthetic path under ITS OWN repo root."""
    return R._slug_for_graph(R.REPO_ROOT / rel)


if __name__ == "__main__":
    sys.exit(main())
