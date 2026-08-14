# scripts/rosetta/

Graph write, registry, and source identity. All accept `--root` /
`$ROSETTA_PROJECT`. None of them hardcode a consumer repo.

**Bare runs from this plugin checkout target the plugin** (it has `.git`).
Pass `--root "$PROJECT"` or export `$ROSETTA_PROJECT` unless you are already
cwd’d in the consumer that owns `agents/`.

Contribution rules: [`../AGENTS.md`](../AGENTS.md).

## `write-graph.py` — only writer of `fact-graph.json`

```bash
python3 scripts/rosetta/write-graph.py --root "$PROJECT" --create <slug> \
  --nrra <nrra.md> --ledger <ledger.json>

python3 scripts/rosetta/write-graph.py --root "$PROJECT" --update <slug> \
  --changeset <changeset.json> --ledger <ledger.json> [--nrra <nrra.md>]

python3 scripts/rosetta/write-graph.py --root "$PROJECT" --check <slug>
python3 scripts/rosetta/write-graph.py --root "$PROJECT" --adopt <slug>
python3 scripts/rosetta/write-graph.py --root "$PROJECT" --backfill <slug>
python3 scripts/rosetta/write-graph.py --root "$PROJECT" --backfill --all \
  [--sources judged.json]
```

`--create` / `--update` refuse to write unless:

1. the ledger is closed (closed + named-gap rows == censused)
2. `run_id` matches the NRRA
3. `decoder_ring_hash` is present on both

`--create` also refuses if the graph already exists (use `--update` /
`/rosetta-upgrade`). `--check` on a legacy (unstamped) graph warns and
exits 0. `--adopt` stamps recoverable `v` fields without a ledger.
`--backfill` harvests on-disk locators plus an optional judged `--sources`
overlay; it never invents URLs.

Changeset shape is **dict-keyed-by-section**. See `test_write_graph.py`.

## `registry.py` — per-project index

```bash
python3 scripts/rosetta/registry.py --root "$PROJECT" scan
python3 scripts/rosetta/registry.py --root "$PROJECT" validate
python3 scripts/rosetta/registry.py --root "$PROJECT" list
python3 scripts/rosetta/registry.py --root "$PROJECT" graph-for <agent>
python3 scripts/rosetta/registry.py --root "$PROJECT" consumers <slug>
python3 scripts/rosetta/registry.py --root "$PROJECT" agents
python3 scripts/rosetta/registry.py --root "$PROJECT" register <agent> <slug>
python3 scripts/rosetta/registry.py --root "$PROJECT" unregister <agent>
```

`scan` walks the project for every `fact-graph.json` and writes
`.rosetta/registry.json`. `register` is a *manual binding* that survives
rescans (`manual_bindings`). The agent must exist and the slug must already
be a known graph.

## `provenance.py` — source identity, not in the graph

```bash
python3 scripts/rosetta/provenance.py --check-stale <slug> --root "$PROJECT"
```

Exit `0` current · `10` stale (names which sources moved) · `1`
undeterminable. Do not treat `1` as current.

## `version_label.py`

Library used by the registry and the excerpt generator. Accepts both
`v.pinned` and `v.pinned_date`. Not a CLI.

## Test

```bash
python3 scripts/rosetta/test_write_graph.py
python3 scripts/rosetta/test_registry.py
python3 scripts/rosetta/test_provenance.py
```
