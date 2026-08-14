import type { DecoderRing, Ledger, ProvenanceManifest, ResearchRequest, SourcePin, WorkerReport } from "../contract.ts";
import { corpusDigest } from "../hash.ts";
import { reconcile } from "./reconcile.ts";

export function emitNrra(opts: {
  req: ResearchRequest;
  ring: DecoderRing;
  ledger: Ledger;
  reports: WorkerReport[];
  runId: string;
  status: "complete" | "partial" | "BLOCKED";
  pins?: SourcePin[];
}): string {
  const rec = reconcile(opts.reports);
  const v = rec.promoted.filter((f) => f.section === "v");
  const w = rec.promoted.filter((f) => f.section === "w");
  const r = rec.promoted.filter((f) => f.section === "r");
  const a = rec.promoted.filter((f) => f.section === "a");
  const x = rec.promoted.filter((f) => f.section === "x");
  const t = rec.promoted.filter((f) => f.section === "t");
  const today = new Date().toISOString().slice(0, 10);
  const seed = {
    v: Object.fromEntries(v.map((f) => [f.key, f.value])),
    w: Object.fromEntries(w.map((f) => [f.key, f.value])),
    r: Object.fromEntries(r.map((f) => [f.key, f.value])),
    a: Object.fromEntries(a.map((f) => [f.key, f.value])),
    x: Object.fromEntries(x.map((f) => [f.key, f.value])),
    t: Object.fromEntries(t.map((f) => [f.key, f.value])),
  };
  const conf = rec.promoted.some((f) => f.confidence === "HIGH")
    ? rec.promoted.every((f) => f.confidence === "HIGH") ? "HIGH" : "MEDIUM"
    : rec.promoted.length ? "MEDIUM" : "LOW";

  const sources = [...new Set(rec.promoted.map((f) => f.source))];
  const factSources: Record<string, { source: string; confidence: string }> = {};
  for (const f of rec.promoted) {
    factSources[`${f.section}:${f.key}`] = { source: f.source, confidence: f.confidence };
  }
  const pins = opts.pins ?? opts.ledger.provenance?.pins ?? [];
  const provenance: ProvenanceManifest = opts.ledger.provenance ?? {
    pins,
    corpus_sha256: pins.length
      ? (pins.length === 1 ? pins[0].corpus_sha256 : corpusDigest(
          pins.flatMap((p) => (p.unitPins ?? []).map((u) => ({ locator: u.locator, sha256: u.sha256 }))),
        ))
      : corpusDigest([]),
    fetched_at: new Date().toISOString(),
  };

  return `---
artifact: rosetta-research
tech: "${opts.req.tech}"
tech_slug: "${opts.req.techSlug}"
base_framework: "${opts.req.baseFramework ?? ""}"
repo_url: "${opts.req.sources.repoUrl ?? opts.req.sources.repoPath ?? "null"}"
docs_url: "${opts.req.sources.docsUrl ?? "null"}"
research_tier: "engine:mastra-acp"
depth: "${opts.req.depth}"
status: "${opts.status}"
overall_confidence: "${conf}"
date: "${today}"
run_id: "${opts.runId}"
decoder_ring_hash: "${opts.ring.hash}"
storage: { holocron_id: null, local_path: null, resolved_by: "engine" }
---

# Rosetta Research: ${opts.req.tech}

## 1. Mental Model (registers)
- Paradigm: ${opts.ring.purpose}
- Registers: ${opts.ring.nouns.map((n) => n.term).join(" ↔ ") || "(none)"}
- Traps: ${opts.ring.traps.map((t) => `${t.theyCallIt} → ${t.itActuallyIs}`).join("; ") || "(none)"}

## 2. Version Pins        # → fact-graph "v"
| field | value | source | confidence |
${v.map((f) => `| ${f.key} | ${JSON.stringify(f.value)} | ${f.source} | ${f.confidence} |`).join("\n") || "| (none) | | | |"}

## 3. Components / Widgets  # → fact-graph "w"
| name | value | source | confidence |
${w.map((f) => `| ${f.key} | ${JSON.stringify(f.value)} | ${f.source} | ${f.confidence} |`).join("\n") || "| (none) | | | |"}

## 4. Hard Rules           # → fact-graph "r"
| rule_id | value | source | confidence |
${r.map((f) => `| ${f.key} | ${JSON.stringify(f.value)} | ${f.source} | ${f.confidence} |`).join("\n") || "| (none) | | | |"}

## 5. API Signatures       # → fact-graph "a"
| operation | exact_expression | source | confidence |
${a.map((f) => `| ${f.key} | ${JSON.stringify(f.value)} | ${f.source} | ${f.confidence} |`).join("\n") || "| (none) | | | |"}

## 6. Anti-Patterns / False Friends   # → fact-graph "x"
| wrong | right | source | confidence |
${x.map((f) => `| ${f.key} | ${JSON.stringify(f.value)} | ${f.source} | ${f.confidence} |`).join("\n")}
${rec.falseFriends.map((ff) => `| ${ff.wrong} | ${ff.right} | ${ff.source} | MEDIUM |`).join("\n")}

## 7. Traits / Interfaces  # → fact-graph "t"
| name | signature_or_contract | source | confidence |
${t.map((f) => `| ${f.key} | ${JSON.stringify(f.value)} | ${f.source} | ${f.confidence} |`).join("\n") || "| (none) | | | |"}

## 8. Per-Axis Findings
${opts.reports.map((rep) => `- ${rep.directiveId} ${rep.status} facts=${rep.facts.length} ${rep.reason ?? ""}`).join("\n")}

## 9. Fact-Graph Seed (draft JSON, ≥ MEDIUM confidence only)
\`\`\`json
${JSON.stringify(seed, null, 2)}
\`\`\`

## 9b. Fact Sources
\`\`\`json
${JSON.stringify(factSources, null, 2)}
\`\`\`

## 10. Provenance
\`\`\`json
${JSON.stringify(provenance, null, 2)}
\`\`\`

## 11. Gaps & Unknowns
${opts.ledger.gaps.map((g) => `- ${g.territory}: ${g.reason}`).join("\n") || "- (none)"}
${rec.demoted.map((f) => `- LOW ${f.section}:${f.key} (${f.source})`).join("\n")}

## 12. Sources
${sources.map((s, i) => `[${i + 1}] ${s}`).join("\n") || "(none)"}

## Ledger
- run_id: ${opts.ledger.run_id}
- decoder_ring_hash: ${opts.ledger.decoder_ring_hash}
- waves: ${opts.ledger.waves}
- censused: ${opts.ledger.censused}
- closed: ${opts.ledger.closed}
- gaps: ${opts.ledger.gaps.length}
- circuitBroken: ${opts.ledger.circuitBroken}
`;
}
