import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, posix, relative, sep } from "node:path";
import {
  ResearchDirective,
  TerritoryClass,
  YIELDS_BY_CLASS,
  type CorpusUnit,
  type ResearchRequest,
  type SourcePin,
} from "../contract.ts";
import type { DecoderRing } from "../contract.ts";
import { corpusDigest, sha256Bytes } from "../hash.ts";

export type ClassifiedFile = {
  rel: string;
  bytes: number;
  class: TerritoryClass | "unclassified";
};

export type CensusResult = {
  directives: ResearchDirective[];
  gaps: { territory: string; reason: string }[];
  merges: { kept: string; absorbed: string; class: TerritoryClass; reason: string }[];
  warnings: string[];
  perClass: Record<string, number>;
  pins: SourcePin[];
};

const SKIP_DIR = new Set([
  ".git", "node_modules", "__pycache__", ".worktrees", ".cmux",
  ".kb-run-sprint", ".claude", ".kb-orchestrate", "dist", "build", ".next",
]);

const SINGLETON_NAMES = new Set([
  "readme", "readme.md", "contributing", "contributing.md",
  "claude.md", "agents.md",
]);

function norm(p: string): string {
  return p.split(sep).join("/");
}

export function classifyPath(rel: string): TerritoryClass | "unclassified" {
  const n = norm(rel);
  const base = posix.basename(n);
  const lower = n.toLowerCase();
  const baseLower = base.toLowerCase();

  // 1 orientation
  if (/^readme(\.[a-z0-9]+)?$/i.test(base)) return "orientation";
  if (/(^|\/)docs\/index(\.[a-z0-9]+)?$/i.test(n)) return "orientation";
  if (/(^|\/)about(\.[a-z0-9]+)?$/i.test(n)) return "orientation";

  // 2 enforcement
  if (/^contributing(\.[a-z0-9]+)?$/i.test(base)) return "enforcement";
  if (baseLower === "claude.md" || baseLower === "agents.md") return "enforcement";
  if (n.startsWith(".github/workflows/")) return "enforcement";
  if (/(^|\/)scripts\/(lint|check|assert)-[^/]+$/i.test(n)) return "enforcement";
  if (/^\.eslintrc(\.|$)/i.test(base) || baseLower === "clippy.toml" || baseLower === ".pre-commit-config.yaml") {
    return "enforcement";
  }

  // 3 scars
  if (/^changelog(\.[a-z0-9]+)?$/i.test(base)) return "scars";
  if (/^migration(\.[a-z0-9]+)?$/i.test(base)) return "scars";
  if (/(^|\/)docs\/troubleshooting/i.test(n)) return "scars";
  if (/postmortem/i.test(lower) || /(^|\/)issues\//i.test(n)) return "scars";

  // 4 contracts
  if (/\.schema\.json$/i.test(base) || /\.proto$/i.test(base) || /\.d\.ts$/i.test(base)) return "contracts";
  if (n.startsWith("types/") || /^openapi/i.test(base)) return "contracts";

  // 5 exemplars
  if (n.startsWith("examples/") || n.startsWith("tests/") || n.startsWith("spec/")) return "exemplars";
  if (/_test\.[^.]+$/.test(base) || /\.test\.[^.]+$/.test(base)) return "exemplars";

  // 6 reference
  if (n.startsWith("docs/") || /\.md$/i.test(base)) return "reference";

  return "unclassified";
}

export function isNamedSingleton(rel: string): boolean {
  const base = posix.basename(norm(rel)).toLowerCase();
  if (SINGLETON_NAMES.has(base)) return true;
  if (/\.schema\.json$/i.test(base)) return true;
  return false;
}

export function listRepoFiles(repoPath: string): { rel: string; bytes: number }[] {
  const git = spawnSync("git", ["-C", repoPath, "ls-files", "-z"], { encoding: "buffer" });
  let rels: string[] = [];
  if (git.status === 0 && git.stdout.length) {
    rels = git.stdout.toString("utf8").split("\0").filter(Boolean);
  } else {
    const find = spawnSync(
      "find",
      [repoPath, "-type", "f"],
      { encoding: "utf8" },
    );
    rels = (find.stdout || "")
      .split("\n")
      .filter(Boolean)
      .map((abs) => relative(repoPath, abs))
      .filter((r) => !r.split("/").some((p) => SKIP_DIR.has(p)));
  }
  const out: { rel: string; bytes: number }[] = [];
  for (const rel of rels) {
    const parts = rel.split("/");
    if (parts.some((p) => SKIP_DIR.has(p))) continue;
    const abs = join(repoPath, rel);
    let bytes = 0;
    try {
      if (existsSync(abs)) bytes = statSync(abs).size;
    } catch {
      bytes = 0;
    }
    out.push({ rel: norm(rel), bytes });
  }
  return out.sort((a, b) => a.rel.localeCompare(b.rel));
}

/** Trailing markdown / list punctuation — not query/hash contents. */
const TRAILING_DOCS_PUNCT = /[)\]>,.:]+$/;

