# Agentic Rosetta

> Stop LLMs from hallucinating your framework. Ships the **machinery**, not the knowledge.

## The Problem

Frontier LLMs know React inside out. But ask them to write Kotlin Compose, SwiftUI, GPUI, Tauri v2, or SpacetimeDB, and they hallucinate — mapping unfamiliar APIs onto familiar React patterns. Hardcoded colors, wrong state primitives, blocking calls on the UI thread — these aren't bugs, they're statistical inevitability for a model trained on 1000× more React than your stack.

You can't retrain the model at inference time. But you **can** inject facts.

## What You Get

Agentic Rosetta injects a **two-layer knowledge base** into the agent's session:

1. A compact JSON **fact graph** (~800 tokens, always-on) that prevents structural anti-patterns
2. Progressive-disclosure **concept docs** (loaded on demand) for reasoning and tradeoffs

It ships **seven skills** and a **Mastra fanout engine** — and **zero pre-installed language knowledge**. You generate the knowledge base for *your* technology with `rosetta-bootstrap`.

| Skill | What it does |
|---|---|
| `rosetta-research` | **The only reader of the world.** Detects harness + model, shells out to `engine/` (`[A0]`–`[A6]`). Real ACP workers, closed ledger, NRRA. Fail closed — never a serial crawl. |
| `rosetta-bootstrap` | Thin wrapper: research → `write-graph.py --create` → `rosetta-sync` |
| `rosetta-upgrade` | Measure (benchmark) or refresh (`--url`). Graph writes go through `write-graph.py --update`. |
| `rosetta-validate` | Stress-test a KB (reads graphs, never writes) |
| `rosetta-sync` | Distribute graphs into agent bodies (`registry.py` + `embed-graph.sh`) |
| `rosetta-ab-test` / `rosetta-stress-test` | Evaluation family |

This repo is the **one true source**. Brain is a consumer of the graphs, not of the machinery.

## Install

### Claude Code (native plugin — recommended)

```
/plugin marketplace add justincrich/agentic-rosetta
/plugin install agentic-rosetta@agentic-rosetta
```

Skills appear namespaced: `/agentic-rosetta:rosetta-bootstrap`, etc.

### Grok Build (native plugin)

```
grok plugin install /path/to/agentic-rosetta --trust
```

Skills appear as `/rosetta:research`, `/rosetta:bootstrap`, `/rosetta:upgrade`,
`/rosetta:validate`, `/rosetta:sync`, `/rosetta:ab-test`, `/rosetta:stress-test`.
(The plugin is named `rosetta`; the Grok pack uses short skill ids so the
namespace is not doubled. Other harnesses keep `/rosetta-research`.)

### This machine (skillshare — the local path)

```bash
./sync.sh               # extras + skillshare install + hash-identical copies on claude/codex/opencode
```

`install.sh` is for **external** checkouts that are not skillshare-managed. It refuses to clobber a newer target unless `--force`, and runs `bun install` in `engine/`. Grok is not a drop-in target — use `grok plugin install` above.

### Any harness (script)

```bash
./install.sh            # install into every detected harness (except Grok drop-ins)
./install.sh claude     # claude | codex | opencode | pi | droid
```

The script drops `skills/<name>/SKILL.md` into the harness's skills directory. It installs **machinery only** — no language knowledge bases, no edits to your project.

## Quick Start

```bash
# In your agent session — create a KB for your stack, then validate and upgrade:
/rosetta-bootstrap "GPUI" --repo-url https://github.com/zed-industries/zed --base-framework React
/rosetta-validate gpui
/rosetta-upgrade agents/gpui-implementer/agent.md          # Mode A — benchmark + expand

# Later, when the framework ships a new version, refresh the KB from a URL:
/rosetta-upgrade gpui --url https://zed.dev/docs/gpui      # Mode B — research latest, diff, bump, re-inject
```

(Use the namespaced `/agentic-rosetta:rosetta-*` form if you installed the native Claude Code plugin.)

## How It Works

