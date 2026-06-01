# Changelog

All notable changes to this project are documented here.

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
