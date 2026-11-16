# CONFIG-01: Require a version 2 project config and validate its paths

> Assignee: python-implementer
AGENT: planner=python-planner | implementer=python-implementer | reviewer=python-reviewer
> Depends on: none
> Priority: P1
> Type: feature
> Files: assets/rosetta_config.py, skills/*/assets/rosetta_config.py, grok-skills/*/assets/rosetta_config.py, scripts/rosetta/roots.py, scripts/rosetta/write-graph.py, scripts/rosetta/test_resolve.py, scripts/rosetta/test_write_graph.py, scripts/rosetta/test_provenance.py, scripts/rosetta/test_registry.py, scripts/kb-delivery/test-embed-graph.sh, scripts/testdata/materialize.py, install.sh
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

- [ ] AC-1: In a fresh `git init` temp repo with ROSETTA_HOME pointing at an empty dir and no `.rosetta/config.json`, `rosetta_config.py show`, `rosetta_config.py path --class docs --slug x` and `write-graph.py --create x` each exit non-zero, name the missing `<repo>/.rosetta/config.json`, print the JSON to create, and leave the temp tree byte-identical before and after.
- [ ] AC-3: With a version 2 config naming the standard layout in the repo, `rosetta_config.py path --class docs --slug x --file fact-graph.json` prints `<repo>/.rosetta/docs/x/fact-graph.json` and exits 0; the existing registry, write-graph and embed suites pass against fixtures that now carry that config.
- [ ] AC-4: A config missing any of the four path keys (docs, research, probes, agents), or with a path value that is absolute, `~`-relative, `device`, contains `..`, or escapes the repo through a symlink, exits non-zero naming the key; a docs path that passes through a registry-pruned name (tmp, research, sandbox, .history) is rejected. One temp-repo test per missing key, one per bad value kind applied to each of the four keys, and one per pruned docs name.
- [ ] AC-5: A device file at `$ROSETTA_HOME/config.json` that sets a docs location does not move the resolved dir and produces one stderr warning that device roots are ignored.
- [ ] AC-6: A version 1 project config (a `roots` map) exits non-zero and prints the equivalent version 2 JSON.
- [ ] AC-7: `rosetta_config.py plugin` still works with no project config (it is exempt).

## Test Criteria

| # | Boolean Statement | Maps To AC | Verify | Status |
|---|-------------------|------------|--------|--------|
| 1 | In a fresh `git init` temp repo with ROSETTA_HOME pointing at an empty dir and no `.rosetta/config.json`, `rosetta_config.py show`, `rosetta_config.py path --class docs --slug x` and `write-graph.py --create x` each exit non-zero, name the missing `<repo>/.rosetta/config.json`, print the JSON to create, and leave the temp tree byte-identical before and after. | AC-1 | `python3 scripts/rosetta/test_resolve.py` | [ ] TRUE [ ] FALSE |
| 3 | With a version 2 config naming the standard layout in the repo, `rosetta_config.py path --class docs --slug x --file fact-graph.json` prints `<repo>/.rosetta/docs/x/fact-graph.json` and exits 0; the existing registry, write-graph and embed suites pass against fixtures that now carry that config. | AC-3 | `python3 scripts/rosetta/test_resolve.py` | [ ] TRUE [ ] FALSE |
| 4 | A config missing any of the four path keys (docs, research, probes, agents), or with a path value that is absolute, `~`-relative, `device`, contains `..`, or escapes the repo through a symlink, exits non-zero naming the key; a docs path that passes through a registry-pruned name (tmp, research, sandbox, .history) is rejected. One temp-repo test per missing key, one per bad value kind applied to each of the four keys, and one per pruned docs name. | AC-4 | `python3 scripts/rosetta/test_resolve.py` | [ ] TRUE [ ] FALSE |
| 5 | A device file at `$ROSETTA_HOME/config.json` that sets a docs location does not move the resolved dir and produces one stderr warning that device roots are ignored. | AC-5 | `python3 scripts/rosetta/test_resolve.py` | [ ] TRUE [ ] FALSE |
| 6 | A version 1 project config (a `roots` map) exits non-zero and prints the equivalent version 2 JSON. | AC-6 | `python3 scripts/rosetta/test_resolve.py` | [ ] TRUE [ ] FALSE |
| 7 | `rosetta_config.py plugin` still works with no project config (it is exempt). | AC-7 | `python3 scripts/rosetta/test_resolve.py` | [ ] TRUE [ ] FALSE |

