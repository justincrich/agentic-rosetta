import { describe, expect, test } from "bun:test";
import { detectHarness, resolveCaller, ResolveError, acpEntrypoint } from "../src/resolve-caller.ts";
import { detectToolLadder } from "../src/tools.ts";

describe("detectHarness", () => {
  test("grok from GROK_AGENT and CMUX_AGENT_LAUNCH_KIND", () => {
    expect(detectHarness({ GROK_AGENT: "1" })).toBe("grok");
    expect(detectHarness({ CMUX_AGENT_LAUNCH_KIND: "grok" })).toBe("grok");
  });
  test("claude from CLAUDECODE / CLAUDE_CODE_EXECPATH", () => {
    expect(detectHarness({ CLAUDECODE: "1" })).toBe("claude");
    expect(detectHarness({ CLAUDE_CODE_EXECPATH: "/usr/bin/claude" })).toBe("claude");
  });
  test("codex / opencode from their env", () => {
    expect(detectHarness({ CODEX_HOME: "/tmp/x" })).toBe("codex");
    expect(detectHarness({ OPENCODE: "1" })).toBe("opencode");
  });
});

describe("resolveCaller", () => {
  test("refuses to run inside a worker", () => {
    expect(() => resolveCaller({ model: "grok-4.6", env: { ROSETTA_WORKER: "1", GROK_AGENT: "1" } })).toThrow(ResolveError);
  });
  test("refuses an unknown model instead of substituting", () => {
    try {
      resolveCaller({ model: "definitely-not-a-model", harness: "grok", env: { GROK_AGENT: "1" } });
      throw new Error("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(ResolveError);
      expect((err as ResolveError).code).toBe("MODEL_UNRESOLVED");
      expect((err as Error).message).not.toContain("falling back");
    }
  });
  test("grok + grok-4.6 resolves an ACP stdio entrypoint", () => {
    const caller = resolveCaller({ model: "grok-4.6", harness: "grok", env: { GROK_AGENT: "1" } });
    expect(caller.harness).toBe("grok");
    expect(caller.model).toBe("grok-4.6");
    expect(caller.acp.command).toContain("grok");
    expect(caller.acp.args).toContain("stdio");
    expect(caller.acp.args).toContain("grok-4.6");
    expect(caller.acp.env.ROSETTA_WORKER).toBe("1");
  });
  test("claude without claude-code-acp fails closed with a named reason", () => {
    try {
      acpEntrypoint("claude", "sonnet");
      // if the adapter is installed this is fine
    } catch (err) {
      expect(err).toBeInstanceOf(ResolveError);
      expect((err as ResolveError).code).toBe("ACP_MISSING");
      expect((err as Error).message).toContain("not ACP");
    }
  });
});

describe("tool ladder", () => {
  test("always includes the harness-native floor", () => {
    const ladder = detectToolLadder({});
    expect(ladder.present).toContain("harness-native");
  });
});
