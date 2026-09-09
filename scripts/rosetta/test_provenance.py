#!/usr/bin/env python3
"""Real-I/O tests for provenance.py: digest, version derive, stale probe."""
from __future__ import annotations

import http.server
import json
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import provenance as P  # noqa: E402

WG = HERE / "write-graph.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(WG), *args], capture_output=True, text=True)


def run_probe(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HERE / "provenance.py"), *args],
        capture_output=True, text=True,
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


def _fleet_checks(check) -> None:
    """--check-stale-fleet: 4 KBs across all 3 registry layouts, docs + repo pin kinds, plus a
    legacy KB with no provenance.json. Real local HTTP ETag server, real file:// bare remote.
    Exit precedence is proven by the fixture triple: as-shipped -> 10, fix the stale hash -> 1,
    add the legacy provenance -> 0."""
    tmp = Path(tempfile.mkdtemp(prefix="rosetta-fleet-"))
    proj = tmp / "proj"
    httpd, url = serve()
    try:
        digest = _sha(_EtagHandler.body)

        def docs_pin(etag: str, sha: str) -> dict:
            return {
                "kind": "docs", "url": url, "etag": etag,
                "fetched_at": "2026-09-09T00:00:00Z", "units": 1,
                "corpus_sha256": P.corpus_digest([{"locator": url, "sha256": sha}]),
                "unitPins": [{"locator": url, "etag": etag, "sha256": sha}],
            }

        current, stale = docs_pin(_EtagHandler.etag, digest), docs_pin('"old"', "0" * 64)

        # real bare remote for the repo pin (ls-remote over file://)
        work = tmp / "work"
        work.mkdir()
        (work / "package.json").write_text(json.dumps({"version": "1.2.3"}))
        subprocess.run(["git", "init"], cwd=work, check=True, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=work, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "i"],
            cwd=work, check=True, capture_output=True,
        )
        remote = tmp / "remote.git"
        subprocess.run(["git", "clone", "--bare", str(work), str(remote)], check=True, capture_output=True)
        head = subprocess.check_output(["git", "-C", str(work), "rev-parse", "HEAD"], text=True).strip()
        pin_repo = {
            "kind": "repo", "url": remote.resolve().as_uri(), "ref": "HEAD", "commit": head,
            "fetched_at": "2026-09-09T00:00:00Z", "units": 1,
            "corpus_sha256": P.corpus_digest([]),
        }

        def graph(tech: str) -> dict:
            return {"_meta": {"technology": tech}, "v": {"kb_version": "1.0.0"}, "w": {}}

        def put(rel: str, payload) -> None:
            p = proj / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(payload if isinstance(payload, str) else json.dumps(payload, indent=2),
                         encoding="utf-8")

        # layout 1: .rosetta/docs/<slug>/ — current docs pin (+ a mirrored copy -> duplicate_paths)
        put(".rosetta/docs/curiodocs/fact-graph.json", graph("Curio"))
        put(".rosetta/docs/curiodocs/provenance.json", {"kb_version": "1.0.0", "sources": [current]})
        put("agents/curiodocs-reviewer/kb/fact-graph.json", graph("Curio"))
        put("agents/curiodocs-reviewer/kb/provenance.json", {"kb_version": "1.0.0", "sources": [current]})
        put("agents/curiodocs-reviewer/agent.md", "# curiodocs reviewer\n")
        # layout 2: docs/<slug>/ — docs pin with a wrong stored hash (stale) + legacy KB (no provenance)
        put("docs/staledocs/fact-graph.json", graph("StaleDocs"))
        put("docs/staledocs/provenance.json", {"kb_version": "0.9.0", "sources": [stale]})
        put("agents/staledocs-implementer/agent.md", "# staledocs\n")
        put(".rosetta/docs/legacyprov/fact-graph.json", graph("Legacy"))
        # layout 3: agents/<name>/kb/ — current repo pin vs the bare remote, with a consuming agent.md
        put("agents/repopin-implementer/kb/fact-graph.json", graph("RepoPin"))
        put("agents/repopin-implementer/kb/provenance.json", {"kb_version": "1.0.0", "sources": [pin_repo]})
        put("agents/repopin-implementer/agent.md", "# repopin\n")

        def fleet(target: Path = proj) -> tuple[int, dict]:
            r = run_probe("--root", str(target), "--check-stale-fleet")
            try:
                return r.returncode, json.loads(r.stdout)
            except json.JSONDecodeError:
                return r.returncode, {"_unparsable": r.stdout + r.stderr}

        want = {"curiodocs", "staledocs", "repopin", "legacyprov"}

        # phase 1: as-shipped — one stale beats the legacy undeterminable -> 10
        rc, rep = fleet()
        rc2, rep2 = fleet()
        check("fleet-stable", (rc, json.dumps(rep, sort_keys=True)) == (rc2, json.dumps(rep2, sort_keys=True)),
              f"({rc}/{rep2})")
        check("fleet-as-shipped-10", rc == 10, f"(rc={rc} {rep})")
        check("fleet-status-stale", rep.get("status") == "stale", str(rep.get("status")))
        check("fleet-per-slug-entries", set(rep.get("graphs") or {}) == want, str(sorted(rep.get("graphs") or {})))
        g = rep.get("graphs") or {}
        check("fleet-curiodocs-current", g.get("curiodocs", {}).get("status") == "current", str(g.get("curiodocs")))
        check("fleet-repopin-current", g.get("repopin", {}).get("status") == "current", str(g.get("repopin")))
        sd = g.get("staledocs", {})
        check("fleet-staledocs-stale", sd.get("status") == "stale", str(sd))
        check("fleet-staledocs-moved-names-url", any(url in m for m in (sd.get("moved") or [])), str(sd.get("moved")))
        lg = g.get("legacyprov", {})
        check("fleet-legacy-undeterminable", lg.get("status") == "undeterminable", str(lg))
        check("fleet-legacy-reason", "provenance.json" in str(lg.get("reason")), str(lg.get("reason")))
        check("fleet-consumers", g.get("repopin", {}).get("consumers") == ["repopin-implementer"],
              str(g.get("repopin", {}).get("consumers")))
        check("fleet-curiodocs-consumers", g.get("curiodocs", {}).get("consumers") == ["curiodocs-reviewer"],
              str(g.get("curiodocs", {}).get("consumers")))
        dups = g.get("curiodocs", {}).get("duplicate_paths") or []
        check("fleet-duplicate-paths", "agents/curiodocs-reviewer/kb/fact-graph.json" in dups, str(dups))
        check("fleet-counts", rep.get("counts") == {"total": 4, "current": 2, "stale": 1, "undeterminable": 1},
              str(rep.get("counts")))

        # phase 2: fix the stale stored hash — only the legacy KB is left undeterminable -> 1
        put("docs/staledocs/provenance.json", {"kb_version": "0.9.0", "sources": [current]})
        rc, rep = fleet()
        undet = sorted(s for s, e in (rep.get("graphs") or {}).items() if e.get("status") == "undeterminable")
        check("fleet-fixed-stale-1", rc == 1, f"(rc={rc} {rep})")
        check("fleet-fixed-stale-status", rep.get("status") == "undeterminable", str(rep.get("status")))
        check("fleet-fixed-only-legacy", undet == ["legacyprov"], str(undet))

        # phase 3: add the legacy provenance — all current -> 0
        put(".rosetta/docs/legacyprov/provenance.json", {"kb_version": "1.0.0", "sources": [current]})
        rc, rep = fleet()
        check("fleet-all-current-0", rc == 0, f"(rc={rc} {rep})")
        check("fleet-all-current-status", rep.get("status") == "current", str(rep.get("status")))

        # phase 4: a per-slug probe failure degrades that slug, never kills the report
        put("agents/repopin-implementer/kb/provenance.json", "{not json")
        rc, rep = fleet()
        check("fleet-broken-prov-exit-1", rc == 1, f"(rc={rc})")
        check("fleet-broken-prov-report-complete", set(rep.get("graphs") or {}) == want, str(rep))
        check("fleet-broken-prov-degraded", (rep.get("graphs") or {}).get("repopin", {}).get("status")
              == "undeterminable", str((rep.get("graphs") or {}).get("repopin")))
        check("fleet-broken-prov-others-fine", (rep.get("graphs") or {}).get("curiodocs", {}).get("status")
              == "current", str((rep.get("graphs") or {}).get("curiodocs")))

        # zero graphs -> 1 (fail-closed, never "all current")
        empty = tmp / "empty-proj"
        empty.mkdir()
        rc, rep = fleet(empty)
        check("fleet-zero-graphs-1", rc == 1, f"(rc={rc} {rep})")
        check("fleet-zero-graphs-reason", rep.get("status") == "undeterminable", str(rep.get("status")))
    finally:
        httpd.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)


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

    tmp = Path(__import__("tempfile").mkdtemp(prefix="rosetta-prov-"))
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
                    "src": "brain/docs/toy/widget.md",
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
        harvested = P.harvest_locators(hdir, graph, root=tmp)
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
        fs = harvested.get("fact_sources") or {}
        check("harvest-fact-src", "w:Widget" in fs, str(fs))

    _fleet_checks(check)

    if failures:
        for f in failures:
            print("FAIL:", f, file=sys.stderr)
        return 1
    print("ok — provenance digest + stale probe (docs ETag + git ls-remote) + harvest + fleet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
