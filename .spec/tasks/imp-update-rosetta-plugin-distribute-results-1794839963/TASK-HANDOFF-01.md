# HANDOFF-01: Prove the change against a copy of agent-content

> Assignee: python-implementer
> Priority: P1
> Type: improvement
> Depends on: TECHFREE-01
> Files: scripts/rosetta/test_registry.py, scripts/rosetta/test_provenance.py
> Patterns: minimum-diff-discipline, anti-stub
> Scope: /Users/justinrich/.config/brain/improvements/imp-update-rosetta-plugin-distribute-results-1794839963.json

## Context

Rosetta never asks for a config: in a repo with none it quietly resolves every artifact class to the built-in
"project" layout, and only the resolver reads a config at all — registry, naming, provenance, generate_excerpt,
embed-graph and the four kb-delivery scripts hardcode `.rosetta/docs` and `agents/`. Brain survives as the
`BRAIN_ROOT` variable and the `brain/docs/` citation alias, and technology data (name tables, a Cloudflare-only
condensed excerpt, real-technology fixture graphs) still ships inside the plugin.

Root cause summary: Where artifacts go is decided by a built-in 'project' default plus a machine-wide device file that points every repo at agent-content, so no repo ever declares its own layout. Only the resolver reads config; registry, naming, generate_excerpt, embed-graph and provenance each hardcode .rosetta/docs and agents/, and provenance carries one load-bearing 'brain/docs/' citation alias. Brain survives as a variable name and that alias, not as a path.

For full reproduction evidence, root-cause file:line refs, considered alternatives, and challenger notes, READ
the binding scope named on the `> Scope:` line above (durable ScopeState JSON per
brain/docs/SCOPESTATE-CONTRACT.md). That contract is binding. You may NOT touch files outside the `> Files:`
list above. Follow this repo's AGENTS.md: the resolver is edited only in `assets/rosetta_config.py` and copied
with `./sync-assets.sh`; tests use real temp trees, never mocks.

## Acceptance Criteria

- [ ] AC-20: Hand-off check on a read-only copy of ~/Projects/agent-content with the migrated version 2 config and its citation aliases: `provenance.py --check-stale` on kotlin, rust, swift and herdrdev returns 0 or 10, never 1.
- [ ] AC-21: On the same copy, `registry.py scan` reproduces the graphs, agents and consumer lists of agent-content's committed registry.json (hetzner-cloud, ai-sdk, spacetime-db and cloudflare-workers included), and `embed-graph.sh --check` returns the same status as before the change.

## Test Criteria

| # | Boolean Statement | Maps To AC | Verify | Status |
|---|-------------------|------------|--------|--------|
| TC-20 | Hand-off check on a read-only copy of ~/Projects/agent-content with the migrated version 2 config and its citation aliases: `provenance.py --check-stale` on kotlin, rust, swift and herdrdev returns 0 or 10, never 1 holds | AC-20 | `ROSETTA_CONSUMER_ROOT=~/Projects/agent-content python3 scripts/rosetta/test_provenance.py` | [ ] TRUE [ ] FALSE |
| TC-21 | On the same copy, `registry.py scan` reproduces the graphs, agents and consumer lists of agent-content's committed registry.json (hetzner-cloud, ai-sdk, spacetime-db and cloudflare-workers included), and `embed-graph.sh --check` returns the same status as before the change holds | AC-21 | `ROSETTA_CONSUMER_ROOT=~/Projects/agent-content python3 scripts/rosetta/test_registry.py` | [ ] TRUE [ ] FALSE |

<!-- REQUIREMENT-CONTRACT v1
fixtures: {"consumer_copy": {"description": "`cp -R ~/Projects/agent-content \"$C\"` (the source is only read), then the migrated version 2 config written to `$C/.rosetta/config.json` with the standard layout, a citation alias `brain/docs` -> `.rosetta/docs`, and the agent-name aliases that used to live in naming.py.", "seed_method": "cli", "records": ["$C/.rosetta/registry.json (committed)", "7 graph files citing `brain/docs/...`", "\"version\": 2"]}}
AC-20: Hand-off check on a read-only copy of ~/Projects/agent-content with the migrated version 2 config and its citation aliases: `provenance.py --check-stale` on kotlin, rust, swift and herdrdev returns 0 or 10, never 1.
  verify: ROSETTA_CONSUMER_ROOT=~/Projects/agent-content python3 scripts/rosetta/test_provenance.py
  scenario: {"id": "AC-20", "primary": true, "test_tier": "integration", "verification_service": "provenance.py --check-stale against a copy of the real agent-content repo", "topology": "single-node", "negative_control": {"would_fail_if": ["fix reverted (the defect reproduces)", "the config gate removed or stubbed so the command falls back to the built-in default", "the check reads a hardcoded `.rosetta/docs` / `agents` path instead of the config"]}, "evidence": {"artifact_type": "stdout", "required_capture": true}, "cases": [{"start_ref": "consumer_copy", "action": {"actor": "maintainer", "steps": ["run `provenance.py --root \"$C\" --check-stale` for kotlin, rust, swift and herdrdev"]}, "end_state": {"must_observe": ["each of the 4 runs exits 0 or 10"], "must_not_observe": ["exit 1 (undeterminable)", "no result (empty output)"]}}]}
AC-21: On the same copy, `registry.py scan` reproduces the graphs, agents and consumer lists of agent-content's committed registry.json (hetzner-cloud, ai-sdk, spacetime-db and cloudflare-workers included), and `embed-graph.sh --check` returns the same status as before the change.
  verify: ROSETTA_CONSUMER_ROOT=~/Projects/agent-content python3 scripts/rosetta/test_registry.py
  scenario: {"id": "AC-21", "primary": true, "test_tier": "integration", "verification_service": "registry.py scan and embed-graph.sh --check against a copy of the real agent-content repo", "topology": "single-node", "negative_control": {"would_fail_if": ["fix reverted (the defect reproduces)", "the config gate removed or stubbed so the command falls back to the built-in default", "the check reads a hardcoded `.rosetta/docs` / `agents` path instead of the config"]}, "evidence": {"artifact_type": "stdout", "required_capture": true}, "cases": [{"start_ref": "consumer_copy", "action": {"actor": "maintainer", "steps": ["save the committed registry.json", "run `registry.py --root \"$C\" scan`", "diff the graphs/agents/consumers"]}, "end_state": {"must_observe": ["the graph, agent and consumer sets are identical to the committed registry.json (diff prints 0 lines)", "`hetzner-cloud` still lists its 3 agents"], "must_not_observe": ["a dropped consumer", "no consumers (empty list) for `ai-sdk`"]}}]}
TC-20: Maps to AC-20 (inherits AC-20's scenario)
TC-21: Maps to AC-21 (inherits AC-21's scenario)
-->

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

Per `AGENTS.md` Supreme Rule: the task is complete only when each AC is verified against REAL services — real
subprocesses of the real CLIs on real temp repos (and, for the hand-off checks, a real copy of
~/Projects/agent-content). The listed `Verify` command must exit 0 against the real dependency.
