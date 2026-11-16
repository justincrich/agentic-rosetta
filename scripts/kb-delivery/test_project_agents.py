#!/usr/bin/env python3
"""test_project_agents.py — the projector's own contract on a real checkout.

WHAT IS REAL HERE
  A temp checkout of the content repository (``$ROSETTA_CONTENT_REPO``, default
  ``~/Projects/agent-content``), moved into a CUSTOM layout — ``kb/graphs`` for knowledge bases
  and ``prompts`` for agent prompts — and given a version 2 config that declares it. Every run
  is the shipped CLI (``project_agents.py``) as a real subprocess over that checkout into a
  sandbox ``HOME``, so the layout under test is the one the checkout declares rather than a name
  the projector happens to spell.

WHY THERE IS NO SECOND PROGRAM
  These proofs used to compare the port against the consumer's own ``scripts/agentshare.py``.
  That program was retired on 2026-11-15 (brain: "retire the duplicated agent projector"), so
  the oracle does not exist any more and a comparison against it would prove nothing. What a
  device actually receives is the projection these runs produce, so the contract is proven
  directly: every agent of the fleet lands in every target tree, a second run changes nothing,
  and the declared side effects (OpenCode's model unpin, Grok's managed config, Grok's legacy
  cleanup) really happen.

WHY THE HOME IS SEEDED
  Two targets do more than write a tree: OpenCode removes ``agent.<name>.model`` from
  ``opencode.json`` (its inherit signal is key absence), and Grok rewrites the managed
  ``[subagents.models]`` table in ``config.toml`` and deletes its own stale profiles from the
  folder Grok scans. A tree-only comparison would pass while the projector silently stopped
  doing those, so the sandbox HOME starts from a device state that makes each one observable.

ENVIRONMENT
  ROSETTA_CONTENT_REPO   optional: the content checkout (default ``~/Projects/agent-content``)

Run:
    python3 scripts/kb-delivery/test_project_agents.py
"""
from __future__ import annotations

import json
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


def seed_device(home: Path) -> None:
    """The device state every run starts from: model pins and a legacy Grok profile."""
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


