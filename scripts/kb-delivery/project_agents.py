#!/usr/bin/env python3
"""project_agents.py — project a consumer checkout's agents into each harness's own format.

WHY THIS EXISTS
  The projection rules used to live only in the consumer's ``scripts/agentshare.py``, so the
  publisher could not stage a snapshot on a machine without that checkout. This module is the
  port of those rules into the plugin: the same five targets (claude, codex, opencode, droid,
  grok), the same frontmatter parsing, tool mapping, model policy, TOML escaping and harness
  side effects, driven by ``--root`` (the checkout) and ``--home`` (where the harnesses read).

  It is a PORT, not a redesign, and that is a hard requirement: ``test_project_agents.py``
  (AC-26) proves per-file sha256 equality with a real ``agentshare.py`` run for all 293 agents
  across all five targets. Until brain's copy is retired the two must produce identical bytes,
  so anything that looks redundant here (the ``agentshare: true`` marker, the codex
  ``# Generated from`` line, the grok config markers) is part of the on-disk protocol the
  harnesses and the device sync already understand — those strings name the *format*, not a
  script that has to exist.

WHAT ONE RUN WRITES (all under ``--home``, never the real HOME unless you ask for it)
  .claude/agents/<name>/…            the agent directory, verbatim (claude reads the folder)
  .codex/agents/<name>.toml          generated TOML, tools rewritten, role-based model
  .config/opencode/agents/<name>.md  generated markdown, permission object, no model (inherit)
  .config/opencode/opencode.json     `agent.<name>.model` removed for every published agent
  .factory/droids/<name>.md          generated markdown, droid tool names
  .grok/agent-library/<name>.md      generated profile (Grok never scans this folder)
  .grok/config.toml                  the managed [subagents.models] table
  .grok/agents/…                     only DELETED from, and only files carrying the marker

  Grok's profiles go to ``agent-library`` and not ``agent scanpath``: Grok lists only the first
  64 agents it discovers alphabetically, so a project symlinks the ones its sprints name.

WHAT IS DELIBERATELY NOT PORTED
  ``pi`` (disabled on 2026-11-04 by operator directive) and ``rogueone`` (a portable *bundle*
  of profiles plus vendored docs, not a harness tree). Neither is a target the publisher stages,
  and porting the bundle would duplicate ``rogueone_bundle.py``'s doc resolution — the job
  ``doc_refs.py`` already does for this repo.

PROVENANCE
  Ported from brain ``scripts/agentshare.py`` (2026-11-13, sha256 a30931c7…812708). Where a
  function is a faithful copy, its name and shape are kept so a reviewer can diff the two files
  side by side; divergences are commented at the site.

Usage:
    python3 project_agents.py --root ~/Projects/brain --target claude --home /tmp/h
    python3 project_agents.py --root ~/Projects/brain --targets claude,codex,opencode,droid,grok
    python3 project_agents.py --root ~/Projects/brain --dry-run     # preview (only the dirs are made)
    python3 project_agents.py --root ~/Projects/brain --list        # agents + targets
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tomllib
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "rosetta"))
from naming import ROLES  # noqa: E402  (the one home for the role vocabulary)

# ── where each harness reads its agents, relative to the home it runs as ────────────────

#: Target name -> destination under ``--home``. This table is the shared answer to "where does
#: this harness look": ``publish_snapshot.py`` imports it for the manifest's ``dest`` and
#: ``targets/<t>/`` layout, so a harness move is one edit here.
TARGET_DEST: dict[str, str] = {
    "claude": ".claude/agents",
    "codex": ".codex/agents",
    "opencode": ".config/opencode/agents",
    "droid": ".factory/droids",
    # Grok Build CLI (xAI) scans ~/.grok/agents/, which lists only the first 64 agents
    # alphabetically; the library folder is what a project symlinks from.
    "grok": ".grok/agent-library",
}

#: Every target this projector writes, in publication order.
TARGETS: tuple[str, ...] = ("claude", "codex", "opencode", "droid", "grok")

#: The extension one agent occupies inside a target tree (claude is a folder, so its entry is
#: the prompt itself).
TARGET_SUFFIX: dict[str, str] = {
    "claude": ".md",
    "codex": ".toml",
    "opencode": ".md",
    "droid": ".md",
    "grok": ".md",
}

# ── Codex model targeting ──────────────────────────────────────────────────────────────
#
# All codex agents use gpt-6.1-sol (operator directive, 2026-10-23, superseding the 2026-09-13
# split of gpt-6-astra planners/reviewers + gpt-5.6-sol implementers). Effort stays role-based.
CODEX_ASSIGNMENT_BY_ROLE: dict[str, tuple[str, str]] = {
    "planner": ("gpt-6.1-sol", "xhigh"),
    "implementer": ("gpt-6.1-sol", "high"),
    "reviewer": ("gpt-6.1-sol", "xhigh"),
}
CODEX_DEFAULT_ASSIGNMENT = ("gpt-6.1-sol", "high")

# ── Grok (Grok Build CLI, xAI) model pins ──────────────────────────────────────────────
#
# Implementers pin to grok-composer-2.5-fast and reviewers to grok-4.6 (operator directive,
# 2026-10-17). Planners and standalone agents fall back to GROK_DEFAULT_MODEL. Model ids are
# bare (no `grok/` prefix) — that is what grok's config and `grok models` use.
GROK_MODEL_BY_ROLE: dict[str, str] = {
    "implementer": "grok-composer-2.5-fast",
    "reviewer": "grok-4.6",
}
GROK_DEFAULT_MODEL = "grok-4.7"

# ── tool mapping ───────────────────────────────────────────────────────────────────────

#: Brain tool -> OpenCode permission key.
TOOL_TO_PERMISSION: dict[str, str] = {
    "Read": "read",
    "Write": "edit",
    "Edit": "edit",
    "Bash": "bash",
    "Glob": "glob",
    "Grep": "grep",
    "Task": "task",
    "AskUserQuestion": "question",
    "WebSearch": "websearch",
    "WebFetch": "webfetch",
}

#: Brain tool -> Droid tool name (case-sensitive, per Factory.ai's custom-droids spec).
TOOL_TO_DROID: dict[str, str] = {
    "Read": "Read",
    "Write": "Create",
    "Edit": "Edit",
    "Bash": "Execute",
    "Glob": "Glob",
    "Grep": "Grep",
    "WebSearch": "WebSearch",
    "WebFetch": "FetchUrl",
    # Task, AskUserQuestion, Skill: no droid equivalent — omitted
}

#: Brain tool -> Codex tool rewrite (body text only).
CODEX_TOOL_REWRITE: dict[str, str] = {
    "Read": "text_editor",
    "Write": "text_editor",
    "Edit": "text_editor",
    "Bash": "shell",
    "Glob": "shell",
    "Grep": "shell",
    "Task": "spawn_agent",
    "AskUserQuestion": "ask the user directly",
    "WebSearch": "web_search",
    "WebFetch": "web tools",
}

WRITE_TOOLS = {"Write", "Edit", "Bash", "Task"}


def role_for_agent(name: str) -> str | None:
    """The role a bare agent name denotes, or ``None`` when it carries none.

    Two spellings count (mirroring agentshare): the name IS the role (``planner`` is a real
    agent in the fleet) or the name ends in ``-<role>``. The vocabulary is
    ``naming.ROLES``; only the model policy below is this module's decision.

    ``naming.split_agent_name`` answers a different question — which trailing token to strip
    off, with a caller-supplied default — so it cannot serve this one without changing how a
    bare ``planner`` splits for the registry.
    """
    for role in ROLES:
        if name == role or name.endswith(f"-{role}"):
            return role
    return None


def _codex_assignment_for(name: str) -> tuple[str, str]:
    role = role_for_agent(name)
    return CODEX_ASSIGNMENT_BY_ROLE[role] if role else CODEX_DEFAULT_ASSIGNMENT


def _codex_model_for(name: str) -> str:
    return _codex_assignment_for(name)[0]


def _codex_reasoning_effort_for(name: str) -> str:
    return _codex_assignment_for(name)[1]


def _grok_model_for(name: str) -> str:
    role = role_for_agent(name)
    return GROK_MODEL_BY_ROLE.get(role or "", GROK_DEFAULT_MODEL)


# ── frontmatter parsing ────────────────────────────────────────────────────────────────

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")
LIST_VALUE_RE = re.compile(r"^\[(.*)\]$")
BLOCK_ITEM_RE = re.compile(r"^\s*-\s+(.*)$")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text.strip()

    frontmatter, body = match.groups()
    meta: dict[str, object] = {}
    lines = frontmatter.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i]
        key_match = KEY_RE.match(line)
        if not key_match:
            i += 1
            continue

        key, raw_value = key_match.groups()
        raw_value = raw_value.strip()

        # YAML block list for any key: `tools:` then `- Read` lines (indented or not). Without
        # this, `tools:` parsed as "" and the agent synced to every harness with NO tools
        # (2026-10-22: implementers went read-only).
        if not raw_value and i + 1 < len(lines) and BLOCK_ITEM_RE.match(lines[i + 1]):
            items = []
            i += 1
            while i < len(lines) and (item := BLOCK_ITEM_RE.match(lines[i])):
                items.append(str(_parse_scalar(item.group(1))))
                i += 1
            meta[key] = items
            continue

        # Array fields: mcp_tools, allowed-tools, nickname_candidates
        if key in ("mcp_tools", "allowed-tools", "allowed_tools", "nickname_candidates"):
            items: list[str] = []
            if raw_value:
                items = _parse_list_value(raw_value)
            else:
                i += 1
                while i < len(lines) and lines[i].startswith("  "):
                    item = lines[i].strip().lstrip("- ")
                    if item:
                        items.append(item)
                    i += 1
                meta[key] = items
                continue
            meta[key] = items
            i += 1
            continue

        # Strip inline comments (e.g., "sonnet  # Maker" -> "sonnet")
        raw_value = re.sub(r"\s+#.*$", "", raw_value)

        meta[key] = _parse_scalar(raw_value)
        i += 1

    return meta, body.strip()


def _parse_scalar(raw: str) -> object:
    raw = raw.strip()
    if raw.startswith(('"', "'")) and raw.endswith(('"', "'")):
        return raw[1:-1]
    list_match = LIST_VALUE_RE.match(raw)
    if list_match:
        inner = list_match.group(1).strip()
        if not inner:
            return []
        return [item.strip().strip("\"'") for item in inner.split(",") if item.strip()]
    return raw


def _parse_list_value(raw: str) -> list[str]:
    val = _parse_scalar(raw)
    if isinstance(val, list):
        return [str(v) for v in val]
    if isinstance(val, str):
        return [p.strip().strip("\"'") for p in val.split(",") if p.strip()]
    return []


# ── agent discovery ────────────────────────────────────────────────────────────────────

Agent = dict  # {name, description, model, tools, mcp_tools, body, source_path}


def discover_agents(src: Path) -> list[Agent]:
    """Every agent under ``src``: a directory with ``agent.md``, or a bare ``.md`` file."""
    agents: list[Agent] = []
    if not src.exists():
        return agents

    for p in sorted(src.iterdir()):
        if p.name.startswith("_") or p.name.startswith("."):
            continue
        agent_file = p / "agent.md" if p.is_dir() else (p if p.suffix == ".md" else None)
        if agent_file is None or not agent_file.exists():
            continue

        text = agent_file.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        if not body.strip():
            continue

        name = str(meta.get("name") or p.stem)
        description = str(meta.get("description") or f"Custom agent from {p.name}")
        model = str(meta.get("model", "sonnet")).strip().lower()
        tools = meta.get("allowed-tools") or meta.get("allowed_tools") or meta.get("tools") or []
        if isinstance(tools, str):
            tools = _parse_list_value(tools)
        mcp_tools = meta.get("mcp_tools") or []
        if isinstance(mcp_tools, str):
            mcp_tools = _parse_list_value(mcp_tools)

        agents.append({
            "name": name,
            "description": description,
            "model": model,
            "model_reasoning_effort": meta.get("model_reasoning_effort")
            or (meta.get("extensions") or {}).get("codex", {}).get("model_reasoning_effort"),
            "tools": [str(t) for t in tools],
            "mcp_tools": [str(t) for t in mcp_tools],
            "body": body.strip(),
            "source_path": p,
        })

    return agents


# ── target: claude ─────────────────────────────────────────────────────────────────────


def sync_target_claude(agents: list[Agent], target_dir: Path, dry_run: bool) -> list[str]:
    """Copy agent directories recursively. Near passthrough."""
    target_dir.mkdir(parents=True, exist_ok=True)
    synced: list[str] = []

    existing = {p.name for p in target_dir.iterdir() if p.is_dir() or p.suffix == ".md"}
    wanted: set[str] = set()

    for agent in agents:
        src = agent["source_path"]
        name = agent["name"]
        wanted.add(name)

        dest = target_dir / name
        if dest.exists() and _dir_matches(dest, src):
            synced.append(f"  [skip] {name} (up to date)")
            continue

        if dry_run:
            synced.append(f"  [would sync] {name}")
            continue

        if dest.exists():
            shutil.rmtree(dest)
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / "agent.md")
        synced.append(f"  [synced] {name}")

    for stale in existing - wanted:
        if dry_run:
            synced.append(f"  [would remove] {stale}")
        else:
            p = target_dir / stale
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            synced.append(f"  [removed] {stale}")

    return synced


def _dir_matches(dest: Path, src: Path) -> bool:
    """Quick check: does dest look like a copy of src?"""
    if src.is_dir():
        src_agent = src / "agent.md"
        dest_agent = dest / "agent.md"
        if not dest_agent.exists():
            return False
        return src_agent.read_text(encoding="utf-8") == dest_agent.read_text(encoding="utf-8")
    return False


# ── flat-target sanitization ───────────────────────────────────────────────────────────


def _prune_invalid_entries(target_dir: Path, dry_run: bool) -> list[str]:
    """Remove subdirs / symlinks that shadow agent files in a flat-file target.

    Flat-file targets (opencode, droid, codex) expect agents as flat files. A recurring bug is
    a symlink or subdirectory named ``agents`` holding per-agent ``agent.md`` files in another
    harness's format — the harness's loader walks into it and rejects the foreign frontmatter.
    """
    pruned: list[str] = []
    if not target_dir.exists():
        return pruned

    for entry in sorted(target_dir.iterdir()):
        if entry.is_symlink():
            try:
                resolved = entry.resolve(strict=True)
            except (OSError, RuntimeError):
                if dry_run:
                    pruned.append(f"  [would prune broken symlink] {entry.name}")
                else:
                    entry.unlink()
                    pruned.append(f"  [pruned broken symlink] {entry.name}")
                continue
            if resolved.is_dir() and any(resolved.glob("**/agent.md")):
                if dry_run:
                    pruned.append(f"  [would prune symlink] {entry.name} -> {resolved}")
                else:
                    entry.unlink()
                    pruned.append(f"  [pruned symlink] {entry.name} -> {resolved}")
            continue

        if entry.is_dir():
            if any(entry.glob("**/agent.md")):
                if dry_run:
                    pruned.append(f"  [would prune dir] {entry.name}/ (contains agent.md files)")
                else:
                    shutil.rmtree(entry)
                    pruned.append(f"  [pruned dir] {entry.name}/")
            continue

    return pruned


def _find_managed_files(directory: Path) -> set[str]:
    """Find files that carry the projector's ``agentshare: true`` marker in their frontmatter."""
    managed: set[str] = set()
    for f in directory.glob("*.md"):
        try:
            text = f.read_text(encoding="utf-8")
            if re.search(r"^agentshare:\s*true\s*$", text, re.MULTILINE):
                managed.add(f.stem)
        except Exception:
            pass
    return managed


