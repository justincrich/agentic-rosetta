# scripts/

Python and bash that own writes the skills must not invent. Contribution
rules: [`AGENTS.md`](AGENTS.md).

| Script | Test | Job |
|---|---|---|
| `rosetta/write-graph.py` | `rosetta/test_write_graph.py` | Only writer of `fact-graph.json` |
| `rosetta/registry.py` | `rosetta/test_registry.py` | Exhaustive per-project graph + consumer index |
| `rosetta/provenance.py` | `rosetta/test_provenance.py` | Source pins + `--check-stale` |
| `rosetta/version_label.py` | (via write-graph / excerpt tests) | Shared `kb_version` label |
| `kb-delivery/embed-graph.sh` | `kb-delivery/test-embed-graph.sh` | Only writer of the `rosetta-kb` region |
| `kb-delivery/generate_excerpt.py` | `kb-delivery/test_generate_excerpt.py`, `test_multigraph.py` | Graph → embeddable render |
| `kb-delivery/assign.py` | `kb-delivery/test_assign.py` | Deterministic condensed/full cohort hash |
| `materialize-grok-skills.py` | `test_materialize_grok_skills.py` | Only writer of `grok-skills/` |
| `assert-no-drift.sh` | (self-failing gate) | Skill copies + named scripts + Grok pack |
| `testdata/materialize.py` | used by the registry / excerpt tests | Build a real temp consumer project from fixtures |

Resolver (lives at repo root, not here): `assets/rosetta_config.py`,
selftest `python3 assets/rosetta_config.py --selftest`. After editing it:
`./sync-assets.sh`.

Deep dives: [`rosetta/README.md`](rosetta/README.md),
[`kb-delivery/README.md`](kb-delivery/README.md).
