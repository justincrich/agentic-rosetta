# Known problems — agentic-rosetta

What is wrong today and not yet fixed. Structure-only runs record items here instead of fixing
them, so the next run does not have to re-diagnose.

## Behaviour defects (recorded, not fixed)

| # | Problem | Where |
|---|---|---|
| 2 | `registry.py` turns every parse failure into the string `"unknown"` via `except Exception` (five sites). A malformed graph and a missing graph are indistinguishable in the index. | `scripts/rosetta/registry.py` |
| 3 | `engine` `bun test`: 3 of 81 fail (`real Grok ACP worker lifecycle`, ~13 s each). They spawn a real ACP child; environmental. Red before any cleaning work. | `engine/tests/worker.integration.test.ts` |
| 4 | `pipeline.schema.json` is generated, committed, and read by nobody. `zod-json-schema.ts`'s own comment claims it exists "so write-graph.py can validate without a second hand-maintained copy". It does not, and the schema does not model the fact-graph. | `engine/src/zod-json-schema.ts` |
| 5 | `assets/rosetta_config.py` ships 220 lines of `selftest()` inside the artifact copied to 14 locations. | `assets/rosetta_config.py` |
| 6 | `sanitize_url` returns early for any IP literal, so `http://127.0.0.1/x` and other private addresses are accepted as "fetchable" URLs. Hostname checks (`localhost`, `::1`) do run, so the two paths disagree. | `scripts/rosetta/locators.py` |
| 7 | `sanitize_url` strips trailing `)` as punctuation, so a URL whose path genuinely ends in a paren (`.../a(b)`) is silently truncated to `.../a(b`. | `scripts/rosetta/locators.py` |
| 8 | The graph gate coerces a section given as an empty list (`"w": []`) to `{}` instead of rejecting it, because `graph.get("w") or {}` treats `[]` as falsy. A non-empty list is rejected correctly. Preserved during extraction; tightening it is a behaviour change. | `scripts/rosetta/factgraph.py` |
| 9 | Nothing reads `pipeline.schema.json`. It now has proper `$defs` (830 lines, 11 definitions) but is still a generated artifact with no consumer, while `write-graph.py` validates with its own gate. | `scripts/rosetta/schema/pipeline.schema.json` |
| 10 | `write-graph._DOCS_DIR` is module-level mutable state set by `main()`, so six verbs depend on a global instead of a parameter. | `scripts/rosetta/write-graph.py` |

## Fixed in the 2026-11-16 run (TECHFREE-01)

- `condensed_excerpt()` was a hardcoded paragraph of one platform's advice (runtime, entry
  points, storage rules, a config-file name) and `generate_excerpt.py` carried a built-in default
  domain. Every line now derives from a graph field, and `--domain` is required when neither
  `--agent` nor `--graph` names a graph.
- Technology data left the machinery: the agent-prefix→graph-slug table and the legacy
  docs-domain table are gone from `naming.py`, and a project declares its own name mismatches in
  the config's `agent_aliases` (validated as slugs, refused as paths). The resolver also stopped
  carrying a technology label per domain.
- `scripts/testdata/graphs` held copies of real product KBs. They are now neutral fixtures
  produced by `write-graph.py --create` from the committed NRRAs in `scripts/testdata/nrra/` and
  the shared ledger, and `test_write_graph.py` regenerates every one and compares the bytes.

## Fixed in the 2026-11-04 run

- `registry._split_name_role` returned `"specialist"` for an unknown role while
  `generate_excerpt._split_name_role` returned `"implementer"` — one concept, two answers. Unified
  with the difference made explicit.
- `AGENT_PREFIX_TO_SLUG` (3 entries) and `DOCSDOMAIN_BY_PREFIX` (9 entries) mapped the same
  agent→slug relation with different content. Unified into one table — and, in the 2026-11-16
  run, moved out of the code entirely into the project's `agent_aliases` config.
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
