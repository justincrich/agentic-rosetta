#!/usr/bin/env python3
"""generate_excerpt.py — fact-graph → per-role KB excerpt (the delivery generator).

Single source of truth: the fact-graph IS the KB. This generator turns ``fact-graph.json``
into a right-sized, per-role excerpt in two delivery forms:

  --kb-source condensed  the ~10-line bootstrap (runtime, entry points, limits, storage, key rules)
  --kb-source full       a rich per-role excerpt: platform identity + REJECT rules + role-relevant
                         widgets / API signatures / anti-patterns / traits  (the delivery default)

The ``full`` render is front-loaded with REJECT rules and the role's most-critical section so the
facts that matter most land at the top of context (mitigating lost-in-the-middle).

Graphs resolve in this order (see :func:`resolve_graph`):
  1. agent-local  ``agents/{name}/kb/fact-graph.json``              (morph-cloud)
  2. legacy docs  ``docs/<docs-domain>/fact-graph.json``            (hetzner, rust, kotlin, …)
  3. repo-shared  ``.rosetta/docs/{domain}/fact-graph.json``        (cloudflare-workers, flyio, …)

Step 2 takes its folder name from ``naming.legacy_docs_domain()``, so the ``docs/`` slug can
differ from the agent stem. Step 3 builds its path through :func:`fact_graph_path`, which
applies the ``naming.SHARED_GRAPHS`` remap INSIDE the path (cloudflare-rust → the
cloudflare-workers graph); the remap is not a third fallback of its own.

kb_version is ``<version> @ <pinned_date> sha:<8>`` — a content hash so graph drift is detectable.

Delivery forms:
  --embed             wrap the body in ``rosetta-kb:begin/end`` sentinels with kb_version stamped
                      in the opening marker. This is the form embedded in ``agent.md``. The body is
                      the COMPLETE fact-graph as minified JSON (``--embed-format json``, the default) —
                      a lossless, parseable knowledge graph. ``--embed-format markdown`` restores the
                      legacy per-role markdown render (one-flag rollback).
  (default)           plain markdown on stdout + a trailing ``<!-- kb_version: ... -->`` comment.
  --print-version     print only the kb_version string.
  --agent <dir|name>  resolve domain+role+graph from the agent (agent-local first), emit the excerpt.

No network, no agent calls — pure deterministic transform of a JSON file. Tested against the real
fact-graph fixtures (see test_generate_excerpt.py, test-embed-graph.sh).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

_ROSETTA = Path(__file__).resolve().parent.parent / "rosetta"
if str(_ROSETTA) not in sys.path:
    sys.path.insert(0, str(_ROSETTA))
from naming import (  # noqa: E402
    ROLES,
    SHARED_GRAPHS,
    legacy_docs_domain,
    split_agent_name,
)
from resolve import project_root  # noqa: E402
from version_label import format_kb_version  # noqa: E402
from versions import (  # noqa: E402
    COMPOSED_KEY,
    SECTIONS,
    composed_entries,
    declared_composed_slugs,
)


# Delegating alias: the 5-tier precedence lives in scripts/rosetta/resolve.py. Kept under
# the old name because callers and the extraction tests reach it as
# ``generate_excerpt.discover_repo_root``.
discover_repo_root = project_root


def set_repo_root(path: str | Path) -> Path:
    global REPO_ROOT
    REPO_ROOT = Path(path).expanduser().resolve()
    return REPO_ROOT


REPO_ROOT = discover_repo_root()
DEFAULT_DOMAIN = "cloudflare-workers"

# Domains whose agents share another domain's graph file now live in naming.SHARED_GRAPHS;
# the local alias keeps this module's older call sites readable.
SHARED_DOMAIN_MAP = SHARED_GRAPHS

# Human technology label per docs-domain (these graphs have no _meta.technology, so the render
# title would otherwise be an ugly title-cased slug like "Ai Sdk"/"Spacetime Db").
TECH_BY_DOMAIN = {
    "hetzner-cloud": "Hetzner Cloud",
    "ai-sdk": "Vercel AI SDK",
    "spacetime-db": "SpacetimeDB",
    "cmux": "cmux",
    "mastra": "Mastra",
    "rust": "Rust",
    "kotlin": "Kotlin (Jetpack Compose)",
    "swift": "Swift (SwiftUI)",
    "electron": "Electron",
}

BEGIN_MARKER = "<!-- rosetta-kb:begin"
END_MARKER = "<!-- rosetta-kb:end -->"


def fact_graph_path(domain: str) -> Path:
    """Repo-shared graph for a domain (``.rosetta/docs/<domain>/fact-graph.json``).

    Shared/aliased domains (e.g. cloudflare-rust) are remapped to their backing graph."""
    domain = SHARED_DOMAIN_MAP.get(domain, domain)
    return REPO_ROOT / ".rosetta" / "docs" / domain / "fact-graph.json"


def _docs_domain_for(stem: str) -> str | None:
    """Delegating alias: the legacy docs/ prefix table lives in ``naming``."""
    return legacy_docs_domain(stem)


def _split_name_role(agent_name: str) -> tuple[str, str]:
    """Delegating alias. The renderer treats a role-less agent as an ``implementer``."""
    return split_agent_name(agent_name, default_role="implementer")


def resolve_graph(agent_name_or_path: str) -> tuple[Path, str, str]:
    """Resolve the graph + domain + role for an agent name or directory.

    Order: agent-local ``kb/fact-graph.json`` first, then the repo-shared graph. Raises
    FileNotFoundError if neither exists (fail-closed)."""
    raw = Path(agent_name_or_path)
    agent_name = raw.name if raw.name else agent_name_or_path.strip("/").split("/")[-1]
    # accept a path to the agent dir or its agent.md
    if agent_name == "agent.md":
        agent_name = raw.parent.name
    domain, role = _split_name_role(agent_name)

    # 1. agent-local graph (morph-cloud): agents/{name}/kb/fact-graph.json
    agent_dir = REPO_ROOT / "agents" / agent_name
    local = agent_dir / "kb" / "fact-graph.json"
    if local.is_file():
        return local, domain, role

    # 2. docs/<domain>/fact-graph.json (hetzner, mastra, rust, kotlin, swift, spacetimedb,
    #    electron, ai-sdk, cmux). Domain slug may differ from the agent-name stem.
    docs_domain = _docs_domain_for(domain)
    if docs_domain:
        docs_graph = REPO_ROOT / "docs" / docs_domain / "fact-graph.json"
        if docs_graph.is_file():
            return docs_graph, docs_domain, role

    # 3. repo-shared graph (.rosetta/docs/{domain}/fact-graph.json, with shared-domain remap)
    shared = fact_graph_path(domain)
    if shared.is_file():
        return shared, domain, role

    raise FileNotFoundError(
        f"no fact-graph for agent {agent_name!r}: tried {local}, docs/{docs_domain or domain}, and {shared}"
    )


def _registry():
    """Lazy-load the rosetta registry module (avoids circular import at module load time)."""
    rosetta_dir = REPO_ROOT / "scripts" / "rosetta"
    if str(rosetta_dir) not in sys.path:
        sys.path.insert(0, str(rosetta_dir))
    import registry  # type: ignore
    return registry


def resolve_graphs(agent_name_or_path: str) -> tuple[list[tuple[Path, str]], str]:
    """Resolve the graph(s) + role for an agent, registry-first.

    Returns ([(graph_path, slug), ...], role). The list has >1 entry when an agent consumes
    multiple graphs (the generator merges them into one block). Falls back to legacy
    ``resolve_graph`` (single) if the registry is missing or the agent isn't indexed."""
    raw = Path(agent_name_or_path)
    name = raw.name if raw.name else agent_name_or_path.strip("/").split("/")[-1]
    if name == "agent.md":
        name = raw.parent.name
    try:
        reg = _registry()
        data = reg.load()
        entry = data.get("agents", {}).get(name)
        if entry:
            role = entry.get("role") or _split_name_role(name)[1]
            out: list[tuple[Path, str]] = []
            for slug in entry.get("graphs", []):
                g = data.get("graphs", {}).get(slug)
                if not g:
                    raise FileNotFoundError(f"registry lists graph {slug!r} for {name!r} but it is missing")
                out.append((REPO_ROOT / g["path"], slug))
            if out:
                return out, role
    except FileNotFoundError:
        raise
    except Exception as e:  # registry unavailable — legacy fallback (kept during cutover)
        sys.stderr.write(f"[generate_excerpt] registry miss for {name!r} ({e}); legacy fallback\n")
    path, domain, role = resolve_graph(name)
    return [(path, domain)], role


