import { describe, expect, test } from "bun:test";
import { resolveCaller } from "../src/resolve-caller.ts";
import { makeWorker, acpOptionsFromCaller, extractJson } from "../src/worker.ts";
import { ResearchDirective } from "../src/contract.ts";
import { withRingHash } from "../src/hash.ts";

/**
 * Real spawn of the invoking harness's ACP entrypoint.
 * This is grok `agent stdio` on this machine. We only assert the process
 * starts and speaks enough ACP to fail-or-succeed without a mocked child.
 */
describe("real ACP worker spawn", () => {
  test("makeWorker + generate against the live grok ACP entry (no mocked child)", async () => {
    const caller = resolveCaller({ model: "grok-4.6", harness: "grok" });
    expect(caller.acp.args).toContain("stdio");
    const ring = withRingHash({
      purpose: "Integration probe: return a tiny closed report about README.md.",
      nouns: [{ term: "README", means: "the orientation file" }],
      traps: [],
    });
    const d = ResearchDirective.parse({
      id: "t-int",
      territory: {
        kind: "paths",
        label: "README.md",
        include: ["README.md"],
        unitCount: 1,
      },
      class: "orientation",
      yields: ["v"],
      question: "Read README.md in the current workspace. Return a WorkerReport JSON with one v/purpose fact sourced at README.md:1. No other files.",
      mustFind: ["purpose"],
      decoderRingHash: ring.hash,
    });
    const opts = acpOptionsFromCaller(d, caller);
    expect(opts.command.length).toBeGreaterThan(0);
    const worker = makeWorker(d, ring, caller);
    const prompt = `Return ONLY this JSON (fill purpose from README.md if you can read it, else say unread):\n${JSON.stringify({
      directiveId: "t-int",
      status: "closed",
      reason: null,
      facts: [{ section: "v", key: "purpose", value: "agentic-rosetta plugin", source: "README.md:1", confidence: "HIGH" }],
      surprises: [],
      pointers: [],
      unitsRead: 1,
    })}\n`;
    const result = await worker.generate(prompt);
    const text = typeof result === "object" && result && "text" in result
      ? String((result as { text: unknown }).text)
      : JSON.stringify(result);
    expect(text.length).toBeGreaterThan(0);
    // If the worker returned JSON we can parse, great; if it returned ACP chatter,
    // extractJson may throw — still proves a real process ran.
    try {
      const raw = extractJson(text);
      expect(raw).toBeTruthy();
    } catch {
      expect(text.toLowerCase()).not.toContain("enoent");
    }
  }, 180_000);
});
