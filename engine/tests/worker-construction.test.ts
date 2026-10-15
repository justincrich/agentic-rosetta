import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  acpOptionsFromCaller,
  applyModelViaConfigOption,
  DEFAULT_WORKER_TIMEOUT_MS,
  makeWorker,
} from "../src/worker.ts";
import { ResearchDirective } from "../src/contract.ts";
import { withRingHash } from "../src/hash.ts";
import { CallerIdentity } from "../src/contract.ts";

const src = readFileSync(join(import.meta.dir, "../src/worker.ts"), "utf8");

describe("makeWorker is constructed only from caller.acp", () => {
  test("source contains a single new AcpAgent and it reads caller.acp", () => {
    const matches = src.match(/new AcpAgent\(/g) ?? [];
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
    // The caller's env passes through untouched EXCEPT the harness nesting guards,
    // which are deliberately neutralized so the child is not refused as a nested
    // session. Asserted exactly, so any future smuggled key still fails this test.
    expect(opts.env).toEqual({ A: "1", CLAUDECODE: "", CLAUDE_CODE_ENTRYPOINT: "" });
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

describe("nesting guards are stripped from the worker env", () => {
  test("CLAUDECODE / CLAUDE_CODE_ENTRYPOINT are neutralized for every harness", () => {
    for (const harness of ["claude", "grok", "opencode"] as const) {
      const caller = {
        harness,
        model: "opus[1m]",
        acp: { command: "/bin/true", args: [], env: { ROSETTA_WORKER: "1" } },
      } as never;
      const opts = acpOptionsFromCaller(
        { id: "t01", class: "reference" } as never,
        caller,
      );
      expect(opts.env?.CLAUDECODE).toBe("");
      expect(opts.env?.CLAUDE_CODE_ENTRYPOINT).toBe("");
      // the caller's own env survives
      expect(opts.env?.ROSETTA_WORKER).toBe("1");
    }
  });
});

describe("opencode workers select the model via session/set_config_option", () => {
  // opencode acp (v2.0.29) registers session/set_mode and session/set_config_option
  // but NOT session/set_model. @mastra/acp sends session/set_model whenever
  // `options.model` is set, which hard-fails session init with
  // `Method not found: session/set_model` and killed a whole research fanout
  // (livekit refresh, 2026-10-15). Regression cover for the fix.
  test("acpOptionsFromCaller leaves opts.model unset for the opencode harness", () => {
    const caller = CallerIdentity.parse({
      harness: "opencode",
      model: "xiaomi-token-plan-sgp/mimo-v2.6-flash",
      acp: { command: "/sentinel/bin/opencode", args: ["acp"], env: {} },
    });
    const opts = acpOptionsFromCaller({ id: "t02", class: "reference" } as never, caller);
    // unset, not defaulted — @mastra/acp must never see a `model` to send
    expect(opts.model).toBeUndefined();
  });

  test("makeWorker installs an instance-level initializeSession override (does not spawn)", () => {
    const ring = withRingHash({ purpose: "p", nouns: [], traps: [] });
    const d = ResearchDirective.parse({
      id: "t03",
      territory: { kind: "paths", label: "README.md", include: ["README.md"], unitCount: 1 },
      class: "orientation",
      yields: ["v"],
      question: "q",
      decoderRingHash: ring.hash,
    });
    const caller = CallerIdentity.parse({
      harness: "opencode",
      model: "xiaomi-token-plan-sgp/mimo-v2.6-flash",
      acp: { command: "/bin/true", args: ["acp"], env: {} },
    });
    const agent = makeWorker(d, ring, caller) as unknown as {
      connection: {
        initializeSession?: () => Promise<void>;
        constructor: { prototype: { initializeSession: () => Promise<void> } };
      };
    };
    const conn = agent.connection;
    // own property on the instance => the prototype method is wrapped, not replaced globally
    expect(Object.hasOwn(conn, "initializeSession")).toBe(true);
    expect(conn.initializeSession).not.toBe(conn.constructor.prototype.initializeSession);
  });

  test("applyModelViaConfigOption sends session/set_config_option and verifies the echo", async () => {
    const calls: unknown[] = [];
    const fake = {
      session: { sessionId: "ses_1" },
      connection: {
        setSessionConfigOption: async (params: unknown) => {
          calls.push(params);
          return { configOptions: [{ id: "model", currentValue: "acme/model-x" }] };
        },
      },
    };
    await applyModelViaConfigOption(fake, "acme/model-x");
    expect(calls).toEqual([
      { sessionId: "ses_1", configId: "model", value: "acme/model-x" },
    ]);
  });

  test("applyModelViaConfigOption fails closed when the session lands on a different model", async () => {
    const fake = {
      session: { sessionId: "ses_2" },
      connection: {
        setSessionConfigOption: async () => ({
          configOptions: [{ id: "model", currentValue: "somebody/else" }],
        }),
      },
    };
    await expect(applyModelViaConfigOption(fake, "acme/model-x")).rejects.toThrow(
      /model mismatch/i,
    );
  });

  test("applyModelViaConfigOption fails closed when the seam is missing", async () => {
    // @mastra/acp private-method drift => named error, never a silent default model
    const fake = { session: { sessionId: "ses_3" }, connection: {} };
    await expect(applyModelViaConfigOption(fake, "acme/model-x")).rejects.toThrow(
      /setSessionConfigOption/i,
    );
    const noSession = { session: undefined, connection: { setSessionConfigOption: async () => ({}) } };
    await expect(applyModelViaConfigOption(noSession, "acme/model-x")).rejects.toThrow(
      /session/i,
    );
  });
});
