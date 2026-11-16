#!/usr/bin/env python3
"""roots.py — one decision, made the same way by every KB writer and reader.

WHY THIS EXISTS
  write-graph.py and provenance.py used to default their root to `Path.cwd()`. That made the
  destination of a knowledge base a property of WHERE THE SHELL HAPPENED TO BE STANDING rather
  than of the config the user set. Running /rosetta-research from an unrelated repo therefore
  wrote (or looked for) the KB in that repo, silently, with exit 0 — and no amount of editing
  ~/.rosetta/config.json changed it, because neither script ever read that file.

  rosetta_config.py already owns "where do artifacts go". This module is the bridge: it asks the
  resolver for a class and hands every script the same answer.

THE CONTRACT
  Rosetta runs only in a repo with its own version 2 config at <repo>/.rosetta/config.json.
  This bridge therefore FAILS CLOSED: when the resolver refuses (no config, invalid path), its
  message is printed verbatim and the calling script exits non-zero. There is no silent fallback
  to `<cwd>/.rosetta/docs` — that fallback is exactly the behaviour the config requirement
  replaced, and it hid the missing config instead of reporting it.

  `--root` / `$ROSETTA_PROJECT` still pin WHICH repo a run is about. The layout inside that repo
  always comes from its config, never from where the shell stands.
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


def resolved_dir(cls: str, project: Path, warn: bool = True) -> Path:
    """Ask rosetta_config.py where `cls` lives in `project`. Refuses rather than guesses.

    Raises SystemExit with the resolver's exit code (and its message on stderr) when the repo
    has no valid config. Callers must not catch this to fall back: a missing config is the
    user's answer to "where should this go", and inventing one writes a KB nobody can find.
    """
    r = resolver_path()
    if r is None:
        print("rosetta-roots: ERROR: rosetta_config.py not found. Re-run install.sh from the "
              "plugin checkout, or set $ROSETTA_CONFIG.", file=sys.stderr)
        raise SystemExit(2)
    try:
        out = subprocess.run(
            [sys.executable, str(r), "root", "--class", cls, "--project", str(project)],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"rosetta-roots: ERROR: resolver failed ({exc})", file=sys.stderr)
        raise SystemExit(2) from None
    lines = [ln for ln in (out.stdout or "").strip().splitlines() if ln.strip()]
    if out.returncode != 0 or not lines:
        detail = (out.stderr or "").strip() or f"rosetta_config.py root exited {out.returncode}"
        print(detail, file=sys.stderr)
        raise SystemExit(out.returncode if out.returncode > 0 else 2)
    # The resolver's warnings are for the person running the command (a device file that sets
    # roots, for example). Swallowing them would make the warning invisible through every
    # script that goes through this bridge.
    if (out.stderr or "").strip():
        print(out.stderr.strip(), file=sys.stderr)
    if warn and len(lines) > 1:
        print(f"rosetta-roots: WARN: resolver printed {len(lines)} lines; using the last",
              file=sys.stderr)
    return Path(lines[-1]).expanduser().resolve()


def resolved_docs_dir(project: Path | None = None, warn: bool = True) -> Path:
    """The docs directory for `project` (default: cwd). See resolved_dir for the refusal."""
    return resolved_dir("docs", project or _cwd_logical(), warn=warn)


def _cwd_logical() -> Path:
    """cwd in the spelling the caller used ($PWD) — os.getcwd() resolves symlinks.

    The refusal message must name the path the operator typed, not its /private twin.
    """
    pwd = os.environ.get("PWD")
    if pwd:
        try:
            if os.path.samefile(pwd, "."):
                return Path(pwd)
        except OSError:
            pass
    return Path.cwd()


def choose(explicit_root: str | Path | None, warn: bool = True) -> tuple[Path, Path | None]:
    """Return (repo_root, docs_override).

    repo_root is what registry/agent lookups use — always a repo, never the KB store.
    docs_override is the docs DIRECTORY from the repo's config. It is never None for a valid
    repo: the config decides, so there is no "<repo_root>/.rosetta/docs" guess left here.
    """
    if explicit_root:
        repo = Path(explicit_root).expanduser()
    elif os.environ.get("ROSETTA_PROJECT"):
        repo = Path(os.environ["ROSETTA_PROJECT"]).expanduser()
    else:
        repo = _cwd_logical()
    # Absolute but symlink-preserving, so a refusal names the path the caller passed in.
    repo = Path(os.path.abspath(repo))
    return repo, resolved_docs_dir(repo, warn=warn)


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
