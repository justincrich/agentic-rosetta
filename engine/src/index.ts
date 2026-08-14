import { randomUUID } from "node:crypto";
import {
  ResearchRequest,
  ResearchResult,
  type ProvenanceManifest,
  type ResearchDirective,
  type SourcePin,
  type WorkerReport,
} from "./contract.ts";
import { preflight } from "./phases/preflight.ts";
import { orient } from "./phases/orient.ts";
import { runCensus } from "./phases/census.ts";
import { fanoutWave } from "./phases/fanout.ts";
import { assertClosed, buildLedger, LedgerOpenError } from "./phases/ledger.ts";
import { emitNrra } from "./phases/emit.ts";
import { bindDepth } from "./phases/census.ts";
import { corpusDigest } from "./hash.ts";
import type { LedgerGap } from "./contract.ts";

export { ResearchRequest, ResearchResult, ResearchDirective, CallerIdentity, DecoderRing, WorkerReport, Ledger } from "./contract.ts";
export { resolveCaller } from "./resolve-caller.ts";
export { makeWorker, validateReport, acpOptionsFromCaller } from "./worker.ts";
export { renderDirective } from "./prompts.ts";
export { runCensus, classifyPath, listRepoFiles, bindDepth, pinRepo, pinDocs, resolveRepoTree } from "./phases/census.ts";
export { corpusDigest, sha256, sha256Bytes } from "./hash.ts";

export function rollupProvenance(pins: SourcePin[]): ProvenanceManifest {
  const units = pins.flatMap((p) =>
    (p.unitPins ?? []).map((u) => ({ locator: u.locator, sha256: u.sha256 })),
  );
  return {
    pins,
    corpus_sha256: pins.length === 1 ? pins[0].corpus_sha256 : corpusDigest(units),
    fetched_at: new Date().toISOString(),
  };
}
export { preflight } from "./phases/preflight.ts";

export async function runResearch(input: unknown): Promise<ResearchResult> {
  const req = ResearchRequest.parse(input);
  const runId = randomUUID();

  const pf = preflight(req);
  if (!pf.ok) {
    const empty = {
      run_id: runId,
      tech: req.tech,
      source_mode: req.sourceMode,
      decoder_ring_hash: "",
      waves: 0,
      censused: 0,
      closed: 0,
      rows: [],
      gaps: [{ territory: "*", reason: pf.reason }],
      merges: [],
      warnings: [],
      circuitBroken: false,
    };
    return ResearchResult.parse({
      runId,
      status: "BLOCKED",
      nrra: `---\nartifact: rosetta-research\nstatus: BLOCKED\nrun_id: "${runId}"\nreason: "${pf.reason}"\n---\n\n# BLOCKED\n\n${pf.reason}\n`,
      ledger: empty,
      reports: [],
      blockReason: `${pf.code}: ${pf.reason}`,
    });
  }

  const ring = await orient(req);
  const depth = bindDepth(req);

  let directives: ResearchDirective[];
  const gaps: LedgerGap[] = [];
  const warnings: string[] = [];
  const merges: { kept: string; absorbed: string; class: import("./contract.ts").TerritoryClass; reason: string }[] = [];
  let pins: SourcePin[] = [];

  if (req.directives && req.directives.length && !req.probeSources) {
    directives = req.directives.map((d) => ({ ...d, decoderRingHash: d.decoderRingHash || ring.hash }));
  } else {
    const census = await runCensus(req, ring);
    directives = req.directives?.length
      ? req.directives.map((d) => ({ ...d, decoderRingHash: d.decoderRingHash || ring.hash }))
      : census.directives;
    gaps.push(...census.gaps);
    warnings.push(...census.warnings);
    merges.push(...census.merges);
    pins = census.pins;
  }

  const provenance = pins.length ? rollupProvenance(pins) : undefined;

  if (req.censusOnly || req.probeSources) {
    const ledger = buildLedger({
      runId,
      req,
      ringHash: ring.hash,
      censused: directives,
      reports: [],
      gaps: [
        ...gaps,
        ...directives.map((d) => ({
          territory: d.territory.label,
          reason: req.probeSources ? "probe-sources: workers not spawned" : "census-only: workers not spawned",
          directiveId: d.id,
        })),
      ],
      waves: 0,
      merges,
      warnings,
      provenance,
    });
    return ResearchResult.parse({
      runId,
      status: "partial",
      nrra: emitNrra({ req, ring, ledger, reports: [], runId, status: "partial", pins }),
      ledger,
      reports: [],
      provenance,
    });
  }

  const allReports: WorkerReport[] = [];
  let pending = directives;
  let wave = 0;
  let spent = 0;
  let circuitBroken = false;
  const seen = new Set(directives.map((d) => d.id));
  const allCensused = [...directives];

  while (pending.length && wave < depth.maxWaves) {
    wave += 1;
    if (req.budget.maxUsd !== undefined && spent >= req.budget.maxUsd) {
      circuitBroken = true;
      for (const d of pending) {
        gaps.push({
          territory: d.territory.label,
          reason: `circuit-breaker: budget.maxUsd=${req.budget.maxUsd}`,
          directiveId: d.id,
        });
      }
      pending = [];
      break;
    }
    const result = await fanoutWave(pending, ring, req.caller, req, spent);
    spent = result.spentUsd;
    allReports.push(...result.reports);
    gaps.push(...result.gaps);
    if (result.gaps.some((g) => g.reason.startsWith("circuit-breaker"))) circuitBroken = true;
    const next = result.nextDirectives.filter((d) => {
      const key = d.territory.include.slice().sort().join("|");
      if (seen.has(key) || seen.has(d.id)) return false;
      seen.add(key);
      seen.add(d.id);
      return true;
    });
    if (wave >= depth.maxWaves && next.length) {
      for (const d of next) {
        gaps.push({ territory: d.territory.label, reason: `unabsorbed pointer at maxWaves=${depth.maxWaves}` });
      }
      pending = [];
      break;
    }
    for (const d of next) allCensused.push(d);
    pending = next;
  }

  const ledger = buildLedger({
    runId,
    req,
    ringHash: ring.hash,
    censused: allCensused,
    reports: allReports,
    gaps,
    waves: wave,
    merges,
    warnings,
    spentUsd: spent,
    circuitBroken,
    provenance,
  });

  try {
    assertClosed(ledger, pending.length);
  } catch (err) {
    if (err instanceof LedgerOpenError) {
      return ResearchResult.parse({
        runId,
        status: "BLOCKED",
        nrra: emitNrra({ req, ring, ledger, reports: allReports, runId, status: "BLOCKED", pins }),
        ledger,
        reports: allReports,
        blockReason: err.message,
        provenance,
      });
    }
    throw err;
  }

  const status = ledger.gaps.length || circuitBroken ? "partial" : "complete";
  return ResearchResult.parse({
    runId,
    status,
    nrra: emitNrra({ req, ring, ledger, reports: allReports, runId, status, pins }),
    ledger,
    reports: allReports,
    provenance,
  });
}
