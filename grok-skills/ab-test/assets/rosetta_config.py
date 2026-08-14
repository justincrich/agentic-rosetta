#!/usr/bin/env python3
"""
rosetta_config.py — deterministic storage-root resolver for the agentic-rosetta skills.

WHY THIS EXISTS
  The skills used to hardcode `.rosetta/research/<slug>.md` (and docs/, probes/) relative to
  whatever project the session happened to be running in. Research and probe catalogs therefore
  stranded themselves across repos, split from the knowledge base and agents they belong to.
  Where an artifact goes must ALWAYS be decided the same way, so the decision lives in CODE —
  this file — not in skill prose that an LLM re-interprets every run.

PRECEDENCE (per artifact class; first tier that yields a value wins)
  1. CLI flag                        --root / --research-root / --docs-root / --probes-root
  2. <project>/.rosetta/config.json  roots.<class>, then roots.default
  3. $ROSETTA_HOME/config.json       roots.<class>, then roots.default   (default: ~/.rosetta)
  4. built-in                        "project"

ROOT FORMS
  "project"   the current project root (nearest ancestor with .git, else with .rosetta, else cwd)
  "device"    $ROSETTA_HOME itself
  <path>      absolute or ~-relative, e.g. ~/Projects/brain

LAYOUT (identical under whichever root wins)
  <root>/.rosetta/research/<slug>.md
  <root>/.rosetta/docs/<slug>/
  <root>/.rosetta/probes/<slug>.md
  ...except root form "device", which already IS the .rosetta dir:
  $ROSETTA_HOME/research/<slug>.md

HONEST DEGRADATION (never fake, never crash)
  no config anywhere  -> "project" (byte-identical to pre-config behavior)
  malformed JSON      -> warn naming the file, skip that tier, continue, exit 0
  unwritable root     -> warn, fall back to project, and REPORT the fallback, so the artifact's
                         `storage:` field can honestly record where it actually landed

Config schema (both tiers use the same shape):
  {"version": 1, "roots": {"default": "project", "research": "~/Projects/brain"}}
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

CLASSES = ("research", "docs", "probes")
CONFIG_RELPATH = Path(".rosetta") / "config.json"
BUILTIN_DEFAULT = "project"
SCHEMA_VERSION = 1

DEFAULT_CONFIG = {
    "version": SCHEMA_VERSION,
    "roots": {"default": "project"},
}


# ───────────────────────────── helpers ─────────────────────────────

def warn(msg: str) -> None:
    print(f"[rosetta-config] WARN: {msg}", file=sys.stderr)


def rosetta_home() -> Path:
    env = os.environ.get("ROSETTA_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".rosetta").resolve()


def project_root(start: Path) -> Path:
    """Nearest ancestor with .git, else nearest with .rosetta, else `start` itself."""
    start = start.expanduser().resolve()
    git_hit = rosetta_hit = None
    cur = start
    while True:
        if git_hit is None and (cur / ".git").exists():
            git_hit = cur
        if rosetta_hit is None and (cur / ".rosetta").is_dir():
            rosetta_hit = cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return git_hit or rosetta_hit or start


def load_config(path: Path) -> tuple[dict, str | None]:
    """Return (config, warning). Malformed/unreadable never raises — it degrades to {}."""
    if not path.is_file():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {}, f"{path}: invalid JSON ({exc.msg} at line {exc.lineno}) — tier skipped"
    except OSError as exc:
        return {}, f"{path}: unreadable ({exc.strerror}) — tier skipped"
    if not isinstance(data, dict):
        return {}, f"{path}: top level must be an object — tier skipped"
    return data, None


def _roots_of(cfg: dict) -> dict:
    roots = cfg.get("roots")
    return roots if isinstance(roots, dict) else {}


def nearest_existing(p: Path) -> Path:
    cur = p
    while not cur.exists() and cur.parent != cur:
        cur = cur.parent
    return cur


# ───────────────────────────── resolver ─────────────────────────────

class Resolver:
    def __init__(self, project_dir: Path | None = None, home: Path | None = None,
                 cli_roots: dict | None = None):
        self.home = (home or rosetta_home())
        self.project = project_root(project_dir or Path.cwd())
        self.cli_roots = {k: v for k, v in (cli_roots or {}).items() if v}
        self.warnings: list[str] = []

        self.project_cfg_path = self.project / CONFIG_RELPATH
        self.device_cfg_path = self.home / "config.json"

        self.project_cfg, w1 = load_config(self.project_cfg_path)
        self.device_cfg, w2 = load_config(self.device_cfg_path)
        for w in (w1, w2):
            if w:
                self.warnings.append(w)
                warn(w)

    # ---- tier walk -------------------------------------------------
    def _decide(self, cls: str) -> dict:
        """Return {'value','tier','file','key'} for the winning tier — pure, no fs writes."""
        cli = self.cli_roots.get(cls) or self.cli_roots.get("default")
        if cli:
            key = cls if self.cli_roots.get(cls) else "default"
            return {"value": cli, "tier": "cli", "file": None, "key": f"--{key}-root"}

        for tier, cfg, path in (
            ("project", self.project_cfg, self.project_cfg_path),
            ("device", self.device_cfg, self.device_cfg_path),
        ):
            roots = _roots_of(cfg)
            for key in (cls, "default"):
                val = roots.get(key)
                if isinstance(val, str) and val.strip():
                    return {"value": val.strip(), "tier": tier, "file": str(path),
                            "key": f"roots.{key}"}
        return {"value": BUILTIN_DEFAULT, "tier": "builtin-default", "file": None,
                "key": "roots.default"}

    def _base_for(self, value: str) -> tuple[Path, str]:
        """Map a root value to the directory that holds research/ docs/ probes/."""
        if value == "project":
            return self.project / ".rosetta", "project"
        if value == "device":
            return self.home, "device"
        return Path(value).expanduser().resolve() / ".rosetta", "path"

    def resolve(self, cls: str) -> dict:
        if cls not in CLASSES:
            raise SystemExit(f"unknown class {cls!r}; expected one of {', '.join(CLASSES)}")
        decision = self._decide(cls)
        base, form = self._base_for(decision["value"])
        target = base / cls
        fallback = False

        anchor = nearest_existing(target)
        if not os.access(anchor, os.W_OK):
            msg = (f"resolved {cls} root {target} is not writable (nearest existing: {anchor}) "
                   f"— falling back to project")
            self.warnings.append(msg)
            warn(msg)
            base, form = self._base_for("project")
            target = base / cls
            fallback = True

        return {
            "class": cls,
            "dir": str(target),
            "root_value": decision["value"],
            "root_form": form,
            "tier": "project-fallback" if fallback else decision["tier"],
            "config_file": decision["file"],
            "config_key": decision["key"],
            "fallback": fallback,
        }

    # ---- artifact paths --------------------------------------------
    def artifact(self, cls: str, slug: str, filename: str | None = None) -> dict:
        r = self.resolve(cls)
        d = Path(r["dir"])
        if cls == "docs":
            p = d / slug / filename if filename else d / slug
        else:
            p = d / f"{slug}.md"
        r = dict(r)
        r["path"] = str(p)
        return r

    def find(self, cls: str, slug: str, filename: str | None = None) -> dict | None:
        """Read side: configured root, then project, then device. First that exists wins."""
        seen: list[str] = []
        candidates = [self.artifact(cls, slug, filename)]
        for forced in ("project", "device"):
            base, form = self._base_for(forced)
            d = base / cls
            p = (d / slug / filename if filename else d / slug) if cls == "docs" else d / f"{slug}.md"
            candidates.append({"class": cls, "path": str(p), "dir": str(d),
                               "root_value": forced, "root_form": form,
                               "tier": f"search:{forced}", "config_file": None,
                               "config_key": None, "fallback": False})
        for c in candidates:
            if c["path"] in seen:
                continue
            seen.append(c["path"])
            if Path(c["path"]).exists():
                return c
        return None

    def show(self) -> dict:
        return {
            "project_root": str(self.project),
            "rosetta_home": str(self.home),
            "config_files": {
                "project": str(self.project_cfg_path),
                "device": str(self.device_cfg_path),
            },
            "config_present": {
                "project": self.project_cfg_path.is_file(),
                "device": self.device_cfg_path.is_file(),
            },
            "roots": {c: self.resolve(c) for c in CLASSES},
            "warnings": self.warnings,
        }


# ───────────────────────────── verbs ─────────────────────────────

def cmd_init(home: Path) -> int:
    home.mkdir(parents=True, exist_ok=True)
    (home / "bin").mkdir(parents=True, exist_ok=True)
    cfg = home / "config.json"
    if cfg.exists():
        print(f"config already present, left untouched: {cfg}")
    else:
        cfg.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8")
        print(f"created {cfg}")
    print(f"rosetta home: {home}")
    return 0


def cmd_doctor(res: Resolver, scan_roots: list[Path], apply: bool,
               excludes: list[Path] | None = None) -> int:
    excludes = [Path(e).expanduser() for e in (excludes or [])]
    print("=== resolved roots ===")
    resolved = {}
    for c in CLASSES:
        r = res.resolve(c)
        resolved[c] = Path(r["dir"]).resolve()
        src = r["config_file"] or r["tier"]
        print(f"  {c:<9} {r['dir']}   [{r['tier']}: {r['config_key']} = {r['root_value']}]"
              + ("  (FELL BACK)" if r["fallback"] else ""))

    # The brain-registry coupling: a KB outside the repo that owns the agents is invisible
    # to any embed pipeline that walks that repo.
    docs_dir = resolved["docs"]
    docs_repo = docs_dir.parent.parent
    if not (docs_repo / "agents").is_dir():
        print(f"\n  NOTE: docs root {docs_dir} has no sibling agents/ directory. If your agent")
        print("        bodies live elsewhere, a KB written here is invisible to that repo's")
        print("        registry/embed pipeline.")

    # A class whose root is "project" is per-repo BY DESIGN — an artifact sitting in another
    # project is correctly placed, not stray. Only centralized classes can strand things.
    centralized = [c for c in CLASSES if res.resolve(c)["root_form"] != "project"]
    skipped_classes = [c for c in CLASSES if c not in centralized]
    print("\n=== stray artifacts (outside the resolved roots) ===")
    if skipped_classes:
        print(f"  (not checking {', '.join(skipped_classes)} — root is 'project', so every repo "
              f"is its own correct home)")
    strays: list[tuple[Path, Path]] = []
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
            for c in centralized:
                src_dir = cfgdir / c
                if not src_dir.is_dir() or src_dir.resolve() == resolved[c]:
                    continue
                for item in sorted(src_dir.iterdir()):
                    if item.name.startswith("."):
                        continue
                    strays.append((item, resolved[c] / item.name))

    if not strays:
        print("  none")
    for src, dst in strays:
        status = "CONFLICT (destination exists)" if dst.exists() else ("move" if apply else "would move")
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
        # prune now-empty class dirs and .rosetta skeletons we emptied
        for scan in scan_roots:
            for cfgdir in sorted(Path(scan).expanduser().glob("*/.rosetta")):
                if not cfgdir.is_dir() or cfgdir.resolve() in {r.parent for r in resolved.values()}:
                    continue
                for c in centralized:
                    d = cfgdir / c
                    if d.is_dir() and not any(d.iterdir()):
                        d.rmdir()
                if cfgdir.is_dir() and not any(cfgdir.iterdir()):
                    cfgdir.rmdir()
                    print(f"  pruned empty skeleton: {cfgdir}")
        print(f"\n  moved {moved}, skipped {skipped} (conflicts)")
    elif strays:
        print("\n  (dry run — re-run with --apply to move them)")
    return 0


# ───────────────────────────── selftest ─────────────────────────────

def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj, indent=2), encoding="utf-8")


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, got, want):
        if got != want:
            failures.append(f"{name}\n     got:  {got}\n     want: {want}")
            print(f"  FAIL {name}")
        else:
            print(f"  ok   {name}")

    tmp = Path(tempfile.mkdtemp(prefix="rosetta-config-selftest-"))
    try:
        home = tmp / "device-home"
        proj = tmp / "proj"
        (proj / ".git").mkdir(parents=True)
        central = tmp / "central"
        central.mkdir()

        print("── tier 4: builtin default (no config anywhere) ──")
        r = Resolver(proj, home)
        check("research -> project", r.resolve("research")["dir"],
              str(proj / ".rosetta" / "research"))
        check("tier is builtin", r.resolve("research")["tier"], "builtin-default")
        check("docs path", r.artifact("docs", "ai-sdk")["path"],
              str(proj / ".rosetta" / "docs" / "ai-sdk"))
        check("docs file", r.artifact("docs", "ai-sdk", "fact-graph.json")["path"],
              str(proj / ".rosetta" / "docs" / "ai-sdk" / "fact-graph.json"))
        check("research file", r.artifact("research", "ai-sdk")["path"],
              str(proj / ".rosetta" / "research" / "ai-sdk.md"))

        print("── tier 3: device config ──")
        _write(home / "config.json",
               {"version": 1, "roots": {"default": "project", "research": str(central)}})
        r = Resolver(proj, home)
        check("research -> central", r.resolve("research")["dir"],
              str(central / ".rosetta" / "research"))
        check("device tier", r.resolve("research")["tier"], "device")
        check("docs still project (default)", r.resolve("docs")["dir"],
              str(proj / ".rosetta" / "docs"))

        print("── device root form has no nested .rosetta ──")
        _write(home / "config.json", {"version": 1, "roots": {"default": "device"}})
        r = Resolver(proj, home)
        check("device layout", r.resolve("probes")["dir"], str(home / "probes"))

        print("── tier 2: project config beats device ──")
        _write(home / "config.json", {"version": 1, "roots": {"research": str(central)}})
        _write(proj / CONFIG_RELPATH, {"version": 1, "roots": {"research": "project"}})
        r = Resolver(proj, home)
        check("project wins", r.resolve("research")["dir"],
              str(proj / ".rosetta" / "research"))
        check("project tier", r.resolve("research")["tier"], "project")

        print("── class beats default within a tier ──")
        _write(proj / CONFIG_RELPATH,
               {"version": 1, "roots": {"default": str(central), "research": "project"}})
        r = Resolver(proj, home)
        check("research uses class key", r.resolve("research")["dir"],
              str(proj / ".rosetta" / "research"))
        check("probes uses default key", r.resolve("probes")["dir"],
              str(central / ".rosetta" / "probes"))

        print("── tier 1: CLI flag beats everything ──")
        cli_dir = tmp / "cli"
        cli_dir.mkdir()
        r = Resolver(proj, home, cli_roots={"research": str(cli_dir)})
        check("cli wins", r.resolve("research")["dir"], str(cli_dir / ".rosetta" / "research"))
        check("cli tier", r.resolve("research")["tier"], "cli")

        print("── degradation: malformed JSON warns and falls through ──")
        _write(proj / CONFIG_RELPATH, "{ this is not json")
        _write(home / "config.json", {"version": 1, "roots": {"research": str(central)}})
        r = Resolver(proj, home)
        check("fell through to device", r.resolve("research")["dir"],
              str(central / ".rosetta" / "research"))
        check("warning recorded", any("invalid JSON" in w for w in r.warnings), True)

        print("── degradation: non-dict config is skipped ──")
        _write(proj / CONFIG_RELPATH, "[1,2,3]")
        r = Resolver(proj, home)
        check("skipped list config", r.resolve("research")["dir"],
              str(central / ".rosetta" / "research"))

        print("── degradation: unwritable root falls back to project ──")
        locked = tmp / "locked"
        (locked / ".rosetta").mkdir(parents=True)
        os.chmod(locked / ".rosetta", 0o500)
        _write(proj / CONFIG_RELPATH, {"version": 1, "roots": {"research": str(locked)}})
        r = Resolver(proj, home)
        got = r.resolve("research")
        check("fell back to project", got["dir"], str(proj / ".rosetta" / "research"))
        check("fallback flagged", got["fallback"], True)
        check("fallback tier", got["tier"], "project-fallback")
        os.chmod(locked / ".rosetta", 0o700)

        print("── read side: find() searches configured, then project, then device ──")
        _write(proj / CONFIG_RELPATH, {"version": 1, "roots": {"default": str(central)}})
        r = Resolver(proj, home)
        check("missing everywhere -> None", r.find("docs", "nope"), None)
        legacy = proj / ".rosetta" / "docs" / "legacy-kb"
        legacy.mkdir(parents=True)
        found = r.find("docs", "legacy-kb")
        check("finds project-local legacy KB", found and found["path"], str(legacy))
        check("records which root answered", found and found["tier"], "search:project")
        preferred = central / ".rosetta" / "docs" / "legacy-kb"
        preferred.mkdir(parents=True)
        found = r.find("docs", "legacy-kb")
        check("configured root wins once populated", found and found["path"], str(preferred))

        print("── project root detection walks up to .git ──")
        deep = proj / "a" / "b" / "c"
        deep.mkdir(parents=True)
        check("nested cwd anchors at repo root", Resolver(deep, home).project, proj)

        print("── init is idempotent and never clobbers ──")
        h2 = tmp / "home2"
        cmd_init(h2)
        _write(h2 / "config.json", {"version": 1, "roots": {"default": "device"}, "mine": True})
        cmd_init(h2)
        kept = json.loads((h2 / "config.json").read_text())
        check("existing config preserved", kept.get("mine"), True)

        print("── CLI subprocess: path prints exactly one line ──")
        import subprocess
        env = dict(os.environ, ROSETTA_HOME=str(home))
        out = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "path",
             "--class", "research", "--slug", "demo", "--project", str(proj)],
            capture_output=True, text=True, env=env)
        check("exit 0", out.returncode, 0)
        check("single line", len(out.stdout.strip().splitlines()), 1)
        check("expected path", out.stdout.strip(),
              str(central / ".rosetta" / "research" / "demo.md"))

        print("── --ensure creates the directory ──")
        target = Path(r.artifact("research", "made")["path"]).parent
        if target.exists():
            shutil.rmtree(target)
        subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "path", "--class", "research",
             "--slug", "made", "--project", str(proj), "--ensure"],
            capture_output=True, text=True, env=env)
        check("dir created", target.is_dir(), True)

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
        description="Resolve where Rosetta artifacts are stored (project / device / any path).")
    ap.add_argument("--selftest", action="store_true", help="run real-filesystem selftests")
    sub = ap.add_subparsers(dest="verb")

    def common(p, slug=False):
        p.add_argument("--class", dest="cls", required=True, choices=CLASSES)
        if slug:
            p.add_argument("--slug", required=True)
            p.add_argument("--file", dest="filename", default=None,
                           help="file within a docs/<slug>/ directory")
        p.add_argument("--project", default=None, help="project dir (default: cwd)")
        p.add_argument("--root", default=None, help="override every class")
        for c in CLASSES:
            p.add_argument(f"--{c}-root", dest=f"{c}_root", default=None)
        p.add_argument("--ensure", action="store_true", help="create the directory if missing")

    common(sub.add_parser("path", help="absolute path for one artifact"), slug=True)
    common(sub.add_parser("root", help="absolute directory for one artifact class"))
    common(sub.add_parser("find", help="read side: first existing copy, searching all roots"),
           slug=True)

    p_show = sub.add_parser("show", help="resolved roots + provenance")
    p_show.add_argument("--project", default=None)
    p_show.add_argument("--root", default=None)
    p_show.add_argument("--format", dest="fmt", choices=("json", "plain"), default="json",
                        help="plain = one 'class dir [tier]' line per class")
    for c in CLASSES:
        p_show.add_argument(f"--{c}-root", dest=f"{c}_root", default=None)

    sub.add_parser("init", help="create $ROSETTA_HOME with a default config (idempotent)")

    p_doc = sub.add_parser("doctor", help="report stray artifacts; move them with --apply")
    p_doc.add_argument("--project", default=None)
    p_doc.add_argument("--scan-root", action="append", default=None,
                       help="where to look for strays (repeatable; default ~/Projects)")
    p_doc.add_argument("--apply", action="store_true", help="actually move (default: dry run)")
    p_doc.add_argument("--exclude", action="append", default=None,
                       help="repo to leave alone (repeatable), e.g. a plugin's own dogfood tree")

    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.verb:
        ap.print_help()
        return 2

    if args.verb == "init":
        return cmd_init(rosetta_home())

    cli_roots = {"default": getattr(args, "root", None)}
    for c in CLASSES:
        cli_roots[c] = getattr(args, f"{c}_root", None)
    res = Resolver(Path(args.project) if args.project else None, None, cli_roots)

    if args.verb == "doctor":
        scans = [Path(s) for s in (args.scan_root or [str(Path.home() / "Projects")])]
        excl = [Path(e) for e in (args.exclude or [])]
        return cmd_doctor(res, scans, args.apply, excl)

    if args.verb == "show":
        data = res.show()
        if args.fmt == "plain":
            for cls, r in data["roots"].items():
                # Show BOTH what decided it and what it decided — "[device]" alone is ambiguous
                # when a device config sets the value "project".
                why = f"{r['tier']}: {r['config_key']} = {r['root_value']}"
                note = "  (FELL BACK — configured root not writable)" if r["fallback"] else ""
                print(f"  {cls:<9} {r['dir']}\n            [{why}]{note}")
        else:
            print(json.dumps(data, indent=2))
        return 0

    if args.verb == "root":
        r = res.resolve(args.cls)
        if args.ensure:
            Path(r["dir"]).mkdir(parents=True, exist_ok=True)
        print(r["dir"])
        return 0

    if args.verb == "path":
        r = res.artifact(args.cls, args.slug, args.filename)
        if args.ensure:
            parent = Path(r["path"]) if args.cls == "docs" and not args.filename \
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
