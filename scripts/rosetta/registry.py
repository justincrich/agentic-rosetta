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
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

def discover_repo_root(explicit: str | Path | None = None) -> Path:
    """Resolve the target project. Never hardcodes ~/Projects/brain.

    Order: explicit --root / set_repo_root → $ROSETTA_PROJECT → a script-parent
    that already has agents/ (isolated fixture / in-tree copy) → cwd's git/.rosetta
    ancestor → cwd. The plugin repo itself has no agents/, so a run from the
    plugin falls through to the caller's project.
    """
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("ROSETTA_PROJECT")
    if env:
        return Path(env).expanduser().resolve()
    script_project = Path(__file__).resolve().parents[2]
    if (script_project / "agents").is_dir():
        return script_project
    start = Path.cwd().resolve()
    cur = start
    while True:
        if (cur / ".git").exists() or (cur / ".rosetta").is_dir():
            return cur
        if cur.parent == cur:
            return start
        cur = cur.parent


def set_repo_root(path: str | Path) -> Path:
    """Rebind module-level paths. Used by tests and --root."""
    global REPO_ROOT, REGISTRY_PATH, AGENTS_DIR
    REPO_ROOT = Path(path).expanduser().resolve()
    REGISTRY_PATH = REPO_ROOT / ".rosetta" / "registry.json"
    AGENTS_DIR = REPO_ROOT / "agents"
    return REPO_ROOT


REPO_ROOT = discover_repo_root()
REGISTRY_PATH = REPO_ROOT / ".rosetta" / "registry.json"
AGENTS_DIR = REPO_ROOT / "agents"
REGISTRY_VERSION = 1
ROLES = ("planner", "implementer", "reviewer")

# Dirs that never hold a canonical fact-graph.json — pruned during the walk.
EXCLUDE_DIR_NAMES = {
    ".git", ".worktrees", ".cmux", ".kb-run-sprint", "node_modules",
    "__pycache__", ".history", "tmp", "research", "sandbox", ".cursor",
}

# Agent-name prefix → graph slug, ONLY when the agent stem and the graph slug are spelled
# differently (hyphen mismatches). Direct stem==slug matches (flyio, modal, rust, kotlin,
# swift, electron, openhands, tauri, morph-cloud, cmux) and prefix-of-stem matches
# (mastra-evals→mastra, cmux-specialist→cmux) are handled by the longest-slug-prefix fallback
# in _slug_for_agent — they need no entry here.
AGENT_PREFIX_TO_SLUG = {
    "hetzner": "hetzner-cloud",
    "aisdk": "ai-sdk",
    "spacetimedb": "spacetime-db",
}

# Agents whose stem maps to a DIFFERENT graph they share (no graph of their own).
SHARED_GRAPHS = {"cloudflare-rust": "cloudflare-workers"}


# --------------------------------------------------------------------------- scan

def _iter_fact_graphs():
    """Yield (absolute_path, slug) for every fact-graph.json in the repo (exhaustive)."""
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        # prune excluded dirs in-place so os.walk doesn't descend
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIR_NAMES]
        for fn in filenames:
            if fn == "fact-graph.json":
                p = Path(dirpath) / fn
                slug = _slug_for_graph(p)
                if slug:
                    yield p, slug


def _slug_for_graph(path: Path) -> str | None:
    """Derive the graph slug from a fact-graph.json path.

    Recognizes the three layouts (pre- and post-consolidation):
      .rosetta/docs/<slug>/fact-graph.json  → slug
      docs/<slug>/fact-graph.json           → slug
      agents/<name>/kb/fact-graph.json      → stem of <name> (morph-cloud-*)
    """
    rel = path.relative_to(REPO_ROOT)
    parts = rel.parts
    if len(parts) >= 3 and parts[0] == ".rosetta" and parts[1] == "docs":
        return parts[2]
    if len(parts) >= 2 and parts[0] == "docs":
        return parts[1]
    if len(parts) >= 3 and parts[0] == "agents" and parts[-2] == "kb":
        return _split_name_role(parts[1])[0]
    return None


def _split_name_role(name: str) -> tuple[str, str]:
    """``cloudflare-workers-implementer`` → (``cloudflare-workers``, ``implementer``)."""
    if "-" in name:
        head, _, tail = name.rpartition("-")
        if tail in ROLES:
            return head, tail
    return name, "specialist"


def _slug_for_agent(name: str, known_slugs: set[str] | None = None) -> str | None:
    """Map an agent name to the graph slug it consumes.

    Order: shared-graph override → spelling-remap (hetzner→hetzner-cloud) → longest prefix of
    the stem that is itself a known slug (mastra-evals→mastra, cmux-specialist→cmux,
    flyio→flyio). ``known_slugs`` is required for the last fallback; without it, only the
    shared/remap cases resolve."""
    stem, _ = _split_name_role(name)
    if stem in SHARED_GRAPHS:
        return SHARED_GRAPHS[stem]
    parts = stem.split("-")
    # spelling remap (longest prefix first)
    for i in range(len(parts), 0, -1):
        prefix = "-".join(parts[:i])
        if prefix in AGENT_PREFIX_TO_SLUG:
            return AGENT_PREFIX_TO_SLUG[prefix]
    # direct: longest prefix of the stem that is a known graph slug
    if known_slugs:
        for i in range(len(parts), 0, -1):
            cand = "-".join(parts[:i])
            if cand in known_slugs:
                return cand
    return stem


