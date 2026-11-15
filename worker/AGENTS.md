# worker/ — read-only MCP service for a published Rosetta snapshot

This Worker serves agent prompts and knowledge-base graphs over MCP so a machine
no longer needs a brain checkout. It is read-only: no Durable Object, no
session, no write tool, no OAuth.

`AGENTS.md` at the repo root still governs the machinery; this file covers the
Worker only.

## Layout

| Path | Role |
|---|---|
| `src/index.ts` | Request entry: `/.well-known/*` → 404, bearer check, `/mcp`, asset reads |
| `src/tools.ts` | Server factory + the four tools; `readAsset` is the only read seam |
| `test/service.test.ts` | Integration tests; each one spawns the real `wrangler dev --local` |
| `wrangler.jsonc` | Static Assets binding, `run_worker_first`, `nodejs_compat` |
| `content/` | Staged snapshot (gitignored in practice: staged by the publisher, never committed) |

## Request flow

1. `/.well-known/*` → **404**. The service has no OAuth discovery surface, so no
   client is pointed at an authorization server that does not exist.
2. **The Worker authenticates the bearer itself**, before the MCP handler runs:
   SHA-256 of both sides, compared with `crypto.subtle.timingSafeEqual`. Two
   slots are accepted, `ROSETTA_MCP_TOKEN` (current) and `ROSETTA_MCP_TOKEN_NEXT`
   (rotation). An unset or empty slot is not a credential, so `'Bearer undefined'`
   never matches. No secret configured at all → **503**. Everything else that is
   not a valid bearer → **401** with `WWW-Authenticate: Bearer realm="rosetta-mcp"`.
3. `/mcp` → a handler built **per request** from the `createRosettaServer`
   factory, with `route: '/mcp'`, `legacy: 'stateless'`, `corsOptions: false`
   and `authContext: { props: { tokenSlot } }`. The factory reads the slot with
   `getMcpAuthContext()` and logs only the label.
4. `/manifest.json` and `/files/<snapshot path>` → the same check, then
   `readAsset`. `run_worker_first: true` is what keeps a static asset from being
   served ahead of the token check.

`export default { fetch }` is an **object**, never the callable handler:
Wrangler treats a callable default export as a `WorkerEntrypoint` class.

## Tools

`list_agents`, `get_agent`, `list_kbs`, `get_kb` — all `readOnlyHint: true`, all
answered from `manifest.json` and the snapshot assets. `get_kb` returns the
section names plus the graph, or one section when `section` is passed.

## Local development

```bash
cd worker
bun install
printf 'ROSETTA_MCP_TOKEN=dev-token\n' > .dev.vars   # never commit
# stage a snapshot into content/ (publish_snapshot.py in the publisher task),
# or run the tests, which stage a minimal fixture and remove it again
bun test
bunx wrangler dev --local --port 8787
```

`.dev.vars` and `content/` are runtime state. Do not commit either. The test
suite rewrites `.dev.vars` with its own throwaway tokens, restores whatever was
there, and removes the snapshot it staged.

## Gates

`bun test` is the gate; there is no repo-level lint for this directory.

```bash
cd worker && bun test                                  # spawns wrangler dev per test
bunx tsc --noEmit -p tsconfig.json                     # optional typecheck of src/
```

Repo-level gates (`./sync-assets.sh --check`, `scripts/assert-no-drift.sh
--scripts-only`) are unaffected by this directory.

## Deploy

```bash
cd worker
bunx wrangler secret put ROSETTA_MCP_TOKEN       # and ROSETTA_MCP_TOKEN_NEXT during a rotation
bunx wrangler deploy
bunx wrangler tail                               # one JSON line per tool call, with tokenSlot
```

A code-only deploy needs `content/` present: the snapshot and the code travel as
one Worker version, so publish and deploy together. `wrangler rollback` restores
the previous snapshot.

## Non-negotiables

- No `McpAgent`, no Durable Object, no `durable_objects` / `migrations` block.
- No module-scope `McpServer`: the factory creates one per request.
- No `legacy: 'reject'` until every harness negotiates the 2026-07-28 protocol.
- `corsOptions: false`; `allowedOriginHostnames` is never `'*'`.
- Never log a token. The log line carries `tool`, `clientId`, `scopes` and
  `tokenSlot` only.
