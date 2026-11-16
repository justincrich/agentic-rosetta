#!/usr/bin/env python3
"""rosetta_config.py — where a repo's Rosetta artifacts live. Required, validated, in code.

WHY THIS EXISTS
  Where an artifact goes must ALWAYS be decided the same way, so the decision lives in CODE —
  this file — not in skill prose that an LLM re-interprets every run. The skills used to
  hardcode `.rosetta/research/<slug>.md` (and docs/, probes/) relative to whatever project the
  session happened to be running in, so catalogs stranded themselves across repos. The first
  fix put a machine-wide device file in front of that: every repo on the machine resolved to
  one consumer's layout, and no repo ever declared its own. Rosetta is distributed, so the
  repo being worked on is the only thing that can answer the question.

THE CONTRACT
  Rosetta runs only in a repo that declares its own layout, in <repo>/.rosetta/config.json:

      {
        "version": 2,
        "paths": {
          "docs": ".rosetta/docs",
          "research": ".rosetta/research",
          "probes": ".rosetta/probes",
          "agents": "agents"
        },
        "citation_aliases": {"notes/": ".rosetta/docs/"}
      }

  All four path keys are required. Every value is a REPO-RELATIVE path: no absolute path, no
  `~` expansion, no `device`, no `..`, nothing that leaves the repo through a symlink.
  `paths.docs` may not pass through a name the registry prunes (tmp, research, sandbox,
  .history) — a knowledge base there is invisible to `registry.py`.

  `citation_aliases` is optional. Each entry maps a citation PREFIX as it is written inside a
  graph (``notes/widgets/vm.md``) to the repo-relative directory it really lives in
  (``.rosetta/docs``). It is the only way a local citation resolves: the prefix table is data,
  so a project that spells its citations differently declares it instead of patching code. The
  value obeys the same rules as a path — absolute, `~`, `..` and a symlink escape are refused,
  naming ``citation_aliases.<prefix>``.

  There is NO built-in default and NO device root. A missing or invalid config is a refusal
  (exit 2) that names the file and prints the JSON to create, never a silent fallback. A
  device file at $ROSETTA_HOME/config.json is read only to warn, once, that device roots are
  ignored.

  `--<class>-root` / `--root` are the one explicit override, and therefore the one way to
  point a single invocation outside the repo.

EXEMPT
  `plugin`, `init` and `--selftest` need no project config: they do not name an artifact.

SELF-HEALING IS NOT DONE HERE
  The resolver never creates or edits a config, and never creates a directory. `show`, `path`
  and `root` leave the tree byte-identical; `--ensure` is the one verb that makes a directory.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

CLASSES = ("docs", "research", "probes", "agents")
CONFIG_RELPATH = Path(".rosetta") / "config.json"
SCHEMA_VERSION = 2
STANDARD_PATHS = {
    "docs": ".rosetta/docs",
    "research": ".rosetta/research",
    "probes": ".rosetta/probes",
    "agents": "agents",
}

# Mirrors the docs-relevant names in registry.EXCLUDE_DIR_NAMES. A knowledge base under one of
# them is pruned out of the registry walk, so accepting it would write a graph nobody indexes.
PRUNED_DOC_SEGMENTS = frozenset({"tmp", "research", "sandbox", ".history"})


# ───────────────────────────── helpers ─────────────────────────────

class ConfigError(Exception):
    """A refusal with a repair. Rosetta never guesses where artifacts go."""


def warn(msg: str) -> None:
    print(f"[rosetta-config] WARN: {msg}", file=sys.stderr)


def suggested_config() -> str:
    return json.dumps(
        {"version": SCHEMA_VERSION, "paths": dict(STANDARD_PATHS)}, indent=2
    )


def error_text(exc: ConfigError) -> str:
    """The refusal as the user reads it: one prefix, the JSON block left copy-pasteable."""
    return f"[rosetta-config] {exc}"


def rosetta_home() -> Path:
    env = os.environ.get("ROSETTA_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".rosetta").resolve()


ENGINE_REL = Path("engine") / "src" / "research.ts"


def _is_plugin_root(p: Path) -> bool:
    return (p / ENGINE_REL).is_file()


def plugin_root() -> tuple[Path | None, str]:
    """Locate the agentic-rosetta plugin checkout (the tree that owns engine/ and scripts/).

    Skills are INSTALLED as bare SKILL.md + assets/ under a harness skills dir, which does not
    carry engine/. Walking up from an installed SKILL.md therefore never finds the engine, and a
    skill that improvises a filesystem-wide search is exactly the failure this resolves.

    Order (first hit wins), returning (path, how):
      1. $ROSETTA_PLUGIN
      2. $ROSETTA_HOME/plugin-root      (recorded by install.sh)
      3. walking up from THIS file      (true when running the in-checkout copy)
    A candidate only counts if it actually contains engine/src/research.ts.
    """
    env = os.environ.get("ROSETTA_PLUGIN")
    if env:
        p = Path(env).expanduser().resolve()
        if _is_plugin_root(p):
            return p, "env:ROSETTA_PLUGIN"
        warn(f"$ROSETTA_PLUGIN={p} has no {ENGINE_REL} — ignoring")

    pointer = rosetta_home() / "plugin-root"
    if pointer.is_file():
        try:
            recorded = pointer.read_text(encoding="utf-8").strip()
        except OSError as exc:
            recorded = ""
            warn(f"cannot read {pointer}: {exc}")
        if recorded:
            p = Path(recorded).expanduser().resolve()
            if _is_plugin_root(p):
                return p, f"pointer:{pointer}"
            warn(f"{pointer} points at {p}, which has no {ENGINE_REL} — ignoring")

    cur = Path(__file__).resolve().parent
    while True:
        if _is_plugin_root(cur):
            return cur, "walk-up-from-resolver"
        if cur.parent == cur:
            break
        cur = cur.parent
    return None, "not-found"


def _start_dir() -> Path:
    """The caller's cwd in the spelling they used ($PWD), falling back to the physical path.

    `os.getcwd()` returns the symlink-free path, so `cd /tmp/repo && rosetta_config.py path`
    would print `/private/tmp/repo/...` — the same file, but not the path the operator typed.
    $PWD is used only after it is proven to name the current directory.
    """
    pwd = os.environ.get("PWD")
    if pwd:
        try:
            if os.path.samefile(pwd, "."):
                return Path(pwd)
        except OSError:
            pass
    return Path.cwd()


def project_root(start: Path) -> Path:
    """The repo a command is about: nearest .git, else nearest config, else nearest .rosetta.

    .git wins so a repo's config is never silently replaced by an outer tree's config. A
    nested `.rosetta` directory with no config in it (a stray docs/ folder) must not become
    the project root either — only a real config does.

    The operator's spelling of the path is preserved (absolute, but symlinks unresolved), so
    `path` prints `<T>/.rosetta/docs/...` for the `$T` they passed rather than a `/private`
    twin of it. Containment checks still resolve both sides, so a symlink cannot escape.
    """
    start = Path(os.path.abspath(start.expanduser()))
    git_hit = config_hit = rosetta_hit = None
    cur = start
    while True:
        if git_hit is None and (cur / ".git").exists():
            git_hit = cur
        if config_hit is None and (cur / CONFIG_RELPATH).is_file():
            config_hit = cur
        if rosetta_hit is None and (cur / ".rosetta").is_dir():
            rosetta_hit = cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return git_hit or config_hit or rosetta_hit or start


# ───────────────────────────── validation ─────────────────────────────

def read_config(path: Path) -> dict:
    """Read one config file strictly. Missing or malformed is a refusal, not a default."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(
            f"no project config at {path}\n"
            f"Rosetta runs only in a repo that declares where its knowledge bases, research "
            f"notes, probes and agent prompts live.\n"
            f"Create {path} with:\n{suggested_config()}"
        ) from None
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror}") from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{path}: invalid JSON ({exc.msg} at line {exc.lineno}) — refusing to guess\n"
            f"Fix it to match:\n{suggested_config()}"
        ) from None
    if not isinstance(data, dict):
        raise ConfigError(
            f"{path}: top level must be a JSON object — refusing to guess\n"
            f"Fix it to match:\n{suggested_config()}"
        )
    return data


