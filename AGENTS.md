# Agentic Rosetta — directory system prompt

`AGENTS.md` is the only directory-level system prompt in this repo. The nearest file
wins; nested files add rules for that tree. Read this before changing machinery.

Deeper map: [`ARCHITECTURE.md`](ARCHITECTURE.md). Storage schema: [`CONFIG.md`](CONFIG.md).
Theory: [`WHY.md`](WHY.md).

## What this repo is

This repository is the **one true source** of Rosetta *machinery*: seven skills, a
Mastra fanout engine, Python writers, and install/sync scripts.

It is **not** a knowledge-base host. No language KBs, no `agents/` triad, no
`fact-graph.json` for a real stack belong here. Those live in a **consumer**
project (on this machine that is often `~/Projects/brain`). Brain is a consumer.
Never `cd` there to “find the plugin.” Never hardcode `~/Projects/brain`.

Install ships machinery only. It does not copy KBs and does not edit a target
project’s `AGENTS.md`.

## Surfaces

| Path | Role | Edit? |
|---|---|---|
| `skills/rosetta-*` | Canonical skill bodies | yes |
| `grok-skills/` | Generated Grok pack (`/rosetta:research`, not `/rosetta:rosetta-research`) | **no** — rematerialize |
| `engine/` | Fanout program `[A0]`–`[A6]` | yes (TS / Bun) |
| `scripts/rosetta/` | Graph / registry / provenance | yes (Python) |
| `scripts/kb-delivery/` | Excerpt + embed | yes |
| `assets/rosetta_config.py` | Canonical storage resolver | yes, then `./sync-assets.sh` |
| `skills/*/assets/rosetta_config.py` | Distributed copies (Claude `/plugin install` never runs `install.sh`) | **no** — sync from `assets/` |
| `~/.rosetta/` | Device home (not in git) | config only |

Two audiences, two homes — do not mix them:

- **Plugin engineer** (you, in this repo): change machinery. This file governs you.
- **KB author** (usually standing in the consumer project that owns `agents/`): run
  `/rosetta-bootstrap` and friends. Those procedures live in the skills, not here.

## Iron writers

Skills are thin wrappers. Behavior lives in programs. Do not re-implement these
in skill prose.

| Artifact | Only writer | Gate |
|---|---|---|
| `fact-graph.json` | `scripts/rosetta/write-graph.py` | ledger closed + matching `run_id` + `decoder_ring_hash`; `--create` refuses if the graph exists |
| Agent `<!-- rosetta-kb -->` block | `scripts/kb-delivery/embed-graph.sh` (via `generate_excerpt.py`) | `--check` must match the render; prose outside the sentinels is never touched |
| Storage path | `assets/rosetta_config.py` | CODE, not skill prose. Three *locations* kept byte-identical by `./sync-assets.sh`: repo `assets/`, each `skills/*/assets/` copy, and `$ROSETTA_HOME/bin/` |
| Research facts (NRRA + ledger) | `engine/` | fail closed; no serial crawl |

## Iron rules

1. **Never hardcode `~/Projects/brain`.** Resolve the plugin with `$ROSETTA_PLUGIN`
   or by walking up until `engine/src/research.ts` exists. Resolve the *target
   project* with `--root` / `$ROSETTA_PROJECT`. If neither is set, scripts take
   a script-parent that already has `agents/` (test fixtures), else the nearest
   `.git` / `.rosetta` ancestor of cwd. **This checkout has `.git`**, so a bare
   run from here targets the plugin — the wrong tree. Pass `--root` unless you
   are already cwd’d in the consumer.
2. **Only `write-graph.py` writes `fact-graph.json`.** Skills never paste JSON
   into `agent.md`. Validate / ab-test / stress-test never write graphs.
3. **Only `embed-graph.sh` writes the `rosetta-kb` region.** Generated, not
   hand-edited. Unknown slug to `--sync` exits non-zero and does **not** iterate
   the fleet.
4. **Paths come from the resolver** (`$ROSETTA_CONFIG` → skill `assets/` →
   `$ROSETTA_HOME/bin`). Writes go to the configured root. Reads search
   configured root, then project, then device. See [`CONFIG.md`](CONFIG.md).
5. **Research has one path: the engine.** Missing `engine/` or ACP → STOP and
   name the reason. No serial crawl. No “floor parallelism you MAY enable.”
   No 3-tier ladder (`/assimilate` → jina → WebSearch) as a substitute.
6. **`--model` is required.** Never omit it. Never invent one. Never default it.
7. **Fail closed.** Ledger open → no graph write. Engine `status=BLOCKED` →
   write nothing else. `provenance.py --check-stale` exit `1` is
   *undeterminable*, not current.
8. **No stubs.** No fake success, no placeholder transport, no mocked ACP or
   fake filesystem in tests that claim the real path works. Existing tests use
   real temp trees and committed fixtures under `scripts/testdata/`.
9. **Copies stay identical.** `./sync-assets.sh --check` for the resolver.
   `scripts/assert-no-drift.sh` for harness skill copies. Grok uses the plugin
   namespace — do not install `~/.grok/skills/rosetta-*` (those become
   `/user:rosetta-research` and collide).
