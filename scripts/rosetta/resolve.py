#!/usr/bin/env python3
"""resolve.py — which project is this run about?

WHY THIS EXISTS
  ``registry.py`` and ``generate_excerpt.py`` each carried a byte-identical 25-line
  ``discover_repo_root``. Two copies of a pure function is survivable; two copies of a
  pure function that decides WHICH TREE GETS WRITTEN is not, because a later fix to one
  silently leaves the other indexing the wrong repo.

HOW THIS DIFFERS FROM roots.py — they are not the same question
  ``roots.py`` answers "where does the KB GO?" and deliberately does NOT walk up: its
  tier 4 is the current directory, because a shell standing in a repo is already the
  answer it wants.

  This module answers "which project am I INDEXING?" and DOES walk up to the nearest
  ``.git`` / ``.rosetta`` ancestor, because ``registry.py scan`` invoked from
  ``repo/scripts/`` must index ``repo``, not ``repo/scripts``.

  Collapsing the two would change where a KB lands. They stay separate, and this note
  exists so the next reader does not "finish the DRY job" by merging them.

THE FIXTURE RULE
  A script parent that already contains ``agents/`` wins before the cwd walk. That is
  what lets a test materialise a project in a temp dir and run the real scripts against
  it without exporting ``$ROSETTA_PROJECT``.
"""
from __future__ import annotations

import os
from pathlib import Path

#: ``resolve.py`` lives at <plugin>/scripts/rosetta/, so two parents up is the plugin.
PLUGIN_ROOT = Path(__file__).resolve().parents[2]

#: Directories that mark a project boundary, in the order they are preferred.
PROJECT_MARKERS = (".git", ".rosetta")


def project_root(explicit: str | Path | None = None) -> Path:
    """Resolve the target project. Never hardcodes ``~/Projects/brain``.

    Order (first hit wins):
      1. ``explicit``                 — ``--root`` or a caller's pinned path
      2. ``$ROSETTA_PROJECT``         — the env pin, same authority as ``--root``
      3. a script parent with ``agents/`` — the isolated fixture / in-tree copy
      4. the nearest ``.git`` or ``.rosetta`` ancestor of cwd
      5. cwd itself

    The plugin repo has no ``agents/``, so a run started inside it deliberately falls
    through to the caller's project rather than indexing the plugin.
    """
    if explicit:
        return Path(explicit).expanduser().resolve()

    env = os.environ.get("ROSETTA_PROJECT")
    if env:
        return Path(env).expanduser().resolve()

    if (PLUGIN_ROOT / "agents").is_dir():
        return PLUGIN_ROOT

    start = Path.cwd().resolve()
    cursor = start
    while True:
        if any((cursor / marker).exists() for marker in PROJECT_MARKERS):
            return cursor
        if cursor.parent == cursor:
            return start
        cursor = cursor.parent
