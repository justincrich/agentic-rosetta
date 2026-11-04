#!/usr/bin/env bun
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { bundleWithDefs } from "./json-schema-defs.ts";
import {
  CallerIdentity,
  CorpusUnit,
  DecoderRing,
  Fact,
  Ledger,
  ProvenanceManifest,
  ResearchDirective,
  ResearchRequest,
  ResearchResult,
  SourcePin,
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
  CorpusUnit,
  SourcePin,
  ProvenanceManifest,
} as const;

// One definition per type, referenced by $ref. Inlining every shared object is what made
// this artifact 1723 lines for ~250 lines of distinct content.
const bundle = bundleWithDefs(schemas);

const dest = resolve(outDir, "pipeline.schema.json");
writeFileSync(dest, JSON.stringify(bundle, null, 2) + "\n");
process.stdout.write(`wrote ${dest}\n`);
