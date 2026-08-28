#!/usr/bin/env python3
"""write-graph.py — the ONLY writer of fact-graph.json.

  write-graph.py --create <tech> --nrra <f> --ledger <f> [--root DIR] [--out PATH] [--source-repo DIR]
  write-graph.py --update <tech> --changeset <f> --ledger <f> [--nrra <f>] [--root DIR]
  write-graph.py --apply-nrra <tech> --nrra <f> --ledger <f> [--root DIR] [--out PATH] [--source-repo DIR]
  write-graph.py --check  <tech> [--root DIR]
  write-graph.py --adopt  <tech> [--root DIR]
  write-graph.py --backfill [TECH] [--all] [--root DIR] [--sources FILE]

Gate (before create/update/apply-nrra acts):
  1. ledger closed — closed + named-gap rows == censused
  2. ledger run_id matches the NRRA's run_id
  3. decoder_ring_hash present on both
Any failure: exit non-zero, write nothing, name the failed check.

--apply-nrra: if dest is absent, same as --create; if present, additive-merge seed
keys only (no deletes). Reports v.pkg/v.version moves; does not write them.
# kb:ceiling: nested callers need an explicit --no-graph
# idempotent apply alone cannot preserve upgrade's accept/improve/roll-back gate

--adopt stamps recoverable fields on a legacy graph; no ledger required.
--backfill harvests on-disk locators, merges an optional judged --sources overlay,
and writes now-pins (status=backfill-now). The overlay is agent-produced (search +
judgment); this writer never invents URLs.
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
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    import registry as R
except ImportError:
    R = None  # type: ignore

import provenance as P
import roots as RT

WRITER = "write-graph.py@1.1.0"
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


# Set by main() when the docs directory came from rosetta_config.py rather than from --root.
# None => <root>/.rosetta/docs. An explicit --root leaves this None, so every caller that pins a
# root keeps byte-identical behavior.
_DOCS_DIR: Path | None = None


def docs_dir(root: Path) -> Path:
    return RT.docs_dir(root, _DOCS_DIR)


def graph_dir(root: Path, tech: str) -> Path:
    return docs_dir(root) / tech


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
    if not v.get("kb_version"):
        _die("schema", "v needs kb_version")
    if v.get("version") == "0.0.0":
        _die("schema", 'v.version must not be the fake "0.0.0"')
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
    t = graph.get("t")
    if t is None:
        graph["t"] = {}
        t = graph["t"]
    if not isinstance(t, dict):
        _die("schema", "t must be an object")
    for k, val in t.items():
        if isinstance(val, str):
            if not val.strip():
                _die("schema", f"t.{k} must be a non-empty string")
        elif isinstance(val, dict):
            if not val:
                _die("schema", f"t.{k} empty")
        else:
            _die("schema", f"t.{k} must be a string or object")


def seed_from_nrra(nrra_text: str, tech: str) -> dict:
    """Pull the §9 JSON seed if present; otherwise empty sections. Never seeds 0.0.0."""
    m = re.search(r"## 9\. Fact-Graph Seed[\s\S]*?```json\n([\s\S]*?)```", nrra_text)
    seed: dict = {"v": {}, "w": {}, "r": {}, "a": {}, "x": {}, "t": {}}
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
    if v.get("version") == "0.0.0":
        v.pop("version", None)
    v["kb_version"] = "1.0.0"
    seed["v"] = v
    return seed


def apply_derived_version(graph: dict, pins: list[dict], repo_path: Path | None) -> None:
    ver, src = P.derive_upstream_version(pins, repo_path=repo_path)
    v = graph.setdefault("v", {})
    v["version"] = ver
    v["version_source"] = src
    v["pinned_date"] = date.today().isoformat()


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


def graph_sha(graph: dict) -> str:
    body = {k: v for k, v in graph.items() if k != "_meta"}
    return sha256_bytes(json.dumps(body, sort_keys=True, separators=(",", ":")).encode())


def atomic_write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def persist_siblings(
    dest: Path,
    graph: dict,
    pins: list[dict],
    fact_sources: dict,
    status: str,
) -> dict:
    kb_dir = dest.parent
    v = graph.get("v") or {}
    prov = P.build_provenance(
        kb_version=str(v.get("kb_version") or ""),
        upstream_version=v.get("version"),
        pins=pins,
        status=status,
        graph_sha256=graph_sha(graph),
    )
    P.write_siblings(kb_dir, provenance=prov, fact_sources=fact_sources)
    P.upsert_readme_kb_line(kb_dir, graph, prov)
    return prov


def nrra_pins_and_sources(nrra_text: str) -> tuple[list[dict], dict]:
    prov = P.parse_provenance_block(nrra_text)
    pins = list(prov.get("pins") or prov.get("sources") or [])
    sources = P.parse_fact_sources(nrra_text)
    return pins, sources if isinstance(sources, dict) else {}


def cmd_create(
    tech: str,
    nrra_path: Path,
    ledger_path: Path,
    root: Path,
    out: Path | None,
    source_repo: Path | None,
) -> int:
    dest = out or graph_path(root, tech)
    if dest.is_file():
        _die("create", f"refusing to overwrite existing {dest}")
    ledger = load_json(ledger_path)
    nrra = nrra_path.read_text(encoding="utf-8")
    meta = parse_nrra_frontmatter(nrra)
    assert_ledger_closed(ledger)
    assert_ids_match(ledger, meta)
    graph = seed_from_nrra(nrra, tech)
    pins, fact_sources = nrra_pins_and_sources(nrra)
    apply_derived_version(graph, pins, source_repo)
    graph["_meta"] = {"technology": tech, "slug": tech}
    validate_graph(graph)
    graph = stamp(graph, ledger)
    atomic_write(dest, graph)
    persist_siblings(dest, graph, pins, fact_sources, status="created")
    print(f"[write-graph] created {dest} kb_version={graph['v']['kb_version']}")
    return 0


def cmd_update(
    tech: str,
    changeset_path: Path,
    ledger_path: Path,
    nrra_path: Path | None,
    root: Path,
    source_repo: Path | None,
) -> int:
    dest = graph_path(root, tech)
    if not dest.is_file():
        _die("update", f"no graph at {dest}")
    ledger = load_json(ledger_path)
    pins: list[dict] = []
    extra_sources: dict = {}
    if nrra_path:
        nrra_text = nrra_path.read_text(encoding="utf-8")
        meta = parse_nrra_frontmatter(nrra_text)
        assert_ids_match(ledger, meta)
        pins, extra_sources = nrra_pins_and_sources(nrra_text)
    else:
        if not ledger.get("run_id") or not ledger.get("decoder_ring_hash"):
            _die("decoder_ring_hash", "absent on ledger (pass --nrra to cross-check)")
    assert_ledger_closed(ledger)
    current = load_json(dest)
    old_snapshot = json.loads(json.dumps(current))
    kb = str((current.get("v") or {}).get("kb_version") or "1.0.0")
    hist = dest.parent / ".history"
    hist.mkdir(parents=True, exist_ok=True)
    snap = hist / f"{date.today().isoformat()}-v{kb}.json"
    shutil.copy2(dest, snap)

    cs = load_json(changeset_path)
    fact_sources: dict[str, dict] = {}
    existing_fs = dest.parent / P.FACT_SOURCES_NAME
    if existing_fs.is_file():
        try:
            loaded = json.loads(existing_fs.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                fact_sources = loaded
        except json.JSONDecodeError:
            fact_sources = {}
    fact_sources.update(extra_sources)

    for section in ("v", "w", "r", "a", "x", "t"):
        added = (cs.get("added") or {}).get(section) or {}
        changed = (cs.get("changed") or {}).get(section) or {}
        deprecated = (cs.get("deprecated") or {}).get(section) or {}
        bucket = current.setdefault(section, {})
        if not isinstance(bucket, dict):
            bucket = {}
            current[section] = bucket
        for k, val in {**added, **changed}.items():
            core, src, conf = P.split_source(val)
            bucket[k] = core
            if src:
                fact_sources[f"{section}:{k}"] = {
                    "source": src,
                    "confidence": conf or "MEDIUM",
                }
        for k in deprecated:
            bucket.pop(k, None)
            fact_sources.pop(f"{section}:{k}", None)

    dep = cs.get("deprecated") or {}
    if dep:
        lines = []
        for section, items in dep.items():
            for k, val in (items or {}).items():
                lines.append(f"- deprecated `{section}.{k}`: {val}")
        srcs = P.source_urls(pins)
        P.prepend_migration(
            dest.parent / "MIGRATION.md",
            f"{date.today().isoformat()} ← v{kb}",
            lines,
            srcs,
        )

    apply_derived_version(current, pins, source_repo)
    old_up = (old_snapshot.get("v") or {}).get("version")
    new_up = (current.get("v") or {}).get("version")
    new_kb = P.bump_kb_version(kb, old_snapshot, current, old_up, new_up)
    current.setdefault("v", {})["kb_version"] = new_kb
    current["v"]["supersedes"] = kb
    validate_graph(current)
    current = stamp(current, ledger)
    atomic_write(dest, current)
    persist_siblings(dest, current, pins, fact_sources, status="updated")
    print(f"[write-graph] updated {dest} {kb} → {new_kb} (snapshot {snap.name})")
    return 0


def cmd_apply_nrra(
    tech: str,
    nrra_path: Path,
    ledger_path: Path,
    root: Path,
    out: Path | None,
    source_repo: Path | None,
) -> int:
    """Create-or-additive-merge from an NRRA seed. Nested bootstrap/upgrade pass --no-graph."""
    # kb:ceiling: nested callers need an explicit --no-graph
    # idempotent apply alone cannot preserve upgrade's accept/improve/roll-back gate
    dest = out or graph_path(root, tech)
    if not dest.is_file():
        return cmd_create(tech, nrra_path, ledger_path, root, out, source_repo)

    ledger = load_json(ledger_path)
    nrra = nrra_path.read_text(encoding="utf-8")
    meta = parse_nrra_frontmatter(nrra)
    assert_ledger_closed(ledger)
    assert_ids_match(ledger, meta)

    seed = seed_from_nrra(nrra, tech)
    pins, extra_sources = nrra_pins_and_sources(nrra)
    current = load_json(dest)
    old_snapshot = json.loads(json.dumps(current))
    kb = str((current.get("v") or {}).get("kb_version") or "1.0.0")
    hist = dest.parent / ".history"
    hist.mkdir(parents=True, exist_ok=True)
    snap = hist / f"{date.today().isoformat()}-v{kb}.json"
    shutil.copy2(dest, snap)

    fact_sources: dict[str, dict] = {}
    existing_fs = dest.parent / P.FACT_SOURCES_NAME
    if existing_fs.is_file():
        try:
            loaded = json.loads(existing_fs.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                fact_sources = loaded
        except json.JSONDecodeError:
            fact_sources = {}
    fact_sources.update(extra_sources)

    cur_v = current.get("v") if isinstance(current.get("v"), dict) else {}
    seed_v = seed.get("v") if isinstance(seed.get("v"), dict) else {}
    for pin_key in ("pkg", "version"):
        seed_val = seed_v.get(pin_key)
        cur_val = cur_v.get(pin_key)
        if seed_val is not None and seed_val != cur_val:
            print(
                f"[write-graph] pin move reported (not written): v.{pin_key} "
                f"{cur_val!r} → {seed_val!r} (use upgrade Mode B)",
                file=sys.stderr,
            )

    for section in ("v", "w", "r", "a", "x", "t"):
        seed_bucket = seed.get(section) or {}
        if not isinstance(seed_bucket, dict):
            continue
        bucket = current.setdefault(section, {})
        if not isinstance(bucket, dict):
            bucket = {}
            current[section] = bucket
        for k, val in seed_bucket.items():
            if section == "v" and k in ("pkg", "version", "kb_version",
                                        "version_source", "pinned_date", "supersedes"):
                continue
            core, src, conf = P.split_source(val)
            bucket[k] = core
            if src:
                fact_sources[f"{section}:{k}"] = {
                    "source": src,
                    "confidence": conf or "MEDIUM",
                }

    old_up = (old_snapshot.get("v") or {}).get("version")
    new_up = (current.get("v") or {}).get("version")
    new_kb = P.bump_kb_version(kb, old_snapshot, current, old_up, new_up)
    current.setdefault("v", {})["kb_version"] = new_kb
    if new_kb != kb:
        current["v"]["supersedes"] = kb
    validate_graph(current)
    current = stamp(current, ledger)
    atomic_write(dest, current)
    persist_siblings(dest, current, pins, fact_sources, status="updated")
    print(f"[write-graph] apply-nrra {dest} {kb} → {new_kb} (snapshot {snap.name})")
    return 0


def _readme_kb(kb_dir: Path) -> str | None:
    readme = kb_dir / "README.md"
    if not readme.is_file():
        return None
    m = re.search(r"\*\*KB version\*\*:\s*([0-9]+(?:\.[0-9]+)*)", readme.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def list_docs_techs(root: Path) -> list[str]:
    docs = docs_dir(root)
    if not docs.is_dir():
        return []
    techs: list[str] = []
    for path in sorted(docs.iterdir()):
        if path.is_dir() and (path / "fact-graph.json").is_file():
            techs.append(path.name)
    return techs


def ensure_adopt_fields(graph: dict, kb_dir: Path) -> bool:
    """Stamp recoverable v fields without writing a legacy pin. Does not bump kb_version."""
    v = graph.setdefault("v", {})
    if not isinstance(v, dict):
        _die("backfill", "v is not an object")
    changed = False
    if not v.get("kb_version"):
        v["kb_version"] = _readme_kb(kb_dir) or "1.0.0"
        changed = True
    if not v.get("pinned_date"):
        v["pinned_date"] = str(v.get("pinned") or date.today().isoformat())
        changed = True
    ver = v.get("version")
    if ver in ("", "0.0.0"):
        v["version"] = None
        v["version_source"] = "legacy-unknown"
        changed = True
    elif ver is not None and "version_source" not in v:
        v["version_source"] = "legacy-unknown"
        changed = True
    if "t" not in graph or graph.get("t") is None:
        graph["t"] = {}
        changed = True
    return changed


def apply_overlay_version(graph: dict, entry: dict[str, Any] | None) -> bool:
    """Use a judged live version only when the graph has none. Never overwrite a real pin."""
    if not isinstance(entry, dict):
        return False
    judged = entry.get("version")
    if not isinstance(judged, str) or not judged.strip() or judged.strip() == "0.0.0":
        return False
    v = graph.setdefault("v", {})
    src = entry.get("version_source") or entry.get("docs_url") or entry.get("repo_url") or "overlay"
    if v.get("version") in (None, "", "0.0.0"):
        v["version"] = judged.strip()
        v["version_source"] = str(src)
        return True
    if v.get("version_source") in (None, "", "legacy-unknown"):
        v["version_source"] = str(src)
        return True
    return False


def cmd_backfill(tech: str, root: Path, overlay: dict | None = None) -> int:
    dest = graph_path(root, tech)
    if not dest.is_file():
        _die("backfill", f"no graph at {dest}")
    graph = load_json(dest)
    entry = (overlay or {}).get(tech) if overlay else None
    changed = ensure_adopt_fields(graph, dest.parent)
    if apply_overlay_version(graph, entry if isinstance(entry, dict) else None):
        changed = True
    if changed:
        try:
            validate_graph(graph)
        except GateError as exc:
            print(f"write-graph: WARN backfill schema ({tech}): {exc}", file=sys.stderr)
        atomic_write(dest, graph)
    planned = P.plan_backfill(
        dest.parent, graph, root, seeds=P.load_seeds(), overlay=overlay,
    )
    persist_siblings(
        dest, graph, planned["pins"], planned["fact_sources"], status=planned["status"],
    )
    n_fail = len(planned.get("failures") or [])
    print(
        f"[backfill] {tech} status={planned['status']} "
        f"pins={len(planned['pins'])} failures={n_fail}"
    )
    if planned.get("failures"):
        for fail in planned["failures"]:
            print(f"  fail {fail.get('kind')} {fail.get('url')}: {fail.get('reason')}")
    return 0


def cmd_adopt(tech: str, root: Path) -> int:
    dest = graph_path(root, tech)
    if not dest.is_file():
        _die("adopt", f"no graph at {dest}")
    graph = load_json(dest)
    v = graph.setdefault("v", {})
    if not isinstance(v, dict):
        _die("adopt", "v is not an object")
    if not v.get("kb_version"):
        v["kb_version"] = _readme_kb(dest.parent) or "1.0.0"
    if not v.get("pinned_date"):
        v["pinned_date"] = str(v.get("pinned") or date.today().isoformat())
    ver = v.get("version")
    if ver in (None, "", "0.0.0"):
        v["version"] = None
        v["version_source"] = "legacy-unknown"
    else:
        v.setdefault("version_source", "legacy-unknown")
    graph.setdefault("t", {})
    try:
        validate_graph(graph)
    except GateError as e:
        # Legacy graphs may predate current section checks; still stamp recoverables.
        print(f"write-graph: WARN adopt schema ({tech}): {e}", file=sys.stderr)
    atomic_write(dest, graph)
    pins = [{
        "kind": "legacy",
        "status": "legacy-unknown",
        "fetched_at": date.today().isoformat(),
        "units": 0,
        "corpus_sha256": P.corpus_digest([]),
    }]
    persist_siblings(dest, graph, pins, {}, status="legacy-unknown")
    print(f"[write-graph] adopted {dest} kb_version={v['kb_version']} version_source={v['version_source']}")
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
    p.add_argument("--apply-nrra", metavar="TECH")
    p.add_argument("--check", metavar="TECH")
    p.add_argument("--adopt", metavar="TECH")
    p.add_argument("--backfill", metavar="TECH", nargs="?", const="*")
    p.add_argument("--all", action="store_true")
    p.add_argument("--sources", type=Path, help="judged overlay JSON {slug: {docs_url, repo_url, version?}}")
    p.add_argument("--nrra", type=Path)
    p.add_argument("--ledger", type=Path)
    p.add_argument("--changeset", type=Path)
    p.add_argument("--out", type=Path, help="override dest path (create / apply-nrra create path)")
    p.add_argument("--source-repo", type=Path, help="local tree used to derive v.version")
    args = p.parse_args(argv)

    # Where the KB goes is a CONFIGURED decision, not "wherever the shell happens to be
    # standing". RT.choose lets --root/$ROSETTA_PROJECT win, else asks rosetta_config.py.
    global _DOCS_DIR
    root, _DOCS_DIR = RT.choose(args.root)
    if R is not None and args.root:
        R.set_repo_root(root)
    if _DOCS_DIR is not None:
        if _DOCS_DIR != root / ".rosetta" / "docs":
            print(f"write-graph: docs root from config: {_DOCS_DIR} (cwd is {root})",
                  file=sys.stderr)
        RT.warn_if_orphaned(_DOCS_DIR)

    try:
        verbs = [v for v in (args.create, args.update, args.apply_nrra, args.check, args.adopt)
                 if v] + ([args.backfill] if args.backfill is not None else [])
        if len(verbs) > 1:
            _die("usage", "pass exactly one of --create/--update/--apply-nrra/--check/--adopt/--backfill")
        if args.create:
            if not args.nrra or not args.ledger:
                _die("usage", "--create needs --nrra and --ledger")
            return cmd_create(args.create, args.nrra, args.ledger, root, args.out, args.source_repo)
        if args.apply_nrra:
            if not args.nrra or not args.ledger:
                _die("usage", "--apply-nrra needs --nrra and --ledger")
            return cmd_apply_nrra(
                args.apply_nrra, args.nrra, args.ledger, root, args.out, args.source_repo,
            )
        if args.update:
            if not args.changeset or not args.ledger:
                _die("usage", "--update needs --changeset and --ledger")
            return cmd_update(args.update, args.changeset, args.ledger, args.nrra, root, args.source_repo)
        if args.adopt:
            return cmd_adopt(args.adopt, root)
        if args.backfill is not None:
            techs = (
                list_docs_techs(root)
                if (args.all or args.backfill == "*")
                else [args.backfill]
            )
            if not techs:
                _die("backfill", f"no fact-graph.json under {docs_dir(root)}")
            overlay = P.load_overlay(args.sources) if args.sources else None
            rc = 0
            for tech in techs:
                try:
                    got = cmd_backfill(tech, root, overlay=overlay)
                except GateError as exc:
                    print(str(exc), file=sys.stderr)
                    got = 2
                except Exception as exc:  # noqa: BLE001 — one KB must not abort --all
                    print(f"write-graph: FAIL backfill {tech}: {exc}", file=sys.stderr)
                    got = 2
                if got != 0:
                    rc = got
            return rc
        if args.check:
            return cmd_check(args.check, root)
    except GateError as e:
        print(str(e), file=sys.stderr)
        return 2
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
