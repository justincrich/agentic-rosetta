#!/usr/bin/env python3
"""Source identity, digest, version derivation, kb_version bump, staleness probe.

Sibling of write-graph.py. The source manifest is NEVER written into fact-graph.json.

CLI:
  provenance.py --check-stale TECH [--root DIR]
    exit 0 current · 10 stale (names which sources moved) · 1 undeterminable
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

PROVENANCE_NAME = "provenance.json"
FACT_SOURCES_NAME = "fact-sources.json"
HISTORY_MANIFEST = Path(".history") / "manifest.json"

_VERSION_RE = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def corpus_digest(units: list[dict[str, Any]]) -> str:
    """SHA-256 over sorted ``(locator, unit_sha256)`` pairs. Pure — no I/O."""
    pairs = []
    for u in units:
        loc = str(u.get("locator") or u.get("url") or "")
        digest = str(u.get("sha256") or "")
        if loc and digest:
            pairs.append((loc, digest))
    pairs.sort(key=lambda p: p[0])
    return sha256_text(canonical_json(pairs))


def extract_manifest_version(repo_path: str | Path | None) -> tuple[str | None, str]:
    """Ordered evidence: package.json, Cargo.toml, pyproject.toml, build.gradle, git describe."""
    if not repo_path:
        return None, "undetermined"
    root = Path(repo_path)
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            ver = data.get("version")
            if isinstance(ver, str) and ver.strip() and ver.strip() != "0.0.0":
                return ver.strip(), str(pkg)
        except (OSError, json.JSONDecodeError):
            pass
    cargo = root / "Cargo.toml"
    if cargo.is_file():
        m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', cargo.read_text(encoding="utf-8"))
        if m and m.group(1) != "0.0.0":
            return m.group(1), str(cargo)
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"))
        if m and m.group(1) != "0.0.0":
            return m.group(1), str(pyproject)
    for gradle_name in ("build.gradle", "build.gradle.kts"):
        gradle = root / gradle_name
        if gradle.is_file():
            text = gradle.read_text(encoding="utf-8")
            m = re.search(r'''version\s*=\s*['"]([^'"]+)['"]''', text)
            if m and m.group(1) != "0.0.0":
                return m.group(1), str(gradle)
    describe = _git_describe(root)
    if describe:
        parsed = _looks_like_version(describe)
        if parsed:
            return parsed, f"git-describe:{describe}"
    return None, "undetermined"


def _git_describe(root: Path) -> str | None:
    r = subprocess.run(
        ["git", "-C", str(root), "describe", "--tags", "--always"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    out = (r.stdout or "").strip()
    return out or None


def _looks_like_version(text: str) -> str | None:
    m = _VERSION_RE.match(text.strip())
    if not m:
        return None
    major = m.group(1)
    minor = m.group(2) or "0"
    patch = m.group(3) or "0"
    return f"{major}.{minor}.{patch}"


def derive_upstream_version(
    pins: list[dict[str, Any]] | None = None,
    repo_path: str | Path | None = None,
    docs_text: str | None = None,
) -> tuple[str | None, str]:
    """Never invent ``0.0.0``. Missing evidence → ``(None, "undetermined")``."""
    if repo_path:
        ver, src = extract_manifest_version(repo_path)
        if ver:
            return ver, src
    for pin in pins or []:
        mv = pin.get("manifest_version")
        vs = pin.get("version_source")
        if isinstance(mv, str) and mv.strip() and mv.strip() != "0.0.0":
            return mv.strip(), str(vs or pin.get("kind") or "pin")
        if pin.get("kind") == "repo":
            desc = pin.get("describe")
            if isinstance(desc, str):
                parsed = _looks_like_version(desc)
                if parsed:
                    return parsed, f"git-describe:{desc}"
    if docs_text:
        for pat in (
            r'(?i)(?:current|latest)\s+version[:\s]+v?(\d+\.\d+(?:\.\d+)?)',
            r'(?i)version["\s:]+v?(\d+\.\d+\.\d+)',
        ):
            m = re.search(pat, docs_text)
            if m:
                return m.group(1), "docs-marker"
    return None, "undetermined"


def parse_semver(value: Any) -> tuple[int, int, int] | None:
    if value is None:
        return None
    m = _VERSION_RE.match(str(value).strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0)


def _inc(parts: list[int], idx: int) -> str:
    out = list(parts)
    while len(out) < 3:
        out.append(0)
    out[idx] += 1
    for j in range(idx + 1, len(out)):
        out[j] = 0
    return ".".join(str(p) for p in out[:3])


def parse_kb(kb: str) -> list[int]:
    bits = []
    for p in str(kb or "1.0.0").split("."):
        try:
            bits.append(int(re.sub(r"\D.*", "", p) or "0"))
        except ValueError:
            bits.append(0)
    return bits or [1, 0, 0]


def _verdict(val: Any) -> str | None:
    if isinstance(val, dict):
        v = val.get("verdict")
        return str(v) if v is not None else None
    if isinstance(val, str):
        return val
    return None


def r_flipped_to_reject(old_r: dict | None, new_r: dict | None) -> bool:
    old_r = old_r or {}
    new_r = new_r or {}
    for key, new_val in new_r.items():
        if str(key).endswith("_note"):
            continue
        new_v = _verdict(new_val)
        old_v = _verdict(old_r[key]) if key in old_r else None
        if new_v == "REJECT" and old_v != "REJECT":
            return True
    return False


def t_contract_changed(old_t: dict | None, new_t: dict | None) -> bool:
    old_t = old_t or {}
    new_t = new_t or {}
    if set(old_t) - set(new_t):
        return True
    for key in set(old_t) & set(new_t):
        if old_t[key] != new_t[key]:
            return True
    return False


_V_BOOKKEEPING = {"kb_version", "pinned_date", "pinned", "version_source", "supersedes", "version"}


def keys_added(old_graph: dict, new_graph: dict) -> bool:
    for sec in ("v", "w", "r", "a", "x", "t"):
        old_keys = set((old_graph.get(sec) or {}))
        new_keys = set((new_graph.get(sec) or {}))
        if sec == "v":
            old_keys -= _V_BOOKKEEPING
            new_keys -= _V_BOOKKEEPING
        if new_keys - old_keys:
            return True
    return False


def bump_kb_version(
    old_kb: str,
    old_graph: dict,
    new_graph: dict,
    old_upstream: Any = None,
    new_upstream: Any = None,
) -> str:
    """major / minor / patch from the section diff — never a model choice."""
    parts = parse_kb(old_kb)
    old_s = parse_semver(old_upstream)
    new_s = parse_semver(new_upstream)
    if old_s and new_s and new_s[0] != old_s[0]:
        return _inc(parts, 0)
    if r_flipped_to_reject(old_graph.get("r"), new_graph.get("r")):
        return _inc(parts, 0)
    if t_contract_changed(old_graph.get("t"), new_graph.get("t")):
        return _inc(parts, 0)
    if keys_added(old_graph, new_graph):
        return _inc(parts, 1)
    if old_s and new_s and new_s != old_s:
        return _inc(parts, 1)
    return _inc(parts, 2)


def parse_nrra_json_block(text: str, heading: str) -> dict[str, Any] | None:
    """Pull the first fenced json block under a heading that contains *heading*."""
    pat = re.compile(
        rf"## [^\n]*{re.escape(heading)}[^\n]*\n[\s\S]*?```json\n([\s\S]*?)```",
        re.I,
    )
    m = pat.search(text)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_fact_sources(nrra_text: str) -> dict[str, Any]:
    block = parse_nrra_json_block(nrra_text, "Fact Sources")
    return block or {}


def parse_provenance_block(nrra_text: str) -> dict[str, Any]:
    block = parse_nrra_json_block(nrra_text, "Provenance")
    return block or {}


def split_source(val: Any) -> tuple[Any, str | None, str | None]:
    """Peel ``source`` / ``confidence`` off a changeset entry for fact-sources.json."""
    if not isinstance(val, dict) or "source" not in val:
        return val, None, None
    src = val.get("source")
    conf = val.get("confidence")
    core = {k: v for k, v in val.items() if k not in ("source", "confidence")}
    if set(core) == {"value"}:
        core = core["value"]
    return core, (str(src) if src is not None else None), (str(conf) if conf is not None else None)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def source_urls(pins: list[dict[str, Any]] | None, extra: list[str] | None = None) -> list[str]:
    urls: list[str] = []
    for pin in pins or []:
        u = pin.get("url")
        if isinstance(u, str) and u and u not in urls:
            urls.append(u)
    for u in extra or []:
        if u and u not in urls:
            urls.append(u)
    return urls


def rollup_corpus(pins: list[dict[str, Any]]) -> str:
    if not pins:
        return corpus_digest([])
    if len(pins) == 1:
        return str(pins[0].get("corpus_sha256") or corpus_digest(pins[0].get("unitPins") or []))
    units = []
    for pin in pins:
        for u in pin.get("unitPins") or []:
            units.append(u)
        if not pin.get("unitPins") and pin.get("corpus_sha256"):
            units.append({"locator": pin.get("url") or pin.get("kind"), "sha256": pin["corpus_sha256"]})
    return corpus_digest(units)


def build_provenance(
    *,
    kb_version: str,
    upstream_version: Any,
    pins: list[dict[str, Any]],
    status: str = "current",
    graph_sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "kb_version": kb_version,
        "upstream_version": upstream_version,
        "corpus_sha256": rollup_corpus(pins) if pins else None,
        "sources": pins,
        "status": status,
        "graph_sha256": graph_sha256,
        "date": date.today().isoformat(),
    }


def write_siblings(
    kb_dir: Path,
    *,
    provenance: dict[str, Any],
    fact_sources: dict[str, Any],
) -> None:
    write_json(kb_dir / PROVENANCE_NAME, provenance)
    write_json(kb_dir / FACT_SOURCES_NAME, fact_sources)
    append_history(kb_dir, {
        "kb_version": provenance.get("kb_version"),
        "date": provenance.get("date") or date.today().isoformat(),
        "upstream_version": provenance.get("upstream_version"),
        "corpus_sha256": provenance.get("corpus_sha256"),
        "sources": source_urls(provenance.get("sources") or []),
        "graph_sha256": provenance.get("graph_sha256"),
        "status": provenance.get("status") or "written",
    })


def append_history(kb_dir: Path, entry: dict[str, Any]) -> None:
    path = kb_dir / HISTORY_MANIFEST
    data: dict[str, Any] = {"entries": []}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                data = {"entries": loaded}
            elif isinstance(loaded, dict):
                data = loaded
                data.setdefault("entries", [])
        except json.JSONDecodeError:
            data = {"entries": []}
    data["entries"].append(entry)
    write_json(path, data)


def prepend_migration(mig: Path, heading: str, body_lines: list[str], sources: list[str]) -> None:
    block = f"## {heading}\n\n"
    if body_lines:
        block += "\n".join(body_lines) + "\n"
    block += f"**Sources:** {', '.join(sources) if sources else '(none)'}\n\n"
    existing = mig.read_text(encoding="utf-8") if mig.is_file() else ""
    mig.write_text(block + existing, encoding="utf-8")


_KB_LINE = re.compile(r"^- \*\*(?:KB version|[^*]+)\*\*:.*(?:KB version|\*\*KB version\*\*).*$", re.M)
_KB_SIMPLE = re.compile(r"^- \*\*KB version\*\*:.*$", re.M)


def format_readme_kb_line(graph: dict[str, Any], provenance: dict[str, Any] | None) -> str:
    v = graph.get("v") or {}
    kb = v.get("kb_version") or "unknown"
    upstream = v.get("version")
    up = "undetermined" if upstream is None else str(upstream)
    corpus = ""
    if provenance:
        corpus = str(provenance.get("corpus_sha256") or "")[:8]
    pinned = v.get("pinned_date") or v.get("pinned") or ""
    return (
        f"- **KB version**: {kb} · **upstream**: {up} · "
        f"**corpus**: {corpus or '—'} · **pinned**: {pinned}"
    )


def upsert_readme_kb_line(kb_dir: Path, graph: dict[str, Any], provenance: dict[str, Any] | None) -> None:
    readme = kb_dir / "README.md"
    if not readme.is_file():
        return
    line = format_readme_kb_line(graph, provenance)
    text = readme.read_text(encoding="utf-8")
    new = _KB_SIMPLE.sub(line, text, count=1)
    if new == text:
        new = _KB_LINE.sub(line, text, count=1)
    if new == text:
        if "## Version" in text:
            new = text.replace("## Version", "## Version\n\n" + line, 1)
        else:
            new = text.rstrip() + "\n\n## Version\n\n" + line + "\n"
    if new != text:
        readme.write_text(new, encoding="utf-8")


# --------------------------------------------------------------------------- stale probe

def _ls_remote(url: str, ref: str) -> tuple[str | None, str]:
    r = subprocess.run(
        ["git", "ls-remote", url, ref],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not (r.stdout or "").strip():
        r = subprocess.run(
            ["git", "ls-remote", url, "HEAD"],
            capture_output=True, text=True,
        )
    if r.returncode != 0:
        return None, (r.stderr or r.stdout or "ls-remote failed").strip()
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    if not lines:
        return None, "ls-remote empty"
    return lines[0].split()[0], ""


def _check_repo_pin(pin: dict[str, Any]) -> tuple[str, str]:
    url = pin.get("url")
    stored = pin.get("commit")
    if not url or not stored:
        return "undeterminable", "repo pin missing url or commit"
    ref = pin.get("ref") or "HEAD"
    remote, err = _ls_remote(str(url), str(ref))
    if not remote:
        return "undeterminable", err
    if remote == stored:
        return "current", ""
    return "stale", f"repo {url} moved {str(stored)[:12]} → {remote[:12]}"


def _conditional_get(url: str, etag: str | None, stored_hash: str | None) -> tuple[str, str]:
    req = urllib.request.Request(url, method="GET")
    if etag:
        req.add_header("If-None-Match", etag)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if getattr(resp, "status", 200) == 304:
                return "current", ""
            body = resp.read()
            digest = hashlib.sha256(body).hexdigest()
            if stored_hash and digest == stored_hash:
                return "current", ""
            return "stale", f"docs {url} body changed"
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return "current", ""
        return "undeterminable", f"docs {url}: HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return "undeterminable", f"docs {url}: {e}"


def _check_docs_pin(pin: dict[str, Any]) -> tuple[str, str]:
    units = list(pin.get("unitPins") or [])
    if pin.get("url") and (pin.get("etag") or pin.get("sha256")):
        units = [{
            "locator": pin.get("url"),
            "etag": pin.get("etag"),
            "sha256": pin.get("sha256"),
        }] + units
    if not units and pin.get("url") and pin.get("corpus_sha256"):
        units = [{"locator": pin["url"], "sha256": pin["corpus_sha256"]}]
    if not units:
        return "undeterminable", "docs pin has no units or etag"
    worst = "current"
    notes: list[str] = []
    for u in units:
        url = u.get("locator") or u.get("url")
        if not url:
            worst = "undeterminable"
            notes.append("docs unit missing locator")
            continue
        state, note = _conditional_get(str(url), u.get("etag"), u.get("sha256"))
        if state == "stale":
            return "stale", note
        if state == "undeterminable":
            worst = "undeterminable"
            if note:
                notes.append(note)
    return worst, "; ".join(notes)


def check_stale(provenance: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    pins = provenance.get("sources") or provenance.get("pins") or []
    if not pins:
        return 1, {"status": "undeterminable", "reason": "no source pins", "moved": []}
    moved: list[str] = []
    undet: list[str] = []
    for pin in pins:
        kind = pin.get("kind")
        if kind == "legacy" or pin.get("status") == "legacy-unknown":
            undet.append("legacy-unknown pin")
            continue
        if kind == "repo":
            state, note = _check_repo_pin(pin)
        elif kind == "docs":
            state, note = _check_docs_pin(pin)
        else:
            state, note = "undeterminable", f"unknown pin kind {kind!r}"
        if state == "stale":
            moved.append(note)
        elif state == "undeterminable":
            undet.append(note)
    if moved:
        return 10, {"status": "stale", "moved": moved, "undeterminable": undet}
    if undet:
        return 1, {"status": "undeterminable", "reason": "; ".join(undet), "moved": []}
    return 0, {"status": "current", "moved": []}


def load_provenance(root: Path, tech: str) -> dict[str, Any]:
    path = root / ".rosetta" / "docs" / tech / PROVENANCE_NAME
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check-stale", metavar="TECH")
    p.add_argument("--root", default=None)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    root = Path(args.root).expanduser().resolve() if args.root else Path(
        os.environ.get("ROSETTA_PROJECT") or Path.cwd()
    ).resolve()
    if args.check_stale:
        try:
            prov = load_provenance(root, args.check_stale)
        except FileNotFoundError as e:
            print(json.dumps({"status": "undeterminable", "reason": f"missing {e}"}))
            return 1
        except json.JSONDecodeError as e:
            print(json.dumps({"status": "undeterminable", "reason": str(e)}))
            return 1
        code, report = check_stale(prov)
        print(json.dumps(report, indent=2, sort_keys=True))
        return code
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
