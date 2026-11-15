/**
 * Read-only MCP surface for the Rosetta snapshot.
 *
 * Everything the tools serve comes from ONE seam, {@link readAsset}, so moving
 * the store from Workers Static Assets to R2 is a change to this file only.
 * No module-scope `McpServer` lives here: {@link createRosettaServer} is a
 * factory, called once per HTTP request by `createMcpHandler`.
 *
 * Large payloads are paged. A real prompt is 523,689 bytes and the largest
 * graph is 1,288,978 bytes; returning either whole spends the client's context
 * on one call and no harness can display it. Every read tool therefore answers
 * with one page of at most {@link MAX_PAGE_CHARS} characters plus the
 * `next_offset` that continues it, and the pages are slices of the stored text
 * so a client that concatenates them in order holds the exact bytes the
 * snapshot published (same sha256, reported on every page).
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

/** The one asset every tool call reads before it can answer. */
const MANIFEST_PATH = '/manifest.json';

/**
 * Hard cap on one page of tool output, in UTF-16 characters. It is a constant,
 * not a default the caller can raise: a tool result that carries more than this
 * is a result no harness can put in front of a model.
 */
export const MAX_PAGE_CHARS = 40_000;

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

/** Parse `manifest.json`, or throw a message an agent can act on. */
async function parseManifest(response: Response): Promise<Manifest> {
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

/**
 * How many asset reads one tool call performed: `manifest` for the catalogue,
 * `payload` for anything it served. An unknown key must read the catalogue and
 * NOTHING else — a payload read after a failed lookup is a path that was joined
 * from the request instead of taken from the manifest.
 */
export interface AssetMeter {
  manifest: number;
  payload: number;
}

/**
 * The per-request read seam. One instance per tool call, so the meter is
 * request state rather than module state (V8 isolates are reused across
 * requests and must not carry mutable globals).
 */
export interface SnapshotReader {
  readonly meter: AssetMeter;
  manifest(): Promise<Manifest>;
  text(path: string): Promise<string>;
}

export function createSnapshotReader(env: Env): SnapshotReader {
  const meter: AssetMeter = { manifest: 0, payload: 0 };
  async function read(path: string): Promise<Response> {
    if (path === MANIFEST_PATH) meter.manifest += 1;
    else meter.payload += 1;
    return readAsset(env, path);
  }
  return {
    meter,
    async manifest(): Promise<Manifest> {
      return parseManifest(await read(MANIFEST_PATH));
    },
    async text(path: string): Promise<string> {
      const response = await read(path);
      if (!response.ok) {
        throw new Error(`Snapshot asset ${path} could not be read (status ${response.status}).`);
      }
      return response.text();
    },
  };
}

/** One page of a stored text payload, and the offset that continues it. */
export interface Page {
  content: string;
  offset: number;
  /** Offset of the next page, or `null` when this page reaches the end. */
  next_offset: number | null;
  page_chars: number;
  total_chars: number;
}

function isHighSurrogate(code: number): boolean {
  return code >= 0xd800 && code <= 0xdbff;
}

/**
 * Cut one page out of `text`, never splitting a surrogate pair: the two halves
 * of one character must travel on the same page, or a client that reassembles
 * the pages by concatenation holds text that no longer encodes to the bytes the
 * snapshot published.
 */
export function pageOf(text: string, offset: number, limit: number): Page {
  if (!Number.isInteger(offset) || offset < 0) {
    throw new Error(`offset must be a non-negative integer, got ${offset}.`);
  }
  if (offset > text.length) {
    throw new Error(`offset ${offset} is past the end of this content (${text.length} characters).`);
  }
  const bounded = Math.min(Math.max(Number.isInteger(limit) ? limit : MAX_PAGE_CHARS, 1), MAX_PAGE_CHARS);
  let end = Math.min(text.length, offset + bounded);
  if (end < text.length && isHighSurrogate(text.charCodeAt(end - 1))) {
    // A one-character page cannot step back without emptying itself.
    end = end - offset === 1 ? end + 1 : end - 1;
  }
  const content = text.slice(offset, end);
  return {
    content,
    offset,
    next_offset: end < text.length ? end : null,
    page_chars: content.length,
    total_chars: text.length,
  };
}

/** Lower-case hex sha256 of the UTF-8 bytes of `text`. */
async function sha256Hex(text: string): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)));
  let out = '';
  for (const byte of digest) out += byte.toString(16).padStart(2, '0');
  return out;
}

function textResult(payload: unknown): { content: Array<{ type: 'text'; text: string }> } {
  return { content: [{ type: 'text', text: JSON.stringify(payload, null, 2) }] };
}

/**
 * One log line per tool call, written when the call finishes so it can carry
 * its outcome and how many asset reads it cost. `tokenSlot` is the only field
 * taken from the auth props, and the credential itself is never logged:
 * `clientId` names the principal, the token value stays in the request.
 */
function logToolCall(
  tool: string,
  clientId: string | undefined,
  scopes: string[] | undefined,
  tokenSlot: string,
  outcome: 'ok' | 'error',
  meter: AssetMeter,
): void {
  console.log(
    JSON.stringify({
      event: 'mcp.tool_call',
      tool,
      clientId: clientId ?? 'unknown',
      scopes: scopes ?? [],
      tokenSlot,
      outcome,
      assetReads: meter.payload,
      manifestReads: meter.manifest,
    }),
  );
}

/**
 * Run one tool body and log its result line whether it answers or throws. The
 * meter is what makes "no asset was read for an unknown key" observable from
 * outside the Worker instead of a claim about the code.
 */