def kb_version(graph: dict, raw_bytes: bytes) -> str:
    """Stable version label — shared with registry via version_label.py.

    Accepts ``v.pinned_date`` (new writes) or ``v.pinned`` (legacy)."""
    return format_kb_version(graph, raw_bytes)


def _corpus_short(path: Path | None) -> str:
    if path is None:
        return ""
    prov = Path(path).parent / "provenance.json"
    if not prov.is_file():
        return ""
    try:
        return str(json.loads(prov.read_text(encoding="utf-8")).get("corpus_sha256") or "")[:8]
    except Exception:
        return ""


def _upstream(graph: dict) -> str:
    ver = (graph.get("v") or {}).get("version")
    return "" if ver is None else str(ver)


def begin_marker(
    domain: str,
    role: str,
    version: str,
    graph: dict,
    path: Path | None = None,
    fmt: str | None = None,
) -> str:
    extra = f' fmt="{fmt}"' if fmt else ""
    return (
        f'{BEGIN_MARKER} domain="{domain}" role="{role}" '
        f'kb_version="{version}" upstream="{_upstream(graph)}" '
        f'corpus="{_corpus_short(path)}"{extra}>'
    )


def condensed_excerpt(graph: dict, domain: str) -> str:
    """The ~10-line bootstrap. Fixed Cloudflare Workers copy, only lightly graph-aware.

    The graph supplies the title (``_meta.technology``, else ``domain``), ``v.runtime`` and
    three ``r.*_note`` lookups (``r_cpu_limits_note``, ``r_memory_128mb_note``,
    ``r_storage_decision_matrix_note``). Everything else — entry points, key rules, the KV
    consistency claim, ``compatibility_date`` — is literal text true of ``cloudflare-workers``,
    which is also ``DEFAULT_DOMAIN``. A non-Cloudflare graph therefore gets Cloudflare advice;
    see known problem #1 in ``docs/code-quality/known-problems.md``."""
    v = graph.get("v", {})
    r = graph.get("r", {})
    tech = graph.get("_meta", {}).get("technology", domain)
    limits = r.get("r_cpu_limits_note", "(see graph)")
    memory = r.get("r_memory_128mb_note", "(see graph)")
    storage = r.get("r_storage_decision_matrix_note", "(see graph)")
    runtime = v.get("runtime", "V8 isolates")
    return f"""# {tech} — FACT GRAPH (condensed bootstrap)

**Runtime**: {runtime}. No fs, no child_process, no require().
**Entry Points**: `export default {{ fetch, scheduled, queue, email }}` — ES modules required.
**Limits**: {limits}
**Memory**: {memory}
**Storage**: {storage}
**Key Rules**: NO global mutable state. NO blocking I/O. ALL persistence via bindings.
**KV**: eventually consistent (~60s) — use Durable Objects for strong consistency.
**compatibility_date** required in wrangler.toml."""


