---
name: rosetta-sync
description: "Sync Rosetta knowledge graphs into consuming agent bodies. Rebuilds the registry (exhaustive scan), validates registry↔filesystem, then HARD-OVERWRITES each agent's embedded knowledge block from the canonical graph. Trigger: /rosetta-sync [<slug>] [--check]."
metadata:
  version: "1.1"
---

# /rosetta-sync — distribute graphs to agent bodies

The **single source of truth** is `fact-graph.json`. This skill is the apply verb: it writes a fresh generated block between `<!-- rosetta-kb:begin -->` and `<!-- rosetta-kb:end -->` in each consuming `agent.md`. It does not push to harness mirrors; `agentshare` (or the harness's own sync) does that after you commit.

## Resolve the target project and the plugin scripts

Never assume `~/Projects/brain`. Never `cd` to a hardcoded repo.

```
PLUGIN = $ROSETTA_PLUGIN or walk up from this skill until scripts/rosetta/registry.py exists
PROJECT = $ROSETTA_PROJECT or the repo you are standing in (nearest .git / .rosetta)
REG = "$PLUGIN/scripts/rosetta/registry.py"
EMB = "$PLUGIN/scripts/kb-delivery/embed-graph.sh"
```

All real subprocesses. Pass `--root "$PROJECT"` so nothing resolves outside the plugin or the target.

```bash
python3 "$REG" --root "$PROJECT" scan
python3 "$REG" --root "$PROJECT" validate          # STOP if drifted
bash "$EMB" --root "$PROJECT" --sync               # or --sync <slug>
bash "$EMB" --root "$PROJECT" --check --all
```

`--check` is steps 1, 2, and the verify only (no writes).

## Inputs

- `<slug>` (optional) — one graph's consumers. Omit = whole fleet in that project.
- `--check` — report drift; exit nonzero if any block differs.

## What "hard overwrite" means

Every `rosetta-kb` region is blown away and replaced with one fresh render. The body above the sentinels is hand-authored and never regenerated.

## Composed knowledge bases

A graph that borrowed another's facts **by value** carries them in its own body under
`composed[<slug>]`, so the same block renders the borrowed facts under a `[composed: <slug>]`
banner. `--sync` needs nothing extra; if the original moved, propagate first (see
`/rosetta-upgrade` § Propagation) and check with
`python3 scripts/rosetta/write-graph.py --root "$PROJECT" --check-composed <borrower>`.

## Output

- `registry.py validate` → `OK`
- `embed-graph.sh --check --all` → exit 0
- Every graph-blessed agent has exactly one `rosetta-kb` block
