/**
 * Integration tests for the read-only Rosetta MCP Worker.
 *
 * Every behavioural assertion here runs against the REAL Worker in workerd:
 * the suite spawns `wrangler dev --local` and drives it over HTTP, including
 * with the official `@modelcontextprotocol/client` 2.4.0 SDK. Nothing is
 * mocked: the token check, the asset reads and the MCP handler are the ones
 * that ship.
 *
 * The suite stages a minimal snapshot into `worker/content/` when the publisher
 * has not staged one, writes the token into `worker/.dev.vars`, and removes
 * both again when it created them.
 */
import { afterAll, describe, expect, it, setDefaultTimeout } from 'bun:test';
import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { join } from 'node:path';
import { Client, StreamableHTTPClientTransport } from '@modelcontextprotocol/client';
import worker from '../src/index';

// A cold `wrangler dev` bundles the Worker before it serves the first request;
// 5s (the bun default) is not enough on a machine with an empty cache.
setDefaultTimeout(120_000);

const WORKER_DIR = join(import.meta.dir, '..');
const CONTENT_DIR = join(WORKER_DIR, 'content');
const DEV_VARS_PATH = join(WORKER_DIR, '.dev.vars');
const WRANGLER_BIN = join(WORKER_DIR, 'node_modules', '.bin', 'wrangler');
const START_TIMEOUT_MS = 90_000;

const TOKEN_CURRENT = 'svc01-current-2f7c9a41';
const TOKEN_NEXT = 'svc01-next-b3d05e18';
const TOKEN_THIRD = 'svc01-unconfigured-9a6b47d2';
const CLIENT_ID = 'static-token';
const MCP_SCOPE = 'mcp:read';

// ---------------------------------------------------------------------------
// Fixture staging: a minimal published snapshot.
// ---------------------------------------------------------------------------

const FIXTURE_AGENTS = [
  { name: 'alpha', description: 'Alpha agent', model: 'sonnet', body: '---\nname: alpha\n---\nAlpha prompt body.\n' },
  { name: 'beta', description: 'Beta agent', model: 'opus', body: '---\nname: beta\n---\nBeta prompt body.\n' },
];

const FIXTURE_KB = { slug: 'demo', kb_version: '1.0.0', graph: { version: '1.0.0', api: { run: 'x()' }, gotchas: { note: 'y' } } };

let stagedContent = false;

/**
 * The developer's `.dev.vars`, captured before any test writes one. Read at
 * module load so a test that starts two servers (AC-8 uses a token-less one)
 * cannot snapshot its own file as if it were the developer's.
 */
const ORIGINAL_DEV_VARS: string | null = existsSync(DEV_VARS_PATH) ? readFileSync(DEV_VARS_PATH, 'utf8') : null;

function stageSnapshot(): void {
  if (existsSync(join(CONTENT_DIR, 'manifest.json'))) return;
  stagedContent = true;
  for (const agent of FIXTURE_AGENTS) {
    const dir = join(CONTENT_DIR, 'targets', 'claude', agent.name);
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, 'agent.md'), agent.body);
  }
  const kbDir = join(CONTENT_DIR, 'kb', FIXTURE_KB.slug);
  mkdirSync(kbDir, { recursive: true });
  const graphText = JSON.stringify(FIXTURE_KB.graph);
  writeFileSync(join(kbDir, 'fact-graph.json'), graphText);
  writeFileSync(
    join(CONTENT_DIR, 'manifest.json'),
    `${JSON.stringify(
      {
        schema: 1,
        snapshot_id: 'svc01fixture0',
        published_at: '2026-11-15T00:00:00.000Z',
        consumer_ref: null,
        plugin_version: '0.4.6',
        targets: { claude: { dest: '.claude/agents', files: FIXTURE_AGENTS.length } },
        agents: FIXTURE_AGENTS.map((agent) => ({
          name: agent.name,
          description: agent.description,
          model: agent.model,
          bytes: agent.body.length,
          path: `targets/claude/${agent.name}/agent.md`,
          target: 'claude',
        })),
        kbs: [{ slug: FIXTURE_KB.slug, kb_version: FIXTURE_KB.kb_version, bytes: graphText.length, path: `kb/${FIXTURE_KB.slug}/fact-graph.json` }],
        excluded: {},
      },
      null,
      2,
    )}\n`,
  );
}