export function cleanDocsUrl(raw: string, baseHref: string): string | null {
  const trimmed = raw.trim().replace(TRAILING_DOCS_PUNCT, "");
  if (!trimmed) return null;
  try {
    const href = new URL(trimmed, baseHref).href;
    return href.replace(TRAILING_DOCS_PUNCT, "") || null;
  } catch {
    return null;
  }
}

/** Pure llms.txt / markdown extractor. Prefer `[text](url)`, then bare URLs; dedupe. */
export function extractDocsUrls(text: string, baseHref: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  const add = (raw: string): void => {
    const href = cleanDocsUrl(raw, baseHref);
    if (!href || seen.has(href)) return;
    seen.add(href);
    out.push(href);
  };
  for (const md of text.matchAll(/\[[^\]]*\]\(([^)]+)\)/g)) {
    add(md[1]);
  }
  for (const m of text.matchAll(/https?:\/\/[^\s<>"']+/g)) {
    add(m[0]);
  }
  return out;
}

export function resolveRepoTree(sources: {
  repoPath?: string;
  repoUrl?: string;
}): { path: string; cloned: boolean } {
  if (sources.repoPath) return { path: sources.repoPath, cloned: false };
  const url = sources.repoUrl;
  if (!url) {
    throw new Error(
      "census: sourceMode includes repo but sources.repoPath is missing and sources.repoUrl is absent",
    );
  }
  const dest = join(tmpdir(), `rosetta-clone-${Date.now()}-${Math.random().toString(16).slice(2)}`);
  const r = spawnSync("git", ["clone", "--depth", "1", url, dest], { encoding: "utf8" });
  if (r.status !== 0) {
    const detail = (r.stderr || r.stdout || "unknown error").trim();
    throw new Error(`census: clone failed for ${url}: ${detail}`);
  }
  return { path: dest, cloned: true };
}

function gitOut(repo: string, args: string[]): string | undefined {
  const r = spawnSync("git", ["-C", repo, ...args], { encoding: "utf8" });
  if (r.status !== 0) return undefined;
  const out = (r.stdout || "").trim();
  return out || undefined;
}

function extractManifestVersion(repoPath: string): { version?: string; source?: string } {
  const pkg = join(repoPath, "package.json");
  if (existsSync(pkg)) {
    try {
      const ver = JSON.parse(readFileSync(pkg, "utf8")).version;
      if (typeof ver === "string" && ver && ver !== "0.0.0") {
        return { version: ver, source: "package.json" };
      }
    } catch {
      /* ignore */
    }
  }
  const cargo = join(repoPath, "Cargo.toml");
  if (existsSync(cargo)) {
    const m = readFileSync(cargo, "utf8").match(/^version\s*=\s*"([^"]+)"/m);
    if (m && m[1] !== "0.0.0") return { version: m[1], source: "Cargo.toml" };
  }
  const py = join(repoPath, "pyproject.toml");
  if (existsSync(py)) {
    const m = readFileSync(py, "utf8").match(/^version\s*=\s*"([^"]+)"/m);
    if (m && m[1] !== "0.0.0") return { version: m[1], source: "pyproject.toml" };
  }
  return {};
}