<!-- REQUIREMENT-CONTRACT v1 -->
<!-- {"task_id":"CONFIG-01","fixtures":{"bare_repo":{"description":"A fresh `git init` temp repo T with ROSETTA_HOME pointing at an empty temp dir and no `.rosetta/config.json` in T or in ROSETTA_HOME.","seed_method":"cli","records":["git init -q \"$T\"","export ROSETTA_HOME=$(mktemp -d)","test ! -e \"$T/.rosetta/config.json\""]},"standard_repo":{"description":"bare_repo plus `$T/.rosetta/config.json` = {\"version\": 2, \"paths\": {\"docs\": \".rosetta/docs\", \"research\": \".rosetta/research\", \"probes\": \".rosetta/probes\", \"agents\": \"agents\"}}.","seed_method":"cli","records":["\"version\": 2","\"docs\": \".rosetta/docs\"","\"agents\": \"agents\""]},"custom_repo":{"description":"bare_repo plus `$T/.rosetta/config.json` = {\"version\": 2, \"paths\": {\"docs\": \"kb/graphs\", \"research\": \"kb/research\", \"probes\": \"kb/probes\", \"agents\": \"prompts\"}} - a valid version 2 config naming a NON-standard layout.","seed_method":"cli","records":["\"version\": 2","\"docs\": \"kb/graphs\"","\"research\": \"kb/research\"","\"probes\": \"kb/probes\"","\"agents\": \"prompts\""]}},"guardrails":{"write_allowed":["assets/rosetta_config.py","skills/*/assets/rosetta_config.py","grok-skills/*/assets/rosetta_config.py","scripts/rosetta/roots.py","scripts/rosetta/write-graph.py","scripts/rosetta/test_resolve.py","scripts/rosetta/test_write_graph.py","scripts/rosetta/test_provenance.py","scripts/rosetta/test_registry.py","scripts/kb-delivery/test-embed-graph.sh","scripts/testdata/materialize.py","install.sh"]},"requirements":[{"id":"AC-1","description":"In a fresh `git init` temp repo with ROSETTA_HOME pointing at an empty dir and no `.rosetta/config.json`, `rosetta_config.py show`, `rosetta_config.py path --class docs --slug x` and `write-graph.py --create x` each exit non-zero, name the missing `<repo>/.rosetta/config.json`, print the JSON to create, and leave the temp tree byte-identical before and after.","verify":"python3 scripts/rosetta/test_resolve.py","scenario":{"id":"AC-1","primary":true,"test_tier":"integration","verification_service":"assets/rosetta_config.py and scripts/rosetta/write-graph.py run as real subprocesses","topology":"single-node","negative_control":{"would_fail_if":["fix reverted (the defect reproduces)","the config gate removed or stubbed so the command falls back to the built-in default","the check reads a hardcoded `.rosetta/docs` / `agents` path instead of the config"]},"evidence":{"artifact_type":"stdout","required_capture":true},"cases":[{"start_ref":"bare_repo","action":{"actor":"maintainer","steps":["cd into T","run `rosetta_config.py show --format plain`, `rosetta_config.py path --class docs --slug x`, and `write-graph.py --create x`","list T recursively before and after"]},"end_state":{"must_observe":["each of the 3 commands exits with a non-zero code","stderr names `<T>/.rosetta/config.json`","stderr prints a JSON block containing `\"version\": 2`","`diff` of the recursive listing of T before and after prints 0 lines"],"must_not_observe":["exit=0","`[builtin-default: roots.default = project]`","no change in exit code from the start state","an empty error message"]}}]}},{"id":"AC-3","description":"With a version 2 config naming the standard layout in the repo, `rosetta_config.py path --class docs --slug x --file fact-graph.json` prints `<repo>/.rosetta/docs/x/fact-graph.json` and exits 0; the existing registry, write-graph and embed suites pass against fixtures that now carry that config.","verify":"python3 scripts/rosetta/test_resolve.py","scenario":{"id":"AC-3","primary":true,"test_tier":"integration","verification_service":"assets/rosetta_config.py path subcommand (real subprocess)","topology":"single-node","negative_control":{"would_fail_if":["fix reverted (the defect reproduces)","the config gate removed or stubbed so the command falls back to the built-in default","the check reads a hardcoded `.rosetta/docs` / `agents` path instead of the config","`Resolver._decide` returns a hardcoded `.rosetta/docs` for the docs class instead of the config's `paths.docs` (the hardcode-docs mutant) - `python3 scripts/rosetta/test_resolve.py` must then exit non-zero"]},"evidence":{"artifact_type":"stdout","required_capture":true},"cases":[{"start_ref":"standard_repo","action":{"actor":"maintainer","steps":["write the standard version 2 config into T","run `rosetta_config.py path --class docs --slug x --file fact-graph.json`"]},"end_state":{"must_observe":["stdout is exactly `<T>/.rosetta/docs/x/fact-graph.json`","exit code is 0"],"must_not_observe":["a path outside T","no output (empty stdout)","`[builtin-default: roots.default = project]`"]}},{"start_ref":"custom_repo","action":{"actor":"maintainer","steps":["write the version 2 config naming the NON-standard layout (docs=kb/graphs, research=kb/research, probes=kb/probes, agents=prompts) into T","run `rosetta_config.py path --class docs --slug x --file fact-graph.json`, then `rosetta_config.py path --class agents --slug x`, then `rosetta_config.py show --format plain`"]},"end_state":{"must_observe":["stdout is exactly `<T>/kb/graphs/x/fact-graph.json` with exit code 0","the agents class prints exactly `<T>/prompts/x`","`show --format plain` reports `[config: paths.docs = kb/graphs]`"],"must_not_observe":["a `.rosetta/docs` segment anywhere in the resolved paths","no output (empty stdout)"]}}]}},{"id":"AC-4","description":"A config missing any of the four path keys (docs, research, probes, agents), or with a path value that is absolute, `~`-relative, `device`, contains `..`, or escapes the repo through a symlink, exits non-zero naming the key; a docs path that passes through a registry-pruned name (tmp, research, sandbox, .history) is rejected. One temp-repo test per missing key, one per bad value kind applied to each of the four keys, and one per pruned docs name.","verify":"python3 scripts/rosetta/test_resolve.py","scenario":{"id":"AC-4","primary":true,"test_tier":"integration","verification_service":"assets/rosetta_config.py show (real subprocess)","topology":"single-node","negative_control":{"would_fail_if":["fix reverted (the defect reproduces)","the config gate removed or stubbed so the command falls back to the built-in default","the check reads a hardcoded `.rosetta/docs` / `agents` path instead of the config","`validate_config` fills the standard default for a missing `paths` key instead of refusing it, for any one of docs, research, probes or agents (the default-a-missing-key mutant) - `python3 scripts/rosetta/test_resolve.py` must then exit non-zero","the bad-value checks run only for `paths.docs`, leaving research, probes and agents unvalidated (the validate-only-docs mutant) - `python3 scripts/rosetta/test_resolve.py` must then exit non-zero","`PRUNED_DOC_SEGMENTS` holds only `tmp`, so the pruned names research, sandbox and .history are absent from it (the prune-only-tmp mutant) - `python3 scripts/rosetta/test_resolve.py` must then exit non-zero"]},"evidence":{"artifact_type":"stdout","required_capture":true},"cases":[{"start_ref":"standard_repo","action":{"actor":"maintainer","steps":["for each of the four keys (docs, research, probes, agents) delete only that key from an otherwise complete config and run `rosetta_config.py show` - 4 temp repos","for each bad value kind (`/abs`, `~/x`, `device`, `../x`, a symlink escaping the repo) write it as the value of each of the four keys in turn and run `rosetta_config.py show` - 20 temp repos","for each registry-pruned name (tmp, research, sandbox, .history) write `paths.docs` as `<name>/graphs` and run `rosetta_config.py show` - 4 temp repos"]},"end_state":{"must_observe":["each of the 28 bad configs exits non-zero","each refusal names the offending key on stderr, e.g. `paths.docs` or `paths.agents`","the 4 missing-key refusals name the deleted key: `paths.docs`, `paths.research`, `paths.probes`, `paths.agents`"],"must_not_observe":["exit=0","a resolved path outside T","no error output (empty stderr)"]}}]}},{"id":"AC-5","description":"A device file at `$ROSETTA_HOME/config.json` that sets a docs location does not move the resolved dir and produces one stderr warning that device roots are ignored.","verify":"python3 scripts/rosetta/test_resolve.py","scenario":{"id":"AC-5","primary":true,"test_tier":"integration","verification_service":"assets/rosetta_config.py show (real subprocess)","topology":"single-node","negative_control":{"would_fail_if":["fix reverted (the defect reproduces)","the config gate removed or stubbed so the command falls back to the built-in default","the check reads a hardcoded `.rosetta/docs` / `agents` path instead of the config"]},"evidence":{"artifact_type":"stdout","required_capture":true},"cases":[{"start_ref":"standard_repo","action":{"actor":"maintainer","steps":["write `{\"version\": 1, \"roots\": {\"docs\": \"/elsewhere\"}}` to `$ROSETTA_HOME/config.json`","run `rosetta_config.py show --format plain` in T"]},"end_state":{"must_observe":["docs resolves to `<T>/.rosetta/docs`","stderr contains exactly 1 warning line that device roots are ignored"],"must_not_observe":["`/elsewhere`","no warning (empty stderr)"]}}]}},{"id":"AC-6","description":"A version 1 project config (a `roots` map) exits non-zero and prints the equivalent version 2 JSON.","verify":"python3 scripts/rosetta/test_resolve.py","scenario":{"id":"AC-6","primary":true,"test_tier":"integration","verification_service":"assets/rosetta_config.py show (real subprocess)","topology":"single-node","negative_control":{"would_fail_if":["fix reverted (the defect reproduces)","the config gate removed or stubbed so the command falls back to the built-in default","the check reads a hardcoded `.rosetta/docs` / `agents` path instead of the config"]},"evidence":{"artifact_type":"stdout","required_capture":true},"cases":[{"start_ref":"bare_repo","action":{"actor":"maintainer","steps":["write `{\"version\": 1, \"roots\": {\"default\": \"project\"}}` to `$T/.rosetta/config.json`","run `rosetta_config.py show`"]},"end_state":{"must_observe":["`echo $?` prints a value other than `0`","stderr prints a JSON block containing `\"version\": 2` and `\"paths\"`"],"must_not_observe":["exit=0","`[builtin-default: roots.default = project]`","no suggested JSON (empty stderr)"]}}]}},{"id":"AC-7","description":"`rosetta_config.py plugin` still works with no project config (it is exempt).","verify":"python3 scripts/rosetta/test_resolve.py","scenario":{"id":"AC-7","primary":true,"test_tier":"integration","verification_service":"assets/rosetta_config.py plugin (real subprocess)","topology":"single-node","negative_control":{"would_fail_if":["fix reverted (the defect reproduces)","the config gate removed or stubbed so the command falls back to the built-in default","the check reads a hardcoded `.rosetta/docs` / `agents` path instead of the config"]},"evidence":{"artifact_type":"stdout","required_capture":true},"cases":[{"start_ref":"bare_repo","action":{"actor":"maintainer","steps":["run `rosetta_config.py plugin` from T with no config"]},"end_state":{"must_observe":["stdout is the plugin checkout path `$P`","exit code is 0"],"must_not_observe":["a missing-config refusal","no output (empty stdout)"]}}]}}]} -->

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

