import { afterEach, describe, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { BUILTIN_CONCURRENCY, resolveConcurrency } from "../src/device-limits.ts";

const temps: string[] = [];

function scratch(): string {
  const dir = mkdtempSync(join(tmpdir(), "rosetta-limits-"));
  temps.push(dir);
  return dir;
}

function writeCfg(dir: string, concurrency: unknown): string {
  const cfgDir = join(dir, ".rosetta");
  mkdirSync(cfgDir, { recursive: true });
  const path = join(cfgDir, "config.json");
  writeFileSync(path, JSON.stringify({ version: 1, limits: { concurrency } }) + "\n");
  return path;
}

afterEach(() => {
  for (const dir of temps.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

describe("resolveConcurrency", () => {
  test("builtin 8 when nothing is set", () => {
    const home = scratch();
    expect(
      resolveConcurrency({ env: {}, cwd: scratch(), home }),
    ).toBe(BUILTIN_CONCURRENCY);
  });

  test("device ~/.rosetta/config.json limits.concurrency", () => {
    const home = scratch();
    writeFileSync(join(home, "config.json"), JSON.stringify({ limits: { concurrency: 4 } }));
    expect(resolveConcurrency({ env: {}, cwd: scratch(), home })).toBe(4);
  });

  test("project config beats device", () => {
    const home = scratch();
    writeFileSync(join(home, "config.json"), JSON.stringify({ limits: { concurrency: 2 } }));
    const project = scratch();
    writeCfg(project, 3);
    expect(resolveConcurrency({ env: {}, cwd: project, home })).toBe(3);
  });

  test("ROSETTA_CONCURRENCY beats config files", () => {
    const home = scratch();
    writeFileSync(join(home, "config.json"), JSON.stringify({ limits: { concurrency: 2 } }));
    const project = scratch();
    writeCfg(project, 3);
    expect(
      resolveConcurrency({ env: { ROSETTA_CONCURRENCY: "4" }, cwd: project, home }),
    ).toBe(4);
  });

  test("CLI beats env and files", () => {
    const home = scratch();
    writeFileSync(join(home, "config.json"), JSON.stringify({ limits: { concurrency: 2 } }));
    expect(
      resolveConcurrency({
        cli: 1,
        env: { ROSETTA_CONCURRENCY: "4" },
        cwd: scratch(),
        home,
      }),
    ).toBe(1);
  });

  test("out-of-range device value is skipped, not clamped", () => {
    const home = scratch();
    writeFileSync(join(home, "config.json"), JSON.stringify({ limits: { concurrency: 99 } }));
    expect(resolveConcurrency({ env: {}, cwd: scratch(), home })).toBe(BUILTIN_CONCURRENCY);
  });

  test("malformed device JSON is skipped", () => {
    const home = scratch();
    writeFileSync(join(home, "config.json"), "{not-json");
    expect(resolveConcurrency({ env: {}, cwd: scratch(), home })).toBe(BUILTIN_CONCURRENCY);
  });

  test("ROSETTA_HOME locates the device file", () => {
    const home = scratch();
    writeFileSync(join(home, "config.json"), JSON.stringify({ limits: { concurrency: 4 } }));
    expect(
      resolveConcurrency({ env: { ROSETTA_HOME: home }, cwd: scratch() }),
    ).toBe(4);
  });
});
