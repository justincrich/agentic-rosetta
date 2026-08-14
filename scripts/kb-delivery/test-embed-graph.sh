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
cp "$HERE/generate_excerpt.py" "$ROOT/scripts/kb-delivery/"
cp "$HERE/embed-graph.sh" "$ROOT/scripts/kb-delivery/"
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

if [ "$fail" -eq 0 ]; then
  echo "ok — embed-graph.sh insert + drift-detect + idempotency + fail-closed all passed"
  exit 0
fi
exit 1
