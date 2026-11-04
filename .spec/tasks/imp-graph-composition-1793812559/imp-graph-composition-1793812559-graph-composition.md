# imp-graph-composition-1793812559-graph-composition: Compose Rosetta graphs by value with versioned dependent sync

> Assignee: python-implementer
> Depends on: (none)
> Priority: P1
> Type: feature
> Files: scripts/rosetta/factgraph.py, scripts/rosetta/versions.py, scripts/rosetta/write-graph.py, scripts/rosetta/registry.py, scripts/rosetta/jsonfile.py, scripts/kb-delivery/generate_excerpt.py, scripts/rosetta/test_factgraph.py, scripts/rosetta/test_versions.py, scripts/rosetta/test_write_graph.py, scripts/rosetta/test_registry.py, scripts/kb-delivery/test_generate_excerpt.py, scripts/kb-delivery/test_multigraph.py, scripts/kb-delivery/test-embed-graph.sh, skills/rosetta-upgrade/SKILL.md, skills/rosetta-sync/SKILL.md
> Patterns: minimum-diff-discipline, anti-stub
> Scope: /Users/justinrich/.config/brain/improvements/imp-graph-composition-1793812559.json

## Context

we need to update rosetta such that kbs and graphs can be composed in other kbs. for example we are working on a herdrdev graph for contributing to herdr desktop. The codebase is in rust and I want to compose the rust graph so agents both know about the codebase as well as are capable in rust. I'd like to copy by value the rust graph but add metadata that points back to the original graph so in future upgrades we can sync changes. For example if we upgrade rust we should be able to find all other graphs that depend on it and upgrade those graphs too ... or have the command list all dependent graphs and ask if it should replace the rust subgraph part with the new graph. The default should be update all but ideally the user can specify a subset. As such all graphs should be versioned.

Root cause summary: The composition declaration is written into _meta and _meta is stripped from every render, so a declared composition never reaches an agent. The only composition that actually delivers facts is registry-level multi-graph binding, which needs a hand-managed binding per agent and only works when every graph sits in one project root. Because _meta is excluded from the integrity stamp, even the declaration's version identity is unverifiable, and no command answers 'which knowledge bases borrowed Rust'.

For full reproduction evidence, root-cause file:line refs, considered alternatives, and
the second agent's notes, READ the binding scope named on the `> Scope:` line above
(durable ScopeState JSON per brain/docs/SCOPESTATE-CONTRACT.md).

That contract is binding. You may NOT touch files outside the `> Files:` list above.

## Acceptance Criteria

- [ ] AC-1: After an original's refresh lands, one command lists every borrower in the project whose _meta.composed_with names the original, each copy's recorded kb_version, and the original's current v.kb_version, then asks update all / a named subset / skip. Default is update all.
- [ ] AC-2 (the criterion that proves the copy): Composition copies the original's v/w/r/a/x/t facts into the borrower's body by value. A test renders the borrower's embed and finds at least one original rule key. A refresh that runs through the writer and leaves `--check` green is NOT sufficient proof — `--check` hashes the body only and ignores _meta (write-graph.py:200 graph_sha -> jsonfile.body_digest; cmd_check at write-graph.py:588).
- [ ] AC-3: A borrower whose declared original is absent from the project fails closed with a named error; there is no silent partial render and no silent single-graph fallback.
- [ ] AC-4: A refresh whose recorded copy fingerprint no longer matches the borrower's current facts stops and reports the mismatch without writing. It reports THAT the copy diverged, not which fact changed.
- [ ] AC-5: Propagation never runs unattended. The plugin's command layer asks the person and the script refuses to write without an explicit confirmation argument — a non-interactive invocation without it exits non-zero and writes nothing. Enforced in code (required argument + terminal check), not by prose instruction to an agent.
- [ ] AC-6: The refresh runs through write-graph.py only; a hand edit fails `--check`. The upgrade skill asks the person before propagating and documents the confirm argument.
- [ ] AC-7: The existing gate stays green: test_registry.py, test_write_graph.py, test_provenance.py, test_factgraph.py, test_versions.py, test_generate_excerpt.py, test_multigraph.py, test-embed-graph.sh - including the unchanged composed-with assertions at test_write_graph.py:117 and test_registry.py:122.
- [ ] AC-8: A composed-version check fails when the borrower records a kb_version that no longer matches the original's current v.kb_version, so staleness is answerable offline without re-reading the original.