export function pinRepo(repoPath: string, url?: string): SourcePin {
  const files = listRepoFiles(repoPath);
  const units: CorpusUnit[] = [];
  for (const f of files) {
    const abs = join(repoPath, f.rel);
    try {
      const buf = readFileSync(abs);
      units.push({ locator: f.rel, sha256: sha256Bytes(buf), bytes: buf.length });
    } catch {
      /* skip unreadable */
    }
  }
  const digest = corpusDigest(units);
  const manifest = extractManifestVersion(repoPath);
  const describe = gitOut(repoPath, ["describe", "--tags", "--always"]);
  const remote = gitOut(repoPath, ["remote", "get-url", "origin"]);
  return {
    kind: "repo",
    url: url || remote,
    ref: gitOut(repoPath, ["rev-parse", "--abbrev-ref", "HEAD"]) || "HEAD",
    commit: gitOut(repoPath, ["rev-parse", "HEAD"]),
    describe,
    fetched_at: new Date().toISOString(),
    units: units.length,
    corpus_sha256: digest,
    manifest_version: manifest.version,
    version_source: manifest.source || (describe ? `git-describe:${describe}` : undefined),
    unitPins: units,
  };
}

async function fetchUnit(url: string): Promise<CorpusUnit | null> {
  try {
    const res = await fetch(url, { redirect: "follow" });
    if (!res.ok) return null;
    const buf = Buffer.from(await res.arrayBuffer());
    return {
      locator: url,
      sha256: sha256Bytes(buf),
      bytes: buf.length,
      etag: res.headers.get("etag") ?? undefined,
      lastModified: res.headers.get("last-modified") ?? undefined,
    };
  } catch {
    return null;
  }
}

export async function pinDocs(docsUrl: string, listed: { rel: string }[]): Promise<SourcePin> {
  const units: CorpusUnit[] = [];
  const primary = await fetchUnit(docsUrl);
  if (primary) units.push(primary);
  const extras = listed.map((f) => f.rel).filter((u) => u.startsWith("http") && u !== docsUrl);
  const cap = extras.slice(0, 40);
  const fetched = await Promise.all(cap.map((u) => fetchUnit(u)));
  for (const u of fetched) {
    if (u && !units.some((x) => x.locator === u.locator)) units.push(u);
  }
  const digest = corpusDigest(units);
  return {
    kind: "docs",
    url: docsUrl,
    fetched_at: new Date().toISOString(),
    units: units.length,
    corpus_sha256: digest,
    etag: primary?.etag,
    lastModified: primary?.lastModified,
    unitPins: units,
  };
}

export function unitsForTerritory(
  kind: "paths" | "urls",
  req: { maxUnits: number; docsMaxUnits: number },
): number {
  return kind === "urls" ? req.docsMaxUnits : req.maxUnits;
}

export async function listDocsUrls(docsUrl: string): Promise<{ rel: string; bytes: number }[]> {
  const origin = new URL(docsUrl);
  const base = docsUrl.replace(/\/?$/, "/");
  const candidates = [
    new URL("llms.txt", base).href, // https://nextjs.org/docs/llms.txt — not origin/llms.txt
    new URL("/llms.txt", origin).href,
    new URL("sitemap.xml", base).href,
    new URL("/sitemap.xml", origin).href,
  ];
  const seen = new Set<string>();
  const urls: string[] = [];

  for (const u of candidates) {
    try {
      const res = await fetch(u, { redirect: "follow" });
      if (!res.ok) continue;
      const text = await res.text();
      if (u.endsWith("llms.txt")) {
        urls.push(...extractDocsUrls(text, u));
        if (urls.length) break;
      }
      if (u.endsWith("sitemap.xml")) {
        for (const m of text.matchAll(/<loc>\s*([^<]+)\s*<\/loc>/gi)) {
          const href = cleanDocsUrl(m[1], u);
          if (href) urls.push(href);
        }
        if (urls.length) break;
      }
    } catch {
      // try next
    }
  }

  if (urls.length === 0) {
    try {
      const res = await fetch(docsUrl, { redirect: "follow" });
      if (res.ok) {
        const html = await res.text();
        for (const m of html.matchAll(/href=["']([^"']+)["']/gi)) {
          const abs = cleanDocsUrl(m[1], docsUrl);
          if (abs && new URL(abs).origin === origin.origin) urls.push(abs);
        }
      }
    } catch {
      /* empty */
    }
  }

  const out: { rel: string; bytes: number }[] = [];
  for (const u of urls) {
    if (seen.has(u)) continue;
    seen.add(u);
    out.push({ rel: u, bytes: 0 });
  }
  if (out.length === 0) out.push({ rel: docsUrl, bytes: 0 });
  return out;
}

