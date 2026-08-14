import { AcpAgent } from "@mastra/acp";
import {
  CallerIdentity,
  DecoderRing,
  ResearchDirective,
  WorkerReport,
} from "./contract.ts";
import { narrowedRetryPrompt, renderDirective } from "./prompts.ts";

export type WorkerReject = {
  ok: false;
  reason: string;
  report?: WorkerReport;
};

export type WorkerAccept = {
  ok: true;
  report: WorkerReport;
};

export function acpOptionsFromCaller(
  directive: ResearchDirective,
  caller: CallerIdentity,
): ConstructorParameters<typeof AcpAgent>[0] {
  return {
    id: directive.id,
    name: `rosetta-${directive.id}`,
    description: `Rosetta worker ${directive.id} (${directive.class})`,
    command: caller.acp.command,
    args: [...caller.acp.args],
    env: { ...caller.acp.env },
    model: caller.model,
  };
}

/** The only factory. Every worker is `new AcpAgent` from `caller.acp`. */
export function makeWorker(
  directive: ResearchDirective,
  _ring: DecoderRing,
  caller: CallerIdentity,
): AcpAgent {
  return new AcpAgent(acpOptionsFromCaller(directive, caller));
}

function globToRegExp(glob: string): RegExp {
  const escaped = glob.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*\*/g, ":::DS:::").replace(/\*/g, "[^/]*").replace(/:::DS:::/g, ".*");
  return new RegExp(`^${escaped}$`);
}

export function locatorInTerritory(source: string, directive: ResearchDirective): boolean {
  const include = directive.territory.include;
  const exclude = directive.territory.exclude ?? [];
  const hit = include.some((pat) => {
    if (directive.territory.kind === "urls") {
      return source === pat || source.startsWith(pat) || source.startsWith(pat.replace(/\/$/, ""));
    }
    // exact path, prefix, or glob
    if (source === pat || source.startsWith(`${pat}:`) || source.startsWith(`${pat}#`)) return true;
    const file = source.split(":")[0].split("#")[0];
    if (file === pat || file.startsWith(`${pat.replace(/\/$/, "")}/`)) return true;
    try {
      return globToRegExp(pat).test(file);
    } catch {
      return file.includes(pat);
    }
  });
  if (!hit) return false;
  return !exclude.some((pat) => {
    const file = source.split(":")[0].split("#")[0];
    return file === pat || file.startsWith(`${pat}/`) || source.includes(pat);
  });
}

export function validateReport(
  raw: unknown,
  directive: ResearchDirective,
  ring: DecoderRing,
): WorkerAccept | WorkerReject {
  const parsed = WorkerReport.safeParse(raw);
  if (!parsed.success) {
    return { ok: false, reason: `schema: ${parsed.error.issues.map((i) => i.path.join(".") + " " + i.message).join("; ")}` };
  }
  const report = parsed.data;
  if (report.directiveId !== directive.id) {
    return { ok: false, reason: `directiveId mismatch: ${report.directiveId} != ${directive.id}`, report };
  }
  if (report.status !== "closed" && !report.reason) {
    return { ok: false, reason: "reason required when status is partial|failed", report };
  }
  for (const fact of report.facts) {
    if (!fact.source || !fact.source.trim()) {
      return { ok: false, reason: `missing source locator on fact ${fact.key}`, report };
    }
    if (!locatorInTerritory(fact.source, directive)) {
      return { ok: false, reason: `locator outside territory.include: ${fact.source}`, report };
    }
  }
  // Ring hash is on the directive; a worker cannot change it. We reject reports that
  // smuggle a conflicting hash in surprises/pointers text only if they claim one.
  const claimed = (raw as { decoderRingHash?: string }).decoderRingHash;
  if (claimed && claimed !== ring.hash) {
    return { ok: false, reason: `decoderRingHash mismatch: ${claimed} != ${ring.hash}`, report };
  }
  if (directive.decoderRingHash !== ring.hash) {
    return { ok: false, reason: `directive decoderRingHash mismatch: ${directive.decoderRingHash} != ${ring.hash}`, report };
  }
  const missing = directive.mustFind.filter((m) => {
    const hit = report.facts.some((f) =>
      f.key.toLowerCase().includes(m.toLowerCase()) ||
      JSON.stringify(f.value).toLowerCase().includes(m.toLowerCase()),
    );
    const named = (report.reason ?? "").toLowerCase().includes(m.toLowerCase());
    return !hit && !named;
  });
  if (missing.length && report.status === "closed") {
    return { ok: false, reason: `mustFind not found and not named as gaps: ${missing.join(", ")}`, report };
  }
  if (report.facts.length === 0 && !report.reason) {
    return { ok: false, reason: "empty result with no reason", report };
  }
  return { ok: true, report };
}

export function extractJson(text: string): unknown {
  const fence = text.match(/```(?:json)?\s*([\s\S]*?)```/);
  const body = fence ? fence[1] : text;
  const start = body.indexOf("{");
  const end = body.lastIndexOf("}");
  if (start < 0 || end <= start) throw new Error("no JSON object in worker output");
  return JSON.parse(body.slice(start, end + 1));
}

export const DEFAULT_WORKER_TIMEOUT_MS = 600_000;

export async function runWorker(
  directive: ResearchDirective,
  ring: DecoderRing,
  caller: CallerIdentity,
): Promise<WorkerAccept | WorkerReject> {
  const prompt = renderDirective(directive, ring);
  const worker = makeWorker(directive, ring, caller);
  const timeoutMs = Number(process.env.ROSETTA_WORKER_TIMEOUT_MS ?? DEFAULT_WORKER_TIMEOUT_MS);
  const withTimeout = <T>(p: Promise<T>): Promise<T> =>
    Promise.race([
      p,
      new Promise<T>((_, rej) =>
        setTimeout(() => rej(new Error(`worker timeout after ${timeoutMs}ms`)), timeoutMs),
      ),
    ]);
  const attempt = async (p: string): Promise<WorkerAccept | WorkerReject> => {
    try {
      const result = await withTimeout(worker.generate(p));
      const text = typeof result === "object" && result && "text" in result
        ? String((result as { text: string }).text)
        : JSON.stringify(result);
      const raw = extractJson(text);
      return validateReport(raw, directive, ring);
    } catch (err) {
      return { ok: false, reason: `worker error: ${(err as Error).message}` };
    }
  };
  const first = await attempt(prompt);
  if (first.ok) return first;
  return attempt(narrowedRetryPrompt(prompt, first.reason));
}
