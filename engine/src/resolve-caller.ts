import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { CallerIdentity, Harness, WORKER_DEPTH_ENV } from "./contract.ts";

export class ResolveError extends Error {
  readonly code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
    this.name = "ResolveError";
  }
}

export function detectHarness(env: NodeJS.ProcessEnv = process.env): Harness | null {
  const kind = (env.CMUX_AGENT_LAUNCH_KIND ?? "").toLowerCase();
  if (kind === "grok" || kind === "claude" || kind === "codex" || kind === "opencode") {
    return kind;
  }
  if (env.GROK_AGENT === "1" || env.GROK_HOME || /\/grok$/.test(env.CMUX_AGENT_LAUNCH_EXECUTABLE ?? "")) {
    return "grok";
  }
  if (env.CLAUDECODE === "1" || env.CLAUDE_CODE_EXECPATH) return "claude";
  if (env.CODEX_HOME || env.CODEX_THREAD_ID || env.CODEX_CI) return "codex";
  if (env.OPENCODE || env.OPENCODE_DIR || env.OPENCODE_CONFIG) return "opencode";
  if (env.PI_CODING_AGENT || env.PI_SESSION_ID) return "pi";
  return null;
}

function which(cmd: string): string | null {
  const r = spawnSync("which", [cmd], { encoding: "utf8" });
  const p = r.stdout.trim();
  return r.status === 0 && p ? p : null;
}

export type AcpEntrypoint = { command: string; args: string[]; note: string };

/**
 * Resolve the ACP server for a harness. Recorded empirically 2026-08-13:
 *   grok     → `grok agent -m <model> --always-approve stdio` (ACP over stdio)
 *   opencode → `opencode acp`
 *   claude   → `claude-code-acp` if on PATH (claude itself has no --acp; stream-json is not ACP)
 *   codex    → no ACP server on this machine; fail closed
 *   pi       → `pi-acp` npm adapter (bridges ACP JSON-RPC over stdio to `pi --mode rpc`)
 */
export function acpEntrypoint(harness: Harness, model: string): AcpEntrypoint {
  switch (harness) {
    case "grok": {
      const grok = which("grok") ?? process.env.CMUX_AGENT_LAUNCH_EXECUTABLE;
      if (!grok || !existsSync(grok) && !which("grok")) {
        throw new ResolveError("ACP_MISSING", "grok binary not on PATH — cannot spawn ACP workers");
      }
      const bin = which("grok") ?? grok;
      return {
        command: bin,
        args: ["agent", "-m", model, "--always-approve", "stdio"],
        note: "grok agent stdio (ACP)",
      };
    }
    case "opencode": {
      const bin = which("opencode");
      if (!bin) throw new ResolveError("ACP_MISSING", "opencode binary not on PATH");
      return { command: bin, args: ["acp"], note: "opencode acp" };
    }
    case "claude": {
      const adapter = which("claude-code-acp");
      if (!adapter) {
        throw new ResolveError(
          "ACP_MISSING",
          "claude has no ACP entrypoint on this machine (no claude-code-acp; `claude -p --output-format stream-json` is not ACP). Install @zed-industries/claude-code-acp or run from grok/opencode.",
        );
      }
      return { command: adapter, args: [], note: "claude-code-acp adapter" };
    }
    case "codex": {
      throw new ResolveError(
        "ACP_MISSING",
        "codex has no ACP server (`codex exec` is not ACP). Fail closed — will not degrade to a serial crawl.",
      );
    }
    case "pi": {
      const adapter = which("pi-acp");
      if (!adapter) {
        throw new ResolveError(
          "ACP_MISSING",
          "pi has no ACP server installed — install the pi-acp adapter: `npm install -g pi-acp`",
        );
      }
      return { command: adapter, args: [], note: "pi-acp adapter (spawns pi --mode rpc)" };
    }
  }
}

export function validateModel(model: string): void {
  if (!model || !model.trim()) {
    throw new ResolveError("MODEL_MISSING", "--model is required; the engine never substitutes a default");
  }
  // Model strings pass through unvalidated — the ACP binary surfaces provider/key
  // errors, which worker.ts classifies (AUTH / MODEL / QUOTA).
}

/**
 * Compose `--provider` into the model string. Only open-source harnesses (pi)
 * take a provider; model-provided harnesses (claude / codex / opencode / grok)
 * ignore it. Never double-prefix a model that already carries "provider/id".
 */
export function composeProviderModel(
  provider: string | undefined,
  harness: Harness,
  model: string,
): string {
  if (provider && harness === "pi" && !model.includes("/")) {
    return `${provider}/${model}`;
  }
  return model;
}

export function resolveCaller(opts: {
  model: string;
  harness?: string;
  provider?: string;
  env?: NodeJS.ProcessEnv;
}): CallerIdentity {
  const env = opts.env ?? process.env;
  if (env[WORKER_DEPTH_ENV] === "1") {
    throw new ResolveError(
      "RECURSION",
      `${WORKER_DEPTH_ENV}=1 — already inside a worker; refusing to fan out (R2)`,
    );
  }
  const detected = opts.harness ? Harness.parse(opts.harness) : detectHarness(env);
  if (!detected) {
    throw new ResolveError(
      "HARNESS_UNRESOLVED",
      "could not detect harness from env (CLAUDECODE / CODEX_* / OPENCODE_* / GROK_* / PI_* / CMUX_AGENT_LAUNCH_KIND)",
    );
  }
  validateModel(opts.model);
  const model = composeProviderModel(opts.provider, detected, opts.model);
  const acp = acpEntrypoint(detected, model);
  return CallerIdentity.parse({
    harness: detected,
    model,
    provider: opts.provider,
    acp: {
      command: acp.command,
      args: acp.args,
      env: { [WORKER_DEPTH_ENV]: "1" },
    },
  });
}
