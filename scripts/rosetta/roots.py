#!/usr/bin/env python3
"""roots.py — one decision, made the same way by every KB writer and reader.

WHY THIS EXISTS
  write-graph.py and provenance.py used to default their root to `Path.cwd()`. That made the
  destination of a knowledge base a property of WHERE THE SHELL HAPPENED TO BE STANDING rather
  than of the config the user set. Running /rosetta-research from an unrelated repo therefore
  wrote (or looked for) the KB in that repo, silently, with exit 0 — and no amount of editing
  ~/.rosetta/config.json changed it, because neither script ever read that file.

  rosetta_config.py already owns "where do artifacts go". This module is the bridge: it asks the
  resolver for the docs class and hands both scripts the same answer.

PRECEDENCE for the docs directory (first that yields a value wins)
  1. --root DIR            caller pinned a repo   -> <root>/.rosetta/docs   (overrides config)
  2. $ROSETTA_PROJECT      caller pinned a repo   -> <root>/.rosetta/docs   (overrides config)
  3. rosetta_config.py     roots.docs             -> whatever it resolves to
  4. cwd                   resolver unavailable   -> <cwd>/.rosetta/docs    (pre-config behavior)

  Tiers 1-2 are an explicit instruction and must beat the config, or --root would be a lie.
  Tier 4 is honest degradation: if the resolver cannot answer, behave exactly as before rather
  than inventing a root of our own.

NOTE the layout is NOT always <root>/.rosetta/docs. With roots.docs = "device" the configured
root IS the .rosetta directory, so the KB lives at $ROSETTA_HOME/docs/<tech>. That is precisely
why tier 3 asks the resolver for a DIRECTORY instead of decorating a repo root here.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def resolver_path() -> Path | None:
    """rosetta_config.py, searched in the order the skills use."""
    cands: list[Path] = []
    env = os.environ.get("ROSETTA_CONFIG")
    if env:
        cands.append(Path(env).expanduser())
    cands.append(HERE.parent.parent / "assets" / "rosetta_config.py")
    home = Path(os.environ.get("ROSETTA_HOME") or (Path.home() / ".rosetta")).expanduser()
    cands.append(home / "bin" / "rosetta_config.py")
    for c in cands:
        if c.is_file():
            return c
    return None


def resolved_docs_dir(warn: bool = True) -> Path | None:
    """Ask rosetta_config.py where the docs class lives. None if it cannot answer."""
    r = resolver_path()
    if r is None:
        if warn:
            print("rosetta-roots: WARN: rosetta_config.py not found; falling back to cwd",
                  file=sys.stderr)
        return None
    try:
        out = subprocess.run(
            [sys.executable, str(r), "root", "--class", "docs"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if warn:
            print(f"rosetta-roots: WARN: resolver failed ({exc}); falling back to cwd",
                  file=sys.stderr)
        return None
    lines = [ln for ln in (out.stdout or "").strip().splitlines() if ln.strip()]
    if out.returncode != 0 or not lines:
        if warn:
            detail = (out.stderr or "").strip() or f"exit {out.returncode}"
            print(f"rosetta-roots: WARN: resolver gave no docs root ({detail}); "
                  f"falling back to cwd", file=sys.stderr)
        return None
    return Path(lines[-1]).expanduser().resolve()


def choose(explicit_root: str | Path | None, warn: bool = True) -> tuple[Path, Path | None]:
    """Return (repo_root, docs_override).

    repo_root is what registry/agent lookups use — always a repo, never the KB store.
    docs_override is the resolved docs DIRECTORY, or None to mean "<repo_root>/.rosetta/docs".
    """
    if explicit_root:
        return Path(explicit_root).expanduser().resolve(), None
    env = os.environ.get("ROSETTA_PROJECT")
    if env:
        return Path(env).expanduser().resolve(), None
    root = Path.cwd().resolve()
    return root, resolved_docs_dir(warn=warn)


def docs_dir(root: Path, override: Path | None) -> Path:
    return override if override is not None else root / ".rosetta" / "docs"


def base_of(docs: Path) -> Path:
    """The repo/home that owns a docs directory. Handles the 'device' layout (no nested .rosetta)."""
    return docs.parent.parent if docs.parent.name == ".rosetta" else docs.parent


def warn_if_orphaned(docs: Path) -> None:
    """The embed pipeline finds fact-graphs by walking the repo that owns agents/. A KB written
    outside that repo is invisible to it — say so loudly instead of failing silently later."""
    if not (base_of(docs) / "agents").is_dir():
        print(f"rosetta-roots: WARN: {docs} has no sibling agents/ directory; an embed pipeline "
              f"rooted elsewhere will not see this KB", file=sys.stderr)
