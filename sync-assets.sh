#!/usr/bin/env bash
# sync-assets.sh — keep every skill's bundled copy of the shared assets byte-identical
# to the canonical source in assets/.
#
# WHY: the resolver has to travel with the skills, because Claude Code's NATIVE plugin install
# (/plugin install agentic-rosetta@agentic-rosetta) never runs install.sh — it just exposes the
# skills/ tree. So each skill carries its own assets/rosetta_config.py. One canonical source,
# N copies, and a --check that fails loudly the moment they diverge.
#
# Usage:
#   ./sync-assets.sh           copy canonical -> every skills/*/assets/
#   ./sync-assets.sh --check   verify copies are byte-identical (exit 1 on drift); writes nothing
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR/assets"
SHARED_ASSETS="rosetta_config.py"

MODE="${1:-sync}"
case "$MODE" in
  sync|--check|-h|--help) ;;
  *) echo "Unknown option: $MODE" >&2; echo "Usage: ./sync-assets.sh [--check]" >&2; exit 2 ;;
esac
if [ "$MODE" = "-h" ] || [ "$MODE" = "--help" ]; then
  echo "Usage: ./sync-assets.sh [--check]"
  exit 0
fi

for a in $SHARED_ASSETS; do
  [ -f "$SRC_DIR/$a" ] || { echo "ERROR: missing canonical asset $SRC_DIR/$a" >&2; exit 1; }
done

drift=0
copied=0
checked=0

# After skill assets, keep the Grok short-name pack in lockstep.
if [ "$MODE" != "--check" ]; then
  python3 "$SCRIPT_DIR/scripts/materialize-grok-skills.py" --root "$SCRIPT_DIR" >/dev/null
fi

for skill_dir in "$SCRIPT_DIR"/skills/*/; do
  [ -d "$skill_dir" ] || continue
  skill="$(basename "$skill_dir")"
  for a in $SHARED_ASSETS; do
    dest="$skill_dir/assets/$a"
    if [ "$MODE" = "--check" ]; then
      checked=$((checked + 1))
      if [ ! -f "$dest" ]; then
        echo "  DRIFT $skill/$a — missing"
        drift=$((drift + 1))
      elif ! cmp -s "$SRC_DIR/$a" "$dest"; then
        echo "  DRIFT $skill/$a — differs from canonical"
        drift=$((drift + 1))
      else
        echo "  ok    $skill/$a"
      fi
    else
      mkdir -p "$skill_dir/assets"
      cp "$SRC_DIR/$a" "$dest"
      chmod +x "$dest"
      copied=$((copied + 1))
      echo "  synced $skill/$a"
    fi
  done
done

# The device copy at $ROSETTA_HOME/bin is a THIRD location the resolver lives in (install.sh puts
# it there). It drifted once already — keep it in the same guard.
ROSETTA_HOME_DIR="${ROSETTA_HOME:-$HOME/.rosetta}"
if [ -d "$ROSETTA_HOME_DIR/bin" ]; then
  for a in $SHARED_ASSETS; do
    dest="$ROSETTA_HOME_DIR/bin/$a"
    if [ "$MODE" = "--check" ]; then
      checked=$((checked + 1))
      if [ ! -f "$dest" ] || ! cmp -s "$SRC_DIR/$a" "$dest"; then
        echo "  DRIFT device-home/bin/$a"
        drift=$((drift + 1))
      else
        echo "  ok    device-home/bin/$a"
      fi
    else
      cp "$SRC_DIR/$a" "$dest"; chmod +x "$dest"
      copied=$((copied + 1))
      echo "  synced device-home/bin/$a"
    fi
  done
fi

if [ "$MODE" = "--check" ]; then
  if [ "$drift" -gt 0 ]; then
    echo
    echo "FAIL: $drift of $checked asset copies drifted. Run ./sync-assets.sh" >&2
    exit 1
  fi
  echo
  echo "OK: $checked asset copies byte-identical to $SRC_DIR"
else
  echo
  echo "Synced $copied asset copy/copies from $SRC_DIR"
fi
