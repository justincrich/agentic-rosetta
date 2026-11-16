#!/usr/bin/env python3
"""test_resolve.py — two contracts, both pinned against real temp repos.

PART 1 — which project a run is about (``resolve.py``).
``discover_repo_root`` decided which tree gets written, and it existed twice. Every tier
is pinned here, including the fixture rule and the "walk up to .git" behaviour that makes
``registry.py scan`` from a subdirectory index the repo rather than the subdirectory.

PART 2 — where a repo's artifacts live (``assets/rosetta_config.py``).
Rosetta now requires a version 2 config at ``<repo>/.rosetta/config.json``. A repo without
one is refused, not defaulted; a path that leaves the repo is refused by key name. These
checks drive the real resolver and the real writer as subprocesses against real temp git
repos, because the contract is about exit codes and stderr, not about a helper's return
value.

PART 3 — the code no longer builds a layout by hand (``AC-14``).
The requirement is a SEARCH, so the assertion is a search: this checkout is grepped for a
hand-built ``.rosetta/docs`` and for the retired orphan-warning helper, and every match must
sit inside a comment, a docstring or Markdown prose. A restored helper or one code line that
spells the retired layout makes this exit non-zero — the check has teeth, not just a clean tree.

Run: python3 scripts/rosetta/test_resolve.py
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import resolve as RV  # noqa: E402
import registry as R  # noqa: E402  (transition cross-check)

sys.path.insert(0, str(HERE.parent / "kb-delivery"))
import generate_excerpt as G  # noqa: E402  (transition cross-check)

REPO = HERE.parent.parent
RESOLVER = REPO / "assets" / "rosetta_config.py"
WRITE_GRAPH = HERE / "write-graph.py"

STANDARD_PATHS = {
    "docs": ".rosetta/docs",
    "research": ".rosetta/research",
    "probes": ".rosetta/probes",
    "agents": "agents",
}

CLASSES = ("docs", "research", "probes", "agents")

# The registry-pruned names AC-4 requires `paths.docs` to refuse. Mirrored from the contract
# on purpose: the assertion is that the resolver refuses all four, not that it agrees with a
# set it also owns.
PRUNED_DOC_NAMES = ("tmp", "research", "sandbox", ".history")


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


# ─────────────────────────── part 2 helpers ───────────────────────────

def run_cli(cmd: list[str], cwd: Path, env_extra: dict | None = None):
    """Run a real CLI with the caller's Rosetta env stripped, then the intended values set."""
    env = dict(os.environ)
    for key in ("ROSETTA_PROJECT", "ROSETTA_CONFIG", "ROSETTA_PLUGIN"):
        env.pop(key, None)
    env.update({k: str(v) for k, v in (env_extra or {}).items()})
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=env)


def new_repo(parent: Path, name: str) -> Path:
    """A real `git init` repo — the fixture the contract names."""
    repo = parent / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    return repo


def write_config(repo: Path, config: dict) -> Path:
    cfg = repo / ".rosetta" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    import json
    cfg.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return cfg


def standard_config(**overrides) -> dict:
    paths = dict(STANDARD_PATHS)
    paths.update(overrides)
    return {"version": 2, "paths": paths}


def tree(root: Path) -> list[tuple[str, str]]:
    """Every path under `root` with file bytes hashed — the byte-identical comparison."""
    out: list[tuple[str, str]] = []
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_symlink():
            out.append((rel, "link:" + os.readlink(p)))
        elif p.is_dir():
            out.append((rel, "dir"))
        else:
            out.append((rel, hashlib.sha256(p.read_bytes()).hexdigest()))
    return out


