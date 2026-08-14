#!/usr/bin/env bash
# Fail if any harness copy of a rosetta SKILL.md differs from the plugin source,
# or if a SKILL.md names a plugin script that does not exist.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SKILLS="rosetta-bootstrap rosetta-research rosetta-validate rosetta-upgrade rosetta-sync rosetta-ab-test rosetta-stress-test"
# Grok slash is the plugin namespace (/rosetta:research). Do not require
# ~/.grok/skills/rosetta-* drop-ins — those become /user:rosetta-research.
HARNESSES="claude:$HOME/.claude/skills codex:$HOME/.codex/skills opencode:$HOME/.config/opencode/skills"
SCRIPTS_ONLY=0
[ "${1:-}" = "--scripts-only" ] && SCRIPTS_ONLY=1

fail=0
fail() { echo "FAIL: $*" >&2; fail=$((fail+1)); }

for name in $SKILLS; do
  src="$ROOT/skills/$name/SKILL.md"
  [ -f "$src" ] || { fail "missing source $src"; continue; }
  # named scripts must exist in the plugin
  while IFS= read -r mentioned; do
    [ -z "$mentioned" ] && continue
    case "$mentioned" in
      scripts/*)
        [ -e "$ROOT/$mentioned" ] || fail "$name names missing $mentioned"
        ;;
    esac
  done < <(grep -oE 'scripts/[A-Za-z0-9_./-]+' "$src" | sort -u)

  [ "$SCRIPTS_ONLY" = "1" ] && continue
  for spec in $HARNESSES; do
    h="${spec%%:*}"
    dir="${spec#*:}"
    dst="$dir/$name/SKILL.md"
    if [ ! -f "$dst" ]; then
      fail "$h missing $name ($dst)"
      continue
    fi
    if ! cmp -s "$src" "$dst"; then
      fail "$h $name differs from plugin ($(shasum -a 256 "$src" | awk '{print substr($1,1,12)}') vs $(shasum -a 256 "$dst" | awk '{print substr($1,1,12)}'))"
    fi
  done
done

# Grok short-name pack must match a fresh generate from skills/rosetta-*
if ! python3 "$ROOT/scripts/materialize-grok-skills.py" --check; then
  fail "grok-skills/ drifted from skills/rosetta-*"
fi

if [ "$fail" -ne 0 ]; then
  echo "assert-no-drift: $fail failure(s)" >&2
  exit 1
fi
echo "assert-no-drift: all harness copies match plugin; named scripts exist; grok-skills current"
