# scripts/kb-delivery/

Turn `fact-graph.json` into the block agents actually see, and write that
block into `agent.md`. Contribution rules: [`../AGENTS.md`](../AGENTS.md).

The graph is the source of truth. The body block is **generated**, never
hand-edited, and delimited by:

```
<!-- rosetta-kb:begin domain="…" role="…" kb_version="…" -->
…
<!-- rosetta-kb:end -->
```

Only the region between the sentinels is touched. Hand-authored prose is
left alone.

## `embed-graph.sh`

```bash
bash scripts/kb-delivery/embed-graph.sh --root "$PROJECT" <agent-dir|agent-name>
bash scripts/kb-delivery/embed-graph.sh --root "$PROJECT" --all
bash scripts/kb-delivery/embed-graph.sh --root "$PROJECT" --sync [<slug|agent>]
bash scripts/kb-delivery/embed-graph.sh --root "$PROJECT" --check [<agent>|--all]
bash scripts/kb-delivery/embed-graph.sh --root "$PROJECT" --list
```

- `--sync` with no known slug or agent exits non-zero and does **not**
  walk the fleet.
- `--check` writes nothing. Exit 0 only when every block matches a fresh
  render. Missing/unreadable graph → non-zero (never silently drop the
  block).
- No network, no agent calls.

## `generate_excerpt.py`

```bash
python3 scripts/kb-delivery/generate_excerpt.py --root "$PROJECT" --agent <name> --embed
python3 scripts/kb-delivery/generate_excerpt.py --graph path/to/fact-graph.json --embed
python3 scripts/kb-delivery/generate_excerpt.py --agent <name> --print-version
```

| Flag | Meaning |
|---|---|
| `--embed` | Wrap in sentinels (the `agent.md` form) |
| `--embed-format json` | Default. Lossless minified JSON of the graph |
| `--embed-format markdown` | Legacy per-role markdown render |
| `--kb-source full\|condensed` | Role excerpt richness (markdown path) |
| `--role` | `planner` / `implementer` / `reviewer` |
| `--print-version` | Print only the `kb_version` label |

Graph resolve order: agent-local `agents/<name>/kb/fact-graph.json`, then
`.rosetta/docs/<slug>/`, then the shared-graph map
(`cloudflare-rust` → `cloudflare-workers`).

## `assign.py`

Pure hash. Same `session_key` always maps to the same cohort
(`full` / `condensed`). No I/O.

```bash
python3 scripts/kb-delivery/assign.py --session-key "$SESSION"
```

## Test

```bash
python3 scripts/kb-delivery/test_assign.py
python3 scripts/kb-delivery/test_generate_excerpt.py
python3 scripts/kb-delivery/test_multigraph.py
bash scripts/kb-delivery/test-embed-graph.sh
```
