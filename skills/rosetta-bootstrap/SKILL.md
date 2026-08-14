---
name: rosetta-bootstrap
description: "Create a new Rosetta KB: research (engine fanout) → write-graph.py --create → rosetta-sync. Trigger: /rosetta-bootstrap {tech} [{repo-url}]"
allowed-tools: Bash, Skill, Read, Write
---

# Rosetta Bootstrap

Thin intent wrapper. Research, graph write, and apply are primitives. This skill parses args, calls them, and reports.

```
INPUT: technology_name
INPUT: repository_url or --repo-path (local clone preferred)
INPUT: base_framework
INPUT: depth (quick|normal|deep)

PLUGIN / PROJECT / RESOLVER — same resolution as rosetta-research (never hardcode brain/).

[0] RESOLVE
    KB_DIR = $(python3 "$RESOLVER" path --class docs --slug {tech_slug} --ensure)

[1] RESEARCH
    INVOKE /rosetta-research "{tech}" with the same repo/docs/depth/model.
    CONSUME NRRA + ledger from the engine --out-dir.

[2] GRAPH  (the only write of fact-graph.json)
    python3 "$PLUGIN/scripts/rosetta/write-graph.py" --create {tech_slug} \
      --nrra {out}/nrra.md --ledger {out}/ledger.json --root "$PROJECT"
    IF a graph already exists → STOP (create refuses). Use /rosetta-upgrade.
    Do NOT write fact-graph.json by hand. Do NOT paste JSON into agent.md.

[3] DOCS
    From the NRRA, write README.md, ROSETTA.md, and 3-lens concept docs into $KB_DIR.
    Probe catalog via the resolver's probes class.

[4] SCAFFOLD agents/{tech}-{planner,implementer,reviewer}/agent.md
    Frontmatter + hand-authored role prose. Leave the sentinel region empty.

[5] APPLY
    INVOKE /rosetta-sync {tech_slug}
    Verify: bash "$PLUGIN/scripts/kb-delivery/embed-graph.sh" --root "$PROJECT" --check {agent}

[6] REPORT
    tech, base, census row count, ledger closed/gaps, graph path, triad created.
```

## Usage

```
/rosetta-bootstrap "GPUI" --repo-url https://github.com/zed-industries/zed --base-framework React
```