# ── target: codex ──────────────────────────────────────────────────────────────────────


def sync_target_codex(agents: list[Agent], target_dir: Path, dry_run: bool) -> list[str]:
    """Generate .toml files for Codex."""
    target_dir.mkdir(parents=True, exist_ok=True)
    synced: list[str] = _prune_invalid_entries(target_dir, dry_run)

    existing = {p.stem for p in target_dir.glob("*.toml")}

    for agent in agents:
        name = agent["name"]
        source_path = agent["source_path"]
        has_write = bool(set(agent["tools"]) & WRITE_TOOLS)
        sandbox = "workspace-write" if has_write else "read-only"

        body = _codexify_body(agent["body"])

        toml = _render_codex_toml(
            name=name,
            description=agent["description"],
            developer_instructions=body,
            sandbox_mode=sandbox,
            source_path=source_path,
            model=_codex_model_for(name),
            model_reasoning_effort=_codex_reasoning_effort_for(name),
        )

        # SELF-CHECK: never ship invalid TOML. A malformed escape silently bricks a Codex agent
        # at load time ("invalid value, expected unicode hexadecimal value") and wedges any
        # codex stage that dispatches it.
        try:
            tomllib.loads(toml)
        except tomllib.TOMLDecodeError as exc:
            raise SystemExit(
                f"ERROR: project_agents rendered invalid Codex TOML for {name} "
                f"(from {source_path}): {exc}"
            ) from exc

        dest = target_dir / f"{name}.toml"
        if dest.exists() and dest.read_text(encoding="utf-8") == toml:
            synced.append(f"  [skip] {name}.toml (up to date)")
            continue

        if dry_run:
            synced.append(f"  [would write] {name}.toml")
            continue

        dest.write_text(toml, encoding="utf-8")
        synced.append(f"  [synced] {name}.toml")

    wanted = {a["name"] for a in agents}
    for stale in existing - wanted:
        if dry_run:
            synced.append(f"  [would remove] {stale}.toml")
        else:
            (target_dir / f"{stale}.toml").unlink()
            synced.append(f"  [removed] {stale}.toml")

    return synced


