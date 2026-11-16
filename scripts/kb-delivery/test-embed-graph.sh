#!/usr/bin/env bash
# test-embed-graph.sh — real-fixture selftest for embed-graph.sh.
#
# Builds an ISOLATED temp brain root (copy of the generator + embed-graph.sh + a real
# fact-graph + throwaway agents) and exercises: insert into a block-less agent, drift
# detection after a graph edit, idempotent re-run, fail-closed on a missing graph.
#
# No mocks — it invokes the real generate_excerpt.py + embed-graph.sh against a real
# fact-graph.json copied from .rosetta. Run: bash scripts/kb-delivery/test-embed-graph.sh
set -u

HERE="$(cd -P "$(dirname "$0")" >/dev/null 2>&1 && pwd)"
PLUGIN_ROOT="$(cd -P "$HERE/../.." >/dev/null 2>&1 && pwd)"
GRAPH_FIXTURE="$PLUGIN_ROOT/scripts/testdata/graphs/widget-platform.fact-graph.json"
fail=0

# fail <name> <detail>
fail() { echo "FAIL: $1 $2" >&2; fail=$((fail+1)); }

if [ ! -f "$GRAPH_FIXTURE" ]; then
  echo "FAIL: missing real graph fixture $GRAPH_FIXTURE" >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --- build an isolated project root ---
ROOT="$TMP/brain"
mkdir -p "$ROOT/scripts/kb-delivery" "$ROOT/agents/widget-platform-implementer" "$ROOT/.rosetta/docs/widget-platform"
# Every fixture repo carries the version 2 config Rosetta requires (see assets/rosetta_config.py).
cat > "$ROOT/.rosetta/config.json" <<'JSON'
{
  "version": 2,
  "paths": {
    "docs": ".rosetta/docs",
    "research": ".rosetta/research",
    "probes": ".rosetta/probes",
    "agents": "agents"
  }
}
JSON
cp "$HERE/generate_excerpt.py" "$ROOT/scripts/kb-delivery/"
cp "$HERE/embed-graph.sh" "$ROOT/scripts/kb-delivery/"
mkdir -p "$ROOT/scripts/rosetta"
# Copy EVERY rosetta module, not a hand-listed subset: the writers import each other, so a
# new module (naming.py, resolve.py, …) that the list forgot makes the fixture fail with
# "No module named X" and looks like a regression in the code under test.
cp "$PLUGIN_ROOT/scripts/rosetta/"*.py "$ROOT/scripts/rosetta/"
chmod +x "$ROOT/scripts/kb-delivery/embed-graph.sh"
cp "$GRAPH_FIXTURE" "$ROOT/.rosetta/docs/widget-platform/fact-graph.json"

# a block-less agent with frontmatter
cat > "$ROOT/agents/widget-platform-implementer/agent.md" <<'EOF'
---
name: widget-platform-implementer
description: test
---
# Widget Platform Implementer

## Job Statement
do edge stuff

## Bootstrap Context (hand prose)
old hand-written summary — should remain untouched outside the block
EOF

EG="$ROOT/scripts/kb-delivery/embed-graph.sh"
# The fixture copies the scripts, so it must also say where the storage resolver lives: the
# copied roots.py otherwise has no plugin checkout above it to find assets/rosetta_config.py.
export ROSETTA_CONFIG="$PLUGIN_ROOT/assets/rosetta_config.py"
export ROSETTA_HOME="$TMP/device-home"
mkdir -p "$ROSETTA_HOME"

# (a) INSERT into a block-less agent
"$EG" widget-platform-implementer >/dev/null 2>&1 || fail "insert-exit" "(rc=$?)"
grep -q 'rosetta-kb:begin' "$ROOT/agents/widget-platform-implementer/agent.md" || fail "insert-begin" "(no begin marker)"
grep -q 'rosetta-kb:end' "$ROOT/agents/widget-platform-implementer/agent.md" || fail "insert-end" "(no end marker)"
# hand prose outside the block survives
grep -q 'old hand-written summary' "$ROOT/agents/widget-platform-implementer/agent.md" || fail "insert-preserves-prose" "(hand prose lost)"
# block lands after the frontmatter closing '---' (front-loaded)
first_fm_end=$(awk 'NR==1&&/^---/{f=1;next} f&&/^---/{print NR;exit}' "$ROOT/agents/widget-platform-implementer/agent.md")
begin_line=$(grep -n 'rosetta-kb:begin' "$ROOT/agents/widget-platform-implementer/agent.md" | head -1 | cut -d: -f1)
[ -n "$first_fm_end" ] && [ "$begin_line" -gt "$first_fm_end" ] || fail "insert-after-frontmatter" "(fm_end=$first_fm_end begin=$begin_line)"
# version stamped in the begin marker
grep -q 'kb_version="' "$ROOT/agents/widget-platform-implementer/agent.md" || fail "insert-version-marker" "(no kb_version in marker)"

