#!/usr/bin/env python3
"""Materialize a real-graph fixture project for the vendored KB-delivery tests.

Copies committed fact-graph fixtures (real graphs, not stubs) into a target
project's .rosetta/docs/ and agents/*/kb/ layouts so registry / generate_excerpt
/ embed-graph can run against a self-contained tree that is not ~/Projects/brain.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GRAPHS = HERE / "graphs"
AGENTS_SRC = HERE / "kb-project" / "agents"

# Graphs that live at .rosetta/docs/<slug>/fact-graph.json
DOCS_SLUGS = ("cloudflare-workers", "rust", "hetzner-cloud", "mastra", "ai-sdk",
              "spacetime-db", "cmux", "flyio")

# Extra empty-but-valid graphs so mapping cases have a known slug on disk.
MINI_SLUGS = ("hetzner-cloud", "mastra", "ai-sdk", "spacetime-db", "cmux", "flyio")

# Agents that must exist for mapping + --agent resolution.
AGENTS = (
    "cloudflare-workers-implementer",
    "cloudflare-workers-planner",
    "cloudflare-workers-reviewer",
    "cloudflare-rust-implementer",
    "cloudflare-rust-planner",
    "cloudflare-rust-reviewer",
    "mastra-implementer",
    "mastra-evals-implementer",
    "hetzner-implementer",
    "aisdk-implementer",
    "spacetimedb-implementer",
    "cmux-specialist",
    "flyio-reviewer",
    "morph-cloud-implementer",
    "rust-implementer",
)


def _mini_graph(slug: str) -> dict:
    return {
        "_meta": {"technology": slug.replace("-", " ").title()},
        "v": {"pkg": slug, "version": "0.0.0", "kb_version": "1.0.0",
              "pinned_date": "2026-01-01"},
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
) -> Path:
    """Build a real fixture project. ``docs``/``agents`` are the layout its config declares.

    The default is the standard layout; a caller that wants the config's authority proven
    passes a custom one (``kb/graphs``, ``prompts``) and gets the same graphs and agents under
    it, so every reader that honours the config finds them and every reader that hardcodes a
    name does not.
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
    (dest / ".rosetta").mkdir(parents=True, exist_ok=True)
    (dest / ".rosetta" / "config.json").write_text(json.dumps(config, indent=2) + "\n",
                                                   encoding="utf-8")

    # Real committed graphs.
    for slug, src_name in (
        ("cloudflare-workers", "cloudflare-workers.fact-graph.json"),
        ("rust", "rust.fact-graph.json"),
    ):
        src = GRAPHS / src_name
        if not src.is_file():
            raise SystemExit(f"missing fixture {src}")
        d = docs_dir / slug
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, d / "fact-graph.json")

    for slug in MINI_SLUGS:
        d = docs_dir / slug
        d.mkdir(parents=True, exist_ok=True)
        target = d / "fact-graph.json"
        if not target.is_file():
            target.write_text(json.dumps(_mini_graph(slug), indent=2) + "\n")

    morph_src = GRAPHS / "morph-cloud.fact-graph.json"
    morph_dir = agents_dir / "morph-cloud-implementer" / "kb"
    morph_dir.mkdir(parents=True, exist_ok=True)
    if morph_src.is_file():
        shutil.copy2(morph_src, morph_dir / "fact-graph.json")

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
