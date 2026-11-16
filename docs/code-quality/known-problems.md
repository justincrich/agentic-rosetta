# Known problems — agentic-rosetta

What is wrong today and not yet fixed. Structure-only runs record items here instead of fixing
them, so the next run does not have to re-diagnose.

## Behaviour defects (recorded, not fixed)

| # | Problem | Where |
|---|---|---|
| 1 | `condensed_excerpt()` is a hardcoded Cloudflare Workers paragraph (only `v.runtime` and three `r.*_note` values come from the graph) and `DEFAULT_DOMAIN` is `cloudflare-workers`, so any non-Cloudflare KB gets Cloudflare advice. The docstring now says so; the behaviour is still wrong. | `scripts/kb-delivery/generate_excerpt.py` |
| 2 | `registry.py` turns every parse failure into the string `"unknown"` via `except Exception` (five sites). A malformed graph and a missing graph are indistinguishable in the index. | `scripts/rosetta/registry.py` |
| 3 | `engine` `bun test`: 3 of 81 fail (`real Grok ACP worker lifecycle`, ~13 s each). They spawn a real ACP child; environmental. Red before any cleaning work. | `engine/tests/worker.integration.test.ts` |
| 4 | `pipeline.schema.json` is generated, committed, and read by nobody. `zod-json-schema.ts`'s own comment claims it exists "so write-graph.py can validate without a second hand-maintained copy". It does not, and the schema does not model the fact-graph. | `engine/src/zod-json-schema.ts` |
| 5 | `assets/rosetta_config.py` ships 220 lines of `selftest()` inside the artifact copied to 14 locations. | `assets/rosetta_config.py` |
| 6 | `sanitize_url` returns early for any IP literal, so `http://127.0.0.1/x` and other private addresses are accepted as "fetchable" URLs. Hostname checks (`localhost`, `::1`) do run, so the two paths disagree. | `scripts/rosetta/locators.py` |
| 7 | `sanitize_url` strips trailing `)` as punctuation, so a URL whose path genuinely ends in a paren (`.../a(b)`) is silently truncated to `.../a(b`. | `scripts/rosetta/locators.py` |
| 8 | The graph gate coerces a section given as an empty list (`"w": []`) to `{}` instead of rejecting it, because `graph.get("w") or {}` treats `[]` as falsy. A non-empty list is rejected correctly. Preserved during extraction; tightening it is a behaviour change. | `scripts/rosetta/factgraph.py` |
| 9 | Nothing reads `pipeline.schema.json`. It now has proper `$defs` (830 lines, 11 definitions) but is still a generated artifact with no consumer, while `write-graph.py` validates with its own gate. | `scripts/rosetta/schema/pipeline.schema.json` |
| 10 | `write-graph._DOCS_DIR` is module-level mutable state set by `main()`, so six verbs depend on a global instead of a parameter. | `scripts/rosetta/write-graph.py` |

## Fixed in the 2026-11-04 run

- `registry._split_name_role` returned `"specialist"` for an unknown role while
  `generate_excerpt._split_name_role` returned `"implementer"` — one concept, two answers. Unified
  with the difference made explicit.
- `AGENT_PREFIX_TO_SLUG` (3 entries) and `DOCSDOMAIN_BY_PREFIX` (9 entries) mapped the same
  agent→slug relation with different content. Unified into one table.
- `discover_repo_root` existed as two byte-identical copies.
- Semver helpers, canonical-JSON/atomic-write, and the URL/TLD set each had two or three homes;
  the valid-TLD set had **three** inside `provenance.py` alone.
- The fact-graph shape was asserted three ways (58 hand-written checks, ad-hoc `.get()` chains,
  silent `except Exception`). Now one typed model.
- `registry.scan()` parsed each `fact-graph.json` five times.
- `registry`'s three independently-rebound module globals (`REPO_ROOT`, `REGISTRY_PATH`,
  `AGENTS_DIR`) became one frozen `Paths` object with a `repo_root()` accessor.
  `write-graph._DOCS_DIR` is still a global — see problem 10.
- `test-embed-graph.sh` hand-listed which `scripts/rosetta` modules to copy into its fixture, so
  adding a module made the suite fail with `No module named naming` — a fixture bug that reads as
  a regression in the code under test. It now copies every `.py`.

## Not a problem, though it looks like one

The 14 byte-identical copies of `rosetta_config.py` are **deployment artifacts**, not duplicated
knowledge: `install.sh` copies bare `.py` files into harness skill directories, and the plugin never
runs an install step there. `sync-assets.sh --check` is the drift gate that makes them safe. Do not
"fix" this by deleting the copies.

## Recorded by the 2026-11-16 sprint run (structure-only, not fixed)

| # | Problem | Where |
|---|---|---|
| 11 | A corrupt `fact-sources.json` or `provenance.json` is read as "no sources"/"no pins" and then rewritten as that, so the run exits 0 after destroying every source, pin and corpus digest. | `scripts/rosetta/write-graph.py` (`read_sibling_state`) |
| 12 | A corrupt `.rosetta/registry.json` is read as "no manual bindings" and the rebuilt registry is written over it, dropping every binding. | `scripts/rosetta/registry.py` (`load`/`scan`) |
| 13 | `registry` writes its index with a fixed temp name and no fsync while `jsonfile.write_json` exists; two concurrent scans can publish a torn file. | `scripts/rosetta/registry.py` (write) |
| 14 | The markdown render skips every dict-shaped rule, so a graph written by `write-graph` prints "(none marked REJECT)" while its JSON embed carries the rules. | `scripts/kb-delivery/generate_excerpt.py` (`_rules_by_severity`) |
| 15 | `cmd_adopt` does not call `ensure_adopt_fields`; the two have already drifted on the `v.version`-null edge. | `scripts/rosetta/write-graph.py` |
| 16 | `set_repo_root` also writes `ROSETTA_PROJECT`, which outranks `--root` for every later resolution in the process. | `scripts/kb-delivery/generate_excerpt.py` |
| 17 | "Where does this project keep graph X" has two answers: the renderer tries three layouts, the writer one. A composition the renderer accepts, the writer refuses. | `generate_excerpt._original_graph_path` vs `write-graph.graph_path` |
| 18 | `restamp()` and `restamp_composed()` disagree: `--propagate` manufactures an assimilation stamp on a legacy borrower, `--compose` refuses to. | `scripts/rosetta/write-graph.py` |
| 19 | Two verbs at once are accepted and only the first runs; `--list`/`--force` are accepted by verbs that ignore them. | `scripts/rosetta/write-graph.py` (`main` dispatch) |
| 20 | A non-list `_meta.composed_with` raises `TypeError` out of `scan`, where `versions.declared_composed_slugs` returns no declaration. | `scripts/rosetta/registry.py` (`_composed_with`) |
| 21 | The tolerant fact-sources read is still written out in `cmd_update` and `cmd_apply_nrra`; `read_sibling_state` now has it, but folding those two in needs a split of the reader. | `scripts/rosetta/write-graph.py` |
| 22 | A fifth copy of the six-section tuple survives outside the four the sprint fixed. | `scripts/rosetta/provenance.py` |
| 23 | `--propagate`'s help says borrowers are graphs "that declare it", while `find_borrowers` also counts a graph carrying a copy without a declaration. | `scripts/rosetta/write-graph.py` (argparse help) |
| 24 | `--list`'s help says exit 0, but a graph nobody borrows exits 1 before the list branch. | `scripts/rosetta/write-graph.py` (argparse help) |