def _codexify_body(body: str) -> str:
    """Rewrite Claude tool references to Codex equivalents."""
    text = body
    text = text.replace("Claude Code", "Codex")
    for claude_tool, codex_tool in CODEX_TOOL_REWRITE.items():
        text = text.replace(f"`{claude_tool}`", f"`{codex_tool}`")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _toml_basic_string(s: str) -> str:
    """Encode a Python str as a TOML basic string.

    json.dumps(ensure_ascii=True) emits SURROGATE PAIRS for astral code points (the 🔑 emoji
    becomes ``\\ud83d\\udd11``), which TOML rejects: its ``\\u`` escape accepts only a Unicode
    scalar value. Emit non-control characters literally and escape only what TOML requires.
    """
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\f":
            out.append("\\f")
        elif ch == "\r":
            out.append("\\r")
        elif o < 0x20 or o == 0x7F:
            out.append(f"\\u{o:04X}")
        else:
            out.append(ch)  # literal UTF-8 — emoji/astral chars stay intact
    out.append('"')
    return "".join(out)


def _render_codex_toml(
    *,
    name: str,
    description: str,
    developer_instructions: str,
    sandbox_mode: str,
    source_path: Path,
    model: str,
    model_reasoning_effort: str | None = None,
) -> str:
    lines = [
        f"# Generated from {source_path}",
        f"name = {_toml_basic_string(name)}",
        f"description = {_toml_basic_string(description)}",
        f"model = {_toml_basic_string(model)}",
    ]
    if model_reasoning_effort:
        lines.append(f"model_reasoning_effort = {_toml_basic_string(model_reasoning_effort)}")
    lines += [
        f"sandbox_mode = {_toml_basic_string(sandbox_mode)}",
        f"developer_instructions = {_toml_basic_string(developer_instructions)}",
        "",
    ]
    return "\n".join(lines)