def _validate_path(key: str, value, repo: Path, config_path: Path) -> str:
    """One `paths.<key>` value: repo-relative, inside the repo, and visible to the registry."""
    label = f"paths.{key}"
    return _repo_relative_value(label, value, repo, config_path, prune=key == "docs")


def _repo_relative_value(label: str, value, repo: Path, config_path: Path,
                         prune: bool = False) -> str:
    """Shared rule for `paths.<key>` and `citation_aliases.<prefix>`. Names ``label`` on refusal."""
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{config_path}: {label} must be a non-empty string, got {value!r}")
    raw = value.strip()

    if raw == "device":
        raise ConfigError(
            f"{config_path}: {label} must be a path inside {repo}, got 'device' "
            f"(device roots are not supported: every artifact lives in the repo)"
        )
    if raw.startswith("~"):
        raise ConfigError(
            f"{config_path}: {label} must be a path inside {repo}, got {raw!r} "
            f"(no ~ expansion — write it relative to the repo root)"
        )
    if Path(raw).is_absolute():
        raise ConfigError(
            f"{config_path}: {label} must be a path inside {repo}, got absolute {raw!r}"
        )
    if ".." in Path(raw).parts:
        raise ConfigError(
            f"{config_path}: {label} must be a path inside {repo}, got {raw!r} "
            f"('..' leaves the repo)"
        )
    if prune:
        pruned = [part for part in Path(raw).parts if part in PRUNED_DOC_SEGMENTS]
        if pruned:
            raise ConfigError(
                f"{config_path}: {label} passes through a registry-pruned name "
                f"{pruned[0]!r}; choose a directory the registry walks"
            )

    repo_resolved = repo.resolve()
    target = (repo / raw).resolve()
    if target != repo_resolved and repo_resolved not in target.parents:
        raise ConfigError(
            f"{config_path}: {label} resolves outside {repo_resolved} "
            f"({raw!r} -> {target}); a symlink must not carry the path out of the repo"
        )
    return raw


