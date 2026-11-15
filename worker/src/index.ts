/**
 * rosetta-kb-mcp — a read-only MCP service over a published Rosetta snapshot.
 *
 * Request flow, in order:
 *   1. `/.well-known/*` is a plain 404: this service has no OAuth discovery.
 *   2. The Worker authenticates the bearer token itself (SHA-256 of both
 *      sides, compared with `crypto.subtle.timingSafeEqual`) and only then
 *      hands the request to the MCP handler.
 *   3. `/mcp` is served by a handler built from a server factory, one per
 *      request, in the stateless legacy lane with sessions disabled.
 *   4. Everything else is a named snapshot path behind the same token.
 *
 * Content lives in R2, addressed through one mutable pointer object (see
 * `store.ts`): the Worker bundle carries no snapshot, so publishing a prompt
 * writes objects and never creates a Worker version.
 *
 * The default export is an object with `fetch` on purpose: Wrangler treats a
 * callable default export as a WorkerEntrypoint class, and the handler must be
 * built inside `fetch` so its tools can close over the bindings.
 */
import { createMcpHandler } from 'agents/mcp/server';
import { createSnapshotStore, type SnapshotStore } from './store';
import { createRosettaServer, type Env as ToolsEnv } from './tools';

export type { Env } from './tools';

/** Bindings the Worker itself reads: the tools' own Env plus the R2 bucket. */
export type WorkerEnv = Omit<ToolsEnv, 'ASSETS'> & { SNAPSHOT: R2Bucket };

/** Realm quoted in every authentication challenge. */
const REALM = 'rosetta-mcp';

/** Which secret slot validated the request. Only the label is ever logged. */
export type TokenSlot = 'current' | 'next';

interface ConfiguredSlot {
  slot: TokenSlot;
  value: string;
}

function unauthorized(reason: string): Response {
  return new Response(JSON.stringify({ error: 'unauthorized', reason }), {
    status: 401,
    headers: {
      'Content-Type': 'application/json',
      'WWW-Authenticate': `Bearer realm="${REALM}"`,
    },
  });
}

function notConfigured(): Response {
  return new Response(JSON.stringify({ error: 'server_not_configured', reason: 'no bearer token is set on this Worker' }), {
    status: 503,
    headers: {
      'Content-Type': 'application/json',
      'WWW-Authenticate': `Bearer realm="${REALM}"`,
    },
  });
}

function notFound(): Response {
  return new Response('Not Found', { status: 404 });
}

async function sha256(value: string): Promise<Uint8Array> {
  return new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value)));
}

/**
 * Constant-time comparison of two SHA-256 digests.
 *
 * `crypto.subtle.timingSafeEqual` is the runtime's own constant-time compare.
 * It is required here rather than reproduced: a hand-rolled loop in JavaScript
 * is not a reliable constant-time primitive, and falling back to `===` is
 * exactly the fail-open shape this check exists to avoid.
 */
function timingSafeEqual(a: Uint8Array, b: Uint8Array): boolean {
  const subtle = crypto.subtle as unknown as {
    timingSafeEqual?: (left: ArrayBuffer | ArrayBufferView, right: ArrayBuffer | ArrayBufferView) => boolean;
  };
  if (typeof subtle.timingSafeEqual !== 'function') {
    throw new Error('crypto.subtle.timingSafeEqual is unavailable in this runtime; refusing to compare tokens without it.');
  }
  return subtle.timingSafeEqual(a, b);
}

function hex(bytes: Uint8Array): string {
  let out = '';
  for (const byte of bytes) out += byte.toString(16).padStart(2, '0');
  return out;
}

/** `Bearer <token>` only. A missing header, another scheme or an empty token is not a credential. */
export function bearerToken(request: Request): string | null {
  const header = request.headers.get('Authorization');
  if (header === null) return null;
  const match = /^Bearer +(\S+)$/i.exec(header.trim());
  if (match === null) return null;
  const token = match[1];
  return token.length === 0 ? null : token;
}

