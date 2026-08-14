#!/usr/bin/env bash
# Local distributor: expose plugin skills to skillshare extras + sync.
# On this machine this is the path that keeps claude/codex/opencode/grok identical.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
EXTRAS="${SKILLSHARE_EXTRAS:-$HOME/.config/skillshare/extras}"
LINK="$EXTRAS/rosetta-skills"

mkdir -p "$EXTRAS"
if [ -L "$LINK" ] || [ -d "$LINK" ]; then
  rm -rf "$LINK"
fi
ln -s "$ROOT/skills" "$LINK"
echo "extras → $LINK -> $ROOT/skills"

if command -v skillshare >/dev/null 2>&1; then
  # Point extras_source at the extras dir (already the default) and sync.
  skillshare extras source "$EXTRAS" >/dev/null || true
  skillshare install "$ROOT" --skill 'rosetta-*' --all --yes --force || \
    echo "WARN: skillshare install failed; falling back to install.sh"
  skillshare sync || true
  skillshare status || true
else
  echo "skillshare not installed; run ./install.sh"
  "$ROOT/install.sh"
fi

"$ROOT/scripts/assert-no-drift.sh" || true