def validate_config(cfg: dict, config_path: Path, repo: Path) -> dict[str, str]:
    """Return {class: relative path}. Raises ConfigError naming the offending key."""
    version = cfg.get("version")
    if "paths" not in cfg and "roots" in cfg or version == 1:
        raise ConfigError(
            f"{config_path}: version 1 configs (a `roots` map) are no longer read\n"
            f"Replace it with the equivalent version 2 config:\n{suggested_config()}"
        )
    if version != SCHEMA_VERSION:
        raise ConfigError(
            f"{config_path}: version must be {SCHEMA_VERSION}, got {version!r}\n"
            f"Use:\n{suggested_config()}"
        )
    paths = cfg.get("paths")
    if not isinstance(paths, dict):
        raise ConfigError(
            f"{config_path}: `paths` must be an object with docs, research, probes and agents\n"
            f"Use:\n{suggested_config()}"
        )
    resolved: dict[str, str] = {}
    for key in CLASSES:
        if key not in paths:
            raise ConfigError(
                f"{config_path}: paths.{key} is required — docs, research, probes and agents "
                f"must all be declared\nUse:\n{suggested_config()}"
            )
        resolved[key] = _validate_path(key, paths[key], repo, config_path)
    return resolved


def validate_citation_aliases(cfg: dict, config_path: Path, repo: Path) -> dict[str, str]:
    """Return {citation prefix: repo-relative dir}. Absent is fine; a bad entry is a refusal.

    A prefix is matched against the start of a citation as written in a graph, so it keeps its
    spelling (a trailing slash is dropped and re-added by the reader). The value is a directory
    inside the repo, validated exactly like ``paths.<key>``.
    """
    aliases = cfg.get("citation_aliases")
    if aliases is None:
        return {}
    if not isinstance(aliases, dict):
        raise ConfigError(
            f"{config_path}: citation_aliases must be an object mapping a citation prefix to a "
            f"repo-relative directory\nUse:\n{suggested_config()}"
        )
    resolved: dict[str, str] = {}
    for prefix, value in aliases.items():
        label = f"citation_aliases.{prefix}"
        if not isinstance(prefix, str) or not prefix.strip():
            raise ConfigError(f"{config_path}: {label} must be a non-empty citation prefix")
        raw_prefix = prefix.strip()
        if raw_prefix.startswith("~") or Path(raw_prefix).is_absolute():
            raise ConfigError(
                f"{config_path}: {label} must be a citation prefix as written in a graph, "
                f"not a path ({raw_prefix!r})"
            )
        if ".." in Path(raw_prefix).parts:
            raise ConfigError(
                f"{config_path}: {label} must not contain '..' ({raw_prefix!r})"
            )
        key = raw_prefix.rstrip("/")
        if key in resolved:
            raise ConfigError(f"{config_path}: {label} is declared twice")
        resolved[key] = _repo_relative_value(label, value, repo, config_path)
    return resolved


def device_declares_roots(path: Path) -> bool:
    """True when the device file still tries to decide a location (the removed design)."""
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    for key in ("roots", "paths"):
        val = data.get(key)
        if isinstance(val, dict) and val:
            return True
    return False


# ───────────────────────────── resolver ─────────────────────────────