// ---------------------------------------------------------------------------
// Dev server harness.
// ---------------------------------------------------------------------------

interface DevWorker {
  url: string;
  /** Everything the dev server printed, worker console.log included. */
  log(): string;
  stop(): Promise<void>;
}

async function freePort(): Promise<number> {
  return await new Promise<number>((resolve, reject) => {
    const probe = createServer();
    probe.once('error', reject);
    probe.listen(0, '127.0.0.1', () => {
      const address = probe.address();
      if (address === null || typeof address === 'string') {
        probe.close();
        reject(new Error('could not allocate a port'));
        return;
      }
      const { port } = address;
      probe.close(() => resolve(port));
    });
  });
}

function writeDevVars(vars: Record<string, string>): void {
  const body = Object.entries(vars)
    .map(([name, value]) => `${name}=${value}`)
    .join('\n');
  writeFileSync(DEV_VARS_PATH, `${body}\n`);
}

async function startDevWorker(vars: Record<string, string>): Promise<DevWorker> {
  stageSnapshot();
  writeDevVars(vars);
  const port = await freePort();
  const child: ChildProcess = spawn(WRANGLER_BIN, ['dev', '--local', '--ip', '127.0.0.1', '--port', String(port)], {
    cwd: WORKER_DIR,
    env: { ...process.env, CI: '1', NO_COLOR: '1', FORCE_COLOR: '0', WRANGLER_SEND_METRICS: 'false' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const chunks: string[] = [];
  const collect = (data: Buffer) => {
    chunks.push(data.toString('utf8'));
  };
  child.stdout?.on('data', collect);
  child.stderr?.on('data', collect);

  const stopped = new Promise<void>((resolve) => {
    child.once('exit', () => resolve());
    child.once('error', () => resolve());
  });

  const log = () => chunks.join('');
  const deadline = Date.now() + START_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (log().includes(`Ready on http://127.0.0.1:${port}`)) break;
    if (child.exitCode !== null) throw new Error(`wrangler dev exited before it was ready.\n${log()}`);
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  if (!log().includes(`Ready on http://127.0.0.1:${port}`)) {
    child.kill('SIGKILL');
    throw new Error(`wrangler dev was not ready within ${START_TIMEOUT_MS}ms.\n${log()}`);
  }
  console.log(`[service] ${WRANGLER_BIN} dev --local --ip 127.0.0.1 --port ${port} :: Ready on http://127.0.0.1:${port}`);

  return {
    url: `http://127.0.0.1:${port}`,
    log,
    async stop() {
      if (child.exitCode === null) child.kill('SIGTERM');
      const timer = setTimeout(() => {
        if (child.exitCode === null) child.kill('SIGKILL');
      }, 10_000);
      await stopped;
      clearTimeout(timer);
    },
  };
}

/**
 * One dev server at a time. Bun runs the tests in a file sequentially, and a
 * server is restarted only when a test needs a different token configuration.
 */
let activeWorker: { key: string; worker: DevWorker } | null = null;

async function devWorkerWith(config: 'current-only' | 'both-slots' | 'no-token'): Promise<DevWorker> {
  if (activeWorker?.key === config) return activeWorker.worker;
  if (activeWorker !== null) {
    await activeWorker.worker.stop();
    activeWorker = null;
  }
  const vars =
    config === 'current-only'
      ? { ROSETTA_MCP_TOKEN: TOKEN_CURRENT }
      : config === 'both-slots'
        ? { ROSETTA_MCP_TOKEN: TOKEN_CURRENT, ROSETTA_MCP_TOKEN_NEXT: TOKEN_NEXT }
        : {};
  const worker = await startDevWorker(vars);
  activeWorker = { key: config, worker };
  return worker;
}

afterAll(async () => {
  if (activeWorker !== null) {
    await activeWorker.worker.stop();
    activeWorker = null;
  }
  if (ORIGINAL_DEV_VARS !== null) writeFileSync(DEV_VARS_PATH, ORIGINAL_DEV_VARS);
  else if (existsSync(DEV_VARS_PATH)) rmSync(DEV_VARS_PATH);
  if (stagedContent) rmSync(CONTENT_DIR, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// Request helpers.
// ---------------------------------------------------------------------------

const ACCEPT_BOTH = 'application/json, text/event-stream';

function initializeBody(protocolVersion = '2025-06-18'): Record<string, unknown> {
  return {
    jsonrpc: '2.0',
    id: 1,
    method: 'initialize',
    params: { protocolVersion, capabilities: {}, clientInfo: { name: 'rosetta-service-test', version: '0.0.0' } },
  };
}

async function postMcp(url: string, headers: Record<string, string>, body: unknown): Promise<Response> {
  return fetch(`${url}/mcp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: ACCEPT_BOTH, ...headers },
    body: JSON.stringify(body),
  });
}

function bearer(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

async function withSdkClient<T>(url: string, token: string, run: (client: Client) => Promise<T>): Promise<T> {
  const transport = new StreamableHTTPClientTransport(new URL(`${url}/mcp`), {
    requestInit: { headers: { Authorization: `Bearer ${token}` } },
  });
  const client = new Client({ name: 'rosetta-service-test', version: '0.0.0' });
  await client.connect(transport);
  try {
    return await run(client);
  } finally {
    await client.close();
  }
}

/** Tool names in the order `tools/list` reports them. */
async function toolNames(url: string, token: string): Promise<string[]> {
  return await withSdkClient(url, token, async (client) => {
    const { tools } = await client.listTools();
    return tools.map((tool) => tool.name);
  });
}

/** One `tools/call` over raw HTTP, so the call shows in the dev server log. */
async function callTool(url: string, token: string, name: string, args: Record<string, unknown>): Promise<Response> {
  return await postMcp(url, bearer(token), { jsonrpc: '2.0', id: 91, method: 'tools/call', params: { name, arguments: args } });
}

function source(file: string): string {
  return readFileSync(join(WORKER_DIR, file), 'utf8');
}

const INDEX_SRC = () => source('src/index.ts');
const TOOLS_SRC = () => source('src/tools.ts');
const WRANGLER_CONFIG = () => source('wrangler.jsonc');

/** The manifest the running Worker is actually serving. */
function currentManifest(): {
  agents: Array<{ name: string; path: string }>;
  kbs: Array<{ slug: string; bytes?: number; path: string }>;
} {
  return JSON.parse(readFileSync(join(CONTENT_DIR, 'manifest.json'), 'utf8')) as {
    agents: Array<{ name: string; path: string }>;
    kbs: Array<{ slug: string; bytes?: number; path: string }>;
  };
}

// ---------------------------------------------------------------------------
// AC-2 — fail closed on the optional second slot.
// ---------------------------------------------------------------------------

describe('SERVICE-01 read-only MCP Worker', () => {
  it('AC-2 rejects "Bearer undefined" and an empty bearer when ROSETTA_MCP_TOKEN_NEXT is unset', async () => {
    const dev = await devWorkerWith('current-only');

    const undefinedBearer = await postMcp(dev.url, bearer('undefined'), initializeBody());
    expect(undefinedBearer.status).toBe(401);
    expect(undefinedBearer.headers.get('www-authenticate')).toBe('Bearer realm="rosetta-mcp"');

    const emptyBearer = await postMcp(dev.url, { Authorization: 'Bearer ' }, initializeBody());
    expect(emptyBearer.status).toBe(401);
    expect(emptyBearer.headers.get('www-authenticate')).toBe('Bearer realm="rosetta-mcp"');

    // The rejections are specific, not a blanket failure: the configured token works.
    const configured = await postMcp(dev.url, bearer(TOKEN_CURRENT), initializeBody());
    expect(configured.status).toBe(200);
  });

  // -------------------------------------------------------------------------
  // AC-6 — wired through a per-request factory, four read-only tools.
  // -------------------------------------------------------------------------

  it('AC-6 exports an object with fetch, builds the handler per request, and lists the four read-only tools over the SDK', async () => {
    // The default export must be an object with `fetch`: Wrangler treats a
    // callable default export as a WorkerEntrypoint class.
    expect(typeof worker).toBe('object');
    expect(worker).not.toBeNull();
    expect(typeof worker.fetch).toBe('function');

    const dev = await devWorkerWith('current-only');
    const names = await toolNames(dev.url, TOKEN_CURRENT);
    console.log(`[service] SDK 2.4.0 client tools/list over workerd: ${JSON.stringify(names)}`);
    expect(names.sort()).toEqual(['get_agent', 'get_kb', 'list_agents', 'list_kbs']);

    await withSdkClient(dev.url, TOKEN_CURRENT, async (client) => {
      const { tools } = await client.listTools();
      expect(tools.length).toBe(4);
      for (const tool of tools) {
        expect(tool.annotations?.readOnlyHint).toBe(true);
      }

      // The listed tools are wired to the real snapshot behind ASSETS, not
      // just registered: each read travels through the Static Assets binding.
      const manifest = currentManifest();
      const agent = manifest.agents[0];
      const kb = [...manifest.kbs].sort((left, right) => (left.bytes ?? 0) - (right.bytes ?? 0))[0];

      const listedAgents = await client.callTool({ name: 'list_agents', arguments: {} });
      expect(listedAgents.content[0]).toMatchObject({ type: 'text' });
      expect((listedAgents.content[0] as { text: string }).text).toContain(agent.name);

      const fetchedAgent = await client.callTool({ name: 'get_agent', arguments: { name: agent.name } });
      const agentPayload = JSON.parse((fetchedAgent.content[0] as { text: string }).text) as { name: string; content: string; path: string };
      expect(agentPayload.name).toBe(agent.name);
      expect(agentPayload.path).toBe(agent.path);
      expect(agentPayload.content.length).toBeGreaterThan(0);

      const listedKbs = await client.callTool({ name: 'list_kbs', arguments: {} });
      expect((listedKbs.content[0] as { text: string }).text).toContain(kb.slug);

      const fetchedKb = await client.callTool({ name: 'get_kb', arguments: { slug: kb.slug } });
      const kbPayload = JSON.parse((fetchedKb.content[0] as { text: string }).text) as { slug: string; sections: string[]; content: unknown };
      expect(kbPayload.slug).toBe(kb.slug);
      expect(Array.isArray(kbPayload.sections)).toBe(true);
      expect(kbPayload.sections.length).toBeGreaterThan(0);
    });

    // Documented handler options, and no module-scope server.
    const index = INDEX_SRC();
    expect(index).toContain("route: '/mcp'");
    expect(index).toContain("legacy: 'stateless'");
    expect(index).toContain('corsOptions: false');
    expect(index).toContain('createMcpHandler(createRosettaServer(env)');
    const handlerCall = index.indexOf('createMcpHandler(');
    const fetchEntry = index.indexOf('async fetch(');
    expect(fetchEntry).toBeGreaterThan(-1);
    expect(handlerCall).toBeGreaterThan(fetchEntry);
    for (const file of ['src/index.ts', 'src/tools.ts']) {
      const text = source(file);
      expect(text).not.toContain('McpAgent');
      expect(text).not.toMatch(/^(?:export\s+)?(?:const|let|var)\s+\w+\s*(?::[^=\n]+)?=\s*new\s+McpServer\b/m);
    }

    // The route option is real: a sibling path is not the MCP endpoint.
    const sibling = await postMcp(dev.url, bearer(TOKEN_CURRENT), initializeBody());
    expect(sibling.status).toBe(200);
    const offRoute = await fetch(`${dev.url}/mcp/other`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: ACCEPT_BOTH, ...bearer(TOKEN_CURRENT) },
      body: JSON.stringify(initializeBody()),
    });
    expect(offRoute.status).toBe(404);
  });

  // -------------------------------------------------------------------------
  // AC-7 — pinned peers, no unmet peer warnings.
  // -------------------------------------------------------------------------

  it('AC-7 pins agents 0.33.0 with MCP server and client 2.4.0 exactly, zod ^4, and a pinned wrangler', async () => {
    const install = spawnSync('bun', ['install'], { cwd: WORKER_DIR, encoding: 'utf8', env: { ...process.env, CI: '1' } });
    const installOutput = `${install.stdout ?? ''}${install.stderr ?? ''}`;
    expect(install.status).toBe(0);
    expect(installOutput).not.toMatch(/peer dependenc|unmet peer|peer warning/i);

    const manifest = JSON.parse(readFileSync(join(WORKER_DIR, 'package.json'), 'utf8')) as {
      dependencies: Record<string, string>;
      devDependencies: Record<string, string>;
    };
    expect(manifest.dependencies.agents).toBe('0.33.0');
    expect(manifest.dependencies['@modelcontextprotocol/server']).toBe('2.4.0');
    expect(manifest.devDependencies['@modelcontextprotocol/client']).toBe('2.4.0');
    expect(manifest.dependencies.zod).toMatch(/^\^4/);
    expect(manifest.devDependencies.wrangler).toMatch(/^\d+\.\d+\.\d+$/);

    // The installed tree matches what the peers require.
    const installed = (path: string) =>
      (JSON.parse(readFileSync(join(WORKER_DIR, 'node_modules', path, 'package.json'), 'utf8')) as { version: string }).version;
    expect(installed('agents')).toBe('0.33.0');
    expect(installed('@modelcontextprotocol/server')).toBe('2.4.0');
    expect(installed('@modelcontextprotocol/client')).toBe('2.4.0');
    expect(installed('zod')).toMatch(/^4\./);

    // Those exact dependencies boot the Worker in workerd.
    const dev = await devWorkerWith('current-only');
    const anonymous = await postMcp(dev.url, {}, initializeBody());
    expect(anonymous.status).toBe(401);
  });

  // -------------------------------------------------------------------------
  // AC-8 — the Worker authenticates the bearer before the handler.
  // -------------------------------------------------------------------------

  it('AC-8 rejects a missing, wrong, query-string or undefined token and hands a valid one to the handler as static-token', async () => {
    const dev = await devWorkerWith('current-only');

    const missing = await postMcp(dev.url, {}, initializeBody());
    expect(missing.status).toBe(401);
    expect(missing.headers.get('www-authenticate')).toBe('Bearer realm="rosetta-mcp"');

    const wrong = await postMcp(dev.url, bearer(TOKEN_THIRD), initializeBody());
    expect(wrong.status).toBe(401);
    expect(wrong.headers.get('www-authenticate')).toBe('Bearer realm="rosetta-mcp"');

    const inQuery = await fetch(`${dev.url}/mcp?token=${encodeURIComponent(TOKEN_CURRENT)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: ACCEPT_BOTH },
      body: JSON.stringify(initializeBody()),
    });
    expect(inQuery.status).toBe(401);
    expect(inQuery.headers.get('www-authenticate')).toBe('Bearer realm="rosetta-mcp"');

    const valid = await postMcp(dev.url, bearer(TOKEN_CURRENT), initializeBody());
    expect(valid.status).toBe(200);
    expect(await valid.text()).toContain('rosetta-kb');

    // An asset path is behind the same check, so Static Assets never bypass it.
    const assetWithoutToken = await fetch(`${dev.url}/manifest.json`);
    expect(assetWithoutToken.status).toBe(401);
    expect(assetWithoutToken.headers.get('www-authenticate')).toBe('Bearer realm="rosetta-mcp"');
    const assetWithToken = await fetch(`${dev.url}/manifest.json`, { headers: bearer(TOKEN_CURRENT) });
    expect(assetWithToken.status).toBe(200);

    // No OAuth discovery surface at all.
    const wellKnown = await fetch(`${dev.url}/.well-known/oauth-protected-resource`);
    expect(wellKnown.status).toBe(404);
    expect(wellKnown.headers.get('www-authenticate')).toBeNull();

    // The handler receives the verified principal, visible in the tool-call log.
    const called = await callTool(dev.url, TOKEN_CURRENT, 'list_agents', {});
    expect(called.status).toBe(200);
    await new Promise((resolve) => setTimeout(resolve, 300));
    const log = dev.log();
    const callLine = log
      .split('\n')
      .find((line) => line.includes('mcp.tool_call') && line.includes('"list_agents"'));
    expect(callLine).toBeDefined();
    const parsed = JSON.parse(callLine as string) as { clientId: string; scopes: string[]; tokenSlot: string; tool: string };
    console.log(`[service] wrangler dev console (workerd): ${(callLine as string).trim()}`);
    expect(parsed.clientId).toBe(CLIENT_ID);
    expect(parsed.scopes).toEqual([MCP_SCOPE]);
    expect(parsed.tokenSlot).toBe('current');
    expect(log).not.toContain(TOKEN_CURRENT);
    expect(log).not.toContain(TOKEN_THIRD);

    // With no secret on the Worker at all, the service fails closed with 503.
    const unconfigured = await devWorkerWith('no-token');
    const noSecret = await postMcp(unconfigured.url, bearer('undefined'), initializeBody());
    expect(noSecret.status).toBe(503);
    expect(noSecret.headers.get('www-authenticate')).toBe('Bearer realm="rosetta-mcp"');
    const noSecretUnset = await postMcp(unconfigured.url, {}, initializeBody());
    expect(noSecretUnset.status).toBe(503);
  });

  // -------------------------------------------------------------------------
  // AC-9 — the legacy stateless lane serves both a raw 2025-06-18 client and
  // the SDK 2.4.0 client.
  // -------------------------------------------------------------------------

  it('AC-9 serves a raw 2025-06-18 initialize and the SDK 2.4.0 client in the legacy stateless lane', async () => {
    const dev = await devWorkerWith('current-only');

    const raw = await postMcp(dev.url, bearer(TOKEN_CURRENT), initializeBody('2025-06-18'));
    expect(raw.status).toBe(200);
    const rawText = await raw.text();
    expect(rawText).not.toContain('-32022');
    expect(rawText).toContain('"serverInfo"');
    expect(rawText).toContain('rosetta-kb');
    expect(rawText).toContain('2025-06-18');

    const names = await toolNames(dev.url, TOKEN_CURRENT);
    expect(names.length).toBe(4);

    expect(INDEX_SRC()).toContain("legacy: 'stateless'");
  });

  // -------------------------------------------------------------------------
  // AC-10 — no session state anywhere.
  // -------------------------------------------------------------------------

  it('AC-10 answers GET and DELETE with 405, never issues mcp-session-id, and carries no Durable Object', async () => {
    const dev = await devWorkerWith('current-only');

    const get = await fetch(`${dev.url}/mcp`, { method: 'GET', headers: { Accept: 'text/event-stream', ...bearer(TOKEN_CURRENT) } });
    expect(get.status).toBe(405);
    expect(get.headers.get('mcp-session-id')).toBeNull();

    const del = await fetch(`${dev.url}/mcp`, { method: 'DELETE', headers: bearer(TOKEN_CURRENT) });
    expect(del.status).toBe(405);
    expect(del.headers.get('mcp-session-id')).toBeNull();

    const initialize = await postMcp(dev.url, bearer(TOKEN_CURRENT), initializeBody());
    expect(initialize.status).toBe(200);
    expect(initialize.headers.get('mcp-session-id')).toBeNull();

    const listed = await withSdkClient(dev.url, TOKEN_CURRENT, async (client) => {
      const { tools } = await client.listTools();
      return tools.length;
    });
    expect(listed).toBe(4);

    const config = WRANGLER_CONFIG();
    expect(config).not.toContain('durable_objects');
    expect(config).not.toContain('"migrations"');
    expect(config).not.toContain('migrations');
    for (const file of ['src/index.ts', 'src/tools.ts']) {
      expect(source(file)).not.toContain('McpAgent');
    }
    expect(TOOLS_SRC()).not.toMatch(/^new\s+McpServer\b/m);
  });

  // -------------------------------------------------------------------------
  // AC-11 — Origin allowlist and no CORS surface.
  // -------------------------------------------------------------------------

  it('AC-11 rejects a foreign or null Origin, admits localhost and Origin-less callers, and adds no CORS headers', async () => {
    const dev = await devWorkerWith('current-only');

    const foreign = await postMcp(dev.url, { ...bearer(TOKEN_CURRENT), Origin: 'https://evil.example' }, initializeBody());
    expect(foreign.status).toBe(403);

    const nullOrigin = await postMcp(dev.url, { ...bearer(TOKEN_CURRENT), Origin: 'null' }, initializeBody());
    expect(nullOrigin.status).toBe(403);

    const localhost = await postMcp(dev.url, { ...bearer(TOKEN_CURRENT), Origin: 'http://localhost:3000' }, initializeBody());
    expect(localhost.status).toBe(200);

    const noOrigin = await postMcp(dev.url, bearer(TOKEN_CURRENT), initializeBody());
    expect(noOrigin.status).toBe(200);

    for (const response of [foreign, nullOrigin, localhost, noOrigin]) {
      for (const [name] of response.headers) {
        expect(name.toLowerCase().startsWith('access-control-')).toBe(false);
      }
    }

    for (const text of [INDEX_SRC(), WRANGLER_CONFIG()]) {
      expect(text).not.toMatch(/allowedOriginHostnames\s*:\s*\[\s*['"]\*['"]/);
      expect(text).not.toMatch(/allowedOriginHostnames\s*:\s*['"]\*['"]/);
    }
  });

  // -------------------------------------------------------------------------
  // AC-22 — two token slots, one refusenik, the slot in the log.
  // -------------------------------------------------------------------------

  it('AC-22 accepts the current and next tokens, rejects a third, and logs only the slot label', async () => {
    const dev = await devWorkerWith('both-slots');

    const currentNames = await toolNames(dev.url, TOKEN_CURRENT);
    expect(currentNames.length).toBe(4);
    const nextNames = await toolNames(dev.url, TOKEN_NEXT);
    expect(nextNames.length).toBe(4);

    const third = await postMcp(dev.url, bearer(TOKEN_THIRD), initializeBody());
    expect(third.status).toBe(401);

    // One tool call per slot, so each slot's label is observable.
    expect((await callTool(dev.url, TOKEN_CURRENT, 'list_kbs', {})).status).toBe(200);
    expect((await callTool(dev.url, TOKEN_NEXT, 'list_kbs', {})).status).toBe(200);
    await new Promise((resolve) => setTimeout(resolve, 300));

    const log = dev.log();
    const callLines = log
      .split('\n')
      .filter((line) => line.includes('mcp.tool_call') && line.includes('"list_kbs"'))
      .map((line) => JSON.parse(line) as { tokenSlot: string; clientId: string });
    expect(callLines.length).toBeGreaterThanOrEqual(2);
    for (const line of callLines) console.log(`[service] wrangler dev console (workerd): ${JSON.stringify(line)}`);
    const slots = callLines.map((line) => line.tokenSlot);
    expect(slots).toContain('current');
    expect(slots).toContain('next');
    for (const line of callLines) {
      expect(line.clientId).toBe(CLIENT_ID);
      // Nothing but the slot label is logged from the auth props.
      expect(Object.keys(line).sort()).toEqual(['clientId', 'event', 'scopes', 'tokenSlot', 'tool'].sort());
    }
    expect(log).not.toContain(TOKEN_CURRENT);
    expect(log).not.toContain(TOKEN_NEXT);
    expect(log).not.toContain(TOKEN_THIRD);

    // The documented own-authentication wiring: per-request props and a factory
    // that reads them with getMcpAuthContext().
    const index = INDEX_SRC();
    expect(index).toContain('authContext: { props: { tokenSlot: slot } }');
    expect(index).toContain('handler.fetch(request, { authInfo })');
    expect(TOOLS_SRC()).toContain('getMcpAuthContext()');
  });
});
