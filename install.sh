#!/usr/bin/env bash
# agentic-rosetta install.sh
# Installs Rosetta skills + the fanout engine into detected harness skill dirs.
# On THIS machine the local path is skillshare (see README). This script is for
# external checkouts that are not skillshare-managed.
# Ships MACHINERY ONLY — no language knowledge bases are installed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_SRC="$SCRIPT_DIR/skills"
SRC_ASSETS="$SCRIPT_DIR/assets"
FORCE=0

# The skills this plugin owns. Uninstall removes EXACTLY these — nothing else.
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

newer_than() {
  # true if $1 is newer than $2
  [ -f "$1" ] && [ -f "$2" ] && [ "$1" -nt "$2" ]
}

ARGS=()
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    *) ARGS+=("$a") ;;
  esac
done
set -- "${ARGS[@]+"${ARGS[@]}"}"

ARG="${1:-all}"
case "$ARG" in
  all)
    TARGETS=""
    for h in $ALL_HARNESSES; do
      [ -d "$(harness_root_for "$h")" ] && TARGETS="$TARGETS $h"
    done
    if [ -z "$(echo "$TARGETS" | tr -d ' ')" ]; then
      echo "No installed harness detected; defaulting to: claude"
      TARGETS="claude"
    fi
    ;;
  claude|codex|opencode|grok|pi|droid)
    TARGETS="$ARG"
    ;;
  -h|--help)
    echo "Usage: ./install.sh [claude|codex|opencode|grok|pi|droid|all] [--force]"
    echo "Default: all (every detected harness; falls back to claude)."
    echo "Refuses to clobber a target SKILL.md newer than the plugin source unless --force."
    echo "On this machine prefer: skillshare sync  (this script is the external-user path)."
    exit 0
    ;;
  *)
    echo "Unknown harness: $ARG"
    echo "Supported: claude codex opencode grok pi droid all"
    exit 1
    ;;
esac

echo "=== agentic-rosetta install ==="
echo "Skills:  $ROSETTA_SKILLS"
echo "Targets:$TARGETS"
echo "(machinery only — no language knowledge bases are installed)"
echo

for name in $ROSETTA_SKILLS; do
  if [ ! -f "$SKILLS_SRC/$name/SKILL.md" ]; then
    echo "ERROR: missing $SKILLS_SRC/$name/SKILL.md" >&2
    exit 1
  fi
done

if [ -x "$SCRIPT_DIR/sync-assets.sh" ]; then
  "$SCRIPT_DIR/sync-assets.sh" >/dev/null
fi

# Engine
if [ -f "$SCRIPT_DIR/engine/package.json" ]; then
  if command -v bun >/dev/null 2>&1; then
    echo "--- engine: bun install ---"
    (cd "$SCRIPT_DIR/engine" && bun install)
  elif command -v npm >/dev/null 2>&1; then
    echo "--- engine: npm install ---"
    (cd "$SCRIPT_DIR/engine" && npm install)
  else
    echo "WARN: no bun/node package runner. Install bun, then:"
    echo "  cd $SCRIPT_DIR/engine && bun install"
    echo "The research skill will STOP until that is done (no serial crawl)."
  fi
  echo
fi

ROSETTA_HOME_DIR="${ROSETTA_HOME:-$HOME/.rosetta}"
if [ -f "$SRC_ASSETS/rosetta_config.py" ]; then
  echo "--- device home -> $ROSETTA_HOME_DIR ---"
  mkdir -p "$ROSETTA_HOME_DIR/bin"
  cp "$SRC_ASSETS/rosetta_config.py" "$ROSETTA_HOME_DIR/bin/rosetta_config.py"
  chmod +x "$ROSETTA_HOME_DIR/bin/rosetta_config.py"
  echo "  installed bin/rosetta_config.py"
  # Record where the plugin (engine/ + scripts/) lives. Skills are installed as bare
  # SKILL.md + assets/, so walking up from an installed skill NEVER finds engine/src/research.ts.
  # Without this pointer /rosetta-research must hard-STOP; with it the skill resolves the engine
  # deterministically instead of improvising a filesystem search.
  if [ -f "$SCRIPT_DIR/engine/src/research.ts" ]; then
    printf '%s\n' "$SCRIPT_DIR" > "$ROSETTA_HOME_DIR/plugin-root"
    echo "  recorded plugin-root -> $SCRIPT_DIR"
  else
    echo "  WARN: no engine/src/research.ts under $SCRIPT_DIR; plugin-root not recorded"
    echo "        /rosetta-research will STOP until the engine is present."
  fi
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

if [ -x "$SCRIPT_DIR/scripts/materialize-grok-skills.py" ] || [ -f "$SCRIPT_DIR/scripts/materialize-grok-skills.py" ]; then
  echo "--- grok-skills: short names for /rosetta:research ---"
  python3 "$SCRIPT_DIR/scripts/materialize-grok-skills.py" --root "$SCRIPT_DIR"
  echo
fi

for h in $TARGETS; do
  if [ "$h" = "grok" ]; then
    echo "--- grok ---"
    echo "  Grok uses the plugin, not ~/.grok/skills/rosetta-*."
    echo "  Install: grok plugin install \"$SCRIPT_DIR\" --trust"
    echo "  Slash:   /rosetta:research /rosetta:bootstrap /rosetta:upgrade"
    echo "           /rosetta:validate /rosetta:sync /rosetta:ab-test /rosetta:stress-test"
    echo
    continue
  fi
  DEST="$(skills_dir_for "$h")"
  echo "--- $h -> $DEST ---"
  mkdir -p "$DEST"
  for name in $ROSETTA_SKILLS; do
    src="$SKILLS_SRC/$name/SKILL.md"
    dst="$DEST/$name/SKILL.md"
    mkdir -p "$DEST/$name"
    # Assets are MACHINERY (rosetta_config.py), kept byte-identical everywhere by
    # sync-assets.sh. They are never hand-edited, so the "don't clobber a newer target" guard
    # below must not cover them — skipping them left installed resolvers silently stale, which
    # is exactly the drift the guard exists to prevent.
    if [ -d "$SKILLS_SRC/$name/assets" ]; then
      mkdir -p "$DEST/$name/assets"
      cp "$SKILLS_SRC/$name/assets/"* "$DEST/$name/assets/"
      chmod +x "$DEST/$name/assets/"*.py 2>/dev/null || true
    fi
    if [ -f "$dst" ] && newer_than "$dst" "$src" && [ "$FORCE" != "1" ]; then
      echo "  SKIP $name SKILL.md (target newer than source; pass --force to clobber) — assets synced"
      continue
    fi
    cp "$src" "$dst"
    echo "  installed $name"
  done
  echo
done

echo "Done. Skills available: $ROSETTA_SKILLS"
echo "Engine: $SCRIPT_DIR/engine  (bun run src/research.ts --help)"
echo
echo "This machine: skillshare is the local distributor. Re-run ./install.sh only if"
echo "you are installing into a harness skillshare does not manage."
echo "Uninstall: ./uninstall.sh$TARGETS"
