#!/usr/bin/env python3
"""write-graph.py — the ONLY writer of fact-graph.json.

  write-graph.py --create <tech> --nrra <f> --ledger <f> [--composed-with a,b] [--root DIR] [--out PATH] [--source-repo DIR]
  write-graph.py --apply-nrra <tech> --nrra <f> --ledger <f> [--root DIR] [--out PATH] [--source-repo DIR]
  write-graph.py --update <tech> --changeset <f> --ledger <f> [--nrra <f>] [--composed-with a,b] [--root DIR]
  write-graph.py --check  <tech> [--root DIR]
  write-graph.py --check-composed <tech> [--root DIR]
  write-graph.py --propagate <graph> [--list] [--borrowers a,b] [--force] --confirm [--root DIR]
  write-graph.py --compose <host-tech> --from <slug>[,<slug>...] [--force] [--root DIR]
  write-graph.py --adopt  <tech> [--root DIR]
  write-graph.py --backfill [TECH] [--all] [--root DIR] [--sources FILE]

Gate (before create/update/apply-nrra acts):
  1. ledger closed — closed + named-gap rows == censused
  2. ledger run_id matches the NRRA's run_id
  3. decoder_ring_hash present on both
Any failure: exit non-zero, write nothing, name the failed check.

--apply-nrra: dest missing → same persist as --create. dest present → add/change-only
seed merge (no deletes). v.pkg / v.version moves are printed, not written.
deprecated / needs_review from an NRRA seed are never applied.

--adopt stamps recoverable fields on a legacy graph; no ledger required.
--backfill harvests on-disk locators, merges an optional judged --sources overlay,
and writes now-pins (status=backfill-now). The overlay is agent-produced (search +
judgment); this writer never invents URLs.
--check on an unstamped (legacy) graph warns, does not fail.

--composed-with copies the named graphs' v/w/r/a/x/t facts into this graph's body by
value (``composed[<slug>]``) and rebuilds ``_meta.composed_with`` from that copy. It is an
input to ``--create`` and ``--update`` only (composing two graphs that already exist is an
``--update``); on any other verb it is a usage error, never a silent no-op.
--propagate lists every borrower and its editions, then re-copies one original's facts into
them; it asks update-all / a named subset / skip and refuses to write without ``--confirm``
when stdin is not a terminal. ``--list`` prints the table and exits without prompting or
writing; a borrower already current is skipped unless ``--force``.
--compose copies named originals into an ALREADY EXISTING host graph, with no census ledger:
composing one KB into another that already exists is maintenance, not research, and
manufacturing a ledger to satisfy the create/update gate would be a fake research artifact.
It never creates a graph, refuses a diverged copy without ``--force`` (exit 10, writes
nothing), and skips a host already current at the same edition unless ``--force``. Source
graphs are read, never written.
--check-composed reports each copy's recorded vs current edition and exits 10 when any is
not current (0 current, 1 composes nothing, 2 a real error).
"""
from __future__ import annotations

import argparse
import json
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

import factgraph
import provenance as P
import roots as RT
from jsonfile import body_digest, read_json, sha256_bytes, write_json
from version_label import kb_edition
from versions import (
    COMPOSED_DIVERGED,
    COMPOSED_FRESH,
    SECTIONS,
    bump_kb_version,
    composed_freshness,
    composed_slugs,
    declared_composed_slugs,
)

WRITER = "write-graph.py@1.3.0"


class GateError(SystemExit):
    def __init__(self, check: str, detail: str):
        super().__init__(f"write-graph: FAIL {check}: {detail}")
        self.check = check


def _die(check: str, detail: str) -> None:
    raise GateError(check, detail)


def load_json(path: Path) -> dict:
    """Strict load, translated to this CLI's GateError vocabulary."""
    try:
        return read_json(path)
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
    """The write gate. Owned by factgraph.py; translated to this CLI's GateError.

    Kept as a named function rather than inlined because ``--check``, ``--adopt``,
    ``--apply-nrra`` and ``--update`` all catch ``GateError`` to warn-and-continue on a
    legacy graph. That control flow is this script's, not the schema's.
    """
    try:
        factgraph.validate(graph)
    except factgraph.SchemaError as exc:
        raise GateError(exc.check, exc.detail) from exc


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
    if ver is None:
        # No version evidence in the incoming research (e.g. a Mode C --update
        # whose changeset carries no v changes). Never demote an existing pin
        # to null/"undetermined"; leave an already-stamped source/date alone.
        if not v.get("version_source"):
            v["version_source"] = src
        if not v.get("pinned_date"):
            v["pinned_date"] = date.today().isoformat()
        return
    v["version"] = ver
    v["version_source"] = src
    v["pinned_date"] = date.today().isoformat()


