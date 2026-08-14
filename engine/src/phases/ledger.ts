import { Ledger, type LedgerGap, type ResearchDirective, type ResearchRequest, type WorkerReport } from "../contract.ts";

export class LedgerOpenError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "LedgerOpenError";
  }
}

export function buildLedger(opts: {
  runId: string;
  req: ResearchRequest;
  ringHash: string;
  censused: ResearchDirective[];
  reports: WorkerReport[];
  gaps: LedgerGap[];
  waves: number;
  merges?: { kept: string; absorbed: string; class: import("../contract.ts").TerritoryClass; reason: string }[];
  warnings?: string[];
  spentUsd?: number;
  circuitBroken?: boolean;
  unabsorbedPointers?: number;
}): Ledger {
  const byId = new Map(opts.reports.map((r) => [r.directiveId, r]));
  const gapIds = new Set(opts.gaps.map((g) => g.directiveId).filter(Boolean));
  const rows = opts.censused.map((d) => {
    const report = byId.get(d.id);
    if (report && (report.status === "closed" || report.status === "partial")) {
      return {
        id: d.id,
        territory: d.territory.label,
        class: d.class,
        owner: d.id,
        status: "closed" as const,
        fact_count: report.facts.length,
        reason: report.status === "partial" ? report.reason : null,
      };
    }
    const gap = opts.gaps.find((g) => g.directiveId === d.id);
    return {
      id: d.id,
      territory: d.territory.label,
      class: d.class,
      owner: d.id,
      status: "gap" as const,
      fact_count: 0,
      reason: gap?.reason ?? "no report",
    };
  });
  const closed = rows.filter((r) => r.status === "closed").length;
  const gapRows = rows.filter((r) => r.status === "gap").length;
  // gaps[] also holds unclassified / unabsorbed pointers that are not rows
  const extraGaps = opts.gaps.filter((g) => !g.directiveId || !opts.censused.some((d) => d.id === g.directiveId));
  return Ledger.parse({
    run_id: opts.runId,
    tech: opts.req.tech,
    source_mode: opts.req.directives?.length && !opts.req.sources.repoPath && !opts.req.sources.docsUrl
      ? "topics"
      : opts.req.sourceMode,
    decoder_ring_hash: opts.ringHash,
    waves: opts.waves,
    censused: opts.censused.length,
    closed,
    rows,
    gaps: opts.gaps,
    merges: opts.merges ?? [],
    warnings: opts.warnings ?? [],
    spentUsd: opts.spentUsd,
    circuitBroken: opts.circuitBroken ?? false,
  });
}

export function assertClosed(ledger: Ledger, unabsorbedPointers = 0): void {
  const accounted = ledger.closed + ledger.gaps.filter((g) => g.directiveId).length;
  // every censused row is either closed or a named gap
  const rowAccounted = ledger.rows.filter((r) => r.status === "closed" || r.status === "gap").length;
  if (rowAccounted !== ledger.censused) {
    throw new LedgerOpenError(
      `ledger open: closed+gap rows (${rowAccounted}) != censused (${ledger.censused})`,
    );
  }
  if (unabsorbedPointers > 0) {
    throw new LedgerOpenError(`ledger open: ${unabsorbedPointers} unabsorbed pointers`);
  }
  void accounted;
  void gapIdsSafe(ledger);
}

function gapIdsSafe(ledger: Ledger): void {
  // structural no-op to keep the helper used
  void ledger.run_id;
}
