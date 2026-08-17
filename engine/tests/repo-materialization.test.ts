import { describe, expect, test } from "bun:test";
import { existsSync, mkdirSync, readdirSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import { runResearch } from "../src/index.ts";
import { orient } from "../src/phases/orient.ts";
import { resolveCaller } from "../src/resolve-caller.ts";
import { ResearchRequest } from "../src/contract.ts";

/** A real git repo on disk, clonable over file:// — no network, still a real `git clone`. */
function realRepo(name: string): string {
  const root = join(tmpdir(), `rosetta-src-${name}-${Date.now()}`);
  mkdirSync(root, { recursive: true });
  writeFileSync(join(root, "README.md"), "# Tiny\n\nA widget kit for testing.\n");
  writeFileSync(join(root, "CONTRIBUTING.md"), "no stubs\n");
  spawnSync("git", ["init"], { cwd: root });
  spawnSync("git", ["add", "-A"], { cwd: root });
  spawnSync("git", ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "i"], { cwd: root });
  return root;
}

function cloneDirs(): string[] {
  return readdirSync(tmpdir()).filter((d) => d.startsWith("rosetta-clone-"));
}

const caller = () => resolveCaller({ model: "grok-4.6", harness: "grok" });

describe("repo materialization happens before orient", () => {
  // REGRESSION: a --repo-url run with no local clone reached orient with nothing local, so the
  // orientation directive had include: [] and ResearchDirective.parse threw an unhandled
  // ZodError that killed the whole run (exit 1, nothing written).
  test("repoUrl with no repoPath is cloned and censused, not crashed", async () => {
    const src = realRepo("ok");
    const before = new Set(cloneDirs());
    try {
      const result = await runResearch({
        tech: "Tiny",
        techSlug: "tiny",
        sourceMode: "repo",
        sources: { repoUrl: `file://${src}` },
        caller: caller(),
        censusOnly: true,
        depth: "normal",
      });
      expect(result.status).not.toBe("BLOCKED");
      // Directives can only exist if the clone really happened and census read real files.
      expect(result.ledger.censused).toBeGreaterThan(0);

      // The tmp clone this run created must not be left behind.
      const leaked = cloneDirs().filter((d) => !before.has(d));
      expect(leaked).toEqual([]);
    } finally {
      rmSync(src, { recursive: true, force: true });
    }
  });

  test("an unclonable repoUrl BLOCKS with a named reason instead of throwing", async () => {
    const result = await runResearch({
      tech: "Nope",
      techSlug: "nope",
      sourceMode: "repo",
      sources: { repoUrl: `file://${join(tmpdir(), "rosetta-does-not-exist-ever")}` },
      caller: caller(),
      censusOnly: true,
      depth: "normal",
    });
    expect(result.status).toBe("BLOCKED");
    expect(result.blockReason).toContain("SOURCE_UNAVAILABLE");
    expect(result.ledger.gaps.length).toBeGreaterThan(0);
  });

  test("a caller-supplied repoPath is used as-is and never deleted", async () => {
    const src = realRepo("keep");
    try {
      const result = await runResearch({
        tech: "Tiny",
        techSlug: "tiny",
        sourceMode: "repo",
        sources: { repoPath: src },
        caller: caller(),
        censusOnly: true,
        depth: "normal",
      });
      expect(result.status).not.toBe("BLOCKED");
      expect(existsSync(src)).toBe(true);
      expect(existsSync(join(src, "README.md"))).toBe(true);
    } finally {
      rmSync(src, { recursive: true, force: true });
    }
  });
});

describe("orient degrades instead of throwing when it has nothing to read", () => {
  test("no repoPath and no docsUrl returns the seed ring", async () => {
    const req = ResearchRequest.parse({
      tech: "Ghost",
      techSlug: "ghost",
      sourceMode: "docs",
      sources: {},
      caller: caller(),
      depth: "normal",
    });
    const ring = await orient(req);
    expect(ring.hash).toBeTruthy();
    expect(ring.purpose).toContain("Ghost");
  });
});
