#!/usr/bin/env bash
# agentic-rosetta uninstall.sh
# Removes ONLY the 4 Rosetta skills from the given harness skill directories.
# Never deletes a whole skills dir; never touches user KBs or .rosetta/.
set -euo pipefail

ROSETTA_SKILLS="rosetta-bootstrap rosetta-research rosetta-validate rosetta-upgrade"
ALL_HARNESSES="claude codex opencode pi droid"

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
    TARGETS=""
    for h in $ALL_HARNESSES; do
      [ -d "$(harness_root_for "$h")" ] && TARGETS="$TARGETS $h"
    done
    ;;
  claude|codex|opencode|pi|droid)
    TARGETS="$ARG"
    ;;
  -h|--help)
    echo "Usage: ./uninstall.sh [claude|codex|opencode|pi|droid|all]"
    exit 0
    ;;
  *)
    echo "Unknown harness: $ARG"
    exit 1
    ;;
esac

echo "=== agentic-rosetta uninstall ==="
echo "Targets:${TARGETS:- <none detected>}"
echo

for h in $TARGETS; do
  DEST="$(skills_dir_for "$h")"
  echo "--- $h -> $DEST ---"
  for name in $ROSETTA_SKILLS; do
    if [ -d "$DEST/$name" ]; then
      rm -rf "${DEST:?}/$name"     # ${DEST:?} guards against an empty variable
      echo "  removed $name"
    else
      echo "  (absent) $name"
    fi
  done
  if [ "$h" = "claude" ]; then
    echo "  NOTE: if you installed via /plugin, also run: /plugin uninstall agentic-rosetta"
  fi
  echo
done

echo "Done. (Your project .rosetta/ KBs were left untouched.)"