class Resolver:
    def __init__(self, project_dir: Path | None = None, home: Path | None = None,
                 cli_roots: dict | None = None):
        self.home = (home or rosetta_home())
        self.project = project_root(project_dir or _start_dir())
        self.cli_roots = {k: v for k, v in (cli_roots or {}).items() if v}
        self.warnings: list[str] = []

        self.project_cfg_path = self.project / CONFIG_RELPATH
        self.device_cfg_path = self.home / "config.json"

        # Validate the project config BEFORE the device note, so a refusal is never buried
        # under an unrelated warning.
        self.project_cfg = read_config(self.project_cfg_path)
        self.paths = validate_config(self.project_cfg, self.project_cfg_path, self.project)
        self.citation_aliases = validate_citation_aliases(
            self.project_cfg, self.project_cfg_path, self.project)

        if device_declares_roots(self.device_cfg_path):
            msg = (f"{self.device_cfg_path} declares roots; device roots are ignored — only "
                   f"{self.project_cfg_path} decides where artifacts go")
            self.warnings.append(msg)
            warn(msg)

    # ---- resolution ------------------------------------------------
    def _decide(self, cls: str) -> dict:
        """Return the winning value for one class. Pure — no fs writes, uses self.paths."""
        for key in (cls, "default"):
            val = self.cli_roots.get(key)
            if isinstance(val, str) and val.strip():
                p = Path(val.strip()).expanduser()
                if not p.is_absolute():
                    p = self.project / p
                return {"dir": p.resolve(), "tier": "cli",
                        "config_key": f"--{key}-root", "value": val.strip()}
        value = self.paths[cls]
        return {"dir": self.project / value, "tier": "config",
                "config_key": f"paths.{cls}", "value": value}

    def resolve(self, cls: str) -> dict:
        if cls not in CLASSES:
            raise SystemExit(f"unknown class {cls!r}; expected one of {', '.join(CLASSES)}")
        decision = self._decide(cls)
        return {
            "class": cls,
            "dir": str(decision["dir"]),
            "value": decision["value"],
            "tier": decision["tier"],
            "config_file": str(self.project_cfg_path) if decision["tier"] == "config" else None,
            "config_key": decision["config_key"],
        }

    # ---- citation aliases ------------------------------------------
    def aliases(self) -> dict[str, str]:
        """{citation prefix: absolute directory}. The reader matches the longest prefix first."""
        return {prefix: str((self.project / value)) for prefix, value in self.citation_aliases.items()}

    # ---- artifact paths --------------------------------------------
    def artifact(self, cls: str, slug: str, filename: str | None = None) -> dict:
        r = self.resolve(cls)
        d = Path(r["dir"])
        if cls in ("docs", "agents"):
            p = d / slug / filename if filename else d / slug
        else:
            p = d / f"{slug}.md"
        r = dict(r)
        r["path"] = str(p)
        return r

    def find(self, cls: str, slug: str, filename: str | None = None) -> dict | None:
        """Read side: the configured location, then the legacy project-local one. No device."""
        candidates = [self.artifact(cls, slug, filename)]
        legacy_dir = self.project / ".rosetta" / cls
        if cls in ("docs", "agents"):
            legacy_path = (legacy_dir / slug / filename if filename else legacy_dir / slug)
        else:
            legacy_path = legacy_dir / f"{slug}.md"
        candidates.append({"class": cls, "path": str(legacy_path), "dir": str(legacy_dir),
                           "value": ".rosetta/" + cls, "tier": "legacy-project",
                           "config_file": None, "config_key": None})
        seen: set[str] = set()
        for c in candidates:
            if c["path"] in seen:
                continue
            seen.add(c["path"])
            if Path(c["path"]).exists():
                return c
        return None

    def show(self) -> dict:
        return {
            "project_root": str(self.project),
            "rosetta_home": str(self.home),
            "config_file": str(self.project_cfg_path),
            "config_present": self.project_cfg_path.is_file(),
            "paths": dict(self.paths),
            "citation_aliases": dict(self.citation_aliases),
            "roots": {c: self.resolve(c) for c in CLASSES},
            "warnings": self.warnings,
        }


# ───────────────────────────── verbs ─────────────────────────────

def cmd_init(home: Path) -> int:
    """Create the device home. It holds the resolver and the plugin pointer, not roots."""
    home.mkdir(parents=True, exist_ok=True)
    (home / "bin").mkdir(parents=True, exist_ok=True)
    print(f"rosetta home: {home}")
    print("artifact locations come from <repo>/.rosetta/config.json; this device home "
          "declares no roots.")
    cfg = home / "config.json"
    if cfg.exists():
        print(f"existing device file left untouched: {cfg}")
        if device_declares_roots(cfg):
            print(f"NOTE: {cfg} declares roots, which are ignored — remove them.")
    return 0


def _repo_intent(repo: Path, config_path: Path) -> dict[str, str] | None:
    """One other repo's declared paths, or None when it has no valid config."""
    try:
        cfg = read_config(config_path)
        return validate_config(cfg, config_path, repo)
    except ConfigError:
        return None


