# CONFIG-01: Require a version 2 project config and validate its paths

> Assignee: python-implementer
> Priority: P1
> Type: improvement
> Depends on: none
> Files: assets/rosetta_config.py, skills/*/assets/rosetta_config.py (via ./sync-assets.sh only), scripts/rosetta/roots.py, scripts/rosetta/test_resolve.py, scripts/rosetta/test_write_graph.py, scripts/rosetta/test_provenance.py, scripts/testdata/materialize.py, install.sh
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

- [ ] AC-1: In a fresh `git init` temp repo with ROSETTA_HOME pointing at an empty dir and no `.rosetta/config.json`, `rosetta_config.py show`, `rosetta_config.py path --class docs --slug x` and `write-graph.py --create x` each exit non-zero, name the missing `<repo>/.rosetta/config.json`, print the JSON to create, and leave the temp tree byte-identical before and after.
- [ ] AC-3: With a version 2 config naming the standard layout in the repo, `rosetta_config.py path --class docs --slug x --file fact-graph.json` prints `<repo>/.rosetta/docs/x/fact-graph.json` and exits 0; the existing registry, write-graph and embed suites pass against fixtures that now carry that config.
- [ ] AC-4: A config missing any of the four path keys (docs, research, probes, agents), or with a path value that is absolute, `~`-relative, `device`, contains `..`, or escapes the repo through a symlink, exits non-zero naming the key; a docs path that passes through a registry-pruned name (tmp, research, sandbox, .history) is rejected. One temp-repo test per value kind.
- [ ] AC-5: A device file at `$ROSETTA_HOME/config.json` that sets a docs location does not move the resolved dir and produces one stderr warning that device roots are ignored.
- [ ] AC-6: A version 1 project config (a `roots` map) exits non-zero and prints the equivalent version 2 JSON.
- [ ] AC-7: `rosetta_config.py plugin` still works with no project config (it is exempt).

## Test Criteria

| # | Boolean Statement | Maps To AC | Verify | Status |
|---|-------------------|------------|--------|--------|
| TC-1 | In a fresh `git init` temp repo with ROSETTA_HOME pointing at an empty dir and no `.rosetta/config.json`, `rosetta_config.py show`, `rosetta_config.py path --class docs --slug x` and `write-graph.py --create x` each exit non-zero, name the missing `<repo>/.rosetta/config.json`, print the JSON to create, and leave the temp tree byte-identical before and after holds | AC-1 | `python3 scripts/rosetta/test_resolve.py` | [ ] TRUE [ ] FALSE |
| TC-3 | With a version 2 config naming the standard layout in the repo, `rosetta_config.py path --class docs --slug x --file fact-graph.json` prints `<repo>/.rosetta/docs/x/fact-graph.json` and exits 0 holds | AC-3 | `python3 scripts/rosetta/test_resolve.py` | [ ] TRUE [ ] FALSE |
| TC-4 | A config missing any of the four path keys (docs, research, probes, agents), or with a path value that is absolute, `~`-relative, `device`, contains `..`, or escapes the repo through a symlink, exits non-zero naming the key holds | AC-4 | `python3 scripts/rosetta/test_resolve.py` | [ ] TRUE [ ] FALSE |
| TC-5 | A device file at `$ROSETTA_HOME/config.json` that sets a docs location does not move the resolved dir and produces one stderr warning that device roots are ignored holds | AC-5 | `python3 scripts/rosetta/test_resolve.py` | [ ] TRUE [ ] FALSE |
| TC-6 | A version 1 project config (a `roots` map) exits non-zero and prints the equivalent version 2 JSON holds | AC-6 | `python3 scripts/rosetta/test_resolve.py` | [ ] TRUE [ ] FALSE |
| TC-7 | `rosetta_config.py plugin` still works with no project config (it is exempt) holds | AC-7 | `python3 scripts/rosetta/test_resolve.py` | [ ] TRUE [ ] FALSE |

<!-- REQUIREMENT-CONTRACT v1
fixtures: {"bare_repo": {"description": "A fresh `git init` temp repo T with ROSETTA_HOME pointing at an empty temp dir and no `.rosetta/config.json` in T or in ROSETTA_HOME.", "seed_method": "cli", "records": ["git init -q \"$T\"", "export ROSETTA_HOME=$(mktemp -d)", "test ! -e \"$T/.rosetta/config.json\""]}, "standard_repo": {"description": "bare_repo plus `$T/.rosetta/config.json` = {\"version\": 2, \"paths\": {\"docs\": \".rosetta/docs\", \"research\": \".rosetta/research\", \"probes\": \".rosetta/probes\", \"agents\": \"agents\"}}.", "seed_method": "cli", "records": ["\"version\": 2", "\"docs\": \".rosetta/docs\"", "\"agents\": \"agents\""]}}
AC-1: In a fresh `git init` temp repo with ROSETTA_HOME pointing at an empty dir and no `.rosetta/config.json`, `rosetta_config.py show`, `rosetta_config.py path --class docs --slug x` and `write-graph.py --create x` each exit non-zero, name the missing `<repo>/.rosetta/config.json`, print the JSON to create, and leave the temp tree byte-identical before and after.
  verify: python3 scripts/rosetta/test_resolve.py
  scenario: {"id": "AC-1", "primary": true, "test_tier": "integration", "verification_service": "assets/rosetta_config.py and scripts/rosetta/write-graph.py run as real subprocesses", "topology": "single-node", "negative_control": {"would_fail_if": ["fix reverted (the defect reproduces)", "the config gate removed or stubbed so the command falls back to the built-in default", "the check reads a hardcoded `.rosetta/docs` / `agents` path instead of the config"]}, "evidence": {"artifact_type": "stdout", "required_capture": true}, "cases": [{"start_ref": "bare_repo", "action": {"actor": "maintainer", "steps": ["cd into T", "run `rosetta_config.py show --format plain`, `rosetta_config.py path --class docs --slug x`, and `write-graph.py --create x`", "list T recursively before and after"]}, "end_state": {"must_observe": ["each of the 3 commands exits with a non-zero code", "stderr names `<T>/.rosetta/config.json`", "stderr prints a JSON block containing `\"version\": 2`", "`diff` of the recursive listing of T before and after prints 0 lines"], "must_not_observe": ["exit=0", "`[builtin-default: roots.default = project]`", "no change in exit code from the start state", "an empty error message"]}}]}
AC-3: With a version 2 config naming the standard layout in the repo, `rosetta_config.py path --class docs --slug x --file fact-graph.json` prints `<repo>/.rosetta/docs/x/fact-graph.json` and exits 0; the existing registry, write-graph and embed suites pass against fixtures that now carry that config.
  verify: python3 scripts/rosetta/test_resolve.py
  scenario: {"id": "AC-3", "primary": true, "test_tier": "integration", "verification_service": "assets/rosetta_config.py path subcommand (real subprocess)", "topology": "single-node", "negative_control": {"would_fail_if": ["fix reverted (the defect reproduces)", "the config gate removed or stubbed so the command falls back to the built-in default", "the check reads a hardcoded `.rosetta/docs` / `agents` path instead of the config"]}, "evidence": {"artifact_type": "stdout", "required_capture": true}, "cases": [{"start_ref": "standard_repo", "action": {"actor": "maintainer", "steps": ["write the standard version 2 config into T", "run `rosetta_config.py path --class docs --slug x --file fact-graph.json`"]}, "end_state": {"must_observe": ["stdout is exactly `<T>/.rosetta/docs/x/fact-graph.json`", "exit code is 0"], "must_not_observe": ["a path outside T", "no output (empty stdout)", "`[builtin-default: roots.default = project]`"]}}]}
AC-4: A config missing any of the four path keys (docs, research, probes, agents), or with a path value that is absolute, `~`-relative, `device`, contains `..`, or escapes the repo through a symlink, exits non-zero naming the key; a docs path that passes through a registry-pruned name (tmp, research, sandbox, .history) is rejected. One temp-repo test per value kind.
  verify: python3 scripts/rosetta/test_resolve.py
  scenario: {"id": "AC-4", "primary": true, "test_tier": "integration", "verification_service": "assets/rosetta_config.py show (real subprocess)", "topology": "single-node", "negative_control": {"would_fail_if": ["fix reverted (the defect reproduces)", "the config gate removed or stubbed so the command falls back to the built-in default", "the check reads a hardcoded `.rosetta/docs` / `agents` path instead of the config"]}, "evidence": {"artifact_type": "stdout", "required_capture": true}, "cases": [{"start_ref": "standard_repo", "action": {"actor": "maintainer", "steps": ["for each bad value (missing `agents` key, `/abs`, `~/x`, `device`, `../x`, a symlink to /tmp, `tmp/graphs`) write it into the config and run `rosetta_config.py show`"]}, "end_state": {"must_observe": ["each of the 7 bad configs exits non-zero", "stderr names the offending key, e.g. `paths.docs`"], "must_not_observe": ["exit=0", "a resolved path outside T", "no error output (empty stderr)"]}}]}
AC-5: A device file at `$ROSETTA_HOME/config.json` that sets a docs location does not move the resolved dir and produces one stderr warning that device roots are ignored.
  verify: python3 scripts/rosetta/test_resolve.py
  scenario: {"id": "AC-5", "primary": true, "test_tier": "integration", "verification_service": "assets/rosetta_config.py show (real subprocess)", "topology": "single-node", "negative_control": {"would_fail_if": ["fix reverted (the defect reproduces)", "the config gate removed or stubbed so the command falls back to the built-in default", "the check reads a hardcoded `.rosetta/docs` / `agents` path instead of the config"]}, "evidence": {"artifact_type": "stdout", "required_capture": true}, "cases": [{"start_ref": "standard_repo", "action": {"actor": "maintainer", "steps": ["write `{\"version\": 1, \"roots\": {\"docs\": \"/elsewhere\"}}` to `$ROSETTA_HOME/config.json`", "run `rosetta_config.py show --format plain` in T"]}, "end_state": {"must_observe": ["docs resolves to `<T>/.rosetta/docs`", "stderr contains exactly 1 warning line that device roots are ignored"], "must_not_observe": ["`/elsewhere`", "no warning (empty stderr)"]}}]}
AC-6: A version 1 project config (a `roots` map) exits non-zero and prints the equivalent version 2 JSON.
  verify: python3 scripts/rosetta/test_resolve.py
  scenario: {"id": "AC-6", "primary": true, "test_tier": "integration", "verification_service": "assets/rosetta_config.py show (real subprocess)", "topology": "single-node", "negative_control": {"would_fail_if": ["fix reverted (the defect reproduces)", "the config gate removed or stubbed so the command falls back to the built-in default", "the check reads a hardcoded `.rosetta/docs` / `agents` path instead of the config"]}, "evidence": {"artifact_type": "stdout", "required_capture": true}, "cases": [{"start_ref": "bare_repo", "action": {"actor": "maintainer", "steps": ["write `{\"version\": 1, \"roots\": {\"default\": \"project\"}}` to `$T/.rosetta/config.json`", "run `rosetta_config.py show`"]}, "end_state": {"must_observe": ["`echo $?` prints a value other than `0`", "stderr prints a JSON block containing `\"version\": 2` and `\"paths\"`"], "must_not_observe": ["exit=0", "`[builtin-default: roots.default = project]`", "no suggested JSON (empty stderr)"]}}]}
AC-7: `rosetta_config.py plugin` still works with no project config (it is exempt).
  verify: python3 scripts/rosetta/test_resolve.py
  scenario: {"id": "AC-7", "primary": true, "test_tier": "integration", "verification_service": "assets/rosetta_config.py plugin (real subprocess)", "topology": "single-node", "negative_control": {"would_fail_if": ["fix reverted (the defect reproduces)", "the config gate removed or stubbed so the command falls back to the built-in default", "the check reads a hardcoded `.rosetta/docs` / `agents` path instead of the config"]}, "evidence": {"artifact_type": "stdout", "required_capture": true}, "cases": [{"start_ref": "bare_repo", "action": {"actor": "maintainer", "steps": ["run `rosetta_config.py plugin` from T with no config"]}, "end_state": {"must_observe": ["stdout is the plugin checkout path `$P`", "exit code is 0"], "must_not_observe": ["a missing-config refusal", "no output (empty stdout)"]}}]}
TC-1: Maps to AC-1 (inherits AC-1's scenario)
TC-3: Maps to AC-3 (inherits AC-3's scenario)
TC-4: Maps to AC-4 (inherits AC-4's scenario)
TC-5: Maps to AC-5 (inherits AC-5's scenario)
TC-6: Maps to AC-6 (inherits AC-6's scenario)
TC-7: Maps to AC-7 (inherits AC-7's scenario)
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
