import { describe, expect, test } from "bun:test";
import { mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import { runResearch } from "../src/index.ts";
import { resolveCaller } from "../src/resolve-caller.ts";
import { withRingHash } from "../src/hash.ts";
import { ResearchDirective } from "../src/contract.ts";
import { buildLedger, assertClosed, LedgerOpenError } from "../src/phases/ledger.ts";
import { ResearchRequest } from "../src/contract.ts";

function tinyRepo(): string {
  const root = join(tmpdir(), `rosetta-run-${Date.now()}`);
  mkdirSync(root, { recursive: true });
  writeFileSync(join(root, "README.md"), "# Tiny\n\nA widget kit.\n");
  writeFileSync(join(root, "CONTRIBUTING.md"), "no stubs\n");
  spawnSync("git", ["init"], { cwd: root });
  spawnSync("git", ["add", "-A"], { cwd: root });
  spawnSync("git", ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "i"], { cwd: root });
  return root;
}

describe("runResearch census-only + topic skip", () => {
  test("census-only on a real temp repo produces directives and spawns nothing", async () => {
    const root = tinyRepo();
    try {
      const caller = resolveCaller({ model: "grok-4.6", harness: "grok" });
      const result = await runResearch({
        tech: "Tiny",
        techSlug: "tiny",
        sourceMode: "repo",
        sources: { repoPath: root },
        caller,
        censusOnly: true,
        depth: "normal",
      });
      expect(result.status).not.toBe("BLOCKED");
      expect(result.ledger.censused).toBeGreaterThan(0);
      expect(result.reports.length).toBe(0);
      expect(result.ledger.gaps.some((g) => g.reason.includes("census-only"))).toBe(true);
      expect(result.nrra).toContain("run_id:");
      expect(result.nrra).toContain("decoder_ring_hash:");
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  test("supplied directives skip census (topic mode) and the run still closes", async () => {
    const caller = resolveCaller({ model: "grok-4.6", harness: "grok" });
    const ring = withRingHash({ purpose: "topic mode", nouns: [], traps: [] });
    const d = ResearchDirective.parse({
      id: "topic-01",
      territory: { kind: "urls", label: "state", include: ["https://example.com/state"], unitCount: 1 },
      class: "reference",
      yields: ["w", "a"],
      question: "state management",
      decoderRingHash: ring.hash,
    });
    const result = await runResearch({
      tech: "Tiny",
      techSlug: "tiny",
      sourceMode: "docs",
      sources: { docsUrl: "https://example.com" },
      caller,
      decoderRing: ring,
      directives: [d],
      budget: { maxUsd: 0 },
    });
    // maxUsd=0 trips the breaker: no workers, remainder is gaps, ledger still accounts every row
    expect(result.ledger.censused).toBe(1);
    expect(result.ledger.circuitBroken).toBe(true);
    expect(result.ledger.gaps.some((g) => g.reason.includes("circuit-breaker"))).toBe(true);
    expect(result.reports.length).toBe(0);
    const closedOrGap = result.ledger.rows.filter((r) => r.status === "closed" || r.status === "gap").length;
    expect(closedOrGap).toBe(result.ledger.censused);
  });
});

describe("ledger 1:1:1:1 and closure", () => {
  test("one directive → one row; open ledger throws", () => {
    const ring = withRingHash({ purpose: "p", nouns: [], traps: [] });
    const req = ResearchRequest.parse({
      tech: "T",
      techSlug: "t",
      sourceMode: "repo",
      sources: { repoPath: "/tmp/nope" },
      caller: resolveCaller({ model: "grok-4.6", harness: "grok" }),
    });
    const d = ResearchDirective.parse({
      id: "t01",
      territory: { kind: "paths", label: "README.md", include: ["README.md"], unitCount: 1 },
      class: "orientation",
      yields: ["v"],
      question: "q",
      decoderRingHash: ring.hash,
    });
    const report = {
      directiveId: "t01",
      status: "closed" as const,
      reason: null,
      facts: [{ section: "v" as const, key: "purpose", value: "x", source: "README.md:1", confidence: "HIGH" as const }],
      surprises: [],
      pointers: [],
      unitsRead: 1,
    };
    const closed = buildLedger({
      runId: "r1",
      req,
      ringHash: ring.hash,
      censused: [d],
      reports: [report],
      gaps: [],
      waves: 1,
    });
    expect(closed.rows.length).toBe(1);
    expect(closed.closed).toBe(1);
    expect(closed.censused).toBe(1);
    assertClosed(closed, 0);

    const open = buildLedger({
      runId: "r2",
      req,
      ringHash: ring.hash,
      censused: [d, { ...d, id: "t02" }],
      reports: [report],
      gaps: [],
      waves: 1,
    });
    expect(open.rows.length).toBe(2);
    expect(() => assertClosed(open, 1)).toThrow(LedgerOpenError);
  });
});