def cmd_doctor(res: Resolver, scan_roots: list[Path], apply: bool,
               excludes: list[Path] | None = None) -> int:
    excludes = [Path(e).expanduser() for e in (excludes or [])]
    print("=== declared paths in this repo ===")
    print(f"  {res.project_cfg_path}")
    resolved = {}
    for c in CLASSES:
        r = res.resolve(c)
        resolved[c] = Path(r["dir"]).resolve()
        print(f"  {c:<9} {r['dir']}   [{r['tier']}: {r['config_key']} = {r['value']}]")

    print("\n=== other repos under the scan roots ===")
    strays: list[tuple[Path, Path]] = []
    unconfigured = 0
    for scan in scan_roots:
        scan = scan.expanduser()
        if not scan.is_dir():
            continue
        for cfgdir in sorted(scan.glob("*/.rosetta")):
            if not cfgdir.is_dir():
                continue
            if any(cfgdir.resolve() == e.resolve() or e.resolve() in cfgdir.resolve().parents
                   for e in excludes):
                continue
            repo = cfgdir.parent
            cfg_path = cfgdir / "config.json"
            if repo.resolve() == res.project.resolve():
                continue
            declared = _repo_intent(repo, cfg_path)
            if declared is None:
                if any((cfgdir / c).is_dir() for c in CLASSES):
                    unconfigured += 1
                    print(f"  NO CONFIG (Rosetta refuses to run here): {cfg_path}")
                continue
            print(f"  {repo}: docs -> {repo / declared['docs']}")
            for c in CLASSES:
                configured = (repo / declared[c]).resolve()
                legacy = (repo / ".rosetta" / c).resolve()
                if legacy == configured or not legacy.is_dir():
                    continue
                for item in sorted(legacy.iterdir()):
                    if item.name.startswith("."):
                        continue
                    strays.append((item, configured / item.name))

    print("\n=== artifacts in the legacy .rosetta layout ===")
    if not strays:
        print("  none")
    for src, dst in strays:
        status = "CONFLICT (destination exists)" if dst.exists() else (
            "move" if apply else "would move")
        print(f"  {status}: {src}\n      -> {dst}")

    moved = skipped = 0
    if apply:
        for src, dst in strays:
            if dst.exists():
                skipped += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved += 1
        print(f"\n  moved {moved}, skipped {skipped} (conflicts)")
    elif strays:
        print("\n  (dry run — re-run with --apply to move them)")
    if unconfigured:
        print(f"\n  {unconfigured} repo(s) still have artifact directories and no config.")
    return 0


# ───────────────────────────── selftest ─────────────────────────────