export function classifyUrl(url: string): TerritoryClass | "unclassified" {
  const u = url.toLowerCase();
  if (/\/(about|index\/?$)/.test(u) || /\/$/.test(new URL(url).pathname) && !/\/docs\//.test(u)) {
    if (/\/about/.test(u)) return "orientation";
  }
  if (/changelog|migration|troubleshooting|postmortem/.test(u)) return "scars";
  if (/contributing|code-of-conduct/.test(u)) return "enforcement";
  if (/openapi|\.proto|schema\.json/.test(u)) return "contracts";
  if (/\/(examples|tutorials)\//.test(u)) return "exemplars";
  if (/\/docs\//.test(u) || /\/api\//.test(u)) return "reference";
  if (/readme/.test(u)) return "orientation";
  return "reference";
}

type Group = { prefix: string; files: ClassifiedFile[]; cls: TerritoryClass };

function parentPrefix(prefix: string): string {
  const i = prefix.lastIndexOf("/");
  return i <= 0 ? "" : prefix.slice(0, i);
}

function nextSegment(rel: string, prefix: string): string {
  const rest = prefix ? rel.slice(prefix.length).replace(/^\//, "") : rel;
  const seg = rest.split("/")[0] ?? rest;
  return prefix ? `${prefix}/${seg}` : seg;
}

function overCap(g: Group, maxUnits: number, maxBytes: number): boolean {
  const bytes = g.files.reduce((s, f) => s + f.bytes, 0);
  return g.files.length > maxUnits || bytes > maxBytes;
}

function deepen(groups: Group[], maxUnits: number, maxBytes: number): Group[] {
  let changed = true;
  let cur = groups;
  while (changed) {
    changed = false;
    const next: Group[] = [];
    for (const g of cur) {
      if (!overCap(g, maxUnits, maxBytes)) {
        next.push(g);
        continue;
      }
      const buckets = new Map<string, ClassifiedFile[]>();
      for (const f of g.files) {
        const key = nextSegment(f.rel, g.prefix);
        const arr = buckets.get(key) ?? [];
        arr.push(f);
        buckets.set(key, arr);
      }
      if (buckets.size === 1) {
        // leaf dir still over cap — chunk
        const files = g.files;
        for (let i = 0; i < files.length; i += maxUnits) {
          const slice = files.slice(i, i + maxUnits);
          next.push({
            prefix: `${g.prefix || "leaf"}#${Math.floor(i / maxUnits) + 1}`,
            files: slice,
            cls: g.cls,
          });
        }
        continue;
      }
      changed = true;
      for (const [prefix, files] of buckets) {
        next.push({ prefix, files, cls: g.cls });
      }
    }
    cur = next;
  }
  return cur;
}

export function bindDepth(req: ResearchRequest): {
  classes: Set<TerritoryClass | "unclassified">;
  maxUnits: number;
  maxWaves: number;
} {
  const all = new Set<TerritoryClass | "unclassified">([
    "orientation", "enforcement", "scars", "contracts", "exemplars", "reference", "unclassified",
  ]);
  if (req.depth === "quick") {
    return {
      classes: new Set(["orientation", "enforcement", "reference", "unclassified"]),
      maxUnits: req.maxUnits * 2,
      maxWaves: 1,
    };
  }
  if (req.depth === "deep") {
    return { classes: all, maxUnits: Math.max(1, Math.floor(req.maxUnits / 2)), maxWaves: 4 };
  }
  return { classes: all, maxUnits: req.maxUnits, maxWaves: req.maxWaves };
}

function questionFor(cls: TerritoryClass, label: string): string {
  switch (cls) {
    case "orientation":
      return `What is this, why does it exist, and what noun hierarchy should every later worker use? Territory: ${label}`;
    case "enforcement":
      return `What rules are enforced here, and what do they reject? Territory: ${label}`;
    case "scars":
      return `What broke, how often, and what does the fix forbid? Territory: ${label}`;
    case "contracts":
      return `What type-level contracts live here? Territory: ${label}`;
    case "exemplars":
      return `What do the tests/examples show as the real call pattern, and what do they forbid? Territory: ${label}`;
    case "reference":
      return `What components and signatures does this reference document? Territory: ${label}`;
  }
}

export function groupIntoDirectives(
  files: ClassifiedFile[],
  opts: {
    kind: "paths" | "urls";
    ringHash: string;
    maxUnits: number;
    maxBytes: number;
    maxDirectives: number;
    allowed: Set<TerritoryClass | "unclassified">;
    wave?: number;
  },
): CensusResult {
  const gaps: CensusResult["gaps"] = [];
  const merges: CensusResult["merges"] = [];
  const warnings: string[] = [];
  const byClass = new Map<TerritoryClass, ClassifiedFile[]>();

  for (const f of files) {
    if (!opts.allowed.has(f.class)) continue;
    if (f.class === "unclassified") {
      gaps.push({ territory: f.rel, reason: "unclassified" });
      continue;
    }
    const arr = byClass.get(f.class) ?? [];
    arr.push(f);
    byClass.set(f.class, arr);
  }

  const groups: Group[] = [];
  for (const [cls, clsFiles] of byClass) {
    const singles: ClassifiedFile[] = [];
    const rest: ClassifiedFile[] = [];
    for (const f of clsFiles) {
      if (isNamedSingleton(f.rel)) singles.push(f);
      else rest.push(f);
    }
    for (const f of singles) {
      groups.push({ prefix: f.rel, files: [f], cls });
    }
    if (rest.length) {
      groups.push({ prefix: "", files: rest, cls });
    }
  }

  let split = deepen(groups, opts.maxUnits, opts.maxBytes);

  // Overflow: merge smallest same-class siblings; log every merge.
  while (split.length > opts.maxDirectives) {
    const by = new Map<string, Group[]>();
    for (const g of split) {
      const key = `${g.cls}::${parentPrefix(g.prefix)}`;
      const arr = by.get(key) ?? [];
      arr.push(g);
      by.set(key, arr);
    }
    let pair: { a: Group; b: Group; key: string } | null = null;
    let best = Infinity;
    for (const [key, arr] of by) {
      if (arr.length < 2) continue;
      const sorted = [...arr].sort((x, y) => x.files.length - y.files.length);
      const cost = sorted[0].files.length + sorted[1].files.length;
      if (cost < best) {
        best = cost;
        pair = { a: sorted[0], b: sorted[1], key };
      }
    }
    if (!pair) {
      warnings.push(`cannot merge further; ${split.length} rows exceed maxDirectives=${opts.maxDirectives}`);
      break;
    }
    const kept = pair.a;
    const absorbed = pair.b;
    kept.files = [...kept.files, ...absorbed.files];
    kept.prefix = parentPrefix(kept.prefix) || kept.cls;
    split = split.filter((g) => g !== absorbed);
    merges.push({
      kept: kept.prefix,
      absorbed: absorbed.prefix,
      class: kept.cls,
      reason: `overflow past maxDirectives=${opts.maxDirectives}; merged smallest same-class siblings`,
    });
  }

  const directives: ResearchDirective[] = [];
  let i = 0;
  for (const g of split.sort((a, b) => a.prefix.localeCompare(b.prefix) || a.cls.localeCompare(b.cls))) {
    i += 1;
    const id = `t${String(i).padStart(2, "0")}`;
    const include = g.files.length === 1 ? [g.files[0].rel] : g.files.map((f) => f.rel);
    const label = g.files.length === 1 ? g.files[0].rel : (g.prefix || g.cls);
    directives.push(
      ResearchDirective.parse({
        id,
        wave: opts.wave ?? 1,
        territory: {
          kind: opts.kind,
          label,
          include,
          exclude: [],
          unitCount: g.files.length,
        },
        class: g.cls,
        yields: YIELDS_BY_CLASS[g.cls],
        question: questionFor(g.cls, label),
        mustFind: [],
        priorArt: [],
        decoderRingHash: opts.ringHash,
      }),
    );
  }

  const perClass: Record<string, number> = {};
  for (const d of directives) {
    perClass[d.class] = (perClass[d.class] ?? 0) + 1;
  }
  perClass.unclassified = gaps.filter((g) => g.reason === "unclassified").length;

  if ((perClass.enforcement ?? 0) === 0 && (perClass.scars ?? 0) === 0) {
    warnings.push("zero enforcement AND zero scars rows — x will be thin; recorded, not blocking");
  }

  return { directives, gaps, merges, warnings, perClass, pins: [] };
}

export async function runCensus(
  req: ResearchRequest,
  ring: DecoderRing,
): Promise<CensusResult> {
  const depth = bindDepth(req);
  const files: ClassifiedFile[] = [];
  const pins: SourcePin[] = [];

  if (req.sourceMode === "repo" || req.sourceMode === "both") {
    const tree = resolveRepoTree(req.sources);
    for (const f of listRepoFiles(tree.path)) {
      files.push({ ...f, class: classifyPath(f.rel) });
    }
    pins.push(pinRepo(tree.path, req.sources.repoUrl));
  }
  if (req.sourceMode === "docs" || req.sourceMode === "both") {
    const url = req.sources.docsUrl;
    if (!url) throw new Error("census: sourceMode includes docs but sources.docsUrl is missing");
    const listed = await listDocsUrls(url);
    for (const f of listed) {
      files.push({
        rel: f.rel,
        bytes: f.bytes,
        class: f.rel.startsWith("http") ? classifyUrl(f.rel) : classifyPath(f.rel),
      });
    }
    pins.push(await pinDocs(url, listed));
  }

  const groupOpts = {
    ringHash: ring.hash,
    maxBytes: req.maxBytes,
    maxDirectives: req.budget.maxDirectives,
    allowed: depth.classes,
  };

  if (req.sourceMode === "docs") {
    return withPins(groupIntoDirectives(files, {
      ...groupOpts,
      kind: "urls",
      maxUnits: unitsForTerritory("urls", req),
    }), pins);
  }

  if (req.sourceMode === "both") {
    const urlFiles = files.filter((f) => f.rel.startsWith("http"));
    const pathFiles = files.filter((f) => !f.rel.startsWith("http"));
    const pathResult = groupIntoDirectives(pathFiles, {
      ...groupOpts,
      kind: "paths",
      maxUnits: unitsForTerritory("paths", { maxUnits: depth.maxUnits, docsMaxUnits: req.docsMaxUnits }),
    });
    const urlResult = groupIntoDirectives(urlFiles, {
      ...groupOpts,
      kind: "urls",
      maxUnits: unitsForTerritory("urls", req),
    });
    return withPins(mergeCensusResults(pathResult, urlResult), pins);
  }

  return withPins(groupIntoDirectives(files, {
    ...groupOpts,
    kind: "paths",
    maxUnits: unitsForTerritory("paths", { maxUnits: depth.maxUnits, docsMaxUnits: req.docsMaxUnits }),
  }), pins);
}

function withPins(result: CensusResult, pins: SourcePin[]): CensusResult {
  return { ...result, pins };
}

function mergeCensusResults(a: CensusResult, b: CensusResult): CensusResult {
  const directives = [...a.directives, ...b.directives].map((d, i) => ({
    ...d,
    id: `t${String(i + 1).padStart(2, "0")}`,
  }));
  const perClass: Record<string, number> = {};
  for (const d of directives) {
    perClass[d.class] = (perClass[d.class] ?? 0) + 1;
  }
  const gaps = [...a.gaps, ...b.gaps];
  perClass.unclassified = gaps.filter((g) => g.reason === "unclassified").length;
  return {
    directives,
    gaps,
    merges: [...a.merges, ...b.merges],
    warnings: [...a.warnings, ...b.warnings],
    perClass,
    pins: [...a.pins, ...b.pins],
  };
}

export function directivesFromTopics(
  topics: string[],
  ring: DecoderRing,
  wave = 1,
): ResearchDirective[] {
  return topics.map((topic, i) =>
    ResearchDirective.parse({
      id: `topic-${String(i + 1).padStart(2, "0")}`,
      wave,
      territory: {
        kind: "urls",
        label: topic,
        include: [topic],
        exclude: [],
        unitCount: 1,
      },
      class: "reference",
      yields: YIELDS_BY_CLASS.reference,
      question: `Research this topic for the knowledge base: ${topic}`,
      mustFind: [topic],
      priorArt: [],
      decoderRingHash: ring.hash,
    }),
  );
}
