import { z } from "zod";

export const GraphSection = z.enum(["v", "w", "r", "a", "x", "t"]);
export type GraphSection = z.infer<typeof GraphSection>;

export const TerritoryClass = z.enum([
  "orientation",
  "reference",
  "enforcement",
  "scars",
  "contracts",
  "exemplars",
]);
export type TerritoryClass = z.infer<typeof TerritoryClass>;

export const Confidence = z.enum(["HIGH", "MEDIUM", "LOW"]);
export type Confidence = z.infer<typeof Confidence>;

export const Harness = z.enum(["claude", "codex", "opencode", "grok", "pi"]);
export type Harness = z.infer<typeof Harness>;

export const CallerIdentity = z.object({
  harness: Harness,
  model: z.string().min(1),
  provider: z.string().optional(),
  acp: z.object({
    command: z.string().min(1),
    args: z.array(z.string()),
    env: z.record(z.string()).default({}),
  }),
});
export type CallerIdentity = z.infer<typeof CallerIdentity>;

export const DecoderRing = z.object({
  purpose: z.string().min(1),
  nouns: z.array(
    z.object({
      term: z.string(),
      means: z.string(),
      notTobeConfusedWith: z.string().optional(),
    }),
  ),
  traps: z.array(
    z.object({
      theyCallIt: z.string(),
      itActuallyIs: z.string(),
    }),
  ),
  hash: z.string().min(16),
});
export type DecoderRing = z.infer<typeof DecoderRing>;

export const ResearchDirective = z.object({
  id: z.string().min(1),
  wave: z.number().int().default(1),
  territory: z.object({
    kind: z.enum(["paths", "urls"]),
    label: z.string(),
    include: z.array(z.string()).min(1),
    exclude: z.array(z.string()).default([]),
    unitCount: z.number().int(),
  }),
  class: TerritoryClass,
  yields: z.array(GraphSection),
  question: z.string().min(1),
  mustFind: z.array(z.string()).default([]),
  priorArt: z.array(z.string()).default([]),
  decoderRingHash: z.string().min(16),
});
export type ResearchDirective = z.infer<typeof ResearchDirective>;

export const Fact = z.object({
  section: GraphSection,
  key: z.string().min(1),
  value: z.unknown(),
  source: z.string().min(1),
  confidence: Confidence,
});
export type Fact = z.infer<typeof Fact>;

export const WorkerReport = z.object({
  directiveId: z.string(),
  status: z.enum(["closed", "partial", "failed"]),
  reason: z.string().nullable(),
  facts: z.array(Fact),
  surprises: z.array(
    z.object({
      claim: z.string(),
      contradicts: z.string(),
      source: z.string(),
    }),
  ),
  pointers: z.array(
    z.object({
      territory: z.string(),
      why: z.string(),
      suggestedClass: TerritoryClass,
    }),
  ),
  unitsRead: z.number().int(),
});
export type WorkerReport = z.infer<typeof WorkerReport>;

export const LedgerRow = z.object({
  id: z.string(),
  territory: z.string(),
  class: TerritoryClass,
  owner: z.string().nullable(),
  status: z.enum(["open", "closed", "gap"]),
  fact_count: z.number().int(),
  reason: z.string().nullable(),
});
export type LedgerRow = z.infer<typeof LedgerRow>;

export const LedgerGap = z.object({
  territory: z.string(),
  reason: z.string(),
  directiveId: z.string().optional(),
});
export type LedgerGap = z.infer<typeof LedgerGap>;

export const LedgerMerge = z.object({
  kept: z.string(),
  absorbed: z.string(),
  class: TerritoryClass,
  reason: z.string(),
});
export type LedgerMerge = z.infer<typeof LedgerMerge>;

export const CorpusUnit = z.object({
  locator: z.string().min(1),
  sha256: z.string().min(16),
  bytes: z.number().int().nonnegative().optional(),
  etag: z.string().optional(),
  lastModified: z.string().optional(),
});
export type CorpusUnit = z.infer<typeof CorpusUnit>;

export const SourcePin = z.object({
  kind: z.enum(["repo", "docs"]),
  url: z.string().optional(),
  ref: z.string().optional(),
  commit: z.string().optional(),
  describe: z.string().optional(),
  fetched_at: z.string().min(1),
  units: z.number().int().nonnegative(),
  corpus_sha256: z.string().min(16),
  manifest_version: z.string().optional(),
  version_source: z.string().optional(),
  unitPins: z.array(CorpusUnit).optional(),
  etag: z.string().optional(),
  lastModified: z.string().optional(),
});
export type SourcePin = z.infer<typeof SourcePin>;

export const ProvenanceManifest = z.object({
  pins: z.array(SourcePin),
  corpus_sha256: z.string().min(16),
  fetched_at: z.string().optional(),
});
export type ProvenanceManifest = z.infer<typeof ProvenanceManifest>;

export const Ledger = z.object({
  run_id: z.string(),
  tech: z.string(),
  source_mode: z.enum(["repo", "docs", "both", "topics"]),
  decoder_ring_hash: z.string(),
  waves: z.number().int(),
  censused: z.number().int(),
  closed: z.number().int(),
  rows: z.array(LedgerRow),
  gaps: z.array(LedgerGap),
  merges: z.array(LedgerMerge).default([]),
  warnings: z.array(z.string()).default([]),
  spentUsd: z.number().optional(),
  circuitBroken: z.boolean().default(false),
  provenance: ProvenanceManifest.optional(),
});
export type Ledger = z.infer<typeof Ledger>;

export const ResearchRequest = z.object({
  tech: z.string().min(1),
  techSlug: z.string().min(1),
  baseFramework: z.string().optional(),
  sourceMode: z.enum(["repo", "docs", "both"]),
  sources: z.object({
    repoPath: z.string().optional(),
    repoUrl: z.string().url().optional(),
    docsUrl: z.string().url().optional(),
  }),
  caller: CallerIdentity,
  decoderRing: DecoderRing.optional(),
  directives: z.array(ResearchDirective).optional(),
  concurrency: z.number().int().min(1).max(16).default(8),
  maxWaves: z.number().int().min(1).max(4).default(4),
  maxUnits: z.number().int().default(40),
  docsMaxUnits: z.number().int().default(10),
  maxBytes: z.number().int().default(250_000),
  censusOnly: z.boolean().default(false),
  probeSources: z.boolean().default(false),
  depth: z.enum(["quick", "normal", "deep"]).default("normal"),
  budget: z
    .object({
      maxDirectives: z.number().int().default(60),
      maxUsd: z.number().optional(),
    })
    .default({}),
});
export type ResearchRequest = z.infer<typeof ResearchRequest>;

export const ResearchResult = z.object({
  runId: z.string(),
  status: z.enum(["complete", "partial", "BLOCKED"]),
  nrra: z.string(),
  ledger: Ledger,
  reports: z.array(WorkerReport),
  blockReason: z.string().optional(),
  provenance: ProvenanceManifest.optional(),
});
export type ResearchResult = z.infer<typeof ResearchResult>;

export const YIELDS_BY_CLASS: Record<TerritoryClass, GraphSection[]> = {
  orientation: ["v"],
  enforcement: ["r"],
  scars: ["x"],
  contracts: ["t"],
  exemplars: ["a", "x"],
  reference: ["w", "a"],
};

export const WORKER_DEPTH_ENV = "ROSETTA_WORKER";
export const MAX_UNITS_DEFAULT = 40;
export const MAX_BYTES_DEFAULT = 250_000;