def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj, indent=2), encoding="utf-8")


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, got, want):
        if got != want:
            failures.append(f"{name}\n     got:  {got!r}\n     want: {want!r}")
            print(f"  FAIL {name}")
        else:
            print(f"  ok   {name}")

    tmp = Path(tempfile.mkdtemp(prefix="rosetta-config-selftest-"))
    try:
        home = tmp / "device-home"
        home.mkdir()
        proj = tmp / "proj"
        (proj / ".git").mkdir(parents=True)

        print("── no project config is a refusal naming the file and the JSON ──")
        missing = proj / CONFIG_RELPATH
        for name, build in (
            ("show", lambda: Resolver(proj, home).show()),
            ("path", lambda: Resolver(proj, home).artifact("docs", "x")),
            ("root", lambda: Resolver(proj, home).resolve("docs")),
        ):
            try:
                build()
                check(f"{name} refused", "no error", "ConfigError")
            except ConfigError as exc:
                check(f"{name} names config", str(missing) in str(exc), True)
                check(f"{name} prints json", '"version": 2' in str(exc), True)

        print("── the standard version 2 config names the standard layout ──")
        _write(proj / CONFIG_RELPATH, {"version": 2, "paths": dict(STANDARD_PATHS)})
        r = Resolver(proj, home)
        check("docs dir", r.resolve("docs")["dir"], str(proj / ".rosetta" / "docs"))
        check("tier is config", r.resolve("docs")["tier"], "config")
        check("key is paths.docs", r.resolve("docs")["config_key"], "paths.docs")
        check("agents dir", r.resolve("agents")["dir"], str(proj / "agents"))
        check("docs path", r.artifact("docs", "a")["path"], str(proj / ".rosetta" / "docs" / "a"))
        check("docs file", r.artifact("docs", "a", "fact-graph.json")["path"],
              str(proj / ".rosetta" / "docs" / "a" / "fact-graph.json"))
        check("research file", r.artifact("research", "a")["path"],
              str(proj / ".rosetta" / "research" / "a.md"))
        check("no warnings", r.warnings, [])

        print("── a custom layout is honoured for every class ──")
        _write(proj / CONFIG_RELPATH, {"version": 2, "paths": {
            "docs": "kb/graphs", "research": "kb/research",
            "probes": "kb/probes", "agents": "prompts"}})
        r = Resolver(proj, home)
        check("custom docs", r.resolve("docs")["dir"], str(proj / "kb" / "graphs"))
        check("custom agents", r.resolve("agents")["dir"], str(proj / "prompts"))
        check("custom research file", r.artifact("research", "a")["path"],
              str(proj / "kb" / "research" / "a.md"))

        print("── every bad path kind is refused, naming its key ──")
        def bad(name: str, paths: dict, key: str) -> None:
            _write(proj / CONFIG_RELPATH, {"version": 2, "paths": paths})
            try:
                Resolver(proj, home)
                check(name, "accepted", f"refused naming {key}")
            except ConfigError as exc:
                check(name, key in str(exc), True)

        base = dict(STANDARD_PATHS)
        bad("missing agents", {k: v for k, v in base.items() if k != "agents"}, "paths.agents")
        bad("absolute", dict(base, docs="/abs"), "paths.docs")
        bad("tilde", dict(base, docs="~/x"), "paths.docs")
        bad("device", dict(base, docs="device"), "paths.docs")
        bad("dotdot", dict(base, docs="../x"), "paths.docs")
        bad("pruned", dict(base, docs="tmp/graphs"), "paths.docs")
        (proj / "escape").symlink_to("/tmp")
        bad("symlink escape", dict(base, docs="escape/graphs"), "paths.docs")
        (proj / "escape").unlink()

        print("── a citation alias is validated like a path, and named on refusal ──")
        _write(proj / CONFIG_RELPATH, {"version": 2, "paths": dict(STANDARD_PATHS),
                                       "citation_aliases": {"notes": ".rosetta/docs"}})
        r = Resolver(proj, home)
        check("alias resolves", r.aliases(), {"notes": str(proj / ".rosetta" / "docs")})
        _write(proj / CONFIG_RELPATH, {"version": 2, "paths": dict(STANDARD_PATHS)})
        check("no aliases is fine", Resolver(proj, home).aliases(), {})
        for alias_name, alias_value in (
            ("absolute", "/tmp/outside"),
            ("tilde", "~/outside"),
            ("dotdot", "../outside"),
        ):
            _write(proj / CONFIG_RELPATH, {"version": 2, "paths": dict(STANDARD_PATHS),
                                           "citation_aliases": {alias_name: alias_value}})
            try:
                Resolver(proj, home)
                check(f"alias {alias_name} refused", "accepted", "ConfigError")
            except ConfigError as exc:
                check(f"alias {alias_name} names key", alias_name in str(exc), True)
        (proj / "escape").symlink_to("/tmp")
        _write(proj / CONFIG_RELPATH, {"version": 2, "paths": dict(STANDARD_PATHS),
                                       "citation_aliases": {"escape/graphs": "escape/graphs"}})
        try:
            Resolver(proj, home)
            check("alias symlink escape refused", "accepted", "ConfigError")
        except ConfigError as exc:
            check("alias symlink escape names key", "escape/graphs" in str(exc), True)
        (proj / "escape").unlink()

        print("── a version 1 config is refused with the equivalent version 2 ──")
        _write(proj / CONFIG_RELPATH, {"version": 1, "roots": {"default": "project"}})
        try:
            Resolver(proj, home)
            check("v1 refused", "accepted", "ConfigError")
        except ConfigError as exc:
            check("v1 suggests version 2", '"version": 2' in str(exc), True)
            check("v1 suggests paths", '"paths"' in str(exc), True)

        print("── a device file never moves a root and warns exactly once ──")
        _write(proj / CONFIG_RELPATH, {"version": 2, "paths": dict(STANDARD_PATHS)})
        _write(home / "config.json", {"version": 1, "roots": {"docs": "/elsewhere"}})
        r = Resolver(proj, home)
        check("device docs ignored", r.resolve("docs")["dir"],
              str(proj / ".rosetta" / "docs"))
        check("one device warning", len(r.warnings), 1)
        check("warning says ignored", "device roots are ignored" in r.warnings[0], True)
        (home / "config.json").unlink()

        print("── a device home with the resolver does not move anything ──")
        r = Resolver(proj, home)
        check("no device warning without a roots file", r.warnings, [])

        print("── CLI overrides win over the config, and only they do ──")
        cli_dir = tmp / "cli"
        cli_dir.mkdir()
        r = Resolver(proj, home, cli_roots={"docs": str(cli_dir)})
        check("cli wins", r.resolve("docs")["dir"], str(cli_dir))
        check("cli tier", r.resolve("docs")["tier"], "cli")

        print("── read side: configured first, then the legacy project layout ──")
        _write(proj / CONFIG_RELPATH, {"version": 2, "paths": {
            "docs": "kb/graphs", "research": "kb/research",
            "probes": "kb/probes", "agents": "prompts"}})
        r = Resolver(proj, home)
        check("missing everywhere -> None", r.find("docs", "nope"), None)
        legacy = proj / ".rosetta" / "docs" / "legacy-kb"
        legacy.mkdir(parents=True)
        found = r.find("docs", "legacy-kb")
        check("finds legacy project KB", found and found["path"], str(legacy))
        check("records legacy tier", found and found["tier"], "legacy-project")
        preferred = proj / "kb" / "graphs" / "legacy-kb"
        preferred.mkdir(parents=True)
        found = r.find("docs", "legacy-kb")
        check("configured root wins once populated", found and found["path"], str(preferred))

        print("── project root detection walks up to .git ──")
        deep = proj / "a" / "b" / "c"
        deep.mkdir(parents=True)
        check("nested cwd anchors at repo root", Resolver(deep, home).project, proj)

        print("── init creates the device home and never invents roots ──")
        h2 = tmp / "home2"
        cmd_init(h2)
        check("home created", (h2 / "bin").is_dir(), True)
        check("init writes no roots", (h2 / "config.json").exists(), False)
        _write(h2 / "config.json", {"version": 1, "roots": {"default": "device"}, "mine": True})
        cmd_init(h2)
        kept = json.loads((h2 / "config.json").read_text())
        check("existing device file preserved", kept.get("mine"), True)

        print("── CLI subprocess: path prints exactly one line, and creates nothing ──")
        import subprocess
        fresh = tmp / "fresh-proj"
        (fresh / ".git").mkdir(parents=True)
        _write(fresh / CONFIG_RELPATH, {"version": 2, "paths": dict(STANDARD_PATHS)})
        env = dict(os.environ, ROSETTA_HOME=str(home))
        env.pop("ROSETTA_PROJECT", None)
        out = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "path",
             "--class", "docs", "--slug", "demo", "--project", str(fresh)],
            capture_output=True, text=True, env=env)
        check("exit 0", out.returncode, 0)
        check("single line", len(out.stdout.strip().splitlines()), 1)
        check("expected path", out.stdout.strip(),
              str(fresh / ".rosetta" / "docs" / "demo"))
        check("path created nothing", (fresh / ".rosetta" / "docs").exists(), False)

        print("── --ensure creates the directory ──")
        subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "path", "--class", "research",
             "--slug", "made", "--project", str(fresh), "--ensure"],
            capture_output=True, text=True, env=env)
        check("dir created", (fresh / ".rosetta" / "research").is_dir(), True)

        print("── plugin root: pointer file, env override, and honest failure ──")
        fake_plugin = tmp / "fake-plugin"
        (fake_plugin / "engine" / "src").mkdir(parents=True)
        (fake_plugin / "engine" / "src" / "research.ts").write_text("// engine\n")
        bare_home = tmp / "bare-home"
        (bare_home / "bin").mkdir(parents=True)

        def plugin_run(extra_env: dict, cwd: Path | None = None):
            e = dict(os.environ)
            e.pop("ROSETTA_PLUGIN", None)
            e.update({k: str(v) for k, v in extra_env.items()})
            return subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "plugin"],
                capture_output=True, text=True, env=e, cwd=str(cwd) if cwd else None)

        # A copy that is NOT inside a plugin checkout, so walk-up cannot rescue it.
        lone = bare_home / "bin" / "rosetta_config.py"
        lone.write_bytes(Path(__file__).resolve().read_bytes())

        def lone_run(extra_env: dict):
            e = dict(os.environ)
            e.pop("ROSETTA_PLUGIN", None)
            e.update({k: str(v) for k, v in extra_env.items()})
            return subprocess.run([sys.executable, str(lone), "plugin"],
                                  capture_output=True, text=True, env=e)

        out = lone_run({"ROSETTA_HOME": bare_home})
        check("no pointer, no walk-up -> exit 1", out.returncode, 1)

        (bare_home / "plugin-root").write_text(str(fake_plugin) + "\n")
        out = lone_run({"ROSETTA_HOME": bare_home})
        check("pointer file resolves", out.stdout.strip(), str(fake_plugin))

        other = tmp / "other-plugin"
        (other / "engine" / "src").mkdir(parents=True)
        (other / "engine" / "src" / "research.ts").write_text("// engine\n")
        out = lone_run({"ROSETTA_HOME": bare_home, "ROSETTA_PLUGIN": str(other)})
        check("env beats pointer", out.stdout.strip(), str(other))

        # A pointer at a tree with no engine must be REJECTED, not returned.
        (bare_home / "plugin-root").write_text(str(tmp / "not-a-plugin") + "\n")
        out = lone_run({"ROSETTA_HOME": bare_home})
        check("stale pointer rejected", out.returncode, 1)

        # The in-checkout copy finds its own plugin by walking up, with no pointer at all.
        out = plugin_run({"ROSETTA_HOME": bare_home})
        expected_walk = str(Path(__file__).resolve().parent.parent)
        if (Path(expected_walk) / "engine" / "src" / "research.ts").is_file():
            check("walk-up from in-checkout copy", out.stdout.strip(), expected_walk)
        else:
            print("  ok   (no engine in this checkout — walk-up case not applicable)")

        print("── asset copies are byte-identical (drift guard) ──")
        canonical = Path(__file__).resolve()
        repo = canonical.parent.parent
        copies = sorted(repo.glob("skills/*/assets/rosetta_config.py"))
        if copies:
            drift = [str(c) for c in copies if c.read_bytes() != canonical.read_bytes()]
            check("no drifted copies", drift, [])
        else:
            print("  ok   (no skill copies yet — sync-assets.sh not run)")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print(f"SELFTEST FAILED — {len(failures)} failure(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SELFTEST PASSED")
    return 0