def _rules_by_severity(graph: dict) -> dict[str, list[tuple[str, str]]]:
    """Group rules (r_* keys) by their severity token (REJECT / r / nr)."""
    r = graph.get("r", {})
    out: dict[str, list[tuple[str, str]]] = {"REJECT": [], "r": [], "nr": []}
    for key, val in r.items():
        if key.endswith("_note") or not isinstance(val, str):
            continue
        sev = val.strip()
        name = key[2:] if key.startswith("r_") else key
        note = r.get(f"{key}_note", "")
        bucket = "REJECT" if sev == "REJECT" else sev if sev in out else "r"
        out.setdefault(bucket, []).append((name, note))
    return out


def _identity_block(graph: dict, domain: str) -> list[str]:
    """Render the ``v`` block generically so every graph schema is faithful — not just
    cloudflare-workers. Skips internal/pinned fields and caps long values."""
    v = graph.get("v", {})
    tech = (
        graph.get("_meta", {}).get("technology")
        or TECH_BY_DOMAIN.get(domain)
        or domain.replace("-", " ").title()
    )
    skip = {"pinned_date", "pinned", "version_source"}
    lines = [f"# {tech} — FULL fact-graph excerpt", ""]
    lines.append("## Platform identity")
    shown = 0
    for key, val in v.items():
        if key in skip:
            continue
        if isinstance(val, (list, tuple)):
            text = ", ".join(str(x) for x in val)
        elif isinstance(val, dict):
            text = json.dumps(val, ensure_ascii=False)
        else:
            text = str(val)
        text = text.replace("\n", " ").strip()
        if not text or len(text) > 180:
            text = (text[:177] + "...") if text else "(see graph)"
        lines.append(f"- **{key}**: {text}")
        shown += 1
        if shown >= 14:
            break
    lines.append("")
    return lines