## Test Criteria

| # | Boolean Statement | Maps To AC | Verify | Status |
|---|-------------------|------------|--------|--------|
| 1 | The propagation command lists every borrower with recorded and current kb_version and asks update all / subset / skip | AC-1 | `python3 scripts/rosetta/test_write_graph.py` | [ ] TRUE [ ] FALSE |
| 2 | The borrower's rendered embed contains at least one fact copied from the original | AC-2 | `python3 scripts/kb-delivery/test_generate_excerpt.py` | [ ] TRUE [ ] FALSE |
| 3 | A declared original that is absent fails closed with a named error | AC-3 | `python3 scripts/rosetta/test_write_graph.py` | [ ] TRUE [ ] FALSE |
| 4 | A refresh whose recorded fingerprint no longer matches stops and reports without writing | AC-4 | `python3 scripts/rosetta/test_write_graph.py` | [ ] TRUE [ ] FALSE |
| 5 | A refresh without an explicit confirmation exits non-zero and writes nothing | AC-5 | `python3 scripts/rosetta/test_write_graph.py` | [ ] TRUE [ ] FALSE |
| 6 | The refresh runs through the single writer and a hand edit fails --check | AC-6 | `python3 scripts/rosetta/test_write_graph.py` | [ ] TRUE [ ] FALSE |
| 7 | Every existing gate script exits 0 | AC-7 | `python3 scripts/rosetta/test_factgraph.py` | [ ] TRUE [ ] FALSE |
| 8 | A composed-version check fails when the recorded edition no longer matches the original | AC-8 | `python3 scripts/rosetta/test_versions.py` | [ ] TRUE [ ] FALSE |

<!-- REQUIREMENT-CONTRACT v1
fixtures: {"reproduction":{"description":"temp project with .rosetta/docs/rust/fact-graph.json (v.kb_version 1.0.0, rule reject-unwrap=REJECT) and .rosetta/docs/herdrdev/fact-graph.json declaring _meta.composed_with=[{graph:rust,kb_version:1.0.0}]","seed_method":"cli","records":{"rust_kb_version":"1.0.0","borrower":"herdrdev","rule_key":"reject-unwrap"}}}
AC-1: AC-1
  verify: python3 scripts/rosetta/test_write_graph.py
  scenario: {"id":"AC-1 propagation lists borrowers and asks","test_tier":"integration","negative_control":{"would_fail_if":["fix reverted (registry reverse query absent)","stubbed scan returning an empty list","hardcoded borrower name"]},"evidence":{"artifact_type":"stdout","required_capture":true},"cases":[{"start_ref":"reproduction","action":{"steps":["python3 scripts/rosetta/write-graph.py --update rust --changeset bump.json  # sets v.kb_version to \"1.1.0\"","python3 scripts/rosetta/write-graph.py --propagate rust --list"]},"end_state":{"must_observe":["stdout lists borrower `herdrdev` with recorded kb_version \"1.0.0\" and current \"1.1.0\"","stdout asks `update all`, `a subset`, or `skip` and states `update all` is the default"],"must_not_observe":["stdout reports 0 borrowers","an empty list","a silent exit with no prompt"]}}]}
