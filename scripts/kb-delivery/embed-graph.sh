#!/usr/bin/env bash
# embed-graph.sh — idempotently embed a generated fact-graph render into an agent.md body.
#
# Single source of truth = fact-graph.json. The body block is GENERATED (by
# generate_excerpt.py --embed), never hand-edited, and delimited by stable sentinels:
#
#   <!-- rosetta-kb:begin domain=".." role=".." kb_version=".." -->
#   …dense per-role render (REJECT rules first, then role emphasis, then reference)…
#   <!-- rosetta-kb:end -->
#
# This script ONLY ever touches the region between the sentinels (or inserts it after the
# frontmatter when absent). It never modifies hand-authored prose. That makes it safe to run
# on every edit and idempotent: if the generated block already matches, the file is untouched.
#
# Usage:
#   embed-graph.sh <agent-dir|agent-name>   regenerate the block for one agent (write)
#   embed-graph.sh --all                    regenerate for every graph-blessed agent (write)
#   embed-graph.sh --sync [<slug|agent>]    hard-replace blocks for a graph's consumers, one
#                                           agent, or (no arg) the whole fleet. Fail-closed:
#                                           unknown slug/agent exits non-zero without fleet-wide
#                                           iteration. Agent name ≠ graph slug (use bare agent
#                                           path for single-agent; slug for that graph's triad).
#   embed-graph.sh --check [<agent>|--all]  no writes; exit 0 if every block matches its graph,
#                                           exit 1 + a report on any drifted/missing block
#   embed-graph.sh --list                   print every graph-blessed agent name (one per line)
#
# Fail-closed: a missing/unreadable graph exits nonzero (we never silently drop the block).
#
# No network, no agent calls. Tested by test-embed-graph.sh on real fixtures.

set -u

SCRIPT_DIR="$(cd -P "$(dirname "$0")" >/dev/null 2>&1 && pwd)"

# Resolve the target project. Never hardcodes ~/Projects/brain.
# --root / $ROSETTA_PROJECT win; else a script-parent that already has agents/
# (isolated fixture / in-tree copy); else cwd's git root; else cwd.
# The plugin repo itself has no agents/, so a run from the plugin falls through
# to the caller's project.
resolve_project_root() {
  if [ -n "${ROSETTA_ROOT_FLAG:-}" ]; then
    cd -P "${ROSETTA_ROOT_FLAG}" >/dev/null 2>&1 && pwd && return
  fi
  if [ -n "${ROSETTA_PROJECT:-}" ]; then
    cd -P "${ROSETTA_PROJECT}" >/dev/null 2>&1 && pwd && return
  fi
  local script_project
  script_project="$(cd -P "$SCRIPT_DIR/../.." >/dev/null 2>&1 && pwd)"
  if [ -d "$script_project/agents" ]; then
    printf '%s' "$script_project"
    return
  fi
  if git -C "$PWD" rev-parse --show-toplevel >/dev/null 2>&1; then
    git -C "$PWD" rev-parse --show-toplevel
    return
  fi
  pwd
}

# Pull a leading --root <dir> off argv so the rest of the CLI is unchanged.
ROSETTA_ROOT_FLAG=""
_NEW_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --root)
      ROSETTA_ROOT_FLAG="$2"
      shift 2
      ;;
    --root=*)
      ROSETTA_ROOT_FLAG="${1#--root=}"
      shift
      ;;
    *)
      _NEW_ARGS+=("$1")
      shift
      ;;
  esac
done
set -- "${_NEW_ARGS[@]+"${_NEW_ARGS[@]}"}"

BRAIN_ROOT="$(resolve_project_root)"
export ROSETTA_PROJECT="$BRAIN_ROOT"
GEN="$SCRIPT_DIR/generate_excerpt.py"
BEGIN_RGX='<!-- rosetta-kb:begin'
END_MARKER='<!-- rosetta-kb:end -->'

# Resolve an agent argument to its absolute agent dir under BRAIN_ROOT/agents.
resolve_agent_dir() {
  local a="$1" name
  # Accept a path or a bare name.
  case "$a" in
    */*) name="$(basename "$a")" ;;
    *)   name="$a" ;;
  esac
  # If they passed the agent.md filename, step up.
  [ "$name" = "agent.md" ] && name="$(basename "$(dirname "$a")")"
  local dir="$BRAIN_ROOT/agents/$name"
  [ -d "$dir" ] || { echo "embed-graph: no such agent dir: $dir" >&2; return 2; }
  printf '%s' "$dir"
}

# Does this agent have a resolvable fact-graph? (exit 0 = graph-blessed)
agent_has_graph() {
  local name="$1"
  python3 "$GEN" --agent "$name" --print-version >/dev/null 2>&1
}

# List every graph-blessed agent name under agents/.
list_graph_agents() {
  local d name
  for d in "$BRAIN_ROOT"/agents/*/; do
    [ -d "$d" ] || continue
    name="$(basename "$d")"
    if agent_has_graph "$name"; then
      printf '%s\n' "$name"
    fi
  done
}