def check_config_contract(tmp: Path, failures: list[str]) -> None:
    """AC-1, AC-3, AC-4, AC-5, AC-6, AC-7 from the CONFIG-01 contract."""
    import json

    def must(name: str, cond: bool, detail: str = "") -> None:
        if not cond:
            failures.append(f"{name}: {detail}" if detail else name)

    def same(name: str, got, want) -> None:
        if got != want:
            failures.append(f"{name}\n     got:  {got!r}\n     want: {want!r}")

    def refusal(name: str, proc, config_path: Path, key: str | None = None) -> None:
        must(f"{name}-nonzero", proc.returncode != 0, f"rc={proc.returncode}")
        must(f"{name}-names-config", str(config_path) in proc.stderr, proc.stderr)
        if key is not None:
            must(f"{name}-names-key", key in proc.stderr, proc.stderr)

    # ── AC-1: no config anywhere is a refusal, and it writes nothing ──────────
    bare = new_repo(tmp, "bare-repo")
    bare_home = tmp / "bare-home"
    bare_home.mkdir()
    cfg = bare / ".rosetta" / "config.json"
    must("ac1-fixture-has-no-config", not cfg.exists(), str(cfg))
    before = tree(bare)
    commands = {
        "ac1-show": [sys.executable, str(RESOLVER), "show", "--format", "plain"],
        "ac1-path": [sys.executable, str(RESOLVER), "path", "--class", "docs", "--slug", "x"],
        "ac1-write-graph": [sys.executable, str(WRITE_GRAPH), "--create", "x"],
    }
    for name, cmd in commands.items():
        proc = run_cli(cmd, bare, {"ROSETTA_HOME": bare_home})
        refusal(name, proc, cfg)
        must(f"{name}-suggested-json", '"version": 2' in proc.stderr, proc.stderr)
        must(f"{name}-no-builtin-tier", "[builtin-default" not in proc.stderr, proc.stderr)
        must(f"{name}-not-empty-error", proc.stderr.strip() != "", proc.stderr)
    after = tree(bare)
    diff = ([f"- {a}" for a in before if a not in after]
            + [f"+ {a}" for a in after if a not in before])
    must("ac1-tree-byte-identical", diff == [], "\n".join(diff))

    # ── AC-3: the configured layout decides the path ──────────────────────────
    # The standard config first. On its own this case cannot distinguish "reads the config"
    # from "hardcodes .rosetta/docs", so the non-standard case below is the negative control
    # that gives the requirement its teeth.
    std = new_repo(tmp, "standard-repo")
    std_home = tmp / "standard-home"
    std_home.mkdir()
    write_config(std, standard_config())
    proc = run_cli([sys.executable, str(RESOLVER), "path", "--class", "docs",
                    "--slug", "x", "--file", "fact-graph.json"],
                   std, {"ROSETTA_HOME": std_home})
    expected = str(std / ".rosetta" / "docs" / "x" / "fact-graph.json")
    same("ac3-exit0", proc.returncode, 0)
    same("ac3-exact-path", proc.stdout.strip(), expected)

    # A config that names a NON-standard layout must move every class. A resolver that
    # hardcodes .rosetta/docs (or anyone's built-in default) fails here and only here.
    custom = new_repo(tmp, "custom-repo")
    custom_home = tmp / "custom-home"
    custom_home.mkdir()
    write_config(custom, {"version": 2, "paths": {
        "docs": "kb/graphs", "research": "kb/research",
        "probes": "kb/probes", "agents": "prompts"}})
    proc = run_cli([sys.executable, str(RESOLVER), "path", "--class", "docs",
                    "--slug", "x", "--file", "fact-graph.json"],
                   custom, {"ROSETTA_HOME": custom_home})
    same("ac3-custom-exit0", proc.returncode, 0)
    same("ac3-custom-path", proc.stdout.strip(),
         str(custom / "kb" / "graphs" / "x" / "fact-graph.json"))
    must("ac3-custom-not-standard", str(custom / ".rosetta" / "docs") not in proc.stdout,
         proc.stdout)
    proc = run_cli([sys.executable, str(RESOLVER), "path", "--class", "agents", "--slug", "x"],
                   custom, {"ROSETTA_HOME": custom_home})
    same("ac3-custom-agents-path", proc.stdout.strip(), str(custom / "prompts" / "x"))
    # The report must name the config key that decided it, not a built-in tier.
    proc = run_cli([sys.executable, str(RESOLVER), "show", "--format", "plain"], custom,
                   {"ROSETTA_HOME": custom_home})
    must("ac3-custom-show-names-config",
         "[config: paths.docs = kb/graphs]" in proc.stdout, proc.stdout)

    # ── AC-4: the full bad-config matrix, one real temp repo per case ────────
    # The requirement is a matrix, so the assertion is a matrix: every one of the four keys
    # missing, every bad value kind applied to every key, and every registry-pruned docs
    # name. A resolver that requires only `agents`, validates only `docs`, or prunes only
    # `tmp` refuses nothing here and fails this section - which is the whole point of it.
    bad: list[tuple[str, dict, str]] = []

    # Missing key: refused BY NAME, for each of the four keys.
    for key in CLASSES:
        cfg_missing = standard_config()
        del cfg_missing["paths"][key]
        bad.append((f"ac4-missing-{key}", cfg_missing, f"paths.{key}"))

    # Bad value kind x key: the value checks must run for every key, not just docs.
    bad_value_kinds = {
        "absolute": "/abs/graphs",
        "tilde": "~/graphs",
        "device": "device",
        "dotdot": "../graphs",
    }
    for key in CLASSES:
        for kind, value in bad_value_kinds.items():
            bad.append((f"ac4-{kind}-{key}", standard_config(**{key: value}),
                        f"paths.{key}"))

    # Registry-pruned docs name: one case per name, not one representative.
    for pruned in PRUNED_DOC_NAMES:
        bad.append((f"ac4-pruned-{pruned.lstrip('.')}",
                    standard_config(docs=f"{pruned}/graphs"), "paths.docs"))

    for name, config, key in bad:
        repo = new_repo(tmp, name)
        home = tmp / f"{name}-home"
        home.mkdir()
        bad_cfg = write_config(repo, config)
        proc = run_cli([sys.executable, str(RESOLVER), "show"], repo,
                       {"ROSETTA_HOME": home})
        refusal(name, proc, bad_cfg, key=key)
        must(f"{name}-no-stdout", proc.stdout.strip() == "", proc.stdout)
        must(f"{name}-no-outside-path", str(repo) not in proc.stdout, proc.stdout)

    # The sixth bad value kind needs a real symlink inside the repo: the path only leaves
    # the repo once it is resolved, so no literal config value can express it.
    for key in CLASSES:
        name = f"ac4-symlink-{key}"
        repo = new_repo(tmp, name)
        home = tmp / f"{name}-home"
        home.mkdir()
        outside = tmp / f"{name}-outside"
        outside.mkdir()
        (repo / "escape").symlink_to(outside)
        link_cfg = write_config(repo, standard_config(**{key: "escape/graphs"}))
        proc = run_cli([sys.executable, str(RESOLVER), "show"], repo,
                       {"ROSETTA_HOME": home})
        refusal(name, proc, link_cfg, key=f"paths.{key}")
        must(f"{name}-no-stdout", proc.stdout.strip() == "", proc.stdout)
        must(f"{name}-no-outside-path", str(repo) not in proc.stdout, proc.stdout)

    # 4 missing keys + (5 bad value kinds x 4 keys) + 4 pruned names = the 28 the contract
    # names. Pinned so a future edit cannot quietly drop a dimension of the matrix.
    same("ac4-matrix-case-count", len(bad) + len(CLASSES), 28)

    # ── AC-5: a device file never moves a root; it warns once ────────────────
    dev = new_repo(tmp, "device-repo")
    dev_home = tmp / "device-home"
    dev_home.mkdir()
    write_config(dev, standard_config())
    (dev_home / "config.json").write_text(
        json.dumps({"version": 1, "roots": {"docs": "/elsewhere"}}), encoding="utf-8")
    proc = run_cli([sys.executable, str(RESOLVER), "show", "--format", "plain"], dev,
                   {"ROSETTA_HOME": dev_home})
    same("ac5-exit0", proc.returncode, 0)
    must("ac5-docs-dir", str(dev / ".rosetta" / "docs") in proc.stdout, proc.stdout)
    must("ac5-device-root-not-used", "/elsewhere" not in proc.stdout, proc.stdout)
    warn_lines = [ln for ln in proc.stderr.splitlines() if ln.strip()]
    same("ac5-one-warning", len(warn_lines), 1)
    must("ac5-warning-says-ignored",
         "device roots are ignored" in " ".join(warn_lines), proc.stderr)

    # ── AC-6: a version 1 config is refused with the equivalent version 2 ────
    v1 = new_repo(tmp, "v1-repo")
    v1_home = tmp / "v1-home"
    v1_home.mkdir()
    v1_cfg = write_config(v1, {"version": 1, "roots": {"default": "project"}})
    proc = run_cli([sys.executable, str(RESOLVER), "show"], v1, {"ROSETTA_HOME": v1_home})
    refusal("ac6-v1", proc, v1_cfg)
    must("ac6-suggests-version2", '"version": 2' in proc.stderr, proc.stderr)
    must("ac6-suggests-paths", '"paths"' in proc.stderr, proc.stderr)
    must("ac6-no-builtin-tier", "[builtin-default" not in proc.stderr, proc.stderr)

    # ── AC-7: `plugin` is exempt from the project config ─────────────────────
    bare2 = new_repo(tmp, "bare-plugin-repo")
    bare2_home = tmp / "bare-plugin-home"
    bare2_home.mkdir()
    proc = run_cli([sys.executable, str(RESOLVER), "plugin"], bare2,
                   {"ROSETTA_HOME": bare2_home})
    same("ac7-plugin-exit0", proc.returncode, 0)
    same("ac7-plugin-path", proc.stdout.strip(), str(REPO.resolve()))
    must("ac7-no-missing-config-refusal", "no project config" not in proc.stderr, proc.stderr)


