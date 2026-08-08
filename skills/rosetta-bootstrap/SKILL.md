---
name: rosetta-bootstrap
description: "Create a new Rosetta knowledge base for any technology. Deep-assimilates a repository, synthesizes a two-layer knowledge base (JSON fact graph + 3-lens docs), produces a Rosetta guide mapping familiar patterns to the new framework, and scaffolds specialist agents. Trigger: /rosetta-bootstrap {tech} [{repo-url}]"
---

# Rosetta Bootstrap

> **Purpose**: Turn a near-zero-training technology into a first-class specialist domain by creating a Rosetta knowledge base and specialist agents.

## When to Use

- Onboarding a new or niche technology/framework where frontier models hallucinate
- Technologies like GPUI, Tauri v2, SpacetimeDB, Rig, MoonBit — anything with weak training coverage
- Also useful for well-known tech stacks (e.g., Kotlin/Compose) where specific idioms differ from what models default to

## Algorithm

```
INPUT: technology_name (e.g., "GPUI", "Tauri v2", "SpacetimeDB")
INPUT: repository_url (optional — auto-discover if omitted)
INPUT: base_framework (e.g., "React", "SwiftUI", "Rust" — what to map FROM)
INPUT: depth — quick | normal | deep

[0] RESOLVE STORAGE ROOTS  (deterministic — do this FIRST, never hardcode a path)
    Where artifacts land is configured, not assumed. Locate the resolver:
        RESOLVER = first existing of
          "$ROSETTA_CONFIG"                               # explicit override
          "<this skill's dir>/assets/rosetta_config.py"    # ships with the skill
          "${ROSETTA_HOME:-$HOME/.rosetta}/bin/rosetta_config.py"
    Then bind the three roots used below:
        KB_DIR     = $(python3 "$RESOLVER" path --class docs   --slug {tech_slug} --ensure)
        PROBES_MD  = $(python3 "$RESOLVER" path --class probes --slug {tech_slug} --ensure)
        RESEARCH_MD= $(python3 "$RESOLVER" path --class research --slug {tech_slug})
    Precedence: CLI flag > <project>/.rosetta/config.json > ~/.rosetta/config.json > "project".
    Root values: "project" (default) | "device" | any path (e.g. ~/Projects/brain). See CONFIG.md.
    IF the resolver is absent (partial install): fall back to the legacy project-local paths
    ({PROJECT}/.rosetta/docs/{tech_slug}/ etc.) and SAY SO in the [9] REPORT — never pretend a
    fallback was the configured destination.

[1] PARSE
    • Convert technology_name → kebab-case tech_slug (this is the directory name)
    • If no repo_url → web search for canonical repository
    • If still no repo → ERROR: "Provide --repo-url"

[2] ASSIMILATE  (delegates to rosetta-research — repo path)
    • INVOKE /rosetta-research "{technology_name}" --repo-url {repo_url} \
        --base-framework {base_framework} --depth {depth}
      (If your harness cannot invoke skills, execute skills/rosetta-research/SKILL.md
       inline as a procedure — it is written as an algorithm, not an API.)
    • rosetta-research runs its 3-tier ladder, NEVER requiring premium MCPs:
        Tier 1 — delegate to /assimilate (repo analysis) when available
        Tier 2 — jina/exa code-context + read_url when provisioned
        Tier 3 — built-in WebFetch/Read on the repo (always works)
      and returns the Normalized Rosetta Research Artifact (NRRA): mental-model
      registers, components, hard rules, API signatures, anti-patterns, traits,
      version pins, gaps, sources.
    • Storage is handled INSIDE rosetta-research (holocron if present, else $RESEARCH_MD —
      the resolved research path from [0]). Do NOT block on it.
    • CONSUME the NRRA as the assimilation context for steps [4]-[5].

[3] GAP RESEARCH (if needed)  (delegates to rosetta-research — topic path)
    • Inspect the NRRA's "Gaps & Unknowns" section plus any of the six axes
      (architecture, state management, routing, async, testing, anti-patterns)
      with confidence < MEDIUM or empty coverage.
    • FOR those thin topics: INVOKE /rosetta-research "{technology_name}" \
        --topics "{thin topic list}" --base-framework {base_framework} --depth {depth}
      rosetta-research uses Tier 1 (/deep-research-swarm or /deep-research if
      available) → Tier 2 (jina/exa search) → Tier 3 (WebSearch/WebFetch), same
      contract, and returns a delta NRRA.
    • MERGE the delta NRRA into the assimilation context (union of components/
      rules/anti-patterns; raise confidence where a second source corroborates).
    • Do NOT fabricate facts for still-thin axes — leave them in the merged NRRA's
      gaps[] so [8] VERIFY and a later /rosetta-upgrade run can close them.

[4] SYNTHESIZE MENTAL MODEL
    • Identify the 3-register equivalent for this technology
      (e.g., View ↔ ViewModel ↔ Repository for Kotlin, View body ↔ @Observable ↔ SwiftData for Swift)
    • Identify hard rules — structural constraints that are ALWAYS true
    • Identify false friends — patterns that LOOK correct but are SILENT TRAPS

[5] EXTRACT FACT GRAPH
    Create $KB_DIR/fact-graph.json with 6 sections:   ($KB_DIR from [0])

    v — VERSION: pkg name, version, commit hash, pinned date
    w — WIDGETS/COMPONENTS: catalog of building blocks
        { "WidgetName": { "t": "sl|sf", "i": "import path", "note": "what it replaces", "src": "concept doc" } }
        Types: sl (stateless), sf (stateful), or technology-specific (pw, macro, view, ga, protocol, etc.)
    r — RULES: structural constraints
        "rule_id": "r" (required) | "nr" (not required) | "REJECT" (cardinal sin)
    a — API SIGNATURES: exact method calls
        "operation_name": "exact code expression"
    x — ANTI-PATTERNS: wrong → right pairs
        "wrong_pattern": "correct_pattern"
    t — TRAITS/INTERFACES: type-level contracts
        "TraitName": "fn signature or description"

    The NRRA's sections map 1:1 onto v/w/r/a/x/t — this step is mostly mechanical.
    Only promote a fact to JSON when its NRRA confidence is ≥ MEDIUM; demote
    low-confidence findings to prose (concept docs) rather than asserting them.

    JSON vs Prose Decision Rule:
    - Named entity with fixed properties → JSON
    - Deterministic lookup / mapping → JSON
    - Rule with clear trigger + consequence → JSON
    - Requires sequential reasoning → prose markdown
    - Tradeoffs or judgment → prose markdown
    - Procedure with ordered steps → prose markdown

[6] CREATE PROGRESSIVE-DISCLOSURE DOCS
    Create $KB_DIR/:   ($KB_DIR from [0] — NOT a hardcoded .rosetta/docs path)

    README.md — Router + Bootstrap
      - Header: "Why this exists" — training-coverage gap
      - Version pins block (table)
      - Mental model (3-register or equivalent)
      - Hard rules (always true)
      - {base_framework} → {tech} Rosetta quick-card
      - False Friends quick-card (silent traps)
      - THE DOC MAP — full router table

    ROSETTA.md — Translation Stone
      - Strategy: "surf, don't fight" the model's base priors
      - Section per domain: State, Components, Events, Lists, Styling,
        Context, Effects, Async, Testing, etc.
      - Each section: Base/React table → {Tech} equivalent → "How to think"
      - FALSE FRIENDS section (where base reflex is a SILENT TRAP)
      - "Where scaffolding RUNS OUT" section (no analog — learn fresh)

    {concept}.md for each concept — 3-LENS FORMAT:
      (1) CAPABILITY LENS — what this covers, how it fits mental model
          (for planner agent)
      (2) PATTERNS & CODE LENS — verbatim snippets, exact APIs
          (for implementer agent)
      (3) REVIEW & GOTCHAS LENS — false friends, scan checklist
          (for reviewer agent)
      Each concept doc OPENS with its Rosetta row.

    Probe catalog — $PROBES_MD   ($PROBES_MD from [0])
      - Seed from the plugin's reference/universal-anti-patterns.md (28 universal),
        specialized to {tech}'s idioms, plus any tech-specific traps the NRRA found
      - Format: id, name, pattern, required_fact, required_correction, severity

[7] SCAFFOLD SPECIALIST AGENTS
    Create agents/{tech_slug}-{planner,implementer,reviewer}/agent.md

    FRONTMATTER: name, model, description, tools
    BOOTSTRAP (injected at session start):
      - JSON fact graph (verbatim from fact-graph.json)
      - Hard rules (prose, extracted from README.md)
      - Quick Reference (mental model + common mistakes)
      - Rosetta Quick-Card (8-10 most critical translations)
      - False Friends table
    ROLE-SPECIFIC WORKFLOWS:
      PLANNER: 8-phase planning workflow
      IMPLEMENTER: RED → GREEN → REFACTOR per AC, TDD
      REVIEWER: Mandatory gates + adversarial review checklist
    HANDOFF: Template for dispatching to next agent in triad

[8] VERIFY
    - fact-graph.json parses as valid JSON
    - All "w" entries have "t" + "i" minimum
    - All "r" entries are "r", "nr", or "REJECT"
    - All "x" entries are wrong → right pairs (both sides populated)
    - All "a" values are syntactically valid for target language
    - ≥ 3 concept docs exist in 3-lens format
    - Doc map in README.md references all existing docs
    - Agent bootstrap JSON matches fact-graph.json (no drift)
    - No stubs or TODOs in published artifacts
    - Every published fact traces to an NRRA source (no fabricated facts);
      still-thin axes remain in gaps[], not invented

[9] REPORT
    Display: Technology, Base Prior, Assimilated repo, Documents created, Agent triad created
    Next: /rosetta-validate {tech}, then /rosetta-upgrade
```

## Usage

```
/rosetta-bootstrap "GPUI" --repo-url https://github.com/zed-industries/zed --base-framework React
/rosetta-bootstrap "Tauri v2" --base-framework React
/rosetta-bootstrap "SpacetimeDB" --depth quick
/rosetta-bootstrap "Rig" --base-framework LangChain --depth normal
```

---

*rosetta-bootstrap — Plugin skill for agentic-rosetta*
*Strategy: Research (via rosetta-research) → synthesize → extract → scaffold → verify*
