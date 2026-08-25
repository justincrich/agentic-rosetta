#!/usr/bin/env bun
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { runResearch } from "./index.ts";
import { resolveCaller } from "./resolve-caller.ts";
import { directivesFromTopics } from "./phases/census.ts";
import { withRingHash } from "./hash.ts";
import { resolveConcurrency } from "./device-limits.ts";

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(name);
  if (i >= 0 && process.argv[i + 1]) return process.argv[i + 1];
  return fallback;
}

function flag(name: string): boolean {
  return process.argv.includes(name);
}

function slugify(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

function classifyUrlArg(url: string): { repoUrl?: string; docsUrl?: string; sourceMode: "repo" | "docs" | "both" } {
  if (/github\.com|gitlab\.com|bitbucket\.org|sr\.ht/.test(url) && !/\/docs(\/|$)/.test(url)) {
    return { repoUrl: url, sourceMode: "repo" };
  }
  return { docsUrl: url, sourceMode: "docs" };
}

async function main(): Promise<number> {
  if (flag("-h") || flag("--help")) {
    process.stdout.write(`Usage: bun run engine/src/research.ts --tech NAME --model MODEL [options]

  --tech NAME            required
  --tech-slug SLUG       default: kebab of --tech
  --model MODEL          required; never defaulted
  --provider NAME        pi only: compose as provider/model (ignored by model-provided harnesses)
  --harness H            claude|codex|opencode|grok|pi (default: detect from env)
  --base-framework NAME
  --repo-path DIR
  --repo-url URL
  --docs-url URL
  --source-mode repo|docs|both
  --topics "a, b"        skip census; fan out these directives
  --depth quick|normal|deep
  --concurrency N        live workers per wave (default: device limits.concurrency, else 8)
  --max-waves N
  --max-units N
  --docs-max-units N    docs/URL leaf size (default 10; --max-units overrides both)
  --max-directives N
  --max-usd N            circuit-breaker; omit = uncapped
  --census-only          A0–A2 only, spawn nothing
  --probe-sources        census + source pins, spawn nothing; write provenance.json
  --out-dir DIR          write nrra.md + ledger.json [+ provenance.json]
`);
    return 0;
  }

  const tech = arg("--tech");
  const model = arg("--model");
  if (!tech || !model) {
    process.stderr.write("ERROR: --tech and --model are required (model is never defaulted)\n");
    return 2;
  }

  const provider = arg("--provider");

  let caller;
  try {
    caller = resolveCaller({ model, harness: arg("--harness"), provider });
  } catch (err) {
    process.stderr.write(`ERROR: ${(err as Error).message}\n`);
    return 2;
  }

  const positional = process.argv.slice(2).filter((a) => !a.startsWith("-") && a !== tech && a !== model);
  const maybeUrl = positional.find((a) => /^https?:\/\//.test(a));
  const classified = maybeUrl ? classifyUrlArg(maybeUrl) : null;

  const repoPath = arg("--repo-path");
  const repoUrl = arg("--repo-url") ?? classified?.repoUrl;
  const docsUrl = arg("--docs-url") ?? classified?.docsUrl;
  const sourceMode = (arg("--source-mode") as "repo" | "docs" | "both" | undefined)
    ?? classified?.sourceMode
    ?? (repoPath || repoUrl ? (docsUrl ? "both" : "repo") : "docs");

  const topicsRaw = arg("--topics");
  const topics = topicsRaw ? topicsRaw.split(",").map((s) => s.trim()).filter(Boolean) : [];

  const seedRing = withRingHash({
    purpose: `${tech} (seed; orient will replace)`,
    nouns: [{ term: tech, means: "subject of this run" }],
    traps: [],
  });

  const result = await runResearch({
    tech,
    techSlug: arg("--tech-slug") ?? slugify(tech),
    baseFramework: arg("--base-framework"),
    sourceMode,
    sources: { repoPath, repoUrl, docsUrl },
    caller,
    directives: topics.length ? directivesFromTopics(topics, seedRing) : undefined,
    decoderRing: topics.length ? seedRing : undefined,
    concurrency: resolveConcurrency({
      cli: arg("--concurrency") ? Number(arg("--concurrency")) : undefined,
    }),
    maxWaves: arg("--max-waves") ? Number(arg("--max-waves")) : 4,
    maxUnits: arg("--max-units") ? Number(arg("--max-units")) : 40,
    docsMaxUnits: arg("--docs-max-units")
      ? Number(arg("--docs-max-units"))
      : (arg("--max-units") ? Number(arg("--max-units")) : 10),
    censusOnly: flag("--census-only"),
    probeSources: flag("--probe-sources"),
    depth: (arg("--depth") as "quick" | "normal" | "deep") ?? "normal",
    budget: {
      maxDirectives: arg("--max-directives") ? Number(arg("--max-directives")) : 60,
      maxUsd: arg("--max-usd") ? Number(arg("--max-usd")) : undefined,
    },
  });

  const outDir = arg("--out-dir");
  if (outDir) {
    mkdirSync(outDir, { recursive: true });
    writeFileSync(resolve(outDir, "nrra.md"), result.nrra);
    writeFileSync(resolve(outDir, "ledger.json"), JSON.stringify(result.ledger, null, 2) + "\n");
    writeFileSync(resolve(outDir, "result.json"), JSON.stringify({
      runId: result.runId,
      status: result.status,
      blockReason: result.blockReason,
      ledger: result.ledger,
    }, null, 2) + "\n");
    if (result.provenance) {
      writeFileSync(
        resolve(outDir, "provenance.json"),
        JSON.stringify(result.provenance, null, 2) + "\n",
      );
    }
  }

  process.stdout.write(JSON.stringify({
    runId: result.runId,
    status: result.status,
    blockReason: result.blockReason,
    ledger: result.ledger,
    nrra: result.nrra,
    directiveCount: result.ledger.censused,
    perClass: result.ledger.rows.reduce<Record<string, number>>((acc, r) => {
      acc[r.class] = (acc[r.class] ?? 0) + 1;
      return acc;
    }, {}),
  }, null, 2) + "\n");

  return result.status === "BLOCKED" ? 3 : 0;
}

void dirname; // keep import if unused in some bun versions

main().then((code) => process.exit(code), (err) => {
  process.stderr.write(`ERROR: ${err?.stack ?? err}\n`);
  process.exit(1);
});
