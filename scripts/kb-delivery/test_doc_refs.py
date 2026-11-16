#!/usr/bin/env python3
"""test_doc_refs.py — integration proofs for doc_refs.py (publish-time doc vendoring).

WHAT IS REAL HERE
  A temp checkout of the content repository moved into a CUSTOM layout (``kb/graphs`` for
  knowledge bases, ``prompts`` for agent prompts) and given a version 2 config that declares
  that layout plus a ``citation_aliases`` table. The publisher stages it with ``--vendor-docs``
  as a real subprocess, the stage is served over real TLS at the routes the deployed service
  answers, and ``sync_agents.py`` mirrors it into a sandbox ``HOME`` that holds no checkout.
  Nothing here stubs a resolver or writes a manifest by hand.

WHY THE SANDBOX HOME IS THE POINT
  The failure being prevented is a device that carries prompts pointing at files it does not
  have. So the proof is made *inside* a HOME with no checkout: a citation counts as resolved
  only when the file it names is on disk there.

WHY THE CONFIG DECIDES
  Which file a citation means is the repo's own answer: a prefix it declares in
  ``citation_aliases``, or a repo-relative path. A prefix nobody declared stays in the prompt
  and is named in the manifest, which is what makes "resolved or acknowledged" checkable.

ENVIRONMENT
  ROSETTA_CONTENT_REPO   optional: the content checkout (default ``~/Projects/agent-content``)

Run:
    python3 scripts/kb-delivery/test_doc_refs.py
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[1]
SYNC = HERE / "sync_agents.py"
PUBLISHER = HERE / "publish_snapshot.py"
DOC_REFS = HERE / "doc_refs.py"
sys.path.insert(0, str(HERE))
import test_sync_agents as sync_proof  # noqa: E402  (the TLS + temp-checkout harness, one home)

sha256_of = sync_proof.sha256_of
tree_hashes = sync_proof.tree_hashes
remove_tree = sync_proof.remove_tree

TOKEN_ENV = "ROSETTA_MCP_TOKEN"
EXPECTED_AGENTS = 293
PUBLISH_TIMEOUT = 1800
SHARE_HOME = "~/.rosetta/share"
REWRITTEN_RE = re.compile(r"~/\.rosetta/share/shared/[\w@.\-/]+")


def run(argv: list[str], *, env: dict | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=PUBLISH_TIMEOUT)
    print(f"$ {' '.join(argv)}")
    print(proc.stdout[-3000:], end="")
    print(proc.stderr[-3000:], end="")
    return proc


class DocRefsTests(unittest.TestCase):
    """Doc vendoring reads the checkout's declared layout and citation table."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="doc-refs-test-"))
        cls.consumer = sync_proof.prepared_consumer(
            cls.tmp / "consumer", docs="kb/graphs", agents="prompts"
        )
        # The citation table is the checkout's declaration: a prefix, and the directory it means.
        config_path = cls.consumer / ".rosetta" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["citation_aliases"] = {"notes": "kb/graphs/notes"}
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

        # One document the prompt cites through the declared prefix, one beside the checkout.
        (cls.consumer / "kb" / "graphs" / "notes").mkdir(parents=True, exist_ok=True)
        (cls.consumer / "kb" / "graphs" / "notes" / "security.md").write_text(
            "# security rules\n\nno secrets in a prompt\n", encoding="utf-8"
        )
        (cls.consumer / "kb" / "graphs" / "local.md").write_text(
            "# local note\n", encoding="utf-8"
        )
        cited = "mcp-planner"
        agent_md = cls.consumer / "prompts" / cited / "agent.md"
        original = agent_md.read_text(encoding="utf-8")
        agent_md.write_text(
            original.rstrip("\n")
            + "\n\n## Documents\n\n"
            "- rules: `notes/security.md`\n"
            "- local: `kb/graphs/local.md`\n"
            "- absent: `notes/gone.md`\n"
            "- remote: https://example.com/spec.md\n",
            encoding="utf-8",
        )
        # A second agent whose citation the table cannot honour at all.
        second = cls.consumer / "prompts" / "mcp-reviewer"
        second.mkdir(parents=True, exist_ok=True)
        (second / "agent.md").write_text(
            "---\nname: mcp-reviewer\ndescription: cites nowhere/spec.md\n---\n"
            "Read `nowhere/spec.md` before reviewing.\n",
            encoding="utf-8",
        )
        cls.stage = cls.tmp / "stage"
        cls.token = sync_proof.FIXTURE_TOKEN
        proc = run([
            sys.executable, str(PUBLISHER), "--root", str(cls.consumer),
            "--targets", "claude", "--stage", str(cls.stage), "--vendor-docs",
        ])
        if proc.returncode != 0:
            raise AssertionError(f"publish --vendor-docs failed:\n{proc.stdout}\n{proc.stderr}")
        cls.manifest = json.loads((cls.stage / "manifest.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls) -> None:
        remove_tree(cls.tmp)

    # ── AC-18 ──────────────────────────────────────────────────────────

    def test_ac18_vendored_document_and_rewritten_prompt_on_a_sandboxed_home(self) -> None:
        """A cited document is vendored, rewritten, and present on a device with no checkout."""
        share = self.manifest.get("share")
        self.assertIsNotNone(share, "the manifest declares no share block for --vendor-docs")
        self.assertEqual(share["dest"], ".rosetta/share")
        self.assertEqual(share["prefix"], "docs/")

        staged_doc = self.stage / "docs" / "shared" / "kb" / "graphs" / "notes" / "security.md"
        self.assertTrue(staged_doc.is_file(), "the cited document was not vendored into the stage")
        self.assertEqual(
            staged_doc.read_bytes(),
            (self.consumer / "kb" / "graphs" / "notes" / "security.md").read_bytes(),
            "the vendored document is not byte-identical to the one the checkout carries",
        )

        staged_prompt = self.stage / "targets" / "claude" / "mcp-planner" / "agent.md"
        prompt = staged_prompt.read_text(encoding="utf-8")
        self.assertIn(f"{SHARE_HOME}/shared/kb/graphs/notes/security.md", prompt)
        self.assertNotIn("`notes/security.md`", prompt, "a citation was left un-rewritten")
        self.assertIn("https://example.com/spec.md", prompt, "a URL citation was rewritten")

        # A device with no checkout at all: the sync must place the document where the prompt
        # points, and the prompt it receives must still name that path.
        home = self.tmp / "home"
        home.mkdir()
        certfile, keyfile = self.tls_certificate()
        with sync_proof.served_stage(self.stage, self.token, certfile, keyfile) as url:
            env = {key: value for key, value in os.environ.items() if key != TOKEN_ENV}
            env.update({"HOME": str(home), TOKEN_ENV: self.token, "SSL_CERT_FILE": str(certfile)})
            synced = run([sys.executable, str(SYNC), "--url", url, "--targets", "claude"], env=env)
        self.assertEqual(synced.returncode, 0, synced.stderr)
        device_doc = home / ".rosetta" / "share" / "shared" / "kb" / "graphs" / "notes" / "security.md"
        self.assertTrue(device_doc.is_file(), "the vendored document never reached the device")
        self.assertEqual(sha256_of(device_doc), sha256_of(staged_doc))
        device_prompt = (home / ".claude" / "agents" / "mcp-planner" / "agent.md").read_text(
            encoding="utf-8"
        )
        for token in REWRITTEN_RE.findall(device_prompt):
            self.assertTrue(
                (home / token[len("~/"):]).is_file(),
                f"the device prompt cites {token}, which the device does not have",
            )

    # ── AC-19 ──────────────────────────────────────────────────────────

    def test_ac19_every_citation_resolves_or_is_named(self) -> None:
        """No citation is silently dropped: each is vendored, rewritten, or named with a reason."""
        unresolved = {entry["ref"]: entry for entry in self.manifest.get("unresolved") or []}
        self.assertIn("notes/gone.md", unresolved, "an unresolvable citation was not named")
        self.assertIn("nowhere/spec.md", unresolved, "an unresolvable citation was not named")
        for ref, entry in unresolved.items():
            self.assertTrue(entry["reason"], f"{ref}: named with no reason")
            self.assertGreaterEqual(entry["agents"], 1, f"{ref}: blamed on nobody")
        self.assertIn(
            "kb/graphs",
            unresolved["notes/gone.md"]["reason"],
            "the reason does not name the alias directory the config declared",
        )

        # The documents that DID resolve are recorded against the prompt that cited them.
        documents = {
            entry["source"]: entry
            for entry in (self.manifest.get("doc_vendoring") or {}).get("documents") or []
        }
        self.assertIn("kb/graphs/notes/security.md", documents)
        self.assertIn("kb/graphs/local.md", documents)

        # A citation that is left in a prompt is always NAMED. A device-local path (an author's
        # checkout) is deliberately left and reported; a bare path in a prompt is invisible to a
        # device, so the accounting is what keeps it honest.
        sys.path.insert(0, str(HERE))
        import doc_refs as under_test  # noqa: E402

        recorded = {entry["ref"] for entry in self.manifest.get("unresolved") or []}
        for path in sorted((self.stage / "targets").rglob("*")):
            if not path.is_file() or path.suffix not in (".md", ".toml"):
                continue
            body = path.read_text(encoding="utf-8", errors="replace")
            for match in under_test.CITATION_RE.finditer(body):
                token = match.group(0).rstrip(".,;:")
                if token.startswith(under_test.SHARE_PREFIXES):
                    continue  # the rewrite itself: the device path this module produced
                if token.startswith(("~", "/", "brain/")):
                    self.assertIn(token, recorded, f"{path}: {token} was left in the prompt unnamed")

        # The four delivery scripts carry no brain citation literal of their own.
        for name in ("publish_snapshot.py", "project_agents.py", "doc_refs.py", "sync_agents.py"):
            text = (HERE / name).read_text(encoding="utf-8")
            self.assertEqual(
                text.count("brain/docs"), 0, f"{name} still carries a brain citation literal"
            )

    # ── the config gate ────────────────────────────────────────────────

    def test_publish_refuses_a_checkout_that_declares_no_layout(self) -> None:
        """A checkout with no config is refused before a document is touched."""
        bare = self.tmp / "bare-consumer"
        (bare / "agents").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(bare)], check=True, capture_output=True)
        stage = self.tmp / "stage-bare"
        proc = run([
            sys.executable, str(PUBLISHER), "--root", str(bare), "--targets", "claude",
            "--stage", str(stage), "--vendor-docs",
        ])
        self.assertNotEqual(proc.returncode, 0, "a checkout with no config was published")
        self.assertIn(str(bare / ".rosetta" / "config.json"), proc.stderr)
        self.assertFalse((stage / "manifest.json").exists())

    def test_every_citation_resolves_through_the_config_not_a_builtin_prefix(self) -> None:
        """Removing the alias turns a resolved citation into a named one — the table is the answer."""
        # The same checkout with the alias removed: the prefix now means nothing.
        stripped = sync_proof.prepared_consumer(
            self.tmp / "consumer-stripped", docs="kb/graphs", agents="prompts"
        )
        agent_md = stripped / "prompts" / "mcp-planner" / "agent.md"
        agent_md.parent.mkdir(parents=True, exist_ok=True)
        agent_md.write_text(
            "---\nname: mcp-planner\n---\n\n- rules: `notes/security.md`\n", encoding="utf-8"
        )
        stage = self.tmp / "stage-stripped"
        proc = run([
            sys.executable, str(PUBLISHER), "--root", str(stripped), "--targets", "claude",
            "--stage", str(stage), "--vendor-docs",
        ])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        manifest = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
        refs = {entry["ref"] for entry in manifest.get("unresolved") or []}
        self.assertIn("notes/security.md", refs)
        self.assertFalse(
            (stage / "docs" / "shared" / "notes" / "security.md").exists(),
            "a prefix with no alias in the config was still resolved",
        )

    def tls_certificate(self) -> tuple[Path, Path]:
        certfile = self.tmp / "cert.pem"
        keyfile = self.tmp / "key.pem"
        if not certfile.is_file():
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


if __name__ == "__main__":
    unittest.main()
