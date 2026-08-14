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