def full_excerpt(graph: dict, domain: str, role: str) -> str:
    """The rich per-role render. REJECT rules first, then the role's emphasis section, then
    the full reference. Front-loaded so the role's most-critical facts survive lost-in-the-middle."""
    w = graph.get("w", {})
    a = graph.get("a", {})
    x = graph.get("x", {})
    t = graph.get("t", {})
    sev = _rules_by_severity(graph)

    lines: list[str] = _identity_block(graph, domain)
    lines.append(f"_Role: **{role}**_")
    lines.append("")

    lines.append("## Hard rules — REJECT (will break at runtime)")
    for name, note in sev["REJECT"]:
        lines.append(f"- **{name}** — {note}")
    if not sev["REJECT"]:
        lines.append("- (none marked REJECT)")
    lines.append("")

    lines.append("## Required limits")
    for name, note in sev["r"][:12]:
        lines.append(f"- {name}: {note}")
    if not sev["r"]:
        lines.append("- (none)")
    lines.append("")

    # Role-relevant emphasis. Every role gets the full graph available, but the FIRST
    # detailed section is the one the role acts on most, so it lands highest in context.
    if role == "planner":
        lines.append("## Components / bindings (widgets)")
        for name, node in w.items():
            if isinstance(node, dict):
                lines.append(f"- **{name}** ({node.get('i', node.get('t', '?'))}): {node.get('note', '')}")
        lines.append("")
        lines.append("## Type contracts (traits)")
        for name, sig in t.items():
            lines.append(f"- `{name}`: {sig}")
    elif role == "implementer":
        lines.append("## API signatures")
        for name, sig in a.items():
            lines.append(f"- `{name}`: {sig}")
        lines.append("")
        lines.append("## Anti-patterns (wrong → right)")
        for wrong, right in x.items():
            lines.append(f"- **{wrong}** → {right}")
    else:  # reviewer
        lines.append("## Anti-pattern checklist (reject/flag these)")
        for wrong, right in x.items():
            lines.append(f"- **{wrong}** → {right}")
        lines.append("")
        lines.append("## REJECT rules to enforce")
        for name, note in sev["REJECT"]:
            lines.append(f"- {name}: {note}")

    lines.append("")
    lines.append("## All API signatures (reference)")
    for name, sig in a.items():
        lines.append(f"- `{name}`: {sig}")
    lines.extend(composed_excerpt(graph, role))
    return "\n".join(lines) + "\n"


def composed_excerpt(graph: dict, role: str) -> list[str]:
    """Render borrowed facts BY VALUE under the source's own banner.

    Never merged into the host's sections: a copied ``r`` key must not overwrite a host rule
    of the same name, and a reader has to be able to see which codebase a fact came from.
    """
    lines: list[str] = []
    for slug, entry in composed_entries(graph).items():
        facts = entry.get("facts") if isinstance(entry, dict) else None
        if not isinstance(facts, dict):
            continue
        edition = entry.get("kb_version") or "unknown"
        lines.append("")
        lines.append("---")
        lines.append(f"## [composed: {slug}] borrowed facts — {slug} edition {edition}")
        lines.append("")
        # Only the six fact sections, so the borrowed render cannot recurse into its own
        # composition (facts carry no _meta, hence no declaration).
        borrowed = {section: facts.get(section) or {} for section in SECTIONS}
        lines.append(full_excerpt(borrowed, f"composed:{slug}", role).rstrip())
    return lines


def _original_graph_path(slug: str) -> Path:
    """Where this project keeps a graph's own file (modern layout, then legacy)."""
    remapped = SHARED_DOMAIN_MAP.get(slug, slug)
    for candidate in (
        REPO_ROOT / ".rosetta" / "docs" / remapped / "fact-graph.json",
        REPO_ROOT / "docs" / remapped / "fact-graph.json",
        REPO_ROOT / "agents" / slug / "kb" / "fact-graph.json",
    ):
        if candidate.is_file():
            return candidate
    return REPO_ROOT / ".rosetta" / "docs" / remapped / "fact-graph.json"


