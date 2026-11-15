/**
 * Read-only MCP surface for the Rosetta snapshot.
 *
 * Everything the tools serve comes from ONE seam, {@link readAsset}, so moving
 * the store from Workers Static Assets to R2 is a change to this file only.
 * No module-scope `McpServer` lives here: {@link createRosettaServer} is a
 * factory, called once per HTTP request by `createMcpHandler`.
 */
import { McpServer } from '@modelcontextprotocol/server';
import { getMcpAuthContext } from 'agents/mcp/server';
import { z } from 'zod';

/** Bindings this Worker reads. Nothing else is available to it. */
export interface Env {
  /** Static Assets binding for the staged snapshot. */
  ASSETS: Fetcher;
  /** Current shared bearer token. */
  ROSETTA_MCP_TOKEN?: string;
  /** Optional next shared token, accepted during a rotation. */
  ROSETTA_MCP_TOKEN_NEXT?: string;
}

/** One agent prompt recorded in `manifest.json`. */
export interface ManifestAgent {
  name: string;
  description?: string;
  model?: string;
  bytes?: number;
  /** Asset path of the prompt, relative to the snapshot root. */
  path: string;
  /** Harness target the entry was projected for, when the manifest records one. */
  target?: string;
}

/** One knowledge-base graph recorded in `manifest.json`. */
export interface ManifestKb {
  slug: string;
  kb_version?: string;
  bytes?: number;
  /** Asset path of the graph, relative to the snapshot root. */
  path: string;
}

/** `manifest.json`: identity plus the listings the `list_*` tools answer from. */
export interface Manifest {
  schema?: number;
  snapshot_id?: string;
  published_at?: string;
  consumer_ref?: string | null;
  plugin_version?: string;
  targets?: Record<string, { dest: string; files: number }>;
  agents: ManifestAgent[];
  kbs: ManifestKb[];
  excluded?: Record<string, number>;
}

/** A path that cannot escape the snapshot root. */
export function normalizeAssetPath(input: string): string | null {
  const relative = input.startsWith('/') ? input.slice(1) : input;
  if (relative.length === 0) return null;
  if (relative.includes('\0') || relative.includes('\\')) return null;
  const segments = relative.split('/');
  for (const segment of segments) {
    if (segment === '' || segment === '.' || segment === '..') return null;
  }
  return `/${segments.join('/')}`;
}

/**
 * The single read seam. Static Assets today; an R2 read replaces the body of
 * this function and nothing else in the Worker.
 */
export async function readAsset(env: Env, path: string): Promise<Response> {
  const normalized = normalizeAssetPath(path);
  if (normalized === null) return new Response('Not Found', { status: 404 });
  const assetUrl = new URL(normalized, 'https://assets.local');
  return env.ASSETS.fetch(new Request(assetUrl, { method: 'GET' }));
}

/** Read and parse `manifest.json`, or throw a message an agent can act on. */
export async function readManifest(env: Env): Promise<Manifest> {
  const response = await readAsset(env, '/manifest.json');
  if (!response.ok) {
    throw new Error(
      `The published snapshot has no manifest.json (asset read returned ${response.status}). Publish the snapshot before serving it.`,
    );
  }
  const parsed: unknown = await response.json();
  if (typeof parsed !== 'object' || parsed === null) {
    throw new Error('manifest.json is not a JSON object.');
  }
  const manifest = parsed as Partial<Manifest>;
  if (!Array.isArray(manifest.agents) || !Array.isArray(manifest.kbs)) {
    throw new Error('manifest.json is missing the agents or kbs listing.');
  }
  return manifest as Manifest;
}

/** Read one asset as UTF-8 text, or throw when the snapshot does not carry it. */
async function readAssetText(env: Env, path: string): Promise<string> {
  const response = await readAsset(env, path);
  if (!response.ok) {
    throw new Error(`Snapshot asset ${path} could not be read (status ${response.status}).`);
  }
  return response.text();
}

function textResult(payload: unknown): { content: Array<{ type: 'text'; text: string }> } {
  return { content: [{ type: 'text', text: JSON.stringify(payload, null, 2) }] };
}

/**
 * One log line per tool call. `tokenSlot` is the only field taken from the
 * auth props, and the credential itself is never logged: `clientId` names the
 * principal, the token value stays in the request.
 */
function logToolCall(tool: string, clientId: string | undefined, scopes: string[] | undefined, tokenSlot: string): void {
  console.log(
    JSON.stringify({
      event: 'mcp.tool_call',
      tool,
      clientId: clientId ?? 'unknown',
      scopes: scopes ?? [],
      tokenSlot,
    }),
  );
}

/** The slot label the Worker put in `authContext.props`; nothing else is read from props. */
function tokenSlotFromAuthContext(): string {
  const props = getMcpAuthContext()?.props ?? {};
  return typeof props.tokenSlot === 'string' ? props.tokenSlot : 'unattributed';
}

