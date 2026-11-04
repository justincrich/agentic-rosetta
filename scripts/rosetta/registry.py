#!/usr/bin/env python3
"""registry.py — the Rosetta brain's active index of every knowledge graph + its consumers.

Single source of truth for "which agents use which graph." Rebuilt by an EXHAUSTIVE filesystem
scan (``scan``), not a hand-maintained map — that is the "can't miss a graph" property: any
``fact-graph.json`` anywhere in the repo (outside worktrees/scratch) enters the index.

The index (``.rosetta/registry.json``) has two sides:
  - ``graphs``: slug → {path, technology, kb_version, shared_from, consumers}
  - ``agents``: agent-name → {graphs: [slug, ...], role}   (``graphs`` is a LIST so an agent
    can consume multiple graphs — the generator merges them into one block)

Consumer discovery uses a small curated override table (agent-name prefix → slug) for the
handful of cases where the agent name and the graph slug differ (hetzner→hetzner-cloud,
aisdk→ai-sdk, spacetimedb→spacetime-db, cmux→cmux-specialist) and the shared-graph case
(cloudflare-rust consumes cloudflare-workers). This table is the ONE place to edit when adding
a new domain with a name mismatch; discovery of the graph FILES themselves is exhaustive.

Self-contained (no import of generate_excerpt) so the generator can import this freely.

CLI:
  registry.py scan                rebuild + write .rosetta/registry.json
  registry.py list                print every graph + its consumers
  registry.py graph-for <agent>   print the slug(s) an agent consumes
  registry.py consumers <slug>    print the agents that consume a graph
  registry.py validate            registry ↔ filesystem consistency (exit 0 = clean)
  registry.py borrowers <slug>    graphs that copied <slug>'s facts by value, with editions
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from naming import slug_for_agent, slug_from_graph_path, split_agent_name
from resolve import project_root as discover_repo_root
from version_label import format_kb_version, kb_edition
from versions import composed_entries, composed_freshness, declared_composed_slugs


@dataclass(frozen=True)
class Paths:
    """Everything reached from the target project root, derived in one place.

    Replaces three independently-rebound globals (_paths.root, _paths.registry,
    _paths.agents). One object, one rebinding point, so the three can no longer disagree
    about which project a scan is about.
    """

    root: Path

    @property
    def registry(self) -> Path:
        return self.root / ".rosetta" / "registry.json"

    @property
    def agents(self) -> Path:
        return self.root / "agents"


_paths = Paths(discover_repo_root())


def repo_root() -> Path:
    """The project this registry is about."""
    return _paths.root


def set_repo_root(path: str | Path) -> Path:
    """Rebind the target project. Used by tests and ``--root``."""
    global _paths
    _paths = Paths(Path(path).expanduser().resolve())
    return _paths.root


REGISTRY_VERSION = 1

# Dirs that never hold a canonical fact-graph.json — pruned during the walk.
EXCLUDE_DIR_NAMES = {
    ".git", ".worktrees", ".cmux", ".kb-run-sprint", "node_modules",
    "__pycache__", ".history", "tmp", "research", "sandbox", ".cursor",
}

# --------------------------------------------------------------------------- scan

def _iter_fact_graphs(root: Path | None = None):
    """Yield (absolute_path, slug) for every fact-graph.json in the repo (exhaustive)."""
    base = Path(root).expanduser().resolve() if root is not None else _paths.root
    for dirpath, dirnames, filenames in os.walk(base):
        # prune excluded dirs in-place so os.walk doesn't descend
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIR_NAMES]
        for fn in filenames:
            if fn == "fact-graph.json":
                p = Path(dirpath) / fn
                slug = slug_from_graph_path(p, base)
                if slug:
                    yield p, slug
    return


def _split_name_role(name: str) -> tuple[str, str]:
    """Delegating alias. Registry classifies a role-less agent as a ``specialist``."""
    return split_agent_name(name, default_role="specialist")


def _slug_for_graph(path: Path) -> str | None:
    """Delegating alias: the three graph layouts are described in ``naming``."""
    return slug_from_graph_path(path, _paths.root)


def _slug_for_agent(name: str, known_slugs: set[str] | None = None) -> str | None:
    """Delegating alias. ``known_slugs`` enables the longest-known-prefix fallback."""
    return slug_for_agent(name, known_slugs, default_role="specialist")


#: Failures that mean "this file is not readable as a graph" rather than "this is a bug".
#: Narrower than ``except Exception`` on purpose: a genuine bug should surface instead of
#: being folded into the "unknown" label, which is how a malformed graph became
#: indistinguishable from a missing one.
_UNREADABLE = (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError)


def _read_graph(path: Path) -> tuple[dict | None, bytes | None]:
    """Read and parse a fact-graph ONCE. ``(None, None)`` when it cannot be read.

    Every field scan needs is derived from this single result. The previous shape parsed
    the same file four times per scan (kb_version, composed_with, provenance fields,
    technology) and each parse had its own error policy.
    """
    try:
        raw = path.read_bytes()
        graph = json.loads(raw)
    except _UNREADABLE:
        return None, None
    return (graph, raw) if isinstance(graph, dict) else (None, None)


def _kb_version(graph: dict | None, raw: bytes | None) -> str:
    """Stable version label — shared with generate_excerpt via version_label.py."""
    if graph is None or raw is None:
        return "unknown"
    return format_kb_version(graph, raw)


def _composed_with(graph: dict | None) -> list[str]:
    """Graph slugs this graph declares as compositional sources (``_meta.composed_with``)."""
    if graph is None:
        return []
    meta = graph.get("_meta") or {}
    if not isinstance(meta, dict):
        return []
    entries = meta.get("composed_with") or []
    return sorted({str(e["graph"]) for e in entries if isinstance(e, dict) and e.get("graph")})


def _provenance_fields(graph: dict | None, path: Path) -> tuple[object, object]:
    """upstream_version + corpus_sha256 from the graph / sibling provenance.json."""
    upstream = (graph.get("v") or {}).get("version") if graph else None
    corpus = None
    prov_path = path.parent / "provenance.json"
    if prov_path.is_file():
        try:
            prov = json.loads(prov_path.read_text(encoding="utf-8"))
        except _UNREADABLE:
            prov = None
        if isinstance(prov, dict):
            if upstream is None:
                upstream = prov.get("upstream_version")
            corpus = prov.get("corpus_sha256")
    return upstream, corpus


def _technology(graph: dict | None, path: Path) -> str:
    if graph is None:
        return ""
    meta = graph.get("_meta") or {}
    if isinstance(meta, dict) and meta.get("technology"):
        return str(meta["technology"])
    return (_slug_for_graph(path) or "").replace("-", " ").title()


def _graph_record(path: Path, rel: str) -> dict:
    """Everything the registry records about one graph, from one parse."""
    graph, raw = _read_graph(path)
    upstream, corpus = _provenance_fields(graph, path)
    return {
        "path": rel,
        "technology": _technology(graph, path),
        "kb_version": _kb_version(graph, raw),
        "upstream_version": upstream,
        "corpus_sha256": corpus,
        "composed_with": _composed_with(graph),
        "shared_from": None,
    }


def scan() -> dict:
    """Rebuild the registry from the filesystem (exhaustive). Returns the registry dict.

    Manual bindings (``manual_bindings``) are PRESERVED across rescans — they survive the
    filesystem rebuild because they're user-authored, not auto-derived. After auto-deriving
    agents from names, manual bindings are applied on top (override or add)."""
    # preserve manual_bindings from the prior registry (survives rescan)
    manual: dict[str, list[str]] = {}
    if _paths.registry.is_file():
        try:
            manual = json.loads(_paths.registry.read_text()).get("manual_bindings", {})
        except Exception:
            manual = {}

    graphs: dict[str, dict] = {}
    # 1. discover every graph file (dedup by slug — e.g. morph-cloud's 3 pre-consolidation copies)
    for path, slug in _iter_fact_graphs():
        rel = str(path.relative_to(_paths.root))
        if slug not in graphs:
            graphs[slug] = _graph_record(path, rel)
        else:
            # duplicate path for the same slug (mirrored copies) — keep first, note the dup
            graphs[slug].setdefault("duplicate_paths", []).append(rel)

    # 2. discover consumers: every agent whose derived slug is a known graph
    agents: dict[str, dict] = {}
    known = set(graphs)
    for slug in graphs:
        graphs[slug]["consumers"] = []
    if _paths.agents.is_dir():
        for agent_dir in sorted(_paths.agents.iterdir()):
            if not agent_dir.is_dir() or not (agent_dir / "agent.md").is_file():
                continue
            name = agent_dir.name
            slug = _slug_for_agent(name, known)
            if slug in graphs:
                _, role = _split_name_role(name)
                agents[name] = {"graphs": [slug], "role": role}
                graphs[slug]["consumers"].append(name)

    # 3. apply manual bindings (override auto-derived; add custom-named agents)
    for agent, slugs in manual.items():
        _, role = _split_name_role(agent)
        agents[agent] = {"graphs": list(slugs), "role": role, "manual": True}
        for slug in slugs:
            if slug in graphs and agent not in graphs[slug]["consumers"]:
                graphs[slug]["consumers"].append(agent)

    return {"version": REGISTRY_VERSION, "graphs": graphs, "agents": agents,
            "manual_bindings": manual}


# --------------------------------------------------------------------------- persistence

def write(registry: dict | None = None) -> Path:
    """Scan (if needed) and write the registry to disk. Returns the path."""
    registry = registry or scan()
    _paths.registry.parent.mkdir(parents=True, exist_ok=True)
    tmp = _paths.registry.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")
    tmp.replace(_paths.registry)
    return _paths.registry


def load(force_rescan: bool = False) -> dict:
    """Load the registry; auto-scan if missing or stale-flagged."""
    if force_rescan or not _paths.registry.is_file():
        return scan()
    try:
        return json.loads(_paths.registry.read_text())
    except Exception:
        return scan()


# --------------------------------------------------------------------------- queries

def graph_for(agent_name: str) -> list[str]:
    """Slugs an agent consumes (list — usually length 1)."""
    reg = load()
    entry = reg.get("agents", {}).get(agent_name)
    return list(entry["graphs"]) if entry else []


def consumers(slug: str) -> list[str]:
    reg = load()
    g = reg.get("graphs", {}).get(slug)
    return list(g["consumers"]) if g else []


def all_agents() -> list[str]:
    return sorted(load().get("agents", {}).keys())


def all_graphs() -> list[str]:
    return sorted(load().get("graphs", {}).keys())


def find_borrowers(slug: str, root: Path | None = None) -> dict:
    """Reverse query: the original graph plus every graph that copied it BY VALUE.

    Walks the project's fact-graphs directly rather than reading ``registry.json``: the
    caller is usually the writer, immediately after the original moved, and a stale index
    would hide exactly the borrowers it needs to rewrite. One walk, one answer.

    Each borrower carries the edition it recorded, the original's current edition, and the
    freshness verdict from ``versions.composed_freshness``.
    """
    base = Path(root).expanduser().resolve() if root is not None else _paths.root
    original: dict | None = None
    original_rel: str | None = None
    found: list[tuple[str, str, dict]] = []
    for path, graph_slug in _iter_fact_graphs(base):
        graph, _raw = _read_graph(path)
        if graph is None:
            continue
        rel = str(path.relative_to(base))
        if graph_slug == slug and original is None:
            original, original_rel = graph, rel
            continue
        declares = slug in declared_composed_slugs(graph)
        copied = slug in composed_entries(graph)
        if declares or copied:
            found.append((graph_slug, rel, graph))
    borrowers = []
    for graph_slug, rel, graph in found:
        report = composed_freshness(graph, original, slug)
        # `report` carries the ORIGINAL's slug; the borrower's own name must win here.
        record = dict(report)
        record.update({"graph": graph_slug, "path": rel})
        borrowers.append(record)
    borrowers.sort(key=lambda item: item["graph"])
    return {
        "graph": slug,
        "path": original_rel,
        "kb_version": kb_edition(original) if isinstance(original, dict) else "",
        "present": original is not None,
        "borrowers": borrowers,
    }


# --------------------------------------------------------------------------- mutation

def add_graph(slug: str, path: str, technology: str = "") -> None:
    """Register a newly-authored graph (called by rosetta-bootstrap). Then rescan."""
    reg = load()
    graphs = reg.setdefault("graphs", {})
    if slug not in graphs:
        graphs[slug] = {"path": path, "technology": technology, "kb_version": "", "consumers": []}
    write(reg)


def bump(slug: str, kb_version: str) -> None:
    """Update a graph's kb_version after a refresh (called by rosetta-upgrade)."""
    reg = load()
    g = reg.get("graphs", {}).get(slug)
    if g:
        g["kb_version"] = kb_version
        write(reg)


