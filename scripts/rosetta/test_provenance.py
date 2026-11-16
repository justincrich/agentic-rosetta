#!/usr/bin/env python3
"""Real-I/O tests for provenance.py: digest, version derive, stale probe."""
from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import provenance as P  # noqa: E402

WG = HERE / "write-graph.py"

STANDARD_CONFIG = {
    "version": 2,
    "paths": {
        "docs": ".rosetta/docs",
        "research": ".rosetta/research",
        "probes": ".rosetta/probes",
        "agents": "agents",
    },
    # A local citation carries a neutral prefix; only this table says where it points.
    "citation_aliases": {"notes": ".rosetta/docs"},
}

# A neutral empty device home: a fixture's layout comes from its own config, never from a
# machine-wide file.
DEVICE_HOME: Path | None = None


def configure(root: Path) -> Path:
    """Seed the version 2 config Rosetta requires. Every fixture repo carries one."""
    (root / ".rosetta").mkdir(parents=True, exist_ok=True)
    (root / ".rosetta" / "config.json").write_text(
        json.dumps(STANDARD_CONFIG, indent=2) + "\n")
    return root


def _env() -> dict[str, str]:
    env = dict(os.environ)
    for key in ("ROSETTA_PROJECT", "ROSETTA_CONFIG", "ROSETTA_PLUGIN"):
        env.pop(key, None)
    if DEVICE_HOME is not None:
        env["ROSETTA_HOME"] = str(DEVICE_HOME)
    return env


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(WG), *args], capture_output=True, text=True,
                          env=_env())


def run_probe(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HERE / "provenance.py"), *args],
        capture_output=True, text=True, env=_env(),
    )


class _EtagHandler(http.server.BaseHTTPRequestHandler):
    body = b"hello-docs-v1"
    etag = '"doc-v1"'

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: ARG002
        return

    def do_GET(self) -> None:  # noqa: N802
        inm = self.headers.get("If-None-Match")
        if inm == self.etag:
            self.send_response(304)
            self.send_header("ETag", self.etag)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("ETag", self.etag)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)


class _Always200:
    """Mutable always-200 server — never 304. Matches servers that ignore If-None-Match."""

    body = b"engine-docs-v1"

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:  # noqa: ARG002
            return

        def do_GET(self) -> None:  # noqa: N802
            payload = _Always200.body
            self.send_response(200)
            self.send_header("ETag", '"ignored"')
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)


def serve(handler: type[http.server.BaseHTTPRequestHandler] = _EtagHandler) -> tuple[http.server.HTTPServer, str]:
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    host, port = httpd.server_address[:2]
    return httpd, f"http://{host}:{port}/"


def _sha(data: bytes) -> str:
    return __import__("hashlib").sha256(data).hexdigest()


def engine_shaped_pin(url: str, body: bytes, etag: str | None = '"pin-etag"') -> dict:
    """Shape pinDocs actually emits: etag on the pin, body hash only on unitPins. No pin.sha256."""
    digest = _sha(body)
    return {
        "kind": "docs",
        "url": url,
        "etag": etag,
        "fetched_at": "2026-08-14T00:00:00Z",
        "units": 1,
        "corpus_sha256": P.corpus_digest([{"locator": url, "sha256": digest}]),
        "unitPins": [{"locator": url, "sha256": digest}],
    }


