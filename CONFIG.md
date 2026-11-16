# Configuring where Rosetta stores things

By default every artifact stays in the project you run a skill in. That is the historical behavior
and it stays the default — install the plugin, change nothing, and nothing moves.

You only need this file if you want some artifacts kept **centrally** instead.

## The problem this solves

The skills used to hardcode `.rosetta/research/<tech>.md` relative to the current project. Run
`/rosetta-research` from one repo and the notes land there; run it from another and they land
somewhere else — split from the knowledge base and agents that consume them. Real example from a
live machine: `idea-factory/.rosetta/research/ai-sdk.md`, while the `ai-sdk` knowledge base and its
agent triad lived in a completely different repo.

## The three artifact classes

| Class | What it is | Path under the chosen root |
|---|---|---|
| `research` | Raw findings from `/rosetta-research` (the NRRA) | `<root>/.rosetta/research/<tech>.md` |
| `docs` | The knowledge base — `fact-graph.json` + concept docs | `<root>/.rosetta/docs/<tech>/` |
| `probes` | Anti-pattern catalogs reviewers run | `<root>/.rosetta/probes/<tech>.md` |

Each is configured independently.

## Config files

Device-wide (created by `install.sh`):

```jsonc
// ~/.rosetta/config.json
{
  "version": 1,
  "roots": {
    "default": "project",
    "research": "~/Projects/agent-content",
    "probes": "~/Projects/agent-content",
    "docs": "~/Projects/agent-content"
  }
}
```

