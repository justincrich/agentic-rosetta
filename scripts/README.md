# scripts/

Python and bash that own writes the skills must not invent. Contribution
rules: [`AGENTS.md`](AGENTS.md).

| Script | Test | Job |
|---|---|---|
| `rosetta/write-graph.py` | `rosetta/test_write_graph.py` | Only writer of `fact-graph.json` |
| `rosetta/registry.py` | `rosetta/test_registry.py` | Exhaustive per-project graph + consumer index |
| `rosetta/factgraph.py` | `rosetta/test_factgraph.py` | The fact-graph shape: pydantic model + write gate |
| `rosetta/naming.py` | `rosetta/test_naming.py` | Agent → role/slug, graph path → slug |
| `rosetta/resolve.py` | `rosetta/test_resolve.py` | Which project a run is about |
| `rosetta/versions.py` | `rosetta/test_versions.py` | Semver + `kb_version` bump rule |
| `rosetta/locators.py` | `rosetta/test_locators.py` | URL verdicts + the one TLD table |
| `rosetta/jsonfile.py` | `rosetta/test_jsonfile.py` | Atomic write, strict read, canonical digest |
| `rosetta/provenance.py` | `rosetta/test_provenance.py` | Facade over the three above; pins + `--check-stale` |
| `rosetta/roots.py` | (via provenance / write-graph tests) | Where the KB goes (**not** project discovery) |
| `rosetta/version_label.py` | (via write-graph / excerpt tests) | Shared `kb_version` label |
| `kb-delivery/embed-graph.sh` | `kb-delivery/test-embed-graph.sh` | Only writer of the `rosetta-kb` region |
| `kb-delivery/generate_excerpt.py` | `kb-delivery/test_generate_excerpt.py`, `test_multigraph.py` | Graph → embeddable render |
| `kb-delivery/assign.py` | `kb-delivery/test_assign.py` | Deterministic condensed/full cohort hash |
| `materialize-grok-skills.py` | `test_materialize_grok_skills.py` | Only writer of `grok-skills/` |
| `assert-no-drift.sh` | (self-failing gate) | Skill copies + named scripts + Grok pack |
| `testdata/materialize.py` | used by the registry / excerpt tests | Build a real temp consumer project from fixtures |

Each module above owns exactly one decision, and its test is the contract for that
decision. Adding a second copy of one beside it is the mistake these files exist to
prevent — see [`../docs/code-quality/profile.md`](../docs/code-quality/profile.md).

Resolver (lives at repo root, not here): `assets/rosetta_config.py`,
selftest `python3 assets/rosetta_config.py --selftest`. After editing it:
`./sync-assets.sh`.

Deep dives: [`rosetta/README.md`](rosetta/README.md),
[`kb-delivery/README.md`](kb-delivery/README.md).
