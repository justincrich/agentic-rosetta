#!/usr/bin/env bash
# agentic-rosetta uninstall.sh
# Removes ONLY the 4 Rosetta skills from the given harness skill directories.
# Never deletes a whole skills dir; never touches user KBs or .rosetta/.
set -euo pipefail

ROSETTA_SKILLS="rosetta-bootstrap rosetta-research rosetta-validate rosetta-upgrade rosetta-sync rosetta-ab-test rosetta-stress-test"
ALL_HARNESSES="claude codex opencode grok pi droid"

skills_dir_for() {
  case "$1" in
    claude)   echo "$HOME/.claude/skills" ;;
    codex)    echo "$HOME/.codex/skills" ;;
    opencode) echo "$HOME/.config/opencode/skills" ;;
    grok)     echo "$HOME/.grok/skills" ;;
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
    grok)     echo "$HOME/.grok" ;;
    pi)       echo "$HOME/.pi/agent" ;;
    droid)    echo "$HOME/.factory" ;;
    *)        return 1 ;;
  esac
}

PURGE=0
ARGS=""
for a in "$@"; do
  case "$a" in
    --purge) PURGE=1 ;;
    *) ARGS="$ARGS $a" ;;
  esac
done
set -- $ARGS

ARG="${1:-all}"
case "$ARG" in
  all)
    TARGETS=""
    for h in $ALL_HARNESSES; do
      [ -d "$(harness_root_for "$h")" ] && TARGETS="$TARGETS $h"
    done
    ;;
  claude|codex|opencode|grok|pi|droid)
    TARGETS="$ARG"
    ;;
  -h|--help)
    echo "Usage: ./uninstall.sh [claude|codex|opencode|grok|pi|droid|all] [--purge]"
    echo "  --purge  also delete \$ROSETTA_HOME (default ~/.rosetta): your config and any"
    echo "           artifacts stored under the 'device' root. Off by default — that"
    echo "           directory holds YOUR data, not the plugin's."
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

ROSETTA_HOME_DIR="${ROSETTA_HOME:-$HOME/.rosetta}"
if [ "$PURGE" = "1" ]; then
  if [ -d "$ROSETTA_HOME_DIR" ]; then
    rm -rf "${ROSETTA_HOME_DIR:?}"     # ${VAR:?} guards against an empty variable
    echo "purged $ROSETTA_HOME_DIR (config + any device-root artifacts)"
  else
    echo "(absent) $ROSETTA_HOME_DIR"
  fi
elif [ -d "$ROSETTA_HOME_DIR" ]; then
  echo "Kept $ROSETTA_HOME_DIR (your config + device-root artifacts). Remove with --purge."
fi
echo
echo "Done. (Your project .rosetta/ KBs were left untouched.)"
