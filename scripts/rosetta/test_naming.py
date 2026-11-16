#!/usr/bin/env python3
"""test_naming.py — pins agent naming, slug resolution, and graph layout detection.

The point of this file is the DIVERGENCE: ``registry`` said ``"specialist"`` and
``generate_excerpt`` said ``"implementer"`` for an agent whose role is not in its name.
Both readings are now explicit arguments, and every case below proves the extracted
module reproduces BOTH old answers rather than picking one.

It also pins WHERE the name rules come from: an alias table is an argument the project
supplies (``agent_aliases``), so the module carries no product list and the same agent name
resolves differently with and without that data.

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

#: A project's declared alias table: an agent that consumes a sibling's graph, and one whose
#: name shares no prefix with the graph it consumes. Data, exactly as a config would carry it.
ALIASES = {"widget-kit": "widget-platform", "widgetkit": "widget-cloud"}


def main() -> int:
    failures: list[str] = []

    def check(name: str, got, want) -> None:
        if got != want:
            failures.append(f"{name}\n     got:  {got!r}\n     want: {want!r}")

    # ── split_agent_name: the two old answers, both preserved ──────
    cases = [
        # (input, stem, role-when-known)
        ("widget-platform-implementer", "widget-platform", "implementer"),
        ("core-lib-planner", "core-lib", "planner"),
        ("widget-platform-evals-reviewer", "widget-platform-evals", "reviewer"),
        ("toy-vm-reviewer", "toy-vm", "reviewer"),
        ("widget-platform", "widget-platform", None),
        ("plain-agent", "plain-agent", None),
        ("widgetkit", "widgetkit", None),
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
    known = {"core-lib", "widget-cloud", "widget-platform", "toy-vm"}
    for name, want in [
        ("widget-kit-implementer", "widget-platform"),  # alias: consumes a sibling's graph
        ("widgetkit-planner", "widget-cloud"),  # alias: name shares no prefix with the graph
        ("widget-platform-evals-implementer", "widget-platform"),  # longest known prefix
        ("widget-platform-implementer", "widget-platform"),  # direct
        ("core-lib-reviewer", "core-lib"),  # direct
    ]:
        check(f"slug_for_agent[{name}]", N.slug_for_agent(name, known, aliases=ALIASES), want)
        check(f"slug_for_agent-provenance[{name}]",
              R._slug_for_agent(name, known, ALIASES), want)

    # WITHOUT the project's table the same names resolve to their own stem and match no graph.
    check("no-alias: mismatch stem returns itself",
          N.slug_for_agent("widgetkit-implementer", known), "widgetkit")
    check("no-alias: shared stem returns itself",
          N.slug_for_agent("widget-kit-implementer", known), "widget-kit")
    check("no known_slugs: stray stem returns itself", N.slug_for_agent("zod-implementer"), "zod")
    check(
        "no known_slugs agrees with provenance",
        N.slug_for_agent("widget-platform-evals-implementer"),
        R._slug_for_agent("widget-platform-evals-implementer"),
    )

    # ── legacy docs domain: the alias table, then the stem ─────────
    for stem, aliases, want in [
        ("widgetkit", ALIASES, "widget-cloud"),
        ("widget-platform-evals", {"widget-platform": "widget-platform"}, "widget-platform"),
        ("core-lib", None, "core-lib"),
        ("core-lib", ALIASES, "core-lib"),
        ("toy-vm", None, "toy-vm"),
    ]:
        check(f"legacy_docs_domain[{stem}]", N.legacy_docs_domain(stem, aliases), want)
        check(f"legacy_docs_domain-provenance[{stem}]",
              G._docs_domain_for(stem, aliases), want)

    # ── graph layout → slug ────────────────────────────────────────
    # The configured docs/agents directories are the FIRST layout; the two legacy layouts are
    # still read so an unmigrated tree stays indexable. Both sides are pinned below.
    root = Path("/tmp/does-not-need-to-exist")
    docs = root / ".rosetta" / "docs"
    agents = root / "agents"
    for rel, want in [
        (".rosetta/docs/core-lib/fact-graph.json", "core-lib"),
        ("docs/legacy-kb/fact-graph.json", "legacy-kb"),
        ("agents/toy-vm-implementer/kb/fact-graph.json", "toy-vm"),
        ("agents/toy-vm/kb/fact-graph.json", "toy-vm"),
        ("somewhere/else/fact-graph.json", None),
        (".rosetta/research/x.json", None),
    ]:
        check(
            f"slug_from_graph_path[{rel}]",
            N.slug_from_graph_path(root / rel, root, docs_dir=docs, agents_dir=agents),
            want,
        )
    # A configured layout that is neither of the legacy names still names its graphs.
    custom_docs = root / "kb" / "graphs"
    custom_agents = root / "prompts"
    for rel, want in [
        ("kb/graphs/x/fact-graph.json", "x"),
        ("prompts/x-implementer/kb/fact-graph.json", "x"),
        ("kb/graphs/nested/deep/fact-graph.json", "nested"),
        ("docs/legacy-kb/fact-graph.json", "legacy-kb"),
    ]:
        check(
            f"slug_from_graph_path-custom[{rel}]",
            N.slug_from_graph_path(root / rel, root, docs_dir=custom_docs, agents_dir=custom_agents),
            want,
        )
    check(
        "slug_from_graph_path-no-config-dirs",
        N.slug_from_graph_path(root / "kb/graphs/x/fact-graph.json", root),
        None,
    )

    # ── graph_path_for ─────────────────────────────────────────────
    check(
        "graph_path_for",
        N.graph_path_for(root, "core-lib", docs_dir=docs),
        root / ".rosetta" / "docs" / "core-lib" / "fact-graph.json",
    )
    check(
        "graph_path_for-custom",
        N.graph_path_for(root, "core-lib", docs_dir=custom_docs),
        root / "kb" / "graphs" / "core-lib" / "fact-graph.json",
    )

    # registry's delegating alias answers with the repo's resolved layout. Point it at a temp
    # repo that declares the standard layout, so the check needs no machine-wide config.
    import json
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="rosetta-naming-"))
    try:
        import os
        # A neutral device home: a fixture's layout comes from its own config, never from this
        # machine's device file.
        os.environ["ROSETTA_HOME"] = str(tmp / "device-home")
        (tmp / "device-home").mkdir()
        (tmp / ".rosetta").mkdir(parents=True)
        (tmp / ".rosetta" / "config.json").write_text(json.dumps({
            "version": 2,
            "paths": {"docs": ".rosetta/docs", "research": ".rosetta/research",
                      "probes": ".rosetta/probes", "agents": "agents"},
        }))
        R.set_repo_root(tmp)
        for rel, want in [
            (".rosetta/docs/core-lib/fact-graph.json", "core-lib"),
            ("agents/toy-vm-implementer/kb/fact-graph.json", "toy-vm"),
            ("somewhere/else/fact-graph.json", None),
        ]:
            check(f"registry_slug[{rel}]", _registry_slug(rel), want)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print(f"FAILED — {len(failures)} case(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ok — naming.py preserves both role defaults, alias-declared name resolution, and "
          "the config layouts")
    return 0


def _registry_slug(rel: str) -> str | None:
    """Call registry's version against a synthetic path under ITS OWN repo root."""
    return R._slug_for_graph(R.repo_root() / rel)


if __name__ == "__main__":
    sys.exit(main())
