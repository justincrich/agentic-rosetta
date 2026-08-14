# grok-skills/ — directory system prompt

You are in the **generated Grok pack**. Do not edit these files.

Canonical skill bodies live in [`../skills/`](../skills/). After changing
those, regenerate:

```
python3 scripts/materialize-grok-skills.py
```

`--check` (wired into `scripts/assert-no-drift.sh`) fails if this tree
drifts from `skills/rosetta-*`.

Why a second tree exists: Grok namespaces by `.grok-plugin/plugin.json`
`name` (`rosetta`). Shipping `name: rosetta-research` would advertise
`/rosetta:rosetta-research`. Short ids (`research`, `bootstrap`, …) make
the slash `/rosetta:research`.

More: [`README.md`](README.md). Root contract: [`../AGENTS.md`](../AGENTS.md).
