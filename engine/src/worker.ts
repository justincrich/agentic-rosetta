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
  /** Classified provider-side failures (AUTH / MODEL / QUOTA). Undefined for validation rejects. */
  kind?: WorkerErrorKind;
};

export type WorkerErrorKind = "AUTH" | "MODEL" | "QUOTA" | "GENERIC";

export type ClassifiedWorkerError = {
  kind: WorkerErrorKind;
  message: string;
};

export type WorkerAccept = {
  ok: true;
  report: WorkerReport;
};

/**
 * Harness nesting guards that must NOT reach an ACP worker. Claude Code refuses to
 * launch inside another Claude Code session ("Nested sessions share runtime
 * resources and will crash all active sessions") by checking CLAUDECODE, so a
 * worker spawned from a Claude Code session inherits the variable and dies with a
 * JSON-RPC "Internal error" before the census ever reaches a model. The engine
 * still READS these to detect the calling harness; it just must not pass them down.
 * Set to empty rather than deleted: AcpAgent merges over process.env, so removing
 * the key from this object would leave the inherited value in place.
 */
const NESTING_GUARD_ENV = ["CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"] as const;

export function acpOptionsFromCaller(
  directive: ResearchDirective,
  caller: CallerIdentity,
  cwd?: string,
): ConstructorParameters<typeof AcpAgent>[0] {
  const env: Record<string, string> = { ...caller.acp.env };
  for (const key of NESTING_GUARD_ENV) env[key] = "";
  const opts: ConstructorParameters<typeof AcpAgent>[0] = {
    id: directive.id,
    name: `rosetta-${directive.id}`,
    description: `Rosetta worker ${directive.id} (${directive.class})`,
    command: caller.acp.command,
    args: [...caller.acp.args],
    env,
    persistSession: false,
    ...(cwd ? { cwd } : {}),
  };
  if (caller.harness === "pi") {
    // pi-acp 0.0.33 (ACP SDK 0.26) removed the `session/set_model` method the
    // client sends when `model` is set — setting it hard-fails session init
    // ("Method not found: session/set_model"). pi also scrubs inherited
    // PI_MODEL/PI_PROVIDER and derives them from its own configured model.
    // Hand the model over via env (handoff §6 fallback): pi uses its default
    // model today, and honors these if a future pi reads them.
    const slash = caller.model.indexOf("/");
    env.PI_PROVIDER = slash >= 0 ? caller.model.slice(0, slash) : (caller.provider ?? "");
    env.PI_MODEL = slash >= 0 ? caller.model.slice(slash + 1) : caller.model;
  } else if (caller.harness === "opencode") {
    // `opencode acp` registers session/set_mode + session/set_config_option but
    // NOT session/set_model (verified against opencode v2.0.29), so
    // `options.model` would hard-fail every worker at session init. The model is
    // applied after session/new via session/set_config_option instead — see
    // wireOpencodeModel/applyModelViaConfigOption. Unlike pi, model selection is
    // NOT dropped: it is deferred, not defaulted.
  } else {
    opts.model = caller.model;
  }
  return opts;
}

/** The only factory. Every worker is `new AcpAgent` from `caller.acp`. */
export function makeWorker(
  directive: ResearchDirective,
  _ring: DecoderRing,
  caller: CallerIdentity,
  cwd?: string,
): AcpAgent {
  const agent = new AcpAgent(acpOptionsFromCaller(directive, caller, cwd));
  if (caller.harness === "opencode" && caller.model) {
    wireOpencodeModel(agent, caller.model);
  }
  return agent;
}

/**
 * The shape of @mastra/acp's ACPConnection we depend on for opencode model
 * selection: `initializeSession` (TS-`private`, i.e. a prototype method, called
 * lazily on first prompt) and the live `connection`/`session` it leaves behind.
 */
type ConfigOptionConn = {
  session?: { sessionId?: string } | null;
  connection?: {
    setSessionConfigOption?: (params: {
      sessionId: string;
      configId: string;
      value: string;
    }) => Promise<{ configOptions?: Array<{ id?: string; currentValue?: string }> | null }>;
  } | null;
};

type InitializeSessionConn = ConfigOptionConn & {
  initializeSession?: () => Promise<void>;
  constructor: { prototype: { initializeSession?: unknown } };
};

/**
 * opencode acp selects a session's model with `session/set_config_option`
 * (`configId: "model"`, value `provider/model-id`) — it never registers
 * `session/set_model`, so `@mastra/acp`'s built-in `options.model` path dies at
 * session init with `Method not found: session/set_model` (observed killing all
 * 11 workers of the livekit refresh fanout on 2026-10-15 against opencode
 * v2.0.29). We therefore omit `options.model` and apply the model right after
 * `session/new`, then verify the agent echoed our model back. Fails CLOSED:
 * seam drift or a landed-on-different-model throws a named error rather than
 * letting a worker silently research on the wrong model.
 *
 * Value must be `provider/model-id` (opencode's model catalog keys); a bare
 * id surfaces as `model not found`, which classifyWorkerError maps to MODEL.
 */
export async function applyModelViaConfigOption(
  conn: ConfigOptionConn,
  model: string,
): Promise<void> {
  const sessionId = conn.session?.sessionId;
  if (!sessionId) {
    throw new Error(
      "opencode model wiring: no sessionId after session/new — refusing to run with an unselected model",
    );
  }
  const set = conn.connection?.setSessionConfigOption;
  if (typeof set !== "function") {
    throw new Error(
      "opencode model wiring: ACPConnection has no setSessionConfigOption — @mastra/acp seam drift; refusing to run with an unselected model",
    );
  }
  const response = await set.call(conn.connection, {
    sessionId,
    configId: "model",
    value: model,
  });
  const current = response?.configOptions?.find((o) => o?.id === "model")?.currentValue;
  if (current !== model) {
    throw new Error(
      `opencode model wiring: model mismatch — requested "${model}", session reports "${current ?? "(none)"}"; refusing to run on the wrong model`,
    );
  }
}

