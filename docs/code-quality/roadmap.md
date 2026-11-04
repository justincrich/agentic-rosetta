# Cleaning roadmap — agentic-rosetta

Last run: 2026-11-04 (`repo`, direction "organize the plugin") — in progress.
Direction in force: module organization, DRY, typed definitions.
Blast radius chosen: `repo`.

Ranked by (how often the file changes) × (how much structure it would repair). Every item is
structure-only; a behaviour change it uncovers becomes a `known-problems` line, not a fix.

## Ranked work list

### 1. One home for each shared decision — **in progress (2026-11-04 run)**
Seven duplicated decisions (repo root, agent stem/role, agent→slug, graph layout, semver,
canonical-JSON/atomic-write, TLD set) lived in two or three modules each, and two had **diverged**
in behaviour. Fixed first because every later split depends on it.

### 2. Split `provenance.py` (1089 lines, 6 jobs) — **next up**
The single largest comprehension barrier in the Python tree, and the second-hottest file. Split by
the decision each part hides: semver → pure, NRRA parsing → pure, locators → pure, pins → network,
stale → network, history → disk. Keep `provenance.py` as a facade so no caller changes in the same
commit. **Cheaper now** than it was, because run 1 gave it `versions`, `locators` and `jsonfile` to
call instead of inline.

### 3. Split `write-graph.py` (749 lines, 6 verbs + validator + snapshot + merge)
Hottest file in the tree (7 changes in 6 months). `cmd_update` and `cmd_apply_nrra` repeat the same
snapshot/merge/persist sequence; the validator now lives in `factgraph.py` after run 1. Next
candidate: one `_persist_revision()` helper for both verbs.

### 4. `registry.py` — parse once, drop the module globals
Landed in run 1 (U12, U13). Keep an eye on it: `scan()` is the only place a fact-graph is parsed.

### 5. `pipeline.schema.json` — shrink with `$defs`
`zod-json-schema.ts` has no `$defs` support, so `SourcePin.unitPins` is inlined four times and the
file is 1723 generated lines whose semantic content is ~250. Run 1 teaches the converter `$defs` +
`$ref`. Revisit whether to wire it into `write-graph.py` once the pydantic model exists on both
sides.

### 6. Get 220 lines of test out of a shipped asset
`assets/rosetta_config.py` is 738 lines, of which `selftest()` is 220. The whole file is copied
verbatim into 14 skill `assets/` directories and into `$ROSETTA_HOME/bin/`. Tests do not belong in
the artifact users install. Needs `sync-assets.sh` in the same commit.

### 7. Comprehension pass on `kb-delivery`
`condensed_excerpt()`'s docstring claimed it is "derived from the graph, not copy-pasted, so it
cannot drift" — it is a hardcoded Cloudflare Workers paragraph, and `DEFAULT_DOMAIN` is
`cloudflare-workers`. The docstring is wrong; the behaviour is a recorded defect.

### 8. Module form (deferred — needs a naming migration)
`write-graph.py`, `generate_excerpt.py`, `materialize-grok-skills.py` cannot be imported. Making the
logic an importable package while keeping hyphenated entry points means updating every skill, doc
and `assert-no-drift.sh` reference by name in one commit. Deferred until items 1–3 land, because it
is a rename of things currently holding the logic.

## Baseline at the last run

All 12 Python gates green. `bun test`: 78 pass / 3 fail (`real Grok ACP worker lifecycle`,
environmental). `bun run typecheck` green.

## How a run picks up

`/clean-code-plan repo` with no arguments takes the default scope: files changed since `Last run`
plus the top item. Running the same command again resumes.
