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
GRAPH_FIXTURE="$PLUGIN_ROOT/scripts/testdata/graphs/cloudflare-workers.fact-graph.json"
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
mkdir -p "$ROOT/scripts/kb-delivery" "$ROOT/agents/cloudflare-workers-implementer" "$ROOT/.rosetta/docs/cloudflare-workers"
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
cp "$GRAPH_FIXTURE" "$ROOT/.rosetta/docs/cloudflare-workers/fact-graph.json"

# a block-less agent with frontmatter
cat > "$ROOT/agents/cloudflare-workers-implementer/agent.md" <<'EOF'
---
name: cloudflare-workers-implementer
description: test
---
# CW Implementer

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
"$EG" cloudflare-workers-implementer >/dev/null 2>&1 || fail "insert-exit" "(rc=$?)"
grep -q 'rosetta-kb:begin' "$ROOT/agents/cloudflare-workers-implementer/agent.md" || fail "insert-begin" "(no begin marker)"
grep -q 'rosetta-kb:end' "$ROOT/agents/cloudflare-workers-implementer/agent.md" || fail "insert-end" "(no end marker)"
# hand prose outside the block survives
grep -q 'old hand-written summary' "$ROOT/agents/cloudflare-workers-implementer/agent.md" || fail "insert-preserves-prose" "(hand prose lost)"
# block lands after the frontmatter closing '---' (front-loaded)
first_fm_end=$(awk 'NR==1&&/^---/{f=1;next} f&&/^---/{print NR;exit}' "$ROOT/agents/cloudflare-workers-implementer/agent.md")
begin_line=$(grep -n 'rosetta-kb:begin' "$ROOT/agents/cloudflare-workers-implementer/agent.md" | head -1 | cut -d: -f1)
[ -n "$first_fm_end" ] && [ "$begin_line" -gt "$first_fm_end" ] || fail "insert-after-frontmatter" "(fm_end=$first_fm_end begin=$begin_line)"
# version stamped in the begin marker
grep -q 'kb_version="' "$ROOT/agents/cloudflare-workers-implementer/agent.md" || fail "insert-version-marker" "(no kb_version in marker)"

# (b) DRIFT detection: --check exits 0 right after a clean embed
"$EG" --check cloudflare-workers-implementer >/dev/null 2>&1 && echo "check-clean: ok" || fail "check-clean-exit0" "(rc=$?)"

# mutate the graph (valid JSON, different bytes) → version/sha changes → block must drift
python3 - "$ROOT/.rosetta/docs/cloudflare-workers/fact-graph.json" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d.setdefault("_meta", {})["_test_marker"] = "mutated"
json.dump(d, open(p, "w"))
PY
"$EG" --check cloudflare-workers-implementer >/dev/null 2>&1 && fail "drift-detected-exit1" "(check passed after graph mutation)" || echo "drift-detected: ok"

# regenerate → back in sync → check exits 0
"$EG" cloudflare-workers-implementer >/dev/null 2>&1 || fail "reembed-exit" "(rc=$?)"
"$EG" --check cloudflare-workers-implementer >/dev/null 2>&1 || fail "reembed-clean" "(rc=$?)"

# (c) IDEMPOTENT: re-run produces no change (sha identical)
before=$(shasum "$ROOT/agents/cloudflare-workers-implementer/agent.md" | awk '{print $1}')
"$EG" cloudflare-workers-implementer >/dev/null 2>&1
after=$(shasum "$ROOT/agents/cloudflare-workers-implementer/agent.md" | awk '{print $1}')
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
MD="$ROOT/agents/cloudflare-workers-reviewer/agent.md"
mkdir -p "$(dirname "$MD")"
cat > "$MD" <<'EOF'
---
name: cloudflare-workers-reviewer
description: real frontmatter and Markdown rule regression
---
# Reviewer

Keep this hand-authored prose exactly.

---

Keep the horizontal rule and final newline.
EOF
cp "$MD" "$TMP/original.md"
python3 "$ROOT/scripts/rosetta/registry.py" --root "$ROOT" scan > "$TMP/scan.json" || fail "rule-scan" "(rc=$?)"
python3 "$ROOT/scripts/rosetta/registry.py" --root "$ROOT" graph-for cloudflare-workers-reviewer > "$TMP/mapping.json" || fail "rule-mapping-exit" "(rc=$?)"
python3 - "$ROOT/.rosetta/registry.json" <<'PYREG'
import json, sys
reg = json.load(open(sys.argv[1]))
assert reg["agents"]["cloudflare-workers-reviewer"]["graphs"] == ["cloudflare-workers"]
PYREG
[ "$?" -eq 0 ] || fail "rule-registry-mapping" "(wrong graph)"
python3 "$ROOT/scripts/kb-delivery/generate_excerpt.py" --root "$ROOT" --agent cloudflare-workers-reviewer --embed > "$TMP/render.md" || fail "rule-render" "(rc=$?)"

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
  "$EG" --root "$ROOT" --sync cloudflare-workers-reviewer > "$TMP/$phase.stdout" 2> "$TMP/$phase.stderr" || fail "rule-$phase-sync" "(rc=$?)"
  cp "$MD" "$TMP/$phase.md"
  verify_rule_bytes "$TMP/$phase.md" || fail "rule-$phase-bytes" "(duplicate block or changed prose)"
done
cmp -s "$TMP/first.md" "$TMP/second.md" || fail "rule-first-second-identical" "(bytes changed)"
cmp -s "$TMP/first.md" "$TMP/third.md" || fail "rule-first-third-identical" "(bytes changed)"
"$EG" --root "$ROOT" --check cloudflare-workers-reviewer > "$TMP/check.stdout" 2> "$TMP/check.stderr" || fail "rule-check-clean" "(rc=$?)"
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

if [ "$fail" -eq 0 ]; then
  echo "ok — embed-graph.sh insert + drift-detect + idempotency + fail-closed all passed"
  exit 0
fi
exit 1