AC-2: AC-2
  verify: python3 scripts/kb-delivery/test_generate_excerpt.py
  scenario: {"id":"AC-2 borrowed facts land in the borrower body","test_tier":"integration","negative_control":{"would_fail_if":["fix reverted (composition writes metadata only, no copy)","stubbed renderer that drops the composed container","empty borrower body"]},"evidence":{"artifact_type":"stdout","required_capture":true},"cases":[{"start_ref":"reproduction","action":{"steps":["python3 scripts/rosetta/write-graph.py --update herdrdev --changeset compose.json  # --composed-with rust","python3 scripts/kb-delivery/generate_excerpt.py --agent herdrdev-implementer --embed"]},"end_state":{"must_observe":["embed stdout contains the Rust rule key \"reject-unwrap\"","embed stdout contains a composed container keyed \"rust\""],"must_not_observe":["0 occurrences of \"reject-unwrap\"","an embed containing only the herdrdev body"]}}]}
AC-3: AC-3
  verify: python3 scripts/rosetta/test_write_graph.py
  scenario: {"id":"AC-3 missing original fails closed","test_tier":"integration","negative_control":{"would_fail_if":["fix reverted (missing source tolerated)","stubbed lookup returning an empty graph","no-op error path"]},"evidence":{"artifact_type":"stdout","required_capture":true},"cases":[{"start_state":{"description":".rosetta/docs/herdrdev/fact-graph.json declares _meta.composed_with=[{graph:ghost}] and no ghost graph exists","seed_method":"cli","records":{"declared":"ghost"}},"action":{"steps":["python3 scripts/rosetta/write-graph.py --update herdrdev --changeset compose.json --composed-with ghost"]},"end_state":{"must_observe":["exit code 2","stderr names \"ghost\" and the missing fact-graph path"],"must_not_observe":["a rendered block with 0 borrowed facts","a silent success (exit 0)"]}}]}
AC-4: AC-4
  verify: python3 scripts/rosetta/test_write_graph.py
  scenario: {"id":"AC-4 diverged copy stops the refresh","test_tier":"integration","negative_control":{"would_fail_if":["fix reverted (whole block overwritten silently)","stubbed fingerprint comparison","empty digest check"]},"evidence":{"artifact_type":"stdout","required_capture":true},"cases":[{"start_state":{"description":"herdrdev composed from rust at fingerprint \"9f2c1ab0\" and its copied body was then edited so its digest is \"c4e7a91d\"","seed_method":"cli","records":{"recorded_fingerprint":"9f2c1ab0","current_digest":"c4e7a91d"}},"action":{"steps":["python3 scripts/rosetta/write-graph.py --propagate rust --confirm"]},"end_state":{"must_observe":["exit code 10","stderr contains \"copy fingerprint mismatch\"","herdrdev body digest still equals \"c4e7a91d\" after the run"],"must_not_observe":["the borrower file rewritten","0 bytes changed reported as success"]}}]}
AC-5: AC-5
  verify: python3 scripts/rosetta/test_write_graph.py
  scenario: {"id":"AC-5 unattended propagation refuses","test_tier":"integration","negative_control":{"would_fail_if":["fix reverted (confirm gate absent)","stubbed isatty returning true","hardcoded confirmation"]},"evidence":{"artifact_type":"stdout","required_capture":true},"cases":[{"start_ref":"reproduction","action":{"steps":["printf '' | python3 scripts/rosetta/write-graph.py --propagate rust  # no --confirm, stdin is not a TTY"]},"end_state":{"must_observe":["exit code 2","stderr contains \"confirmation required\"","herdrdev file digest is unchanged at \"1f392b03\""],"must_not_observe":["the refresh applied without an answer","an empty confirmation accepted","0 refusals"]}}]}
AC-6: AC-6
  verify: python3 scripts/rosetta/test_write_graph.py
  scenario: {"id":"AC-6 single writer and hand-edit detection","test_tier":"integration","negative_control":{"would_fail_if":["fix reverted (hand edit accepted)","stubbed digest comparison","no-op check verb"]},"evidence":{"artifact_type":"stdout","required_capture":true},"cases":[{"start_ref":"reproduction","action":{"steps":["edit .rosetta/docs/herdrdev/fact-graph.json by hand (change one rule text)","python3 scripts/rosetta/write-graph.py --check herdrdev"]},"end_state":{"must_observe":["exit code 1","stderr contains \"stamp\" and \"mismatch\""],"must_not_observe":["exit 0 with 0 findings","a passing check"]}}]}
