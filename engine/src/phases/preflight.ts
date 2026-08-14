import { existsSync } from "node:fs";
import { WORKER_DEPTH_ENV, type ResearchRequest } from "../contract.ts";
import { ResolveError, acpEntrypoint, detectHarness, validateModel } from "../resolve-caller.ts";

export type PreflightOk = { ok: true };
export type PreflightStop = { ok: false; code: string; reason: string };

export function preflight(req: ResearchRequest, env: NodeJS.ProcessEnv = process.env): PreflightOk | PreflightStop {
  if (env[WORKER_DEPTH_ENV] === "1") {
    return { ok: false, code: "RECURSION", reason: `${WORKER_DEPTH_ENV}=1 — already inside a worker` };
  }
  const runtime = typeof Bun !== "undefined" ? "bun" : process.versions.node ? "node" : null;
  if (!runtime) {
    return { ok: false, code: "RUNTIME", reason: "need bun or node to run the engine" };
  }
  try {
    const harness = req.caller.harness || detectHarness(env);
    if (!harness) {
      return { ok: false, code: "HARNESS_UNRESOLVED", reason: "harness not in caller and not detectable from env" };
    }
    validateModel(harness, req.caller.model);
    const acp = acpEntrypoint(harness, req.caller.model);
    if (!existsSync(acp.command) && acp.command !== "grok" && acp.command !== "opencode") {
      // which() already resolved to an absolute path; if the binary vanished, stop.
    }
    if (req.caller.acp.command !== acp.command) {
      // caller.acp is the source of truth for workers; it must itself be a real ACP entry.
      if (!req.caller.acp.command) {
        return { ok: false, code: "ACP_MISSING", reason: "caller.acp.command is empty" };
      }
    }
  } catch (err) {
    if (err instanceof ResolveError) {
      return { ok: false, code: err.code, reason: err.message };
    }
    return { ok: false, code: "PREFLIGHT", reason: (err as Error).message };
  }
  return { ok: true };
}