# ── target: opencode ───────────────────────────────────────────────────────────────────

# OpenCode's loader walks subdirectories of the agents target. Anything that isn't a flat .md
# file in OpenCode's native frontmatter shape crashes its config validator. Known leak:
# skillshare's `sync agents` creates a bridge symlink `${target}/agents -> ~/.claude/agents`,
# which exposes Claude-format files and breaks every OpenCode run.
OPENCODE_BRIDGE_NAMES: tuple[str, ...] = ("agents",)


def _enforce_opencode_shape(target_dir: Path, dry_run: bool) -> list[str]:
    """Force the OpenCode target to a flat-.md-only shape, and block the bridge from returning.

    Removes any entry named in ``OPENCODE_BRIDGE_NAMES`` (symlink, dir or file) plus any other
    foreign agent tree, then plants an empty immutable sentinel so an external tool's ``ln -s``
    fails. Leaves flat .md files alone (the file sync owns those). Idempotent.
    """
    cleaned: list[str] = []
    if not target_dir.exists():
        return cleaned

    for name in OPENCODE_BRIDGE_NAMES:
        entry = target_dir / name
        is_present = entry.exists() or entry.is_symlink()
        is_sentinel = (
            is_present and entry.is_file() and not entry.is_symlink() and entry.stat().st_size == 0
        )

        if is_sentinel:
            continue

        if is_present:
            kind = "symlink" if entry.is_symlink() else ("dir" if entry.is_dir() else "file")
            target_repr = ""
            if entry.is_symlink():
                try:
                    target_repr = f" -> {entry.resolve(strict=False)}"
                except (OSError, RuntimeError):
                    target_repr = " -> (broken)"
            if dry_run:
                cleaned.append(f"  [would remove bridge] {name} ({kind}){target_repr}")
                continue
            if entry.is_symlink() or entry.is_file():
                entry.unlink()
            else:
                shutil.rmtree(entry)
            cleaned.append(f"  [removed bridge] {name} ({kind}){target_repr}")

        # macOS user-immutable, so an external tool cannot replace the sentinel with `ln -sf`.
        if not dry_run and not entry.exists():
            entry.touch()
            try:
                import subprocess

                subprocess.run(["chflags", "uchg", str(entry)], check=False, capture_output=True)
            except Exception:
                pass
            cleaned.append(f"  [planted sentinel] {name}")

    cleaned.extend(_prune_invalid_entries(target_dir, dry_run))
    return cleaned


