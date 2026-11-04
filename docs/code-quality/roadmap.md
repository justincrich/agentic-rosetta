# Cleaning roadmap — agentic-rosetta

Last run: 2026-11-04 (`repo`, direction "organize the plugin") — **complete**.
Next run: start at item 2.
Direction in force: module organization, DRY, typed definitions.
Blast radius chosen: `repo`.

Ranked by (how often the file changes) × (how much structure it would repair). Every item is
structure-only; a behaviour change it uncovers becomes a `known-problems` line, not a fix.

## Ranked work list

### 1. One home for each shared decision — **DONE 2026-11-04**
Seven duplicated decisions collapsed into `scripts/rosetta/{resolve,naming,jsonfile,versions,
locators}.py`, plus `factgraph.py` for the graph shape. Two of them had already diverged in
behaviour (agent role default, agent→slug table). Each new module has a test that pins today's
behaviour, and the branches that were deliberately permissive are pinned as permissive.

### 2. Split `provenance.py` (907 lines, 5 jobs) — **NEXT UP**
Now a facade over `versions`/`locators`/`jsonfile`, which removed ~180 lines, but it still holds
five jobs: NRRA block parsing, URL/locator *harvesting*, network pinning, staleness probing, and
Markdown/JSON companion writing. Split by job, keep `provenance.py` as the facade. Cheaper than it
was, because the version, locator and JSON decisions already have homes to call.

### 3. Split `write-graph.py` (690 lines, 6 verbs)
Still the hottest file (7 changes in 6 months) and still repeats the
snapshot → merge → bump → validate → stamp → persist sequence in `cmd_update` and
`cmd_apply_nrra`. One `_persist_revision()` helper is the next move.

### 4. `registry.py` — parse once, drop the globals — **DONE 2026-11-04**
One read per graph (was four, plus a fifth in `validate`); three mutable globals became one frozen
`Paths`. Remaining: `write-graph._DOCS_DIR` is still a module global — see item 6.

### 5. `pipeline.schema.json` — `$defs` — **DONE 2026-11-04**
`json-schema-defs.ts` indexes each named body by canonical JSON and rewrites equal sub-objects to
`$ref`. 1723 → 830 lines, 11 definitions, every ref resolves.
Still open: nothing reads the artifact yet. Deciding whether to wire `write-graph.py` to it (its
own comment claims it does) is a separate decision.

### 6. Thread `write-graph`'s docs override explicitly
`_DOCS_DIR` is set by `main()` and read by `docs_dir()`, so six verbs depend on hidden state.
Threading it through is 12 call sites across `cmd_create`/`cmd_update`/`cmd_apply_nrra`/
`cmd_backfill`/`cmd_adopt`/`cmd_check`. Deferred from the 2026-11-04 run: the same unit also
touches three of those verbs' bodies, and half-doing it would be worse than not starting.

### 7. Get 220 lines of test out of a shipped asset
`assets/rosetta_config.py` is 738 lines, of which `selftest()` is 220. The whole file is copied
verbatim into 14 skill `assets/` directories and into `$ROSETTA_HOME/bin/`. Tests do not belong in
the artifact users install. Needs `sync-assets.sh` in the same commit.

### 8. Comprehension pass on `kb-delivery`
`condensed_excerpt()`'s docstring claimed it is "derived from the graph, not copy-pasted, so it
cannot drift" — it is a hardcoded Cloudflare Workers paragraph. Behaviour defect recorded; the
docstring correction is a one-line structure fix that can ride along with item 2.

### 9. Module form (deferred — needs a naming migration)
`write-graph.py`, `generate_excerpt.py`, `materialize-grok-skills.py` cannot be imported. Making the
logic an importable package while keeping hyphenated entry points means updating every skill, doc
and `assert-no-drift.sh` reference by name in one commit. Now that `factgraph`/`naming`/`resolve`
carry the logic, the remaining importable surface is smaller than it was.

## Baseline at the last run

All 18 Python gates green (12 original + 6 added). `bun test`: 78 pass / 3 fail
(`real Grok ACP worker lifecycle`, environmental, red before any change). `bun run typecheck` green.

## How a run picks up

`/clean-code-plan repo` with no arguments takes the default scope: files changed since `Last run`
plus the top item. Running the same command again resumes.