def _kb_version(path: Path) -> str:
    """Stable version label for a graph file (mirrors generate_excerpt.kb_version)."""
    try:
        raw = path.read_bytes()
        graph = json.loads(raw)
    except Exception:
        return "unknown"
    meta = graph.get("_meta", {})
    v = graph.get("v", {})
    version = v.get("kb_version") or meta.get("version") or v.get("version") or "unknown"
    pinned = v.get("pinned_date") or meta.get("pinned_date") or ""
    sha8 = hashlib.sha256(raw).hexdigest()[:8]
    return f"{version}{' @ ' + pinned if pinned else ''} sha:{sha8}"


def _technology(path: Path) -> str:
    try:
        graph = json.loads(path.read_bytes())
    except Exception:
        return ""
    return (
        graph.get("_meta", {}).get("technology")
        or _slug_for_graph(path).replace("-", " ").title()
    )


def scan() -> dict:
    """Rebuild the registry from the filesystem (exhaustive). Returns the registry dict.

    Manual bindings (``manual_bindings``) are PRESERVED across rescans — they survive the
    filesystem rebuild because they're user-authored, not auto-derived. After auto-deriving
    agents from names, manual bindings are applied on top (override or add)."""
    # preserve manual_bindings from the prior registry (survives rescan)
    manual: dict[str, list[str]] = {}
    if REGISTRY_PATH.is_file():
        try:
            manual = json.loads(REGISTRY_PATH.read_text()).get("manual_bindings", {})
        except Exception:
            manual = {}

    graphs: dict[str, dict] = {}
    # 1. discover every graph file (dedup by slug — e.g. morph-cloud's 3 pre-consolidation copies)
    for path, slug in _iter_fact_graphs():
        rel = str(path.relative_to(REPO_ROOT))
        if slug not in graphs:
            graphs[slug] = {
                "path": rel,
                "technology": _technology(path),
                "kb_version": _kb_version(path),
                "shared_from": None,
            }
        else:
            # duplicate path for the same slug (mirrored copies) — keep first, note the dup
            graphs[slug].setdefault("duplicate_paths", []).append(rel)

    # 2. discover consumers: every agent whose derived slug is a known graph
    agents: dict[str, dict] = {}
    known = set(graphs)
    for slug in graphs:
        graphs[slug]["consumers"] = []
    if AGENTS_DIR.is_dir():
        for agent_dir in sorted(AGENTS_DIR.iterdir()):
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
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")
    tmp.replace(REGISTRY_PATH)
    return REGISTRY_PATH


def load(force_rescan: bool = False) -> dict:
    """Load the registry; auto-scan if missing or stale-flagged."""
    if force_rescan or not REGISTRY_PATH.is_file():
        return scan()
    try:
        return json.loads(REGISTRY_PATH.read_text())
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
        p = REPO_ROOT / g.get("path", "")
        if not p.is_file():
            errors.append(f"registered path missing: {slug} -> {g.get('path')}")

    # every listed consumer must have an agent.md
    for slug, g in reg.get("graphs", {}).items():
        for name in g.get("consumers", []):
            if not (AGENTS_DIR / name / "agent.md").is_file():
                errors.append(f"consumer agent missing: {slug} -> {name}")

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
    sub.add_parser("validate", help="registry ↔ filesystem consistency (exit 0 = clean)")
    sub.add_parser("agents", help="list all graph-blessed agents")
    rg = sub.add_parser("register", help="manually bind an agent (by name) to a graph slug")
    rg.add_argument("agent")
    rg.add_argument("slug")
    ur = sub.add_parser("unregister", help="remove a manual agent→graph binding")
    ur.add_argument("agent")
    args = p.parse_args(argv)
    if args.root:
        set_repo_root(args.root)
        os.environ["ROSETTA_PROJECT"] = str(REPO_ROOT)

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
        if not (AGENTS_DIR / args.agent / "agent.md").is_file():
            print(f"register: no agent.md at agents/{args.agent}/", file=sys.stderr)
            return 2
        reg = load()
        if args.slug not in reg.get("graphs", {}):
            print(f"register: unknown graph slug {args.slug!r} — run 'list' to see slugs",
                  file=sys.stderr)
            return 2
        reg.setdefault("manual_bindings", {})[args.agent] = [args.slug]
        write(reg)        # persist the manual_bindings
        write(scan())     # rescan → re-derives agents WITH manual_bindings applied
        print(f"[register] {args.agent} → {args.slug} (manual binding; survives rescan)")
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
