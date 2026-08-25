import { describe, expect, test } from "bun:test";
import { classifyWorkerError, WorkerErrorKind } from "../src/worker.ts";
import { CallerIdentity } from "../src/contract.ts";

const caller = CallerIdentity.parse({
  harness: "grok",
  model: "grok-4.6",
  acp: { command: "grok", args: ["agent", "-m", "grok-4.6", "--always-approve", "stdio"], env: {} },
});

function kind(err: unknown): WorkerErrorKind {
  return classifyWorkerError(err, caller).kind;
}

describe("classifyWorkerError", () => {
  test("AUTH from message text", () => {
    expect(kind(new Error("401 unauthorized: invalid api key"))).toBe("AUTH");
    expect(kind(new Error("authentication failed"))).toBe("AUTH");
    expect(kind({ message: "invalid api key supplied" })).toBe("AUTH");
  });
  test("AUTH from http_status 401 / 403 even with a terse message", () => {
    expect(kind({ message: "forbidden", http_status: 403 })).toBe("AUTH");
    expect(kind({ message: "nope", data: { message: "nope", http_status: 401 } })).toBe("AUTH");
  });
  test("AUTH from stderr", () => {
    expect(kind({ stderr: "AuthenticationError: api key rejected" })).toBe("AUTH");
  });
  test("MODEL from unknown-model variants", () => {
    expect(kind(new Error("model grok-4.6 not found"))).toBe("MODEL");
    expect(kind(new Error("unknown model: definitely-not-a-model"))).toBe("MODEL");
    expect(kind(new Error("model does not exist"))).toBe("MODEL");
    expect(kind(new Error("no model matched your request"))).toBe("MODEL");
  });
  test("QUOTA from the real ACP SDK RequestError shape (grok 402)", () => {
    const err = {
      message: "API error (status 402 Payment Required): Grok Build usage balance exhausted",
      http_status: 402,
      data: { message: "API error (status 402 Payment Required): Grok Build usage balance exhausted", http_status: 402 },
      code: -32603,
    };
    expect(kind(err)).toBe("QUOTA");
    const classified = classifyWorkerError(err, caller);
    expect(classified.message).toContain("grok");
    expect(classified.message).toContain("grok-4.6");
    expect(classified.message).toContain("QUOTA");
  });
  test("QUOTA from 429 / billing / credits", () => {
    expect(kind(new Error("429 rate limit exceeded"))).toBe("QUOTA");
    expect(kind(new Error("billing: insufficient credits"))).toBe("QUOTA");
    expect(kind({ message: "usage balance exhausted", http_status: 402 })).toBe("QUOTA");
  });
  test("GENERIC fallback keeps the historical worker error shape", () => {
    const classified = classifyWorkerError(new Error("boom"), caller);
    expect(classified.kind).toBe("GENERIC");
    expect(classified.message).toBe("worker error: boom");
  });
  test("GENERIC for worker timeouts and JSON parse failures", () => {
    expect(kind(new Error("worker timeout after 600000ms"))).toBe("GENERIC");
    expect(kind(new Error("no JSON object in worker output"))).toBe("GENERIC");
  });
  test("AUTH takes precedence over QUOTA/MODEL when multiple patterns match", () => {
    expect(kind(new Error("401 unauthorized: model xyz not found"))).toBe("AUTH");
  });
});
