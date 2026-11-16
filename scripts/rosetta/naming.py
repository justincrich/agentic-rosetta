#!/usr/bin/env python3
"""naming.py — who is this agent, and which graph does it consume?

WHY THIS EXISTS
  Two modules answered these questions with DIFFERENT ANSWERS:

    registry._split_name_role        unknown role -> "specialist"
    generate_excerpt._split_name_role unknown role -> "implementer"

  Same concept, two answers, both live. Whichever ran last decided what an agent's role
  was. That is a latent bug wearing a DRY violation's clothes, and it is the reason this
  module exists before any of the bigger splits.

WHERE THE NAME RULES COME FROM
  This module used to carry technology tables: an agent-prefix→graph-slug map and a
  legacy docs-domain map, both listing real products. That made every new domain an edit to
  the plugin, and it put product data inside machinery.

  The rules now come from DATA the project declares in its own config
  (``agent_aliases``: agent-name prefix → graph slug), and the fallbacks are structural:

    shared / remapped   the config's alias table (the project says the two names differ)
    longest known slug  the longest prefix of the stem that IS an indexed slug
                        (``mastra-evals`` → ``mastra``)
    the bare stem       nothing matched, so the stem is the answer

  ``slug_for_agent`` and ``legacy_docs_domain`` take that table as an argument. Passing
  ``None`` means "this project declares no aliases", which is a different answer from an
  empty table only in that it cannot resolve a mismatch — never a silent fallback to a
  built-in product list.

THE ROLE DEFAULT
  ``split_agent_name`` takes ``default_role`` explicitly. There is no correct global
  default — the writer's registry calls ``"specialist"`` a graph-less agent, the delivery
  generator calls ``"implementer"`` the role to render for. Making it a required
  parameter is what stops the two answers from silently re-diverging.
"""
from __future__ import annotations

from pathlib import Path

#: Trailing name tokens that name an agent's role. Only these are PARSED off a name.
ROLES = ("planner", "implementer", "reviewer")

#: Values a caller may use as ``default_role``. ``specialist`` is here but NOT in ROLES,
#: because it is a classification for an agent whose name carries no role — adding it to
#: ROLES would change how ``foo-specialist`` splits, which is not a refactor.
DEFAULT_ROLES = ROLES + ("specialist",)

#: Legacy layouts still READ (a graph written before the repo declared its config). The
#: configured docs directory is checked FIRST and is the only one a writer targets; these two
#: stay so an unmigrated tree is still indexable. Held as data so the rule has one home.
GRAPH_LAYOUTS: tuple[tuple[tuple[str, ...], int], ...] = (
    (("docs",), 1),
    (("agents",), -2),
)


def split_agent_name(name: str, *, default_role: str) -> tuple[str, str]:
    """``widget-platform-implementer`` -> ``("widget-platform", "implementer")``.

    The role is the trailing hyphen token when it is a known role. Otherwise the whole
    name is the stem and ``default_role`` answers — the caller states which reading it
    wants, because the two existing callers want different ones.
    """
    if default_role not in DEFAULT_ROLES:
        # Fail closed: a typo here would silently attribute a graph to the wrong role.
        raise ValueError(f"default_role must be one of {DEFAULT_ROLES}, got {default_role!r}")
    if "-" in name:
        head, _, tail = name.rpartition("-")
        if tail in ROLES:
            return head, tail
    return name, default_role


def _longest_prefix_match(stem: str, table: dict[str, str]) -> str | None:
    if not table:
        return None
    parts = stem.split("-")
    for i in range(len(parts), 0, -1):
        candidate = "-".join(parts[:i])
        if candidate in table:
            return table[candidate]
    return None


def slug_for_agent(
    name: str,
    known_slugs: set[str] | None = None,
    *,
    default_role: str = "specialist",
    aliases: dict[str, str] | None = None,
) -> str | None:
    """Map an agent name to the graph slug it consumes.

    Order: the project's alias table → longest prefix of the stem that is itself a known
    slug (``mastra-evals`` -> ``mastra``) → the bare stem.

    ``aliases`` is the project's ``agent_aliases`` table (agent prefix → graph slug); it is
    passed in because the answer belongs to the project, not to this module. ``known_slugs``
    is required for the longest-known-prefix step; without it only the alias cases resolve.
    """
    stem, _ = split_agent_name(name, default_role=default_role)
    remapped = _longest_prefix_match(stem, aliases or {})
    if remapped:
        return remapped
    if known_slugs:
        known = _longest_prefix_match(stem, {slug: slug for slug in known_slugs})
        if known:
            return known
    return stem


def legacy_docs_domain(stem: str, aliases: dict[str, str] | None = None) -> str:
    """Map an agent stem to a ``docs/<domain>`` slug under the pre-config layout.

    An alias the project declared wins; otherwise the stem is the domain, which covers every
    case where the agent name and the graph directory agree. A legacy tree is never a writer
    target — this only keeps an unmigrated graph readable.
    """
    return _longest_prefix_match(stem, aliases or {}) or stem


def slug_from_graph_path(
    path: Path,
    repo_root: Path,
    docs_dir: Path | None = None,
    agents_dir: Path | None = None,
) -> str | None:
    """Derive the graph slug from a ``fact-graph.json`` path, or ``None`` if unrecognized.

    The repo's CONFIG decides first: a graph under the configured docs directory is named by
    its first segment relative to that directory (``kb/graphs/x/fact-graph.json`` → ``x``),
    and one under the configured agents directory by the agent that owns it
    (``prompts/x-implementer/kb/fact-graph.json`` → ``x``).

    Two legacy layouts stay readable, so an unmigrated tree is still indexed:
      ``docs/<slug>/fact-graph.json``           → slug
      ``agents/<name>/kb/fact-graph.json``      → stem of ``<name>``
    Neither is a writer target any more; the configured directory is.
    """
    parts = path.relative_to(repo_root).parts
    if docs_dir is not None:
        try:
            rel = path.relative_to(docs_dir).parts
        except ValueError:
            rel = ()
        if rel and len(rel) >= 2 and rel[-1] == "fact-graph.json":
            return rel[0]
    if agents_dir is not None:
        try:
            rel = path.relative_to(agents_dir).parts
        except ValueError:
            rel = ()
        if rel and len(rel) >= 3 and rel[-2] == "kb":
            return split_agent_name(rel[0], default_role="specialist")[0]
    if len(parts) >= 2 and parts[0] == "docs":
        return parts[1]
    if len(parts) >= 3 and parts[0] == "agents" and parts[-2] == "kb":
        return split_agent_name(parts[1], default_role="specialist")[0]
    return None


def graph_path_for(repo_root: Path, slug: str, docs_dir: Path | None = None) -> Path:
    """The canonical graph path for a slug: the configured docs directory, else the legacy one."""
    base = docs_dir if docs_dir is not None else repo_root / "docs"
    return base / slug / "fact-graph.json"