/**
 * Build the per-request server factory handed to `createMcpHandler`. The
 * factory closes over `env` (bindings are only reachable inside `fetch`) and
 * over the `authInfo` the handler resolved for this request.
 */
export function createRosettaServer(env: Env): (ctx: { authInfo?: { clientId: string; scopes: string[] } }) => McpServer {
  return (ctx) => {
    const clientId = ctx.authInfo?.clientId;
    const scopes = ctx.authInfo?.scopes;
    // Read once, in the factory, from the auth props the Worker attached.
    const tokenSlot = tokenSlotFromAuthContext();
    const server = new McpServer({ name: 'rosetta-kb', version: '0.1.0' });

    server.registerTool(
      'list_agents',
      {
        title: 'List agent prompts',
        description:
          'List every agent prompt in the published Rosetta snapshot: name, description and model. Read-only.',
        inputSchema: z.object({}),
        annotations: { readOnlyHint: true },
      },
      async () => {
        logToolCall('list_agents', clientId, scopes, tokenSlot);
        const manifest = await readManifest(env);
        return textResult({
          snapshot_id: manifest.snapshot_id ?? null,
          published_at: manifest.published_at ?? null,
          count: manifest.agents.length,
          agents: manifest.agents.map((agent) => ({
            name: agent.name,
            description: agent.description ?? null,
            model: agent.model ?? null,
            target: agent.target ?? null,
          })),
        });
      },
    );

    server.registerTool(
      'get_agent',
      {
        title: 'Get an agent prompt',
        description: 'Return the full prompt text of one agent, by the name list_agents reports. Read-only.',
        inputSchema: z.object({ name: z.string().min(1).describe('Agent name exactly as list_agents reports it.') }),
        annotations: { readOnlyHint: true },
      },
      async ({ name }) => {
        logToolCall('get_agent', clientId, scopes, tokenSlot);
        const manifest = await readManifest(env);
        const agent = manifest.agents.find((entry) => entry.name === name);
        if (agent === undefined) {
          throw new Error(`No agent named ${name} in snapshot ${manifest.snapshot_id ?? '(unknown)'}. Call list_agents first.`);
        }
        const content = await readAssetText(env, agent.path);
        return textResult({
          name: agent.name,
          description: agent.description ?? null,
          model: agent.model ?? null,
          target: agent.target ?? null,
          path: agent.path,
          bytes: agent.bytes ?? null,
          content,
        });
      },
    );

    server.registerTool(
      'list_kbs',
      {
        title: 'List knowledge bases',
        description: 'List every knowledge-base graph in the published Rosetta snapshot: slug, version and size. Read-only.',
        inputSchema: z.object({}),
        annotations: { readOnlyHint: true },
      },
      async () => {
        logToolCall('list_kbs', clientId, scopes, tokenSlot);
        const manifest = await readManifest(env);
        return textResult({
          snapshot_id: manifest.snapshot_id ?? null,
          published_at: manifest.published_at ?? null,
          count: manifest.kbs.length,
          kbs: manifest.kbs.map((kb) => ({
            slug: kb.slug,
            kb_version: kb.kb_version ?? null,
            bytes: kb.bytes ?? null,
          })),
        });
      },
    );

    server.registerTool(
      'get_kb',
      {
        title: 'Get a knowledge base',
        description:
          'Return one knowledge-base graph by slug. Without a section it returns the section names and the whole graph; with a section it returns only that top-level section. Read-only.',
        inputSchema: z.object({
          slug: z.string().min(1).describe('Knowledge-base slug exactly as list_kbs reports it.'),
          section: z
            .string()
            .min(1)
            .optional()
            .describe('Optional top-level section of the graph, as reported by a call without a section.'),
        }),
        annotations: { readOnlyHint: true },
      },
      async ({ slug, section }) => {
        logToolCall('get_kb', clientId, scopes, tokenSlot);
        const manifest = await readManifest(env);
        const kb = manifest.kbs.find((entry) => entry.slug === slug);
        if (kb === undefined) {
          throw new Error(`No knowledge base with slug ${slug} in snapshot ${manifest.snapshot_id ?? '(unknown)'}. Call list_kbs first.`);
        }
        const text = await readAssetText(env, kb.path);
        const graph: unknown = JSON.parse(text);
        if (typeof graph !== 'object' || graph === null || Array.isArray(graph)) {
          throw new Error(`Graph ${kb.path} is not a JSON object.`);
        }
        const sections = Object.keys(graph as Record<string, unknown>).sort();
        if (section === undefined) {
          return textResult({ slug: kb.slug, kb_version: kb.kb_version ?? null, sections, content: graph });
        }
        if (!sections.includes(section)) {
          throw new Error(`Section ${section} is not in ${kb.slug}. Sections: ${sections.join(', ')}.`);
        }
        return textResult({
          slug: kb.slug,
          kb_version: kb.kb_version ?? null,
          section,
          content: (graph as Record<string, unknown>)[section],
        });
      },
    );

    return server;
  };
}