class ProjectorContractProof(unittest.TestCase):
    """Every agent, every target, from the checkout's declared layout; idempotent and honest."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="project-agents-parity-"))

    def tearDown(self) -> None:
        # The OpenCode target plants a `uchg` sentinel; clear it before removing the tree.
        remove_tree(self.tmp)

    def run_port(self, consumer: Path, home: Path, target: str | None = None,
                 targets: str | None = None, expect: int = 0) -> subprocess.CompletedProcess:
        argv = [sys.executable, str(PROJECTOR), "--root", str(consumer), "--home", str(home)]
        if target:
            argv += ["--target", target]
        if targets:
            argv += ["--targets", targets]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=PROJECT_TIMEOUT)
        print(f"$ project_agents.py --root {consumer.name} --target {target or targets} --home {home.name}")
        print(proc.stdout[-2000:], end="")
        print(proc.stderr[-2000:], end="")
        self.assertEqual(proc.returncode, expect, msg=f"project_agents.py exited {proc.returncode}")
        return proc

    def test_ac26_every_agent_in_every_target_from_the_declared_layout(self) -> None:
        """A custom layout is honoured, complete, deterministic, and its side effects are applied."""
        consumer = harness.prepared_consumer(self.tmp / "consumer", docs="kb/graphs", agents="prompts")
        self.assertTrue((consumer / "prompts").is_dir(), "the prompts were not moved into the layout")
        self.assertFalse((consumer / "agents").exists(), "the standard agents directory still exists")

        home = self.tmp / "device-home"
        seed_device(home)

        agents = project_agents.discover_agents(consumer / "prompts")
        # 293 is what the fleet held when this AC was written; the consumer moves, so the floor
        # is asserted and every discovered agent is required in every tree.
        self.assertGreaterEqual(
            len(agents), MIN_AGENTS, f"the checkout's fleet is {len(agents)} agents, under {MIN_AGENTS}"
        )
        names = {agent["name"] for agent in agents}
        seeded_config = sha256_of(home / ".config" / "opencode" / "opencode.json")

        trees: dict[str, dict[str, str]] = {}
        for target in project_agents.TARGETS:
            self.assertFalse(
                (home / project_agents.TARGET_DEST[target]).exists(),
                f"{target}: the target tree exists before the projector ran, so 'no change' proves nothing",
            )
            self.run_port(consumer, home, target=target)
            trees[target] = tree_hashes(home / project_agents.TARGET_DEST[target])

        for target in project_agents.TARGETS:
            produced = trees[target]
            self.assertGreater(len(produced), 0, f"{target}: the projector produced an empty tree")
            missing = sorted(
                name for name in names if AGENT_ENTRY[target].format(name=name) not in produced
            )
            self.assertEqual(missing, [], f"{target}: {len(missing)} agents are missing from the tree")
            print(f"[ac26] {target}: {len(produced)} files, {len(names)} agents present")

        # The side effects are part of the projection.
        unpinned = json.loads((home / ".config" / "opencode" / "opencode.json").read_text(encoding="utf-8"))
        self.assertNotEqual(
            sha256_of(home / ".config" / "opencode" / "opencode.json"), seeded_config,
            "the seeded model pin was not removed, so the unpin proof is vacuous",
        )
        self.assertNotIn("model", unpinned["agent"]["mcp-planner"])
        self.assertEqual(unpinned["agent"]["not-a-published-agent"], {"model": "keep/me"})

        # Grok's scanned folder keeps the user's file and loses the marked legacy profile; its
        # managed table lands in the config Grok reads.
        legacy = {path.name for path in (home / ".grok" / "agents").iterdir()}
        self.assertEqual(
            legacy, {"my-own-agent.md"},
            "the legacy cleanup removed the wrong files from the folder Grok scans",
        )
        self.assertTrue((home / ".grok" / "config.toml").is_file(), "no Grok config was written")
        self.assertIn(
            "[subagents.models]", (home / ".grok" / "config.toml").read_text(encoding="utf-8"),
            "the managed Grok table is missing from the config the projector wrote",
        )

        # A second run over the same checkout changes no byte: the projection is a function of
        # its input, which is what makes a republish and a device sync converge.
        before = {target: tree_hashes(home / project_agents.TARGET_DEST[target])
                  for target in project_agents.TARGETS}
        before_config = sha256_of(home / ".grok" / "config.toml")
        self.run_port(consumer, home, targets=",".join(project_agents.TARGETS))
        for target in project_agents.TARGETS:
            self.assertEqual(
                tree_hashes(home / project_agents.TARGET_DEST[target]), before[target],
                f"{target}: a second run changed the tree",
            )
        self.assertEqual(sha256_of(home / ".grok" / "config.toml"), before_config,
                         "a second run changed the Grok config")

        print(
            f"[ac26] 0 differing files across {len(project_agents.TARGETS)} targets, "
            f"{len(names)} agents, {sum(len(files) for files in trees.values())} files projected"
        )

    def test_refuses_a_checkout_that_declares_no_layout(self) -> None:
        """A checkout with no config is refused, naming the file, and writes nothing."""
        bare = self.tmp / "bare-consumer"
        (bare / "agents").mkdir(parents=True)
        # A repo boundary, so the refusal is about THIS checkout and not an enclosing one.
        subprocess.run(["git", "init", "-q", str(bare)], check=True, capture_output=True)
        home = self.tmp / "bare-home"
        home.mkdir()
        proc = self.run_port(bare, home, target="claude", expect=2)
        self.assertIn(str(bare / ".rosetta" / "config.json"), proc.stderr)
        self.assertEqual(sorted(path.name for path in home.iterdir()), [],
                         "a refused run wrote into the device home")

    def test_declared_agents_directory_is_the_only_one_read(self) -> None:
        """Agents under the declared directory project; a stray ``agents/`` is not consulted."""
        consumer = harness.prepared_consumer(self.tmp / "consumer-layout", docs="kb/graphs",
                                             agents="prompts")
        # A decoy the projector must NOT read: same shape, different directory.
        decoy = consumer / "agents" / "decoy-implementer"
        decoy.mkdir(parents=True)
        (decoy / "agent.md").write_text(
            "---\nname: decoy-implementer\ndescription: must not be projected\n---\n", encoding="utf-8"
        )
        home = self.tmp / "layout-home"
        home.mkdir()
        self.run_port(consumer, home, target="claude", expect=0)
        listed = {path.name for path in (home / ".claude" / "agents").iterdir()}
        self.assertNotIn("decoy-implementer", listed,
                         "the projector read the undeclared agents/ directory")
        self.assertIn("mcp-planner", listed, "the declared prompts were not projected")


if __name__ == "__main__":
    unittest.main()
