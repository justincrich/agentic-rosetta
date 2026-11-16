#!/usr/bin/env python3
"""test_publish_snapshot.py — integration proofs for publish_snapshot.py.

WHAT IS REAL HERE
  AC-3/5/13/17 stage the REAL authoring checkout (``$ROSETTA_PUBLISH_ROOT``, default
  ``~/Projects/brain``) and inspect the staged artifacts. Nothing is mocked: the catalogue
  numbers asserted here (293 agents, 65 knowledge bases, 6 ``*.bak``, 3 ``*.log``,
  3 ``.history/``) are the real tree's, and the snapshot id is computed from real bytes.

  AC-25 needs no brain at all: it clones the REAL content repository
  (``$ROSETTA_CONTENT_REPO``, default ``~/Projects/agent-content`` — data only, no scripts),
  publishes it with a sandbox ``HOME`` while a real audit hook watches for any read under a
  brain checkout, uploads the stage into local R2 through the publisher's own path, serves it
  from the REAL Worker in workerd over TLS, and syncs it onto the bare ``HOME``. The result is
  compared with the pre-move snapshot's inventory (``PRE_MOVE_SNAPSHOT_ID``), so "byte-identical"
  is one digest over every file, not a count.

Run:
    python3 scripts/kb-delivery/test_publish_snapshot.py            # every requirement
    python3 scripts/kb-delivery/test_publish_snapshot.py -k ac3     # one requirement
    python3 scripts/kb-delivery/test_publish_snapshot.py -k ac25    # no brain, no token needed
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[1]
PUBLISHER = HERE / "publish_snapshot.py"
SYNC = HERE / "sync_agents.py"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import test_sync_agents as sync_suite  # noqa: E402  (the workerd-over-TLS helper both suites share)
WRANGLER = PLUGIN / "worker" / "node_modules" / ".bin" / "wrangler"
PAYLOAD_DIRS = ("targets", "kb", "docs")
METADATA_FILES = ("manifest.json", "files.json", "scan-result.json")
R2_BUCKET = "rosetta-kb-mcp-snapshots"
EXPECTED_AGENTS = 293
EXPECTED_KBS = 65
EXPECTED_EXCLUDED = {"*.bak": 6, "*.log": 3, ".history/": 3}
SNAPSHOT_ID_RE = re.compile(r"^[0-9a-f]{12}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PATTERNS = ["sk-", "ghp_", "AKIA", "BEGIN PRIVATE KEY"]
OTHER_TARGETS = ("opencode", "grok", "droid", "rogueone")

# ── the pre-move snapshot (AC-25's reference) ──────────────────────────
# The last publish of the authoring checkout before the content moved out (brain HEAD 306a2efe5,
# 2026-11-15, targets claude,codex): snapshot 4ee128bc60e4 over 694 payload files. Its inventory
# is these constants. The digest is sha256 over sorted "<path> <sha256>\n" lines — the same
# arithmetic the publisher uses to derive a snapshot id — so an equal digest means every one of
# those files matches byte for byte, not merely that the counts agree.
PRE_MOVE_SNAPSHOT_ID = "4ee128bc60e4"
PRE_MOVE_TARGETS_DIGEST = "d0e7558f314d02f977b32a2e678e39e113b44f0e08c2544d777fa730e516775f"
PRE_MOVE_TARGET_FILES = {"claude": 336, "codex": 293}
PRE_MOVE_KB_DIGEST = "204631beafa0df4766f1faad3e8b713657043512f90042a18c23fae0ed53791c"
PRE_MOVE_KB_SLUGS = (
    "agent-intel", "ai-sdk", "assistant-ui", "astro", "betterauth", "bun",
    "clean-code", "cloudflare-agents", "cloudflare-durable-objects-ios", "cloudflare-realtime",
    "cloudflare-workers", "cmux", "cmux-codebase", "deepseek-harness", "docker-agent", "doppler",
    "electron", "evals", "expo-ai-elements", "flue", "flyio", "github-agentic-workflow", "gpui",
    "grok-build-tui", "hatchet", "herdr", "herdr-app", "herdr-core", "herdrdev", "hetzner-cloud",
    "hubspot-crm", "jev", "kotlin", "langfuse", "livekit", "logging-best-practices", "mastra",
    "mcp-python-sdk", "modal", "morph-cloud", "n8n", "nextjs", "nextjs-cloudflare", "openhands",
    "pi-packages", "project-management", "quickbooks", "react-native-reusables", "restate", "rig",
    "rust", "rust-acp", "rust-mcp", "shadcn", "shadcn-ai-elements", "simpro-api", "spacetime-db",
    "supabase", "sweetui", "swift", "tart", "tauri", "taurpc", "techwriting", "trpc",
)
# The graph agentic-rosetta held the only copy of; the move added it to the content repository
# (CONTENTMOVE-01), so a publish of today's content carries one more KB than the pre-move snapshot.
CONTENT_KB_ADDITIONS = ("go",)
CONTENT_REPO_ENV = "ROSETTA_CONTENT_REPO"

# The publisher's own audit probe: a real `sys.addaudithook` observer on PYTHONPATH, so "no read
# under a brain checkout" is watched rather than asserted. Nothing is stubbed — the publish runs
# unchanged, and the hook only appends the paths it sees.
AUDIT_PROBE_SOURCE = '''\
import os
import sys

_watch = [part for part in os.environ.get("AC25_WATCH", "").split(",") if part]
_log = os.environ.get("AC25_OPEN_LOG")
if _watch and _log:
    _prefixes = [os.path.realpath(part) for part in _watch]

    def _observed(event, args):
        if event not in ("open", "os.listdir", "os.scandir") or not args:
            return
        raw = args[0]
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        elif not isinstance(raw, str):
            return
        try:
            resolved = os.path.realpath(raw)
        except (OSError, ValueError):
            return
        if not any(resolved == p or resolved.startswith(p + os.sep) for p in _prefixes):
            return
        with open(_log, "a", encoding="utf-8") as handle:
            handle.write(f"{event} {resolved}\\n")

    sys.addaudithook(_observed)
'''


def content_repo() -> Path:
    """The content repository whose fresh clone AC-25 publishes (data only, no machinery)."""
    raw = os.environ.get(CONTENT_REPO_ENV) or (Path.home() / "Projects" / "agent-content")
    root = Path(raw).expanduser()
    missing = [name for name in ("agents", ".rosetta/docs") if not (root / name).is_dir()]
    if missing:
        raise AssertionError(
            f"AC-25 publishes a real content repository; {root} is missing {missing}. Set "
            f"${CONTENT_REPO_ENV} to a checkout holding agents/ and .rosetta/docs/."
        )
    return root


def source_root() -> Path:
    raw = os.environ.get("ROSETTA_PUBLISH_ROOT") or (Path.home() / "Projects/brain")
    root = Path(raw).expanduser()
    required = ("agents", "scripts/agentshare.py", ".rosetta/docs")
    missing = [name for name in required if not (root / name).exists()]
    if missing:
        raise AssertionError(
            f"this suite proves the publisher against a real consumer checkout; {root} is missing "
            f"{missing}. Set ROSETTA_PUBLISH_ROOT to a checkout that has agents/, "
            "scripts/agentshare.py and .rosetta/docs/."
        )
    return root


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def digest_of(files: dict[str, str]) -> str:
    """One digest over ``{path: sha256}``: equal digests mean every file matches byte for byte."""
    lines = "".join(f"{path} {digest}\n" for path, digest in sorted(files.items()))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def payload_hashes(stage: Path) -> dict[str, str]:
    """``{stage-relative path: sha256}`` for every staged payload file."""
    found: dict[str, str] = {}
    for name in PAYLOAD_DIRS:
        base = stage / name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file():
                found[path.relative_to(stage).as_posix()] = sha256_of(path)
    return found


def payload_files(stage: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for name in PAYLOAD_DIRS:
        base = stage / name
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file():
                found[path.relative_to(stage).as_posix()] = path
    return dict(sorted(found.items()))


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class PublishSnapshotTests(unittest.TestCase):
    """One test per requirement id in the task file."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="publish-snapshot-test-"))
        self.addCleanup(sync_suite.remove_tree, self.tmp)
        self._consumer: Path | None = None

    # ── helpers ────────────────────────────────────────────────────────

    def consumer(self) -> Path:
        """The authoring checkout the brain-based requirements publish (AC-3/5/13/17).

        Resolved on demand, so AC-25 — which publishes a content clone on a machine with no
        brain at all — never touches it.
        """
        if self._consumer is None:
            self._consumer = source_root()
        return self._consumer

    def run_publisher(
        self, *args: str, expect: int | None = None, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        argv = [sys.executable, str(PUBLISHER), *args]
        proc = subprocess.run(argv, capture_output=True, text=True, env=env)
        if expect is not None:
            self.assertEqual(
                proc.returncode,
                expect,
                msg=f"command={argv}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}",
            )
        return proc

    def publish(
        self,
        stage: Path,
        *extra: str,
        root: Path | None = None,
        expect: int | None = 0,
        env: dict[str, str] | None = None,
    ):
        return self.run_publisher(
            "--root",
            str(root or self.consumer()),
            "--targets",
            "claude,codex",
            "--stage",
            str(stage),
            *extra,
            expect=expect,
            env=env,
        )

    def fresh_clone(self, dest: Path) -> Path:
        """A real ``git clone`` of the content repository: tracked content and nothing else."""
        source = content_repo()
        proc = subprocess.run(
            ["git", "clone", "--quiet", "--no-hardlinks", str(source), str(dest)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, f"git clone {source} failed: {proc.stderr}")
        return dest

    def audit_probe(self, watch: tuple[Path, ...]) -> tuple[dict[str, str], Path]:
        """``(env, log)`` — a real audit-hook observer for every access under ``watch``."""
        probe = self.tmp / "audit-probe"
        probe.mkdir()
        (probe / "sitecustomize.py").write_text(AUDIT_PROBE_SOURCE, encoding="utf-8")
        log = self.tmp / "audit-probe.log"
        env = {
            "PYTHONPATH": str(probe),
            "AC25_WATCH": ",".join(str(path) for path in watch),
            "AC25_OPEN_LOG": str(log),
        }
        return env, log

    def tls_certificate(self) -> tuple[Path, Path]:
        """A throwaway certificate, so the sync talks real TLS to the real Worker."""
        certfile = self.tmp / "cert.pem"
        keyfile = self.tmp / "key.pem"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                "-keyout", str(keyfile), "-out", str(certfile),
                "-subj", "/CN=127.0.0.1",
                "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost",
            ],
            check=True,
            capture_output=True,
        )
        return certfile, keyfile

    def scan_only(self, stage: Path, expect: int | None = None):
        return self.run_publisher("--stage", str(stage), "--scan-only", expect=expect)

    def checkout(self, dest: Path) -> Path:
        """Extract a real checkout of the consumer's HEAD commit at ``dest``."""
        dest.mkdir(parents=True, exist_ok=True)
        archive = subprocess.run(
            ["git", "-C", str(self.consumer()), "archive", "HEAD"], capture_output=True
        )
        self.assertEqual(archive.returncode, 0, f"git archive failed: {archive.stderr.decode()}")
        extract = subprocess.run(["tar", "-x", "-C", str(dest)], input=archive.stdout, capture_output=True)
        self.assertEqual(extract.returncode, 0, f"tar failed: {extract.stderr.decode()}")
        return dest

    def wrangler(self, *args: str, expect: int = 0) -> subprocess.CompletedProcess:
        """Run wrangler against local R2 storage; the transport the publisher uses."""
        self.assertTrue(WRANGLER.is_file(), f"the Worker's wrangler install is missing at {WRANGLER}")
        proc = subprocess.run(
            [str(WRANGLER), "r2", "object", *args], capture_output=True, text=True, cwd=str(WRANGLER.parents[1])
        )
        self.assertEqual(proc.returncode, expect, f"command=wrangler r2 object {' '.join(args)}\n{proc.stdout}\n{proc.stderr}")
        return proc

    def r2_get(self, persist: Path, key: str) -> bytes:
        """Read one object back out of local R2 through the CLI."""
        out = self.tmp / f"read-{key.replace('/', '-')}"
        self.wrangler(
            "get",
            f"{R2_BUCKET}/{key}",
            "--file",
            str(out),
            "--local",
            "--persist-to",
            str(persist),
        )
        return out.read_bytes()

    def publish_to_local_r2(
        self, stage: Path, persist: Path, *, targets: str = "claude", env: dict | None = None, expect: int = 0
    ) -> subprocess.CompletedProcess:
        """Run the publisher's real R2 path against local storage."""
        argv = [
            sys.executable,
            str(PUBLISHER),
            "--root",
            str(self.consumer()),
            "--targets",
            targets,
            "--stage",
            str(stage),
            "--r2-bucket",
            R2_BUCKET,
            "--r2-local",
            "--persist-to",
            str(persist),
            "--wrangler",
            str(WRANGLER),
        ]
        proc = subprocess.run(argv, capture_output=True, text=True, env=env)
        if expect is not None:
            self.assertEqual(
                proc.returncode,
                expect,
                msg=f"command={argv}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}",
            )
        return proc

    @staticmethod
    def grep_tree(root: Path, needles: tuple[str, ...]) -> list[str]:
        """Every file under ``root`` whose bytes carry one of ``needles``."""
        hits: list[str] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            body = path.read_bytes()
            if any(needle.encode() in body for needle in needles):
                hits.append(path.relative_to(root).as_posix())
        return hits

    @staticmethod
    def grep_tracked_repo(needles: tuple[str, ...]) -> list[str]:
        """Every git-tracked file in the plugin checkout whose bytes carry a needle."""
        listing = subprocess.run(["git", "-C", str(PLUGIN), "ls-files", "-z"], capture_output=True)
        hits: list[str] = []
        for raw in listing.stdout.split(b"\0"):
            if not raw:
                continue
            relative = raw.decode("utf-8", "replace")
            path = PLUGIN / relative
            if not path.is_file():
                continue
            body = path.read_bytes()
            if any(needle.encode() in body for needle in needles):
                hits.append(relative)
        return hits

    # ── AC-4 ───────────────────────────────────────────────────────────

    def test_ac4_r2_s3_keys_stay_in_the_environment(self) -> None:
        """Exported R2 S3 keys reach no tracked file, no staged file and no publish log line."""
        # Derived, not typed in: a literal would collide with the "no key in a
        # tracked file" assertion below, and the value a test writes down is
        # exactly the kind of value that leaks into a repo.
        key_id = "ac4keyid" + hashlib.sha256(b"ac4-test-key-id").hexdigest()[:24]
        secret = "ac4secret" + hashlib.sha256(b"ac4-test-secret").hexdigest()[:32]
        needles = (key_id, secret)
        stage = self.tmp / "stage"
        persist = self.tmp / "state"

        # The operator exports R2 S3 keys; the publish still uploads for real
        # (wrangler carries its own OAuth credential, so the keys are never
        # needed, logged, staged or handed to a child process).
        env = {**os.environ, "R2_ACCESS_KEY_ID": key_id, "R2_SECRET_ACCESS_KEY": secret}
        proc = self.publish_to_local_r2(stage, persist, env=env)
        log = proc.stdout + proc.stderr

        # A publish really happened: the pointer, the snapshot metadata and a
        # payload blob all read back out of R2, and the pointer was written last.
        manifest = read_json(stage / "manifest.json")
        pointer = json.loads(self.r2_get(persist, "current.json"))
        self.assertEqual(pointer["snapshot_id"], manifest["snapshot_id"])
        self.assertEqual(pointer["files"], len(payload_files(stage)))
        self.assertRegex(pointer["manifest_sha256"], SHA256_RE)
        self.assertEqual(
            self.r2_get(persist, f"snapshots/{manifest['snapshot_id']}/manifest.json").decode(),
            (stage / "manifest.json").read_text(encoding="utf-8"),
        )
        agent = manifest["agents"][0]
        sha = read_json(stage / "files.json")["files"][agent["path"]]["sha256"]
        self.assertEqual(self.r2_get(persist, f"blobs/{sha}"), (stage / agent["path"]).read_bytes())
        self.assertGreater(len(manifest["agents"]), 0)
        self.assertIn("blobs", proc.stdout)
        # The upload log's last line names the pointer, i.e. it was written last.
        self.assertIn("current.json", proc.stdout.strip().splitlines()[-1])
        print(f"[ac4] publish log (last 4 lines):\n" + "\n".join(proc.stdout.strip().splitlines()[-4:]))
        print(f"[ac4] pointer read back from R2: {json.dumps(pointer, sort_keys=True)}")
        print(f"[ac4] manifest read back from R2: {len(manifest['agents'])} agents, {len(manifest['kbs'])} kbs")
        print(f"[ac4] blob {sha} read back: {len((stage / agent['path']).read_bytes())} bytes")

        # Zero matches anywhere the value could be committed, staged or logged.
        tracked_hits = self.grep_tracked_repo(needles)
        stage_hits = self.grep_tree(stage, needles)
        log_hits = [needle for needle in needles if needle in log]
        print(f"[ac4] git ls-files matches: {tracked_hits}")
        print(f"[ac4] staged-tree matches: {stage_hits}")
        print(f"[ac4] publish-log matches: {log_hits}")
        self.assertEqual(tracked_hits, [])
        self.assertEqual(stage_hits, [])
        self.assertEqual(log_hits, [])
        # The upload never reads a key from a file and never lets one reach a child.
        source = PUBLISHER.read_text(encoding="utf-8")
        self.assertIn('R2_SECRET_ENV = ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")', source)
        self.assertIn("env.pop(name, None)", source)

        # Negative control: the same grep does find a planted copy, so the zero
        # matches above are the gate working, not a gate that cannot see.
        planted = self.tmp / "leak-probe"
        (planted / "nested").mkdir(parents=True)
        (planted / "nested" / "leak.txt").write_text(f"{key_id}\n{secret}\n", encoding="utf-8")
        self.assertEqual(self.grep_tree(planted, needles), ["nested/leak.txt"])

    # ── AC-13 ──────────────────────────────────────────────────────────

    def test_ac13_manifest_catalog_and_fail_closed(self) -> None:
        """A real brain checkout yields 293 agents / 65 KBs, a stable id, and fails closed."""
        stage = self.tmp / "stage"
        first = self.publish(stage)
        manifest = read_json(stage / "manifest.json")

        self.assertEqual(len(manifest["agents"]), EXPECTED_AGENTS)
        self.assertEqual(len(manifest["kbs"]), EXPECTED_KBS)
        self.assertEqual(manifest["excluded"], EXPECTED_EXCLUDED)
        self.assertRegex(manifest["snapshot_id"], SNAPSHOT_ID_RE)
        self.assertIn(f"snapshot_id: {manifest['snapshot_id']}", first.stdout)
        self.assertEqual(manifest["targets"]["claude"]["dest"], ".claude/agents")
        self.assertEqual(manifest["targets"]["codex"]["dest"], ".codex/agents")

        # files.json carries a correct sha256 for every staged payload file.
        files_doc = read_json(stage / "files.json")
        staged = payload_files(stage)
        self.assertEqual(len(files_doc["files"]), len(staged))
        self.assertEqual(set(files_doc["files"]), set(staged))
        for relative, path in staged.items():
            self.assertEqual(files_doc["files"][relative]["sha256"], sha256_of(path), relative)
            self.assertEqual(files_doc["files"][relative]["bytes"], path.stat().st_size, relative)
        self.assertEqual(files_doc["snapshot_id"], manifest["snapshot_id"])
        first_files_json = (stage / "files.json").read_bytes()

        # A second run with no changes prints the same 12-hex snapshot id.
        second = self.publish(stage)
        manifest_again = read_json(stage / "manifest.json")
        self.assertEqual(manifest_again["snapshot_id"], manifest["snapshot_id"])
        self.assertIn(f"snapshot_id: {manifest['snapshot_id']}", second.stdout)
        self.assertEqual((stage / "files.json").read_bytes(), first_files_json)

        # Fail closed when the root holds no agents to project: name the missing tree.
        # (CONTENTPUB-01 moved projection into this repo, so a consumer no longer needs
        # scripts/agentshare.py for a publish to succeed; what it must have is content.)
        bare = self.tmp / "no-content-consumer"
        bare.mkdir()
        bad = self.publish(self.tmp / "stage-bad", root=bare, expect=1)
        self.assertIn("no agents/ directory", bad.stderr)
        self.assertFalse((self.tmp / "stage-bad" / "manifest.json").exists())

        # Fail closed when agents/ holds no agent prompts: name the reason.
        empty = self.tmp / "empty-consumer"
        (empty / "agents").mkdir(parents=True)
        zero = self.publish(self.tmp / "stage-zero", root=empty, expect=1)
        self.assertIn("zero agent prompts", zero.stderr)
        self.assertFalse((self.tmp / "stage-zero" / "manifest.json").exists())

        # The publisher never hardcodes the consumer location.
        self.assertNotIn("Projects/brain", PUBLISHER.read_text(encoding="utf-8"))

    # ── AC-5 ───────────────────────────────────────────────────────────

    def test_ac5_explicit_targets_and_path_independent_snapshot(self) -> None:
        """Two checkouts of the same commit stage identical snapshots with no home path line."""
        one = self.checkout(self.tmp / "checkout-a")
        two = self.checkout(self.tmp / "deeper" / "nested" / "checkout-b")
        self.assertNotEqual(one, two)

        stage_one = self.tmp / "stage-one"
        stage_two = self.tmp / "stage-two"
        first = self.publish(stage_one, root=one)
        second = self.publish(stage_two, root=two)

        # One target is projected at a time, with an explicit --target, and only the requested ones.
        for proc in (first, second):
            self.assertIn("--target claude", proc.stdout)
            self.assertIn("--target codex", proc.stdout)
            for other in OTHER_TARGETS:
                self.assertNotIn(f"--target {other}", proc.stdout)
        for stage in (stage_one, stage_two):
            self.assertEqual(sorted(p.name for p in (stage / "targets").iterdir()), ["claude", "codex"])

        manifest_one = read_json(stage_one / "manifest.json")
        manifest_two = read_json(stage_two / "manifest.json")
        self.assertRegex(manifest_one["snapshot_id"], SNAPSHOT_ID_RE)
        self.assertRegex(manifest_two["snapshot_id"], SNAPSHOT_ID_RE)
        self.assertEqual(manifest_one["snapshot_id"], manifest_two["snapshot_id"])

        # The two staged trees are byte-identical, not merely catalogued alike.
        files_one = {rel: sha256_of(path) for rel, path in payload_files(stage_one).items()}
        files_two = {rel: sha256_of(path) for rel, path in payload_files(stage_two).items()}
        self.assertEqual(files_one, files_two)

        # Every Codex TOML has a consumer-relative provenance line and no absolute path.
        codex = sorted((stage_one / "targets" / "codex").glob("*.toml"))
        self.assertEqual(len(codex), EXPECTED_AGENTS)
        for toml_path in codex:
            head = toml_path.read_text(encoding="utf-8").split("\n", 1)[0]
            self.assertTrue(head.startswith("# Generated from agents/"), f"{toml_path.name}: {head!r}")
            self.assertFalse(head.startswith("# Generated from /"), f"{toml_path.name}: {head!r}")
        self.assertEqual(
            [p.name for p in (stage_one / "targets" / "codex").glob("*.toml") if "# Generated from /" in p.read_text(encoding="utf-8")],
            [],
        )

        # No staged file carries the checkout path the publisher itself could have injected.
        for stage, checkout_dir in ((stage_one, one), (stage_two, two)):
            marker = str(checkout_dir)
            for relative, path in payload_files(stage).items():
                self.assertNotIn(marker.encode(), path.read_bytes(), relative)

    # ── AC-3 ───────────────────────────────────────────────────────────

    def test_ac3_credential_scan_covers_vendored_docs_and_blocks_deploy(self) -> None:
        """A credential planted in a document inside the stage is found and invalidates it."""
        stage = self.tmp / "stage"
        self.publish(stage)

        baseline = read_json(stage / "scan-result.json")
        self.assertEqual(baseline["status"], "clean", baseline["findings"])
        self.assertTrue((stage / "manifest.json").is_file())

        # A vendored document lands beside the projections (doc vendoring writes docs/); the scan
        # is tree-recursive, so whatever a vendoring pass stages here is covered.
        planted = stage / "docs" / "brain" / "docs" / "vendored-credential-fixture.md"
        planted.parent.mkdir(parents=True, exist_ok=True)
        planted.write_text(
            "# vendored fixture\n\naws_access_key_id = AKIA" + "Q" * 16 + "\n", encoding="utf-8"
        )

        blocked = self.scan_only(stage, expect=1)
        self.assertIn("docs/brain/docs/vendored-credential-fixture.md", blocked.stderr)

        scanned = read_json(stage / "scan-result.json")
        self.assertEqual(scanned["status"], "findings")
        self.assertEqual(len(scanned["findings"]), 1, scanned["findings"])
        finding = scanned["findings"][0]
        self.assertEqual(finding["path"], "docs/brain/docs/vendored-credential-fixture.md")
        self.assertEqual(finding["pattern"], "AKIA")
        self.assertEqual(scanned["files_scanned"], len(payload_files(stage)))
        self.assertGreaterEqual(scanned["files_scanned"], EXPECTED_AGENTS)

        # The stage cannot be deployed from: the deployable pointer is gone.
        self.assertFalse((stage / "manifest.json").exists())
        self.assertFalse((stage / "files.json").exists())

        # Negative control: the same scan over the same stage without the plant is clean, and a
        # republish restores the deployable manifest at the original snapshot id.
        planted.unlink()
        clean_again = self.scan_only(stage, expect=0)
        self.assertIn("scan clean", clean_again.stdout)
        self.assertEqual(read_json(stage / "scan-result.json")["findings"], [])
        self.publish(stage)
        self.assertEqual(read_json(stage / "manifest.json")["snapshot_id"], baseline["snapshot_id"])

    # ── AC-17 ──────────────────────────────────────────────────────────

    def test_ac17_scan_record_for_the_owner(self) -> None:
        """The pre-deploy scan records its four patterns, coverage and time for the owner."""
        stage = self.tmp / "stage"
        proc = self.publish(stage)
        scan = read_json(stage / "scan-result.json")
        manifest = read_json(stage / "manifest.json")

        self.assertEqual(scan["patterns"], PATTERNS)
        # Every pattern is backed by at least one shape rule the reader can inspect.
        self.assertEqual({rule["pattern"] for rule in scan["rules"]}, set(PATTERNS))
        self.assertEqual(len(scan["rules"]), 6)
        self.assertEqual(scan["status"], "clean")
        self.assertEqual(scan["findings"], [])
        self.assertEqual(scan["files_scanned"], len(payload_files(stage)))
        self.assertGreaterEqual(scan["files_scanned"], 354)
        self.assertEqual(manifest["scan"]["patterns"], PATTERNS)
        self.assertEqual(manifest["scan"]["files_scanned"], scan["files_scanned"])

        # The record is written before the deploy pointer, so it always precedes a deploy.
        scanned_at = datetime.strptime(scan["scanned_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        published_at = datetime.strptime(manifest["published_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertLessEqual(scanned_at, published_at)
        self.assertLessEqual(scanned_at, datetime.now(timezone.utc))

        # The record is announced, not only written.
        self.assertIn("credential scan:", proc.stdout)
        self.assertIn(f"{len(PATTERNS)} patterns", proc.stdout)
        self.assertTrue((stage / "scan-result.json").stat().st_size > 0)

    # ── AC-25 ──────────────────────────────────────────────────────────

    def test_ac25_a_content_clone_publishes_and_syncs_like_the_pre_move_snapshot(self) -> None:
        """A fresh content clone, no brain on disk: publish → serve → sync → the pre-move bytes.

        The pre-move snapshot (``PRE_MOVE_SNAPSHOT_ID``) is the reference: this run must reach the
        same ``targets/`` bytes from a checkout that holds no machinery at all, with a sandbox
        ``HOME`` that contains no brain, and the device must then hold those exact bytes after a
        real sync through the real Worker in workerd.
        """
        clone = self.fresh_clone(self.tmp / "content-clone")

        # The clone is content and nothing else: no projector inside it, nothing untracked.
        self.assertFalse(
            (clone / "scripts" / "agentshare.py").exists(),
            "the content repository must not carry the projector; the publisher carries it",
        )
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(clone), "status", "--porcelain"], capture_output=True, text=True
            ).stdout.strip(),
            "",
            "the clone carries untracked files, so it is not the fresh clone the AC describes",
        )
        self.assertEqual(
            len([path for path in (clone / "agents").iterdir() if path.is_dir()]), EXPECTED_AGENTS
        )
        clone_ref = subprocess.run(
            ["git", "-C", str(clone), "rev-parse", "--short", "HEAD"], capture_output=True, text=True
        ).stdout.strip()

        home = self.tmp / "bare-home"
        home.mkdir()
        stage = self.tmp / "stage"

        # Watch the publisher's own file accesses through a real audit hook, and give it a HOME
        # that holds no checkout: the content clone is the only input it is given.
        brain = Path(os.path.expanduser("~")) / "Projects" / "brain"
        canary_dir = self.tmp / "watch-probe"
        canary_dir.mkdir()
        canary = canary_dir / "canary.txt"
        canary.write_text("the observer is live\n", encoding="utf-8")
        probe_env, probe_log = self.audit_probe((brain, clone, canary_dir))
        env = {**os.environ, "HOME": str(home), **probe_env}

        proc = self.publish(stage, root=clone, env=env)
        log = proc.stdout + proc.stderr
        self.assertIn("--target claude", proc.stdout)
        self.assertIn("project_agents", proc.stdout)
        self.assertEqual(
            sorted(path.name for path in home.iterdir()), [], "the publish wrote into the device HOME"
        )

        # 0 reads under a brain checkout. The observer's own control comes first: an access the
        # publish never made is recorded, so silence about `brain` means "not read".
        subprocess.run(
            [sys.executable, "-c", f"open({str(canary)!r}, encoding='utf-8').read()"],
            env=env, check=True, capture_output=True,
        )
        observed = probe_log.read_text(encoding="utf-8").splitlines() if probe_log.exists() else []
        brain_root = str(brain.resolve())
        brain_reads = [line for line in observed if brain_root in line]
        clone_reads = [line for line in observed if str(clone.resolve()) in line]
        print(f"[ac25] audit hook: {len(observed)} access(es), {len(clone_reads)} under the clone, "
              f"{len(brain_reads)} under {brain}")
        self.assertIn(f"open {canary.resolve()}", observed, "the audit hook observed nothing at all")
        self.assertEqual(brain_reads, [], "the publish read a brain checkout")
        self.assertGreaterEqual(len(clone_reads), EXPECTED_AGENTS, "the audit hook saw no agent reads")
        self.assertNotIn(str(brain), log, "the publish log names a brain checkout")

        manifest = read_json(stage / "manifest.json")
        self.assertRegex(manifest["snapshot_id"], SNAPSHOT_ID_RE)
        self.assertEqual(manifest["consumer_ref"], clone_ref)
        self.assertEqual(len(manifest["agents"]), EXPECTED_AGENTS)

        staged = payload_hashes(stage)
        targets = {path: digest for path, digest in staged.items() if path.startswith("targets/")}
        counts = {
            target: sum(1 for path in targets if path.startswith(f"targets/{target}/"))
            for target in PRE_MOVE_TARGET_FILES
        }
        self.assertEqual(counts, PRE_MOVE_TARGET_FILES)
        self.assertEqual(digest_of(targets), PRE_MOVE_TARGETS_DIGEST, "target bytes differ from the pre-move snapshot")
        self.assertEqual(sum(1 for path in targets if path.endswith("/agent.md")), EXPECTED_AGENTS)

        # Every knowledge graph the pre-move snapshot carried is byte-identical here; the content
        # repository adds only the graph agentic-rosetta held the only copy of.
        kbs = {path: digest for path, digest in staged.items() if path.startswith("kb/")}
        pre_move_kbs = {path: digest for path, digest in kbs.items() if path.split("/")[1] in PRE_MOVE_KB_SLUGS}
        self.assertEqual(len(pre_move_kbs), len(PRE_MOVE_KB_SLUGS))
        self.assertEqual(digest_of(pre_move_kbs), PRE_MOVE_KB_DIGEST, "a pre-move graph's bytes changed")
        self.assertEqual(sorted({path.split("/")[1] for path in kbs} - set(PRE_MOVE_KB_SLUGS)), list(CONTENT_KB_ADDITIONS))
        self.assertEqual(len(manifest["kbs"]), len(PRE_MOVE_KB_SLUGS) + len(CONTENT_KB_ADDITIONS))
        print(
            f"[ac25] published from {clone} ({clone_ref}): {len(manifest['agents'])} agents, "
            f"{len(manifest['kbs'])} KBs, snapshot {manifest['snapshot_id']}"
        )
        print(f"[ac25] targets digest {digest_of(targets)} == pre-move {PRE_MOVE_TARGETS_DIGEST}")

        # Serve that stage from the real Worker in workerd (over TLS, from the local R2 store the
        # publisher's own upload filled) and sync it onto the bare HOME.
        token = "ac25-" + secrets.token_hex(16)
        certfile, keyfile = self.tls_certificate()
        with sync_suite.workerd_snapshot(stage, token, certfile, keyfile) as url:
            sync_env = {key: value for key, value in os.environ.items() if key != "ROSETTA_MCP_URL"}
            sync_env.update(
                {"HOME": str(home), "ROSETTA_MCP_TOKEN": token, "SSL_CERT_FILE": str(certfile)}
            )
            argv = [sys.executable, str(SYNC), "--url", url, "--targets", "claude,codex"]
            print(f"$ {' '.join(argv)}")
            synced = subprocess.run(
                argv, capture_output=True, text=True, env=sync_env, cwd=str(PLUGIN),
                timeout=sync_suite.SYNC_TIMEOUT,
            )
            print(synced.stdout, end="")
            print(synced.stderr, end="")
            self.assertEqual(
                synced.returncode, 0,
                f"sync exited {synced.returncode}:\n{synced.stdout}\n{synced.stderr}",
            )

        device = {}
        for target in PRE_MOVE_TARGET_FILES:
            for relative, digest in sync_suite.tree_hashes(home / sync_suite.TARGET_DEST[target]).items():
                device[f"targets/{target}/{relative}"] = digest
        self.assertEqual(device, targets, "the device holds different bytes than the snapshot published")
        self.assertEqual(digest_of(device), PRE_MOVE_TARGETS_DIGEST, "the device differs from the pre-move snapshot")
        self.assertEqual(len(list((home / ".claude" / "agents").glob("*/agent.md"))), EXPECTED_AGENTS)
        self.assertEqual(len(list((home / ".codex" / "agents").glob("*.toml"))), EXPECTED_AGENTS)
        print(f"[ac25] device {home}: {len(device)} file(s), digest {digest_of(device)}")

        # Negative control: one edited prompt moves both the digest and the device comparison, so
        # the equality above is a comparison that can fail, not one that cannot see.
        victim = sorted(path for path in (clone / "agents").iterdir() if path.is_dir())[0] / "agent.md"
        victim.write_text(
            victim.read_text(encoding="utf-8") + "\n<!-- ac25 mutation probe -->\n", encoding="utf-8"
        )
        mutant_stage = self.tmp / "stage-mutant"
        self.publish(mutant_stage, root=clone, env=env)
        mutant = {path: digest for path, digest in payload_hashes(mutant_stage).items() if path.startswith("targets/")}
        self.assertNotEqual(digest_of(mutant), PRE_MOVE_TARGETS_DIGEST)
        differing = sorted(path for path in set(targets) | set(mutant) if targets.get(path) != mutant.get(path))
        self.assertEqual(
            differing,
            sorted([f"targets/claude/{victim.parent.name}/agent.md", f"targets/codex/{victim.parent.name}.toml"]),
        )
        print(f"[ac25] mutation control: editing {victim.parent.name}/agent.md differs in {differing}")


if __name__ == "__main__":
    unittest.main()