AC-7: AC-7
  verify: python3 scripts/rosetta/test_factgraph.py
  scenario: {"id":"AC-7 existing gate stays green","test_tier":"integration","negative_control":{"would_fail_if":["fix reverted (gate red)","stubbed test runner exiting 0","empty test selection"]},"evidence":{"artifact_type":"stdout","required_capture":true},"cases":[{"start_state":{"description":"clean worktree at base 9ae48f6 with the seven gate scripts present","seed_method":"cli","records":{"base":"9ae48f6"}},"action":{"steps":["run test_registry.py, test_write_graph.py, test_provenance.py, test_factgraph.py, test_versions.py, test_generate_excerpt.py, test_multigraph.py, test-embed-graph.sh"]},"end_state":{"must_observe":["each of the 8 scripts exits 0 and prints \"ok\"","stdout of the run names \"test_factgraph.py\" and \"test_versions.py\" among the 8 executed"],"must_not_observe":["any script exits non-zero","0 scripts run","an empty test selection"]}}]}
AC-8: AC-8
  verify: python3 scripts/rosetta/test_versions.py
  scenario: {"id":"AC-8 composed edition staleness is answerable offline","test_tier":"integration","negative_control":{"would_fail_if":["fix reverted (recorded edition ignored)","stubbed version comparison","empty report"]},"evidence":{"artifact_type":"stdout","required_capture":true},"cases":[{"start_state":{"description":"herdrdev records composed kb_version \"1.0.0\"; rust is now \"1.2.0\"","seed_method":"cli","records":{"recorded":"1.0.0","current":"1.2.0"}},"action":{"steps":["python3 scripts/rosetta/write-graph.py --check-composed herdrdev"]},"end_state":{"must_observe":["exit code 10","stderr prints recorded \"1.0.0\" and current \"1.2.0\""],"must_not_observe":["exit 0 with no difference reported","an empty report","0 differences when a version moved"]}}]}
TC-1: Maps to AC-1 (inherits AC-1's scenario)
TC-2: Maps to AC-2 (inherits AC-2's scenario)
TC-3: Maps to AC-3 (inherits AC-3's scenario)
TC-4: Maps to AC-4 (inherits AC-4's scenario)
TC-5: Maps to AC-5 (inherits AC-5's scenario)
TC-6: Maps to AC-6 (inherits AC-6's scenario)
TC-7: Maps to AC-7 (inherits AC-7's scenario)
TC-8: Maps to AC-8 (inherits AC-8's scenario)
-->

## Out of scope

- Scheduled or continuous propagation
- Merging knowledge bases that were researched separately into a shared edition
- Any change to how agents are scored or how knowledge bases are judged

## Risks

- Borrowers grow by roughly the size of the original body, so a 600-byte embed becomes about 9 KB; every consuming agent's context grows accordingly.
- Changing the host's stamp on every dependency refresh means every borrower's `--check` goes red until re-synced; intended, but noisy.
- A partial refresh leaves some borrowers moved and others not, so the command must report per-borrower outcomes rather than a single summary line.
- Copying whole sections can overwrite a local edit to a borrowed fact; the fingerprint guard stops the write and reports the mismatch instead.
- No other in-flight copy of this repo exists under .claude/worktrees, so no overlapping edits were observed.
- main advanced 8293c6a -> 9ae48f6 mid-plan; the branch was re-cut from the new tip and the file list re-anchored to the modules that now own each decision (factgraph.py, versions.py). The gate gained test_factgraph.py and test_versions.py since the plan was written.

## Verification posture

Per `AGENTS.md` Supreme Rule: the task is complete only when each AC is verified against
REAL services. For this task that means running the real writer against a real temp project
built from `scripts/testdata/materialize.py`, and taking the embed from the real
`generate_excerpt.py`. A green `--check` alone is NOT proof of AC-2: `--check` hashes the
body only and ignores `_meta`.
