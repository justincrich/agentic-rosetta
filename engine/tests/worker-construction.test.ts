import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { acpOptionsFromCaller, DEFAULT_WORKER_TIMEOUT_MS, makeWorker } from "../src/worker.ts";
import { ResearchDirective } from "../src/contract.ts";
import { withRingHash } from "../src/hash.ts";
import { CallerIdentity } from "../src/contract.ts";

const src = readFileSync(join(import.meta.dir, "../src/worker.ts"), "utf8");

describe("makeWorker is constructed only from caller.acp", () => {
  test("source contains a single new AcpAgent and it reads caller.acp", () => {
    const matches = src.match(/return new AcpAgent/g) ?? [];
    expect(matches.length).toBe(1);
    expect(src).toContain("caller.acp.command");
    expect(src).toContain("caller.acp.args");
    expect(src).not.toContain('command: "grok"');
    expect(src).not.toContain("command: 'claude'");
  });

  test("runtime options are exactly the caller's command/args/model", () => {
    const caller = CallerIdentity.parse({
      harness: "grok",
      model: "sentinel-model",
      acp: { command: "/sentinel/bin/acp", args: ["--flag", "x"], env: { A: "1" } },
    });
    const ring = withRingHash({ purpose: "p", nouns: [], traps: [] });
    const d = ResearchDirective.parse({
      id: "t99",
      territory: { kind: "paths", label: "README.md", include: ["README.md"], unitCount: 1 },
      class: "orientation",
      yields: ["v"],
      question: "q",
      decoderRingHash: ring.hash,
    });
    const opts = acpOptionsFromCaller(d, caller);
    expect(opts.command).toBe("/sentinel/bin/acp");
    expect(opts.args).toEqual(["--flag", "x"]);
    expect(opts.model).toBe("sentinel-model");
    expect(opts.env).toEqual({ A: "1" });
  });

  test("pi harness: model goes via PI_MODEL/PI_PROVIDER env, not the ACP client", () => {
    const caller = CallerIdentity.parse({
      harness: "pi",
      model: "deepseek/deepseek-v4-flash",
      acp: { command: "/sentinel/bin/pi-acp", args: [], env: {} },
    });
    const ring = withRingHash({ purpose: "p", nouns: [], traps: [] });
    const d = ResearchDirective.parse({
      id: "t100",
      territory: { kind: "paths", label: "README.md", include: ["README.md"], unitCount: 1 },
      class: "orientation",
      yields: ["v"],
      question: "q",
      decoderRingHash: ring.hash,
    });
    const opts = acpOptionsFromCaller(d, caller);
    expect(opts.model).toBeUndefined();
    expect(opts.env?.PI_PROVIDER).toBe("deepseek");
    expect(opts.env?.PI_MODEL).toBe("deepseek-v4-flash");
  });

  test("default worker timeout is 10 minutes (docs leaves cannot finish in 180s)", () => {
    expect(DEFAULT_WORKER_TIMEOUT_MS).toBe(600_000);
    expect(src).toContain("DEFAULT_WORKER_TIMEOUT_MS");
    expect(src).not.toMatch(/ROSETTA_WORKER_TIMEOUT_MS \?\? 180_000/);
  });

  test("makeWorker is a function (do not spawn AcpAgent in this file)", () => {
    expect(typeof makeWorker).toBe("function");
  });
});
