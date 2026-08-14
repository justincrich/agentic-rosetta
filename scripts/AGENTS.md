# scripts/ — directory system prompt

You are in the **Python writers**. This tree mutates graphs, rebuilds the
registry, embeds renders into `agent.md`, and materializes the Grok pack.
Root contract: [`../AGENTS.md`](../AGENTS.md). Inventory: [`README.md`](README.md).

Do **not** edit `grok-skills/` by hand. Do **not** add a `~/Projects/brain`
special case to any `discover_repo_root()`.

## What this tree owns

| Path | Job |
|---|---|
| `rosetta/write-graph.py` | **Only** writer of `fact-graph.json` |
| `rosetta/registry.py` | Exhaustive per-project graph + consumer index |
| `rosetta/provenance.py` | Source pins, staleness probe, version bump helpers |
| `rosetta/version_label.py` | Shared `kb_version` label |
| `kb-delivery/embed-graph.sh` | **Only** writer of the `rosetta-kb` region |
| `kb-delivery/generate_excerpt.py` | Graph → embeddable render |
| `kb-delivery/assign.py` | Deterministic condensed/full cohort hash |
| `materialize-grok-skills.py` | **Only** writer of `grok-skills/` |
| `assert-no-drift.sh` | Skill-copy + named-script + Grok-pack gate |
| `testdata/` | Real committed fixtures for selftests |

`assets/rosetta_config.py` (repo root, not this folder) is the storage
resolver. After editing it, run `./sync-assets.sh` from the repo root — do
not hand-copy into `skills/*/assets/`.

## Rules

1. **`--root` / `$ROSETTA_PROJECT` always win.**
   `discover_repo_root()` order (do not add a brain branch):

   | What you pass | What you get |
   |---|---|
   | `--root` or `$ROSETTA_PROJECT` | that project |
   | cwd inside a consumer (has `.git` / `.rosetta`) | that consumer |
   | cwd inside *this* plugin, no env | **this plugin** (it has `.git`; it is the wrong tree) |
   | script parent that already has `agents/` | that fixture / in-tree copy |

   A bare `registry.py scan` or `write-graph.py` from this checkout indexes
   **the plugin**, which has no `agents/` and is the wrong place to write a
   KB. Always pass `--root` / `$ROSETTA_PROJECT` unless you are already cwd’d
   in the consumer.
2. **`write-graph.py` is the only mutator of `fact-graph.json`.** Gate before
   `--create` / `--update`: ledger closed (closed + named-gap rows ==
   censused), `run_id` matches the NRRA, `decoder_ring_hash` present on both.
   Any miss: exit non-zero, write nothing, name the check.
3. **Changesets are dict-keyed-by-section**, not a list:

   ```json
   { "added": { "r": { "no-stub": { "verdict": "REJECT", "rule": "…" } } } }
   ```

   Shape is locked by `rosetta/test_write_graph.py`. `needs_review[]` is
   never auto-applied. Do not put `source` / `confidence` inside the graph
   value — those go to `fact-sources.json`.
4. **Registry discovery is exhaustive.** Any `fact-graph.json` outside
   excluded dirs enters the index. The **only** hand tables are
   `AGENT_PREFIX_TO_SLUG` (name ≠ slug) and `SHARED_GRAPHS` (no graph of
   their own). Matching stem==slug domains need no entry.
   `manual_bindings` survive a rescan.
5. **`provenance.py --check-stale` exit codes:** `0` current, `10` stale,
   `1` undeterminable (missing file, legacy pin, `ls-remote` failed). Do
   **not** treat `1` as current. Source identity is never written into
   `fact-graph.json`.
6. **`embed-graph.sh --sync` fails closed** on an unknown slug or agent.
   It does not walk the fleet “just in case.” `--check` writes nothing.
7. **Tests are real-filesystem selftests.** `test_*.py` and
   `test-embed-graph.sh` build temp projects from `testdata/` fixtures.
   They are runnable as `python3 path/to/test_foo.py` (they also happen to
   collect under pytest). Do not replace them with mocks of the filesystem
   or of `write-graph.py`.
8. **`--adopt` / `--backfill` are honest.** Adopt stamps recoverable
   fields on a legacy graph; no ledger. Backfill harvests on-disk locators
   and an optional judged `--sources` overlay. The writer never invents
   URLs.

## Verify

From the repo root, the scripts that cover this tree:

```bash
python3 scripts/rosetta/test_registry.py
python3 scripts/rosetta/test_write_graph.py
python3 scripts/rosetta/test_provenance.py
python3 scripts/kb-delivery/test_assign.py
python3 scripts/kb-delivery/test_generate_excerpt.py
python3 scripts/kb-delivery/test_multigraph.py
bash scripts/kb-delivery/test-embed-graph.sh
python3 scripts/test_materialize_grok_skills.py
python3 assets/rosetta_config.py --selftest
./sync-assets.sh --check
python3 scripts/materialize-grok-skills.py --check
```
