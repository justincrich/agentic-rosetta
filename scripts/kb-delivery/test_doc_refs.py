#!/usr/bin/env python3
"""test_doc_refs.py — integration proofs for doc_refs.py (publish-time doc vendoring).

WHAT IS REAL HERE
  Every case starts from a REAL consumer checkout (``$ROSETTA_PUBLISH_ROOT``, default
  ``~/Projects/brain``): 293 agents whose prompts cite documents, and a resolver
  (``scripts/rogueone_bundle.py``) that decides what each citation means. The publisher stages
  that checkout with ``--vendor-docs``, the stage is uploaded into local R2 with the publisher's
  own upload path, the REAL Worker in workerd (``wrangler dev --local``, over TLS) serves it
  exactly as the deployed service serves a snapshot, and
  ``sync_agents.py`` mirrors it into a sandbox ``HOME`` that holds no checkout at all. Nothing
  here writes a manifest by hand, stubs a resolver, or fakes a download.

WHY THE SANDBOX HOME IS THE POINT
  The failure being prevented is a device that carries prompts pointing at files it does not
  have. So the proof is made *inside* a HOME with no ``~/Projects/brain``: a citation counts as
  resolved only when the file it names is on disk there.

ENVIRONMENT
  ROSETTA_PUBLISH_ROOT   optional: the consumer checkout (default ``~/Projects/brain``)

Run:
    python3 scripts/kb-delivery/test_doc_refs.py
    python3 scripts/kb-delivery/test_doc_refs.py -k ac18
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[1]
SYNC = HERE / "sync_agents.py"
PUBLISHER = HERE / "publish_snapshot.py"
DOC_REFS = HERE / "doc_refs.py"
sys.path.insert(0, str(HERE))
import test_sync_agents as sync_proof  # noqa: E402  (the workerd + projector harness, one home)

consumer_root = sync_proof.consumer_root
sha256_of = sync_proof.sha256_of
tree_hashes = sync_proof.tree_hashes
remove_tree = sync_proof.remove_tree
workerd_snapshot = sync_proof.workerd_snapshot

TOKEN_ENV = "ROSETTA_MCP_TOKEN"
EXPECTED_AGENTS = 293
PUBLISH_TIMEOUT = 1800
SYNC_TIMEOUT = 1800
# The plan recorded 118 citing agents / 81 distinct brain/docs paths when it was written; the
# consumer moves, so the proof asserts a fleet-scale floor and prints the exact counts it saw.
RECORDED_CITING_AGENTS = 118
RECORDED_DISTINCT_DOC_PATHS = 81
MIN_CITING_AGENTS = 110
MIN_DISTINCT_DOC_PATHS = 75
MIN_RESOLVED_CITATIONS = 500
SHARE_HOME = "~/.rosetta/share"
# A rewritten citation no longer matches the consumer's brain-root pattern (its lookbehind
# rejects `.rosetta/` after a `/`), so the rewrites are counted with their own two shapes:
# home-anchored (`~/…`) and home-relative (`.rosetta/share/…`).
REWRITTEN_RE = re.compile(r"~/\.rosetta/share/[\w@.\-/]+")
RELATIVE_SHARE_RE = re.compile(r"(?<![\w/~])\.rosetta/share/[\w@.\-/]+")


def load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"{path} could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def brain_rel(ref: str) -> str:
    """A citation with its brain-root prefix removed, so "is it a brain doc?" is one question."""
    for prefix in ("~/Projects/brain/", "~/.claude/brain/", "brain/"):
        if ref.startswith(prefix):
            return ref[len(prefix) :]
    return ref


class DocRefsTests(unittest.TestCase):
    """One test per requirement id in the task file."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.consumer = consumer_root()
        cls.doc_refs = load_module(DOC_REFS, "_doc_refs_under_test")
        cls.resolver = cls.doc_refs.load_resolver(cls.consumer)
        publisher = load_module(PUBLISHER, "_publisher_for_doc_refs_test")
        agentshare = publisher.load_agentshare(cls.consumer)
        cls.agents = agentshare.discover_agents(cls.consumer / "agents")
        if len(cls.agents) != EXPECTED_AGENTS:
            raise AssertionError(
                f"{cls.consumer}: {len(cls.agents)} agents, expected {EXPECTED_AGENTS}; this proof is "
                "pinned to the fleet the plan measured"
            )

        cls.tmp = Path(tempfile.mkdtemp(prefix="doc-refs-test-"))
        cls.tls_dir = Path(tempfile.mkdtemp(prefix="doc-refs-tls-"))
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

        cls.stage = cls.tmp / "stage"
        argv = [
            sys.executable, str(PUBLISHER), "--root", str(cls.consumer),
            "--targets", "claude", "--vendor-docs", "--stage", str(cls.stage),
        ]
        print(f"$ {' '.join(argv)}")
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=PUBLISH_TIMEOUT, cwd=str(PLUGIN))
        print(proc.stdout, end="")
        print(proc.stderr, end="")
        if proc.returncode != 0:
            raise AssertionError(f"publish --vendor-docs failed:\n{proc.stdout}\n{proc.stderr}")
        cls.manifest = json.loads((cls.stage / "manifest.json").read_text(encoding="utf-8"))
        cls.unresolved_refs = {entry["ref"] for entry in cls.manifest.get("unresolved") or []}

        cls.token = "doc-refs-workerd-token-91c4e2"
        cls.worker = workerd_snapshot(cls.stage, cls.token, cls.certfile, cls.keyfile)
        cls.url = cls.worker.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        with contextlib.suppress(Exception):
            cls.worker.__exit__(None, None, None)
        remove_tree(cls.tls_dir)
        remove_tree(cls.tmp)

    # ── helpers ────────────────────────────────────────────────────────

    def sync_home(self, name: str) -> tuple[Path, dict, subprocess.CompletedProcess]:
        """A sandbox HOME with no checkout, filled from the real Worker."""
        home = self.tmp / name
        home.mkdir(parents=True, exist_ok=True)
        env = {key: value for key, value in os.environ.items() if key != "ROSETTA_MCP_URL"}
        env["HOME"] = str(home)
        env[TOKEN_ENV] = self.token
        env["SSL_CERT_FILE"] = str(self.certfile)
        argv = [sys.executable, str(SYNC), "--url", self.url]
        proc = subprocess.run(argv, capture_output=True, text=True, env=env, cwd=str(PLUGIN), timeout=SYNC_TIMEOUT)
        print(f"$ HOME={home} {' '.join(argv)}")
        print(proc.stdout, end="")
        print(proc.stderr, end="")
        print(f"exit={proc.returncode}")
        self.assertEqual(proc.returncode, 0, msg=f"sync failed:\n{proc.stdout}\n{proc.stderr}")
        lines = [line for line in proc.stdout.splitlines() if line.strip().startswith("{")]
        self.assertTrue(lines, msg=f"no JSON summary on stdout:\n{proc.stdout}\n{proc.stderr}")
        return home, json.loads(lines[-1]), proc

    def citations(self, text: str) -> list[str]:
        """Every brain-root path token in ``text``, as the consumer's own pattern sees it."""
        found: list[str] = []
        for match in self.resolver._BRAIN_REF_RE.finditer(text):
            token = match.group(0)
            found.append(token.rstrip(".,;:"))
        return found

    def agent_citing(self, fragment: str) -> str:
        """An agent whose prompt really cites ``fragment`` (read from the checkout, not guessed)."""
        for agent in self.agents:
            source = Path(agent["source_path"])
            prompt = source / "agent.md" if source.is_dir() else source
            if fragment in prompt.read_text(encoding="utf-8"):
                return str(agent["name"])
        raise AssertionError(f"no agent in {self.consumer} cites {fragment!r}")

    # ── AC-18 ─────────────────────────────────────────────────────────

    def test_ac18_published_manifest_and_rewritten_prompt_on_a_sandboxed_home(self) -> None:
        """The manifest records what was vendored, and a synced prompt cites a file that exists."""
        manifest = self.manifest
        self.assertGreater(manifest["vendored_docs"], 0, "the manifest records no vendored documents")
        self.assertEqual(
            manifest["doc_vendoring"]["vendored_files"],
            manifest["share"]["files"],
            "the declared share file count and the vendored file count disagree",
        )
        self.assertEqual(manifest["share"]["dest"], ".rosetta/share")
        self.assertEqual(manifest["share"]["prefix"], "docs/")
        print(
            f"[ac18] manifest: {manifest['vendored_docs']} documents, "
            f"{manifest['doc_vendoring']['vendored_files']} files, "
            f"{len(manifest['unresolved'])} unresolved citation(s)"
        )

        # A citation brain cannot honour is named, not dropped: docs/kotlin does not exist.
        self.assertIn(
            "brain/docs/kotlin/compose-ui.md",
            self.unresolved_refs,
            "the manifest does not name the known-missing brain doc",
        )
        kotlin_entry = next(e for e in manifest["unresolved"] if e["ref"] == "brain/docs/kotlin/compose-ui.md")
        self.assertEqual(kotlin_entry["kind"], "unresolved")
        self.assertTrue(kotlin_entry["reason"])
        print(f"[ac18] unresolved cites: brain/docs/kotlin/compose-ui.md ({kotlin_entry['reason']})")

        # An agent that really cites a brain document, in a HOME with no checkout.
        name = self.agent_citing("brain/docs/mcp-rules/security.md")
        home, summary, _proc = self.sync_home("ac18-home")
        self.assertEqual(summary["snapshot_id"], manifest["snapshot_id"])
        self.assertEqual(summary["share"]["written"], manifest["share"]["files"])
        self.assertFalse((home / "Projects" / "brain").exists(), "the sandbox HOME holds a checkout")

        prompt_path = home / ".claude" / "agents" / name / "agent.md"
        self.assertTrue(prompt_path.is_file(), f"{prompt_path} was not synced")
        prompt = prompt_path.read_text(encoding="utf-8")
        rewritten = f"{SHARE_HOME}/brain/docs/mcp-rules/security.md"
        self.assertIn(rewritten, prompt, f"{name} does not cite the rewritten path")
        landed = home / ".rosetta" / "share" / "brain" / "docs" / "mcp-rules" / "security.md"
        self.assertTrue(landed.is_file(), f"{landed} does not exist after sync")
        self.assertEqual(
            sha256_of(landed),
            sha256_of(self.consumer / "docs" / "mcp-rules" / "security.md"),
            "the vendored document is not the consumer's bytes",
        )
        # The original citation is gone: no bare `brain/docs/...` left pointing at a checkout.
        self.assertNotIn("`brain/docs/mcp-rules/security.md`", prompt, "a citation was left un-rewritten")
        print(
            f"[ac18] observation: {landed} exists, "
            f"{landed.stat().st_size} bytes, sha256={sha256_of(landed)}"
        )
        cited = [line.strip() for line in prompt.splitlines() if "mcp-rules/security.md" in line]
        print(f"[ac18] observation: synced prompt line -> {cited[0]}")
        print(f"[ac18] {name} cites {rewritten}; the file is on disk with the consumer's sha256")

    # ── AC-19 ─────────────────────────────────────────────────────────

    def test_ac19_every_citation_resolves_or_is_named_across_the_citing_fleet(self) -> None:
        """Across every citing agent: zero silent drops, and nothing resolves outside the sandbox."""
        home, summary, _proc = self.sync_home("ac19-home")
        self.assertEqual(summary["snapshot_id"], self.manifest["snapshot_id"])
        self.assertFalse((home / "Projects" / "brain").exists(), "the sandbox HOME holds a checkout")
        self.assertFalse((home / ".claude" / "brain").exists())

        # How many agents cite a brain document, and how many distinct documents they name: the
        # plan recorded 118 agents / 81 paths; both are re-measured from the checkout.
        doc_citing: set[str] = set()
        distinct_docs: set[str] = set()
        for agent in self.agents:
            rewrites, resolved, missing = self.doc_refs.resolve_agent(
                resolver=self.resolver, agent=agent, consumer_root=self.consumer
            )
            doc_refs = [
                entry["source"] for entry in resolved if entry["source"].startswith("docs/")
            ] + [
                brain_rel(entry["ref"])
                for entry in missing
                if entry["kind"] == "unresolved" and brain_rel(entry["ref"]).startswith("docs/")
            ]
            if doc_refs:
                doc_citing.add(str(agent["name"]))
                distinct_docs.update(doc_refs)
        self.assertGreaterEqual(
            len(doc_citing), MIN_CITING_AGENTS, f"only {len(doc_citing)} agents cite a brain document"
        )
        self.assertGreaterEqual(
            len(distinct_docs),
            MIN_DISTINCT_DOC_PATHS,
            f"only {len(distinct_docs)} distinct brain documents are cited",
        )
        print(
            f"[ac19] {len(doc_citing)} citing agents, {len(distinct_docs)} distinct brain document paths "
            f"(plan recorded {RECORDED_CITING_AGENTS}/{RECORDED_DISTINCT_DOC_PATHS}); "
            f"{len(self.agents)} prompts checked"
        )

        # Every citation in the synced prompts is accounted for: it is either a rewritten path
        # that exists inside this HOME, or it is named in the manifest's unresolved list.
        resolved_count = 0
        unresolved_count = 0
        silent_drops: list[str] = []
        escaping: list[str] = []
        for prompt_path in sorted((home / ".claude" / "agents").rglob("*")):
            if not prompt_path.is_file():
                continue
            text = prompt_path.read_text(encoding="utf-8", errors="replace")
            where = prompt_path.relative_to(home).as_posix()
            for match in REWRITTEN_RE.finditer(text):
                token = match.group(0).rstrip(".,;:")
                landed = home / token[len("~/") :]
                if landed.exists():
                    resolved_count += 1
                else:
                    escaping.append(f"{where}: {token} -> {landed} is missing")
            for match in RELATIVE_SHARE_RE.finditer(text):
                token = match.group(0).rstrip(".,;:")
                landed = home / token
                if landed.exists():
                    resolved_count += 1
                else:
                    escaping.append(f"{where}: {token} -> {landed} is missing")
            for token in self.citations(text):
                if token in self.unresolved_refs:
                    unresolved_count += 1
                    continue
                silent_drops.append(f"{where}: {token}")

        self.assertEqual(silent_drops, [], "citations neither resolved nor named in the manifest")
        self.assertEqual(escaping, [], "a rewritten citation does not resolve inside the sandbox")
        self.assertGreaterEqual(
            resolved_count, MIN_RESOLVED_CITATIONS, f"only {resolved_count} citations were rewritten"
        )
        self.assertGreater(unresolved_count, 0, "an all-resolved fleet means the unresolved list is untested")
        print(
            f"[ac19] {resolved_count} citation(s) resolve inside the sandbox, "
            f"{unresolved_count} are named in the manifest, 0 silent drops, 0 escapes"
        )

        # The unresolved list is a real list of names, each with a reason, and at least one of
        # them is a citation the synced prompts still make (the list is not decoration).
        self.assertTrue(self.manifest["unresolved"])
        for entry in self.manifest["unresolved"]:
            self.assertTrue(entry["ref"])
            self.assertTrue(entry["reason"])
            self.assertTrue(entry["kind"])
        synced_text = "".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in (home / ".claude" / "agents").rglob("agent.md")
        )
        named_in_prompts = [entry["ref"] for entry in self.manifest["unresolved"] if entry["ref"] in synced_text]
        self.assertTrue(
            named_in_prompts,
            "no unresolved citation appears in a synced prompt, so nothing proves they were named instead of dropped",
        )
        print(f"[ac19] {len(named_in_prompts)} unresolved citation(s) still appear in synced prompts and are named")
        self.assertIn("share", summary, "the shared document tree was not synced")


if __name__ == "__main__":
    unittest.main()
