# Known problems — agentic-rosetta

What is wrong today and not yet fixed. Structure-only runs record items here instead of fixing
them, so the next run does not have to re-diagnose.

## Behaviour defects (recorded, not fixed)

| # | Problem | Where |
|---|---|---|
| 1 | `condensed_excerpt()` docstring claims the bootstrap is "derived from the graph, not copy-pasted, so it cannot drift". It is a hardcoded Cloudflare Workers paragraph, and `DEFAULT_DOMAIN` is `cloudflare-workers`. Any non-Cloudflare KB gets Cloudflare advice. | `scripts/kb-delivery/generate_excerpt.py` |
| 2 | `registry.py` turns every parse failure into the string `"unknown"` via `except Exception` (five sites). A malformed graph and a missing graph are indistinguishable in the index. | `scripts/rosetta/registry.py` |
| 3 | `engine` `bun test`: 3 of 81 fail (`real Grok ACP worker lifecycle`, ~13 s each). They spawn a real ACP child; environmental. Red before any cleaning work. | `engine/tests/worker.integration.test.ts` |
| 4 | `pipeline.schema.json` is generated, committed, and read by nobody. `zod-json-schema.ts`'s own comment claims it exists "so write-graph.py can validate without a second hand-maintained copy". It does not, and the schema does not model the fact-graph. | `engine/src/zod-json-schema.ts` |
| 5 | `assets/rosetta_config.py` ships 220 lines of `selftest()` inside the artifact copied to 14 locations. | `assets/rosetta_config.py` |

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
- Module-level mutable root state (`REPO_ROOT`/`REGISTRY_PATH`/`AGENTS_DIR`,
  `write-graph._DOCS_DIR`).

## Not a problem, though it looks like one

The 14 byte-identical copies of `rosetta_config.py` are **deployment artifacts**, not duplicated
knowledge: `install.sh` copies bare `.py` files into harness skill directories, and the plugin never
runs an install step there. `sync-assets.sh --check` is the drift gate that makes them safe. Do not
"fix" this by deleting the copies.
