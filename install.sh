#!/usr/bin/env bash
# agentic-rosetta install.sh
# Installs the 4 Rosetta skills into one or more harness skill directories.
# Ships MACHINERY ONLY — no language knowledge bases are installed, no project files edited.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_SRC="$SCRIPT_DIR/skills"
SRC_ASSETS="$SCRIPT_DIR/assets"

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

# Shared assets must be present and identical across skills before we ship them.
if [ -x "$SCRIPT_DIR/sync-assets.sh" ]; then
  "$SCRIPT_DIR/sync-assets.sh" >/dev/null
fi

# --- device home: config + a copy of the resolver -----------------------------
# Created here so `install.sh` users get it. Native `/plugin install` never runs this script,
# which is why each skill ALSO carries its own assets/ copy of the resolver.
ROSETTA_HOME_DIR="${ROSETTA_HOME:-$HOME/.rosetta}"
if [ -f "$SRC_ASSETS/rosetta_config.py" ]; then
  echo "--- device home -> $ROSETTA_HOME_DIR ---"
  mkdir -p "$ROSETTA_HOME_DIR/bin"
  cp "$SRC_ASSETS/rosetta_config.py" "$ROSETTA_HOME_DIR/bin/rosetta_config.py"
  chmod +x "$ROSETTA_HOME_DIR/bin/rosetta_config.py"
  echo "  installed bin/rosetta_config.py"
  if [ -f "$ROSETTA_HOME_DIR/config.json" ]; then
    echo "  config.json already present — left untouched"
  else
    cat > "$ROSETTA_HOME_DIR/config.json" <<'JSON'
{
  "version": 1,
  "roots": {
    "default": "project"
  }
}
JSON
    echo "  created config.json (default: everything stays in the project)"
  fi
  echo
fi

for h in $TARGETS; do
  DEST="$(skills_dir_for "$h")"
  echo "--- $h -> $DEST ---"
  mkdir -p "$DEST"
  for name in $ROSETTA_SKILLS; do
    mkdir -p "$DEST/$name"
    cp "$SKILLS_SRC/$name/SKILL.md" "$DEST/$name/SKILL.md"   # idempotent overwrite
    # Ship the whole skill: assets/ travels with SKILL.md so the storage resolver is
    # reachable even when the skill is invoked outside this repo.
    if [ -d "$SKILLS_SRC/$name/assets" ]; then
      mkdir -p "$DEST/$name/assets"
      cp "$SKILLS_SRC/$name/assets/"* "$DEST/$name/assets/"
      chmod +x "$DEST/$name/assets/"*.py 2>/dev/null || true
    fi
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
if [ -x "$ROSETTA_HOME_DIR/bin/rosetta_config.py" ]; then
  echo
  echo "Storage roots as resolved from THIS directory ($(pwd)):"
  echo "  (they follow you — 'project' means whatever repo you run a skill in)"
  python3 "$ROSETTA_HOME_DIR/bin/rosetta_config.py" show --format plain \
    || echo "  (run: python3 $ROSETTA_HOME_DIR/bin/rosetta_config.py show)"
  echo
  echo "  project = alongside the repo you are in (default)    device = $ROSETTA_HOME_DIR"
  echo "  or any path, e.g. \"research\": \"~/Projects/brain\"   — see CONFIG.md"
fi
echo
echo "Next: /rosetta-bootstrap \"YourTech\" --base-framework React"
echo "Uninstall: ./uninstall.sh$TARGETS"
