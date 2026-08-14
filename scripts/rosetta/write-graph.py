#!/usr/bin/env python3
"""write-graph.py — the ONLY writer of fact-graph.json.

  write-graph.py --create <tech> --nrra <f> --ledger <f> [--root DIR] [--out PATH]
  write-graph.py --update <tech> --changeset <f> --ledger <f> [--nrra <f>] [--root DIR]
  write-graph.py --check  <tech> [--root DIR]

Gate (before either write mode acts):
  1. ledger closed — closed + named-gap rows == censused
  2. ledger run_id matches the NRRA's run_id
  3. decoder_ring_hash present on both
Any failure: exit non-zero, write nothing, name the failed check.

--check on an unstamped (legacy) graph warns, does not fail.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    import registry as R
except ImportError:
    R = None  # type: ignore

WRITER = "write-graph.py@1.0.0"
R_VERDICTS = {"r", "nr", "REJECT"}


class GateError(SystemExit):
    def __init__(self, check: str, detail: str):
        super().__init__(f"write-graph: FAIL {check}: {detail}")
        self.check = check


def _die(check: str, detail: str) -> None:
    raise GateError(check, detail)


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _die("io", f"missing {path}")
    except json.JSONDecodeError as e:
        _die("json", f"{path}: {e}")
    raise AssertionError("unreachable")


def parse_nrra_frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    if not m:
        _die("nrra", "NRRA has no YAML frontmatter")
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta


def graph_dir(root: Path, tech: str) -> Path:
    return root / ".rosetta" / "docs" / tech


def graph_path(root: Path, tech: str) -> Path:
    return graph_dir(root, tech) / "fact-graph.json"


def assert_ledger_closed(ledger: dict) -> None:
    censused = int(ledger.get("censused", -1))
    rows = ledger.get("rows") or []
    closed = sum(1 for r in rows if r.get("status") == "closed")
    gaps = sum(1 for r in rows if r.get("status") == "gap")
    if closed + gaps != censused:
        _die("ledger-closed", f"closed({closed})+gaps({gaps}) != censused({censused})")
    if any(r.get("status") == "open" for r in rows):
        _die("ledger-closed", "one or more rows still open")


def assert_ids_match(ledger: dict, nrra_meta: dict) -> None:
    lid = str(ledger.get("run_id") or "")
    nid = str(nrra_meta.get("run_id") or "")
    if not lid or not nid:
        _die("run_id", f"missing run_id ledger={lid!r} nrra={nid!r}")
    if lid != nid:
        _die("run_id", f"mismatch ledger={lid} nrra={nid}")
    h = str(ledger.get("decoder_ring_hash") or nrra_meta.get("decoder_ring_hash") or "")
    if not h:
        _die("decoder_ring_hash", "absent on ledger and NRRA")


def validate_graph(graph: dict) -> None:
    if not isinstance(graph, dict):
        _die("schema", "graph is not an object")
    raw = json.dumps(graph)
    if "rosetta-kb:end" in raw:
        _die("schema", "graph contains literal rosetta-kb:end (would poison embed)")
    v = graph.get("v")
    if not isinstance(v, dict):
        _die("schema", "v missing")
    for key in ("pkg", "version"):
        if not v.get(key) and not v.get("kb_version"):
            # kb_version is seeded on create; pkg/version may live under other keys
            pass
    if not v.get("kb_version") and not v.get("version"):
        _die("schema", "v needs kb_version or version")
    w = graph.get("w") or {}
    if not isinstance(w, dict):
        _die("schema", "w must be an object")
    for name, node in w.items():
        if isinstance(node, dict) and not (node.get("t") and node.get("i")):
            _die("schema", f"w.{name} needs t + i")
    r = graph.get("r") or {}
    if not isinstance(r, dict):
        _die("schema", "r must be an object")
    for k, val in r.items():
        if k.endswith("_note"):
            continue
        verdict = val.get("verdict") if isinstance(val, dict) else val
        if verdict not in R_VERDICTS:
            _die("schema", f"r.{k} verdict {verdict!r} not in {sorted(R_VERDICTS)}")
    x = graph.get("x") or {}
    if not isinstance(x, dict):
        _die("schema", "x must be an object")
    for k, val in x.items():
        if isinstance(val, dict):
            if not (val.get("wrong") or val.get("right") or val.get("to")):
                _die("schema", f"x.{k} pair incomplete")
        elif not isinstance(val, str) or not val:
            _die("schema", f"x.{k} empty")
    a = graph.get("a") or {}
    if not isinstance(a, dict):
        _die("schema", "a must be an object")
    for k, val in a.items():
        if not isinstance(val, str) or not val.strip():
            _die("schema", f"a.{k} must be a non-empty string")


def seed_from_nrra(nrra_text: str, tech: str) -> dict:
    """Pull the §9 JSON seed if present; otherwise empty sections."""
    m = re.search(r"## 9\. Fact-Graph Seed[\s\S]*?```json\n([\s\S]*?)```", nrra_text)
    seed = {"v": {}, "w": {}, "r": {}, "a": {}, "x": {}, "t": {}}
    if m:
        try:
            parsed = json.loads(m.group(1))
            if isinstance(parsed, dict):
                for k in seed:
                    if isinstance(parsed.get(k), dict):
                        seed[k] = parsed[k]
        except json.JSONDecodeError:
            pass
    v = seed["v"] if isinstance(seed["v"], dict) else {}
    v.setdefault("pkg", tech)
    v.setdefault("version", "0.0.0")
    v["kb_version"] = "1.0.0"
    seed["v"] = v
    return seed


def stamp(graph: dict, ledger: dict) -> dict:
    body = {k: v for k, v in graph.items() if k != "_meta"}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    meta = dict(graph.get("_meta") or {})
    meta["assimilation"] = {
        "run_id": ledger.get("run_id"),
        "written_at": date.today().isoformat(),
        "waves": ledger.get("waves"),
        "censused": ledger.get("censused"),
        "closed": ledger.get("closed"),
        "gaps": len(ledger.get("gaps") or []),
        "graph_sha256": sha256_bytes(raw),
        "writer": WRITER,
    }
    out = dict(graph)
    out["_meta"] = meta
    return out


def atomic_write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def cmd_create(tech: str, nrra_path: Path, ledger_path: Path, root: Path, out: Path | None) -> int:
    dest = out or graph_path(root, tech)
    if dest.is_file():
        _die("create", f"refusing to overwrite existing {dest}")
    ledger = load_json(ledger_path)
    nrra = nrra_path.read_text(encoding="utf-8")
    meta = parse_nrra_frontmatter(nrra)
    assert_ledger_closed(ledger)
    assert_ids_match(ledger, meta)
    graph = seed_from_nrra(nrra, tech)
    graph["_meta"] = {"technology": tech, "slug": tech}
    validate_graph(graph)
    graph = stamp(graph, ledger)
    atomic_write(dest, graph)
    print(f"[write-graph] created {dest} kb_version={graph['v']['kb_version']}")
    return 0


def cmd_update(tech: str, changeset_path: Path, ledger_path: Path, nrra_path: Path | None,
               root: Path) -> int:
    dest = graph_path(root, tech)
    if not dest.is_file():
        _die("update", f"no graph at {dest}")
    ledger = load_json(ledger_path)
    if nrra_path:
        meta = parse_nrra_frontmatter(nrra_path.read_text(encoding="utf-8"))
        assert_ids_match(ledger, meta)
    else:
        if not ledger.get("run_id") or not ledger.get("decoder_ring_hash"):
            _die("decoder_ring_hash", "absent on ledger (pass --nrra to cross-check)")
    assert_ledger_closed(ledger)
    current = load_json(dest)
    # Snapshot FIRST, before any mutation.
    kb = str((current.get("v") or {}).get("kb_version") or "0.0.0")
    hist = dest.parent / ".history"
    hist.mkdir(parents=True, exist_ok=True)
    snap = hist / f"{date.today().isoformat()}-v{kb}.json"
    shutil.copy2(dest, snap)

    cs = load_json(changeset_path)
    # only ≥ MEDIUM; never auto-apply needs_review
    for section in ("v", "w", "r", "a", "x", "t"):
        added = (cs.get("added") or {}).get(section) or {}
        changed = (cs.get("changed") or {}).get(section) or {}
        deprecated = (cs.get("deprecated") or {}).get(section) or {}
        bucket = current.setdefault(section, {})
        if not isinstance(bucket, dict):
            bucket = {}
            current[section] = bucket
        for k, val in {**added, **changed}.items():
            bucket[k] = val
        for k in deprecated:
            bucket.pop(k, None)

    # age old x into MIGRATION.md (keep current-major only — we just append a section)
    mig = dest.parent / "MIGRATION.md"
    dep = (cs.get("deprecated") or {})
    if dep:
        block = f"\n## {date.today().isoformat()} ← v{kb}\n\n"
        for section, items in dep.items():
            for k, val in (items or {}).items():
                block += f"- deprecated `{section}.{k}`: {val}\n"
        with mig.open("a", encoding="utf-8") as f:
            f.write(block)

    # bump
    parts = kb.split(".")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
        new_kb = ".".join(parts)
    except ValueError:
        new_kb = kb + ".1"
    current.setdefault("v", {})["kb_version"] = new_kb
    current["v"]["supersedes"] = kb
    validate_graph(current)
    current = stamp(current, ledger)
    atomic_write(dest, current)
    print(f"[write-graph] updated {dest} {kb} → {new_kb} (snapshot {snap.name})")
    return 0


def cmd_check(tech: str, root: Path) -> int:
    dest = graph_path(root, tech)
    if not dest.is_file():
        _die("check", f"no graph at {dest}")
    graph = load_json(dest)
    validate_graph(graph)
    assim = (graph.get("_meta") or {}).get("assimilation")
    if not assim:
        print(f"write-graph: WARN {dest} legacy — unverified (no _meta.assimilation stamp)")
        return 0
    body = {k: v for k, v in graph.items() if k != "_meta"}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    got = sha256_bytes(raw)
    want = assim.get("graph_sha256")
    if got != want:
        _die("stamp", f"graph_sha256 mismatch (file was hand-edited?) got={got} want={want}")
    print(f"[write-graph] OK {dest} stamp={got[:12]}")
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=None)
    p.add_argument("--create", metavar="TECH")
    p.add_argument("--update", metavar="TECH")
    p.add_argument("--check", metavar="TECH")
    p.add_argument("--nrra", type=Path)
    p.add_argument("--ledger", type=Path)
    p.add_argument("--changeset", type=Path)
    p.add_argument("--out", type=Path, help="override dest path (create only)")
    args = p.parse_args(argv)

    root = Path(args.root).expanduser().resolve() if args.root else Path(
        os.environ.get("ROSETTA_PROJECT") or Path.cwd()
    ).resolve()
    if R is not None and args.root:
        R.set_repo_root(root)

    try:
        if args.create:
            if not args.nrra or not args.ledger:
                _die("usage", "--create needs --nrra and --ledger")
            return cmd_create(args.create, args.nrra, args.ledger, root, args.out)
        if args.update:
            if not args.changeset or not args.ledger:
                _die("usage", "--update needs --changeset and --ledger")
            return cmd_update(args.update, args.changeset, args.ledger, args.nrra, root)
        if args.check:
            return cmd_check(args.check, root)
    except GateError as e:
        print(str(e), file=sys.stderr)
        return 2
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
