# Agentic Rosetta

> Stop LLMs from hallucinating your framework. Ships the **machinery**, not the knowledge.

## The Problem

Frontier LLMs know React inside out. But ask them to write Kotlin Compose, SwiftUI, GPUI, Tauri v2, or SpacetimeDB, and they hallucinate — mapping unfamiliar APIs onto familiar React patterns. Hardcoded colors, wrong state primitives, blocking calls on the UI thread — these aren't bugs, they're statistical inevitability for a model trained on 1000× more React than your stack.

You can't retrain the model at inference time. But you **can** inject facts.

## What You Get

Agentic Rosetta injects a **two-layer knowledge base** into the agent's session:

1. A compact JSON **fact graph** (~800 tokens, always-on) that prevents structural anti-patterns
2. Progressive-disclosure **concept docs** (loaded on demand) for reasoning and tradeoffs

It ships **four skills** — and **zero pre-installed language knowledge**. You generate the knowledge base for *your* technology with `rosetta-bootstrap`.

| Skill | What it does |
|---|---|
| `rosetta-bootstrap` | Create a new Rosetta KB for any technology (research → synthesize → fact-graph → 3-lens docs → scaffold agents) |
| `rosetta-research` | Research an unknown tech with **graceful degradation** — delegates to `/assimilate` & `/deep-research` when present, uses `jina`/`exa` MCPs when provisioned, falls back to built-in web search. MCPs optional, never required. |
| `rosetta-validate` | Stress-test a KB by challenging an agent to build with it, then adversarially review against 28 universal anti-patterns |
| `rosetta-upgrade` | Before/after agent test → expand the fact graph with discovered gaps → you decide accept / improve / roll back |

## Install

### Claude Code (native plugin — recommended)

```
/plugin marketplace add justincrich/agentic-rosetta
/plugin install agentic-rosetta@agentic-rosetta
```

Skills appear namespaced: `/agentic-rosetta:rosetta-bootstrap`, etc.

### Any harness (script)

```bash
./install.sh            # install into every detected harness
./install.sh claude     # or a specific one: claude | codex | opencode | pi | droid
```

The script drops `skills/<name>/SKILL.md` into the harness's skills directory. It installs **machinery only** — no language knowledge bases, no edits to your project.

## Quick Start

```bash
# In your agent session — create a KB for your stack, then validate and upgrade:
/rosetta-bootstrap "GPUI" --repo-url https://github.com/zed-industries/zed --base-framework React
/rosetta-validate gpui
/rosetta-upgrade agents/gpui-implementer/agent.md
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

## Research MCPs (optional)

`rosetta-research` is better with [Jina](https://jina.ai) and [Exa](https://exa.ai) MCP servers and a holocron store, but **requires none of them**. With nothing configured, it runs entirely on the built-in `WebSearch` / `WebFetch` / `Read` tools. Every fact it returns traces to a real source — it never fabricates or stubs; unknowns are recorded as gaps.

## Uninstall

```bash
./uninstall.sh           # remove from every detected harness
./uninstall.sh claude    # or a specific one
```

Removes only the four Rosetta skills. Your generated `.rosetta/` knowledge bases are left untouched. (If you installed the native plugin: `/plugin uninstall agentic-rosetta`.)

## Theory

See [WHY.md](./WHY.md) for the theory of operation: two-layer knowledge injection, the three-register mental model, and the JSON-vs-prose decision rule.

---

*agentic-rosetta — Skills-as-code plugin for agentic IDEs (Claude Code, Codex, OpenCode, pi).*
