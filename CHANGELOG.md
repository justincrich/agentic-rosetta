# Changelog

All notable changes to this project are documented here.

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