def assert_composed_complete(graph: dict, slug: str) -> None:
    """Fail closed when a declaration has no copy, or its original is not in this project.

    A declaration that renders as nothing IS the silent partial render this feature exists
    to remove, so it is an error, not a warning, and there is no single-graph fallback."""
    declared = declared_composed_slugs(graph)
    if not declared:
        return
    copied = set(composed_entries(graph))
    missing_copy = [source for source in declared if source not in copied]
    if missing_copy:
        raise ValueError(
            f"composed-copy-missing: {slug} declares {missing_copy} but its body carries no "
            f"copy under {COMPOSED_KEY!r}"
        )
    missing_original = [
        source for source in declared if not _original_graph_path(source).is_file()
    ]
    if missing_original:
        details = "; ".join(
            f"{source} -> {_original_graph_path(source)}" for source in missing_original
        )
        raise ValueError(
            f"composed-original-missing: {slug} declares {details} but no such fact-graph "
            f"exists in this project"
        )


def render_embed(graph: dict, raw_bytes: bytes, domain: str, role: str, path: Path | None = None) -> str:
    """The body block embedded in ``agent.md``: dense render wrapped in rosetta-kb sentinels
    with kb_version stamped in the opening marker (so the drift guard can read the version
    without parsing the body)."""
    body = full_excerpt(graph, domain, role)
    version = kb_version(graph, raw_bytes)
    begin = begin_marker(domain, role, version, graph, path)
    return f"{begin}\n{body}{END_MARKER}"


def _composite_version(items: list[tuple[dict, bytes, str]]) -> str:
    """kb_version for a merged block: the single graph's version when N==1, else a composite
    hash over every graph so any constituent change is detectable."""
    if len(items) == 1:
        return kb_version(*items[0][:2])
    h = hashlib.sha256()
    for _graph, raw, slug in items:
        h.update(slug.encode())
        h.update(raw)
    return f"merge[{len(items)}] sha:{h.hexdigest()[:8]}"


def render_embed_multi(
    items: list[tuple[dict, bytes, str]],
    role: str,
    paths: list[Path | None] | None = None,
) -> str:
    """Embed block for one or more graphs. N==1 renders exactly as ``render_embed`` (clean, no
    prefix). N>1 merges: a unioned REJECT quick-reference at the top (every domain's
    will-break-at-runtime rules surfaced first), then each graph's full per-role render under its
    own ``[slug]`` banner — separate sections mean no key can silently overwrite another."""
    paths = paths or [None] * len(items)
    if len(items) == 1:
        graph, raw, slug = items[0]
        return render_embed(graph, raw, slug, role, paths[0])

    slugs = [s for _, _, s in items]
    version = _composite_version(items)
    begin = begin_marker("|".join(slugs), role, version, items[0][0], paths[0])

    lines: list[str] = [f"# Merged fact-graph excerpt ({len(items)} graphs)", "", f"_Role: **{role}**_", ""]
    lines.append("## Hard rules — REJECT (unioned across all graphs; will break at runtime)")
    any_reject = False
    for graph, _raw, slug in items:
        for name, note in _rules_by_severity(graph)["REJECT"]:
            lines.append(f"- **[{slug}] {name}** — {note}")
            any_reject = True
    if not any_reject:
        lines.append("- (none marked REJECT)")
    lines.append("")

    for graph, _raw, slug in items:
        tech = graph.get("_meta", {}).get("technology") or slug.replace("-", " ").title()
        lines.append(f"---")
        lines.append(f"## [{slug}] {tech}")
        lines.append("")
        # full per-role render for this graph (its own REJECT/limits/sigs, scoped under this banner)
        lines.append(full_excerpt(graph, slug, role).rstrip())
        lines.append("")
    body = "\n".join(lines).rstrip() + "\n"
    return f"{begin}\n{body}{END_MARKER}"


def _strip_meta(graph: dict) -> dict:
    """Return a shallow copy of *graph* without the ``_meta`` key.

    ``_meta`` is research/provenance metadata (repo URLs, confidence, sources). It is not
    agent-facing fact content, so the embed payload drops it to save context. ``kb_version``
    is still computed from the raw file bytes (including ``_meta``) so the drift guard still
    detects any graph file change."""
    return {k: v for k, v in graph.items() if k != "_meta"}


