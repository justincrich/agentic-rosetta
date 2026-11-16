# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Removed
- **The MCP service and the snapshot delivery path.** `worker/` (the read-only
  `rosetta-kb-mcp` Cloudflare Worker), `publish_snapshot.py`, `sync_agents.py`,
  `doc_refs.py`, `project_agents.py` and their four test suites are gone. The
  service is being rebuilt in `hackerpug-ai/agent-content`, which already holds
  the agents and knowledge graphs it serves. This repo is machinery only: the
  skills, the fanout engine, and the graph writers. `scripts/kb-delivery/` keeps
  `embed-graph.sh`, `generate_excerpt.py` and `assign.py`, which the skills use.

## [0.4.6] — 2026-08-25

### Changed
- **Model pass-through.** The fanout engine no longer ships hardcoded per-harness
  model rosters. Any model string passes through to the ACP binary; the binary or
  provider surfaces unknown-model / key / quota errors, which the worker classifies
  into `AUTH` / `MODEL` / `QUOTA` and reports as educational messages naming the
  harness + model. `MODEL_UNRESOLVED` is gone; only an empty `--model` is rejected
  (`MODEL_MISSING`, exit 2 — still never defaulted).
- **`--provider` (optional, pi).** `research.ts` accepts `--provider NAME`; for
  the `pi` harness it composes `provider/model`; model-provided harnesses
  (claude / codex / opencode / grok) ignore it. Never defaulted.
- **pi is the fifth harness.** Detected via `PI_CODING_AGENT` / `PI_SESSION_ID`;
  its ACP server is the separate `pi-acp` npm adapter (spawns `pi --mode rpc`),
  installed globally on the machine running the engine.

### Fixed
- Worker rejections no longer claim “after retry” when the failure was classified
  as AUTH / MODEL / QUOTA (those skip the narrowed-prompt retry).

## [0.4.5] — 2026-08-21

### Added
- **`rosetta-upgrade` Mode C (`--topics`).** Expand an existing KB at the current
  framework pin: research those specialties, merge via `write-graph.py --update`,
  add or extend 3-lens concept docs, re-inject, gate on accept / improve / roll
  back. Does not move `v.pkg` / `v.version` (that is Mode B). `--topics` + `--url`
  is Mode C — extra URLs are research sources, not a pin bump. Empty `--topics`
  stops; it does not fall through to the benchmark.
- Mode A remaining-gap expansion now *invokes* Mode C instead of inlining a
  second write path. Validate / ab-test / stress-test `--topics` pointers name
  Mode C so they match dispatch.

### Changed
- Upgrade dispatch is `--topics` → C, else `--url` → B, else A. Installer
  versions (`plugin.json`) bump to 0.4.5 with this skill.

## [0.4.4] — 2026-08-16

### Added
- **Device live-worker cap.** `limits.concurrency` in `~/.rosetta/config.json`
  (or a project `.rosetta/config.json`) is the default `--concurrency` when the
  flag is omitted. `$ROSETTA_CONCURRENCY` sits between the CLI flag and the
  files. Built-in remains 8.

## [0.4.3] — 2026-08-14

### Added
- **Source identity.** Census records git HEAD / `describe` / per-unit hashes (and
  docs ETag / Last-Modified / body hash) as a `corpus_sha256`. Sibling
  `provenance.json`, `fact-sources.json`, and `.history/manifest.json` sit next
  to `fact-graph.json` — not inside it.
- **`provenance.py --check-stale`.** Exit 0 current / 10 stale / 1 undeterminable.
  `git ls-remote` for repos; conditional GET for docs.
- **`--adopt`** stamps recoverable version fields on legacy graphs.
- **`--probe-sources`** / `--repo-url` without a local path (shallow-clones or
  names the clone failure).
- Excerpt markers carry `upstream=` and `corpus=`. Shared `version_label.py`
  accepts both `v.pinned` and `v.pinned_date`.

### Changed
- `kb_version` bump is derived (major / minor / patch) from the section diff.
  Never write `"0.0.0"` as a fake upstream version.
- Registry validate reports stored-vs-recomputed `kb_version` drift.
- `rosetta-upgrade` Mode B gains R0 PREFLIGHT; R4 changeset is dict-keyed-by-section.

