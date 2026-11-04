#!/usr/bin/env python3
"""test_resolve.py — pins which project a run is about.

``discover_repo_root`` decided which tree gets written, and it existed twice. Every tier
is pinned here, including the fixture rule and the "walk up to .git" behaviour that makes
``registry.py scan`` from a subdirectory index the repo rather than the subdirectory.

Run: python3 scripts/rosetta/test_resolve.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import resolve as RV  # noqa: E402
import registry as R  # noqa: E402  (transition cross-check)

sys.path.insert(0, str(HERE.parent / "kb-delivery"))
import generate_excerpt as G  # noqa: E402  (transition cross-check)


@contextmanager
def _env(project: str | None):
    """Set or clear $ROSETTA_PROJECT for the duration of the block."""
    saved = os.environ.get("ROSETTA_PROJECT")
    try:
        if project is None:
            os.environ.pop("ROSETTA_PROJECT", None)
        else:
            os.environ["ROSETTA_PROJECT"] = project
        yield
    finally:
        if saved is None:
            os.environ.pop("ROSETTA_PROJECT", None)
        else:
            os.environ["ROSETTA_PROJECT"] = saved


@contextmanager
def _cwd(path: Path):
    saved = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(saved)


def main() -> int:
    failures: list[str] = []

    def check(name: str, got, want) -> None:
        if got != want:
            failures.append(f"{name}\n     got:  {got!r}\n     want: {want!r}")

    tmp = Path(tempfile.mkdtemp(prefix="resolve-test-"))
    try:
        git_repo = tmp / "git-repo"
        (git_repo / ".git").mkdir(parents=True)
        rosetta_repo = tmp / "rosetta-repo"
        (rosetta_repo / ".rosetta").mkdir(parents=True)
        plain = tmp / "plain"
        plain.mkdir()
        nested = git_repo / "a" / "b"
        nested.mkdir(parents=True)

        # ── tier 1: explicit wins over everything ──────────────────
        with _env(str(rosetta_repo)):
            with _cwd(git_repo):
                check("explicit beats env and cwd", RV.project_root(plain), plain.resolve())
                check("env used when no explicit", RV.project_root(), rosetta_repo.resolve())

        # ── tier 2: $ROSETTA_PROJECT ───────────────────────────────
        with _env(str(rosetta_repo)):
            with _cwd(plain):
                check("env wins over plain cwd", RV.project_root(), rosetta_repo.resolve())
            with _cwd(git_repo):
                check("env wins over .git ancestor", RV.project_root(), rosetta_repo.resolve())

        # ── tier 4: walk up to the nearest marker ──────────────────
        with _env(None):
            with _cwd(nested):
                check("walks up to .git", RV.project_root(), git_repo.resolve())
                # Both old call sites must give the same answer.
                check("registry agrees on .git walk", R.discover_repo_root(), git_repo.resolve())
                check("excerpt agrees on .git walk", G.discover_repo_root(), git_repo.resolve())

            with _cwd(rosetta_repo):
                check(".rosetta marks a root", RV.project_root(), rosetta_repo.resolve())

            # ── tier 5: no marker anywhere → cwd itself ────────────
            # This harness points TMPDIR inside a tree that has .rosetta in its ancestry,
            # so the fallback branch needs a directory with no marker above it.
            bare = Path(tempfile.mkdtemp(dir="/tmp", prefix="resolve-bare-"))
            try:
                with _cwd(bare):
                    check("falls back to cwd when no marker exists", RV.project_root(), bare.resolve())
            finally:
                shutil.rmtree(bare, ignore_errors=True)

        # ── tier 3: a plugin checkout that already has agents/ ─────
        # The real checkout has no agents/, so the branch is only reachable with a
        # fixture. Rebinding the module constant is the smallest way to reach it.
        fake_plugin = tmp / "plugin-with-agents"
        (fake_plugin / "agents" / "rust-implementer").mkdir(parents=True)
        original_root = RV.PLUGIN_ROOT
        try:
            RV.PLUGIN_ROOT = fake_plugin
            with _env(None):
                with _cwd(git_repo):
                    check(
                        "agents/ in the plugin root beats the cwd walk",
                        RV.project_root(),
                        fake_plugin,
                    )
                # An explicit path still outranks the fixture rule.
                check(
                    "explicit beats the fixture rule",
                    RV.project_root(plain),
                    plain.resolve(),
                )
        finally:
            RV.PLUGIN_ROOT = original_root
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print(f"FAILED — {len(failures)} case(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ok — resolve.py pins all 5 tiers and agrees with both old call sites")
    return 0


if __name__ == "__main__":
    sys.exit(main())