# Registry-driven agent list (the canonical source). $1 = optional slug → its consumers.
# Falls back to list_graph_agents ONLY when no target is given and the registry is unavailable.
# When a target is given, fail-closed: never fall through to the full fleet (E1 regression).
REG="$SCRIPT_DIR/../rosetta/registry.py"
registry_agents() {
  if [ -x "$REG" ] || [ -f "$REG" ]; then
    if [ -n "${1:-}" ]; then
      # Capture stdout only on success so failed "no consumers" messages never become loop tokens.
      local out rc
      out="$(python3 "$REG" consumers "$1" 2>/dev/null)" && rc=0 || rc=$?
      if [ "$rc" -eq 0 ] && [ -n "$out" ]; then
        printf '%s\n' "$out"
        return 0
      fi
      return 1
    else
      if python3 "$REG" agents 2>/dev/null; then
        return 0
      fi
    fi
  fi
  # Fleet-wide fallback only for unscoped --sync / --all (no target).
  if [ -z "${1:-}" ]; then
    list_graph_agents
    return 0
  fi
  return 1
}

# Resolve --sync target → newline-separated agent names.
# Accepts a graph slug (its consumers) OR a single agent name (that agent only).
# Exit 0 + prints agents; exit 1 + stderr on unknown target. Never iterates the fleet by accident.
resolve_sync_targets() {
  local target="$1" out

  if [ -z "$target" ]; then
    # Unscoped: all registry agents (or probe fallback).
    registry_agents || list_graph_agents
    return 0
  fi

  # 1) Graph slug with consumers?
  if out="$(registry_agents "$target")"; then
    # Filter to real agent dirs only (reject garbage tokens).
    local name kept=0
    while IFS= read -r name; do
      [ -z "$name" ] && continue
      if [ -d "$BRAIN_ROOT/agents/$name" ] && [ -f "$BRAIN_ROOT/agents/$name/agent.md" ]; then
        printf '%s\n' "$name"
        kept=1
      fi
    done <<EOF
$out
EOF
    [ "$kept" -eq 1 ] && return 0
  fi

  # 2) Bare agent name → single-agent target.
  if [ -d "$BRAIN_ROOT/agents/$target" ] && [ -f "$BRAIN_ROOT/agents/$target/agent.md" ]; then
    if agent_has_graph "$target"; then
      printf '%s\n' "$target"
      return 0
    fi
    echo "embed-graph: agent $target has no resolvable fact-graph" >&2
    return 1
  fi

  echo "embed-graph: unknown graph slug or agent: $target (no consumers / no such agent)" >&2
  return 1
}

# Generate the fresh embed block for an agent name → stdout. Fail-closed on missing graph.
generate_block() {
  local name="$1"
  python3 "$GEN" --root "$BRAIN_ROOT" --agent "$name" --embed || {
    echo "embed-graph: could not render block for $name (graph missing/unreadable)" >&2
    return 2
  }
}

# Splice the fresh block into agent.md at $1. Idempotent: no write if unchanged.
# - block present → replace region between sentinels
# - block absent  → insert immediately after the frontmatter closing '---' (or at top)
embed_one() {
  local agent_dir name md tmp fresh body newfile have_front
  agent_dir="$1"
  name="$(basename "$agent_dir")"
  md="$agent_dir/agent.md"
  [ -f "$md" ] || { echo "embed-graph: no agent.md at $md" >&2; return 2; }

  fresh="$(generate_block "$name")" || return $?

  # Build the new file content with awk.
  tmp="$(mktemp "${md}.embed.XXXXXX")"
  # pass the fresh block via a temp file to avoid argv/quoting issues
  local blk; blk="$(mktemp "${md}.blk.XXXXXX")"
  printf '%s\n' "$fresh" >"$blk"

  if grep -qF "$BEGIN_RGX" "$md"; then
    # Fail-closed sentinel-pair guard (E7): mismatched begin/end would make the replace-path
    # awk delete from an unmatched begin to EOF. Refuse to write rather than truncate.
    local n_begin n_end
    n_begin="$(grep -cF "$BEGIN_RGX" "$md" || true)"
    n_end="$(grep -cF "$END_MARKER" "$md" || true)"
    if [ "$n_begin" -ne "$n_end" ]; then
      rm -f "$blk" "$tmp"
      echo "embed-graph: $name has mismatched rosetta-kb sentinels (begin=$n_begin end=$n_end); refusing to splice" >&2
      return 2
    fi
    # REPLACE: collapse EVERY existing begin..end block into ONE fresh block, emitted at the
    # position of the first. Robust to duplication (e.g. a sync-hook race embedded twice): the
    # fresh block is emitted once (on the first begin), and all old blocks are dropped.
    awk -v blk="$blk" '
      function emit_block() { close(blk); while ((getline b < blk) > 0) print b; close(blk) }
      /<!-- rosetta-kb:begin/ { if (!done) { emit_block(); done=1 } skipping=1; next }
      /<!-- rosetta-kb:end -->/ { skipping=0; next }
      skipping { next }
      { print }
    ' "$md" >"$tmp"
  else
    # INSERT after frontmatter closing '---'. Frontmatter = leading '---' ... '---'.
    # If the file does not start with '---', insert at top.
    awk -v blk="$blk" '
      function emit_block() {
        close(blk); print ""; while ((getline b < blk) > 0) print b; close(blk)
      }
      NR==1 && /^---[[:space:]]*$/ { in_fm=1; print; next }
      in_fm && /^---[[:space:]]*$/ { in_fm=0; print; emit_block(); done=1; next }
      { print }
      END { if (!done) { } }
    ' "$md" >"$tmp"
    # If no frontmatter was found, the block was never emitted — prepend it.
    if ! grep -qF "$BEGIN_RGX" "$tmp"; then
      { printf '%s\n\n' "$fresh"; cat "$md"; } >"$tmp"
    fi
  fi

  rm -f "$blk"
  # Idempotent: only move if content changed.
  if ! cmp -s "$md" "$tmp"; then
    mv "$tmp" "$md"
    echo "[embed] $name → block updated"
  else
    rm -f "$tmp"
  fi
  return 0
}