## AGENT INSTRUCTIONS

Continuation for the re-dispatched implementer: the product code is complete and mutation-proven
across two candidate commits on branch
`kb-run-sprint/imp-update-rosetta-plugin-distribute-results-1794839963/CONFIG-01` (b06e883 product
and contract tests; 916e38c AC-3 non-standard-layout case). Triage route `test` closed the code:
the remaining work is test-only - widen the AC-4 section of `scripts/rosetta/test_resolve.py` to the
full matrix this contract names, and keep the AC-3 custom-layout case. Change no product file unless
a new case exposes a real defect.

The verify `python3 scripts/rosetta/test_resolve.py` must exit non-zero under each mutant this
contract's negative controls name: default a missing key, validate only docs, prune only tmp,
hardcode docs. Prove each with a real mutation run in a disposable copy before reporting done.

### reading_list

- `.kb-run-sprint/imp-update-rosetta-plugin-distribute-results-1794839963/CONFIG-01/cycle-1.verdict.json` and `.kb-run-sprint/imp-update-rosetta-plugin-distribute-results-1794839963/CONFIG-01/cycle-2.verdict.json` - F-CONFIG-01-1/-3 and U-CONFIG-01-1/-2 carry the reproduced mutants and their exact triggers
- `scripts/rosetta/test_resolve.py` - the AC-3 custom-layout case and the AC-4 section
- `assets/rosetta_config.py` - `validate_config`, `_validate_path`, `PRUNED_DOC_SEGMENTS`, `Resolver._decide`
- `AGENTS.md` - rules 8 (real temp trees, never mocks) and 9 (grok-skills copies stay byte-identical)