/**
 * Wrap the instance's `initializeSession` so the model lands via
 * `session/set_config_option` after `session/new`. Instance-level only: the
 * prototype is never patched, so other workers in this process are unaffected.
 * Throws (fail closed) if @mastra/acp's private surface has drifted.
 */
function wireOpencodeModel(agent: AcpAgent, model: string): void {
  const conn = (agent as unknown as { connection?: InitializeSessionConn }).connection;
  if (
    !conn ||
    typeof conn.initializeSession !== "function" ||
    typeof conn.constructor?.prototype?.initializeSession !== "function"
  ) {
    throw new Error(
      "opencode model wiring: ACPConnection.initializeSession seam missing — refusing to run with an unselected model",
    );
  }
  const runOriginal = conn.initializeSession.bind(conn);
  conn.initializeSession = async () => {
    await runOriginal();
    await applyModelViaConfigOption(conn, model);
  };
}

const AUTH_PATTERNS = /api.?key|unauthorized|authentication|invalid.*key|401|403/;
const MODEL_PATTERNS = /model.*not.?found|does.?not.?exist|is.?not.?available|unrecognized.*model|unknown.*model|no.*model.*matched/;
const QUOTA_PATTERNS = /rate.?limit|quota|billing|insufficient|429|payment|credit/;

/**
 * Pure classifier for errors surfaced by the ACP worker. Gathers text from the
 * thrown error's message / data.message / stderr (the ACP SDK's RequestError
 * carries { http_status, data: { message, http_status } }) and classifies
 * provider-side failures into AUTH / MODEL / QUOTA with an educational message
 * naming the harness + model. Everything else is GENERIC with the historical
 * `worker error: <msg>` shape (validation failures keep retrying).
 */
export function classifyWorkerError(err: unknown, caller: CallerIdentity): ClassifiedWorkerError {
  const parts: string[] = [];
  let httpStatus: number | undefined;
  const record = (v: unknown) => {
    if (typeof v === "string") parts.push(v);
    else if (v && typeof v === "object") {
      const d = v as Record<string, unknown>;
      if (typeof d.message === "string") parts.push(d.message);
      if (typeof d.error === "string") parts.push(d.error);
      if (typeof d.stderr === "string") parts.push(d.stderr);
      if (typeof d.http_status === "number" && httpStatus === undefined) httpStatus = d.http_status;
    }
  };
  const e = err as { message?: unknown; data?: unknown; stderr?: unknown; http_status?: unknown };
  record(e.message);
  record(e.data);
  record(e.stderr);
  if (typeof e.http_status === "number" && httpStatus === undefined) httpStatus = e.http_status;

  const joined = parts.join("\n");
  const hay = joined.toLowerCase();
  const firstLine = (joined.split("\n")[0] || "unknown error").slice(0, 240);
  const auth = AUTH_PATTERNS.test(hay) || httpStatus === 401 || httpStatus === 403;
  const quota = QUOTA_PATTERNS.test(hay) || httpStatus === 429 || httpStatus === 402;
  const model = MODEL_PATTERNS.test(hay);

  if (auth) {
    return {
      kind: "AUTH",
      message: `${caller.harness} rejected the request as an auth failure (model ${caller.model}): ${firstLine}. Check the provider key for ${caller.harness} and re-run. [AUTH]`,
    };
  }
  if (quota) {
    return {
      kind: "QUOTA",
      message: `${caller.harness} hit a quota/payment limit (model ${caller.model}): ${firstLine}. Top up credits or wait for the rate limit to reset, then re-run. [QUOTA]`,
    };
  }
  if (model) {
    return {
      kind: "MODEL",
      message: `${caller.harness} does not recognize model ${caller.model}: ${firstLine}. Pass a model the provider actually serves, then re-run. [MODEL]`,
    };
  }
  return { kind: "GENERIC", message: `worker error: ${firstLine}` };
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
  cwd?: string,
): Promise<WorkerAccept | WorkerReject> {
  const prompt = renderDirective(directive, ring);
  const worker = makeWorker(directive, ring, caller, cwd);
  const timeoutMs = Number(process.env.ROSETTA_WORKER_TIMEOUT_MS ?? DEFAULT_WORKER_TIMEOUT_MS);
  const attempt = async (p: string): Promise<WorkerAccept | WorkerReject> => {
    const abortController = new AbortController();
    const timeoutError = new Error(`worker timeout after ${timeoutMs}ms`);
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      abortController.abort(timeoutError);
      worker.connection.disconnect();
    }, timeoutMs);
    try {
      const result = await worker.generate(p, { abortSignal: abortController.signal });
      const text = typeof result === "object" && result && "text" in result
        ? String((result as { text: string }).text)
        : JSON.stringify(result);
      const raw = extractJson(text);
      return validateReport(raw, directive, ring);
    } catch (err) {
      const classified = classifyWorkerError(timedOut ? timeoutError : err, caller);
      return { ok: false, reason: classified.message, kind: classified.kind };
    } finally {
      clearTimeout(timer);
    }
  };
  const first = await attempt(prompt);
  if (first.ok) return first;
  // AUTH / MODEL / QUOTA are provider-side: a narrower prompt cannot fix them — fail fast.
  // GENERIC / validation rejects still retry once with the narrowed prompt.
  if (first.kind && first.kind !== "GENERIC") return first;
  return attempt(narrowedRetryPrompt(prompt, first.reason));
}
