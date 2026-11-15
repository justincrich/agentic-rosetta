#!/usr/bin/env python3
"""test_sync_agents.py — integration proofs for sync_agents.py.

WHAT IS REAL HERE
  AC-15 and AC-20 drive ``sync_agents.py`` against the DEPLOYED service
  (``$ROSETTA_MCP_URL``, default ``https://rosetta-kb-mcp.justin-d51.workers.dev``) into a
  sandbox ``HOME`` that holds no consumer checkout, and compare the result with a real
  ``agentshare.py`` run of the consumer checkout (projected by the publisher's own
  ``--copy``/normalisation rules, so the exclusion list has one home). The republish case
  stands up the real Worker in workerd (``wrangler dev --local``) over TLS on a freshly
  published stage — no hand-written manifest, no fake transport.

  AC-21 publishes OpenCode, Grok and Droid with the publisher, serves that stage from the real
  Worker in workerd, syncs it into a sandbox ``HOME``, and compares every file's sha256 with a
  real ``agentshare --target`` run of the same checkout. It also checks the two things a file
  mirror cannot carry: Grok's profiles must land in ``.grok/agent-library`` and never in the
  folder Grok scans, and the OpenCode ``opencode.json`` model unpin must be reproduced on the
  device (byte-for-byte the same result as the projector) or reported as not applied.

  AC-1's hostile inputs cannot come from the real service (it will never publish
  ``../escape.md`` or a wrong sha256), so this suite serves them from a real HTTPS origin it
  runs itself, with a certificate the sync is told to trust through ``SSL_CERT_FILE``. The
  client under test is the shipped one: real TLS, real redirects, real bytes.

ENVIRONMENT
  ROSETTA_MCP_TOKEN   required for AC-15/AC-20: the service's shared bearer token (never printed)
  ROSETTA_MCP_URL     optional: another deployment of the same Worker
  ROSETTA_PUBLISH_ROOT  optional: the consumer checkout (default ``~/Projects/brain``)

Run:
    ROSETTA_MCP_TOKEN=… python3 scripts/kb-delivery/test_sync_agents.py
    ROSETTA_MCP_TOKEN=… python3 scripts/kb-delivery/test_sync_agents.py -k ac20
    python3 scripts/kb-delivery/test_sync_agents.py -k ac21      # no deployed token needed
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[1]
SYNC = HERE / "sync_agents.py"
PUBLISHER = HERE / "publish_snapshot.py"
WRANGLER = PLUGIN / "worker" / "node_modules" / ".bin" / "wrangler"
WORKER_DIR = PLUGIN / "worker"

SERVICE_URL = os.environ.get("ROSETTA_MCP_URL") or "https://rosetta-kb-mcp.justin-d51.workers.dev"
TOKEN_ENV = "ROSETTA_MCP_TOKEN"
EXPECTED_AGENTS = 293
EXPECTED_CLAUDE_FILES = 336
EXPECTED_CODEX_FILES = 293
TARGET_DEST = {
    "claude": ".claude/agents",
    "codex": ".codex/agents",
    "opencode": ".config/opencode/agents",
    "droid": ".factory/droids",
    "grok": ".grok/agent-library",
}
TARGET_FILES = {"claude": EXPECTED_CLAUDE_FILES, "codex": EXPECTED_CODEX_FILES}
BACKUP_RE = re.compile(r"^agents\.bak-\d{8}-\d{6}$")
WRANGLER_START_TIMEOUT = 120
SYNC_TIMEOUT = 600


# ── small helpers ──────────────────────────────────────────────────────


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def remove_tree(path: Path) -> None:
    """Remove a temporary tree, clearing the immutable flags the OpenCode target plants.

    agentshare marks ``<opencode target>/agents`` as ``uchg`` so other tools cannot recreate a
    symlink over it. That is correct on a device and fatal to ``rmtree`` on our throwaway copy,
    so the flag is cleared here rather than leaving every run's tree behind in the temp folder.
    """
    subprocess.run(["chflags", "-R", "nouchg", str(path)], check=False, capture_output=True)
    shutil.rmtree(path, ignore_errors=True)


def tree_hashes(root: Path) -> dict[str, str]:
    """``{relative posix path: sha256}`` for every file under ``root``."""
    if not root.is_dir():
        return {}
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            found[path.relative_to(root).as_posix()] = "symlink->" + os.readlink(path)
        elif path.is_file():
            found[path.relative_to(root).as_posix()] = sha256_of(path)
    return found


def tree_digest(root: Path) -> str:
    """One digest over a whole tree, so "byte-identical" is one comparison."""
    lines = [f"{relative} {digest}\n" for relative, digest in sorted(tree_hashes(root).items())]
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def mtimes(root: Path) -> dict[str, int]:
    return {
        path.relative_to(root).as_posix(): path.stat().st_mtime_ns
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextlib.contextmanager
def workerd_snapshot(stage: Path, token: str, certfile: Path, keyfile: Path):
    """The real Worker in workerd, over TLS, serving ``stage`` as its snapshot.

    One home for both suites that need a published snapshot served the way the deployed
    service serves it: ``wrangler dev --local`` binds the stage as Static Assets and the sync
    talks to it over real TLS with the real bearer check.
    """
    if not WRANGLER.is_file():
        raise AssertionError(
            f"{WRANGLER} is missing; run `bun install` in {WORKER_DIR} (the Worker's real "
            "runtime is what these proofs drive)."
        )
    port = free_port()
    argv = [
        str(WRANGLER), "dev", "--local", "--ip", "127.0.0.1", "--port", str(port),
        "--local-protocol", "https",
        "--https-cert-path", str(certfile), "--https-key-path", str(keyfile),
        "--assets", str(stage), "--var", f"{TOKEN_ENV}:{token}",
    ]
    env = {**os.environ, "CI": "1", "NO_COLOR": "1", "FORCE_COLOR": "0", "WRANGLER_SEND_METRICS": "false"}
    child = subprocess.Popen(
        argv, cwd=str(WORKER_DIR), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    log: list[str] = []
    reader = threading.Thread(target=lambda: [log.append(line) for line in child.stdout], daemon=True)
    reader.start()
    url = f"https://127.0.0.1:{port}"
    probe_context = ssl.create_default_context(cafile=str(certfile))
    try:
        deadline = time.time() + WRANGLER_START_TIMEOUT
        ready = False
        while time.time() < deadline:
            if child.poll() is not None:
                raise AssertionError("wrangler dev exited before it was ready:\n" + "".join(log))
            try:
                urllib.request.urlopen(
                    urllib.request.Request(url + "/manifest.json"), timeout=2, context=probe_context
                )
                ready = True
                break
            except urllib.error.HTTPError as error:
                if error.code == 401:
                    ready = True
                    break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                pass
            time.sleep(0.25)
        if not ready:
            raise AssertionError(f"wrangler dev was not ready within {WRANGLER_START_TIMEOUT}s:\n{''.join(log)}")
        print(f"[workerd] {url} assets={stage}")
        yield url
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                child.kill()


def payload_files(stage: Path) -> dict[str, str]:
    """``{stage-relative path: sha256}`` for the files a snapshot ships."""
    found: dict[str, str] = {}
    for name in ("targets", "kb", "docs"):
        base = stage / name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file():
                found[path.relative_to(stage).as_posix()] = sha256_of(path)
    return found


def load_publisher() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_publisher_for_sync_test", PUBLISHER)
    if spec is None or spec.loader is None:
        raise AssertionError(f"{PUBLISHER} could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def consumer_root() -> Path:
    raw = os.environ.get("ROSETTA_PUBLISH_ROOT") or (Path.home() / "Projects/brain")
    root = Path(raw).expanduser()
    required = ("agents", "scripts/agentshare.py", ".rosetta/docs")
    missing = [name for name in required if not (root / name).exists()]
    if missing:
        raise AssertionError(
            f"this suite proves sync parity against a real consumer checkout; {root} is missing "
            f"{missing}. Set ROSETTA_PUBLISH_ROOT to a checkout that has agents/, "
            "scripts/agentshare.py and .rosetta/docs/."
        )
    return root


def require_token() -> str:
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        raise AssertionError(
            f"${TOKEN_ENV} is not set. These proofs run against the deployed service; export the "
            "shared bearer token before running the suite (the value is never printed)."
        )
    return token


# ── a real HTTPS origin for the hostile cases ──────────────────────────


class LocalOrigin:
    """A real TLS origin that answers snapshot routes exactly as a test hands them over.

    Each record is either ``(status, headers, body)`` or a callable taking the handler and
    returning one. Every request is recorded, so a proof can assert which headers travelled
    where (the redirect case counts Authorization headers on the second origin).
    """

    def __init__(self, certfile: Path, keyfile: Path, *, advertised_host: str = "127.0.0.1") -> None:
        self.routes: dict[str, object] = {}
        self.requests: list[dict[str, object]] = []
        self.advertised_host = advertised_host
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(self))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=str(certfile), keyfile=str(keyfile))
        self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True)
        self.port = int(self.httpd.server_address[1])
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"https://{self.advertised_host}:{self.port}"

    def serve(self, path: str, *, status: int = 200, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.routes[path] = (status, headers or {}, body)

    def authorization_headers(self) -> int:
        return sum(
            1
            for record in self.requests
            for name in record["headers"]  # type: ignore[union-attr]
            if str(name).lower() == "authorization"
        )

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _handler_for(origin: LocalOrigin):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            origin.requests.append({"path": self.path, "headers": dict(self.headers.items())})
            route = origin.routes.get(self.path)
            if route is None:
                status, headers, body = 404, {}, b"not found"
            elif callable(route):
                status, headers, body = route(self)
            else:
                status, headers, body = route  # type: ignore[misc]
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def log_message(self, *args: object) -> None:  # keep the proof output readable
            return

    return Handler


def manifest_document(
    *,
    targets: dict[str, dict],
    agents: list[dict] | None = None,
    kbs: list[dict] | None = None,
    snapshot_id: str = "ac1test000001",
) -> bytes:
    return json.dumps(
        {
            "schema": 1,
            "snapshot_id": snapshot_id,
            "published_at": "2026-11-15T00:00:00Z",
            "targets": targets,
            "agents": agents if agents is not None else [],
            "kbs": kbs if kbs is not None else [],
        }
    ).encode("utf-8")


def inventory_document(files: dict[str, dict], *, snapshot_id: str = "ac1test000001") -> bytes:
    return json.dumps({"schema": 1, "snapshot_id": snapshot_id, "files": files}).encode("utf-8")


# ── the suite ──────────────────────────────────────────────────────────


class SyncAgentsTests(unittest.TestCase):
    """One test per requirement id in the task file."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tls_dir = Path(tempfile.mkdtemp(prefix="sync-agents-tls-"))
        cls.certfile = cls.tls_dir / "cert.pem"
        cls.keyfile = cls.tls_dir / "key.pem"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                "-keyout", str(cls.keyfile), "-out", str(cls.certfile),
                "-subj", "/CN=127.0.0.1",
                "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost",
            ],
            check=True,
            capture_output=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tls_dir, ignore_errors=True)

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="sync-agents-test-"))
        self.addCleanup(remove_tree, self.tmp)
        self.origins: list[LocalOrigin] = []
        self.addCleanup(self._stop_origins)
        # Which entries `_copy_projection` dropped for each reference target (AC-15 reports them).
        self.reference_excluded: dict[str, dict[str, int]] = {}

    def _stop_origins(self) -> None:
        for origin in self.origins:
            origin.stop()
        self.origins.clear()

    # ── helpers ────────────────────────────────────────────────────────

    def origin(self, *, advertised_host: str = "127.0.0.1") -> LocalOrigin:
        origin = LocalOrigin(self.certfile, self.keyfile, advertised_host=advertised_host)
        self.origins.append(origin)
        return origin

    def sandbox_home(self, name: str) -> Path:
        home = self.tmp / name
        home.mkdir(parents=True, exist_ok=True)
        return home

    def run_sync(
        self,
        home: Path,
        *args: str,
        url: str | None = None,
        token: str | bool | None = None,
        ca: Path | None = None,
        expect: int | None = None,
    ) -> subprocess.CompletedProcess:
        env = {key: value for key, value in os.environ.items() if key != "ROSETTA_MCP_URL"}
        env["HOME"] = str(home)
        if token is None:
            token = require_token()
        if token is False:
            env.pop(TOKEN_ENV, None)
        else:
            env[TOKEN_ENV] = str(token)
        if ca is None:
            env.pop("SSL_CERT_FILE", None)
        else:
            env["SSL_CERT_FILE"] = str(ca)
        argv = [sys.executable, str(SYNC)]
        if url is not None:
            argv.extend(["--url", url])
        argv.extend(args)
        proc = subprocess.run(
            argv, capture_output=True, text=True, env=env, cwd=str(PLUGIN), timeout=SYNC_TIMEOUT
        )
        print(f"$ {' '.join(argv)}")
        print(proc.stdout, end="")
        print(proc.stderr, end="")
        print(f"exit={proc.returncode}")
        if expect is not None:
            self.assertEqual(
                proc.returncode,
                expect,
                msg=f"command={argv}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}",
            )
        return proc

    def summary(self, proc: subprocess.CompletedProcess) -> dict:
        lines = [line for line in proc.stdout.splitlines() if line.strip().startswith("{")]
        self.assertTrue(lines, msg=f"no JSON summary on stdout:\n{proc.stdout}\n{proc.stderr}")
        return json.loads(lines[-1])

    def assert_token_absent(self, proc: subprocess.CompletedProcess, token: str) -> None:
        self.assertNotIn(token, proc.stdout, "the token was printed on stdout")
        self.assertNotIn(token, proc.stderr, "the token was printed on stderr")

    def deployed_manifest(self) -> dict:
        token = require_token()
        request = urllib.request.Request(
            f"{SERVICE_URL.rstrip('/')}/manifest.json",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                # The edge rejects the default `Python-urllib/x.y` user agent (Cloudflare
                # error 1010), so ask the way the shipped client asks.
                "User-Agent": "rosetta-sync/1",
            },
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            self.assertEqual(response.status, 200)
            return json.loads(response.read().decode("utf-8"))

    def publish(self, root: Path, stage: Path, *, targets: str = "claude,codex") -> None:
        proc = subprocess.run(
            [sys.executable, str(PUBLISHER), "--root", str(root), "--targets", targets, "--stage", str(stage)],
            capture_output=True,
            text=True,
            timeout=SYNC_TIMEOUT,
            cwd=str(PLUGIN),
        )
        print(f"$ python3 {PUBLISHER.name} --root {root} --targets {targets} --stage {stage}")
        print(proc.stdout, end="")
        print(proc.stderr, end="")
        self.assertEqual(proc.returncode, 0, msg=f"publish failed:\n{proc.stdout}\n{proc.stderr}")

    def reference_target(self, consumer: Path, target: str, home: Path) -> dict[str, str]:
        """One target's tree as the consumer's projector really writes it, keyed by sha256.

        The publisher's own exclusion rule is applied to the projector's output (taken from
        ``publish_snapshot`` rather than reproduced here), so the two sides cannot drift on which
        files are dropped. For these three targets nothing is excluded: the comparison is a plain
        per-file sha256 equality with a real ``agentshare --target`` run.
        """
        publisher = load_publisher()
        home.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["BRAIN_ROOT"] = str(consumer)
        for name in ("GROK_HOME", "ROGUEONE_SHARE"):
            env.pop(name, None)
        proc = subprocess.run(
            [sys.executable, str(consumer / "scripts" / "agentshare.py"), "--target", target],
            env=env,
            cwd=str(consumer),
            capture_output=True,
            text=True,
            timeout=SYNC_TIMEOUT,
        )
        print(f"$ HOME={home} BRAIN_ROOT={consumer} agentshare.py --target {target}")
        print(proc.stdout, end="")
        print(proc.stderr, end="")
        self.assertEqual(proc.returncode, 0, msg=f"agentshare --target {target} failed")
        produced = home / TARGET_DEST[target]
        self.assertTrue(produced.is_dir(), f"agentshare wrote no tree at {produced}")
        staged = self.tmp / f"reference-{target}"
        copied, excluded = publisher._copy_projection(produced, staged)
        self.assertGreater(copied, 0)
        if target == "codex":
            publisher.normalize_generated_paths(staged, consumer)
        self.reference_excluded[target] = excluded
        return tree_hashes(staged)

    def consumer_checkout(self, dest: Path) -> Path:
        """A real checkout of the consumer's HEAD, so a republish cannot touch the real tree."""
        dest.mkdir(parents=True, exist_ok=True)
        archive = subprocess.run(["git", "-C", str(consumer_root()), "archive", "HEAD"], capture_output=True)
        self.assertEqual(archive.returncode, 0, msg=f"git archive failed: {archive.stderr.decode()}")
        extract = subprocess.run(["tar", "-x", "-C", str(dest)], input=archive.stdout, capture_output=True)
        self.assertEqual(extract.returncode, 0, msg=f"tar failed: {extract.stderr.decode()}")
        return dest

    def reference_projection(self, consumer: Path) -> dict[str, dict[str, str]]:
        """What the consumer's projector really produces, with the publisher's own rules.

        ``agentshare.py`` writes both trees into $HOME; the publisher then drops the entries
        it excludes (*.bak, *.log, .history/) and normalises the Codex provenance line. Both
        rules come from ``publish_snapshot.py`` rather than being reproduced here, so the
        exclusion list keeps one home.
        """
        return {
            target: self.reference_target(consumer, target, self.tmp / f"projector-home-{target}")
            for target in ("claude", "codex")
        }

    @contextlib.contextmanager
    def dev_worker(self, stage: Path, token: str):
        """The real Worker in workerd, over TLS, serving ``stage`` as its snapshot."""
        with workerd_snapshot(stage, token, self.certfile, self.keyfile) as url:
            yield url

    # ── AC-1 ───────────────────────────────────────────────────────────

    def test_ac1_url_path_hash_redirect_and_symlink_refusals(self) -> None:
        """Hostile manifests, a clear-text URL, a cross-host redirect and a symlink are refused."""
        token = "ac1-probe-token-4f1c9d-not-a-real-secret"
        home = self.sandbox_home("ac1-home")
        outside = self.tmp / "outside"
        outside.mkdir()

        # (a) A clear-text URL is refused before a single request leaves the machine.
        clear = self.origin()
        clear.serve("/manifest.json", body=manifest_document(targets={"claude": {"dest": ".claude/agents", "files": 1}}))
        proc = self.run_sync(home, "--targets", "claude", url=clear.url.replace("https://", "http://"), token=token, expect=2)
        self.assertIn("https URLs only", proc.stderr)
        self.assertEqual(clear.requests, [], "the sync made a clear-text request")
        self.assert_token_absent(proc, token)

        # (b) A path that climbs out of the target is refused, and nothing is written anywhere.
        escape = self.origin()
        body = b"escape body\n"
        escape.serve(
            "/manifest.json",
            body=manifest_document(
                targets={"claude": {"dest": ".claude/agents", "files": 1}},
                agents=[{"name": "alpha", "path": "targets/claude/alpha/agent.md"}],
            ),
        )
        escape.serve(
            "/files/files.json",
            body=inventory_document({"targets/claude/../../../escape.md": {"sha256": sha256_bytes(body), "bytes": len(body)}}),
        )
        escape.serve("/files/targets/claude/../../../escape.md", body=body)
        proc = self.run_sync(home, "--targets", "claude", url=escape.url, token=token, ca=self.certfile, expect=2)
        self.assertIn("..", proc.stderr)
        self.assertEqual(list(home.rglob("*")), [], "a refused path still wrote into the sandbox")
        self.assertFalse((self.tmp / "escape.md").exists(), "a file was written outside the target")
        self.assertFalse((self.tmp / "outside" / "escape.md").exists())
        self.assert_token_absent(proc, token)

        # (b2) An absolute path in the inventory, and a climbing path in the manifest catalog,
        # are refused the same way.
        absolute = self.origin()
        absolute.serve(
            "/manifest.json",
            body=manifest_document(targets={"claude": {"dest": ".claude/agents", "files": 1}}),
        )
        absolute.serve(
            "/files/files.json",
            body=inventory_document({"/etc/escape.md": {"sha256": sha256_bytes(body), "bytes": len(body)}}),
        )
        proc = self.run_sync(home, "--targets", "claude", url=absolute.url, token=token, ca=self.certfile, expect=2)
        self.assertIn("absolute", proc.stderr)
        self.assertEqual(list(home.rglob("*")), [])

        catalog = self.origin()
        catalog.serve(
            "/manifest.json",
            body=manifest_document(
                targets={"claude": {"dest": ".claude/agents", "files": 1}},
                agents=[{"name": "alpha", "path": "../escape.md"}],
            ),
        )
        catalog.serve("/files/files.json", body=inventory_document({}))
        proc = self.run_sync(home, "--targets", "claude", url=catalog.url, token=token, ca=self.certfile, expect=2)
        self.assertIn("..", proc.stderr)
        self.assertEqual(list(home.rglob("*")), [])
        self.assert_token_absent(proc, token)

        # (c) A body whose sha256 is not the published one is refused before it is written.
        tampered = self.origin()
        published, served = b"published body\n", b"published BODY\n"
        self.assertEqual(
            len(published), len(served), "same-length tamper: only the sha256 check can catch it"
        )
        tampered.serve(
            "/manifest.json",
            body=manifest_document(
                targets={"claude": {"dest": ".claude/agents", "files": 1}},
                agents=[{"name": "alpha", "path": "targets/claude/alpha/agent.md"}],
            ),
        )
        tampered.serve(
            "/files/files.json",
            body=inventory_document(
                {
                    "targets/claude/alpha/agent.md": {
                        "sha256": sha256_bytes(published),
                        "bytes": len(published),
                    }
                }
            ),
        )
        tampered.serve("/files/targets/claude/alpha/agent.md", body=served)
        tampered_home = self.sandbox_home("ac1-tampered-home")
        proc = self.run_sync(
            tampered_home, "--targets", "claude", url=tampered.url, token=token, ca=self.certfile, expect=2
        )
        self.assertIn("does not match the published", proc.stderr)
        self.assertFalse(
            (tampered_home / ".claude" / "agents" / "alpha" / "agent.md").exists(),
            "a body that failed its sha256 check was written",
        )
        self.assertEqual(list(tampered_home.rglob("*")), [])
        self.assert_token_absent(proc, token)

        # (d) A redirect to another host is followed WITHOUT the bearer token.
        first = self.origin()
        second = self.origin(advertised_host="localhost")
        redirected = manifest_document(
            targets={"claude": {"dest": ".claude/agents", "files": 1}},
            agents=[{"name": "alpha", "path": "targets/claude/alpha/agent.md"}],
        )
        redirected_body = b"redirected body\n"
        first.serve("/manifest.json", status=302, headers={"Location": f"{second.url}/manifest.json"})
        first.serve(
            "/files/files.json",
            body=inventory_document(
                {
                    "targets/claude/alpha/agent.md": {
                        "sha256": sha256_bytes(redirected_body),
                        "bytes": len(redirected_body),
                    }
                }
            ),
        )
        first.serve("/files/targets/claude/alpha/agent.md", body=redirected_body)
        second.serve("/manifest.json", body=redirected)
        redirect_home = self.sandbox_home("ac1-redirect-home")
        proc = self.run_sync(redirect_home, "--targets", "claude", url=first.url, token=token, ca=self.certfile, expect=0)
        self.assertTrue(first.authorization_headers() > 0, "the first origin saw no credential at all")
        self.assertEqual(second.authorization_headers(), 0, "the bearer token travelled to the redirect target")
        self.assertIn("following redirect without the bearer token", proc.stderr)
        self.assertEqual(
            (redirect_home / ".claude" / "agents" / "alpha" / "agent.md").read_bytes(),
            redirected_body,
            "the redirected response was not the one used",
        )
        self.assert_token_absent(proc, token)

        # (e) A missing token is refused, and the refusal never echoes a value.
        no_token_home = self.sandbox_home("ac1-no-token-home")
        proc = self.run_sync(no_token_home, "--targets", "claude", url=first.url, token=False, ca=self.certfile, expect=2)
        self.assertIn(TOKEN_ENV, proc.stderr)
        self.assertEqual(list(no_token_home.rglob("*")), [])

        # (f) ANY symlinked component under the destination is refused.
        linked = self.sandbox_home("ac1-linked-home")
        (linked / ".claude" / "agents").mkdir(parents=True)
        os.symlink(outside, linked / ".claude" / "agents" / "sub")
        nested = self.origin()
        nested.serve(
            "/manifest.json",
            body=manifest_document(
                targets={"claude": {"dest": ".claude/agents", "files": 1}},
                agents=[{"name": "alpha", "path": "targets/claude/sub/agent.md"}],
            ),
        )
        nested.serve(
            "/files/files.json",
            body=inventory_document({"targets/claude/sub/agent.md": {"sha256": sha256_bytes(body), "bytes": len(body)}}),
        )
        nested.serve("/files/targets/claude/sub/agent.md", body=body)
        proc = self.run_sync(linked, "--targets", "claude", url=nested.url, token=token, ca=self.certfile, expect=2)
        self.assertIn("is a symlink", proc.stderr)
        self.assertEqual(list(outside.iterdir()), [], "a write travelled through the nested symlink")
        self.assertFalse(
            [record for record in nested.requests if str(record["path"]).startswith("/files/targets/")],
            "a payload was fetched before the symlink refusal",
        )
        self.assert_token_absent(proc, token)

    # ── AC-15 ──────────────────────────────────────────────────────────

    def test_ac15_parity_incremental_writes_and_no_user_file_deletion(self) -> None:
        """The device tree equals the projector's, stays put on a second run, and moves by one file."""
        token = require_token()
        consumer = consumer_root()
        home = self.sandbox_home("ac15-home")

        manifest = self.deployed_manifest()
        print(
            f"[ac15] deployed service {SERVICE_URL} -> snapshot_id={manifest['snapshot_id']} "
            f"agents={len(manifest['agents'])} kbs={len(manifest['kbs'])} targets={sorted(manifest['targets'])}"
        )
        self.assertEqual(len(manifest["agents"]), EXPECTED_AGENTS)

        # First run: a sandbox HOME with no consumer checkout at all.
        first = self.run_sync(home, url=SERVICE_URL, expect=0)
        summary = self.summary(first)
        self.assertEqual(summary["snapshot_id"], manifest["snapshot_id"])
        self.assertEqual(summary["written"], EXPECTED_CLAUDE_FILES + EXPECTED_CODEX_FILES)
        for target, expected in TARGET_FILES.items():
            self.assertEqual(len(tree_hashes(home / TARGET_DEST[target])), expected, target)

        # Byte parity with a real agentshare run of the consumer checkout.
        reference = self.reference_projection(consumer)
        for target in ("claude", "codex"):
            synced = tree_hashes(home / TARGET_DEST[target])
            self.assertEqual(
                sorted(synced),
                sorted(reference[target]),
                f"{target}: the synced file set differs from agentshare's projection",
            )
            mismatched = {path for path, digest in synced.items() if reference[target][path] != digest}
            self.assertEqual(mismatched, set(), f"{target}: files differ from the agentshare run")
            print(f"[ac15] {target}: {len(synced)} files sha256-identical to agentshare's projection")

        # A second run writes nothing at all.
        before_hashes = {target: tree_hashes(home / TARGET_DEST[target]) for target in TARGET_FILES}
        before_mtimes = {target: mtimes(home / TARGET_DEST[target]) for target in TARGET_FILES}
        second = self.run_sync(home, url=SERVICE_URL, expect=0)
        second_summary = self.summary(second)
        self.assertEqual(second_summary["written"], 0, "a second run rewrote files")
        self.assertEqual(second_summary["pruned"], 0)
        self.assertEqual(second_summary["skipped"], EXPECTED_CLAUDE_FILES + EXPECTED_CODEX_FILES)
        for target in TARGET_FILES:
            self.assertEqual(tree_hashes(home / TARGET_DEST[target]), before_hashes[target], target)
            self.assertEqual(
                mtimes(home / TARGET_DEST[target]),
                before_mtimes[target],
                f"{target}: a file was touched",
            )

        # A file the device owns is never deleted or rewritten.
        user_file = home / TARGET_DEST["claude"] / "my-agent.md"
        user_file.write_text("---\nname: my-agent\n---\nmine, not the service's\n", encoding="utf-8")
        user_sha = sha256_of(user_file)
        self.run_sync(home, url=SERVICE_URL, expect=0)
        self.assertTrue(user_file.is_file(), "the sync deleted a file it did not create")
        self.assertEqual(sha256_of(user_file), user_sha, "the sync rewrote a file it did not create")
        before_republish = {target: tree_hashes(home / TARGET_DEST[target]) for target in TARGET_FILES}

        # Republish exactly one changed payload file, serve it from the real Worker, and
        # watch exactly that file move.
        checkout = self.consumer_checkout(self.tmp / "consumer")
        stage_a = self.tmp / "stage-a"
        self.publish(checkout, stage_a)
        stage_a_doc = json.loads((stage_a / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            stage_a_doc["snapshot_id"],
            manifest["snapshot_id"],
            msg="the deployed snapshot is not what a fresh publish of the consumer produces; "
            "republish (publish_snapshot.py + wrangler deploy) before running this proof",
        )

        changed_source = checkout / "agents" / "morph-cloud-planner" / "kb" / "volumes.md"
        self.assertTrue(changed_source.is_file(), f"{changed_source} is not part of the consumer")
        changed_source.write_text(
            changed_source.read_text(encoding="utf-8") + "\nSync-01 republish probe: one changed file.\n",
            encoding="utf-8",
        )
        stage_b = self.tmp / "stage-b"
        self.publish(checkout, stage_b)

        files_a, files_b = payload_files(stage_a), payload_files(stage_b)
        changed = {path for path in set(files_a) | set(files_b) if files_a.get(path) != files_b.get(path)}
        self.assertEqual(len(changed), 1, f"the republished stage changed {sorted(changed)}")
        changed_path = next(iter(changed))
        self.assertTrue(changed_path.startswith("targets/claude/"), changed_path)
        print(f"[ac15] republished stage changes exactly 1 payload file: {changed_path}")

        local_token = "ac15-workerd-token-2b7e5a"
        with self.dev_worker(stage_b, local_token) as local_url:
            after_republish = self.run_sync(
                home, url=local_url, token=local_token, ca=self.certfile, expect=0
            )
        republish_summary = self.summary(after_republish)
        self.assertEqual(republish_summary["written"], 1, "a one-file republish wrote more than one file")
        self.assertEqual(
            republish_summary["snapshot_id"],
            json.loads((stage_b / "manifest.json").read_text(encoding="utf-8"))["snapshot_id"],
        )
        changed_relative = changed_path[len("targets/claude/") :]
        self.assertEqual(
            (home / TARGET_DEST["claude"] / changed_relative).read_bytes(),
            (stage_b / changed_path).read_bytes(),
            "the changed file was not the one the republished snapshot carries",
        )
        for target in TARGET_FILES:
            before = before_republish[target]
            after = tree_hashes(home / TARGET_DEST[target])
            moved = {path for path in set(before) | set(after) if before.get(path) != after.get(path)}
            expected = {changed_relative} if target == "claude" else set()
            self.assertEqual(moved, expected, f"{target}: republish changed {sorted(moved)}")
        self.assertEqual(sha256_of(user_file), user_sha, "the republish run touched the user's file")

        # Removing a published file prunes the copy this tool wrote, and nothing else.
        changed_source.unlink()
        stage_c = self.tmp / "stage-c"
        self.publish(checkout, stage_c)
        self.assertNotIn(changed_path, payload_files(stage_c), "stage-c still ships the removed file")
        with self.dev_worker(stage_c, local_token) as local_url:
            pruned_run = self.run_sync(home, url=local_url, token=local_token, ca=self.certfile, expect=0)
        pruned_summary = self.summary(pruned_run)
        self.assertEqual(pruned_summary["pruned"], 1, "the removed file was not pruned")
        self.assertEqual(pruned_summary["written"], 0)
        self.assertFalse((home / TARGET_DEST["claude"] / changed_relative).exists())
        self.assertTrue(user_file.is_file(), "pruning deleted a file the sync did not create")
        self.assertEqual(sha256_of(user_file), user_sha, "pruning rewrote a file the sync did not create")

        # A symlinked target directory is refused without writing a byte.
        symlink_home = self.sandbox_home("ac15-symlink-home")
        victim = self.tmp / "symlink-victim"
        victim.mkdir()
        (symlink_home / ".claude").mkdir()
        os.symlink(victim, symlink_home / ".claude" / "agents")
        proc = self.run_sync(symlink_home, "--targets", "claude", url=SERVICE_URL, expect=2)
        self.assertIn("is a symlink", proc.stderr)
        self.assertEqual(list(victim.iterdir()), [], "0 bytes were supposed to land behind the symlink")
        self.assertFalse((symlink_home / ".codex").exists(), "the refused run went on to write another target")

    # ── AC-20 ──────────────────────────────────────────────────────────

    def test_ac20_symlinked_agents_dir_refused_then_adopted(self) -> None:
        """A symlinked claude agents dir is refused by default and adopted with --adopt."""
        token = require_token()
        manifest = self.deployed_manifest()
        print(
            f"[ac20] deployed service {SERVICE_URL} -> snapshot_id={manifest['snapshot_id']} "
            f"agents={len(manifest['agents'])}"
        )

        home = self.sandbox_home("ac20-home")
        fixture = self.tmp / "fixture-agents"
        fixture.mkdir()
        (fixture / "alpha").mkdir()
        (fixture / "alpha" / "agent.md").write_text("---\nname: alpha\n---\nfixture prompt\n", encoding="utf-8")
        (fixture / "notes.md").write_text("fixture note\n", encoding="utf-8")
        fixture_digest = tree_digest(fixture)

        (home / ".claude").mkdir()
        os.symlink(fixture, home / ".claude" / "agents")

        refused = self.run_sync(home, "--targets", "claude", url=SERVICE_URL, expect=2)
        self.assertIn("is a symlink", refused.stderr)
        self.assertTrue((home / ".claude" / "agents").is_symlink(), "the symlink was moved without --adopt")
        self.assertEqual(tree_digest(fixture), fixture_digest, "the fixture directory changed on a refused run")
        self.assertEqual(
            sorted(path.name for path in (home / ".claude").iterdir()),
            ["agents"],
            "a refused run left something beside the symlink",
        )

        adopted = self.run_sync(home, "--targets", "claude", "--adopt", url=SERVICE_URL, expect=0)
        summary = self.summary(adopted)
        backups = sorted(path.name for path in (home / ".claude").glob("agents.bak-*"))
        self.assertEqual(len(backups), 1, f"expected one backup, found {backups}")
        self.assertRegex(backups[0], BACKUP_RE)
        backup = home / ".claude" / backups[0]
        self.assertTrue(backup.is_symlink(), "the backup is not the moved symlink")
        self.assertEqual(os.readlink(backup), str(fixture), "the backup does not point at the fixture")
        self.assertEqual(summary["targets"]["claude"]["adopted"], str(backup))

        real = home / ".claude" / "agents"
        self.assertTrue(real.is_dir() and not real.is_symlink(), "a real directory was not written")
        agent_dirs = sorted(path for path in real.iterdir() if path.is_dir())
        self.assertEqual(len(agent_dirs), EXPECTED_AGENTS, "a real directory with 293 agent folders was expected")
        self.assertTrue((real / "mcp-planner" / "agent.md").is_file())
        self.assertEqual(tree_digest(fixture), fixture_digest, "the fixture directory changed during --adopt")

    # ── AC-21 ──────────────────────────────────────────────────────────

    def test_ac21_opencode_grok_droid_parity_and_side_effects(self) -> None:
        """OpenCode, Grok and Droid sync to the projector's own bytes, with declared side effects."""
        consumer = self.consumer_checkout(self.tmp / "consumer")
        stage = self.tmp / "stage-targets"
        self.publish(consumer, stage, targets="opencode,grok,droid")
        manifest = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))

        # (a) The snapshot publishes the three new targets, each into the folder its harness reads.
        self.assertEqual(sorted(manifest["targets"]), ["droid", "grok", "opencode"])
        for target in ("opencode", "grok", "droid"):
            self.assertEqual(manifest["targets"][target]["dest"], TARGET_DEST[target])
            self.assertEqual(manifest["targets"][target]["files"], EXPECTED_AGENTS + (1 if target == "opencode" else 0))
        print(
            f"[ac21] staged targets: "
            + ", ".join(f"{name} -> {manifest['targets'][name]['dest']}" for name in sorted(manifest["targets"]))
        )

        # (b) Grok is staged into the library folder and NOWHERE in the folder Grok scans.
        staged_grok = sorted(
            path.relative_to(stage).as_posix() for path in (stage / "targets" / "grok").rglob("*") if path.is_file()
        )
        self.assertEqual(len(staged_grok), EXPECTED_AGENTS)
        self.assertEqual(
            [path for path in staged_grok if path.startswith("targets/grok/agents/")],
            [],
            "a Grok profile was staged into the folder Grok scans (~/.grok/agents)",
        )
        self.assertEqual(
            manifest["targets"]["grok"]["side_effects"][0]["path"],
            ".grok/config.toml",
            "the grok side effect list no longer names the config the projector rewrites",
        )

        # (c) A device state the unpin can be observed in: the same opencode.json on both sides,
        # with model pins for two published agents, one agent nobody publishes, and other keys.
        seeded_config = {
            "theme": "system",
            "provider": {"local": {"options": {"baseURL": "http://127.0.0.1:4545/v1"}}},
            "agent": {
                "mcp-planner": {"model": "zai/glm-5.3", "description": "pinned by hand"},
                "nextjs-reviewer": {"model": "openai/gpt-6.1-sol"},
                "not-a-published-agent": {"model": "keep/me"},
            },
        }
        home = self.sandbox_home("ac21-home")
        reference_home = self.tmp / "ac21-reference-home"
        for root in (home, reference_home):
            config = root / ".config" / "opencode" / "opencode.json"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(json.dumps(seeded_config, indent=2, ensure_ascii=False), encoding="utf-8")
        seeded_sha = sha256_of(home / ".config" / "opencode" / "opencode.json")

        # (d) The reference: a real agentshare run for each target, into its own sandbox HOME.
        reference = {
            target: self.reference_target(consumer, target, reference_home)
            for target in ("opencode", "grok", "droid")
        }
        for target, excluded in self.reference_excluded.items():
            self.assertEqual(sum(excluded.values()), 0, f"{target}: the projector produced excluded entries")

        reference_config = reference_home / ".config" / "opencode" / "opencode.json"
        self.assertNotEqual(sha256_of(reference_config), seeded_sha, "agentshare did not unpin the seeded models")
        reference_agents_config = json.loads(reference_config.read_text(encoding="utf-8"))
        self.assertEqual(
            reference_agents_config["agent"]["not-a-published-agent"], {"model": "keep/me"},
            "agentshare touched an agent it does not publish",
        )
        print(f"[ac21] agentshare unpinned the seeded models in {reference_config}")

        # (e) The real Worker serves the published stage; the device sync pulls it into a HOME
        # that holds no checkout at all.
        local_token = "ac21-workerd-token-7d3b1f"
        with self.dev_worker(stage, local_token) as url:
            proc = self.run_sync(
                home, "--targets", "opencode,grok,droid", url=url, token=local_token, ca=self.certfile, expect=0
            )
        summary = self.summary(proc)
        self.assertEqual(summary["snapshot_id"], manifest["snapshot_id"])
        self.assertEqual(summary["written"], EXPECTED_AGENTS * 3 + 1)
        self.assertNotIn("share", summary, "a plain publish carries no shared documents")

        # (f) Per-file sha256 parity with the projector's own output.
        for target in ("opencode", "grok", "droid"):
            synced = tree_hashes(home / TARGET_DEST[target])
            self.assertEqual(
                sorted(synced), sorted(reference[target]), f"{target}: the synced file set differs from agentshare's"
            )
            mismatched = {path for path, digest in synced.items() if reference[target][path] != digest}
            self.assertEqual(mismatched, set(), f"{target}: files differ from the agentshare run")
            print(f"[ac21] {target}: {len(synced)} files sha256-identical to agentshare's projection")

        # (g) Grok files land only in the library: the scanned folder stays empty, and the config
        # the projector rewrites is reported as NOT applied rather than silently skipped.
        self.assertFalse((home / ".grok" / "agents").exists(), "a Grok file landed in the folder Grok scans")
        self.assertFalse((home / ".grok" / "config.toml").exists(), "the sync wrote the device's grok config")
        self.assertTrue(
            (reference_home / ".grok" / "config.toml").is_file(),
            "the projector wrote no grok config, so the 'not applied' report proves nothing",
        )
        grok_effects = {effect["name"]: effect for effect in summary["targets"]["grok"]["side_effects"]}
        for name in ("grok-subagents-models-table", "grok-legacy-agents-cleanup"):
            effect = grok_effects[name]
            self.assertEqual(effect["handling"], "not-applied")
            self.assertFalse(effect["applied"])
            self.assertTrue(effect["detail"], f"{name}: not applied but with no reason")
            self.assertIn(name, proc.stderr, f"{name}: not reported on stderr")
        print(f"[ac21] grok side effects reported: {sorted(grok_effects)}")

        # (h) The OpenCode model unpin is REPRODUCED, byte for byte the same result as agentshare.
        synced_config = home / ".config" / "opencode" / "opencode.json"
        self.assertEqual(
            sha256_of(synced_config),
            sha256_of(reference_config),
            "the sync's opencode.json does not match what the projector leaves behind",
        )
        synced_agents_config = json.loads(synced_config.read_text(encoding="utf-8"))
        self.assertNotIn("model", synced_agents_config["agent"]["mcp-planner"])
        self.assertEqual(synced_agents_config["agent"]["not-a-published-agent"], {"model": "keep/me"})
        self.assertEqual(synced_agents_config["theme"], "system")
        self.assertEqual(synced_agents_config["provider"], seeded_config["provider"])
        opencode_effects = {effect["name"]: effect for effect in summary["targets"]["opencode"]["side_effects"]}
        unpin = opencode_effects["opencode-agent-model-unpin"]
        self.assertEqual(unpin["handling"], "sync-applies")
        self.assertTrue(unpin["applied"], f"the unpin was not applied: {unpin['detail']}")
        self.assertEqual(unpin["pins_removed"], 2, unpin["detail"])
        print(
            f"[ac21] observation: {synced_config} -> agent keys "
            f"{sorted(synced_agents_config['agent'])}, mcp-planner entry "
            f"{json.dumps(synced_agents_config['agent']['mcp-planner'], sort_keys=True)}"
        )
        print(f"[ac21] opencode unpin reproduced: {unpin['detail']}")

        # (i) Droid has no side effect and none is invented for it.
        self.assertEqual(summary["targets"]["droid"]["side_effects"], [])
        self.assertIn("droid", summary["targets"])


if __name__ == "__main__":
    unittest.main()