def sync_target_opencode(agents: list[Agent], target_dir: Path, dry_run: bool) -> list[str]:
    """Generate flat .md files for OpenCode with permission objects.

    Emits NO ``model:`` line: every subagent inherits the parent session's model (OpenCode's
    inherit signal is key absence).
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    synced: list[str] = _enforce_opencode_shape(target_dir, dry_run)

    existing = {p.stem for p in target_dir.glob("*.md")}

    for agent in agents:
        name = agent["name"]
        permissions = _build_opencode_permissions(agent["tools"])

        content = _render_opencode_md(
            description=agent["description"],
            permissions=permissions,
            body=agent["body"],
        )

        dest = target_dir / f"{name}.md"
        if dest.exists() and dest.read_text(encoding="utf-8") == content:
            synced.append(f"  [skip] {name}.md (up to date)")
            continue

        if dry_run:
            synced.append(f"  [would write] {name}.md")
            continue

        dest.write_text(content, encoding="utf-8")
        synced.append(f"  [synced] {name}.md")

    wanted = {a["name"] for a in agents}
    for stale in existing - wanted:
        if dry_run:
            synced.append(f"  [would remove] {stale}.md")
        else:
            (target_dir / f"{stale}.md").unlink()
            synced.append(f"  [removed] {stale}.md")

    synced.extend(_enforce_opencode_shape(target_dir, dry_run))

    # A config-level `agent.<name>.model` beats the profile's key absence.
    synced.extend(
        _strip_opencode_agent_models(wanted, target_dir.parent / "opencode.json", dry_run)
    )

    return synced


def _strip_opencode_agent_models(names: set[str], config_path: Path, dry_run: bool) -> list[str]:
    """Remove ``model`` from ``agent.<name>`` in opencode.json for the published agents.

    Drops an entry left empty. Other keys and non-published agents are untouched.
    """
    if not config_path.exists():
        return []
    config = json.loads(config_path.read_text(encoding="utf-8"))
    agent_cfg = config.get("agent") or {}
    pinned = sorted(n for n in names if "model" in (agent_cfg.get(n) or {}))
    if not pinned:
        return [f"  [skip] {config_path.name} agent models (none pinned)"]
    if dry_run:
        return [f"  [would unpin] {config_path.name} agent.{n}.model" for n in pinned]
    for n in pinned:
        del agent_cfg[n]["model"]
        if not agent_cfg[n]:
            del agent_cfg[n]
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    return [f"  [unpinned] {config_path.name} agent.{n}.model" for n in pinned]


def _build_opencode_permissions(tools: list[str]) -> dict[str, str]:
    """Derive the OpenCode permission object from the brain tool list."""
    tool_set = set(tools)

    perm_to_tools: dict[str, list[str]] = {}
    for brain_tool, perm_key in TOOL_TO_PERMISSION.items():
        perm_to_tools.setdefault(perm_key, []).append(brain_tool)

    perms: dict[str, str] = {}
    for perm_key, brain_tools in perm_to_tools.items():
        perms[perm_key] = "allow" if any(bt in tool_set for bt in brain_tools) else "deny"

    return perms


def _render_opencode_md(
    *,
    description: str,
    permissions: dict[str, str],
    body: str,
) -> str:
    lines = ["---"]
    lines.append(f"description: {json.dumps(description)}")
    lines.append("mode: all")
    lines.append("permission:")
    for key, value in sorted(permissions.items()):
        lines.append(f"  {key}: {json.dumps(value)}")
    lines.append("---")
    lines.append("")
    lines.append("<!-- agentshare-managed: true -->")
    lines.append("")
    lines.append(body)
    lines.append("")
    return "\n".join(lines)


# ── target: droid ──────────────────────────────────────────────────────────────────────


def sync_target_droid(agents: list[Agent], target_dir: Path, dry_run: bool) -> list[str]:
    """Generate flat .md files for Factory.ai droid (top-level only, no subdirs)."""
    target_dir.mkdir(parents=True, exist_ok=True)
    synced: list[str] = _prune_invalid_entries(target_dir, dry_run)

    for agent in agents:
        name = agent["name"]
        tools_array = _build_droid_tools(agent["tools"])

        content = _render_droid_md(
            name=name,
            description=agent["description"],
            tools_array=tools_array,
            body=agent["body"],
        )

        dest = target_dir / f"{name}.md"
        if dest.exists() and dest.read_text(encoding="utf-8") == content:
            synced.append(f"  [skip] {name}.md (up to date)")
            continue

        if dry_run:
            synced.append(f"  [would write] {name}.md")
            continue

        dest.write_text(content, encoding="utf-8")
        synced.append(f"  [synced] {name}.md")

    managed = _find_managed_files(target_dir)
    wanted = {a["name"] for a in agents}
    for stale in managed - wanted:
        if dry_run:
            synced.append(f"  [would remove] {stale}.md")
        else:
            (target_dir / f"{stale}.md").unlink()
            synced.append(f"  [removed] {stale}.md")

    return synced


def _build_droid_tools(tools: list[str]) -> list[str]:
    """Convert brain tools to droid tool names. Empty list = all tools."""
    out: list[str] = []
    for tool in tools:
        droid_name = TOOL_TO_DROID.get(tool)
        if droid_name and droid_name not in out:
            out.append(droid_name)
    return out


def _render_droid_md(
    *,
    name: str,
    description: str,
    tools_array: list[str],
    body: str,
) -> str:
    lines = ["---"]
    lines.append(f"name: {name}")
    lines.append(f"description: {json.dumps(description)}")
    # No `model:` line — droid inherits the parent session's model.
    if tools_array:
        lines.append(f"tools: {json.dumps(tools_array)}")
    lines.append("agentshare: true")
    lines.append("---")
    lines.append("")
    lines.append(body)
    lines.append("")
    return "\n".join(lines)


# ── target: grok ───────────────────────────────────────────────────────────────────────

# Managed [subagents.models] block in <home>/.grok/config.toml. grok honours a subagent's
# per-type model via [subagents.models] — the per-type override that applies for any parent —
# while the profile's `model:` pin is only guaranteed for `--agent` session launch. kb-run-sprint
# spawns subagents BY TYPE, so without this block the pin is inert for dispatch. The table is
# ALWAYS replaced from the current roster; grok's own config rewriter strips comments, which is
# why the table itself (not the markers) is what gets read. Other tables are preserved.
GROK_SUBMODELS_BEGIN = (
    "# >>> agentshare: [subagents.models] (managed — regenerate via scripts/agentshare.py) >>>"
)
GROK_SUBMODELS_END = "# <<< agentshare: [subagents.models] <<<"
_SUBAGENTS_MODELS_HEADER = re.compile(r"^\[subagents\.models(?:\.[^\]]*)?\]\s*$")


def _grok_subagents_models_block(agents: list[Agent]) -> str:
    """The managed ``[subagents.models]`` TOML block, one entry per agent, sorted."""
    entries = sorted((a["name"], _grok_model_for(a["name"])) for a in agents)
    lines = [GROK_SUBMODELS_BEGIN, "[subagents.models]"]
    for name, model in entries:
        lines.append(f'"{name}" = "{model}"')
    lines.append(GROK_SUBMODELS_END)
    return "\n".join(lines)


def _replace_grok_subagents_models(text: str, block: str) -> str:
    """Write ``block`` as the sole ``[subagents.models]`` table in ``text``.

    Any existing table — marker-wrapped or unmarked, including ``[subagents.models.*]``
    subtables — is replaced in place; other tables are preserved; an absent table is appended.
    Always ends in a trailing newline.
    """
    if GROK_SUBMODELS_BEGIN in text and GROK_SUBMODELS_END in text:
        before, rest = text.split(GROK_SUBMODELS_BEGIN, 1)
        _, after = rest.split(GROK_SUBMODELS_END, 1)
        desired = before + block + after
    elif not text:
        desired = block + "\n"
    else:
        lines = text.splitlines(keepends=True)
        start: int | None = None
        i = 0
        while i < len(lines):
            stripped = lines[i].strip()
            if _SUBAGENTS_MODELS_HEADER.match(stripped):
                if start is None:
                    start = i
                i += 1
                continue
            if start is not None and stripped.startswith("[") and stripped.endswith("]"):
                break
            i += 1
        end = i
        if start is None:
            sep = "" if text.endswith("\n") else "\n"
            desired = text + sep + "\n" + block + "\n"
        else:
            before = "".join(lines[:start])
            after = "".join(lines[end:])
            if after and not after.startswith("\n"):
                desired = before + block + "\n\n" + after
            elif after:
                desired = before + block + after
            else:
                desired = before + block + "\n"
    if not desired.endswith("\n"):
        desired += "\n"
    return desired


def _sync_grok_subagents_models(
    agents: list[Agent], config_path: Path, dry_run: bool
) -> list[str]:
    """Idempotently write the managed ``[subagents.models]`` table into grok's config.toml."""
    out: list[str] = []
    block = _grok_subagents_models_block(agents)
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    desired = _replace_grok_subagents_models(existing, block)

    if desired == existing:
        out.append(f"  [skip] {config_path.name} [subagents.models] (up to date)")
        return out
    if dry_run:
        out.append(
            f"  [would write] {config_path.name} [subagents.models] ({len(agents)} entries)"
        )
        return out
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(desired, encoding="utf-8")
    out.append(f"  [synced] {config_path.name} [subagents.models] ({len(agents)} entries)")
    return out


