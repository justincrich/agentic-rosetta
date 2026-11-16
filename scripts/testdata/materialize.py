#!/usr/bin/env python3
"""Materialize a fixture project for the registry / KB-delivery tests.

Copies the committed fact-graph fixtures into a target project's configured docs directory
and an agent-local ``kb/`` layout, declares the project's own version 2 config, and writes the
throwaway ``agent.md`` files the registry scans. Everything here is data a test project needs;
nothing is a mock of a tool under test.

WHY THE GRAPHS ARE NEUTRAL
  ``scripts/testdata/graphs`` used to hold copies of real product knowledge bases, so product
  data lived inside the plugin and every name-shaped expectation in the tests named a real
  technology. The fixtures are now written by the REAL writer (``write-graph.py --create``)
  from the committed neutral NRRAs in ``../nrra/`` and the shared closed ledger in
  ``../ledger/``; ``scripts/rosetta/test_write_graph.py`` regenerates them and compares the
  bytes, so a fixture cannot be hand-edited without that test failing.

  The shapes are the ones the readers must handle: a shared graph consumed by a differently
  spelled agent (``widget-kit-*``), a name ≠ slug alias (``widgetkit-*`` → ``widget-cloud``),
  a longest-known-prefix agent (``widget-platform-evals-*``), an agent-local graph
  (``toy-vm-implementer/kb/fact-graph.json``), and a composed graph (``toy-stack``).
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GRAPHS = HERE / "graphs"
NRRA = HERE / "nrra"
LEDGER = HERE / "ledger" / "closed.json"

#: The date every fixture stamp carries, passed to ``write-graph.py --as-of``. One constant so
#: the committed bytes and the regeneration in ``test_write_graph.py`` cannot drift apart.
FIXTURE_DATE = "2026-11-16"

#: ``(graph slug, NRRA stem, composed-with slug or None)`` — the recipe that reproduces every
#: file in ``graphs/``. Order matters: a borrower is created after the original it composes.
FIXTURE_RECIPE: tuple[tuple[str, str, str | None], ...] = (
    ("widget-platform", "widget-platform", None),
    ("core-lib", "core-lib", None),
    ("toy-vm", "toy-vm", None),
    ("toy-stack", "toy-stack", "core-lib"),
)

#: Graphs copied into the configured docs directory.
DOCS_SLUGS = ("widget-platform", "core-lib", "toy-stack")

#: Extra empty-but-valid graphs, so an alias target has a known slug on disk.
MINI_SLUGS = ("widget-cloud",)

#: The agent-local graph: it lives with the agent that owns it, not in the docs directory.
AGENT_LOCAL = ("toy-vm", "toy-vm-implementer")

#: Agents that must exist for mapping + ``--agent`` resolution. Their names carry the shapes
#: above; the alias table below is what connects the spelled-differently ones to a graph.
AGENTS = (
    "widget-platform-implementer",
    "widget-platform-planner",
    "widget-platform-reviewer",
    "widget-kit-implementer",
    "widget-kit-planner",
    "widget-kit-reviewer",
    "widgetkit-implementer",
    "widget-platform-evals-implementer",
    "core-lib-implementer",
    "toy-stack-implementer",
    "toy-vm-implementer",
    "plain-agent",
)

#: The agent-name aliases this fixture project declares in its own config. ``widget-kit``
#: consumes the widget platform's graph; ``widgetkit`` is the same idea with a name that
#: shares no prefix with its graph. Both are DATA: a project without these keys resolves
#: neither, which is what ``test_registry.py`` proves.
AGENT_ALIASES = {
    "widget-kit": "widget-platform",
    "widgetkit": "widget-cloud",
}


def _mini_graph(slug: str) -> dict:
    return {
        "_meta": {"technology": slug.replace("-", " ").title()},
        "v": {"pkg": slug, "version": "0.0.0", "kb_version": "1.0.0",
              "pinned_date": FIXTURE_DATE},
        "w": {},
        "r": {},
        "a": {},
        "x": {},
        "t": {},
    }


def materialize(
    dest: Path,
    docs: str = ".rosetta/docs",
    agents: str = "agents",
    citation_aliases: dict[str, str] | None = None,
    agent_aliases: dict[str, str] | None = None,
) -> Path:
    """Build a fixture project. ``docs``/``agents`` are the layout its config declares.

    The default is the standard layout; a caller that wants the config's authority proven
    passes a custom one (``kb/graphs``, ``prompts``) and gets the same graphs and agents under
    it, so every reader that honours the config finds them and every reader that hardcodes a
    name does not.

    ``agent_aliases=None`` declares this project's :data:`AGENT_ALIASES`; pass ``{}`` to build
    the same tree with no aliases at all (the control for "the data lives in config").
    """
    dest = dest.expanduser().resolve()
    docs_dir = dest / docs
    agents_dir = dest / agents
    docs_dir.mkdir(parents=True, exist_ok=True)
    agents_dir.mkdir(parents=True, exist_ok=True)
    (dest / ".git").mkdir(exist_ok=True)

    # Every fixture project carries the version 2 config Rosetta requires. Without it the
    # resolver refuses to run, so a fixture without one would test the refusal, not the tool.
    config: dict = {
        "version": 2,
        "paths": {
            "docs": docs,
            "research": "kb/research" if docs.startswith("kb/") else ".rosetta/research",
            "probes": "kb/probes" if docs.startswith("kb/") else ".rosetta/probes",
            "agents": agents,
        },
    }
    if citation_aliases:
        config["citation_aliases"] = dict(citation_aliases)
    aliases = AGENT_ALIASES if agent_aliases is None else agent_aliases
    if aliases:
        config["agent_aliases"] = dict(aliases)
    (dest / ".rosetta").mkdir(parents=True, exist_ok=True)
    (dest / ".rosetta" / "config.json").write_text(json.dumps(config, indent=2) + "\n",
                                                   encoding="utf-8")

    # The real fixture graphs, written by the real writer from the committed NRRAs.
    for slug in DOCS_SLUGS:
        src = GRAPHS / f"{slug}.fact-graph.json"
        if not src.is_file():
            raise SystemExit(f"missing fixture {src}; regenerate it with test_write_graph.py")
        d = docs_dir / slug
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, d / "fact-graph.json")

    for slug in MINI_SLUGS:
        d = docs_dir / slug
        d.mkdir(parents=True, exist_ok=True)
        target = d / "fact-graph.json"
        if not target.is_file():
            target.write_text(json.dumps(_mini_graph(slug), indent=2) + "\n")

    # The agent-local graph, under the agent that owns it.
    local_slug, local_agent = AGENT_LOCAL
    local_src = GRAPHS / f"{local_slug}.fact-graph.json"
    local_dir = agents_dir / local_agent / "kb"
    local_dir.mkdir(parents=True, exist_ok=True)
    if local_src.is_file():
        shutil.copy2(local_src, local_dir / "fact-graph.json")
    else:
        raise SystemExit(f"missing fixture {local_src}; regenerate it with test_write_graph.py")

    for name in AGENTS:
        ad = agents_dir / name
        ad.mkdir(parents=True, exist_ok=True)
        md = ad / "agent.md"
        if not md.is_file():
            md.write_text(f"---\nname: {name}\ndescription: fixture\n---\n# {name}\n")

    return dest


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.stderr.write("usage: materialize.py <dest-dir>\n")
        sys.exit(2)
    print(materialize(Path(sys.argv[1])))
