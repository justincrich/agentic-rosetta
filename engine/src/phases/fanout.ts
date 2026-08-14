import type {
  CallerIdentity,
  DecoderRing,
  LedgerGap,
  ResearchDirective,
  ResearchRequest,
  WorkerReport,
} from "../contract.ts";
import { classifyPath, classifyUrl, groupIntoDirectives, unitsForTerritory } from "./census.ts";
import { runWorker } from "../worker.ts";

export type FanoutResult = {
  reports: WorkerReport[];
  gaps: LedgerGap[];
  nextDirectives: ResearchDirective[];
  spentUsd: number;
};

async function mapPool<T, R>(items: T[], limit: number, fn: (item: T) => Promise<R>): Promise<R[]> {
  const out: R[] = new Array(items.length);
  let i = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (i < items.length) {
      const idx = i++;
      out[idx] = await fn(items[idx]);
    }
  });
  await Promise.all(workers);
  return out;
}

export async function fanoutWave(
  directives: ResearchDirective[],
  ring: DecoderRing,
  caller: CallerIdentity,
  req: ResearchRequest,
  alreadySpent: number,
): Promise<FanoutResult> {
  const gaps: LedgerGap[] = [];
  const reports: WorkerReport[] = [];
  let spent = alreadySpent;
  const runnable: ResearchDirective[] = [];

  for (const d of directives) {
    if (req.budget.maxUsd !== undefined && spent >= req.budget.maxUsd) {
      gaps.push({
        territory: d.territory.label,
        reason: `circuit-breaker: budget.maxUsd=${req.budget.maxUsd} already spent=${spent}`,
        directiveId: d.id,
      });
    } else {
      runnable.push(d);
    }
  }

  const results = await mapPool(runnable, req.concurrency, async (d) => {
    const result = await runWorker(d, ring, caller);
    return { d, result };
  });

  const pointers: { territory: string; why: string; suggestedClass: string }[] = [];
  for (const { d, result } of results) {
    // Nominal cost unit so the breaker is real even without a billing API.
    spent += 1;
    if (req.budget.maxUsd !== undefined && spent > req.budget.maxUsd) {
      gaps.push({
        territory: d.territory.label,
        reason: `circuit-breaker tripped mid-wave (maxUsd=${req.budget.maxUsd})`,
        directiveId: d.id,
      });
      continue;
    }
    if (!result.ok) {
      gaps.push({
        territory: d.territory.label,
        reason: `worker rejected after retry: ${result.reason}`,
        directiveId: d.id,
      });
      continue;
    }
    reports.push(result.report);
    for (const p of result.report.pointers) pointers.push(p);
  }

  const nextDirectives: ResearchDirective[] = [];
  if (pointers.length) {
    const files = pointers.map((p) => ({
      rel: p.territory,
      bytes: 0,
      class: p.territory.startsWith("http") ? classifyUrl(p.territory) : classifyPath(p.territory),
    }));
    const kind = files[0]?.rel.startsWith("http") ? "urls" : "paths";
    const grouped = groupIntoDirectives(files, {
      kind,
      ringHash: ring.hash,
      maxUnits: unitsForTerritory(kind, req),
      maxBytes: req.maxBytes,
      maxDirectives: req.budget.maxDirectives,
      allowed: new Set(["orientation", "enforcement", "scars", "contracts", "exemplars", "reference", "unclassified"]),
      wave: (directives[0]?.wave ?? 1) + 1,
    });
    nextDirectives.push(...grouped.directives);
    gaps.push(...grouped.gaps.map((g) => ({ territory: g.territory, reason: g.reason })));
  }

  return { reports, gaps, nextDirectives, spentUsd: spent };
}