def sync_target_grok(agents: list[Agent], target_dir: Path, dry_run: bool) -> list[str]:
    """Generate flat .md profiles for Grok Build CLI, plus its managed model table.

    Profiles go to ``agent-library``, a folder grok never scans; the folder grok does scan is
    only cleaned of files this projector wrote (marker present). A profile never gets a
    ``tools:`` allowlist: listing ``run_terminal_cmd`` there fails grok's profile build, so
    capability is controlled by ``permission_mode`` alone (read-only agents -> plan).
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    synced: list[str] = _prune_invalid_entries(target_dir, dry_run)

    for agent in agents:
        name = agent["name"]
        permission_mode = _grok_permission_mode_for(agent["tools"])
        model = _grok_model_for(name)

        content = _render_grok_md(
            name=name,
            description=agent["description"],
            model=model,
            permission_mode=permission_mode,
            body=agent["body"],
        )

        dest = target_dir / f"{name}.md"
        if dest.exists() and dest.read_text(encoding="utf-8") == content:
            synced.append(f"  [skip] {name}.md (up to date)")
            continue

        if dry_run:
            synced.append(f"  [would write] {name}.md")
            continue

        dest.write_text(content, encoding="utf-8")
        synced.append(f"  [synced] {name}.md")

    managed = _find_managed_files(target_dir)
    wanted = {a["name"] for a in agents}
    for stale in managed - wanted:
        if dry_run:
            synced.append(f"  [would remove] {stale}.md")
        else:
            (target_dir / f"{stale}.md").unlink()
            synced.append(f"  [removed] {stale}.md")

    # Clear profiles an older projector wrote into the scanned ~/.grok/agents/; files without
    # the marker are the user's and stay.
    legacy_dir = target_dir.parent / "agents"
    if legacy_dir != target_dir and legacy_dir.is_dir():
        for old in sorted(_find_managed_files(legacy_dir)):
            if dry_run:
                synced.append(f"  [would remove] agents/{old}.md (moved to {target_dir.name}/)")
            else:
                (legacy_dir / f"{old}.md").unlink()
                synced.append(f"  [removed] agents/{old}.md (moved to {target_dir.name}/)")

    # Enforce per-subagent-type model at the config level (belt-and-suspenders with the pin).
    config_path = target_dir.parent / "config.toml"
    synced.extend(_sync_grok_subagents_models(agents, config_path, dry_run))

    return synced


def _grok_permission_mode_for(tools: list[str]) -> str:
    """Read-only agents get ``plan``; everything else gets ``default`` (full toolset)."""
    if set(tools) & {"Write", "Edit", "Bash"}:
        return "default"
    return "plan"


def _render_grok_md(
    *,
    name: str,
    description: str,
    model: str,
    permission_mode: str,
    body: str,
) -> str:
    lines = ["---"]
    lines.append(f"name: {name}")
    lines.append(f"description: {json.dumps(description)}")
    lines.append(f"model: {model}")
    lines.append("prompt_mode: full")
    lines.append(f"permission_mode: {permission_mode}")
    lines.append("agents_md: true")
    lines.append("agentshare: true")
    lines.append("---")
    lines.append("")
    lines.append(body)
    lines.append("")
    return "\n".join(lines)


# ── entry points ───────────────────────────────────────────────────────────────────────

SYNC_FNS = {
    "claude": sync_target_claude,
    "codex": sync_target_codex,
    "opencode": sync_target_opencode,
    "droid": sync_target_droid,
    "grok": sync_target_grok,
}


def project_target(
    agents: list[Agent], target: str, *, home: Path, dry_run: bool = False
) -> list[str]:
    """Project every agent into one harness target under ``home``. Returns its log lines."""
    if target not in SYNC_FNS:
        raise ValueError(f"unknown target {target!r}; this projector writes {', '.join(TARGETS)}")
    return SYNC_FNS[target](agents, Path(home) / TARGET_DEST[target], dry_run)


def project(
    agents: list[Agent], targets: tuple[str, ...], *, home: Path, dry_run: bool = False
) -> dict[str, list[str]]:
    """Project every agent into each requested target under ``home``."""
    return {target: project_target(agents, target, home=home, dry_run=dry_run) for target in targets}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Project a consumer checkout's agents into each harness's own format."
    )
    parser.add_argument(
        "--root",
        required=True,
        help="consumer checkout holding agents/<name>/agent.md (nothing else is read)",
    )
    parser.add_argument(
        "--home",
        default=None,
        help="home the harnesses read (default: $HOME); pass a sandbox to keep the real one clean",
    )
    parser.add_argument("--target", "-t", choices=list(TARGETS), help="project one target")
    parser.add_argument(
        "--targets",
        default=None,
        help=f"comma-separated targets (default: all of {','.join(TARGETS)})",
    )
    parser.add_argument(
        "--dry-run", "-n", action="store_true", help="preview the projected files (target dirs are still created, as agentshare does)"
    )
    parser.add_argument("--list", action="store_true", help="show discovered agents and targets")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not (root / "agents").is_dir():
        print(f"[project] {root}: no agents/ directory to project", file=sys.stderr)
        return 1
    home = Path(args.home).expanduser().resolve() if args.home else Path.home()

    agents = discover_agents(root / "agents")
    if not agents:
        print(
            f"[project] no agents found in {root / 'agents'}; refusing to project an empty fleet",
            file=sys.stderr,
        )
        return 1

    if args.list:
        print(f"Source: {root / 'agents'}")
        print(f"Agents ({len(agents)}):")
        for agent in agents:
            tools = ", ".join(agent["tools"]) or "(none)"
            print(f"  {agent['name']} — model:{agent['model']} role:{role_for_agent(agent['name']) or '-'} tools:[{tools}]")
        print("\nTargets:")
        for name in TARGETS:
            path = home / TARGET_DEST[name]
            exists = "exists" if path.exists() else "missing"
            count = len(list(path.glob("*"))) if path.exists() else 0
            print(f"  {name}: {path} ({exists}, {count} entries)")
        return 0

    if args.target and args.targets:
        parser.error("--target and --targets are alternatives; name one target or a list")
    if args.target:
        targets: tuple[str, ...] = (args.target,)
    elif args.targets:
        targets = tuple(part.strip() for part in args.targets.split(",") if part.strip())
        unknown = [name for name in targets if name not in SYNC_FNS]
        if unknown:
            parser.error(f"unknown target(s) {', '.join(unknown)}; this projector writes {', '.join(TARGETS)}")
    else:
        targets = TARGETS

    print(f"[project] root: {root}")
    print(f"[project] home: {home}")
    print(f"[project] agents: {len(agents)}")
    total = 0
    for target in targets:
        print(f"\n=== {target} ({home / TARGET_DEST[target]}) ===")
        lines = project_target(agents, target, home=home, dry_run=args.dry_run)
        for line in lines:
            print(line)
        total += sum(1 for line in lines if "[synced]" in line or "[would write]" in line)
    print(
        f"\n{'Would project' if args.dry_run else 'Projected'} {total} agent file(s) "
        f"across {len(targets)} target(s) into {home}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