# (b) DRIFT detection: --check exits 0 right after a clean embed
"$EG" --check widget-platform-implementer >/dev/null 2>&1 && echo "check-clean: ok" || fail "check-clean-exit0" "(rc=$?)"

# mutate the graph (valid JSON, different bytes) → version/sha changes → block must drift
python3 - "$ROOT/.rosetta/docs/widget-platform/fact-graph.json" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d.setdefault("_meta", {})["_test_marker"] = "mutated"
json.dump(d, open(p, "w"))
PY
"$EG" --check widget-platform-implementer >/dev/null 2>&1 && fail "drift-detected-exit1" "(check passed after graph mutation)" || echo "drift-detected: ok"

# regenerate → back in sync → check exits 0
"$EG" widget-platform-implementer >/dev/null 2>&1 || fail "reembed-exit" "(rc=$?)"
"$EG" --check widget-platform-implementer >/dev/null 2>&1 || fail "reembed-clean" "(rc=$?)"

# (c) IDEMPOTENT: re-run produces no change (sha identical)
before=$(shasum "$ROOT/agents/widget-platform-implementer/agent.md" | awk '{print $1}')
"$EG" widget-platform-implementer >/dev/null 2>&1
after=$(shasum "$ROOT/agents/widget-platform-implementer/agent.md" | awk '{print $1}')
[ "$before" = "$after" ] || fail "idempotent" "(sha changed on no-op re-run)"

# (d) FAIL-CLOSED: agent whose graph is missing → embed exits nonzero, no block written
mkdir -p "$ROOT/agents/no-graph-agent"
cat > "$ROOT/agents/no-graph-agent/agent.md" <<'EOF'
---
name: no-graph-agent
description: test
---
no graph for this one
EOF
# no domain 'no-graph' exists in .rosetta, and no agent-local kb → generator exits 2
if "$EG" no-graph-agent >/dev/null 2>&1; then
  fail "fail-closed-exit" "(embed succeeded with no graph)"
else
  echo "fail-closed: ok"
fi

# (e) Fresh frontmatter followed by a Markdown rule inserts once and converges immediately.
MD="$ROOT/agents/widget-platform-reviewer/agent.md"
mkdir -p "$(dirname "$MD")"
cat > "$MD" <<'EOF'
---
name: widget-platform-reviewer
description: real frontmatter and Markdown rule regression
---
# Reviewer

Keep this hand-authored prose exactly.

---

Keep the horizontal rule and final newline.
EOF
cp "$MD" "$TMP/original.md"
python3 "$ROOT/scripts/rosetta/registry.py" --root "$ROOT" scan > "$TMP/scan.json" || fail "rule-scan" "(rc=$?)"
python3 "$ROOT/scripts/rosetta/registry.py" --root "$ROOT" graph-for widget-platform-reviewer > "$TMP/mapping.json" || fail "rule-mapping-exit" "(rc=$?)"
python3 - "$ROOT/.rosetta/registry.json" <<'PYREG'
import json, sys
reg = json.load(open(sys.argv[1]))
assert reg["agents"]["widget-platform-reviewer"]["graphs"] == ["widget-platform"]
PYREG
[ "$?" -eq 0 ] || fail "rule-registry-mapping" "(wrong graph)"
python3 "$ROOT/scripts/kb-delivery/generate_excerpt.py" --root "$ROOT" --agent widget-platform-reviewer --embed > "$TMP/render.md" || fail "rule-render" "(rc=$?)"

# Independent byte splice oracle: no YAML reserialization or whitespace normalization.
# The single newline preceding the generated block is the writer's insertion separator.
verify_rule_bytes() {
  python3 - "$TMP/original.md" "$TMP/render.md" "$1" <<'PYBYTES'
from pathlib import Path
import sys
original, block, actual = (Path(p).read_bytes() for p in sys.argv[1:])
lines = original.splitlines(keepends=True)
assert lines[0] == b"---\n"
cut = sum(map(len, lines[:1 + next(i for i in range(1, len(lines)) if lines[i] == b"---\n")]))
prefix, suffix = original[:cut], original[cut:]
assert actual.count(b"<!-- rosetta-kb:begin") == 1, "duplicate begin block"
assert actual.count(b"<!-- rosetta-kb:end -->") == 1, "duplicate end block"
assert actual == prefix + b"\n" + block + suffix, "full splice bytes changed"
assert actual[:len(prefix)] == prefix, "frontmatter bytes changed"
assert actual[len(prefix) + 1 + len(block):] == suffix, "hand prose bytes changed"
PYBYTES
}
for phase in first second third; do
  "$EG" --root "$ROOT" --sync widget-platform-reviewer > "$TMP/$phase.stdout" 2> "$TMP/$phase.stderr" || fail "rule-$phase-sync" "(rc=$?)"
  cp "$MD" "$TMP/$phase.md"
  verify_rule_bytes "$TMP/$phase.md" || fail "rule-$phase-bytes" "(duplicate block or changed prose)"