def stamp(graph: dict, ledger: dict) -> dict:
    meta = dict(graph.get("_meta") or {})
    meta["assimilation"] = {
        "run_id": ledger.get("run_id"),
        "written_at": date.today().isoformat(),
        "waves": ledger.get("waves"),
        "censused": ledger.get("censused"),
        "closed": ledger.get("closed"),
        "gaps": len(ledger.get("gaps") or []),
        "graph_sha256": body_digest(graph),
        "writer": WRITER,
    }
    out = dict(graph)
    out["_meta"] = meta
    return out


def graph_sha(graph: dict) -> str:
    """Content identity of a graph. See jsonfile.body_digest for the ascii caveat."""
    return body_digest(graph)


def restamp(graph: dict) -> dict:
    """Refresh the integrity stamp after a body-only rewrite (composition propagation).

    Same keys ``stamp()`` writes. Only the fields a propagate actually changes are
    touched, so the run_id / censused provenance of the last research run survives.
    """
    meta = dict(graph.get("_meta") or {})
    assimilation = dict(meta.get("assimilation") or {})
    assimilation["graph_sha256"] = body_digest(graph)
    assimilation["written_at"] = date.today().isoformat()
    assimilation["writer"] = WRITER
    meta["assimilation"] = assimilation
    out = dict(graph)
    out["_meta"] = meta
    return out


def restamp_composed(graph: dict) -> dict:
    """Re-stamp a graph whose body changed by composition, inventing no census data.

    A stamped graph keeps every research field (``run_id``, ``waves``, ``censused``) and gets
    a fresh digest, so ``--check`` stays green. A legacy graph with no
    ``_meta.assimilation`` is left unstamped on purpose: ``--check``'s WARN path is how that
    graph is reported, and a compose must not manufacture a stamp it cannot back.
    """
    if not ((graph.get("_meta") or {}).get("assimilation")):
        return graph
    return restamp(graph)


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


def resolve_composed_with(root: Path, slugs: list[str]) -> list[tuple[str, dict]]:
    """Resolve composition sources to ``(slug, graph)`` — fail closed on unknown slugs.

    A composed-with slug naming a graph that does not exist in this project's docs root is
    a typo, not a soft warning: the resulting KB would claim a composition it cannot back.
    The loaded graph is what gets copied by value, so resolution and copy read one file."""
    out: list[tuple[str, dict]] = []
    for slug in slugs:
        p = graph_path(root, slug)
        if not p.is_file():
            _die("composed-with", f"composed graph {slug!r} has no fact-graph at {p}")
        out.append((slug, load_json(p)))
    return out