function configuredSlots(env: Pick<ToolsEnv, 'ROSETTA_MCP_TOKEN' | 'ROSETTA_MCP_TOKEN_NEXT'>): ConfiguredSlot[] {
  const candidates: Array<{ slot: TokenSlot; value: string | undefined }> = [
    { slot: 'current', value: env.ROSETTA_MCP_TOKEN },
    { slot: 'next', value: env.ROSETTA_MCP_TOKEN_NEXT },
  ];
  // An unset or empty slot is not a credential: drop it rather than compare it.
  return candidates.filter((entry): entry is ConfiguredSlot => typeof entry.value === 'string' && entry.value.length > 0);
}

/**
 * Which slot the presented token belongs to, or null.
 *
 * Every configured slot is compared even after a match, so the loop does not
 * reveal which slot matched through its timing. An unset or empty slot is not
 * a credential and never matches: `'Bearer undefined'` is rejected whether or
 * not `ROSETTA_MCP_TOKEN_NEXT` is configured.
 */
async function resolveTokenSlot(slots: ConfiguredSlot[], token: string): Promise<TokenSlot | null> {
  const presented = await sha256(token);
  let matched: TokenSlot | null = null;
  for (const { slot, value } of slots) {
    const secret = await sha256(value);
    if (timingSafeEqual(secret, presented)) matched = slot;
  }
  return matched;
}

/** The principal a valid shared token stands for. */
function authInfoFor(presentedDigest: string): { token: string; clientId: string; scopes: string[] } {
  return {
    // The credential itself is never copied into handler state or logs; its
    // SHA-256 fingerprint identifies the call without exposing the secret.
    token: `sha256:${presentedDigest}`,
    clientId: 'static-token',
    scopes: ['mcp:read'],
  };
}

/** `/manifest.json` and `/files/<snapshot path>` are the only readable assets. */
function assetPathFor(pathname: string): string | null {
  if (pathname === '/manifest.json') return '/manifest.json';
  if (pathname === '/files' || pathname.startsWith('/files/')) return pathname.slice('/files'.length) || null;
  return null;
}

async function serveAsset(request: Request, store: SnapshotStore, path: string): Promise<Response> {
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    return new Response('Method Not Allowed', { status: 405, headers: { Allow: 'GET, HEAD' } });
  }
  const response = await store.read(path);
  if (response.status === 404) return notFound();
  // The snapshot is private (bearer-gated) and changes with every publish, so
  // a shared cache that kept the previous version's bytes would serve a rolled
  // back snapshot to a device. No shared cache at all.
  const headers = new Headers(response.headers);
  headers.set('Cache-Control', 'no-store');
  return new Response(response.body, { status: response.status, headers });
}

export default {
  async fetch(request: Request, env: WorkerEnv, _ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    // No OAuth discovery surface: unauthenticated probes get a plain 404 so no
    // client is pointed at an authorization server that does not exist.
    if (url.pathname === '/.well-known' || url.pathname.startsWith('/.well-known/')) {
      return notFound();
    }

    const slots = configuredSlots(env);
    if (slots.length === 0) return notConfigured();

    const token = bearerToken(request);
    if (token === null) return unauthorized('missing bearer token');

    const slot = await resolveTokenSlot(slots, token);
    if (slot === null) return unauthorized('invalid bearer token');

    const presentedDigest = hex(await sha256(token));
    const authInfo = authInfoFor(presentedDigest);
    const store = createSnapshotStore(env.SNAPSHOT);

    if (url.pathname === '/mcp') {
      // `tools.ts` reads through its own `env.ASSETS.fetch` seam; the store
      // adapter answers that seam from R2, so the tools and the asset route
      // read the same published snapshot.
      const toolEnv: ToolsEnv = { ...env, ASSETS: store.fetcher() as unknown as Fetcher };
      const handler = createMcpHandler(createRosettaServer(toolEnv), {
        route: '/mcp',
        legacy: 'stateless',
        corsOptions: false,
        authContext: { props: { tokenSlot: slot } },
      });
      return handler.fetch(request, { authInfo });
    }

    const assetPath = assetPathFor(url.pathname);
    if (assetPath !== null) return serveAsset(request, store, assetPath);

    return notFound();
  },
};