done
cmp -s "$TMP/first.md" "$TMP/second.md" || fail "rule-first-second-identical" "(bytes changed)"
cmp -s "$TMP/first.md" "$TMP/third.md" || fail "rule-first-third-identical" "(bytes changed)"
"$EG" --root "$ROOT" --check widget-platform-reviewer > "$TMP/check.stdout" 2> "$TMP/check.stderr" || fail "rule-check-clean" "(rc=$?)"
cmp -s "$TMP/third.md" "$MD" || fail "rule-check-readonly" "(check wrote bytes)"

# A real hand-prose corruption must be rejected by the identical byte oracle.
cp "$TMP/first.md" "$TMP/corrupt.md"
python3 - "$TMP/corrupt.md" <<'PYCORRUPT'
from pathlib import Path
import sys
p = Path(sys.argv[1])
raw = p.read_bytes()
assert raw.count(b"Keep this hand-authored prose exactly.") == 1
p.write_bytes(raw.replace(b"Keep this hand-authored prose exactly.", b"Keep this hand-authored prose changed.", 1))
PYCORRUPT
if verify_rule_bytes "$TMP/corrupt.md" > "$TMP/corrupt.stdout" 2> "$TMP/corrupt.stderr"; then
  fail "rule-prose-control" "(oracle accepted changed prose)"
else
  echo "rule-prose-control: rejected actual changed prose"
fi
cp "$TMP/first.md" "$TMP/corrupt.md"
verify_rule_bytes "$TMP/corrupt.md" || fail "rule-prose-control-restored" "(restore failed)"
echo "rule-regression: real registry + renderer + three syncs + readonly check exercised"

# ══════════════ the repo's config decides the layout (AC-2 / AC-8 / AC-9) ══════════════
CUSTOM="$TMP/custom"
mkdir -p "$CUSTOM/.rosetta" "$CUSTOM/kb/graphs/x" "$CUSTOM/prompts/x-implementer" \
         "$CUSTOM/scripts/kb-delivery" "$CUSTOM/scripts/rosetta"
cp "$HERE/generate_excerpt.py" "$CUSTOM/scripts/kb-delivery/"
cp "$HERE/embed-graph.sh" "$CUSTOM/scripts/kb-delivery/"
cp "$PLUGIN_ROOT/scripts/rosetta/"*.py "$CUSTOM/scripts/rosetta/"
chmod +x "$CUSTOM/scripts/kb-delivery/embed-graph.sh"
cp "$GRAPH_FIXTURE" "$CUSTOM/kb/graphs/x/fact-graph.json"
cat > "$CUSTOM/.rosetta/config.json" <<'JSON'
{
  "version": 2,
  "paths": {
    "docs": "kb/graphs",
    "research": "kb/research",
    "probes": "kb/probes",
    "agents": "prompts"
  }
}
JSON
cat > "$CUSTOM/prompts/x-implementer/agent.md" <<'EOF'
---
name: x-implementer
description: custom-layout agent
---
# X Implementer

## Hand prose that must survive the splice
do the x thing
EOF
cp "$CUSTOM/prompts/x-implementer/agent.md" "$TMP/custom-original.md"
CEG="$CUSTOM/scripts/kb-delivery/embed-graph.sh"

# AC-8: --sync x writes the sentinel region of the configured agents dir and nothing else.
"$CEG" --sync x >"$TMP/custom-sync.stdout" 2>"$TMP/custom-sync.stderr" \
  || fail "custom-sync-exit" "$(cat "$TMP/custom-sync.stderr")"
grep -q 'rosetta-kb:begin' "$CUSTOM/prompts/x-implementer/agent.md" \
  || fail "custom-sync-block" "(no block written under prompts/)"
grep -q 'do the x thing' "$CUSTOM/prompts/x-implementer/agent.md" \
  || fail "custom-sync-prose" "(hand prose outside the block lost)"