# ───────────────────────────── CLI ─────────────────────────────

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="rosetta_config.py",
        description="Resolve where Rosetta artifacts live, from the repo's version 2 config.")
    ap.add_argument("--selftest", action="store_true", help="run real-filesystem selftests")
    sub = ap.add_subparsers(dest="verb")

    def common(p, slug=False):
        p.add_argument("--class", dest="cls", required=True, choices=CLASSES)
        if slug:
            p.add_argument("--slug", required=True)
            p.add_argument("--file", dest="filename", default=None,
                           help="file within a docs/<slug> (or agents/<slug>) directory")
        p.add_argument("--project", default=None, help="project dir (default: cwd)")
        p.add_argument("--root", default=None,
                       help="explicit directory override for every class (not read from config)")
        for c in CLASSES:
            p.add_argument(f"--{c}-root", dest=f"{c}_root", default=None,
                           help=f"explicit directory override for {c} (not read from config)")
        p.add_argument("--ensure", action="store_true", help="create the directory if missing")

    common(sub.add_parser("path", help="absolute path for one artifact"), slug=True)
    common(sub.add_parser("root", help="absolute directory for one artifact class"))
    common(sub.add_parser("find", help="read side: first existing copy, searching all roots"),
           slug=True)

    p_show = sub.add_parser("show", help="resolved paths + provenance")
    p_show.add_argument("--project", default=None)
    p_show.add_argument("--root", default=None)
    p_show.add_argument("--format", dest="fmt", choices=("json", "plain"), default="json",
                        help="plain = one 'class dir [source]' line per class")
    for c in CLASSES:
        p_show.add_argument(f"--{c}-root", dest=f"{c}_root", default=None)

    sub.add_parser("init", help="create $ROSETTA_HOME (device home + bin/; declares no roots)")

    p_alias = sub.add_parser(
        "citation-aliases",
        help="print the config's citation prefix table as JSON {prefix: absolute dir}")
    p_alias.add_argument("--project", default=None)

    p_plug = sub.add_parser("plugin", help="absolute path of the plugin checkout that owns engine/")
    p_plug.add_argument("--why", action="store_true", help="also print which tier resolved it")

    p_doc = sub.add_parser("doctor", help="report repos still using the legacy .rosetta layout")
    p_doc.add_argument("--project", default=None)
    p_doc.add_argument("--scan-root", action="append", default=None,
                       help="where to look for other repos (repeatable; default ~/Projects)")
    p_doc.add_argument("--apply", action="store_true", help="actually move (default: dry run)")
    p_doc.add_argument("--exclude", action="append", default=None,
                       help="repo to leave alone (repeatable), e.g. the plugin's own tree")

    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.verb:
        ap.print_help()
        return 2

    if args.verb == "init":
        return cmd_init(rosetta_home())

    if args.verb == "plugin":
        p, how = plugin_root()
        if p is None:
            print(
                "plugin checkout not found. Set $ROSETTA_PLUGIN, or re-run install.sh from the\n"
                f"checkout so it records {rosetta_home() / 'plugin-root'}.",
                file=sys.stderr,
            )
            return 1
        print(f"{p}\t[{how}]" if args.why else str(p))
        return 0

    cli_roots = {"default": getattr(args, "root", None)}
    for c in CLASSES:
        cli_roots[c] = getattr(args, f"{c}_root", None)
    try:
        res = Resolver(Path(args.project) if args.project else None, None, cli_roots)
    except ConfigError as exc:
        print(error_text(exc), file=sys.stderr)
        return 2

    if args.verb == "doctor":
        scans = [Path(s) for s in (args.scan_root or [str(Path.home() / "Projects")])]
        excl = [Path(e) for e in (args.exclude or [])]
        return cmd_doctor(res, scans, args.apply, excl)

    if args.verb == "show":
        data = res.show()
        if args.fmt == "plain":
            for cls, r in data["roots"].items():
                why = f"{r['tier']}: {r['config_key']} = {r['value']}"
                print(f"  {cls:<9} {r['dir']}\n            [{why}]")
        else:
            print(json.dumps(data, indent=2))
        return 0

    if args.verb == "root":
        r = res.resolve(args.cls)
        if args.ensure:
            Path(r["dir"]).mkdir(parents=True, exist_ok=True)
        print(r["dir"])
        return 0

    if args.verb == "citation-aliases":
        print(json.dumps(res.aliases(), indent=2, sort_keys=True))
        return 0

    if args.verb == "path":
        r = res.artifact(args.cls, args.slug, args.filename)
        if args.ensure:
            parent = Path(r["path"]) if args.cls in ("docs", "agents") and not args.filename \
                else Path(r["path"]).parent
            parent.mkdir(parents=True, exist_ok=True)
        print(r["path"])
        return 0

    if args.verb == "find":
        hit = res.find(args.cls, args.slug, args.filename)
        if hit is None:
            return 1
        print(hit["path"])
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