# --------------------------------------------------------------------------- consistency

def validate() -> dict:
    """Check registry ↔ filesystem alignment. Returns {ok: bool, errors: [...]}."""
    errors: list[str] = []
    on_disk = scan()
    reg = load()

    reg_graphs, disk_graphs = set(reg.get("graphs", {})), set(on_disk["graphs"])
    for slug in sorted(disk_graphs - reg_graphs):
        errors.append(f"graph on disk but not in registry: {slug} ({on_disk['graphs'][slug]['path']})")
    for slug in sorted(reg_graphs - disk_graphs):
        errors.append(f"graph in registry but missing on disk: {slug} ({reg['graphs'][slug].get('path')})")

    # every registered graph file must actually exist
    for slug, g in reg.get("graphs", {}).items():
        p = _paths.root / g.get("path", "")
        if not p.is_file():
            errors.append(f"registered path missing: {slug} -> {g.get('path')}")

    # every listed consumer must have an agent.md
    for slug, g in reg.get("graphs", {}).items():
        for name in g.get("consumers", []):
            if not (_paths.agents / name / "agent.md").is_file():
                errors.append(f"consumer agent missing: {slug} -> {name}")

    # stored kb_version label vs recomputed from the graph bytes
    for slug, g in reg.get("graphs", {}).items():
        p = _paths.root / g.get("path", "")
        if not p.is_file():
            continue
        stored = g.get("kb_version")
        recomputed = _kb_version(*_read_graph(p))
        if stored and stored != recomputed:
            errors.append(
                f"kb_version drift {slug}: stored={stored!r} recomputed={recomputed!r}"
            )

    return {"ok": not errors, "errors": errors}


