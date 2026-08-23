# skills/

Canonical skill bodies. Contribution rules: [`AGENTS.md`](AGENTS.md).
Grok pack (generated, do not edit): [`../grok-skills/README.md`](../grok-skills/README.md).

## Slash names

| Canonical id (`skills/`) | Claude / Codex / OpenCode / skillshare | Grok (plugin `rosetta`) |
|---|---|---|
| `rosetta-research` | `/rosetta-research` | `/rosetta:research` |
| `rosetta-bootstrap` | `/rosetta-bootstrap` | `/rosetta:bootstrap` |
| `rosetta-upgrade` | `/rosetta-upgrade` | `/rosetta:upgrade` |
| `rosetta-validate` | `/rosetta-validate` | `/rosetta:validate` |
| `rosetta-sync` | `/rosetta-sync` | `/rosetta:sync` |
| `rosetta-ab-test` | `/rosetta-ab-test` | `/rosetta:ab-test` |
| `rosetta-stress-test` | `/rosetta-stress-test` | `/rosetta:stress-test` |

Native Claude plugin install namespaces as `/agentic-rosetta:rosetta-research`.

After editing a `SKILL.md`:

```bash
python3 scripts/materialize-grok-skills.py
scripts/assert-no-drift.sh --scripts-only
```

## Thin wrappers — where behavior lives

| Skill | What the markdown does | Primitive |
|---|---|---|
| `rosetta-research` | Detect harness, pass model, shell out | `engine/src/research.ts`. Optional `write-graph.py --create` only if the caller asked to persist a graph |
| `rosetta-bootstrap` | Resolve roots, scaffold triad + 3-lens docs | research → `write-graph.py --create` → `/rosetta-sync` |
| `rosetta-upgrade` | Mode C `--topics` expand / Mode B `--url` refresh / Mode A benchmark; user gate | `provenance.py --check-stale` (B only), research, `write-graph.py --update`, `embed-graph.sh` |
| `rosetta-sync` | Rebuild index, overwrite sentinel blocks | `registry.py scan/validate`, `embed-graph.sh --sync` |
| `rosetta-validate` | Challenge + 28 universal anti-patterns | reads graph/probes; `embed-graph.sh --check`; **no graph write** |
| `rosetta-ab-test` | Before/after measurement | evaluation only |
| `rosetta-stress-test` | Universal coding challenge against the KB | evaluation only |

Each skill directory also carries `assets/rosetta_config.py`, a
byte-identical copy of `assets/rosetta_config.py` at the repo root. Claude’s
`/plugin install` never runs `install.sh`, so the copy has to travel with
the skill. Change the canonical file and run `./sync-assets.sh`.

Storage resolution is documented in [`../CONFIG.md`](../CONFIG.md), not here.
