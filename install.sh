#!/usr/bin/env bash
# agentic-rosetta install.sh
# Installs the 4 Rosetta skills into one or more harness skill directories.
# Ships MACHINERY ONLY — no language knowledge bases are installed, no project files edited.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_SRC="$SCRIPT_DIR/skills"

# The skills this plugin owns. Uninstall removes EXACTLY these — nothing else.
ROSETTA_SKILLS="rosetta-bootstrap rosetta-research rosetta-validate rosetta-upgrade"

ALL_HARNESSES="claude codex opencode pi droid"

# Harness -> skills directory (matches brain skillshare config).
skills_dir_for() {
  case "$1" in
    claude)   echo "$HOME/.claude/skills" ;;
    codex)    echo "$HOME/.codex/skills" ;;
    opencode) echo "$HOME/.config/opencode/skills" ;;
    pi)       echo "$HOME/.pi/agent/skills" ;;
    droid)    echo "$HOME/.factory/skills" ;;
    *)        return 1 ;;
  esac
}

# Parent dir whose existence implies "this harness is installed".
harness_root_for() {
  case "$1" in
    claude)   echo "$HOME/.claude" ;;
    codex)    echo "$HOME/.codex" ;;
    opencode) echo "$HOME/.config/opencode" ;;
    pi)       echo "$HOME/.pi/agent" ;;
    droid)    echo "$HOME/.factory" ;;
    *)        return 1 ;;
  esac
}

ARG="${1:-all}"
case "$ARG" in
  all)
    # bash-3.2-safe detection (no mapfile): collect harnesses whose root dir exists.
    TARGETS=""
    for h in $ALL_HARNESSES; do
      [ -d "$(harness_root_for "$h")" ] && TARGETS="$TARGETS $h"
    done
    if [ -z "$(echo "$TARGETS" | tr -d ' ')" ]; then
      echo "No installed harness detected; defaulting to: claude"
      TARGETS="claude"
    fi
    ;;
  claude|codex|opencode|pi|droid)
    TARGETS="$ARG"
    ;;
  -h|--help)
    echo "Usage: ./install.sh [claude|codex|opencode|pi|droid|all]"
    echo "Default: all (every detected harness; falls back to claude)."
    exit 0
    ;;
  *)
    echo "Unknown harness: $ARG"
    echo "Supported: claude codex opencode pi droid all"
    exit 1
    ;;
esac

echo "=== agentic-rosetta install ==="
echo "Skills:  $ROSETTA_SKILLS"
echo "Targets:$TARGETS"
echo "(machinery only — no language knowledge bases are installed)"
echo

# Verify every skill source exists before touching anything.
for name in $ROSETTA_SKILLS; do
  if [ ! -f "$SKILLS_SRC/$name/SKILL.md" ]; then
    echo "ERROR: missing $SKILLS_SRC/$name/SKILL.md" >&2
    exit 1
  fi
done

for h in $TARGETS; do
  DEST="$(skills_dir_for "$h")"
  echo "--- $h -> $DEST ---"
  mkdir -p "$DEST"
  for name in $ROSETTA_SKILLS; do
    mkdir -p "$DEST/$name"
    cp "$SKILLS_SRC/$name/SKILL.md" "$DEST/$name/SKILL.md"   # idempotent overwrite
    echo "  installed $name"
  done
  if [ "$h" = "claude" ]; then
    echo
    echo "  NOTE: Claude Code also supports NATIVE plugin install (recommended):"
    echo "    /plugin marketplace add justincrich/agentic-rosetta"
    echo "    /plugin install agentic-rosetta@agentic-rosetta"
    echo "  Use ONE path, not both — the native plugin gives namespaced"
    echo "  /agentic-rosetta:rosetta-*, this drop-in gives bare /rosetta-*."
  fi
  echo
done

echo "Done. Skills available: $ROSETTA_SKILLS"
echo "Next: /rosetta-bootstrap \"YourTech\" --base-framework React"
echo "Uninstall: ./uninstall.sh$TARGETS"
