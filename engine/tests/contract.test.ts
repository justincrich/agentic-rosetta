import { describe, expect, test } from "bun:test";
import { DecoderRing, ResearchDirective, WorkerReport } from "../src/contract.ts";
import { withRingHash } from "../src/hash.ts";
import { renderDirective } from "../src/prompts.ts";
import { validateReport } from "../src/worker.ts";

const ring = withRingHash({
  purpose: "A toy widget kit used only in tests.",
  nouns: [{ term: "Widget", means: "the outermost thing", notTobeConfusedWith: "tab" }],
  traps: [{ theyCallIt: "workspace", itActuallyIs: "pane" }],
});

const directive: ResearchDirective = ResearchDirective.parse({
  id: "t01",
  wave: 1,
  territory: {
    kind: "paths",
    label: "README.md",
    include: ["README.md"],
    exclude: [],
    unitCount: 1,
  },
  class: "orientation",
  yields: ["v"],
  question: "What is this?",
  mustFind: ["purpose"],
  priorArt: [],
  decoderRingHash: ring.hash,
});

describe("decoder ring hash", () => {
  test("same input yields the same hash twice", () => {
    const a = withRingHash({ purpose: ring.purpose, nouns: ring.nouns, traps: ring.traps });
    const b = withRingHash({ purpose: ring.purpose, nouns: ring.nouns, traps: ring.traps });
    expect(a.hash).toBe(b.hash);
    expect(a.hash).toBe(ring.hash);
  });
  test("hash changes when purpose changes", () => {
    const other = withRingHash({ purpose: "different", nouns: ring.nouns, traps: ring.traps });
    expect(other.hash).not.toBe(ring.hash);
  });
});

describe("directive → prompt → WorkerReport", () => {
  test("renderDirective embeds the ring verbatim and the class body", () => {
    const prompt = renderDirective(directive, ring);
    expect(prompt).toContain(ring.hash);
    expect(prompt).toContain("toy widget kit");
    expect(prompt).toContain("theyCallIt");
    expect(prompt).toContain("Class-specific charge");
    expect(prompt).toContain("noun hierarchy");
    expect(prompt).toContain("README.md");
    expect(prompt).not.toContain("What rules are enforced"); // enforcement body stays out
  });

  test("a handwritten worker response parses", () => {
    const raw = {
      directiveId: "t01",
      status: "closed",
      reason: null,
      facts: [
        { section: "v", key: "purpose", value: "widget kit", source: "README.md:1", confidence: "HIGH" },
      ],
      surprises: [],
      pointers: [],
      unitsRead: 1,
    };
    const parsed = WorkerReport.parse(raw);
    const v = validateReport(parsed, directive, ring);
    expect(v.ok).toBe(true);
  });

  test("missing source is rejected with a named reason", () => {
    const v = validateReport({
      directiveId: "t01",
      status: "closed",
      reason: null,
      facts: [{ section: "v", key: "purpose", value: "x", source: "", confidence: "HIGH" }],
      surprises: [],
      pointers: [],
      unitsRead: 1,
    }, directive, ring);
    expect(v.ok).toBe(false);
    if (!v.ok) expect(v.reason.toLowerCase()).toMatch(/source/);
  });

  test("locator outside territory.include is rejected", () => {
    const v = validateReport({
      directiveId: "t01",
      status: "closed",
      reason: null,
      facts: [{ section: "v", key: "purpose", value: "x", source: "secret/other.ts:9", confidence: "HIGH" }],
      surprises: [],
      pointers: [],
      unitsRead: 1,
    }, directive, ring);
    expect(v.ok).toBe(false);
    if (!v.ok) expect(v.reason).toContain("outside territory.include");
  });

  test("decoderRingHash mismatch is rejected", () => {
    const other = { ...directive, decoderRingHash: "0".repeat(64) };
    const v = validateReport({
      directiveId: "t01",
      status: "closed",
      reason: null,
      facts: [{ section: "v", key: "purpose", value: "x", source: "README.md:1", confidence: "HIGH" }],
      surprises: [],
      pointers: [],
      unitsRead: 1,
    }, other, ring);
    expect(v.ok).toBe(false);
    if (!v.ok) expect(v.reason).toContain("decoderRingHash mismatch");
  });

  test("empty result with no reason fails", () => {
    const v = validateReport({
      directiveId: "t01",
      status: "closed",
      reason: null,
      facts: [],
      surprises: [],
      pointers: [],
      unitsRead: 0,
    }, directive, ring);
    expect(v.ok).toBe(false);
    if (!v.ok) expect(v.reason).toMatch(/empty result|mustFind/);
  });

  test("DecoderRing schema requires hash", () => {
    expect(() => DecoderRing.parse({ purpose: "p", nouns: [], traps: [] })).toThrow();
  });
});
