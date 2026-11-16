# TECHFREE-01: Move technology data out of the code and replace the sample graphs

> Assignee: python-implementer
> Priority: P1
> Type: improvement
> Depends on: READERS-01
> Files: scripts/rosetta/naming.py, scripts/rosetta/registry.py, scripts/rosetta/locators.py, scripts/kb-delivery/generate_excerpt.py, assets/rosetta_config.py, skills/*/assets/rosetta_config.py (via ./sync-assets.sh only), scripts/testdata/graphs/*, scripts/testdata/kb-project/agents/*, scripts/testdata/materialize.py, scripts/rosetta/test_naming.py, scripts/rosetta/test_registry.py, scripts/rosetta/test_locators.py, scripts/rosetta/test_write_graph.py, scripts/rosetta/test_provenance.py, scripts/kb-delivery/test_generate_excerpt.py, scripts/kb-delivery/test_multigraph.py, scripts/kb-delivery/test-embed-graph.sh, scripts/kb-delivery/test_publish_snapshot.py, scripts/kb-delivery/test_doc_refs.py, scripts/kb-delivery/test_sync_agents.py, scripts/kb-delivery/README.md, scripts/README.md, docs/code-quality/known-problems.md
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

- [ ] AC-15: `git grep -niE "cloudflare|hetzner|spacetime|morph|aisdk|ai-sdk|kotlin|flyio|electron" -- assets scripts engine/src engine/tests 'skills/*/assets'` returns nothing (`mastra` is not searched: the research engine depends on the @mastra/acp package).
- [ ] AC-16: In a temp repo whose config declares an agent alias (vendor -> vendor-cloud), `registry.py scan` attributes vendor-implementer to graph vendor-cloud; the same repo without the key does not (the data lives in config, not code).
- [ ] AC-17: An agent-name alias value that fails the existing slug rule, or names a path, exits non-zero naming the key (one temp-repo test).
- [ ] AC-18: `generate_excerpt.py` no longer contains `V8 isolates`, `wrangler` or a default domain; `--domain` is required, and the condensed mode either derives every line from graph fields or is removed.
- [ ] AC-19: `scripts/testdata/graphs` contains only graphs reproduced byte-for-byte by `write-graph.py --create` from a committed neutral NRRA and ledger; a test regenerates and compares them.

## Test Criteria

| # | Boolean Statement | Maps To AC | Verify | Status |
|---|-------------------|------------|--------|--------|
| TC-15 | `git grep -niE "cloudflare|hetzner|spacetime|morph|aisdk|ai-sdk|kotlin|flyio|electron" -- assets scripts engine/src engine/tests 'skills/*/assets'` returns nothing (`mastra` is not searched: the research engine depends on the @mastra/acp package) holds | AC-15 | `test -z "$(git grep -niE 'cloudflare|hetzner|spacetime|morph|aisdk|ai-sdk|kotlin|flyio|electron' -- assets scripts engine/src engine/tests 'skills/*/assets')"` | [ ] TRUE [ ] FALSE |
| TC-16 | In a temp repo whose config declares an agent alias (vendor -> vendor-cloud), `registry.py scan` attributes vendor-implementer to graph vendor-cloud holds | AC-16 | `python3 scripts/rosetta/test_registry.py && python3 scripts/rosetta/test_naming.py` | [ ] TRUE [ ] FALSE |
| TC-17 | An agent-name alias value that fails the existing slug rule, or names a path, exits non-zero naming the key (one temp-repo test) holds | AC-17 | `python3 scripts/rosetta/test_registry.py` | [ ] TRUE [ ] FALSE |
| TC-18 | `generate_excerpt.py` no longer contains `V8 isolates`, `wrangler` or a default domain holds | AC-18 | `python3 scripts/kb-delivery/test_generate_excerpt.py && python3 scripts/kb-delivery/test_multigraph.py` | [ ] TRUE [ ] FALSE |
| TC-19 | `scripts/testdata/graphs` contains only graphs reproduced byte-for-byte by `write-graph.py --create` from a committed neutral NRRA and ledger holds | AC-19 | `python3 scripts/rosetta/test_write_graph.py` | [ ] TRUE [ ] FALSE |

<!-- REQUIREMENT-CONTRACT v1
fixtures: {"plugin_tree": {"description": "The improvement checkout P at its HEAD, read with `git grep` and the real CLIs.", "seed_method": "cli", "records": ["git -C \"$P\" rev-parse HEAD"]}, "standard_repo": {"description": "bare_repo plus `$T/.rosetta/config.json` = {\"version\": 2, \"paths\": {\"docs\": \".rosetta/docs\", \"research\": \".rosetta/research\", \"probes\": \".rosetta/probes\", \"agents\": \"agents\"}}.", "seed_method": "cli", "records": ["\"version\": 2", "\"docs\": \".rosetta/docs\"", "\"agents\": \"agents\""]}}
AC-15: `git grep -niE "cloudflare|hetzner|spacetime|morph|aisdk|ai-sdk|kotlin|flyio|electron" -- assets scripts engine/src engine/tests 'skills/*/assets'` returns nothing (`mastra` is not searched: the research engine depends on the @mastra/acp package).
  verify: test -z "$(git grep -niE 'cloudflare|hetzner|spacetime|morph|aisdk|ai-sdk|kotlin|flyio|electron' -- assets scripts engine/src engine/tests 'skills/*/assets')"
  scenario: {"id": "AC-15", "primary": true, "test_tier": "integration", "verification_service": "git grep over the improvement checkout", "topology": "single-node", "negative_control": {"would_fail_if": ["fix reverted (the defect reproduces)", "the config gate removed or stubbed so the command falls back to the built-in default", "the check reads a hardcoded `.rosetta/docs` / `agents` path instead of the config"]}, "evidence": {"artifact_type": "stdout", "required_capture": true}, "cases": [{"start_ref": "plugin_tree", "action": {"actor": "maintainer", "steps": ["run the technology-name search in P"]}, "end_state": {"must_observe": ["the search prints 0 lines and exits 1"], "must_not_observe": ["any `cloudflare`, `hetzner` or `kotlin` line", "a non-empty match list"]}}]}
