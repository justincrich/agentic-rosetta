import { describe, expect, test } from "bun:test";
import { execFileSync } from "node:child_process";
import { join } from "node:path";
import { resolveCaller } from "../src/resolve-caller.ts";
import { runWorker } from "../src/worker.ts";
import { ResearchDirective } from "../src/contract.ts";
import { withRingHash } from "../src/hash.ts";

type ProcessRow = {
  pid: number;
  ppid: number;
  command: string;
};

type GrokObservation<T> = {
  result?: T;
  error?: unknown;
  pids: number[];
  maxConcurrent: number;
};

const REPO_ROOT = join(import.meta.dir, "../..");
const LIVE_MODEL = "grok-4.6";

function processRows(): ProcessRow[] {
  const output = execFileSync("/bin/ps", ["-axo", "pid=,ppid=,state=,command="], {
    encoding: "utf8",
  });
  return output.split("\n").flatMap((line) => {
    const match = line.match(/^\s*(\d+)\s+(\d+)\s+\S+\s+(.*)$/);
    if (!match) return [];
    return [{ pid: Number(match[1]), ppid: Number(match[2]), command: match[3] }];
  });
}

function ownedGrokPids(): number[] {
  const rows = processRows();
  const descendants = new Set([process.pid]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const row of rows) {
      if (descendants.has(row.ppid) && !descendants.has(row.pid)) {
        descendants.add(row.pid);
        changed = true;
      }
    }
  }
  return rows
    .filter((row) => descendants.has(row.pid))
    .filter((row) => /(?:^|\/)grok agent\b.*\bstdio\b/.test(row.command))
    .map((row) => row.pid);
}

async function observeOwnedGrok<T>(operation: () => Promise<T>): Promise<GrokObservation<T>> {
  const seen = new Set<number>();
  let maxConcurrent = 0;
  const poll = () => {
    const pids = ownedGrokPids();
    maxConcurrent = Math.max(maxConcurrent, pids.length);
    for (const pid of pids) seen.add(pid);
  };
  poll();
  const timer = setInterval(poll, 5);
  try {
    const result = await operation();
    poll();
    return { result, pids: [...seen], maxConcurrent };
  } catch (error) {
    poll();
    return { error, pids: [...seen], maxConcurrent };
  } finally {
    clearInterval(timer);
  }
}

async function aliveAfter(pids: number[], timeoutMs = 5_000): Promise<number[]> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const live = new Set(processRows().map((row) => row.pid));
    const alive = pids.filter((pid) => live.has(pid));
    if (alive.length === 0) return [];
    await Bun.sleep(25);
  }
  const live = new Set(processRows().map((row) => row.pid));
  return pids.filter((pid) => live.has(pid));
}

async function terminateOwned(pids: number[]): Promise<void> {
  const owned = new Set(ownedGrokPids());
  for (const pid of pids) {
    if (!owned.has(pid)) continue;
    try {
      process.kill(pid, "SIGTERM");
    } catch {
      // Already exited.
    }
  }
  await aliveAfter(pids, 2_000);
}

function directive(id: string, ringHash: string): ResearchDirective {
  return ResearchDirective.parse({
    id,
    territory: {
      kind: "paths",
      label: "README.md",
      include: ["README.md"],
      unitCount: 1,
    },
    class: "orientation",
    yields: ["v"],
    question: "Read README.md and report what this project provides. Cite the README line you used.",
    mustFind: ["purpose"],
    decoderRingHash: ringHash,
  });
}

async function expectStopped(observation: GrokObservation<unknown>): Promise<void> {
  try {
    expect(observation.error).toBeUndefined();
    expect(observation.pids.length).toBeGreaterThan(0);
    expect(await aliveAfter(observation.pids)).toEqual([]);
  } finally {
    await terminateOwned(observation.pids);
  }
}

describe("real Grok ACP worker lifecycle", () => {
  test("stops the real ACP child after a valid worker report", async () => {
    const caller = resolveCaller({ model: LIVE_MODEL, harness: "grok" });
    const ring = withRingHash({
      purpose: "Verify the project purpose from README.md.",
      nouns: [{ term: "Rosetta", means: "the project documented in README.md" }],
      traps: [],
    });
    const observation = await observeOwnedGrok(() =>
      runWorker(directive("lifecycle-success", ring.hash), ring, caller, REPO_ROOT),
    );
    expect(observation.result?.ok).toBe(true);
    await expectStopped(observation);
  }, 240_000);

  test("uses a fresh real ACP child for the validation retry and stops both", async () => {
    const caller = resolveCaller({ model: LIVE_MODEL, harness: "grok" });
    const ring = withRingHash({
      purpose: "Verify retry cleanup while reading README.md.",
      nouns: [],
      traps: [],
    });
    const wrongHash = "0".repeat(64);
    expect(wrongHash).not.toBe(ring.hash);
    const observation = await observeOwnedGrok(() =>
      runWorker(directive("lifecycle-retry", wrongHash), ring, caller, REPO_ROOT),
    );
    expect(observation.result?.ok).toBe(false);
    if (observation.result && !observation.result.ok) {
      expect(observation.result.reason).toContain("directive decoderRingHash mismatch");
    }
    expect(observation.pids.length).toBeGreaterThanOrEqual(2);
    expect(observation.maxConcurrent).toBe(1);
    await expectStopped(observation);
  }, 360_000);

  test("stops the real ACP child after invalid-model initialization fails", async () => {
    const caller = resolveCaller({
      model: "definitely-not-a-real-grok-model-rosetta",
      harness: "grok",
    });
    const ring = withRingHash({
      purpose: "Verify invalid model initialization cleanup.",
      nouns: [],
      traps: [],
    });
    const observation = await observeOwnedGrok(() =>
      runWorker(directive("lifecycle-invalid-model", ring.hash), ring, caller, REPO_ROOT),
    );
    expect(observation.result?.ok).toBe(false);
    await expectStopped(observation);
    if (observation.result && !observation.result.ok) {
      // Preserve the existing classifier result for Grok's live initialization
      // failure; classification changes are outside this lifecycle fix.
      expect(observation.result.kind).toBe("AUTH");
      expect(observation.result.reason).toContain(caller.model);
    }
  }, 120_000);

  test("aborts a short deadline, disconnects, then retries without overlap", async () => {
    const caller = resolveCaller({ model: LIVE_MODEL, harness: "grok" });
    const ring = withRingHash({
      purpose: "Verify deadline cleanup before retry.",
      nouns: [],
      traps: [],
    });
    const previous = process.env.ROSETTA_WORKER_TIMEOUT_MS;
    process.env.ROSETTA_WORKER_TIMEOUT_MS = "1000";
    let observation: GrokObservation<Awaited<ReturnType<typeof runWorker>>>;
    try {
      observation = await observeOwnedGrok(() =>
        runWorker(directive("lifecycle-timeout", ring.hash), ring, caller, REPO_ROOT),
      );
    } finally {
      if (previous === undefined) delete process.env.ROSETTA_WORKER_TIMEOUT_MS;
      else process.env.ROSETTA_WORKER_TIMEOUT_MS = previous;
    }
    expect(observation.result?.ok).toBe(false);
    if (observation.result && !observation.result.ok) {
      expect(observation.result.kind).toBe("GENERIC");
      expect(observation.result.reason).toContain("worker timeout after 1000ms");
    }
    expect(observation.pids.length).toBeGreaterThanOrEqual(2);
    expect(observation.maxConcurrent).toBe(1);
    await expectStopped(observation);
  }, 30_000);
});