### coding_standards

- One fresh `git init` temp repo per case; the CLI runs as a real subprocess of `assets/rosetta_config.py`; no mocks, stubs or in-memory fakes.
- Case names follow the existing `ac4-*` style (e.g. `ac4-missing-docs`, `ac4-absolute-research`, `ac4-pruned-sandbox`).
- `grok-skills/*/assets/rosetta_config.py` is regenerated with `python3 scripts/materialize-grok-skills.py`, never hand-edited.
- Mutation probes run in a disposable copy of the worktree, never in the working tree itself.

## Amendments

- 2026-11-16 - **refine**, authorized by the attempts_due decision `attempts_due:CONFIG-01:start`
  (attempts: implementer `ses_e1afb9240ffez04ToXP9O0GmC1`, review cycles 1 and 2; triage route
  `test`) under ask `ask-config-01-refine-1`. Chosen over split: both attempts' product code is
  correct and mutation-proven (triage: "`code` is closed at this count") and every block is an
  oracle gap in the declared verify, so a split would narrow this contract to the proven cases and
  leave the remainder carrying the same text-versus-verify contradiction that blocked both cycles.
  Strengthen only: AC-4's requirement and scenario enumerate the full matrix (each of
  docs/research/probes/agents missing; each bad value kind applied to each of the four keys; one
  case per pruned docs name - tmp, research, sandbox, .history), AC-3's scenario requires the
  non-standard-layout case (landed at 916e38c), and both scenarios' negative controls name the
  mutants that must fail the verify (default a missing key, validate only docs, prune only tmp,
  hardcode docs). No requirement claim, verify command, gate step or guardrail is weakened or
  removed. Carries the owner-authorized scope fix both attempts reported in `scope_impact` and
  reviewers kept advisory (F-CONFIG-01-2): `grok-skills/*/assets/rosetta_config.py` joins
  `guardrails.write_allowed` and the `> Files:` line (the ask authorizes it; `grok-skills/*` is in
  the binding scope's files in scope). The re-dispatched implementer continues from b06e883 and
  916e38c on `kb-run-sprint/imp-update-rosetta-plugin-distribute-results-1794839963/CONFIG-01`.