# --------------------------------------------------------------------------- CLI

def _cli(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=None,
                   help="target project root (default: $ROSETTA_PROJECT or cwd project)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan", help="rebuild + write .rosetta/registry.json")
    sub.add_parser("list", help="print every graph + its consumers")
    gf = sub.add_parser("graph-for", help="slug(s) an agent consumes")
    gf.add_argument("agent")
    cs = sub.add_parser("consumers", help="agents that consume a graph")
    cs.add_argument("slug")
    bw = sub.add_parser("borrowers", help="graphs that copied a graph's facts by value")
    bw.add_argument("slug")
    sub.add_parser("validate", help="registry ↔ filesystem consistency (exit 0 = clean)")
    sub.add_parser("agents", help="list all graph-blessed agents")
    rg = sub.add_parser("register", help="manually bind an agent (by name) to one or more graph slugs")
    rg.add_argument("agent")
    rg.add_argument("slug", nargs="+")
    ur = sub.add_parser("unregister", help="remove a manual agent→graph binding")
    ur.add_argument("agent")
    args = p.parse_args(argv)
    if args.root:
        set_repo_root(args.root)
        os.environ["ROSETTA_PROJECT"] = str(_paths.root)

    if args.cmd == "scan":
        path = write()
        reg = load()
        print(f"[registry] wrote {path} — {len(reg['graphs'])} graphs, {len(reg['agents'])} agents")
        return 0
    if args.cmd == "list":
        reg = load()
        for slug in sorted(reg["graphs"]):
            g = reg["graphs"][slug]
            n = len(g.get("consumers", []))
            print(f"{slug:20} {g.get('technology',''):28} consumers={n}  {g['path']}")
        return 0
    if args.cmd == "graph-for":
        slugs = graph_for(args.agent)
        print(" ".join(slugs) if slugs else f"(no graph for {args.agent})")
        return 0 if slugs else 1
    if args.cmd == "consumers":
        cs = consumers(args.slug)
        print("\n".join(cs) if cs else f"(no consumers for {args.slug})")
        return 0 if cs else 1
    if args.cmd == "borrowers":
        found = find_borrowers(args.slug)
        if not found["present"]:
            print(f"(no fact-graph for {args.slug} in this project)", file=sys.stderr)
            return 1
        print(f"{args.slug} kb_version={found['kb_version'] or '-'}")
        for borrower in found["borrowers"]:
            print(
                f"{borrower['graph']:28} "
                f"recorded={borrower['recorded_kb_version'] or '-':12} "
                f"current={borrower['current_kb_version'] or '-':12} "
                f"{borrower['status']}"
            )
        if not found["borrowers"]:
            print(f"(no borrowers for {args.slug})")
            return 1
        return 0
    if args.cmd == "agents":
        for a in all_agents():
            print(a)
        return 0
    if args.cmd == "validate":
        res = validate()
        if res["ok"]:
            print(f"[registry] OK — {len(load()['graphs'])} graphs consistent")
            return 0
        print("[registry] DRIFT:")
        for e in res["errors"]:
            print("  " + e)
        return 1
    if args.cmd == "register":
        # fail-closed: agent.md must exist; slug must be a known graph
        if not (_paths.agents / args.agent / "agent.md").is_file():
            print(f"register: no agent.md at agents/{args.agent}/", file=sys.stderr)
            return 2
        reg = load()
        unknown = [s for s in args.slug if s not in reg.get("graphs", {})]
        if unknown:
            print(f"register: unknown graph slug(s) {', '.join(map(repr, unknown))} — "
                  f"run 'list' to see slugs", file=sys.stderr)
            return 2
        # Bind ALL slugs at once: a single-slug register on a multi-graph agent would
        # REPLACE its bindings and silently drop the primary graph on the next sync.
        reg.setdefault("manual_bindings", {})[args.agent] = list(args.slug)
        write(reg)        # persist the manual_bindings
        write(scan())     # rescan → re-derives agents WITH manual_bindings applied
        print(f"[register] {args.agent} → [{', '.join(args.slug)}] (manual binding; survives rescan)")
        return 0
    if args.cmd == "unregister":
        reg = load()
        if args.agent in reg.get("manual_bindings", {}):
            del reg["manual_bindings"][args.agent]
            write(reg)     # persist manual_bindings minus the entry
            write(scan())  # rescan → agent reverts to auto-derived (or unbound)
            print(f"[unregister] {args.agent} manual binding removed")
        else:
            print(f"[unregister] {args.agent} had no manual binding")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
