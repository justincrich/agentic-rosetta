# scripts/rosetta/

Graph write, registry, and source identity. All accept `--root` /
`$ROSETTA_PROJECT`. None of them hardcode a consumer repo.

**Bare runs from this plugin checkout target the plugin** (it has `.git`).
Pass `--root "$PROJECT"` or export `$ROSETTA_PROJECT` unless you are already
cwd’d in the consumer that owns `agents/`.

Contribution rules: [`../AGENTS.md`](../AGENTS.md).

## Module map

One decision per module. Do not add a second copy of any of these beside itself.

| Module | Owns | Pure? |
|---|---|---|
| `resolve.py` | Which project a run is about (5-tier precedence) | env + fs |
| `naming.py` | Agent → (stem, role), agent → slug, graph path → slug, `ROLES` | yes |
| `versions.py` | Semver parse/increment, the `kb_version` bump rule | yes |
| `locators.py` | URL verdicts, the one valid-TLD table | yes |
| `factgraph.py` | The fact-graph shape: pydantic model + the write gate | yes |
| `jsonfile.py` | Atomic write, strict read, canonical digest | disk |
| `provenance.py` | Facade over `versions`/`locators`/`jsonfile`; pins, staleness, companions | disk + net |
| `registry.py` | Exhaustive graph index + consumer mapping | disk |
| `roots.py` | Where the KB goes — **not** project discovery | env + subprocess |

`roots.choose()` and `resolve.project_root()` answer different questions and must stay
separate: `roots` decides where an artifact lands and deliberately does not walk up;
`resolve` decides which project is being indexed and does.

`factgraph.py` is the only module here that imports **pydantic** (declared in
`../../pyproject.toml`). A missing install fails closed with the command. Never add a
silent fallback that validates less.

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
4. the resulting graph passes `factgraph.validate()` — schema, the
   `rosetta-kb:end` sentinel check, and a non-fake `v.version`

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