async function answerTool<T>(
  tool: string,
  clientId: string | undefined,
  scopes: string[] | undefined,
  tokenSlot: string,
  reader: SnapshotReader,
  body: () => Promise<T>,
): Promise<T> {
  try {
    const result = await body();
    logToolCall(tool, clientId, scopes, tokenSlot, 'ok', reader.meter);
    return result;
  } catch (error) {
    logToolCall(tool, clientId, scopes, tokenSlot, 'error', reader.meter);
    throw error;
  }
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
        const reader = createSnapshotReader(env);
        return answerTool('list_agents', clientId, scopes, tokenSlot, reader, async () => {
          const manifest = await reader.manifest();
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
        });
      },
    );

    server.registerTool(
      'get_agent',
      {
        title: 'Get an agent prompt',
        description:
          'Return one page of an agent prompt, by the name list_agents reports. A real prompt spans several pages: follow next_offset until it is null and concatenate the page texts in order to hold the whole prompt. Read-only.',
        inputSchema: z.object({
          name: z.string().min(1).describe('Agent name exactly as list_agents reports it.'),
          offset: z
            .number()
            .int()
            .min(0)
            .optional()
            .describe('Character offset of the page to return; omit for the first page, then pass the previous page\'s next_offset.'),
          limit: z
            .number()
            .int()
            .min(1)
            .max(MAX_PAGE_CHARS)
            .optional()
            .describe(`Characters per page, at most ${MAX_PAGE_CHARS}.`),
        }),
        annotations: { readOnlyHint: true },
      },
      async ({ name, offset, limit }) => {
        const reader = createSnapshotReader(env);
        return answerTool('get_agent', clientId, scopes, tokenSlot, reader, async () => {
          const manifest = await reader.manifest();
          const agent = manifest.agents.find((entry) => entry.name === name);
          if (agent === undefined) {
            throw new Error(`No agent named "${name}" in snapshot ${manifest.snapshot_id ?? '(unknown)'}. Call list_agents first.`);
          }
          const text = await reader.text(agent.path);
          const page = pageOf(text, offset ?? 0, limit ?? MAX_PAGE_CHARS);
          return textResult({
            name: agent.name,
            description: agent.description ?? null,
            model: agent.model ?? null,
            target: agent.target ?? null,
            path: agent.path,
            bytes: agent.bytes ?? null,
            sha256: await sha256Hex(text),
            ...page,
          });
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
        const reader = createSnapshotReader(env);
        return answerTool('list_kbs', clientId, scopes, tokenSlot, reader, async () => {
          const manifest = await reader.manifest();
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
        });
      },
    );

    server.registerTool(
      'get_kb',
      {
        title: 'Get a knowledge base',
        description:
          'Return one page of a knowledge-base graph by slug, with the graph\'s section names. A graph can be over a megabyte: follow next_offset until it is null and concatenate the page texts in order, or pass a section to page through only that top-level section. Read-only.',
        inputSchema: z.object({
          slug: z.string().min(1).describe('Knowledge-base slug exactly as list_kbs reports it.'),
          section: z
            .string()
            .min(1)
            .optional()
            .describe('Optional top-level section of the graph, as reported by a call without a section.'),
          offset: z
            .number()
            .int()
            .min(0)
            .optional()
            .describe('Character offset of the page to return; omit for the first page, then pass the previous page\'s next_offset.'),
          limit: z
            .number()
            .int()
            .min(1)
            .max(MAX_PAGE_CHARS)
            .optional()
            .describe(`Characters per page, at most ${MAX_PAGE_CHARS}.`),
        }),
        annotations: { readOnlyHint: true },
      },
      async ({ slug, section, offset, limit }) => {
        const reader = createSnapshotReader(env);
        return answerTool('get_kb', clientId, scopes, tokenSlot, reader, async () => {
          const manifest = await reader.manifest();
          const kb = manifest.kbs.find((entry) => entry.slug === slug);
          if (kb === undefined) {
            throw new Error(
              `No knowledge base with slug "${slug}" in snapshot ${manifest.snapshot_id ?? '(unknown)'}. Call list_kbs first.`,
            );
          }
          // The page is a slice of the stored bytes, so the graph is read as
          // text and only parsed for its section names.
          const text = await reader.text(kb.path);
          let graph: unknown;
          try {
            graph = JSON.parse(text);
          } catch (error) {
            throw new Error(`Graph ${kb.path} is not valid JSON: ${(error as Error).message}`);
          }
          if (typeof graph !== 'object' || graph === null || Array.isArray(graph)) {
            throw new Error(`Graph ${kb.path} is not a JSON object.`);
          }
          const sections = Object.keys(graph as Record<string, unknown>).sort();
          let source = text;
          if (section !== undefined) {
            if (!sections.includes(section)) {
              throw new Error(`Section "${section}" is not in ${kb.slug}. Sections: ${sections.join(', ')}.`);
            }
            source = JSON.stringify((graph as Record<string, unknown>)[section], null, 2);
          }
          const page = pageOf(source, offset ?? 0, limit ?? MAX_PAGE_CHARS);
          return textResult({
            slug: kb.slug,
            kb_version: kb.kb_version ?? null,
            ...(section === undefined ? {} : { section }),
            sections,
            path: kb.path,
            bytes: kb.bytes ?? null,
            sha256: await sha256Hex(source),
            ...page,
          });
        });
      },
    );

    return server;
  };
}
