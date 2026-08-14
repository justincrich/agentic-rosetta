#!/usr/bin/env python3
"""Real-I/O tests for provenance.py: digest, version derive, stale probe."""
from __future__ import annotations

import http.server
import json
import subprocess
import sys
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


def serve() -> tuple[http.server.HTTPServer, str]:
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _EtagHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    host, port = httpd.server_address[:2]
    return httpd, f"http://{host}:{port}/"


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

    if failures:
        for f in failures:
            print("FAIL:", f, file=sys.stderr)
        return 1
    print("ok — provenance digest + stale probe (docs ETag + git ls-remote)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
