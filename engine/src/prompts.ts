import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { DecoderRing, ResearchDirective, TerritoryClass } from "./contract.ts";

const DIR = dirname(fileURLToPath(import.meta.url));

function load(name: string): string {
  return readFileSync(join(DIR, "prompts", name), "utf8");
}

const FRAME = load("frame.md");
const BODIES: Record<TerritoryClass, string> = {
  orientation: load("orientation.md"),
  reference: load("reference.md"),
  enforcement: load("enforcement.md"),
  scars: load("scars.md"),
  contracts: load("contracts.md"),
  exemplars: load("exemplars.md"),
};

export function renderDirective(d: ResearchDirective, ring: DecoderRing): string {
  const mustFind = d.mustFind.length ? d.mustFind.map((m) => `- ${m}`).join("\n") : "(none)";
  const priorArt = d.priorArt.length ? d.priorArt.map((p) => `- ${p}`).join("\n") : "(none)";
  const frame = FRAME
    .replace("__DECODER_RING__", JSON.stringify(ring, null, 2))
    .replace("__TERRITORY_KIND__", d.territory.kind)
    .replace("__TERRITORY_LABEL__", d.territory.label)
    .replace("__TERRITORY_INCLUDE__", JSON.stringify(d.territory.include))
    .replace("__TERRITORY_EXCLUDE__", JSON.stringify(d.territory.exclude))
    .replace("__CLASS__", d.class)
    .replace("__YIELDS__", d.yields.join(", "))
    .replace("__QUESTION__", d.question)
    .replace("__MUST_FIND__", mustFind)
    .replace("__PRIOR_ART__", priorArt)
    .replace("__DIRECTIVE_ID__", d.id)
    .replace("__RING_HASH__", d.decoderRingHash);
  return `${frame}\n\n## Class-specific charge\n\n${BODIES[d.class].trim()}\n`;
}

export function narrowedRetryPrompt(original: string, reason: string): string {
  return `${original}\n\n## RETRY — previous report was rejected\n\nReason: ${reason}\nStay inside territory.include. Every fact needs a source locator. mustFind misses need a reason. Return only the JSON object.\n`;
}
