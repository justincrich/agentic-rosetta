# Why Rosetta Works

## The Problem

Frontier LLMs are trained on billions of tokens of React, Python, and Node.js code. But when asked to write GPUI, Tauri v2, or SpacetimeDB, they hallucinate — they map unfamiliar APIs onto familiar patterns from their training data.

A model asked to write Kotlin Compose will write React hooks (`useState`, `useEffect`, `onClick`). A model asked to write SwiftUI will write CSS (`className`, `backgroundColor`). These aren't bugs — they're statistical inevitability from a model that has 1000x more React than Kotlin in its training corpus.

You cannot retrain the model at inference time. But you CAN inject facts.

## The Solution: Two-Layer Knowledge Injection

Rosetta injects knowledge into the agent session at two levels:

### Layer 1: Compact JSON Fact Graph (~800 tokens, always-on)

The `fact-graph.json` contains deterministic, lookup-only facts in 6 sections:

| Section | What | Example |
|---------|------|---------|
| `v` | Version pins | `{"pkg": "androidx-compose", "pinned": "2026-06"}` |
| `w` | Widgets/components | `{"LazyColumn": {"t":"sl", "i":"androidx.compose.foundation.lazy.LazyColumn"}}` |
| `r` | Rules (r/nr/REJECT) | `"mutableStateOf in ViewModel": "REJECT"` |
| `a` | API signatures | `"hiltViewModel": "val vm: MyViewModel = hiltViewModel()"` |
| `x` | Anti-patterns | `"useState in Compose": "remember / rememberSaveable + mutableStateOf"` |
| `t` | Traits/interfaces | `"@Composable": "Marks function as UI component. Must be pure."` |

This is injected into the agent's bootstrap. It costs ~800 tokens — less than 1% of a 200K context window — and prevents 100% of structural anti-patterns (proven in challenge testing).

### Layer 2: Progressive Disclosure Markdown Docs (loaded on demand)

Concept docs in 3-lens format provide the reasoning that JSON can't capture:

- **Capability lens** — what this covers, how it fits the mental model (for planner)
- **Patterns & Code lens** — verbatim, compile-verified snippets (for implementer)
- **Review & Gotchas lens** — false friends, deterministic scan checklist (for reviewer)

Each concept doc is ~3-5K tokens and is loaded only when the task requires it.

## The Three-Register Mental Model

Every UI framework has the same fundamental data flow, even if the primitives differ:

```
           Events flow UP
    ┌──────────────────────────┐
    │     UI REGISTER           │  ← Kotlin: @Composable, Swift: View body, GPUI: div()
    │  (renders state,          │
    │   emits user events)      │
    └────────────┬─────────────┘
                 │ State flows DOWN
    ┌────────────▼─────────────┐
    │     STATE REGISTER        │  ← Kotlin: ViewModel+StateFlow, Swift: @Observable, GPUI: Entity+Context
    │  (single source of truth, │
    │   survives lifecycle)     │
    └────────────┬─────────────┘
                 │
    ┌────────────▼─────────────┐
    │     DATA REGISTER         │  ← Kotlin: Repository+Room, Swift: SwiftData, GPUI: App+async
    │  (persistence, network,   │
    │   business logic)         │
    └──────────────────────────┘
```

Mapping this model to a new technology is the first step in bootstrapping a Rosetta KB. Once you identify the three registers, you know what concepts to document and what anti-patterns to flag.

## Why JSON + Prose, Not Just One

| Question | JSON | Prose |
|----------|------|-------|
| Named entity with fixed properties? | Yes | |
| Mapping from one thing to another? | Yes | |
| Rule with clear trigger + consequence? | Yes | |
| Requires sequential reasoning? | | Yes |
| Involves tradeoffs or judgment? | | Yes |
| The "why" is as important as "what"? | | Yes |
| Procedure with ordered steps? | | Yes |
| Autocomplete/lookup would suffice? | Yes | |

**Rule of thumb**: If a fact can be represented as a key→value lookup without losing information, it belongs in JSON. If removing explanatory prose would make the fact unusable, it stays in markdown.

## Research Evidence

The Rosetta system has been stress-tested on two technology stacks:

| Technology | Structural Prevention | Subtle Bug Prevention | Overall Score |
|---|---|---|---|
| Kotlin Android | 100% (13/13 anti-patterns blocked) | 2/3 prevented | 81% |
| Swift / SwiftUI | 100% (8/8 anti-patterns blocked) | 2/5 prevented | 62% |

The 100% structural prevention rate across both technologies confirms that the fact-graph injection works for deterministic lookups. The lower subtle bug scores indicate where reasoning (agent triad) is still required.

## The Dynamic Research Loop

The `rosetta-upgrade` skill implements a continuous improvement cycle:

1. Run tests on an agent WITHOUT Rosetta → capture anti-patterns
2. Apply Rosetta → run tests again → measure delta
3. For each remaining gap → expand the fact graph with new rules
4. Re-validate with the expanded graph
5. The expanded graph feeds back into future runs

This means the Rosetta KB improves with every use. The fact graph grows organically from discovered gaps rather than being hand-crafted in advance.

## What Rosetta Replaces

| Without Rosetta | With Rosetta |
|---|---|
| Agent writes `useState` in Kotlin Compose | Agent writes `remember { mutableStateOf() }` |
| Agent uses `className`/`CSS` in SwiftUI | Agent uses `Modifier`/theme tokens |
| Agent navigates between loading/success/error | Agent uses `sealed class State` + `when` |
| Agent blocks main thread with DB queries | Agent uses `suspend` DAOs + `Dispatchers.IO` |
| You spend 30 minutes per PR fixing anti-patterns | The KB prevents them before code is written |

## When NOT to Use Rosetta

- If the technology is already well-represented in training data (React, Python, basic Node.js)
- If you only need trivial one-liners (the KB overhead isn't worth it)
- If the agent has no context window for injection (unlikely with today's models)

---

*agentic-rosetta — Theory of operation*
