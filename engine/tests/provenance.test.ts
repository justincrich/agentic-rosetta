import { describe, expect, test } from "bun:test";
import { mkdirSync, writeFileSync, rmSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import { pinRepo, resolveRepoTree, pinDocs, runCensus } from "../src/phases/census.ts";
import { corpusDigest } from "../src/hash.ts";
import { withRingHash } from "../src/hash.ts";
import { emitNrra } from "../src/phases/emit.ts";
import { runResearch } from "../src/index.ts";
import { resolveCaller } from "../src/resolve-caller.ts";

function git(cwd: string, args: string[]): string {
  const r = spawnSync("git", args, { cwd, encoding: "utf8" });
  if (r.status !== 0) throw new Error(`git ${args.join(" ")}: ${r.stderr || r.stdout}`);
  return (r.stdout || "").trim();
}

function realRepo(): string {
  const root = join(tmpdir(), `rosetta-pin-${Date.now()}-${Math.random().toString(16).slice(2)}`);
  mkdirSync(root, { recursive: true });
  writeFileSync(join(root, "README.md"), "# Toy\n");
  writeFileSync(join(root, "package.json"), JSON.stringify({ name: "toy", version: "3.2.1" }) + "\n");
  git(root, ["init"]);
  git(root, ["add", "-A"]);
  git(root, ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"]);
  git(root, ["tag", "v3.2.1"]);
  return root;
}

describe("repo source pin on a real git repo", () => {
  test("commit + describe match git; digest changes then stays", () => {
    const root = realRepo();
    try {
      const pin1 = pinRepo(root);
      const head = git(root, ["rev-parse", "HEAD"]);
      const describe = git(root, ["describe", "--tags", "--always"]);
      expect(pin1.commit).toBe(head);
      expect(pin1.describe).toBe(describe);
      expect(pin1.manifest_version).toBe("3.2.1");
      expect(pin1.corpus_sha256).toBe(
        corpusDigest((pin1.unitPins ?? []).map((u) => ({ locator: u.locator, sha256: u.sha256 }))),
      );

      writeFileSync(join(root, "NEW.md"), "added\n");
      git(root, ["add", "-A"]);
      git(root, ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "add"]);
      const pin2 = pinRepo(root);
      expect(pin2.corpus_sha256).not.toBe(pin1.corpus_sha256);
      expect(pin2.commit).not.toBe(pin1.commit);

      const pin3 = pinRepo(root);
      expect(pin3.corpus_sha256).toBe(pin2.corpus_sha256);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  test("--repo-url without --repo-path clones a file:// remote twice", async () => {
    const src = realRepo();
    const bare = join(tmpdir(), `rosetta-bare-${Date.now()}.git`);
    try {
      git(src, ["clone", "--bare", src, bare]);
      const url = `file://${bare}`;
      const a = resolveRepoTree({ repoUrl: url });
      expect(a.cloned).toBe(true);
      expect(a.path.length).toBeGreaterThan(0);
      const listed = spawnSync("git", ["-C", a.path, "rev-parse", "HEAD"], { encoding: "utf8" });
      expect(listed.status).toBe(0);
      const b = resolveRepoTree({ repoUrl: url });
      expect(b.cloned).toBe(true);
      const headA = spawnSync("git", ["-C", a.path, "rev-parse", "HEAD"], { encoding: "utf8" }).stdout.trim();
      const headB = spawnSync("git", ["-C", b.path, "rev-parse", "HEAD"], { encoding: "utf8" }).stdout.trim();
      expect(headA).toBe(headB);
      rmSync(a.path, { recursive: true, force: true });
      rmSync(b.path, { recursive: true, force: true });

      const caller = resolveCaller({ model: "grok-4.6", harness: "grok" });
      const r1 = await runResearch({
        tech: "Toy",
        techSlug: "toy",
        sourceMode: "repo",
        sources: { repoUrl: url },
        caller,
        censusOnly: true,
      });
      expect(r1.status).not.toBe("BLOCKED");
      expect(r1.provenance?.pins[0]?.commit).toBeTruthy();
      const r2 = await runResearch({
        tech: "Toy",
        techSlug: "toy",
        sourceMode: "repo",
        sources: { repoUrl: url },
        caller,
        censusOnly: true,
      });
      expect(r2.provenance?.pins[0]?.commit).toBe(r1.provenance?.pins[0]?.commit);
    } finally {
      rmSync(src, { recursive: true, force: true });
      rmSync(bare, { recursive: true, force: true });
    }
  });
});

describe("docs pin against Bun.serve ETag", () => {
  test("stores ETag; conditional GET is 304", async () => {
    const body = "docs-body-v1";
    const etag = '"abc123"';
    const server = Bun.serve({
      port: 0,
      fetch(req) {
        if (req.headers.get("if-none-match") === etag) {
          return new Response(null, { status: 304, headers: { ETag: etag } });
        }
        return new Response(body, { headers: { ETag: etag, "Content-Type": "text/plain" } });
      },
    });
    try {
      const url = `http://127.0.0.1:${server.port}/`;
      const pin = await pinDocs(url, [{ rel: url }]);
      expect(pin.etag).toBe(etag);
      expect(pin.unitPins?.[0]?.etag).toBe(etag);
      expect(pin.corpus_sha256.length).toBe(64);

      const again = await fetch(url, { headers: { "If-None-Match": pin.etag ?? "" } });
      expect(again.status).toBe(304);

      const ring = withRingHash({ purpose: "docs", nouns: [], traps: [] });
      const census = await runCensus({
        tech: "Docs",
        techSlug: "docs",
        sourceMode: "docs",
        sources: { docsUrl: url },
        caller: resolveCaller({ model: "grok-4.6", harness: "grok" }),
        maxUnits: 40,
        docsMaxUnits: 10,
        maxBytes: 250_000,
        maxWaves: 1,
        concurrency: 1,
        censusOnly: true,
        depth: "normal",
        budget: { maxDirectives: 60 },
      } as never, ring);
      expect(census.pins[0]?.etag).toBe(etag);
    } finally {
      server.stop(true);
    }
  });
});

describe("emit keeps source/confidence and writes §9b + §10", () => {
  test("promoted facts appear in fact-sources block", () => {
    const ring = withRingHash({ purpose: "p", nouns: [], traps: [] });
    const nrra = emitNrra({
      req: {
        tech: "Toy",
        techSlug: "toy",
        sourceMode: "repo",
        sources: {},
        caller: resolveCaller({ model: "grok-4.6", harness: "grok" }),
        maxUnits: 40,
        docsMaxUnits: 10,
        maxBytes: 250_000,
        maxWaves: 1,
        concurrency: 1,
        depth: "normal",
        budget: { maxDirectives: 60 },
        censusOnly: true,
      } as never,
      ring,
      ledger: {
        run_id: "r",
        tech: "Toy",
        source_mode: "repo",
        decoder_ring_hash: ring.hash,
        waves: 0,
        censused: 0,
        closed: 0,
        rows: [],
        gaps: [],
        merges: [],
        warnings: [],
        circuitBroken: false,
      },
      reports: [{
        directiveId: "t01",
        status: "closed",
        reason: null,
        facts: [
          { section: "v", key: "purpose", value: "kit", source: "README.md:1", confidence: "HIGH" },
        ],
        surprises: [],
        pointers: [],
        unitsRead: 1,
      }],
      runId: "r",
      status: "partial",
      pins: [],
    });
    expect(nrra).toContain("## 9b. Fact Sources");
    expect(nrra).toContain("## 10. Provenance");
    expect(nrra).toContain("README.md:1");
    expect(nrra).toContain('"v:purpose"');
  });
});

void readFileSync;
