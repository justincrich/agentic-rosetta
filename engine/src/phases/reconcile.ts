import type { Fact, WorkerReport } from "../contract.ts";

export type Precedence =
  | "running"
  | "source"
  | "enforcement"
  | "docs"
  | "marketing";

const RANK: Record<Precedence, number> = {
  running: 5,
  source: 4,
  enforcement: 3,
  docs: 2,
  marketing: 1,
};

export function sourcePrecedence(source: string, cls?: string): Precedence {
  if (cls === "enforcement" || /\/scripts\/(lint|check|assert)-|\.github\/workflows\//.test(source)) {
    return "enforcement";
  }
  if (cls === "scars") return "source";
  if (/\.(ts|js|rs|go|py|swift|kt)$/.test(source.split(":")[0])) return "source";
  if (/changelog|migration/i.test(source)) return "source";
  if (/readme|about|landing/i.test(source)) return "marketing";
  if (/docs\/|https?:/.test(source)) return "docs";
  return "source";
}

export type Reconciled = {
  promoted: Fact[];
  falseFriends: { wrong: string; right: string; why: string; source: string }[];
  demoted: Fact[];
};

export function reconcile(reports: WorkerReport[]): Reconciled {
  const byKey = new Map<string, Fact[]>();
  for (const r of reports) {
    for (const f of r.facts) {
      const k = `${f.section}:${f.key}`;
      const arr = byKey.get(k) ?? [];
      arr.push(f);
      byKey.set(k, arr);
    }
  }
  const promoted: Fact[] = [];
  const demoted: Fact[] = [];
  const falseFriends: Reconciled["falseFriends"] = [];

  for (const [, facts] of byKey) {
    const usable = facts.filter((f) => f.confidence !== "LOW");
    const low = facts.filter((f) => f.confidence === "LOW");
    demoted.push(...low);
    if (usable.length === 0) continue;
    usable.sort((a, b) => RANK[sourcePrecedence(b.source)] - RANK[sourcePrecedence(a.source)]);
    const winner = usable[0];
    promoted.push(winner);
    for (const other of usable.slice(1)) {
      if (JSON.stringify(other.value) !== JSON.stringify(winner.value)) {
        falseFriends.push({
          wrong: `${other.key}=${JSON.stringify(other.value)} (from ${other.source})`,
          right: `${winner.key}=${JSON.stringify(winner.value)} (from ${winner.source})`,
          why: `precedence: ${sourcePrecedence(winner.source)} beats ${sourcePrecedence(other.source)}`,
          source: winner.source,
        });
      }
    }
  }

  for (const r of reports) {
    for (const s of r.surprises) {
      falseFriends.push({
        wrong: s.claim,
        right: s.contradicts,
        why: "worker surprise — a claim an agent would plausibly believe",
        source: s.source,
      });
    }
  }

  return { promoted, falseFriends, demoted };
}