# Check one agent: exit 0 if its block matches the freshly generated block.
check_one() {
  local agent_dir name md fresh current begin end
  agent_dir="$1"
  name="$(basename "$agent_dir")"
  md="$agent_dir/agent.md"
  [ -f "$md" ] || { echo "DRIFT  $name  (no agent.md)"; return 1; }
  fresh="$(generate_block "$name")" || { echo "DRIFT  $name  (graph unreadable)"; return 1; }

  if ! grep -qF "$BEGIN_RGX" "$md"; then
    echo "DRIFT  $name  (no rosetta-kb block — run: embed-graph.sh $name)"
    return 1
  fi

  # Extract the on-disk block (begin..end inclusive) and compare to fresh.
  current="$(awk '/<!-- rosetta-kb:begin/{f=1} f{print} /<!-- rosetta-kb:end -->/{f=0}' "$md")"
  if [ "$current" = "$fresh" ]; then
    echo "OK     $name"
    return 0
  fi
  echo "DRIFT  $name  (block differs from fact-graph — run: embed-graph.sh $name)"
  return 1
}

main() {
  local mode="${1:-}"
  case "$mode" in
    --list)
      list_graph_agents
      return 0
      ;;
    --all)
      local d rc=0 name
      for d in "$BRAIN_ROOT"/agents/*/; do
        [ -d "$d" ] || continue
        name="$(basename "$d")"
        agent_has_graph "$name" || continue
        embed_one "$d" || rc=1
      done
      return $rc
      ;;
    --sync)
      # Registry-driven HARD REPLACE: for every consumer (all, one slug's, or one agent), blow
      # away any existing rosetta-kb block(s) and write one fresh render. Convergent — running
      # N times is byte-identical. This is the standardized distribution verb (rosetta-sync).
      # Target may be a graph slug OR an agent name (resolved by resolve_sync_targets).
      local target="${2:-}" name rc=0 out err targets
      if ! targets="$(resolve_sync_targets "$target")"; then
        return 1
      fi
      if [ -z "$targets" ]; then
        echo "embed-graph: no agents to sync for target ${target:-'(all)'}" >&2
        return 1
      fi
      while IFS= read -r name; do
        [ -z "$name" ] && continue
        # Capture stdout+stderr separately so failures surface by agent name (AC5).
        if out="$(embed_one "$BRAIN_ROOT/agents/$name" 2>&1)"; then
          case "$out" in *updated*) echo "$out";; esac
        else
          rc=1
          echo "[sync] FAIL $name: $out" >&2
        fi
      done <<EOF
$targets
EOF
      if [ $rc -eq 0 ]; then
        echo "[sync] all consumers in sync with the registry (hard replace complete)"
      else
        echo "[sync] completed with errors (rc=$rc)" >&2
      fi
      return $rc
      ;;
    --check)
      local target="${2:-}" agent_dir rc=0 name
      if [ "$target" = "--all" ] || [ -z "$target" ]; then
        for name in $(list_graph_agents); do
          check_one "$BRAIN_ROOT/agents/$name" || rc=1
        done
      else
        agent_dir="$(resolve_agent_dir "$target")" || return $?
        check_one "$agent_dir" || rc=1
      fi
      return $rc
      ;;
    -h|--help)
      sed -n '2,28p' "$0"
      return 0
      ;;
    *)
      local agent_dir
      agent_dir="$(resolve_agent_dir "$mode")" || return $?
      embed_one "$agent_dir"
      return $?
      ;;
  esac
}

main "$@"
