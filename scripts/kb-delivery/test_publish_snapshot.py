#!/usr/bin/env python3
"""test_publish_snapshot.py — integration proofs for publish_snapshot.py.

Every test below stages the REAL consumer checkout named by ``$ROSETTA_PUBLISH_ROOT``
(default ``~/Projects/brain``) with that checkout's own ``scripts/agentshare.py``, and then
inspects the staged artifacts. Nothing is mocked: the catalogue numbers asserted here (293
agents, 65 knowledge bases, 6 ``*.bak``, 3 ``*.log``, 3 ``.history/``) are the real tree's, and
the snapshot id is computed from real bytes.

Run:
    python3 scripts/kb-delivery/test_publish_snapshot.py            # every requirement
    python3 scripts/kb-delivery/test_publish_snapshot.py -k ac3     # one requirement
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[1]
PUBLISHER = HERE / "publish_snapshot.py"
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
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = source_root()

    # ── helpers ────────────────────────────────────────────────────────

    def run_publisher(self, *args: str, expect: int | None = None) -> subprocess.CompletedProcess:
        argv = [sys.executable, str(PUBLISHER), *args]
        proc = subprocess.run(argv, capture_output=True, text=True)
        if expect is not None:
            self.assertEqual(
                proc.returncode,
                expect,
                msg=f"command={argv}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}",
            )
        return proc

    def publish(self, stage: Path, *extra: str, root: Path | None = None, expect: int | None = 0):
        return self.run_publisher(
            "--root",
            str(root or self.root),
            "--targets",
            "claude,codex",
            "--stage",
            str(stage),
            *extra,
            expect=expect,
        )

    def scan_only(self, stage: Path, expect: int | None = None):
        return self.run_publisher("--stage", str(stage), "--scan-only", expect=expect)

    def checkout(self, dest: Path) -> Path:
        """Extract a real checkout of the consumer's HEAD commit at ``dest``."""
        dest.mkdir(parents=True, exist_ok=True)
        archive = subprocess.run(
            ["git", "-C", str(self.root), "archive", "HEAD"], capture_output=True
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
            str(self.root),
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

        # Fail closed when the consumer has no scripts/agentshare.py: name the missing file.
        fixture = PLUGIN / "scripts" / "testdata" / "kb-project"
        self.assertTrue((fixture / "agents").is_dir(), "the committed consumer fixture is missing")
        bad = self.publish(self.tmp / "stage-bad", root=fixture, expect=1)
        self.assertIn("scripts/agentshare.py", bad.stderr)
        self.assertFalse((self.tmp / "stage-bad" / "manifest.json").exists())

        # Fail closed when agentshare reports zero agents: name the reason.
        empty = self.tmp / "empty-consumer"
        (empty / "scripts").mkdir(parents=True)
        shutil.copy2(self.root / "scripts" / "agentshare.py", empty / "scripts" / "agentshare.py")
        (empty / "agents").mkdir()
        zero = self.publish(self.tmp / "stage-zero", root=empty, expect=1)
        self.assertIn("zero agents", zero.stderr)
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

        # agentshare is always invoked with an explicit --target, and only for requested targets.
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


if __name__ == "__main__":
    unittest.main()