def render_embed_json(
    items: list[tuple[dict, bytes, str]],
    role: str,
    paths: list[Path | None] | None = None,
) -> str:
    """Embed block carrying the fact-graph(s) as minified JSON (the delivery default).

    N==1 → the bare graph object (minus ``_meta``), minified. N>1 → a slug-keyed map
    ``{slug: graph, ...}``, minified (no agent consumes >1 graph today, but the multi-graph
    contract is preserved). ``_meta`` is stripped from the body; ``kb_version`` is still
    stamped from raw bytes so content drift remains detectable.

    Serialization is deterministic: ``json.loads`` preserves the file's key order and ``json.dumps``
    re-emits it with fixed separators, so the embedded form is a canonical minified copy regardless
    of the source file's whitespace — the drift guard's regenerate-and-compare stays stable."""
    paths = paths or [None] * len(items)
    slugs = [s for _, _, s in items]
    version = _composite_version(items)
    domain_attr = "|".join(slugs)
    if len(items) == 1:
        payload: dict = _strip_meta(items[0][0])
    else:
        payload = {slug: _strip_meta(graph) for graph, _raw, slug in items}
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    # Fail-closed: a graph value must never smuggle the end sentinel — it would truncate the
    # sentinel-based extraction in embed-graph.sh and wedge the drift guard into a re-embed loop.
    if "rosetta-kb:end" in body:
        raise ValueError(
            f"fact-graph for {domain_attr!r} contains the literal 'rosetta-kb:end' sentinel; refusing to embed"
        )
    begin = begin_marker(domain_attr, role, version, items[0][0], paths[0], fmt="json")
    return f"{begin}\n{body}\n{END_MARKER}"


def _load_graph(path: Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    return json.loads(raw), raw


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--domain", default=DEFAULT_DOMAIN)
    p.add_argument("--role", choices=ROLES, default="implementer")
    p.add_argument("--kb-source", choices=("condensed", "full"), default="full")
    p.add_argument("--print-version", action="store_true", help="print only the kb_version string and exit")
    p.add_argument("--graph", help="override path to fact-graph.json (for tests)")
    p.add_argument("--agent", help="resolve domain+role+graph from an agent dir or name")
    p.add_argument("--embed", action="store_true",
                   help="wrap output in rosetta-kb:begin/end sentinels with kb_version (agent.md body form)")
    p.add_argument("--embed-format", choices=("json", "markdown"), default="json",
                   help="body format for --embed: minified JSON graph (default) or legacy markdown render")
    p.add_argument("--root", default=None,
                   help="target project root (default: $ROSETTA_PROJECT or cwd project)")
    args = p.parse_args(argv)
    if args.root:
        set_repo_root(args.root)
        os.environ["ROSETTA_PROJECT"] = str(REPO_ROOT)

    path: Path | None = None
    items: list[tuple[dict, bytes, str]] = []
    item_paths: list[Path | None] = []
    role = args.role
    try:
        if args.agent:
            graph_paths, role = resolve_graphs(args.agent)
            if "--role" in argv:
                role = args.role
            for pth, slug in graph_paths:
                g, raw = _load_graph(pth)
                items.append((g, raw, slug))
                item_paths.append(pth)
            path = graph_paths[0][0]
        elif args.graph:
            path = Path(args.graph)
            g, raw = _load_graph(path)
            items.append((g, raw, args.domain))
            item_paths.append(path)
        else:
            path = fact_graph_path(args.domain)
            g, raw = _load_graph(path)
            items.append((g, raw, args.domain))
            item_paths.append(path)
    except FileNotFoundError as e:
        msg = str(e)
        if path is not None and str(path) in msg:
            msg = f"fact-graph not found: {path}"
        elif "no fact-graph for agent" not in msg:
            msg = f"fact-graph not found: {msg}"
        sys.stderr.write(f"generate_excerpt: {msg}\n")
        return 2
    except json.JSONDecodeError as e:
        sys.stderr.write(f"generate_excerpt: invalid JSON in {path}: {e}\n")
        return 2

    # Fail closed on a graph that declares a composition it cannot show (AC-3). No graph is
    # rendered past this point, and there is no silent single-graph fallback.
    for graph, _raw, slug in items:
        try:
            assert_composed_complete(graph, slug)
        except ValueError as e:
            sys.stderr.write(f"generate_excerpt: {e}\n")
            return 2

    version = _composite_version(items)
    if args.print_version:
        print(version)
        return 0

    if args.embed:
        if args.embed_format == "markdown":
            print(render_embed_multi(items, role, item_paths))
        else:
            print(render_embed_json(items, role, item_paths))
        return 0

    # plain (non-embed) output: single-graph only (the A/B apparatus path)
    graph, raw, domain = items[0]
    if args.kb_source == "condensed":
        body = condensed_excerpt(graph, domain)
    else:
        body = full_excerpt(graph, domain, role)
    print(body)
    print(f"\n<!-- kb_version: {version} | kb_source: {args.kb_source} | role: {role} | domain: {domain} -->")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