def main() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        if not ok:
            failures.append(f"{name} {detail}")

    # digest: unchanged bytes → identical; extra unit → different
    a = P.corpus_digest([{"locator": "a", "sha256": "1" * 64}, {"locator": "b", "sha256": "2" * 64}])
    b = P.corpus_digest([{"locator": "b", "sha256": "2" * 64}, {"locator": "a", "sha256": "1" * 64}])
    check("digest-order-stable", a == b)
    c = P.corpus_digest([{"locator": "a", "sha256": "1" * 64}, {"locator": "b", "sha256": "3" * 64}])
    check("digest-changes", a != c)
    check("never-000", P.derive_upstream_version()[0] is None)
    check("never-000-src", P.derive_upstream_version()[1] == "undetermined")

    global DEVICE_HOME
    tmp = Path(__import__("tempfile").mkdtemp(prefix="rosetta-prov-"))
    DEVICE_HOME = tmp / "device-home"
    DEVICE_HOME.mkdir()
    # real git remote for ls-remote (no GitHub)
    remote = tmp / "remote.git"
    work = tmp / "work"
    work.mkdir()
    (work / "package.json").write_text(json.dumps({"version": "9.8.7"}))
    subprocess.run(["git", "init"], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=work, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "i"],
        cwd=work, check=True, capture_output=True,
    )
    subprocess.run(["git", "clone", "--bare", str(work), str(remote)], check=True, capture_output=True)
    head = subprocess.check_output(["git", "-C", str(work), "rev-parse", "HEAD"], text=True).strip()

    httpd, url = serve()
    try:
        # current docs pin
        pin_docs = {
            "kind": "docs",
            "url": url,
            "etag": _EtagHandler.etag,
            "sha256": __import__("hashlib").sha256(_EtagHandler.body).hexdigest(),
            "fetched_at": "2026-08-14T00:00:00Z",
            "units": 1,
            "corpus_sha256": P.corpus_digest([{
                "locator": url,
                "sha256": __import__("hashlib").sha256(_EtagHandler.body).hexdigest(),
            }]),
            "unitPins": [{
                "locator": url,
                "etag": _EtagHandler.etag,
                "sha256": __import__("hashlib").sha256(_EtagHandler.body).hexdigest(),
            }],
        }
        code, report = P.check_stale({"sources": [pin_docs]})
        check("docs-current-1", code == 0, str(report))
        code2, report2 = P.check_stale({"sources": [pin_docs]})
        check("docs-current-2", code2 == 0 and report2 == report, str(report2))

        # stale docs: stored hash/etag no longer match body
        stale_docs = json.loads(json.dumps(pin_docs))
        stale_docs["etag"] = '"old"'
        stale_docs["unitPins"][0]["etag"] = '"old"'
        stale_docs["sha256"] = "0" * 64
        stale_docs["unitPins"][0]["sha256"] = "0" * 64
        s1, r1 = P.check_stale({"sources": [stale_docs]})
        s2, r2 = P.check_stale({"sources": [stale_docs]})
        check("docs-stale-1", s1 == 10, str(r1))
        check("docs-stale-2", s2 == 10, str(r2))
        check("docs-stale-names", any(url in x for x in (r1.get("moved") or [])), str(r1))

        # repo pin via file:// bare remote
        remote_url = remote.resolve().as_uri()
        pin_repo = {
            "kind": "repo",
            "url": remote_url,
            "ref": "HEAD",
            "commit": head,
            "fetched_at": "2026-08-14T00:00:00Z",
            "units": 1,
            "corpus_sha256": P.corpus_digest([]),
        }
        c1, rp1 = P.check_stale({"sources": [pin_repo]})
        c2, rp2 = P.check_stale({"sources": [pin_repo]})
        check("repo-current-1", c1 == 0, str(rp1))
        check("repo-current-2", c2 == 0 and rp2 == rp1, str(rp2))

        pin_repo_stale = dict(pin_repo, commit="deadbeef" * 5)
        rs1, rr1 = P.check_stale({"sources": [pin_repo_stale]})
        rs2, rr2 = P.check_stale({"sources": [pin_repo_stale]})
        check("repo-stale-1", rs1 == 10, str(rr1))
        check("repo-stale-2", rs2 == 10, str(rr2))
        check("repo-stale-names", any("moved" in x for x in (rr1.get("moved") or [])), str(rr1))

        # CLI twice current
        configure(tmp / "proj")
        kb = tmp / "proj" / ".rosetta" / "docs" / "toy"
        kb.mkdir(parents=True)
        (kb / "provenance.json").write_text(json.dumps({"sources": [pin_docs]}, indent=2))
        cli1 = run_probe("--root", str(tmp / "proj"), "--check-stale", "toy")
        cli2 = run_probe("--root", str(tmp / "proj"), "--check-stale", "toy")
        check("cli-current-1", cli1.returncode == 0, cli1.stdout + cli1.stderr)
        check("cli-current-2", cli2.returncode == 0 and cli1.stdout == cli2.stdout)

        (kb / "provenance.json").write_text(json.dumps({"sources": [stale_docs]}, indent=2))
        cli3 = run_probe("--root", str(tmp / "proj"), "--check-stale", "toy")
        cli4 = run_probe("--root", str(tmp / "proj"), "--check-stale", "toy")
        check("cli-stale-1", cli3.returncode == 10, cli3.stdout)
        check("cli-stale-2", cli4.returncode == 10)
    finally:
        httpd.shutdown()

    # Engine-shaped pin vs always-200 (no 304): same body → 0; body change → 10.
    _Always200.body = b"engine-docs-v1"
    httpd2, url2 = serve(_Always200.Handler)
    try:
        eng = engine_shaped_pin(url2, _Always200.body)
        check("engine-pin-no-sha256", "sha256" not in eng)
        e1, er1 = P.check_stale({"sources": [eng]})
        e2, er2 = P.check_stale({"sources": [eng]})
        check("engine-200-current-1", e1 == 0, str(er1))
        check("engine-200-current-2", e2 == 0 and er2 == er1, str(er2))

        configure(tmp / "proj2")
        kb2 = tmp / "proj2" / ".rosetta" / "docs" / "eng"
        kb2.mkdir(parents=True)
        (kb2 / "provenance.json").write_text(json.dumps({"sources": [eng]}, indent=2))
        c_a = run_probe("--root", str(tmp / "proj2"), "--check-stale", "eng")
        c_b = run_probe("--root", str(tmp / "proj2"), "--check-stale", "eng")
        check("engine-cli-current-1", c_a.returncode == 0, c_a.stdout + c_a.stderr)
        check("engine-cli-current-2", c_b.returncode == 0 and c_a.stdout == c_b.stdout)

        _Always200.body = b"engine-docs-CHANGED"
        e3, er3 = P.check_stale({"sources": [eng]})
        e4, er4 = P.check_stale({"sources": [eng]})
        check("engine-200-stale-1", e3 == 10, str(er3))
        check("engine-200-stale-2", e4 == 10, str(er4))
        check("engine-200-stale-names", any(url2 in x for x in (er3.get("moved") or [])), str(er3))

        (kb2 / "provenance.json").write_text(json.dumps({"sources": [eng]}, indent=2))
        c_c = run_probe("--root", str(tmp / "proj2"), "--check-stale", "eng")
        c_d = run_probe("--root", str(tmp / "proj2"), "--check-stale", "eng")
        check("engine-cli-stale-1", c_c.returncode == 10, c_c.stdout)
        check("engine-cli-stale-2", c_d.returncode == 10)
    finally:
        httpd2.shutdown()

    # --- harvest / sanitize (fixture files, no network) ---
    if not hasattr(P, "sanitize_url") or not hasattr(P, "harvest_locators"):
        failures.append("harvest-api missing sanitize_url/harvest_locators")
    else:
        check("san-drop-x", P.sanitize_url("https://x") is None)
        check("san-drop-localhost", P.sanitize_url("http://localhost:4111/docs") is None)
        check("san-keep-loopback-for-tests", P.sanitize_url("http://127.0.0.1:17320/foo") == "http://127.0.0.1:17320/foo")
        check("san-drop-template", P.sanitize_url("https://r.example.com/styles/{style}/x.json") is None)
        check("san-drop-truncated", P.sanitize_url("http://127.0.0.1:17320/<tag") is None)
        check("san-drop-internal", P.sanitize_url("http://_api.internal:4280") is None)
        check(
            "san-scheme-less",
            P.sanitize_url("developers.cloudflare.com/agents/llms-full.txt")
            == "https://developers.cloudflare.com/agents/llms-full.txt",
        )
        check(
            "san-keep-github",
            P.sanitize_url("https://github.com/manaflow-ai/cmux")
            == "https://github.com/manaflow-ai/cmux",
        )
        check("san-drop-filename-tld", P.sanitize_url("https://fact-graph.json") is None)
        check("san-drop-md-host", P.sanitize_url("principles.md") is None)
        check("san-drop-camel-host", P.sanitize_url("Dispatchers.IO") is None)
        check(
            "san-strip-paren-tail",
            P.sanitize_url("https://cmux.com/docs/api (a CURATED SUBSET)")
            == "https://cmux.com/docs/api",
        )
        check(
            "classify-repo",
            P.classify_locator("https://github.com/manaflow-ai/cmux") == "repo",
        )
        check(
            "classify-raw-docs",
            P.classify_locator(
                "https://raw.githubusercontent.com/manaflow-ai/cmux/main/docs/cli-contract.md"
            ) == "docs",
        )

        hdir = tmp / "harvest-kb"
        hdir.mkdir()
        # The citation carries the fixture config's neutral prefix, so the prefix's meaning
        # comes from the config and nowhere else.
        configure(tmp)
        alias_target = tmp / ".rosetta" / "docs" / "toy" / "widget.md"
        alias_target.parent.mkdir(parents=True)
        alias_target.write_text("# widget\n")
        graph = {
            "v": {
                "repo": "github.com/acme/toy",
                "authoritative_cli_contract": "https://example.com/contract.md",
                "kb_version": "1.0.0",
            },
            "_meta": {"sources": ["https://example.com/docs/", "http://localhost:9/skip"]},
            "w": {
                "Widget": {
                    "t": "sl", "i": "toy",
                    "src": "notes/toy/widget.md",
                }
            },
        }
        (hdir / "README.md").write_text(
            "See developers.cloudflare.com/agents/llms-full.txt and https://x\n"
        )
        (hdir / ".history").mkdir()
        (hdir / ".history" / "manifest.json").write_text(json.dumps({
            "refreshes": [{"source_urls": ["https://example.com/history-src.md"]}],
        }))
        (hdir / "widget.md").write_text("# widget\n")
        aliases = P.citation_aliases(tmp)
        check("harvest-aliases-from-config", aliases.get("notes"), tmp / ".rosetta" / "docs")
        harvested = P.harvest_locators(hdir, graph, root=tmp, aliases=aliases)
        docs = set(harvested.get("docs") or [])
        repos = set(harvested.get("repos") or [])
        local = harvested.get("local") or []
        check("harvest-scheme-less-cf", "https://developers.cloudflare.com/agents/llms-full.txt" in docs)
        check("harvest-meta-docs", "https://example.com/docs/" in docs or "https://example.com/docs" in docs)
        check("harvest-history", "https://example.com/history-src.md" in docs)
        check("harvest-contract", "https://example.com/contract.md" in docs)
        check("harvest-repo", any("github.com/acme/toy" in r for r in repos))
        check("harvest-no-localhost", not any("localhost" in d for d in docs))
        check("harvest-no-https-x", "https://x" not in docs)
        check("harvest-local-exists", len(local) >= 1, str(local))
        check("harvest-local-through-alias",
              [entry["locator"] for entry in local], [".rosetta/docs/toy/widget.md"])
        fs = harvested.get("fact_sources") or {}
        check("harvest-fact-src", "w:Widget" in fs, str(fs))

        # The prefix means NOTHING without the config: remove every candidate a built-in rule
        # could have guessed (the file beside the graph) and the citation is not resolved.
        (hdir / "widget.md").unlink()
        unaliased = P.harvest_locators(hdir, graph, root=tmp)
        check("harvest-prefix-needs-the-config", unaliased.get("local") == [],
              str(unaliased.get("local")))
        with_alias = P.harvest_locators(hdir, graph, root=tmp, aliases=aliases)
        check("harvest-prefix-resolves-with-the-config", len(with_alias.get("local") or []), 1)

    # ── the config decides the layout, and a bad citation alias is refused (AC-2 / AC-9 / AC-12)
    ac9 = configure(tmp / "ac9-prov")
    kb = ac9 / ".rosetta" / "docs" / "toy"
    kb.mkdir(parents=True)
    (kb / "widget.md").write_text("widget v1\n")
    widget_digest = _sha(b"widget v1\n")
    (kb / "provenance.json").write_text(json.dumps({"sources": [{
        "kind": "local",
        "unitPins": [{"locator": ".rosetta/docs/toy/widget.md", "sha256": widget_digest}],
    }]}, indent=2))
    first = run_probe("--root", str(ac9), "--check-stale", "toy")
    check("ac9-prov-current", first.returncode == 0, first.stdout + first.stderr)

    # Change ONLY paths.docs, move the KB, and the same citation locator moves with it.
    cfg_path = ac9 / ".rosetta" / "config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["paths"]["docs"] = "kb/other"
    cfg["citation_aliases"] = {"notes": "kb/other"}
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
    (ac9 / "kb" / "other").mkdir(parents=True)
    (ac9 / ".rosetta" / "docs" / "toy").rename(ac9 / "kb" / "other" / "toy")
    (ac9 / "kb" / "other" / "toy" / "provenance.json").write_text(json.dumps({"sources": [{
        "kind": "local",
        "unitPins": [{"locator": "kb/other/toy/widget.md", "sha256": widget_digest}],
    }]}, indent=2))
    moved = run_probe("--root", str(ac9), "--check-stale", "toy")
    check("ac9-prov-reads-new-root", moved.returncode == 0, moved.stdout + moved.stderr)
    check("ac9-prov-old-root-gone",
          (ac9 / ".rosetta" / "docs" / "toy" / "provenance.json").exists() is False,
          str(sorted(p.as_posix() for p in (ac9 / ".rosetta").rglob("*"))))

    # AC-2: an unconfigured repo is refused, naming the file, and nothing is created.
    bare = tmp / "bare-prov"
    (bare / ".git").mkdir(parents=True)
    refused = run_probe("--root", str(bare), "--check-stale", "toy")
    check("prov-bare-nonzero", refused.returncode != 0, f"(rc={refused.returncode})")
    check("prov-bare-names-config",
          str(bare / ".rosetta" / "config.json") in refused.stderr, refused.stderr)
    check("prov-bare-creates-nothing", (bare / ".rosetta").exists() is False,
          str(sorted(p.name for p in bare.iterdir())))

    # AC-12: a citation alias that leaves the repo is refused, naming its key — for every kind.
    outside = tmp / "outside-the-repo"
    outside.mkdir()
    (ac9 / "escape").symlink_to(outside)
    for alias_key, alias_value in (
        ("old/docs", "../outside"),
        ("old/abs", str(outside)),
        ("old/home", "~/outside"),
        ("old/link", "escape/graphs"),
    ):
        cfg = json.loads(cfg_path.read_text())
        cfg["citation_aliases"] = {alias_key: alias_value}
        cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
        bad = run_probe("--root", str(ac9), "--check-stale", "toy")
        check(f"alias-refused[{alias_key}]", bad.returncode != 0, f"(rc={bad.returncode})")
        check(f"alias-names-key[{alias_key}]", alias_key in bad.stderr, bad.stderr)
        check(f"alias-prints-no-path[{alias_key}]", outside.as_posix() not in bad.stdout,
              bad.stdout)
    (ac9 / "escape").unlink()

    if failures:
        for f in failures:
            print("FAIL:", f, file=sys.stderr)
        return 1
    print("ok — provenance digest + stale probe (docs ETag + git ls-remote) + harvest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
