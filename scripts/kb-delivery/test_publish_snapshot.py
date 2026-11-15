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
PAYLOAD_DIRS = ("targets", "kb", "docs")
METADATA_FILES = ("manifest.json", "files.json", "scan-result.json")
EXPECTED_AGENTS = 293
EXPECTED_KBS = 65
EXPECTED_EXCLUDED = {"*.bak": 6, "*.log": 3, ".history/": 3}
SNAPSHOT_ID_RE = re.compile(r"^[0-9a-f]{12}$")
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