AC-16: In a temp repo whose config declares an agent alias (vendor -> vendor-cloud), `registry.py scan` attributes vendor-implementer to graph vendor-cloud; the same repo without the key does not (the data lives in config, not code).
  verify: python3 scripts/rosetta/test_registry.py && python3 scripts/rosetta/test_naming.py
  scenario: {"id": "AC-16", "primary": true, "test_tier": "integration", "verification_service": "registry.py scan (real subprocess)", "topology": "single-node", "negative_control": {"would_fail_if": ["fix reverted (the defect reproduces)", "the config gate removed or stubbed so the command falls back to the built-in default", "the check reads a hardcoded `.rosetta/docs` / `agents` path instead of the config"]}, "evidence": {"artifact_type": "stdout", "required_capture": true}, "cases": [{"start_ref": "standard_repo", "action": {"actor": "maintainer", "steps": ["add `vendor -> vendor-cloud` to the config's agent aliases", "run `registry.py scan` then `registry.py consumers vendor-cloud`", "remove the alias and scan again"]}, "end_state": {"must_observe": ["with the alias, `consumers vendor-cloud` lists `vendor-implementer`", "without the alias, `consumers vendor-cloud` lists 0 agents"], "must_not_observe": ["`vendor-implementer` attributed to `vendor-cloud` with no alias in config", "no consumers (empty list) when the alias is present"]}}]}
AC-17: An agent-name alias value that fails the existing slug rule, or names a path, exits non-zero naming the key (one temp-repo test).
  verify: python3 scripts/rosetta/test_registry.py
  scenario: {"id": "AC-17", "primary": true, "test_tier": "integration", "verification_service": "registry.py scan (real subprocess)", "topology": "single-node", "negative_control": {"would_fail_if": ["fix reverted (the defect reproduces)", "the config gate removed or stubbed so the command falls back to the built-in default", "the check reads a hardcoded `.rosetta/docs` / `agents` path instead of the config"]}, "evidence": {"artifact_type": "stdout", "required_capture": true}, "cases": [{"start_ref": "standard_repo", "action": {"actor": "maintainer", "steps": ["add an agent alias `vendor -> ../escape` to the config", "run `registry.py scan`"]}, "end_state": {"must_observe": ["`echo $?` prints a value other than `0`", "stderr names the alias key `vendor`"], "must_not_observe": ["exit=0", "a written `.rosetta/registry.json`", "no error output (empty stderr)"]}}]}
AC-18: `generate_excerpt.py` no longer contains `V8 isolates`, `wrangler` or a default domain; `--domain` is required, and the condensed mode either derives every line from graph fields or is removed.
  verify: python3 scripts/kb-delivery/test_generate_excerpt.py && python3 scripts/kb-delivery/test_multigraph.py
  scenario: {"id": "AC-18", "primary": true, "test_tier": "integration", "verification_service": "generate_excerpt.py (real subprocess)", "topology": "single-node", "negative_control": {"would_fail_if": ["fix reverted (the defect reproduces)", "the config gate removed or stubbed so the command falls back to the built-in default", "the check reads a hardcoded `.rosetta/docs` / `agents` path instead of the config"]}, "evidence": {"artifact_type": "stdout", "required_capture": true}, "cases": [{"start_ref": "standard_repo", "action": {"actor": "maintainer", "steps": ["search generate_excerpt.py for the 2 literals", "run `generate_excerpt.py` without `--domain`"]}, "end_state": {"must_observe": ["the literal search prints 0 lines", "running without `--domain` exits non-zero naming `--domain`"], "must_not_observe": ["`V8 isolates`", "`wrangler`", "a silent default domain"]}}]}
AC-19: `scripts/testdata/graphs` contains only graphs reproduced byte-for-byte by `write-graph.py --create` from a committed neutral NRRA and ledger; a test regenerates and compares them.
  verify: python3 scripts/rosetta/test_write_graph.py
  scenario: {"id": "AC-19", "primary": true, "test_tier": "integration", "verification_service": "write-graph.py --create (real subprocess) on a temp repo", "topology": "single-node", "negative_control": {"would_fail_if": ["fix reverted (the defect reproduces)", "the config gate removed or stubbed so the command falls back to the built-in default", "the check reads a hardcoded `.rosetta/docs` / `agents` path instead of the config"]}, "evidence": {"artifact_type": "stdout", "required_capture": true}, "cases": [{"start_ref": "plugin_tree", "action": {"actor": "maintainer", "steps": ["regenerate every fixture graph from its committed neutral NRRA and ledger", "compare with `cmp`"]}, "end_state": {"must_observe": ["every regenerated graph is byte-identical (`cmp` exits 0)", "`git grep -niE` for the technology names over `scripts/testdata` prints 0 lines"], "must_not_observe": ["`rust.fact-graph.json`", "`cloudflare-workers.fact-graph.json`", "an empty fixture directory"]}}]}
TC-15: Maps to AC-15 (inherits AC-15's scenario)
TC-16: Maps to AC-16 (inherits AC-16's scenario)
TC-17: Maps to AC-17 (inherits AC-17's scenario)
TC-18: Maps to AC-18 (inherits AC-18's scenario)
TC-19: Maps to AC-19 (inherits AC-19's scenario)
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
