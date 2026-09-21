import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import type { DecoderRing, ResearchRequest } from "../contract.ts";
import { withRingHash } from "../hash.ts";
import { runWorker } from "../worker.ts";
import { ResearchDirective, YIELDS_BY_CLASS } from "../contract.ts";

function cheapLocalRing(req: ResearchRequest): DecoderRing {
  let purpose = `${req.tech}: orientation not yet read from a worker; purpose inferred from the request.`;
  const repo = req.sources.repoPath;
  if (repo) {
    for (const name of ["README.md", "README", "readme.md"]) {
      const p = join(repo, name);
      if (existsSync(p)) {
        const text = readFileSync(p, "utf8").slice(0, 2000);
        const firstPara = text.replace(/^#.*$/m, "").trim().split(/\n\n/)[0] ?? text;
        purpose = firstPara.slice(0, 600) || purpose;
        break;
      }
    }
  }
  return withRingHash({
    purpose,
    nouns: [{ term: req.tech, means: `the technology under study (${req.techSlug})` }],
    traps: req.baseFramework
      ? [{ theyCallIt: req.baseFramework, itActuallyIs: `not a ${req.baseFramework} app; map FROM ${req.baseFramework}, do not copy it` }]
      : [],
  });
}

export async function orient(req: ResearchRequest): Promise<DecoderRing> {
  if (req.decoderRing) return req.decoderRing;
  if (req.censusOnly) return cheapLocalRing(req);
  // Cheap deterministic seed so census can hash-bind even if the orient worker fails.
  const seed = cheapLocalRing(req);
  const include = [
    ...(req.sources.repoPath ? ["README.md", "README", "docs/index.md"] : []),
    ...(req.sources.docsUrl ? [req.sources.docsUrl] : []),
  ].filter(Boolean);
  // No local tree and no docs URL => nothing to point an orientation worker AT. The directive
  // schema requires include.min(1), so building one here threw an unhandled ZodError and killed
  // the run. Degrade to the seed ring — the same thing a failed orient worker already does.
  if (!include.length) return seed;
  const directive = ResearchDirective.parse({
    id: "orient",
    wave: 0,
    territory: {
      kind: req.sources.docsUrl ? "urls" : "paths",
      label: "orientation",
      include,
      exclude: [],
      unitCount: 1,
    },
    class: "orientation",
    yields: YIELDS_BY_CLASS.orientation,
    question: `Produce the decoder ring for ${req.tech}: purpose, noun hierarchy outermost-first, marketing→structural traps.`,
    mustFind: ["purpose"],
    priorArt: [],
    decoderRingHash: seed.hash,
  });
  try {
    // Same cwd fanout gives its workers: the include list is repo-relative ("README.md").
    const result = await runWorker(directive, seed, req.caller, req.sources.repoPath);
    if (result.ok) {
      const purpose = result.report.facts.find((f) => f.key === "purpose")?.value;
      const nouns = result.report.facts
        .filter((f) => f.key.startsWith("noun:"))
        .map((f) => ({
          term: f.key.slice(5),
          means: String(f.value),
        }));
      const traps = result.report.surprises.map((s) => ({
        theyCallIt: s.claim,
        itActuallyIs: s.contradicts,
      }));
      return withRingHash({
        purpose: typeof purpose === "string" && purpose ? purpose : seed.purpose,
        nouns: nouns.length ? nouns : seed.nouns,
        traps: traps.length ? traps : seed.traps,
      });
    }
  } catch {
    // fall through to seed
  }
  return seed;
}
