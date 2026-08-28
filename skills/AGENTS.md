# skills/ — directory system prompt

You are in the **canonical skill bodies**. Edit `skills/rosetta-*/SKILL.md`
only. Root contract: [`../AGENTS.md`](../AGENTS.md). Skill map:
[`README.md`](README.md).

Do **not** edit `../grok-skills/` — rematerialize it. Do **not** hand-edit
`assets/rosetta_config.py` copies in this tree; change
`../assets/rosetta_config.py` and run `./sync-assets.sh`.

## What this tree owns

Seven skill directories. Each `SKILL.md` is a thin wrapper: parse args, call
a primitive, report. Behavior that must always happen belongs in a script or
in `engine/`, not in more skill prose.

| Skill | Primitive |
|---|---|
| `rosetta-research` | `engine/src/research.ts` then `write-graph.py --apply-nrra` |
| `rosetta-bootstrap` | research → `write-graph.py --create` → `/rosetta-sync` |
| `rosetta-upgrade` | Mode C `--topics` / Mode B `--url` / Mode A → `write-graph.py --update` → embed |
| `rosetta-sync` | `registry.py` + `embed-graph.sh` |
| `rosetta-validate` | reads + `embed-graph.sh --check` |
| `rosetta-ab-test` | evaluation only |
| `rosetta-stress-test` | evaluation only |

After any `SKILL.md` change:

```bash
python3 scripts/materialize-grok-skills.py
scripts/assert-no-drift.sh --scripts-only
```

## Rules

1. **Thin wrappers.** If a step can be a script, it stays a script. Do not
   re-implement census, path resolution, graph merge, or excerpt rendering
   in markdown.
2. **Resolver lookup order** (already in each skill — keep it):

   ```
   $ROSETTA_CONFIG
     → <this skill>/assets/rosetta_config.py
     → ${ROSETTA_HOME:-$HOME/.rosetta}/bin/rosetta_config.py
   ```

   Never hardcode `~/Projects/brain`. Never invent a fourth lookup.
3. **Named `scripts/…` paths must exist.** `assert-no-drift.sh` greps every
   `scripts/…` token out of `SKILL.md` and fails if the file is missing.
4. **Validate / ab-test / stress-test do not write graphs.** Bootstrap
   `--create`s. Upgrade `--update`s. Research `--apply-nrra`s after a closed
   successful run unless `--no-graph`. Nested bootstrap/upgrade research
   passes `--no-graph` so those skills keep their own write/gate steps.
5. **`--model` is passed through, never invented.** Research STOPs if the
   engine is absent — it does not crawl.

## Do not resurrect

These are deleted bugs. Putting them back is a regression, not a feature:

- Floor-parallelism (“you MAY fan 2–3 Task workers”)
- A 3-tier research ladder (`/assimilate` → jina/exa → WebSearch) as a
  substitute for the engine
- Hand-pasted JSON into `agent.md` (the block is a generated render)
- Hardcoded `~/Projects/brain` or `brain/` paths
- Graph writes from validate / ab-test / stress-test
- A `~/.grok/skills/rosetta-*` drop-in (collides with `/rosetta:research`)

## Grok names

Canonical ids stay `rosetta-research` etc. so Claude / Codex / OpenCode /
skillshare keep `/rosetta-research`. Grok namespaces by plugin name
(`rosetta`), so the generated pack uses short ids (`research`) and slashes
as `/rosetta:research`. See [`../grok-skills/AGENTS.md`](../grok-skills/AGENTS.md).