```
rosetta-bootstrap          rosetta-validate          rosetta-upgrade
(create KB from scratch)   (stress-test existing)    (before/after agent test)
        │                          │                          │
        ▼                          ▼                          ▼
  research the tech ───┐     Pick challenge              Backup agent
  (rosetta-research)   │     Implement spec              Run tests WITHOUT KB
  synthesize patterns  │     Adversarial review          Apply Rosetta bootstrap
  extract fact graph   │     Grade fact graph            Run tests WITH KB
  create 3-lens docs   │     Emit refactors              Compute improvement delta
  scaffold agents      │            │                          │
        │              │            └──────────────┬───────────┘
        │   degrade gracefully:                    │
        │   /assimilate /deep-research →           ▼
        │   jina/exa MCP → WebSearch       Expand fact graph with
        │   (never required)               discovered gaps
        └──────────────────────────────►  (accept / improve / roll back)
```

### Keeping a KB current — `rosetta-upgrade --url` (Refresh mode)

Bootstrapping pins a KB to a framework version. When the framework moves, point the same skill at a URL to bring the KB **and** the agents that embed it up to date:

```
/rosetta-upgrade electron --url https://www.electronjs.org/docs/latest/breaking-changes

  snapshot old graph → research latest (rosetta-research) → diff → bump fact graph + docs
  → re-inject into the planner/implementer/reviewer triad → re-validate → accept / improve / roll back
```

**How version history is held** — so the agent has context on what changed without bloating the always-on graph:

| Artifact | Role |
|---|---|
| `.rosetta/docs/{tech}/fact-graph.json` | **Latest only** — stays lean (~800 tokens), always injected |
| `.rosetta/docs/{tech}/.history/{date}-v{n}.json` | **Snapshot** of the prior graph before each refresh (audit + rollback) |
| `.rosetta/docs/{tech}/MIGRATION.md` | **Per-version changelog** — what's new / changed / deprecated (old→new), loaded on demand |

The agent learns the "new hotness" because every old→new transition is folded into the fact graph's `x` (anti-pattern) section as a **version-tagged false friend** — e.g. `"electron-trpc (pre-v43)" → "native ipc (v43+)"` — so it actively avoids the deprecated API. Old transitions age out of the lean graph into `MIGRATION.md`.

## Research MCPs (optional)

`rosetta-research` shells out to the engine. Workers inherit this session's harness and model (`grok agent stdio`, `opencode acp`, or `claude-code-acp`). Search upgrades to jina / exa / firecrawl when those MCPs are present; otherwise the harness-native floor. If the engine or an ACP entrypoint is missing, the skill **STOPs** — it will not improvise a serial crawl.

## Where things get stored

By default everything stays in the project you run a skill in — install and change nothing, and
nothing moves. If you'd rather keep research notes (or probe catalogs, or knowledge bases) centrally,
each class is independently configurable:

```jsonc
// ~/.rosetta/config.json   (created by install.sh; a project can override it)
{ "version": 1, "roots": { "default": "project", "research": "~/Projects/my-brain" } }
```

Precedence: CLI flag > project config > device config > built-in `project`. Root values are
`project`, `device` (`~/.rosetta`), or any path. See **[CONFIG.md](CONFIG.md)** for the schema,
the read-side search order, `doctor` (find + move stranded artifacts), and one real caveat about
pointing `docs` away from the repo that owns your agents.

```bash
python3 ~/.rosetta/bin/rosetta_config.py show --format plain   # what are my roots?
python3 ~/.rosetta/bin/rosetta_config.py doctor                # anything stranded?
```

## Uninstall

```bash
./uninstall.sh           # remove from every detected harness
./uninstall.sh claude    # or a specific one
```

Removes only the four Rosetta skills. Your generated `.rosetta/` knowledge bases are left untouched. (If you installed the native plugin: `/plugin uninstall agentic-rosetta`.)

## Theory

See [WHY.md](./WHY.md) for the theory of operation: two-layer knowledge injection, the three-register mental model, and the JSON-vs-prose decision rule.

---

*agentic-rosetta — Skills-as-code plugin for agentic IDEs (Claude Code, Codex, OpenCode, Grok, pi).*
