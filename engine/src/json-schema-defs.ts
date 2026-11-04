import type { z } from "zod";
import { zodToJsonSchema } from "./zod-json-schema.ts";

/**
 * Emit one JSON Schema bundle that uses `$defs` + `$ref` instead of inlining every
 * shared object.
 *
 * WHY
 *   `zodToJsonSchema` converts one schema at a time, so a type used in four places was
 *   written out four times. `SourcePin` alone appeared inside `Ledger`, `ResearchResult`
 *   and `ProvenanceManifest`, and `unitPins` appeared inside each of those — which is how
 *   the bundle reached 1723 lines whose distinct content is roughly a quarter of that.
 *
 * HOW
 *   1. Convert each named schema once and keep its body (title held aside, because a
 *      nested inline copy has no title and a title-inclusive comparison would never match).
 *   2. Index every body by its canonical JSON.
 *   3. Walk each schema and replace any sub-object structurally equal to a known body with
 *      `{ "$ref": "#/$defs/<Name>" }`. A definition's own root is left intact so it cannot
 *      become a self-reference.
 *   4. Publish the top-level entries as `$ref`s, so each type is defined exactly once.
 *
 * The result is deterministic: `Object.keys` order comes from the caller's literal, and the
 * canonical form sorts keys at every level.
 */

/** Key-order-independent JSON, so structurally equal schemas compare equal. */
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`);
    return `{${entries.join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

interface NamedBody {
  title: string;
  body: Record<string, unknown>;
}

export function bundleWithDefs(
  schemas: Record<string, z.ZodTypeAny>,
): Record<string, unknown> {
  const named: Record<string, NamedBody> = {};
  for (const [name, schema] of Object.entries(schemas)) {
    const { title, ...body } = zodToJsonSchema(schema, name) as {
      title?: string;
      [k: string]: unknown;
    };
    named[name] = { title: title ?? name, body };
  }

  // shape -> the def name that owns it. First name wins, so the order of `schemas`
  // decides which name a shared shape takes.
  const shapeToName = new Map<string, string>();
  for (const [name, { body }] of Object.entries(named)) {
    const key = canonical(body);
    if (!shapeToName.has(key)) shapeToName.set(key, name);
  }

  function rewrite(node: unknown, selfName: string, isRoot: boolean): unknown {
    if (Array.isArray(node)) return node.map((item) => rewrite(item, selfName, false));
    if (node && typeof node === "object") {
      const record = node as Record<string, unknown>;
      if (!isRoot) {
        const match = shapeToName.get(canonical(record));
        if (match) return { $ref: `#/$defs/${match}` };
      }
      const out: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(record)) {
        out[key] = rewrite(value, selfName, false);
      }
      return out;
    }
    return node;
  }

  const defs: Record<string, unknown> = {};
  for (const [name, { title, body }] of Object.entries(named)) {
    defs[name] = { title, ...(rewrite(body, name, true) as Record<string, unknown>) };
  }

  const bundle: Record<string, unknown> = {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    $id: "https://agentic-rosetta.dev/schema/pipeline.json",
    title: "agentic-rosetta pipeline contract",
    generatedFrom: "engine/src/contract.ts",
    $defs: defs,
  };
  for (const name of Object.keys(named)) {
    bundle[name] = { $ref: `#/$defs/${name}` };
  }
  return bundle;
}
