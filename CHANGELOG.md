# Changelog

All notable changes to this project are documented here.

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
