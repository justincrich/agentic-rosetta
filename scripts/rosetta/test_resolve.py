#!/usr/bin/env python3
"""test_resolve.py — pins which project a run is about, and where this device stores artifacts.

``discover_repo_root`` decided which tree gets written, and it existed twice. Every tier
is pinned here, including the fixture rule and the "walk up to .git" behaviour that makes
``registry.py scan`` from a subdirectory index the repo rather than the subdirectory.

The second case pins the **device** half: ``~/.rosetta/config.json`` points docs, research and
probes at the consumer repository, so a run no longer needs a brain checkout. It asserts the
real device file, the real backup of the previous one, the real resolver CLI, and CONFIG.md.
It is machine state, not repo state, so it runs only when named — the plain invocation stays
portable.

Run: python3 scripts/rosetta/test_resolve.py            # the project-root tiers (portable)
     python3 scripts/rosetta/test_resolve.py -k ac31    # this device's storage config
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))

import resolve as RV  # noqa: E402
import registry as R  # noqa: E402  (transition cross-check)

sys.path.insert(0, str(HERE.parent / "kb-delivery"))
import generate_excerpt as G  # noqa: E402  (transition cross-check)

#: The consumer repository that owns agents/ and every KB (CONFIG.md, AGENTS.md).
CONSUMER_REPO = "~/Projects/agent-content"


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


def _tier_failures() -> list[str]:
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

    return failures


def _resolver():
    """Load the canonical resolver from assets/ — the same file the device copy is checked against."""
    path = REPO / "assets" / "rosetta_config.py"
    spec = importlib.util.spec_from_file_location("rosetta_config_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path


def _ac31_failures() -> list[str]:
    """This device's storage config: docs, research and probes are the consumer repository."""
    failures: list[str] = []

    def check(name: str, got, want) -> None:
        if got != want:
            failures.append(f"{name}\n     got:  {got!r}\n     want: {want!r}")

    resolver, cli = _resolver()
    home = resolver.rosetta_home()
    cfg_path = home / "config.json"
    print(f"  device config: {cfg_path}  (resolver.rosetta_home())")
    if not cfg_path.is_file():
        return [f"device config missing: {cfg_path}"]
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    roots = cfg.get("roots") or {}

    consumer = Path(CONSUMER_REPO).expanduser().resolve()
    for cls in resolver.CLASSES:
        check(f"device roots.{cls}", roots.get(cls), CONSUMER_REPO)
    check("the consumer repo the config names exists", consumer.is_dir(), True)
    check("the consumer repo owns agents/", (consumer / "agents").is_dir(), True)
    for cls in resolver.CLASSES:
        check(f"the consumer repo holds .rosetta/{cls}/", (consumer / ".rosetta" / cls).is_dir(), True)

    backups = sorted(home.glob("config.json.bak-*"))
    check("a backup of the previous device config exists", bool(backups), True)
    if backups:
        newest = max(backups, key=lambda p: p.stat().st_mtime)
        previous = json.loads(newest.read_text(encoding="utf-8"))
        old_roots = previous.get("roots") or {}
        print(f"  backup: {newest.name} ({newest.stat().st_size} bytes) roots={old_roots}")
        check("the backup is a complete config", sorted(old_roots), sorted(roots))
        moved = [cls for cls in resolver.CLASSES if old_roots.get(cls) != roots.get(cls)]
        check("the backup holds the pre-retirement roots", bool(moved), True)

    # The real resolver CLI, on the real home, from a project with no config of its own.
    proj = Path(tempfile.mkdtemp(prefix="ac31-project-"))
    try:
        (proj / ".git").mkdir()  # pin the project root here, not in an ancestor
        out = subprocess.run(
            [sys.executable, str(cli), "show", "--format", "plain", "--project", str(proj)],
            capture_output=True, text=True,
        )
    finally:
        shutil.rmtree(proj, ignore_errors=True)
    check("resolver show exits 0", out.returncode, 0)
    print("  resolver show --format plain:")
    for line in out.stdout.splitlines():
        print(f"  | {line}")
    for cls in resolver.CLASSES:
        want = str(consumer / ".rosetta" / cls)
        check(f"resolver prints {cls} in the consumer repo", want in out.stdout, True)
        check(f"resolver did not fall back for {cls}", f"root not writable" in out.stderr, False)

    config_doc = (REPO / "CONFIG.md").read_text(encoding="utf-8")
    check("CONFIG.md names the consumer repo", "agent-content" in config_doc, True)

    return failures


CASES = {
    "resolve-tiers": _tier_failures,
    "ac31": _ac31_failures,
}

#: Machine-state cases run only when named, so the plain invocation stays portable.
DEVICE_CASES = ("ac31",)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-k", "--case", action="append", default=None,
                    choices=sorted(CASES),
                    help="run one case (repeatable); default: the portable project-root tiers")
    args = ap.parse_args(argv)
    selected = args.case or [name for name in CASES if name not in DEVICE_CASES]

    failures: list[str] = []
    for name in selected:
        print(f"── {name} ──")
        case_failures = CASES[name]()
        if case_failures:
            print(f"FAILED — {name}: {len(case_failures)} case(s):")
            for f in case_failures:
                print(f"  - {f}")
            failures.extend(case_failures)

    if failures:
        print(f"\nFAILED — {len(failures)} case(s) across {', '.join(selected)}")
        return 1
    if selected == ["resolve-tiers"]:
        print("ok — resolve.py pins all 5 tiers and agrees with both old call sites")
    else:
        print(f"ok — {', '.join(selected)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
