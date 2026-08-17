# engine/

Mastra fanout that acquires facts for a Rosetta knowledge base and emits one
NRRA + a closed coverage ledger. This is the **only** research path.

Contribution rules: [`AGENTS.md`](AGENTS.md). Pipeline: [`../ARCHITECTURE.md`](../ARCHITECTURE.md).

## Setup

```bash
cd engine
bun install          # npm install also works
bun test
bun run typecheck
```

Requires Bun (or Node) and an ACP entrypoint for the invoking harness
(`claude-code-acp`, `opencode acp`, `grok agent stdio`, …). If those are
missing, a run returns `status=BLOCKED` and writes nothing.

## Run

`--tech` and `--model` are **required**. `--model` is never defaulted.

```bash
bun run src/research.ts --tech "GPUI" --model grok-4.6 \
  --repo-url https://github.com/zed-industries/zed \
  --base-framework React \
  --out-dir /tmp/gpui-run
```

`--help` (kept in sync with `src/research.ts`):

```
--tech NAME            required
--tech-slug SLUG       default: kebab of --tech
--model MODEL          required; never defaulted
--harness H            claude|codex|opencode|grok (default: detect from env)
--base-framework NAME
--repo-path DIR
--repo-url URL
--docs-url URL
--source-mode repo|docs|both
--topics "a, b"        skip census; fan out these directives
--depth quick|normal|deep
--concurrency N        live workers per wave (default: device limits.concurrency, else 8)
--max-waves N
--max-units N
--docs-max-units N     docs/URL leaf size (default 10; --max-units overrides both)
--max-directives N
--max-usd N            circuit-breaker; omit = uncapped
--census-only          A0–A2 only, spawn nothing
--probe-sources        census + source pins, spawn nothing; write provenance.json
--out-dir DIR          write nrra.md + ledger.json [+ provenance.json]
```

Stdout is JSON: `{ runId, status, nrra, ledger, blockReason, … }`.
`status=BLOCKED` means stop — do not write a graph.

## Layout

| Path | Role |
|---|---|
| `src/contract.ts` | Zod schemas (`ResearchRequest`, `ResearchResult`, `Ledger`, …) |
| `src/research.ts` | CLI |
| `src/index.ts` | `runResearch` |
| `src/resolve-caller.ts` | Detect harness + resolve ACP + validate model |
| `src/worker.ts` | ACP worker construction |
| `src/phases/preflight.ts` | `[A0]` |
| `src/phases/orient.ts` | `[A1]` decoder ring |
| `src/phases/census.ts` | `[A2]` |
| `src/phases/fanout.ts` | `[A3]` |
| `src/phases/ledger.ts` | `[A4]` |
| `src/phases/reconcile.ts` | `[A5]` |
| `src/phases/emit.ts` | `[A6]` NRRA |
| `src/prompts/` | Per-class worker constitution |
| `tests/` | Bun tests |

## Testing

```bash
cd engine
bun test
bun run typecheck
```

`tests/worker.integration.test.ts` exercises a real ACP construction path.
Do not replace that with a mocked transport and still claim the worker works.