# ─────────────────────────── part 3: AC-14's search ───────────────────────────

#: The criterion's own search: a hand-built ``.rosetta/docs`` in the writer surface. It is
#: spelled here once, and the assertion is that every match is prose (a comment, a docstring or
#: a Markdown file) — never a line that builds a path.
RETIRED_LAYOUT_RE = re.compile(r"\.rosetta.{0,6}docs")

#: The helper AC-14 removes. It is assembled so this file is not itself a carrier of the name
#: the criterion's second search forbids.
RETIRED_HELPER = "warn_if" + "_orphaned"


def _git_grep(repo: Path, pattern: str, paths: list[str],
              excludes: list[str]) -> subprocess.CompletedProcess:
    """Run one real ``git grep`` over the checkout, exactly as the criterion spells it.

    ``-E`` is required, not cosmetic: the criterion's pattern is ``\\.rosetta.{0,6}docs``, and
    in git's basic regex ``{0,6}`` is literal text — the search would then find nothing and the
    assertion below would pass by being blind.
    """
    return subprocess.run(
        ["git", "-C", str(repo), "grep", "-nE", pattern, "--", *paths, *excludes],
        capture_output=True, text=True,
    )


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for line in text.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    return starts


def _prose_spans(text: str) -> list[tuple[int, int]]:
    """Absolute ``[start, end)`` of every COMMENT and STRING token: the prose a match may sit in.

    A token is the unit, not the line: a line such as ``LEGACY = root / ".rosetta" / "docs"``
    contains a string literal, and a line-level check would wrongly accept the hand-built path
    it spells. The match has to be CONTAINED in one token, so that line is rejected.
    """
    starts = _line_starts(text)
    spans: list[tuple[int, int]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                spans.append((starts[tok.start[0] - 1] + tok.start[1],
                              starts[tok.end[0] - 1] + tok.end[1]))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []
    return spans


def _match_is_prose(path: Path, line_no: int, line_text: str) -> bool:
    """Is every occurrence of the retired layout on this line inside prose?"""
    matches = list(RETIRED_LAYOUT_RE.finditer(line_text))
    if not matches:
        return True
    if path.suffix != ".py":
        # Markdown is prose; a shell or TOML line is prose only when it is a comment.
        return path.suffix in (".md", ".markdown") or line_text.strip().startswith("#")
    text = path.read_text(encoding="utf-8")
    spans = _prose_spans(text)
    base = _line_starts(text)[line_no - 1]
    for match in matches:
        start, end = base + match.start(), base + match.end()
        if not any(s <= start and end <= e for s, e in spans):
            return False
    return True


def check_layout_hygiene(failures: list[str], tmp: Path) -> None:
    """AC-14: the search finds only prose, the retired helper is gone, and doctor is silent.

    Each claim is checked twice over: against the checkout as it stands (the criterion's own
    search, so a regression is named by file:line) and against a copy of the tree that has the
    forbidden shape planted in it (so the check cannot pass by being blind).
    """
    def must(name: str, cond: bool, detail: str = "") -> None:
        if not cond:
            failures.append(f"{name}: {detail}" if detail else name)

    inside = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--is-inside-work-tree"],
                            capture_output=True, text=True)
    must("ac14-git-checkout", inside.returncode == 0 and inside.stdout.strip() == "true",
         f"{REPO} is not a git checkout, so the criterion's search cannot run")
    if inside.returncode != 0:
        return

    paths = ["scripts/rosetta", "scripts/kb-delivery"]
    excludes = [":!*test*"]

    # ── the first search: only comments, docstrings and Markdown prose ──────────
    proc = _git_grep(REPO, r"\.rosetta.{0,6}docs", paths, excludes)
    must("ac14-search-exit", proc.returncode in (0, 1),
         f"git grep exited {proc.returncode}: {proc.stderr}")
    code_lines: list[str] = []
    for raw in proc.stdout.splitlines():
        if not raw.strip():
            continue
        # `git grep -n` prints `<file>:<line>:<text>`; the file path itself may contain a colon,
        # so split from the left once for the path and once for the line number.
        file_part, _, rest = raw.partition(":")
        line_part, _, line_text = rest.partition(":")
        path = REPO / file_part
        if not path.is_file() or not line_part.isdigit():
            code_lines.append(f"{raw}  (unreadable match location)")
            continue
        if not _match_is_prose(path, int(line_part), line_text):
            code_lines.append(raw)
    must("ac14-only-prose", code_lines == [],
         "a code line builds the retired layout:\n    " + "\n    ".join(code_lines))

    # The check is not blind: a hand-built path planted in the same surface is caught.
    planted = tmp / "ac14-mutant"
    (planted / "scripts").mkdir(parents=True)
    mutant = planted / "scripts" / "layout_mutant.py"
    mutant.write_text(
        '"""A module whose prose names .rosetta/docs, and whose code builds it."""\n'
        'LEGACY_DOCS = repo_root / ".rosetta" / "docs"\n'
        "# a comment may name .rosetta/docs freely\n",
        encoding="utf-8",
    )
    mutant_code = [raw for raw in mutant.read_text(encoding="utf-8").splitlines()
                   if RETIRED_LAYOUT_RE.search(raw)
                   and not _match_is_prose(mutant, mutant.read_text(encoding="utf-8")
                                           .splitlines().index(raw) + 1, raw)]
    must("ac14-mutant-caught", len(mutant_code) == 1,
         f"the prose test accepted a hand-built path: {mutant_code}")
    mutant_docstring_only = mutant.with_name("docstring_mutant.py")
    mutant_docstring_only.write_text(
        '"""Only prose: .rosetta/docs is named inside the module docstring."""\nVALUE = 1\n',
        encoding="utf-8",
    )
    doc_lines = (mutant_docstring_only.read_text(encoding="utf-8").splitlines())
    must("ac14-docstring-accepted",
         all(_match_is_prose(mutant_docstring_only, i + 1, line)
             for i, line in enumerate(doc_lines) if RETIRED_LAYOUT_RE.search(line)),
         "the prose test rejected a docstring that only names the path")

    # ── the second search: the retired helper has no home ──────────────────────
    helper = _git_grep(REPO, RETIRED_HELPER, ["scripts"], [])
    must("ac14-no-retired-helper", helper.returncode == 1 and helper.stdout.strip() == "",
         f"'{RETIRED_HELPER}' still exists:\n{helper.stdout}")

    # ── doctor: no agents-sibling note, and the declared paths are what it prints ──
    doc_repo = new_repo(tmp, "ac14-doctor-repo")
    doc_home = tmp / "ac14-doctor-home"
    doc_home.mkdir()
    write_config(doc_repo, standard_config())
    proc = run_cli([sys.executable, str(RESOLVER), "doctor", "--project", str(doc_repo),
                    "--scan-root", str(tmp / "ac14-no-such-scan-root")],
                   doc_repo, {"ROSETTA_HOME": doc_home})
    must("ac14-doctor-exit0", proc.returncode == 0, proc.stderr)
    must("ac14-doctor-no-sibling-note", "sibling" not in proc.stdout.lower(), proc.stdout)
    for cls in CLASSES:
        must(f"ac14-doctor-lists-{cls}", f"paths.{cls}" in proc.stdout, proc.stdout)


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

        # ── the required version 2 project config (CONFIG-01) ──────
        check_config_contract(tmp, failures)

        # ── no reader spells the retired layout by hand (AC-14) ────
        check_layout_hygiene(failures, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print(f"FAILED — {len(failures)} case(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ok — resolve.py pins all 5 tiers and agrees with both old call sites; "
          "the required version 2 project config is refused, validated and honoured")
    return 0


if __name__ == "__main__":
    sys.exit(main())
