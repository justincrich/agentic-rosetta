# DOCS-01: Skills, docs, Grok pack and version files follow the required config

> Assignee: ai-tooling-implementer
AGENT: implementer=ai-tooling-implementer | reviewer=ai-tooling-reviewer
> Depends on: HANDOFF-01 (needs the final config shape and the agent-content migration it proves, which the docs describe)
> Priority: P1
> Type: feature
> Files: skills/rosetta-research/SKILL.md, skills/rosetta-validate/SKILL.md, skills/rosetta-bootstrap/SKILL.md, skills/rosetta-upgrade/SKILL.md, skills/rosetta-sync/SKILL.md, skills/rosetta-stress-test/SKILL.md, skills/rosetta-ab-test/SKILL.md, grok-skills/, README.md, CONFIG.md, AGENTS.md, ARCHITECTURE.md, scripts/AGENTS.md, CHANGELOG.md, .claude-plugin/plugin.json, .grok-plugin/plugin.json, .claude-plugin/marketplace.json
> Patterns: minimum-diff-discipline, anti-stub
> Scope: /Users/justinrich/.config/brain/improvements/imp-update-rosetta-plugin-distribute-results-1794839963.json

## Context

Rosetta never asks for a config: in a repo with none it quietly resolves every artifact class to the built-in
"project" layout, and only the resolver reads a config at all. registry, naming, provenance, generate_excerpt,
embed-graph and the four kb-delivery scripts hardcode `.rosetta/docs` and `agents/`. Brain survives as the
`BRAIN_ROOT` variable and the `brain/docs/` citation alias, and technology data (name tables, a Cloudflare-only
condensed excerpt, real-technology fixture graphs) still ships inside the plugin.

Root cause summary: Where artifacts go is decided by a built-in 'project' default plus a machine-wide device file that points every repo at agent-content, so no repo ever declares its own layout. Only the resolver reads config; registry, naming, generate_excerpt, embed-graph and provenance each hardcode .rosetta/docs and agents/, and provenance carries one load-bearing 'brain/docs/' citation alias. Brain survives as a variable name and that alias, not as a path.

The binding scope on the `> Scope:` line carries the reproduction evidence, file:line refs, the challenger's notes
and the flattened criteria (AC-1..AC-22 across this sprint's five tasks). You may NOT touch files outside this
task's `write_allowed`. Follow this repo's AGENTS.md: the resolver is edited only in `assets/rosetta_config.py`
and copied with `./sync-assets.sh`; tests use real temp trees and committed fixtures, never mocks.

## Acceptance Criteria

- [ ] AC-22: The full AGENTS.md verification list is green except the 3 known-failing bun ACP tests; `./sync-assets.sh --check` and `python3 scripts/materialize-grok-skills.py --check` are clean.

## Test Criteria

| # | Boolean Statement | Maps To AC | Verify | Status |
|---|-------------------|------------|--------|--------|
| 22 | The full AGENTS.md verification list is green except the 3 known-failing bun ACP tests; `./sync-assets.sh --check` and `python3 scripts/materialize-grok-skills.py --check` are clean. | AC-22 | `./sync-assets.sh --check && python3 scripts/materialize-grok-skills.py --check && scripts/assert-no-drift.sh --scripts-only` | [ ] TRUE [ ] FALSE |

<!-- REQUIREMENT-CONTRACT v1 -->
<!-- {"task_id":"DOCS-01","fixtures":{"plugin_tree":{"description":"The improvement checkout P at its HEAD, read with `git grep` and the real CLIs.","seed_method":"cli","records":["git -C \"$P\" rev-parse HEAD"]}},"guardrails":{"write_allowed":["skills/rosetta-research/SKILL.md","skills/rosetta-validate/SKILL.md","skills/rosetta-bootstrap/SKILL.md","skills/rosetta-upgrade/SKILL.md","skills/rosetta-sync/SKILL.md","skills/rosetta-stress-test/SKILL.md","skills/rosetta-ab-test/SKILL.md","grok-skills/","README.md","CONFIG.md","AGENTS.md","ARCHITECTURE.md","scripts/AGENTS.md","CHANGELOG.md",".claude-plugin/plugin.json",".grok-plugin/plugin.json",".claude-plugin/marketplace.json"]},"requirements":[{"id":"AC-22","description":"The full AGENTS.md verification list is green except the 3 known-failing bun ACP tests; `./sync-assets.sh --check` and `python3 scripts/materialize-grok-skills.py --check` are clean.","verify":"./sync-assets.sh --check && python3 scripts/materialize-grok-skills.py --check && scripts/assert-no-drift.sh --scripts-only","scenario":{"id":"AC-22","primary":true,"test_tier":"integration","verification_service":"the repo's own drift checks and the AGENTS.md verification list","topology":"single-node","negative_control":{"would_fail_if":["fix reverted (the defect reproduces)","the config gate removed or stubbed so the command falls back to the built-in default","the check reads a hardcoded `.rosetta/docs` / `agents` path instead of the config"]},"evidence":{"artifact_type":"stdout","required_capture":true},"cases":[{"start_ref":"plugin_tree","action":{"actor":"maintainer","steps":["run `./sync-assets.sh --check`","run `python3 scripts/materialize-grok-skills.py --check`","run the rest of the AGENTS.md verification list"]},"end_state":{"must_observe":["both drift checks exit 0","the only failing engine cases are the 3 `real Grok ACP worker lifecycle` cases"],"must_not_observe":["a drifted resolver copy","a stale Grok pack","no output (empty run)"]}}]}}]} -->

## Out of scope

- Process-history files (.spec/tasks, .handoff, docs/T1-DRIFT-AUDIT.md)
- Deciding the fate of assign.py (brain A/B cohort hash)
- Unifying the engine's TypeScript config reader with the Python resolver
- Editing the agent-content repo from the plugin repo

## Risks

- All moderate risks.
- Must land after the kb-mcp-service sprint, because its publisher, projector and doc_refs hardcode .rosetta/docs, agents/ and brain citation aliases; porting them first means rewriting unmerged code.
- Roughly 100 test assertions across five files name specific technologies; replacing them with neutral names weakens the oracle unless the neutral fixtures keep the same shapes (shared graph, name-mismatch alias, agent-local graph, composed graph).
- agent-content needs a one-time config migration for 12 agents (hetzner, aisdk, spacetimedb, cloudflare-rust stems) or its registry silently loses those consumers.
- AGENTS.md rule 8 requires real fixtures; neutral graphs must come from the real writer, not hand-built JSON.

## Verification posture

Per `AGENTS.md` Supreme Rule: the task is complete only when each AC is verified against REAL services: real
subprocesses of the real CLIs on real temp repos (and, for the hand-off checks, a real copy of
~/Projects/agent-content). Each `verify` command must exit 0 against the real dependency.