10. **`docs` stay in the repo that owns `agents/`.** A graph written outside that
    repo is invisible to `registry.py` / `embed-graph.sh`. That is why `docs`
    defaults to `"project"`.

## Storage vs tracking

These are not the same system.

- **Storage roots** (`~/.rosetta/config.json`, optional
  `<project>/.rosetta/config.json`) decide where *new* `research` / `docs` /
  `probes` are written. Built-in default is `"project"`. A machine may point
  `research` or `probes` at a central repo (this machine’s device file points
  those two at `~/Projects/brain`). That is an *opt-in*, not a plugin default.
- **Tracking** is `scripts/rosetta/registry.py`: an exhaustive walk of **one**
  project for `fact-graph.json`. The index is
  `<that project>/.rosetta/registry.json`. There is no machine-wide catalog.
  `~/.rosetta` is not a KB index.

`registry.py` recognizes three on-disk layouts:

- `.rosetta/docs/<slug>/fact-graph.json`
- `docs/<slug>/fact-graph.json`
- `agents/<name>/kb/fact-graph.json`

## Env and flags

| Name | Meaning |
|---|---|
| `$ROSETTA_PLUGIN` | Plugin checkout (directory that contains `engine/` + `scripts/`) |
| `$ROSETTA_PROJECT` | Consumer project being scanned / written |
| `$ROSETTA_HOME` | Device home; default `~/.rosetta` |
| `$ROSETTA_CONFIG` | Explicit path to `rosetta_config.py` |
| `$ROSETTA_WORKER` | Set to `1` inside a worker; preflight STOPs (no recursion) |
| `$ROSETTA_WORKER_TIMEOUT_MS` | Worker timeout; default `600000` |
| `--root` | Same as `$ROSETTA_PROJECT` on registry / write-graph / embed / provenance |

## Local Domain Experts

No in-tree specialist triad. This repo is not a kb-sprint consumer by default.
When dispatching work, use the live roster:

| Domain | Planner | Implementer | Reviewer |
|---|---|---|---|
| Fanout engine (`engine/`) | `mastra-planner` | `mastra-implementer` | `mastra-reviewer` |
| Python scripts | `python-planner` | `python-implementer` | `python-reviewer` |
| Skills / plugin packaging | primary session + `/skill-plan` | `ai-tooling-implementer` | `ai-tooling-reviewer` |

## Verification (before you say done)

There is **no** `.github/` CI. The gate is local. Run from the repo root:

```bash
./sync-assets.sh --check
python3 scripts/materialize-grok-skills.py --check
scripts/assert-no-drift.sh --scripts-only
python3 assets/rosetta_config.py --selftest
python3 scripts/rosetta/test_registry.py
python3 scripts/rosetta/test_write_graph.py
python3 scripts/rosetta/test_provenance.py
python3 scripts/kb-delivery/test_assign.py
python3 scripts/kb-delivery/test_generate_excerpt.py
python3 scripts/kb-delivery/test_multigraph.py
bash scripts/kb-delivery/test-embed-graph.sh
python3 scripts/test_materialize_grok_skills.py
( cd engine && bun test && bun run typecheck )
```

`assert-no-drift.sh` *without* `--scripts-only` also compares
`~/.claude/skills`, `~/.codex/skills`, and `~/.config/opencode/skills` to
`skills/`. That fails on a machine that has not run `./sync.sh`. Prefer
`--scripts-only` unless you just distributed.

Touched only `engine/` → `bun test` + `bun run typecheck` is the minimum, plus
any Python test whose fixture you changed. Touched a `SKILL.md` → rematerialize
Grok + `--scripts-only`. Touched `assets/rosetta_config.py` → `./sync-assets.sh`
(write, not just `--check`).

## Version files

`CHANGELOG.md` is the narrative of what shipped. Installer-facing versions
currently **lag** and are not a single source of truth:

| File | Version on disk (as of 0.4.3 narrative) |
|---|---|
| `CHANGELOG.md` | `0.4.3` |
| `.claude-plugin/plugin.json` | `0.4.1` |
| `.grok-plugin/plugin.json` | `0.4.1` |
| `.claude-plugin/marketplace.json` | `0.3.0` |
| `engine/package.json` | `0.4.0` |

Bump the installer files in the **same commit** as the feature they describe.
Do not treat any one of them as secretly authoritative until they are aligned.

## Where to go next

| File | Open when |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | You need the end-to-end pipeline |
| [`engine/AGENTS.md`](engine/AGENTS.md) | Changing fanout / census / workers |
| [`scripts/AGENTS.md`](scripts/AGENTS.md) | Changing writers / registry / embed |
| [`skills/AGENTS.md`](skills/AGENTS.md) | Changing skill prose |
| [`grok-skills/AGENTS.md`](grok-skills/AGENTS.md) | Tempted to edit the Grok pack |
| [`CONFIG.md`](CONFIG.md) | Storage roots, `doctor`, precedence |
| [`WHY.md`](WHY.md) | Why two layers / three registers |
