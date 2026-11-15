/**
 * The snapshot store: immutable R2 blobs behind one mutable pointer object.
 *
 * Layout, written by `scripts/kb-delivery/publish_snapshot.py`:
 *
 *   current.json                        the pointer: `{ snapshot_id, manifest_sha256, published_at, files }`
 *   snapshots/<snapshot_id>/manifest.json
 *   snapshots/<snapshot_id>/files.json  per-path `{ sha256, bytes }`
 *   blobs/<sha256>                      payload bytes, content-addressed and shared across snapshots
 *
 * A publish writes every blob first, then the snapshot metadata, then
 * `current.json` LAST. R2 writes are strongly consistent once the put
 * resolves, so the pointer flip is the swap: a reader either follows the old
 * pointer into a complete old snapshot or the new pointer into a complete new
 * one, never a mix and never a manifest naming a blob that does not exist.
 *
 * Everything the Worker serves goes through {@link SnapshotStore.read}. The
 * tools in `tools.ts` read through their own `readAsset` seam, which calls
 * `env.ASSETS.fetch`; `tools.ts` is not in this task's write set, so
 * {@link SnapshotStore.fetcher} serves that seam from R2 instead of the Static
 * Assets binding the Worker used to carry. Reads are still the same bytes: the
 * snapshot the pointer names, resolved to a content-addressed blob.
 */
import { normalizeAssetPath } from './tools';

/** The one binding this Worker reads its content from. */
export interface StoreEnv {
  SNAPSHOT: R2Bucket;
}

/** The pointer object's key. Mutable; every other key is immutable. */
export const POINTER_KEY = 'current.json';
/** Prefix of immutable payload objects, keyed by their sha256. */
export const BLOB_PREFIX = 'blobs/';
/** Prefix of per-snapshot metadata objects. */
export const SNAPSHOT_PREFIX = 'snapshots/';

const MANIFEST_PATH = '/manifest.json';
const FILES_PATH = '/files.json';

/** `current.json`: the one object a publish replaces. */
export interface CurrentPointer {
  schema?: number;
  snapshot_id: string;
  /** sha256 of `snapshots/<snapshot_id>/manifest.json`, so a reader can verify the pointer. */
  manifest_sha256?: string;
  published_at?: string;
  /** Payload files in the snapshot, for an operator reading the pointer alone. */
  files?: number;
}

interface FileEntry {
  sha256: string;
  bytes?: number;
}

/** `snapshots/<id>/files.json`: every staged path and the blob that holds it. */
export interface FilesIndex {
  schema?: number;
  snapshot_id?: string;
  files: Record<string, FileEntry>;
}

function contentTypeFor(key: string): string {
  if (key.endsWith('.json')) return 'application/json; charset=utf-8';
  if (key.endsWith('.md')) return 'text/markdown; charset=utf-8';
  if (key.endsWith('.toml')) return 'text/plain; charset=utf-8';
  return 'text/plain; charset=utf-8';
}

function notFound(): Response {
  return new Response('Not Found', { status: 404, headers: { 'Cache-Control': 'no-store' } });
}

function storeError(message: string): Response {
  return new Response(JSON.stringify({ error: 'snapshot_store_error', reason: message }), {
    status: 500,
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
  });
}

/**
 * The read surface. One instance per request: the memo below is request state,
 * never module state (V8 isolates are reused across requests).
 */
export interface SnapshotStore {
  /** The pointer currently published, or null when nothing has been published. */
  pointer(): Promise<CurrentPointer | null>;
  /** The bytes of one snapshot path, or a 404 when the pointer or the blob is absent. */
  read(path: string): Promise<Response>;
  /**
   * A `fetch`-shaped adapter for the one seam `tools.ts` already uses
   * (`env.ASSETS.fetch(new Request(url))`). It resolves the same store; it
   * implements no `connect`, because nothing in the Worker opens a socket
   * through it.
   */
  fetcher(): { fetch(request: Request): Promise<Response> };
}

export function createSnapshotStore(bucket: R2Bucket): SnapshotStore {
  let pointerRequest: Promise<CurrentPointer | null> | null = null;
  const indexRequests = new Map<string, Promise<FilesIndex | null>>();

  async function readJson<T>(key: string): Promise<T | null> {
    const object = await bucket.get(key);
    if (object === null) return null;
    const text = await object.text();
    try {
      return JSON.parse(text) as T;
    } catch (error) {
      throw new Error(`${key} is not valid JSON: ${(error as Error).message}`);
    }
  }

  function pointer(): Promise<CurrentPointer | null> {
    if (pointerRequest === null) pointerRequest = readJson<CurrentPointer>(POINTER_KEY);
    return pointerRequest;
  }

  function filesIndex(snapshotId: string): Promise<FilesIndex | null> {
    const key = `${SNAPSHOT_PREFIX}${snapshotId}/files.json`;
    const cached = indexRequests.get(key);
    if (cached !== undefined) return cached;
    const pending = readJson<FilesIndex>(key);
    indexRequests.set(key, pending);
    return pending;
  }

  /**
   * Which immutable object holds `path`, or null when the published snapshot
   * does not name it. A path is a lookup in the snapshot's own inventory, never
   * a key joined from the request: a path the publisher did not stage cannot
   * address an object.
   */
  async function objectKeyFor(path: string): Promise<string | null> {
    const normalized = normalizeAssetPath(path);
    if (normalized === null) return null;
    const current = await pointer();
    if (current === null || typeof current.snapshot_id !== 'string' || current.snapshot_id.length === 0) return null;
    const base = `${SNAPSHOT_PREFIX}${current.snapshot_id}`;
    if (normalized === MANIFEST_PATH) return `${base}/manifest.json`;
    if (normalized === FILES_PATH) return `${base}/files.json`;
    const index = await filesIndex(current.snapshot_id);
    if (index === null || typeof index.files !== 'object' || index.files === null) return null;
    const entry = index.files[normalized.slice(1)];
    if (entry === undefined || typeof entry.sha256 !== 'string' || entry.sha256.length === 0) return null;
    return `${BLOB_PREFIX}${entry.sha256}`;
  }

  async function read(path: string): Promise<Response> {
    try {
      const key = await objectKeyFor(path);
      if (key === null) return notFound();
      const object = await bucket.get(key);
      if (object === null) return notFound();
      return new Response(await object.text(), {
        status: 200,
        headers: {
          'Content-Type': contentTypeFor(key),
          // A bearer-gated snapshot changes on publish, so no shared cache
          // may keep a previous version's bytes.
          'Cache-Control': 'no-store',
        },
      });
    } catch (error) {
      return storeError((error as Error).message);
    }
  }

  return {
    pointer,
    read,
    fetcher() {
      return {
        fetch: (request: Request): Promise<Response> => read(new URL(request.url).pathname),
      };
    },
  };
}