## [0.4.2] — 2026-08-14

### Fixed
- **Docs census URL cleanup.** Next.js-style `llms.txt` lines (`[Title](url): prose`)
  no longer produce a dead `url):` twin next to the real URL. Markdown links win;
  bare URLs strip trailing `):,:.]`.
- **Docs leaf size.** URL territories default to `docsMaxUnits=10` (repo
  `maxUnits` stays 40). `--max-units` still overrides both. Pointer follow-up
  waves use the same split.
- **Worker timeout.** Default is 10 minutes (`DEFAULT_WORKER_TIMEOUT_MS=600000`).
  `ROSETTA_WORKER_TIMEOUT_MS` still overrides. 180s was killing every Next.js
  reference leaf.

## [0.4.1] — 2026-08-13

### Changed
- **Grok slash names are short.** Plugin name stays `rosetta`; skill ids in the
  Grok pack are `research`, `bootstrap`, `upgrade`, `validate`, `sync`,
  `ab-test`, `stress-test`. Slash: `/rosetta:research`, not
  `/rosetta:rosetta-research`. Canonical `skills/rosetta-*` (Claude / Codex /
  OpenCode / skillshare) is unchanged. Generated tree: `grok-skills/`.

## [0.4.0] — 2026-08-13

### Added
- **Fanout engine** (`engine/`) — Mastra workflow, `@mastra/acp` workers inherit the
  invoking harness + model. Stages `[A0]`–`[A6]`: preflight, orient, census, fanout
  waves, ledger close, reconcile, emit NRRA. Fail closed; never a serial crawl.
- **Census** — deterministic `git ls-files` / docs crawl, first-match class table,
  `MAX_UNITS` 40 / 250 KB grouping, named singletons, logged overflow merges.
- **`scripts/rosetta/write-graph.py`** — the only writer of `fact-graph.json`
  (`--create` / `--update` / `--check`), ledger + `run_id` gate, stamp, legacy warn.
- **Vendored KB-delivery** — `registry.py`, `embed-graph.sh`, `generate_excerpt.py`,
  `assign.py` + tests, project-root aware (`--root` / `$ROSETTA_PROJECT`).
- Skills: `rosetta-sync`, `rosetta-ab-test`, `rosetta-stress-test`.
- `grok` harness in `install.sh`. Drift guard `scripts/assert-no-drift.sh`.
- `sync.sh` — skillshare extras + install from this repo (the local distributor).

### Changed
- `rosetta-research` is a thin engine wrapper. Floor-parallelism clause deleted.
- Bootstrap / upgrade graph writes go through `write-graph.py`. Validate / ab-test /
  stress-test no longer write graphs.
- Brain is a consumer; agentic-rosetta is the one true source.

## [0.3.0] — 2026-08-08

### Added
- **Configurable storage roots.** Research notes, knowledge bases, and probe catalogs can each be
  kept in the project (default, unchanged), on the device (`~/.rosetta`), or at any path — set
  independently per artifact class. Previously all three were hardcoded relative to whatever project
  the session ran in, so research and probes stranded themselves across repos, split from the KB and
  agents that consume them.
- `assets/rosetta_config.py` — the deterministic resolver. Verbs: `path`, `root`, `find`, `show`,
  `init`, `doctor`, `--selftest`. Precedence: CLI flag > `<project>/.rosetta/config.json` >
  `$ROSETTA_HOME/config.json` (default `~/.rosetta`) > built-in `project`. Path resolution is CODE,
  not skill prose, so it happens the same way every run.
- `~/.rosetta/` device home — `config.json` + `bin/rosetta_config.py`, created by `install.sh`,
  never clobbered on re-install.
- **Read-side search order** — configured root, then project, then device. A KB that already exists
  in a project keeps being found after you point the config elsewhere; a config change orphans nothing.
- `doctor` — reports artifacts sitting outside their configured root; `--apply` moves them (dry run
  by default, never overwrites, `--exclude` to skip a repo). Only checks centralized classes: a class
  rooted at `project` is per-repo by design.