def cmd_create(
    tech: str,
    nrra_path: Path,
    ledger_path: Path,
    root: Path,
    out: Path | None,
    source_repo: Path | None,
    composed_with: list[str] | None = None,
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
    if composed_with:
        # Copy BY VALUE into the body. `_meta` is stripped from every render, so the
        # declaration alone would reach no agent; sync_composed_with then rebuilds the
        # declaration from the copy, leaving one record of which edition was taken.
        for slug, original in resolve_composed_with(root, composed_with):
            graph = factgraph.compose_by_value(graph, original, slug)
        graph = factgraph.sync_composed_with(graph)
    validate_graph(graph)
    graph = stamp(graph, ledger)
    write_json(dest, graph)
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
    composed_with: list[str] | None = None,
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
    # A declaration is a promise that the original lives in this project. If it does not,
    # stop instead of refreshing a KB that claims a composition nothing can back (AC-3).
    absent = [s for s in declared_composed_slugs(current) if not graph_path(root, s).is_file()]
    if absent:
        _die("composed-with",
             f"declared composed graph(s) absent from the project: {', '.join(absent)}")
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

    for section in SECTIONS:
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
    composed_reports: list[str] = []
    if composed_with:
        # Composing an EXISTING graph is the normal case (`--update herdrdev --composed-with
        # rust`): both graphs predate the composition. Copy through the same path `--create`
        # uses — resolve fail-closed, then copy by value — so there is one implementation of
        # the copy. The copy lands before the bump, so the host edition records that its
        # borrowed edition changed, and before sync_composed_with rebuilds the mirror.
        for slug, original in resolve_composed_with(root, composed_with):
            current = factgraph.compose_by_value(current, original, slug)
            composed_reports.append(
                f"[write-graph] composed {tech} <- {slug} "
                f"kb_version={kb_edition(original)}"
            )
        current = factgraph.sync_composed_with(current)
    old_up = (old_snapshot.get("v") or {}).get("version")
    new_up = (current.get("v") or {}).get("version")
    new_kb = bump_kb_version(kb, old_snapshot, current, old_up, new_up)
    current.setdefault("v", {})["kb_version"] = new_kb
    current["v"]["supersedes"] = kb
    validate_graph(current)
    current = stamp(current, ledger)
    write_json(dest, current)
    persist_siblings(dest, current, pins, fact_sources, status="updated")
    for line in composed_reports:
        print(line)
    print(f"[write-graph] updated {dest} {kb} → {new_kb} (snapshot {snap.name})")
    return 0


# Pin fields research must not silently move; bookkeeping is owned by stamp/bump.
_APPLY_V_SKIP = frozenset({
    "pkg", "version", "kb_version", "pinned_date", "pinned", "version_source", "supersedes",
})
_PIN_KEYS = ("pkg", "version")


def cmd_apply_nrra(
    tech: str,
    nrra_path: Path,
    ledger_path: Path,
    root: Path,
    out: Path | None,
    source_repo: Path | None,
) -> int:
    dest = out or graph_path(root, tech)
    if not dest.is_file():
        return cmd_create(tech, nrra_path, ledger_path, root, out, source_repo)

    ledger = load_json(ledger_path)
    nrra = nrra_path.read_text(encoding="utf-8")
    meta = parse_nrra_frontmatter(nrra)
    assert_ledger_closed(ledger)
    assert_ids_match(ledger, meta)

    current = load_json(dest)
    old_snapshot = json.loads(json.dumps(current))
    kb = str((current.get("v") or {}).get("kb_version") or "1.0.0")
    hist = dest.parent / ".history"
    hist.mkdir(parents=True, exist_ok=True)
    snap = hist / f"{date.today().isoformat()}-v{kb}.json"
    shutil.copy2(dest, snap)

    seed = seed_from_nrra(nrra, tech)
    pins, extra_sources = nrra_pins_and_sources(nrra)
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

    pin_reports: list[str] = []
    for section in SECTIONS:
        incoming = seed.get(section) or {}
        if not isinstance(incoming, dict):
            continue
        bucket = current.setdefault(section, {})
        if not isinstance(bucket, dict):
            bucket = {}
            current[section] = bucket
        for k, val in incoming.items():
            if section == "v" and k in _APPLY_V_SKIP:
                if k in _PIN_KEYS and bucket.get(k) != val:
                    pin_reports.append(f"v.{k}: {bucket.get(k)!r} → {val!r} (not written)")
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
    new_kb = bump_kb_version(kb, old_snapshot, current, old_up, new_up)
    current.setdefault("v", {})["kb_version"] = new_kb
    current["v"]["supersedes"] = kb
    validate_graph(current)
    current = stamp(current, ledger)
    write_json(dest, current)
    persist_siblings(dest, current, pins, fact_sources, status="updated")
    for line in pin_reports:
        print(f"[write-graph] pin move {line}")
    print(f"[write-graph] applied {dest} {kb} → {new_kb} (snapshot {snap.name})")
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
        write_json(dest, graph)
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
    write_json(dest, graph)
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
    got = body_digest(graph)
    want = assim.get("graph_sha256")
    if got != want:
        _die("stamp", f"graph_sha256 mismatch (file was hand-edited?) got={got} want={want}")
    print(f"[write-graph] OK {dest} stamp={got[:12]}")
    return 0


# ─────────────────────────── composed sources ───────────────────────────

#: Exit code for "a composed copy is not current" — the repo's established stale code, shared
#: with ``provenance.py --check-stale`` and ``--propagate``'s divergence report.
EXIT_COMPOSED_STALE = 10


def cmd_check_composed(tech: str, root: Path) -> int:
    """Report each composed copy's edition; exit 10 when any copy is not current.

    Exit codes: 0 every copy is current, 1 the graph composes nothing, 10 at least one copy is
    stale / diverged / missing, 2 a real error (no graph, or the schema gate refused).
    Offline: every original is read from this project, never fetched (AC-8)."""
    dest = graph_path(root, tech)
    if not dest.is_file():
        _die("check", f"no graph at {dest}")
    graph = load_json(dest)
    validate_graph(graph)
    sources = composed_slugs(graph)
    if not sources:
        print(f"[write-graph] {tech} composes nothing")
        return 1
    problems: list[str] = []
    for slug in sources:
        path = graph_path(root, slug)
        original = load_json(path) if path.is_file() else None
        report = composed_freshness(graph, original, slug)
        print(
            f"[write-graph] {tech} <- {slug}: "
            f"recorded={report['recorded_kb_version'] or '-'} "
            f"current={report['current_kb_version'] or '-'} {report['status']}"
        )
        if report["status"] != COMPOSED_FRESH:
            problems.append(f"{slug}={report['status']}")
    if problems:
        print(f"[write-graph] STALE {tech}: {', '.join(problems)}", file=sys.stderr)
        return EXIT_COMPOSED_STALE
    print(f"[write-graph] OK {tech} composed sources current")
    return 0


def parse_borrower_selection(answer: str, borrowers: list[str]) -> list[str]:
    """``all``/empty → every borrower, ``skip`` → none, ``a,b`` → that named subset.

    Unknown names die: a silently ignored name would look like a refresh that ran."""
    text = (answer or "").strip().lower()
    if text in ("", "all"):
        return list(borrowers)
    if text in ("skip", "none", "no"):
        return []
    wanted = [part.strip() for part in text.split(",") if part.strip()]
    unknown = [name for name in wanted if name not in borrowers]
    if unknown:
        _die("composed-with", f"not a borrower of this graph: {', '.join(unknown)}")
    return wanted


def persist_after_compose(dest: Path, graph: dict, status: str = "propagated") -> None:
    """Rewrite the sibling files so provenance does not contradict the graph it describes."""
    kb_dir = dest.parent
    pins: list[dict] = []
    prov_path = kb_dir / P.PROVENANCE_NAME
    if prov_path.is_file():
        try:
            loaded = json.loads(prov_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                pins = list(loaded.get("sources") or [])
        except json.JSONDecodeError:
            pins = []
    fact_sources: dict = {}
    fs_path = kb_dir / P.FACT_SOURCES_NAME
    if fs_path.is_file():
        try:
            loaded_fs = json.loads(fs_path.read_text(encoding="utf-8"))
            if isinstance(loaded_fs, dict):
                fact_sources = loaded_fs
        except json.JSONDecodeError:
            fact_sources = {}
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


def cmd_propagate(
    original_slug: str,
    root: Path,
    subset: list[str] | None,
    confirm: bool,
    list_only: bool = False,
    force: bool = False,
) -> int:
    """Re-copy one original's facts into every borrower that declares it. Never unattended.

    ``list_only`` is the side-effect-free ``--list`` verb: print the table, exit 0, never
    prompt and never write. A borrower already at the original's current edition is skipped
    unless ``force`` — re-copying it would churn ``v.kb_version`` on an unchanged project.

    Exit 0 every selected borrower handled (refreshed, skipped as current, or skip chosen),
    1 nobody borrows it, 2 a gate refused, 10 at least one selected borrower was left alone
    because its copy diverged.
    """
    original_path = graph_path(root, original_slug)
    if not original_path.is_file():
        _die("composed-with",
             f"composed graph {original_slug!r} has no fact-graph at {original_path}")
    original = load_json(original_path)
    if R is None:
        _die("registry", "registry module unavailable — cannot find borrowers")
    found = R.find_borrowers(original_slug, root)
    borrowers = found["borrowers"]
    names = [borrower["graph"] for borrower in borrowers]

    print(f"[write-graph] {original_slug} kb_version={found['kb_version'] or '-'} "
          f"— {len(borrowers)} borrower(s)")
    for borrower in borrowers:
        print(f"  {borrower['graph']:28} recorded={borrower['recorded_kb_version'] or '-':12} "
              f"current={borrower['current_kb_version'] or '-':12} {borrower['status']}")
    if not borrowers:
        print(f"[write-graph] nobody composes {original_slug!r}; nothing to propagate")
        return 1
    if list_only:
        return 0

    # AC-5: propagation never runs unattended. The explicit argument is required for a
    # non-interactive run, and the terminal check is what makes that real rather than prose.
    if not confirm and not sys.stdin.isatty():
        _die("confirm",
             "confirmation required: propagation rewrites other knowledge bases; pass "
             "--confirm. A non-interactive run cannot ask update-all / a named subset / skip.")

    if subset:
        selected = parse_borrower_selection(",".join(subset), names)
    elif confirm:
        selected = list(names)  # default: update all
    else:
        print("Update all (default), a named subset (a,b), or skip? [all]: ",
              end="", flush=True)
        selected = parse_borrower_selection(sys.stdin.readline(), names)

    if not selected:
        print("[write-graph] skip — wrote nothing")
        return 0

    rc = 0
    for borrower in borrowers:
        name = borrower["graph"]
        if name not in selected:
            continue
        if borrower["status"] == COMPOSED_DIVERGED:
            # AC-4: report THAT the copy diverged, never which fact changed.
            print(f"[write-graph] SKIP {name}: copy fingerprint mismatch — its recorded copy "
                  f"diverged from its own facts (a local edit changed the copy); refusing to "
                  f"overwrite", file=sys.stderr)
            rc = 10
            continue
        if borrower["status"] == COMPOSED_FRESH and not force:
            # No-op churn guard: re-copying an identical body would still bump the host
            # edition (1.0.0 → 1.0.1 → 1.0.2 on an unchanged project) for nothing.
            print(f"[write-graph] SKIP {name}: already current at "
                  f"{borrower['recorded_kb_version'] or '-'}")
            continue
        dest = graph_path(root, name)
        graph = load_json(dest)
        old_kb = str((graph.get("v") or {}).get("kb_version") or "1.0.0")
        old_up = (graph.get("v") or {}).get("version")
        updated = factgraph.compose_by_value(graph, original, original_slug)
        updated = factgraph.sync_composed_with(updated)
        new_up = (updated.get("v") or {}).get("version")
        new_kb = bump_kb_version(old_kb, graph, updated, old_up, new_up)
        updated.setdefault("v", {})["kb_version"] = new_kb
        updated["v"]["supersedes"] = old_kb
        validate_graph(updated)
        updated = restamp(updated)
        write_json(dest, updated)
        persist_after_compose(dest, updated)
        print(f"[write-graph] propagated {original_slug} -> {name} {old_kb} -> {new_kb}")
    return rc


def cmd_compose(tech: str, sources: list[str], root: Path, force: bool = False) -> int:
    """Compose named originals into an existing host graph. No census ledger, no new graph.

    Composing Rust into an already-researched host is a maintenance action: both graphs
    exist, nothing is being researched. ``--create`` / ``--update`` gate on a closed census
    ledger, and fabricating one just to copy facts by value would be a fake research
    artifact — so this verb takes none. It reuses the ONE copy path
    (``resolve_composed_with`` → ``compose_by_value`` → ``sync_composed_with``) that
    ``--create``, ``--update`` and ``--propagate`` already use.

    Exit 0 composed or already current, 2 a gate refused (no host, unknown source, usage),
    10 a copy's recorded fingerprint no longer matches its facts and ``--force`` was absent.

    Divergence is checked for EVERY source before anything is written: a compose is one
    atomic write to one host, so a partially copied host is a state no later run repairs.
    """
    dest = graph_path(root, tech)
    if not dest.is_file():
        _die("compose", f"no graph at {dest}")
    current = load_json(dest)
    resolved = resolve_composed_with(root, sources)

    if not force:
        diverged = [
            slug for slug, original in resolved
            if composed_freshness(current, original, slug)["status"] == COMPOSED_DIVERGED
        ]
        if diverged:
            for slug in diverged:
                print(
                    f"[write-graph] SKIP {tech} <- {slug}: copy fingerprint mismatch — its "
                    f"recorded copy diverged from its own facts (a local edit changed the "
                    f"copy); refusing to overwrite (pass --force)", file=sys.stderr,
                )
            print(f"[write-graph] refused {tech}: copy fingerprint mismatch "
                  f"({', '.join(diverged)}); wrote nothing", file=sys.stderr)
            return EXIT_COMPOSED_STALE

    pending: list[tuple[str, dict]] = []
    for slug, original in resolved:
        report = composed_freshness(current, original, slug)
        if report["status"] == COMPOSED_FRESH and not force:
            # Same no-op churn guard as --propagate: an unchanged project must not bump.
            print(f"[write-graph] SKIP {tech}: already current at "
                  f"{report['recorded_kb_version'] or '-'}")
            continue
        pending.append((slug, original))
    if not pending:
        return 0

    old_snapshot = json.loads(json.dumps(current))
    old_kb = str((current.get("v") or {}).get("kb_version") or "1.0.0")
    old_up = (old_snapshot.get("v") or {}).get("version")
    for slug, original in pending:
        current = factgraph.compose_by_value(current, original, slug)
        print(f"[write-graph] composed {tech} <- {slug} kb_version={kb_edition(original)}")
    current = factgraph.sync_composed_with(current)
    # Composition is a content change, so it moves the host edition by the same rule every
    # other write uses — never a judgment call.
    new_up = (current.get("v") or {}).get("version")
    new_kb = bump_kb_version(old_kb, old_snapshot, current, old_up, new_up)
    current.setdefault("v", {})["kb_version"] = new_kb
    current["v"]["supersedes"] = old_kb
    validate_graph(current)
    current = restamp_composed(current)
    write_json(dest, current)
    persist_after_compose(dest, current, status="composed")
    print(f"[write-graph] composed {dest} {old_kb} -> {new_kb}")
    return 0


#: Verbs in the order ``main()`` dispatches them, with the name used in messages. One place
#: names the verb that will run, so a flag guard cannot drift from the dispatch it guards.
_VERB_ORDER = (
    ("create", "--create"),
    ("apply_nrra", "--apply-nrra"),
    ("update", "--update"),
    ("compose", "--compose"),
    ("adopt", "--adopt"),
    ("propagate", "--propagate"),
    ("check_composed", "--check-composed"),
    ("backfill", "--backfill"),
    ("check", "--check"),
)


def active_verb(args: argparse.Namespace) -> str | None:
    """The verb ``main()`` will dispatch, as its flag spelling, or ``None`` when none is set."""
    for attr, flag in _VERB_ORDER:
        if getattr(args, attr) is not None:
            return flag
    return None


def split_slugs(value: str | None) -> list[str]:
    """``"a, b ,"`` → ``["a", "b"]``. Empty input is no composition, not an empty slug."""
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=None)
    p.add_argument("--create", metavar="TECH")
    p.add_argument("--apply-nrra", metavar="TECH")
    p.add_argument("--update", metavar="TECH")
    p.add_argument("--check", metavar="TECH")
    p.add_argument("--check-composed", metavar="TECH",
                   help="fail when a graph's recorded composed editions are not the "
                        "originals' current ones (offline)")
    p.add_argument("--propagate", metavar="GRAPH",
                   help="re-copy a graph's facts into every borrower that declares it")
    p.add_argument("--compose", metavar="HOST",
                   help="compose --from sources into an existing graph by value; no census "
                        "ledger, never creates a graph, no-op when already current")
    p.add_argument("--from", dest="from_graphs", metavar="SLUGS",
                   help="comma-separated source graphs for --compose")
    p.add_argument("--borrowers", metavar="SLUGS",
                   help="comma-separated borrower subset for --propagate (default: all)")
    p.add_argument("--list", action="store_true",
                   help="with --propagate: print every borrower and the two editions, exit 0, "
                        "never prompt and never write")
    p.add_argument("--force", action="store_true",
                   help="with --propagate: also rewrite a borrower already current at the "
                        "original's edition (default: skip it); with --compose: also "
                        "overwrite a copy that is current or diverged")
    p.add_argument("--confirm", action="store_true",
                   help="explicit consent for --propagate; required when stdin is not a terminal")
    p.add_argument("--adopt", metavar="TECH")
    p.add_argument("--backfill", metavar="TECH", nargs="?", const="*")
    p.add_argument("--all", action="store_true")
    p.add_argument("--sources", type=Path, help="judged overlay JSON {slug: {docs_url, repo_url, version?}}")
    p.add_argument("--nrra", type=Path)
    p.add_argument("--ledger", type=Path)
    p.add_argument("--changeset", type=Path)
    p.add_argument("--out", type=Path, help="override dest path (create / apply-nrra)")
    p.add_argument("--source-repo", type=Path, help="local tree used to derive v.version")
    p.add_argument("--composed-with",
                   help="comma-separated graph slugs this KB composes (--create / --update "
                        "only); copied by value into composed[<slug>] and mirrored into "
                        "_meta.composed_with with each graph's current kb_version")
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
        composed = split_slugs(args.composed_with)
        sources = split_slugs(args.from_graphs)
        # --from belongs to --compose and nothing else. Accepting it anywhere would look like
        # a composition that never happened.
        if args.compose is None and args.from_graphs is not None:
            _die("usage", "--from is an input to --compose only; "
                         f"{active_verb(args) or 'no verb'} cannot take sources")
        if args.compose is not None:
            # One write path per invocation. Two verbs at once used to mean "whichever the
            # dispatch table hits first wins", which is not something a caller can rely on.
            others = [flag for attr, flag in _VERB_ORDER
                      if flag != "--compose" and getattr(args, attr) is not None]
            if others:
                _die("usage", f"--compose cannot be combined with {', '.join(others)}")
            # --compose composes by value from --from; it takes no research or edit input.
            foreign = [
                flag for attr, flag in (
                    ("nrra", "--nrra"), ("changeset", "--changeset"), ("ledger", "--ledger"),
                    ("out", "--out"), ("source_repo", "--source-repo"),
                    ("sources", "--sources"), ("borrowers", "--borrowers"),
                    ("all", "--all"), ("confirm", "--confirm"), ("list", "--list"),
                )
                if getattr(args, attr) not in (None, False)
            ]
            if foreign:
                _die("usage", f"--compose takes no {', '.join(foreign)}; it composes by "
                              f"value from --from only")
            if not sources:
                _die("usage", "--compose needs --from <slug>[,<slug>...]")
        # --composed-with is an input to a WRITE that composes. On a verb that cannot honour
        # it the flag used to be accepted and ignored: exit 0, a normal run, no composition
        # change — the exact silent fallback composition exists to prevent. Reject it instead.
        if args.composed_with is not None and active_verb(args) not in ("--create", "--update"):
            _die("usage",
                 f"--composed-with applies to --create and --update only; "
                 f"{active_verb(args) or 'no verb'} cannot compose")
        if args.compose is not None:
            return cmd_compose(args.compose, sources, root, force=args.force)
        if args.create:
            if not args.nrra or not args.ledger:
                _die("usage", "--create needs --nrra and --ledger")
            return cmd_create(args.create, args.nrra, args.ledger, root, args.out,
                              args.source_repo, composed_with=composed)
        if args.apply_nrra:
            if not args.nrra or not args.ledger:
                _die("usage", "--apply-nrra needs --nrra and --ledger")
            return cmd_apply_nrra(
                args.apply_nrra, args.nrra, args.ledger, root, args.out, args.source_repo,
            )
        if args.update:
            if not args.changeset or not args.ledger:
                _die("usage", "--update needs --changeset and --ledger")
            return cmd_update(args.update, args.changeset, args.ledger, args.nrra, root,
                              args.source_repo, composed_with=composed)
        if args.adopt:
            return cmd_adopt(args.adopt, root)
        if args.propagate:
            subset = [s.strip() for s in (args.borrowers or "").split(",") if s.strip()]
            return cmd_propagate(args.propagate, root, subset or None, args.confirm,
                                 list_only=args.list, force=args.force)
        if args.check_composed:
            return cmd_check_composed(args.check_composed, root)
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
