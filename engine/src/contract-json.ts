#!/usr/bin/env bun
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { zodToJsonSchema } from "./zod-json-schema.ts";
import {
  CallerIdentity,
  DecoderRing,
  Fact,
  Ledger,
  ResearchDirective,
  ResearchRequest,
  ResearchResult,
  WorkerReport,
} from "./contract.ts";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = resolve(here, "../../scripts/rosetta/schema");
mkdirSync(outDir, { recursive: true });

const schemas = {
  ResearchRequest,
  ResearchDirective,
  CallerIdentity,
  DecoderRing,
  Fact,
  WorkerReport,
  Ledger,
  ResearchResult,
} as const;

const bundle: Record<string, unknown> = {
  $schema: "https://json-schema.org/draft/2020-12/schema",
  $id: "https://agentic-rosetta.dev/schema/pipeline.json",
  title: "agentic-rosetta pipeline contract",
  generatedFrom: "engine/src/contract.ts",
};
for (const [name, schema] of Object.entries(schemas)) {
  bundle[name] = zodToJsonSchema(schema, name);
}

const dest = resolve(outDir, "pipeline.schema.json");
writeFileSync(dest, JSON.stringify(bundle, null, 2) + "\n");
process.stdout.write(`wrote ${dest}\n`);