- `sync-assets.sh` — keeps the canonical resolver byte-identical across all three locations it lives
  in (repo `assets/`, each skill's `assets/`, `~/.rosetta/bin/`); `--check` fails on drift and is
  wired into `--selftest`.
- `CONFIG.md` — schema, precedence, root forms, degradation, and the agent-embed caveat.
- `uninstall.sh --purge` — also removes `$ROSETTA_HOME`. Without it, your config and any
  device-rooted artifacts are preserved.

### Changed
- `install.sh` now ships whole skill **directories** (was: `SKILL.md` only), so `assets/` travels
  with each skill — required because Claude Code's native `/plugin install` never runs `install.sh`.
- All four skills resolve storage roots instead of hardcoding `.rosetta/...`:
  `rosetta-research` (§ Storage + NRRA `storage.local_path` / `storage.resolved_by`),
  `rosetta-bootstrap` (new `[0] RESOLVE STORAGE ROOTS`), `rosetta-upgrade` (R1 locate, `.history`
  snapshot, docs/probes/MIGRATION writes), `rosetta-validate` (`[1]` discovery via `find`).

### Notes
- **Defaults are unchanged.** With no config anywhere, every path resolves exactly as before.
- A knowledge base written outside the repo that owns `agents/` is invisible to the agent-embed
  pipeline (`registry.py` walks that repo). `docs` therefore defaults to `project`, and `doctor`
  warns when the resolved docs root has no sibling `agents/`.

## [0.2.0] — 2026-06-03

### Added
- `rosetta-upgrade` **Refresh mode** (`--url`) — point at a framework's release notes / breaking-changes / migration URL to bring a KB up to the latest version: snapshot the old graph → research current state (via `rosetta-research`) → diff → bump the fact graph + 3-lens docs → re-inject into the agent triad → re-validate → gate on accept / improve / roll back. The existing before/after benchmark becomes **Mode A**; `--url` selects **Mode B**.
- **Versioning model** for keeping a KB current without bloating the always-on fact graph:
  - `.rosetta/docs/{tech}/.history/{date}-v{n}.json` — snapshot of the prior fact graph before each refresh (audit + rollback target), indexed by `.history/manifest.json`.
  - `.rosetta/docs/{tech}/MIGRATION.md` — per-refresh changelog (new / changed / deprecated old→new table + sources), loaded on demand.
  - Version-tagged `x` (anti-pattern) entries — old→new transitions ride the existing false-friend channel (e.g. `"electron-trpc (pre-v43)" → "native ipc (v43+)"`) so agents actively avoid deprecated APIs. **Deprecation aging** retires older transitions from the lean graph into `MIGRATION.md`.

### Changed
- `rosetta-upgrade` frontmatter/description, `README.md` (skill table + lifecycle + Quick Start), and `WHY.md` (new "Keeping Knowledge Current" section) updated to document the two modes. `rosetta-research` is **unchanged** — Refresh mode scopes version research through its existing `--focus`/`--topics` inputs.

## [0.1.0] — 2026-06-01

Initial standalone release, extracted from the `brain` monorepo.

### Added
- `.claude-plugin/plugin.json` + `marketplace.json` — installable as a Claude Code plugin via `/plugin`.
- `rosetta-research` skill — 3-tier graceful research degradation (delegate to `/assimilate` & `/deep-research` → premium MCPs `jina`/`exa` → built-in `WebSearch`/`WebFetch`). Research MCPs are optional, never required.
- `reference/universal-anti-patterns.md` — canonical 28 universal anti-patterns (language-agnostic machinery).
- Cross-harness `install.sh` / `uninstall.sh` (claude, codex, opencode, pi, droid, or `all`).

### Changed
- Converted skills to Claude Code folder form (`skills/<name>/SKILL.md`).
- `rosetta-bootstrap` steps [2]/[3] now delegate research to `rosetta-research`.
- `rosetta-validate` gained required frontmatter; title/usage reconciled from the old "rosetta-stress-test".
- `rosetta-upgrade` repointed off the deleted `probes/universal.md`.

### Removed
- Bundled Kotlin/Swift knowledge bases and per-technology probes. **The plugin ships the machinery, not the knowledge** — generate a KB for your stack with `/rosetta-bootstrap`.
- Old install behavior that copied language docs into the target project and edited its `AGENTS.md`.