python3 - "$TMP/custom-original.md" "$CUSTOM/prompts/x-implementer/agent.md" <<'PYCUSTOM'
import sys
from pathlib import Path
before, after = (Path(p).read_bytes() for p in sys.argv[1:])
begin, end = b"<!-- rosetta-kb:begin", b"<!-- rosetta-kb:end -->"
# The writer inserts the block after the closing frontmatter line with one separator newline,
# so the exact oracle is: before == prefix + suffix and after == prefix + "\n" + block + suffix.
lines = before.splitlines(keepends=True)
cut = sum(map(len, lines[:1 + next(i for i in range(1, len(lines)) if lines[i] == b"---\n")]))
prefix, suffix = before[:cut], before[cut:]
assert after.count(begin) == 1 and after.count(end) == 1, "block duplicated"
block = after[after.index(begin):after.index(end) + len(end)]
# The writer separates the block from the prose by one newline on each side.
assert after == prefix + b"\n" + block + b"\n" + suffix, "bytes outside the sentinels changed"
assert after[:len(prefix)] == prefix, "frontmatter bytes changed"
assert after[len(prefix) + 2 + len(block):] == suffix, "hand prose bytes changed"
PYCUSTOM
[ "$?" -eq 0 ] || fail "custom-sync-only-sentinels" "(bytes outside the sentinels changed)"
"$CEG" --root "$CUSTOM" --check x-implementer >"$TMP/custom-check.stdout" 2>&1 \
  || fail "custom-check-exit0" "$(cat "$TMP/custom-check.stdout")"
[ ! -d "$CUSTOM/.rosetta/docs" ] || fail "custom-no-legacy-docs" "(.rosetta/docs exists)"
[ ! -d "$CUSTOM/agents" ] || fail "custom-no-agents-dir" "(agents/ exists)"
grep -q "kb/graphs/x/fact-graph.json" <(python3 "$CUSTOM/scripts/rosetta/registry.py" --root "$CUSTOM" list) \
  || fail "custom-registry-path" "(registry does not list kb/graphs/x/fact-graph.json)"

# AC-9: changing ONLY paths.docs moves where embed-graph reads; the old root stops being read.
python3 - "$CUSTOM/.rosetta/config.json" <<'PY'
import json, sys
p = sys.argv[1]
cfg = json.load(open(p))
cfg["paths"]["docs"] = "kb/other"
json.dump(cfg, open(p, "w"), indent=2)
PY
if "$CEG" --root "$CUSTOM" --check x-implementer >"$TMP/custom-moved.stdout" 2>&1; then
  fail "custom-docs-move-detected" "(check passed with the graph only under the old root)"
else
  echo "custom-docs-move-detected: ok"
fi
mkdir -p "$CUSTOM/kb/other"
cp -R "$CUSTOM/kb/graphs/x" "$CUSTOM/kb/other/x"
"$CEG" --root "$CUSTOM" --check x-implementer >"$TMP/custom-moved2.stdout" 2>&1 \
  || fail "custom-docs-move-reads-new-root" "$(cat "$TMP/custom-moved2.stdout")"

# AC-2: a repo with no config is refused, naming the file, and no agent.md is touched.
BARE="$TMP/bare"
mkdir -p "$BARE/prompts/x-implementer" "$BARE/scripts/kb-delivery" "$BARE/scripts/rosetta"
cp "$HERE/generate_excerpt.py" "$HERE/embed-graph.sh" "$BARE/scripts/kb-delivery/"
cp "$PLUGIN_ROOT/scripts/rosetta/"*.py "$BARE/scripts/rosetta/"
chmod +x "$BARE/scripts/kb-delivery/embed-graph.sh"
printf '# untouched\n' > "$BARE/prompts/x-implementer/agent.md"
# A repo boundary, exactly as the AC-2 fixture defines it (`git init -q "$T"`): without one, a temp
# directory that happens to sit under $HOME resolves to the enclosing tree and the refusal names
# THAT config — a fixture artifact, not the behaviour under test.
git init -q "$BARE"
BEG="$BARE/scripts/kb-delivery/embed-graph.sh"
if "$BEG" --root "$BARE" --sync x >"$TMP/bare.stdout" 2>"$TMP/bare.stderr"; then
  fail "bare-refused" "(sync succeeded with no config)"
else
  echo "bare-refused: ok"
fi
grep -qF "$BARE/.rosetta/config.json" "$TMP/bare.stderr" \
  || fail "bare-names-config" "$(cat "$TMP/bare.stderr")"
[ "$(cat "$BARE/prompts/x-implementer/agent.md")" = "# untouched" ] \
  || fail "bare-leaves-agent-md" "(agent.md was modified)"
[ ! -e "$BARE/.rosetta" ] || fail "bare-creates-nothing" "(.rosetta was created)"


if [ "$fail" -eq 0 ]; then
  echo "ok — embed-graph.sh insert + drift-detect + idempotency + fail-closed all passed"
  exit 0
fi
exit 1
