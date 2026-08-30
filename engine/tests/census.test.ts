import { describe, expect, test } from "bun:test";
import { mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import {
  classifyPath,
  cleanDocsUrl,
  extractDocsUrls,
  isDocumentUrl,
  urlsFromVitepressHashmap,
  groupIntoDirectives,
  isNamedSingleton,
  listRepoFiles,
  unitsForTerritory,
} from "../src/phases/census.ts";
import { ResearchDirective } from "../src/contract.ts";
import { withRingHash } from "../src/hash.ts";

function makeRepo(): string {
  const root = join(tmpdir(), `rosetta-census-${Date.now()}-${Math.random().toString(16).slice(2)}`);
  const files: Record<string, string> = {
    "README.md": "# Toy\n",
    "CONTRIBUTING.md": "be kind\n",
    "CLAUDE.md": "rules\n",
    "AGENTS.md": "agents\n",
    "CHANGELOG.md": "## 1.0\n",
    "docs/index.md": "index\n",
    "docs/api.md": "api\n",
    "docs/troubleshooting.md": "broke\n",
    ".github/workflows/ci.yml": "name: ci\n",
    "scripts/lint-foo.sh": "#!/bin/sh\n",
    "scripts/check-bar.sh": "#!/bin/sh\n",
    "types/index.d.ts": "export {}\n",
    "widget.schema.json": "{}\n",
    "examples/demo.ts": "demo\n",
    "tests/foo.test.ts": "test\n",
    "src/main.ts": "console.log(1)\n",
    "package.json": "{}\n",
  };
  for (const [rel, body] of Object.entries(files)) {
    mkdirSync(join(root, rel, ".."), { recursive: true });
    writeFileSync(join(root, rel), body);
  }
  spawnSync("git", ["init"], { cwd: root });
  spawnSync("git", ["add", "-A"], { cwd: root });
  spawnSync("git", ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"], { cwd: root });
  return root;
}

describe("classifyPath first-match-wins", () => {
  test("ordered table", () => {
    expect(classifyPath("README.md")).toBe("orientation");
    expect(classifyPath("docs/index.md")).toBe("orientation");
    expect(classifyPath("CONTRIBUTING.md")).toBe("enforcement");
    expect(classifyPath("CLAUDE.md")).toBe("enforcement");
    expect(classifyPath(".github/workflows/ci.yml")).toBe("enforcement");
    expect(classifyPath("scripts/lint-foo.sh")).toBe("enforcement");
    expect(classifyPath("CHANGELOG.md")).toBe("scars");
    expect(classifyPath("docs/troubleshooting.md")).toBe("scars");
    expect(classifyPath("widget.schema.json")).toBe("contracts");
    expect(classifyPath("types/index.d.ts")).toBe("contracts");
    expect(classifyPath("examples/demo.ts")).toBe("exemplars");
    expect(classifyPath("tests/foo.test.ts")).toBe("exemplars");
    expect(classifyPath("docs/api.md")).toBe("reference");
    expect(classifyPath("notes.md")).toBe("reference");
    expect(classifyPath("src/main.ts")).toBe("unclassified");
    expect(classifyPath("package.json")).toBe("unclassified");
  });
  test("named singletons", () => {
    expect(isNamedSingleton("README.md")).toBe(true);
    expect(isNamedSingleton("AGENTS.md")).toBe(true);
    expect(isNamedSingleton("widget.schema.json")).toBe(true);
    expect(isNamedSingleton("docs/api.md")).toBe(false);
  });
});

describe("census on a real temp git repo", () => {
  test("exact classes, unclassified gaps, every directive validates", () => {
    const root = makeRepo();
    try {
      const listed = listRepoFiles(root);
      expect(listed.some((f) => f.rel === "README.md")).toBe(true);
      const files = listed.map((f) => ({ ...f, class: classifyPath(f.rel) }));
      const ring = withRingHash({ purpose: "toy", nouns: [], traps: [] });
      const result = groupIntoDirectives(files, {
        kind: "paths",
        ringHash: ring.hash,
        maxUnits: 40,
        maxBytes: 250_000,
        maxDirectives: 60,
        allowed: new Set(["orientation", "enforcement", "scars", "contracts", "exemplars", "reference", "unclassified"]),
      });
      expect(result.gaps.some((g) => g.reason === "unclassified" && g.territory === "src/main.ts")).toBe(true);
      expect(result.gaps.some((g) => g.territory === "package.json")).toBe(true);
      expect(files.filter((f) => f.class === "orientation").length).toBeGreaterThanOrEqual(2);
      expect(files.filter((f) => f.class === "enforcement").length).toBeGreaterThanOrEqual(4);
      expect(files.filter((f) => f.class === "scars").length).toBeGreaterThanOrEqual(2);
      expect(result.perClass.orientation).toBeGreaterThanOrEqual(1);
      expect(result.perClass.enforcement).toBeGreaterThanOrEqual(4);
      expect(files.filter((f) => f.class === "contracts").length).toBeGreaterThanOrEqual(2);
      expect(files.filter((f) => f.class === "exemplars").length).toBeGreaterThanOrEqual(2);
      expect(files.filter((f) => f.class === "reference").length).toBeGreaterThanOrEqual(1);
      expect(result.directives.length).toBeGreaterThan(5);
      for (const d of result.directives) {
        const parsed = ResearchDirective.parse(d);
        expect(parsed.decoderRingHash).toBe(ring.hash);
        expect(parsed.territory.include.length).toBeGreaterThan(0);
      }
      // singletons own their own row
      expect(result.directives.some((d) => d.territory.label === "README.md")).toBe(true);
      expect(result.directives.some((d) => d.territory.label === "CONTRIBUTING.md")).toBe(true);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  test("overflow merges smallest same-class siblings and logs every merge", () => {
    const files = Array.from({ length: 8 }, (_, i) => ({
      rel: `docs/page-${i}.md`,
      bytes: 10,
      class: "reference" as const,
    }));
    const ring = withRingHash({ purpose: "t", nouns: [], traps: [] });
    const result = groupIntoDirectives(files, {
      kind: "paths",
      ringHash: ring.hash,
      maxUnits: 2,
      maxBytes: 250_000,
      maxDirectives: 3,
      allowed: new Set(["reference", "unclassified"]),
    });
    expect(result.directives.length).toBeLessThanOrEqual(3);
    expect(result.merges.length).toBeGreaterThan(0);
    for (const m of result.merges) {
      expect(m.reason).toContain("maxDirectives");
      expect(m.class).toBe("reference");
    }
  });
});

describe("extractDocsUrls (Next.js llms.txt punctuation)", () => {
  const base = "https://nextjs.org/docs/llms.txt";

  test("markdown link plus bare url): tail dedupes to one clean URL", () => {
    const text = [
      "- [Installation](https://nextjs.org/docs/app/getting-started/installation): Learn how to create a new Next.js application.",
      "https://nextjs.org/docs/app/getting-started/installation):",
    ].join("\n");
    const urls = extractDocsUrls(text, base);
    expect(urls).toEqual(["https://nextjs.org/docs/app/getting-started/installation"]);
    expect(urls.some((u) => u.endsWith("):"))).toBe(false);
  });

  test("heading markdown link extracts once", () => {
    const text = "## [Getting Started](https://nextjs.org/docs/app/getting-started)\n";
    expect(extractDocsUrls(text, base)).toEqual([
      "https://nextjs.org/docs/app/getting-started",
    ]);
  });

  test("keeps query strings and hashes; only strips trailing markdown punctuation", () => {
    const text = [
      "[q](https://nextjs.org/docs/app?foo=1#bar)",
      "https://nextjs.org/docs/app?foo=1#bar).",
    ].join("\n");
    expect(extractDocsUrls(text, base)).toEqual([
      "https://nextjs.org/docs/app?foo=1#bar",
    ]);
  });
});

describe("docs vs repo unit caps", () => {
  test("URL territories use docsMaxUnits; path territories use maxUnits", () => {
    expect(unitsForTerritory("urls", { maxUnits: 40, docsMaxUnits: 10 })).toBe(10);
    expect(unitsForTerritory("paths", { maxUnits: 40, docsMaxUnits: 10 })).toBe(40);
  });

  test("25 docs URLs at docsMaxUnits=10 yield leaves of size <= 10", () => {
    const files = Array.from({ length: 25 }, (_, i) => ({
      rel: `https://nextjs.org/docs/page-${i}`,
      bytes: 0,
      class: "reference" as const,
    }));
    const ring = withRingHash({ purpose: "t", nouns: [], traps: [] });
    const maxUnits = unitsForTerritory("urls", { maxUnits: 40, docsMaxUnits: 10 });
    const result = groupIntoDirectives(files, {
      kind: "urls",
      ringHash: ring.hash,
      maxUnits,
      maxBytes: 250_000,
      maxDirectives: 60,
      allowed: new Set(["reference", "unclassified"]),
    });
    expect(maxUnits).toBe(10);
    expect(result.directives.length).toBeGreaterThan(1);
    for (const d of result.directives) {
      expect(d.territory.include.length).toBeLessThanOrEqual(10);
    }
  });

  test("same 25 paths at repo maxUnits=40 stay in one leaf", () => {
    const files = Array.from({ length: 25 }, (_, i) => ({
      rel: `docs/page-${i}.md`,
      bytes: 0,
      class: "reference" as const,
    }));
    const ring = withRingHash({ purpose: "t", nouns: [], traps: [] });
    const maxUnits = unitsForTerritory("paths", { maxUnits: 40, docsMaxUnits: 10 });
    const result = groupIntoDirectives(files, {
      kind: "paths",
      ringHash: ring.hash,
      maxUnits,
      maxBytes: 250_000,
      maxDirectives: 60,
      allowed: new Set(["reference", "unclassified"]),
    });
    expect(maxUnits).toBe(40);
    expect(result.directives).toHaveLength(1);
    expect(result.directives[0].territory.include).toHaveLength(25);
  });
});

describe("isDocumentUrl (build assets must never enter a docs corpus)", () => {
  const base = "https://example.github.io/proj";
  test("rejects the asset kinds a VitePress page links in its head", () => {
    for (const p of [
      "/assets/style.D49WVbqx.css",
      "/assets/chunks/framework.BaPQmObz.js",
      "/assets/chunks/theme.Cs4YiyRq.mjs",
      "/assets/inter-roman-latin.Di8DUHzh.woff2",
      "/vp-icons.css",
      "/logo.svg",
      "/hero.png",
      "/bundle.js.map",
    ]) {
      expect(isDocumentUrl(base + p)).toBe(false);
    }
  });
  test("accepts extensionless doc routes and .html/.md", () => {
    for (const p of [
      "/en/reference/",
      "/en/reference/subsystems/core",
      "/en/develop/cordis-tutorial/04-events",
      "/docs/guide.html",
      "/docs/guide.md",
    ]) {
      expect(isDocumentUrl(base + p)).toBe(true);
    }
  });
  test("a non-URL is not a document", () => {
    expect(isDocumentUrl("not a url")).toBe(false);
  });
});

describe("urlsFromVitepressHashmap", () => {
  test("maps underscore-separated source keys onto routes", async () => {
    const hashmapUrl = "https://example.github.io/proj/hashmap.json";
    const body = JSON.stringify({
      "en_index.md": "a1",
      "en_reference_index.md": "b2",
      "en_reference_subsystems_core.md": "c3",
      "en_reference_cordis-api_context.md": "d4",
    });
    const real = globalThis.fetch;
    globalThis.fetch = (async () => new Response(null, { status: 200 })) as unknown as typeof fetch;
    try {
      const urls = await urlsFromVitepressHashmap(body, hashmapUrl);
      expect(urls).toEqual([
        "https://example.github.io/proj/en/",
        "https://example.github.io/proj/en/reference/",
        "https://example.github.io/proj/en/reference/cordis-api/context",
        "https://example.github.io/proj/en/reference/subsystems/core",
      ]);
    } finally {
      globalThis.fetch = real;
    }
  });

  test("drops routes that do not resolve (underscore ambiguity is HEAD-verified)", async () => {
    const hashmapUrl = "https://example.github.io/proj/hashmap.json";
    const body = JSON.stringify({ "en_some_page.md": "x", "en_real.md": "y" });
    const real = globalThis.fetch;
    globalThis.fetch = (async (input: string | URL | Request) => {
      const href = typeof input === "string" ? input : input.toString();
      return new Response(null, { status: href.endsWith("/en/real") ? 200 : 404 });
    }) as unknown as typeof fetch;
    try {
      const urls = await urlsFromVitepressHashmap(body, hashmapUrl);
      expect(urls).toEqual(["https://example.github.io/proj/en/real"]);
    } finally {
      globalThis.fetch = real;
    }
  });

  test("malformed json yields nothing rather than throwing", async () => {
    expect(await urlsFromVitepressHashmap("{not json", "https://x.dev/hashmap.json")).toEqual([]);
  });
});

