#!/usr/bin/env python3
"""Source identity, digest, version derivation, kb_version bump, staleness probe.

Sibling of write-graph.py. The source manifest is NEVER written into fact-graph.json.

CLI:
  provenance.py --check-stale TECH [--root DIR]
    exit 0 current · 10 stale (names which sources moved) · 1 undeterminable
  provenance.py --check-stale-fleet [--root DIR]
    one JSON report, one entry per KB the registry finds in the project
    exit 10 any stale · 1 any undeterminable or zero KBs · 0 all current
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _roots():
    """Lazy so importing provenance never depends on roots.py being importable."""
    import roots
    return roots


PROVENANCE_NAME = "provenance.json"
FACT_SOURCES_NAME = "fact-sources.json"
HISTORY_MANIFEST = Path(".history") / "manifest.json"
SEEDS_NAME = "backfill-seeds.json"
DOCS_MAX_UNITS = 40
USER_AGENT = "rosetta-provenance/1.0"
V_URL_KEYS = (
    "repo", "repo_url", "docs_url", "authoritative_cli_contract",
    "canonical_skill", "web_api_page",
)
META_URL_KEYS = ("repo", "docs", "docs_url", "source")
_URL_RE = re.compile(r"https?://[^\s)\]>\"'`,]+")
_HOST_RE = re.compile(
    r"(?<![\w./@-])((?:[A-Za-z0-9-]+\.)+(?:com|org|net|io|dev|ai|app|so|cloud|rs|sh|to|co|gg|ms|tv|me|info|edu|gov)(?:/[^\s)\]>\"'`,]*)?)",
    re.I,
)
_OK_TLDS = frozenset({
    "com", "org", "net", "io", "dev", "ai", "app", "so", "cloud", "rs", "sh",
    "to", "co", "gg", "ms", "tv", "me", "info", "edu", "gov",
})

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


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sanitize_url(raw: str | None) -> str | None:
    """Keep fetchable http(s)/file URLs. Drop localhost, templates, junk hosts."""
    text = (raw or "").strip()
    text = re.sub(r"\s+\(.*$", "", text)
    text = text.rstrip(".,;:)\"'")
    if not text:
        return None
    if any(ch in text for ch in "{}<> \t\n"):
        return None
    if text.startswith("file:"):
        return text
    if not re.match(r"^https?://", text, re.I):
        if re.match(r"^(?:github|gitlab|bitbucket)\.com/", text, re.I):
            text = "https://" + text
        elif re.match(r"^(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:[/?#]|$)", text):
            text = "https://" + text
        else:
            return None
    try:
        parsed = urllib.parse.urlparse(text)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    if host in {"localhost", "::1"}:
        return None
    try:
        ipaddress.ip_address(host)
        return text
    except ValueError:
        pass
    if "." not in host:
        return None
    if host.startswith("_") or host.endswith(".internal") or ".svc." in host:
        return None
    tld = host.rsplit(".", 1)[-1]
    if tld not in _OK_TLDS:
        return None
    # Scheme-less promotions: reject CamelCase code tokens (Dispatchers.IO, Cargo.to)
    # and toy two-letter hosts (e.me, t3.me). Explicit https:// keeps them if a TLD matches.
    original = (raw or "").strip()
    had_scheme = bool(re.match(r"^https?://", original, re.I))
    if not had_scheme:
        raw_host = original.split("/")[0].split(":")[0]
        if any(c.isupper() for c in raw_host):
            return None
        labels = host.split(".")
        if len(labels) < 2 or len(labels[0]) <= 2:
            return None
        if tld not in {"com", "org", "net", "io", "dev", "ai", "app", "cloud"}:
            return None
    return text


def classify_locator(url: str) -> str:
    """``repo`` for a git remote root; ``docs`` otherwise."""
    if url.startswith("file:"):
        return "repo"
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host in {"github.com", "gitlab.com", "bitbucket.org"}:
        parts = [p for p in parsed.path.split("/") if p]
        if parts and parts[-1].endswith(".git"):
            parts = parts[:-1] + [parts[-1][:-4]]
        if len(parts) == 2:
            return "repo"
    return "docs"


def resolve_local_path(src: str, root: Path | None, kb_dir: Path) -> Path | None:
    text = (src or "").strip()
    if not text or text.startswith(("http://", "https://", "file:")):
        return None
    text = re.sub(r":\d+$", "", text)
    candidates: list[Path] = []
    abs_p = Path(text)
    if abs_p.is_file():
        return abs_p.resolve()
    if root:
        candidates.append(root / text)
        if text.startswith("brain/docs/"):
            candidates.append(root / ".rosetta" / "docs" / text[len("brain/docs/"):])
        if text.startswith("docs/"):
            candidates.append(root / ".rosetta" / "docs" / text[len("docs/"):])
    candidates.append(kb_dir / text)
    candidates.append(kb_dir / Path(text).name)
    if text.startswith("docs/"):
        candidates.append(kb_dir / text[len("docs/"):])
    for cand in candidates:
        try:
            if cand.is_file():
                return cand.resolve()
        except OSError:
            continue
    return None


def _rel_locator(path: Path, root: Path | None, kb_dir: Path) -> str:
    resolved = path.resolve()
    if root:
        try:
            return resolved.relative_to(root.resolve()).as_posix()
        except ValueError:
            pass
    try:
        return resolved.relative_to(kb_dir.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def harvest_locators(kb_dir: Path, graph: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    """Collect locators from on-disk graph / README / history. No network."""
    docs: list[str] = []
    repos: list[str] = []
    local: list[dict[str, str]] = []
    fact_sources: dict[str, Any] = {}
    seen_http: set[str] = set()
    seen_local: set[str] = set()
    priority_docs: list[str] = []
    priority_repos: list[str] = []

    def add_http(raw: Any, *, priority: bool = False) -> None:
        url = sanitize_url(str(raw) if raw is not None else "")
        if not url or url in seen_http:
            return
        seen_http.add(url)
        kind = classify_locator(url)
        if kind == "repo":
            repos.append(url)
            if priority:
                priority_repos.append(url)
        else:
            docs.append(url)
            if priority:
                priority_docs.append(url)

    def add_fact(key: str, src: str) -> None:
        fact_sources[key] = src
        resolved = resolve_local_path(src, root, kb_dir)
        if not resolved:
            return
        loc = _rel_locator(resolved, root, kb_dir)
        if loc in seen_local:
            return
        seen_local.add(loc)
        local.append({"locator": loc, "path": str(resolved)})

    v = graph.get("v") or {}
    if isinstance(v, dict):
        for key in V_URL_KEYS:
            val = v.get(key)
            if isinstance(val, str) and val.strip():
                add_http(val, priority=True)

    meta = graph.get("_meta") or {}
    if isinstance(meta, dict):
        for key in META_URL_KEYS:
            val = meta.get(key)
            if isinstance(val, str):
                add_http(val)
        for item in meta.get("sources") or []:
            if isinstance(item, str):
                add_http(item)
            elif isinstance(item, dict):
                for val in item.values():
                    if isinstance(val, str):
                        add_http(val)

    hist = kb_dir / HISTORY_MANIFEST
    if hist.is_file():
        try:
            data = json.loads(hist.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        urls: list[Any] = []
        if isinstance(data, dict):
            urls.extend(data.get("source_urls") or [])
            for ref in (data.get("refreshes") or data.get("entries") or []):
                if isinstance(ref, dict):
                    urls.extend(ref.get("source_urls") or ref.get("sources") or [])
        elif isinstance(data, list):
            for ref in data:
                if isinstance(ref, dict):
                    urls.extend(ref.get("source_urls") or [])
        for url in urls:
            if isinstance(url, str):
                add_http(url)

    for name in ("README.md", "ROSETTA.md"):
        path = kb_dir / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for url in _URL_RE.findall(text):
            add_http(url)
        for host in _HOST_RE.findall(text):
            add_http(host)

    for sec in ("v", "w", "r", "a", "x", "t"):
        secv = graph.get(sec) or {}
        if not isinstance(secv, dict):
            continue
        for key, val in secv.items():
            if not isinstance(val, dict):
                continue
            src = val.get("src") if val.get("src") is not None else val.get("source")
            if not isinstance(src, str) or not src.strip():
                continue
            fact_key = f"{sec}:{key}"
            cleaned = sanitize_url(src)
            if cleaned:
                add_http(src)
                fact_sources[fact_key] = cleaned
            else:
                add_fact(fact_key, src.strip())

    if len(docs) > DOCS_MAX_UNITS:
        kept: list[str] = []
        seen: set[str] = set()
        for url in priority_docs + docs:
            if url in seen:
                continue
            seen.add(url)
            kept.append(url)
            if len(kept) >= DOCS_MAX_UNITS:
                break
        for url in priority_docs:
            if url not in kept:
                kept.append(url)
        docs = kept

    return {
        "docs": docs,
        "repos": repos,
        "local": local,
        "fact_sources": fact_sources,
        "priority_docs": priority_docs,
        "priority_repos": priority_repos,
    }


def load_seeds(path: Path | None = None) -> dict[str, list[str]]:
    seed_path = path or (HERE / SEEDS_NAME)
    if not seed_path.is_file():
        return {}
    try:
        data = json.loads(seed_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for slug, urls in data.items():
        if isinstance(urls, list):
            out[str(slug)] = [str(u) for u in urls]
        elif isinstance(urls, str):
            out[str(slug)] = [urls]
    return out


def fetch_docs_unit(url: str) -> tuple[dict[str, Any] | None, str]:
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            etag = resp.headers.get("ETag")
            last_mod = resp.headers.get("Last-Modified")
            unit: dict[str, Any] = {
                "locator": url,
                "sha256": hashlib.sha256(body).hexdigest(),
                "bytes": len(body),
            }
            if etag:
                unit["etag"] = etag
            if last_mod:
                unit["lastModified"] = last_mod
            return unit, ""
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return None, str(exc)


def pin_docs_now(urls: list[str]) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    units: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    primary = urls[0] if urls else None
    primary_etag = None
    primary_lm = None
    for url in urls:
        unit, err = fetch_docs_unit(url)
        if unit:
            units.append(unit)
            if url == primary:
                primary_etag = unit.get("etag")
                primary_lm = unit.get("lastModified")
        else:
            failures.append({"url": url, "reason": err})
    if not units:
        return None, failures
    pin: dict[str, Any] = {
        "kind": "docs",
        "url": primary,
        "fetched_at": _now(),
        "units": len(units),
        "corpus_sha256": corpus_digest(units),
        "unitPins": units,
    }
    if primary_etag:
        pin["etag"] = primary_etag
    if primary_lm:
        pin["lastModified"] = primary_lm
    return pin, failures


def pin_repo_now(url: str) -> tuple[dict[str, Any] | None, str]:
    commit, err = _ls_remote(url, "HEAD")
    if not commit:
        return None, err or "ls-remote failed"
    digest = sha256_text(commit)
    return {
        "kind": "repo",
        "url": url,
        "ref": "HEAD",
        "commit": commit,
        "fetched_at": _now(),
        "units": 0,
        "corpus_sha256": digest,
    }, ""


def pin_local_now(items: list[dict[str, str]]) -> dict[str, Any] | None:
    units: list[dict[str, Any]] = []
    for item in items:
        path = Path(item["path"])
        try:
            data = path.read_bytes()
        except OSError:
            continue
        units.append({
            "locator": item["locator"],
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        })
    if not units:
        return None
    return {
        "kind": "local",
        "fetched_at": _now(),
        "units": len(units),
        "corpus_sha256": corpus_digest(units),
        "unitPins": units,
    }


def _split_overlay_urls(entry: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Judged sources from a discovery overlay. Applied first, never invented here."""
    docs: list[str] = []
    repos: list[str] = []
    seen: set[str] = set()

    def take(raw: Any) -> None:
        url = sanitize_url(str(raw) if raw is not None else "")
        if not url or url in seen:
            return
        seen.add(url)
        if classify_locator(url) == "repo":
            repos.append(url)
        else:
            docs.append(url)

    if not isinstance(entry, dict):
        return docs, repos
    take(entry.get("repo_url") or entry.get("repo"))
    take(entry.get("docs_url") or entry.get("docs"))
    for extra in entry.get("urls") or []:
        take(extra)
    return docs, repos


