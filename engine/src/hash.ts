import { createHash } from "node:crypto";
import type { DecoderRing } from "./contract.ts";

/** Canonical JSON: sorted keys, no whitespace. Load-bearing for the ring hash. */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(sortKeys(value));
}

function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(obj).sort()) {
      out[key] = sortKeys(obj[key]);
    }
    return out;
  }
  return value;
}

export function sha256(text: string): string {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

export function sha256Bytes(buf: Uint8Array | Buffer): string {
  return createHash("sha256").update(buf).digest("hex");
}

/** Digest over sorted ``(locator, unit_sha256)`` pairs. Pure — no I/O. */
export function corpusDigest(units: { locator: string; sha256: string }[]): string {
  const pairs = units
    .filter((u) => u.locator && u.sha256)
    .map((u) => [u.locator, u.sha256] as const)
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  return sha256(canonicalJson(pairs));
}

export function hashDecoderRing(ring: Omit<DecoderRing, "hash">): string {
  return sha256(
    canonicalJson({
      purpose: ring.purpose,
      nouns: ring.nouns,
      traps: ring.traps,
    }),
  );
}

export function withRingHash(ring: Omit<DecoderRing, "hash">): DecoderRing {
  return { ...ring, hash: hashDecoderRing(ring) };
}