Those three values belong to the machine, not to this repo. This machine's device file names
the consumer repository (see [Which repo holds the content](#which-repo-holds-the-content));
another machine may leave them at `"project"` and change nothing.

Per-project, same shape, overrides the device file:

```jsonc
// <project>/.rosetta/config.json
{ "version": 1, "roots": { "research": "project" } }
```

## Run limits

The engine's live-worker cap is a device (or project) setting, not a skill hint.

```jsonc
// ~/.rosetta/config.json
{
  "version": 1,
  "roots": { "default": "project" },
  "limits": { "concurrency": 4 }
}
```

`limits.concurrency` is the default for `/rosetta-research` when `--concurrency` is omitted. Integer, 1–16. Out-of-range values are skipped, not clamped.

Precedence: `--concurrency` > `$ROSETTA_CONCURRENCY` > project `.rosetta/config.json` > device `~/.rosetta/config.json` > 8.

### Root values

| Value | Means |
|---|---|
| `"project"` | The repo you are in — nearest ancestor with `.git`, else with `.rosetta`, else the cwd. **Default.** |
| `"device"` | `~/.rosetta` itself (override the location with `$ROSETTA_HOME`). No nested `.rosetta`: artifacts go to `~/.rosetta/research/…`. |
| any path | Absolute or `~`-relative, e.g. `"~/Projects/agent-content"`. |

## Precedence

Per class, highest first. The first tier that yields a value wins; within a tier, the specific class
key beats `default`.

1. **CLI flag** — `--research-root` / `--docs-root` / `--probes-root`, or `--root` for all
2. **`<project>/.rosetta/config.json`** — `roots.<class>`, then `roots.default`
3. **`~/.rosetta/config.json`** — `roots.<class>`, then `roots.default`
4. **Built-in** — `project`

## Who obeys this config

Every path decision goes through `rosetta_config.py`. The scripts that write and read the
knowledge base reach it via `scripts/rosetta/roots.py`:

| Tool | Root it uses |
|---|---|
| the skills (`research`, `docs`, `probes` paths) | resolver, always |
| `write-graph.py` (the only writer of `fact-graph.json`) | `--root` → `$ROSETTA_PROJECT` → **resolver** → cwd |
| `provenance.py --check-stale` | same order, so it probes the graph `write-graph.py` will update |
| `registry.py`, `embed-graph.sh` | the **project** — they scan the repo that owns `agents/` (the consumer repository), by design |

`--root` and `$ROSETTA_PROJECT` are explicit instructions and beat the config; without them the
destination is the configured one, never "whatever directory the shell is standing in."

> Before this, `write-graph.py` and `provenance.py` defaulted straight to `Path.cwd()` and never
> read the config at all. Running a skill from an unrelated repo wrote the KB into that repo,
> silently, exit 0 — and editing this config could not move it. `rosetta-bootstrap` was worse: it
> resolved `KB_DIR` through the resolver but passed `--root "$PROJECT"` to the writer, so with any
> non-`project` docs root the graph and its concept docs landed in two different repos.

## Reading is not the same as writing

Writes go to the resolved root. **Reads search three places** — configured root, then project, then
device — and take the first hit. A knowledge base that already exists in a project keeps being found
after you point the config elsewhere; nothing is orphaned by a config change.

## Honest degradation

The resolver never fakes and never crashes:

| Situation | Behavior |
|---|---|
| No config anywhere | `project` — byte-identical to pre-config behavior |
| Malformed JSON | Warn naming the file and line, skip that tier, continue, exit 0 |
| Config isn't an object | Same — warn and skip |
| Resolved root not writable | Warn, fall back to the project, **and flag the fallback** so the artifact's `storage:` field records where it actually landed rather than where it meant to go |
| Resolver missing entirely | Skills fall back to project-local paths and say so in their report |

## Which repo holds the content

This repo ships machinery only. The content it produces and consumes — every agent prompt and
every knowledge base — lives in the **consumer repository**:

| What | Where |
|---|---|
| agent prompts | `<consumer>/agents/<name>/agent.md` |
| knowledge bases (fact graph + concept docs) | `<consumer>/.rosetta/docs/<slug>/` |
| research notes | `<consumer>/.rosetta/research/<slug>.md` |
| anti-pattern probes | `<consumer>/.rosetta/probes/<slug>.md` |

On this machine the consumer is `~/Projects/agent-content`, and `~/.rosetta/config.json` points
all three storage classes there, so a skill run from any directory writes next to the agents that
read the result. The same content is published to a read-only MCP service, which is how a machine
that carries no content checkout receives it.

A device config is machine state, not repo state. Before changing it, keep a backup of the
previous file beside it (`config.json.bak-<timestamp>`) so the old roots stay recoverable.

## ⚠️ One real constraint on `docs`

If you use the agent-embed pipeline (`registry.py` + `embed-graph.sh`, which inject a fact-graph
render into agent bodies), be careful pointing `docs` somewhere new. That pipeline finds
fact-graphs by **walking the repo that owns `agents/`**. A knowledge base written outside that repo
is invisible to it — the agents silently never receive the graph.

Point `docs` at the repo that holds your agents — `~/Projects/agent-content` on this machine, which
is why the device file names it instead of leaving it at `"project"` — and run
`/rosetta-bootstrap` from that repo.
`rosetta_config.py doctor` warns when the resolved docs root has no sibling `agents/` directory,
and `write-graph.py` now repeats that warning at write time rather than letting you discover it
when an agent turns up without its graph.

Note `"project"` means *the repo you are standing in*, not any particular repo. Running a skill
from an unrelated checkout with `docs: "project"` puts the KB in that checkout — correctly, per
this config, and still uselessly far from your agents. That is a reason to run bootstrap from the
right repo, not a bug.

## Commands

```bash
R=~/.rosetta/bin/rosetta_config.py     # or <skill-dir>/assets/rosetta_config.py

python3 $R show --format plain          # what are my roots, and what decided them?
python3 $R show                         # same, as JSON with full provenance
python3 $R path --class research --slug ai-sdk        # absolute file path
python3 $R path --class docs --slug ai-sdk --file fact-graph.json
python3 $R root --class probes --ensure               # class directory, created if missing
python3 $R find --class docs --slug ai-sdk            # read side: first existing copy
python3 $R init                                       # create ~/.rosetta (idempotent)
python3 $R plugin                                     # the checkout that owns engine/ + scripts/
python3 $R plugin --why                               # ...and which tier resolved it
python3 $R doctor                                     # list stray artifacts (dry run)
python3 $R doctor --apply --exclude ~/Projects/some-repo
python3 $R --selftest                                 # real-filesystem tests
```

`show --format plain` prints both the resolved directory and what decided it, e.g.

```
  research  /Users/me/Projects/agent-content/.rosetta/research
            [device: roots.research = ~/Projects/agent-content]
  docs      /Users/me/Projects/agent-content/.rosetta/docs
            [device: roots.docs = ~/Projects/agent-content]
```

Note `[device: roots.docs = project]` in a config that says `"docs": "project"` — the *device*
config supplied the value `project`, which resolved to the repo you are standing in. Tier and
value are different things; the output shows both.

## `doctor`

Finds artifacts sitting outside their configured root and, with `--apply`, moves them. It is a dry
run by default and never overwrites an existing destination (conflicts are reported, not resolved).

It only checks **centralized** classes. A class whose root is `project` is per-repo by design, so an
artifact in another project is correctly placed, not stray.

## Where the resolver lives

Three copies, kept byte-identical by `./sync-assets.sh` (`--check` fails on drift):

1. `assets/rosetta_config.py` — canonical, in the plugin repo
2. `skills/<name>/assets/rosetta_config.py` — travels with each skill, so Claude Code's native
   `/plugin install` (which never runs `install.sh`) still has it
3. `~/.rosetta/bin/rosetta_config.py` — installed by `install.sh`

Skills look for it in that order: `$ROSETTA_CONFIG` → their own `assets/` → `~/.rosetta/bin/` →
fall back to project-local paths.

`install.sh` syncs these copies even when it skips a hand-edited `SKILL.md`, so an installed
resolver can never quietly fall behind the canonical one.

## Where the *engine* lives

An installed skill is only `SKILL.md` + `assets/`. It carries no `engine/`, so walking up from an
installed skill never finds `engine/src/research.ts` — `/rosetta-research` must STOP rather than
go hunting the filesystem for it. `install.sh` therefore records the checkout:

```
~/.rosetta/plugin-root      # e.g. /Users/me/Projects/agentic-rosetta
```

`python3 $R plugin` resolves it: `$ROSETTA_PLUGIN` → that pointer → walking up from the resolver
itself (true for the in-checkout copy). A candidate only counts if it really contains the engine,
so a stale pointer is rejected rather than returned. No candidate → exit 1, and the skill stops
with install instructions.

## Uninstalling

`./uninstall.sh` removes the skills and **keeps** `~/.rosetta` — it holds your config and, if you
chose the `device` root, your artifacts. Use `./uninstall.sh --purge` to delete it too.
