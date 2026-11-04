# engine/ — directory system prompt

You are in the **fanout engine**. This tree is TypeScript on Bun. It is the
only research path. Root contract: [`../AGENTS.md`](../AGENTS.md). How to run:
[`README.md`](README.md). Pipeline map: [`../ARCHITECTURE.md`](../ARCHITECTURE.md).

Do **not** edit `skills/` or `grok-skills/` from here. Do **not** write
`fact-graph.json` — that is `scripts/rosetta/write-graph.py`.

## What this tree owns

- `src/contract.ts` — Zod wire types. Change the schema here first.
- `src/research.ts` — CLI. `src/index.ts` — `runResearch`.
- `src/phases/` — `[A0]`–`[A6]`. Keep one concern per file.
- `src/prompts/` — worker constitution, one file per territory class.
- `src/worker.ts` / `src/resolve-caller.ts` — ACP inheritance of harness + model.
- `src/json-schema-defs.ts` — emits the JSON Schema bundle with `$defs`/`$ref`.
- `tests/` — Bun tests against real fixtures and (for integration) a real ACP path.

**`scripts/rosetta/schema/pipeline.schema.json` is GENERATED.** Do not hand-edit it. It comes
from `bun run contract:json`, which converts `contract.ts` through `json-schema-defs.ts` — that
module exists because converting one schema at a time inlined every shared object, which made a
~250-line contract into a 1723-line file. If you add a shared shape, add it to `contract.ts`
only; the `$defs` pass dedupes it by structural equality automatically.

## Rules

1. **Fail closed.** `preflight.ts` returns a named code
   (`RECURSION`, `HARNESS_UNRESOLVED`, `MODEL_MISSING`, `ACP_MISSING`,
   `RUNTIME`). Do not add a “best effort” branch that crawls the web from this
   process.
2. **No serial-crawl fallback.** If ACP cannot start, `status=BLOCKED`. The
   skill prints the reason and writes nothing.
3. **Never default `--model`.** `research.ts` exits 2 if `--tech` or `--model`
   is missing. Model strings are pass-through: `validateModel` rejects only an
   EMPTY model. Unknown model names reach the ACP binary, which surfaces
   provider / key errors the worker classifies into `AUTH` / `MODEL` / `QUOTA`
   (see `classifyWorkerError` in `src/worker.ts`).

   `--provider` is optional and never defaulted. For open-source harnesses
   (`pi`) it composes as `provider/model`; model-provided harnesses
   (claude / codex / opencode / grok) ignore it. `pi` is the fifth harness —
   its ACP server is the separate `pi-acp` npm adapter (spawns `pi --mode rpc`),
   installed globally on the machine running the engine.
4. **No recursive fanout.** `ROSETTA_WORKER=1` (`WORKER_DEPTH_ENV`) inside a
   worker. Preflight STOPs if it is set.
5. **Census is deterministic.** `git ls-files` / docs crawl, first-match class
   table, logged overflow merges. Defaults: `maxUnits=40` (repo),
   `docsMaxUnits=10`, `maxBytes=250_000`. Do not silently drop leftover units.
6. **Prompts stay short and class-specific.**
   `orientation | reference | enforcement | scars | contracts | exemplars`.
   `YIELDS_BY_CLASS` is the only map from class → graph sections.
7. **Tests that claim the worker path works do not mock ACP.**
   `tests/worker.integration.test.ts` is the pattern. Pure schema / hash /
   census tests belong in the other files and must stay I/O-honest (real temp
   dirs, real `git ls-files` trees).
8. **Do not hardcode a consumer repo.** `--repo-path` / `--repo-url` /
   `--docs-url` are inputs. The engine does not know about brain.

## Verify

```bash
cd engine
bun test
bun run typecheck
```

`--help` on `src/research.ts` must keep listing `--tech` and `--model` as
required. If you change the CLI, update [`README.md`](README.md) in the same
commit.
