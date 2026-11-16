#!/usr/bin/env python3
"""test_project_agents.py — AC-26 parity proof for the ported agent projector.

WHAT IS REAL HERE
  The port is compared against the projector it replaces: brain's own ``scripts/agentshare.py``,
  run as a real CLI over a real consumer checkout (``$ROSETTA_PUBLISH_ROOT``, default
  ``~/Projects/brain``) into a sandbox ``HOME``. ``project_agents.py`` runs over the SAME
  checkout into a SECOND sandbox ``HOME``. Every file of every target tree is then compared by
  sha256, and every one of the fleet's agents must be present in all five trees. Nothing is
  mocked: the oracle is the program the port has to be interchangeable with.

WHY BOTH HOMES ARE SEEDED IDENTICALLY
  Two targets do more than write a tree: OpenCode removes ``agent.<name>.model`` from
  ``opencode.json`` (its inherit signal is key absence), and Grok rewrites the managed
  ``[subagents.models]`` table in ``config.toml`` and deletes its own stale profiles from the
  folder Grok scans. A tree-only comparison would pass while the port silently stopped doing
  those, so both HOMEs start from the same device state — two model pins (one for a published
  agent, one for an agent nobody publishes) and one legacy profile carrying the projector's
  marker — and the side effects are compared byte for byte as well.

ENVIRONMENT
  ROSETTA_PUBLISH_ROOT   optional: the consumer checkout (default ``~/Projects/brain``)

Run:
    python3 scripts/kb-delivery/test_project_agents.py -k ac26
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[1]
PROJECTOR = HERE / "project_agents.py"
sys.path.insert(0, str(HERE))
import project_agents  # noqa: E402  (the module under test: its API is what the test drives)
import test_sync_agents as harness  # noqa: E402  (shared temp-tree helpers, one home)

sha256_of = harness.sha256_of
tree_hashes = harness.tree_hashes
remove_tree = harness.remove_tree

MIN_AGENTS = 293
PROJECT_TIMEOUT = 900
#: What a target tree must hold for every agent: claude keeps a directory per agent.
AGENT_ENTRY = {"claude": "{name}/agent.md"}
for _target in ("codex", "opencode", "droid", "grok"):
    AGENT_ENTRY[_target] = "{name}" + project_agents.TARGET_SUFFIX[_target]
LEGACY_PROFILE = "legacy-from-an-older-projector"


def consumer_root() -> Path:
    """The real checkout the parity is measured against (its agents/ and its agentshare.py)."""
    raw = os.environ.get("ROSETTA_PUBLISH_ROOT") or (Path.home() / "Projects/brain")
    root = Path(raw).expanduser()
    required = ("agents", "scripts/agentshare.py")
    missing = [name for name in required if not (root / name).exists()]
    if missing:
        raise AssertionError(
            f"this suite proves parity against a real consumer checkout; {root} is missing "
            f"{missing}. Set ROSETTA_PUBLISH_ROOT to a checkout that has agents/ and "
            "scripts/agentshare.py."
        )
    return root


def seed_device(home: Path) -> None:
    """The device state both projectors start from: model pins and a legacy Grok profile."""
    home.mkdir(parents=True, exist_ok=True)
    config_path = home / ".config" / "opencode" / "opencode.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "theme": "system",
                "agent": {
                    "mcp-planner": {"model": "zai/glm-5.3", "description": "pinned by hand"},
                    "nextjs-reviewer": {"model": "openai/gpt-6.1-sol"},
                    "not-a-published-agent": {"model": "keep/me"},
                },
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    legacy_dir = home / ".grok" / "agents"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / f"{LEGACY_PROFILE}.md").write_text(
        f"---\nname: {LEGACY_PROFILE}\ndescription: written by an older projector\n"
        "agentshare: true\n---\n\nold profile\n",
        encoding="utf-8",
    )
    (legacy_dir / "my-own-agent.md").write_text(
        "---\nname: my-own-agent\ndescription: the user's own file\n---\n\nmine\n",
        encoding="utf-8",
    )


class ProjectorParityProof(unittest.TestCase):
    """AC-26: the port's output equals real agentshare output for the whole fleet."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="project-agents-parity-"))

    def tearDown(self) -> None:
        # The OpenCode target plants a `uchg` sentinel; clear it before removing the tree.
        remove_tree(self.tmp)

    # ── the two runs ──────────────────────────────────────────────────

    def run_port(self, consumer: Path, home: Path, target: str) -> subprocess.CompletedProcess:
        argv = [
            sys.executable,
            str(PROJECTOR),
            "--root",
            str(consumer),
            "--target",
            target,
            "--home",
            str(home),
        ]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=PROJECT_TIMEOUT)
        print(f"$ project_agents.py --root {consumer.name} --target {target} --home {home.name}")
        print(proc.stdout, end="")
        print(proc.stderr, end="")
        self.assertEqual(proc.returncode, 0, msg=f"project_agents.py --target {target} failed")
        return proc

    def run_agentshare(self, consumer: Path, home: Path, target: str) -> subprocess.CompletedProcess:
        """The oracle: the consumer's own projector, same checkout, its own sandbox HOME."""
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["BRAIN_ROOT"] = str(consumer)
        for name in ("GROK_HOME", "ROGUEONE_SHARE"):
            env.pop(name, None)  # an inherited config dir would escape the sandbox HOME
        argv = [sys.executable, str(consumer / "scripts" / "agentshare.py"), "--target", target]
        proc = subprocess.run(
            argv, cwd=str(consumer), env=env, capture_output=True, text=True, timeout=PROJECT_TIMEOUT
        )
        print(f"$ HOME={home.name} BRAIN_ROOT={consumer.name} agentshare.py --target {target}")
        print(proc.stdout, end="")
        print(proc.stderr, end="")
        self.assertEqual(proc.returncode, 0, msg=f"agentshare --target {target} failed")
        return proc

    # ── AC-26 ─────────────────────────────────────────────────────────

    def test_ac26_five_target_parity_with_real_agentshare(self) -> None:
        """Every file of all five target trees is sha256-identical to a real agentshare run."""
        consumer = consumer_root()
        ported_home = self.tmp / "ported-home"
        reference_home = self.tmp / "reference-home"
        for home in (ported_home, reference_home):
            seed_device(home)

        agents = project_agents.discover_agents(consumer / "agents")
        # 293 is what the fleet held when this AC was written; the consumer moves, so the floor
        # is asserted and every discovered agent is required in every tree (parity is the claim).
        self.assertGreaterEqual(
            len(agents), MIN_AGENTS, f"the checkout's fleet is {len(agents)} agents, under {MIN_AGENTS}"
        )
        names = {agent["name"] for agent in agents}

        # The device state that makes the side effects observable, captured before either run.
        seeded_config = sha256_of(ported_home / ".config" / "opencode" / "opencode.json")

        trees: dict[str, dict[str, str]] = {}
        reference: dict[str, dict[str, str]] = {}
        for target in project_agents.TARGETS:
            self.assertFalse(
                (ported_home / project_agents.TARGET_DEST[target]).exists(),
                f"{target}: the target tree exists before the projector ran, so 'no change' proves nothing",
            )
            self.run_port(consumer, ported_home, target)
            self.run_agentshare(consumer, reference_home, target)
            trees[target] = tree_hashes(ported_home / project_agents.TARGET_DEST[target])
            reference[target] = tree_hashes(reference_home / project_agents.TARGET_DEST[target])

        compared = 0
        differing: dict[str, list[str]] = {}
        for target in project_agents.TARGETS:
            produced = trees[target]
            expected = reference[target]
            self.assertGreater(len(expected), 0, f"{target}: agentshare produced an empty tree")

            # Every agent of the fleet is in the tree, in the shape its harness reads.
            missing = sorted(
                name
                for name in names
                if AGENT_ENTRY[target].format(name=name) not in produced
            )
            self.assertEqual(missing, [], f"{target}: {len(missing)} agents are missing from the tree")

            mismatched = sorted(
                path for path, digest in produced.items() if expected.get(path) != digest
            )
            absent = sorted(set(expected) - set(produced))
            extra = sorted(set(produced) - set(expected))
            if mismatched or absent or extra:
                differing[target] = mismatched + absent + extra
            compared += len(produced) + len(expected)
            print(
                f"[ac26] {target}: {len(produced)} files, {len(names)} agents present, "
                f"{len(mismatched)} differing, {len(absent)} missing, {len(extra)} extra "
                f"(agentshare: {len(expected)} files)"
            )

        self.assertEqual(differing, {}, f"files differ from the agentshare projection: {differing}")
        self.assertGreater(compared, 0, "0 files were compared")

        # The side effects are part of the projection, so they are part of the parity.
        for target, relative in (
            ("opencode", ".config/opencode/opencode.json"),
            ("grok", ".grok/config.toml"),
        ):
            ported = ported_home / relative
            references = reference_home / relative
            self.assertTrue(references.is_file(), f"agentshare wrote no {relative}, nothing to compare")
            self.assertTrue(ported.is_file(), f"the port wrote no {relative}")
            self.assertEqual(
                sha256_of(ported), sha256_of(references), f"{relative}: the port left different bytes"
            )
            print(f"[ac26] {target} side effect {relative}: {sha256_of(ported)} matches agentshare")

        unpinned = json.loads((ported_home / ".config" / "opencode" / "opencode.json").read_text(encoding="utf-8"))
        self.assertNotEqual(
            sha256_of(ported_home / ".config" / "opencode" / "opencode.json"),
            seeded_config,
            "the seeded model pin was not removed, so the unpin parity above is vacuous",
        )
        self.assertNotIn("model", unpinned["agent"]["mcp-planner"])
        self.assertEqual(unpinned["agent"]["not-a-published-agent"], {"model": "keep/me"})

        # Grok's scanned folder keeps the user's file and loses the marked legacy profile.
        legacy = {path.name for path in (ported_home / ".grok" / "agents").iterdir()}
        self.assertEqual(
            legacy, {"my-own-agent.md"}, "the legacy cleanup removed the wrong files from ~/.grok/agents"
        )
        self.assertEqual(
            legacy, {path.name for path in (reference_home / ".grok" / "agents").iterdir()},
            "the port and agentshare disagree about what the grok cleanup removes",
        )

        print(
            f"[ac26] observation: {ported_home}/.claude/agents holds "
            f"{len(list((ported_home / '.claude' / 'agents').iterdir()))} entries; "
            f"file counts by target "
            + json.dumps({target: len(files) for target, files in trees.items()}, sort_keys=True)
        )
        print(
            f"[ac26] 0 differing files across {len(project_agents.TARGETS)} targets, "
            f"{len(names)} agents, {sum(len(files) for files in trees.values())} files compared"
        )


if __name__ == "__main__":
    unittest.main()