def load_overlay(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def plan_backfill(
    kb_dir: Path,
    graph: dict[str, Any],
    root: Path | None,
    seeds: dict[str, list[str]] | None = None,
    overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    harvested = harvest_locators(kb_dir, graph, root)
    docs = list(harvested["docs"])
    repos = list(harvested["repos"])
    local = list(harvested["local"])
    over_docs, over_repos = _split_overlay_urls((overlay or {}).get(kb_dir.name) or {})
    docs = over_docs + [u for u in docs if u not in over_docs]
    repos = over_repos + [u for u in repos if u not in over_repos]
    if not docs and not repos and not local:
        for raw in (seeds or {}).get(kb_dir.name) or []:
            url = sanitize_url(raw)
            if not url:
                continue
            if classify_locator(url) == "repo":
                repos.append(url)
            else:
                docs.append(url)
    pins: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for url in repos:
        pin, err = pin_repo_now(url)
        if pin:
            pins.append(pin)
        else:
            failures.append({"url": url, "reason": err, "kind": "repo"})
    if docs:
        pin, fails = pin_docs_now(docs)
        if pin:
            pins.append(pin)
        for fail in fails:
            failures.append({"url": fail.get("url", ""), "reason": fail.get("reason", ""), "kind": "docs"})
    if local:
        pin = pin_local_now(local)
        if pin:
            pins.append(pin)
    status = "backfill-now" if pins else "undeterminable"
    return {
        "pins": pins,
        "fact_sources": harvested["fact_sources"],
        "status": status,
        "failures": failures,
        "harvested": harvested,
    }


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
            if stored_hash:
                return "stale", f"docs {url} body changed"
            return "undeterminable", f"docs {url}: 200 but no stored body hash"
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return "current", ""
        return "undeterminable", f"docs {url}: HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return "undeterminable", f"docs {url}: {e}"


def _docs_units(pin: dict[str, Any]) -> list[dict[str, Any]]:
    """Engine pins put etag on the pin and the body hash on unitPins — never invent a hashless unit."""
    units = [dict(u) for u in (pin.get("unitPins") or []) if isinstance(u, dict)]
    seen = {str(u.get("locator") or u.get("url") or "") for u in units}
    pin_url = pin.get("url")
    pin_hash = pin.get("sha256")
    if pin_url and pin_hash and pin_url not in seen:
        units.append({
            "locator": pin_url,
            "etag": pin.get("etag"),
            "sha256": pin_hash,
        })
        seen.add(str(pin_url))
    if pin_url and pin.get("etag"):
        for u in units:
            loc = u.get("locator") or u.get("url")
            if loc == pin_url and not u.get("etag"):
                u["etag"] = pin.get("etag")
    return [u for u in units if u.get("locator") or u.get("url")]


def _check_docs_pin(pin: dict[str, Any]) -> tuple[str, str]:
    units = _docs_units(pin)
    if not units:
        return "undeterminable", "docs pin has no unitPins with a locator"
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


def _check_local_pin(pin: dict[str, Any], root: Path | None) -> tuple[str, str]:
    units = [u for u in (pin.get("unitPins") or []) if isinstance(u, dict)]
    if not units:
        return "undeterminable", "local pin has no unitPins"
    worst = "current"
    notes: list[str] = []
    for unit in units:
        loc = unit.get("locator") or unit.get("path")
        stored = unit.get("sha256")
        path: Path | None = None
        if loc:
            direct = Path(str(loc))
            if direct.is_file():
                path = direct
            elif root is not None:
                cand = Path(root) / str(loc)
                if cand.is_file():
                    path = cand
        if path is None or not path.is_file():
            worst = "undeterminable"
            notes.append(f"local missing {loc}")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if stored and digest != stored:
            return "stale", f"local {loc} body changed"
        if not stored:
            worst = "undeterminable"
            notes.append(f"local {loc}: no stored hash")
    return worst, "; ".join(notes)


def check_stale(provenance: dict[str, Any], root: Path | None = None) -> tuple[int, dict[str, Any]]:
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
        elif kind == "local":
            state, note = _check_local_pin(pin, root)
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


# --------------------------------------------------------------------------- fleet probe

def check_stale_fleet(root: Path) -> tuple[int, dict[str, Any]]:
    """Probe every KB the registry finds under one project. Read-only — scan() never writes.

    Each slug's verdict is the EXISTING check_stale path, resolved layout-blind from the
    provenance.json sitting beside its fact-graph.json. One slug's failure degrades that slug
    to undeterminable; the report always completes.
    """
    import registry  # lazy: importing provenance must stay free of the registry walk

    def blank() -> dict[str, Any]:
        return {"total": 0, "current": 0, "stale": 0, "undeterminable": 0}

    counts = blank()
    report: dict[str, Any] = {"root": str(root), "graphs": {}, "counts": counts}
    registry.set_repo_root(root)
    try:
        reg = registry.scan()
    except Exception as exc:  # fail closed: a broken walk is never "all current"
        report.update(status="undeterminable", reason=f"registry scan failed: {exc}")
        return 1, report

    entries: dict[str, Any] = {}
    for slug in sorted(reg.get("graphs") or {}):
        g = reg["graphs"][slug]
        entry: dict[str, Any] = {
            "path": g.get("path"),
            "consumers": list(g.get("consumers") or []),
            "duplicate_paths": list(g.get("duplicate_paths") or []),
            "moved": [],
            "reason": "",
        }
        try:
            graph_path = root / str(g.get("path") or "")
            prov_path = graph_path.parent / PROVENANCE_NAME
            # kb: provenance-resolved-as-sibling: a KB whose provenance.json lives elsewhere is
            # reported undeterminable, not probed (upgrade path: explicit provenance locator in
            # registry entries).
            # kb: legacy-kbs-undeterminable: fleet never reports clean while a legacy KB exists;
            # backfill manual (upgrade path: --backfill over plan_backfill).
            if not prov_path.is_file():
                entry.update(status="undeterminable",
                             reason=f"missing {PROVENANCE_NAME} beside {g.get('path')}")
            else:
                prov = json.loads(prov_path.read_text(encoding="utf-8"))
                _, probe = check_stale(prov, root=root)
                entry["status"] = probe.get("status")
                entry["moved"] = list(probe.get("moved") or [])
                entry["reason"] = str(probe.get("reason") or "")
                if probe.get("undeterminable"):
                    entry["undeterminable"] = list(probe["undeterminable"])
        except Exception as exc:  # one slug's probe failure must not kill the report
            entry.update(status="undeterminable", reason=f"probe failed: {exc}")
        entries[slug] = entry

    counts.update(
        total=len(entries),
        current=sum(1 for e in entries.values() if e.get("status") == "current"),
        stale=sum(1 for e in entries.values() if e.get("status") == "stale"),
        undeterminable=sum(1 for e in entries.values() if e.get("status") == "undeterminable"),
    )
    report["graphs"] = entries
    if not entries:
        report.update(status="undeterminable", reason=f"no fact-graph.json under {root} (fail-closed)")
        return 1, report
    if counts["stale"]:
        report["status"] = "stale"
        return 10, report
    if counts["undeterminable"]:
        report["status"] = "undeterminable"
        return 1, report
    report["status"] = "current"
    return 0, report


def load_provenance(root: Path, tech: str, docs_override: Path | None = None) -> dict[str, Any]:
    path = _roots().docs_dir(root, docs_override) / tech / PROVENANCE_NAME
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check-stale", metavar="TECH")
    p.add_argument("--check-stale-fleet", action="store_true",
                   help="probe every KB the registry finds under --root; one JSON report")
    p.add_argument("--root", default=None)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    # Must agree with write-graph.py, or the staleness probe reads a different KB than the one
    # the upgrade skill is about to update.
    root, docs_override = _roots().choose(args.root)
    if args.check_stale_fleet:
        # kb: one-project-per-invocation: no machine-wide report (upgrade path: caller loops
        # per project root — the registry indexes exactly one project).
        code, fleet_report = check_stale_fleet(root)
        print(json.dumps(fleet_report, indent=2, sort_keys=True))
        return code
    if args.check_stale:
        try:
            prov = load_provenance(root, args.check_stale, docs_override)
        except FileNotFoundError as e:
            print(json.dumps({"status": "undeterminable", "reason": f"missing {e}"}))
            return 1
        except json.JSONDecodeError as e:
            print(json.dumps({"status": "undeterminable", "reason": str(e)}))
            return 1
        code, report = check_stale(prov, root=root)
        print(json.dumps(report, indent=2, sort_keys=True))
        return code
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
