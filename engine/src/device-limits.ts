import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

export const BUILTIN_CONCURRENCY = 8;
export const MIN_CONCURRENCY = 1;
export const MAX_CONCURRENCY = 16;

export type ConcurrencySources = {
  /** `--concurrency` when the flag was actually passed. */
  cli?: number;
  env?: NodeJS.ProcessEnv;
  /** Walk up from here for `<dir>/.rosetta/config.json`. */
  cwd?: string;
  /** `$ROSETTA_HOME` or `~/.rosetta`. */
  home?: string;
};

function asConcurrency(value: unknown): number | undefined {
  const n = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
  if (!Number.isInteger(n) || n < MIN_CONCURRENCY || n > MAX_CONCURRENCY) return undefined;
  return n;
}

function concurrencyFromFile(path: string): number | undefined {
  if (!existsSync(path)) return undefined;
  try {
    const data: unknown = JSON.parse(readFileSync(path, "utf8"));
    if (!data || typeof data !== "object") return undefined;
    return asConcurrency((data as { limits?: { concurrency?: unknown } }).limits?.concurrency);
  } catch {
    return undefined;
  }
}

function nearestProjectConfig(start: string, deviceHomePath: string): string | undefined {
  const userHome = homedir();
  let cur = start;
  for (;;) {
    const rosettaDir = join(cur, ".rosetta");
    const candidate = join(rosettaDir, "config.json");
    // $HOME/.rosetta/config.json is the device file, never a project overlay.
    if (cur !== userHome && rosettaDir !== deviceHomePath && existsSync(candidate)) {
      return candidate;
    }
    const parent = dirname(cur);
    if (parent === cur) return undefined;
    cur = parent;
  }
}

function deviceHome(env: NodeJS.ProcessEnv, homeOverride?: string): string {
  if (homeOverride) return homeOverride;
  const fromEnv = env.ROSETTA_HOME?.trim();
  if (fromEnv) return fromEnv;
  return join(homedir(), ".rosetta");
}

/**
 * Live-worker cap for a research run.
 *
 * Precedence: `--concurrency` > `$ROSETTA_CONCURRENCY` > project
 * `.rosetta/config.json` `limits.concurrency` > device `~/.rosetta/config.json`
 * `limits.concurrency` > 8.
 *
 * Out-of-range values at a tier are skipped, not clamped — the next tier wins.
 */
export function resolveConcurrency(src: ConcurrencySources = {}): number {
  const env = src.env ?? process.env;
  const cli = asConcurrency(src.cli);
  if (cli !== undefined) return cli;

  const fromEnv = asConcurrency(env.ROSETTA_CONCURRENCY);
  if (fromEnv !== undefined) return fromEnv;

  const home = deviceHome(env, src.home);
  const projectCfg = nearestProjectConfig(src.cwd ?? process.cwd(), home);
  if (projectCfg) {
    const n = concurrencyFromFile(projectCfg);
    if (n !== undefined) return n;
  }

  const fromDevice = concurrencyFromFile(join(home, "config.json"));
  if (fromDevice !== undefined) return fromDevice;

  return BUILTIN_CONCURRENCY;
}
