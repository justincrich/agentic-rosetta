---
name: rosetta-upgrade
description: "Before/after Rosetta agent upgrade test. Run tests without Rosetta, apply Rosetta bootstrap, run same tests, compute delta, expand fact graph with discovered gaps, then ask user: Accept / Improve / Roll Back."
---

# Rosetta Upgrade

> **Purpose**: Measure the real-world impact of applying Rosetta to a specialist agent, dynamically expand the fact graph to close discovered gaps, and gate the upgrade on user approval.

## When to Use

- After creating a Rosetta KB for a technology (via `rosetta-bootstrap`)
- Before declaring a specialist agent "Rosetta-ready"
- When evaluating whether a Rosetta KB actually improves agent output
- When you discover new anti-patterns and want to expand the fact graph

## Algorithm

```
INPUT: agent_path       (e.g., agents/kotlin-implementer/agent.md)
INPUT: rosetta_kb       (e.g., .rosetta/docs/kotlin/)
INPUT: challenge_id     (default: "universal-crud")

═══════════════ PHASE 1 — BACKUP ═══════════════
cp {agent_path} {agent_path}.pre-rosetta.bak

═══════════════ PHASE 2 — BASELINE (without Rosetta) ═══════════════
[1] Load the current agent (no Rosetta bootstrap)
[2] Select challenge from rosetta-validate skill's challenge library
[3] Dispatch agent with challenge spec (agent uses ONLY its existing knowledge)
[4] Scan output using the 28 UNIVERSAL ANTI-PATTERNS (carried inline in the
    rosetta-validate skill; canonical copy at the plugin's
    reference/universal-anti-patterns.md). If a per-tech probe catalog exists
    at .rosetta/probes/{tech}.md, apply it too.
[5] Record baseline metrics:
    - anti_patterns_found: N
    - acceptance_criteria_passed: X/Y
    - stubs_found: N
    - files_written: N
    - lines_of_code: N

═══════════════ PHASE 3 — APPLY ROSETTA ═══════════════
[6] Read Rosetta KB:
    - {kb}/fact-graph.json
    - {kb}/README.md (extract hard rules, mental model, false friends)
[7] Construct bootstrap section with:
    - JSON fact graph (verbatim)
    - Hard rules (prose)
    - Quick reference (mental model + doc map)
    - False friends table
[8] Insert bootstrap between agent frontmatter (---) and existing content
[9] Verify: agent JSON matches canonical fact-graph.json

═══════════════ PHASE 4 — AFTER (with Rosetta) ═══════════════
[10] Run SAME challenge + SAME anti-pattern scan from Phase 2
[11] Record after metrics (same format)

═══════════════ PHASE 5 — COMPUTE DELTA ═══════════════
[12] Compute per-dimension improvement:
     anti_pattern_delta = baseline - after
     ac_improvement = after_passed - baseline_passed
     stub_delta = baseline_stubs - after_stubs
     overall_improvement = weighted average

[13] Render comparison report:
     ╔══════════════════════════════════════════════════════════════╗
     ║  ROSETTA UPGRADE REPORT                                      ║
     ╠══════════════════════════════════════════════════════════════╣
     ║  Agent:     kotlin-implementer                               ║
     ║  KB:        .rosetta/docs/kotlin/                            ║
     ║  Challenge: universal-crud                                   ║
     ╠══════════════════════════════════════════════════════════════╣
     ║                                                              ║
     ║  Metric              Before     After      Delta             ║
     ║  ─────────────────   ──────     ─────     ─────              ║
     ║  Anti-patterns       11         2         -9 (81.8%) ✅      ║
     ║  ACs passed          4/10       8/10      +4 (40.0%) ✅      ║
     ║  Stubs found         2          0         -2        ✅       ║
     ║                                                              ║
     ║  OVERALL IMPROVEMENT:  +62%                                   ║
     ║  GRADE: B+                                                   ║
     ║                                                              ║
     ║  Remaining gaps (after Rosetta):                             ║
     ║    1. Flow.collect in suspend fun (no KB rule)               ║
     ║    2. Filtering in view body (KB rule exists but weak)       ║
     ╚══════════════════════════════════════════════════════════════╝

═══════════════ PHASE 6 — EXPAND FACT GRAPH ═══════════════
For EACH remaining gap discovered in Phase 5:

[14] DETERMINE the correct pattern
     Find the correct idiomatic pattern for the gap by RESEARCHING the
     framework docs / canonical source. Prefer delegating to the
     `rosetta-research` skill so research degrades gracefully (brain research
     skills → jina/exa MCP → built-in WebSearch/WebFetch):
        /rosetta-research "{technology}" --topics "{gap description}"
     (If your harness cannot invoke skills, execute skills/rosetta-research/SKILL.md
     inline as a procedure — it is written as an algorithm, not an API.)
     Never invent the pattern: every fix below must trace to a real source.

[15] WRITE what's missing into the fact graph:
     a) If it's a missing RULE:
        - Add to fact-graph.json → "r" section
        - Add to the "Hard Rules" section of the agent bootstrap
        - Append to .rosetta/docs/{tech}/README.md hard rules list

     b) If it's a missing ANTI-PATTERN:
        - Add to fact-graph.json → "x" section (wrong → right pair)
        - Add to the "False Friends" table of the agent bootstrap
        - Append to .rosetta/docs/{tech}/ROSETTA.md

     c) If a pattern is MISSING ENTIRELY (no existing concept doc):
        - Create a new concept doc in 3-lens format at
          .rosetta/docs/{tech}/{concept-name}.md
        - Add a "w" entry to fact-graph.json if it's a widget/component
        - Add to the doc map in agent bootstrap

     d) If it's a probe that SHOULD EXIST for this technology:
        - Add to .rosetta/probes/{tech}.md
        - This ensures future validate runs catch this gap

[16] Example: discovered gap "Flow.collect inside suspend fun hangs forever"
     → RULE:   "Flow.collect inside suspend fun for one-shot read" → "REJECT"
     → ANTI:   "Flow.collect inside suspend fun for one-shot read" → "Flow.first() or Flow.firstOrNull()"
     → DOC:    Add section to .rosetta/docs/kotlin/coroutines-flow.md:
               "⚠️ Infinite Flow: never call .collect on a Room Flow inside
                a suspend fun. Use .first() for single emission."

[17] UPDATE the agent bootstrap with expanded fact graph
     Re-read: fact-graph.json (now expanded)
     Re-inject: the new JSON + new rules + new false friends into agent.md

[18] OPTIONAL: Re-run Phase 4-5 to verify gaps are now closed
     If gaps remain → loop back to Phase 6 with new gaps.

═══════════════ PHASE 7 — USER DECISION ═══════════════

[19] SHOW the user:
     - The improvement delta
     - What gaps were found and expanded
     - The modified files (fact-graph.json, agent.md, concept docs)

[20] USER CHOOSES:

     ACCEPT  → Keep Rosetta + expanded fact graph
               - Remove backup: rm {agent_path}.pre-rosetta.bak
               - Log: "Rosetta accepted on {date} with {score}% improvement"
               - Write {kb}/APPLIED.log

     IMPROVE → Refine specific gaps, then re-run Phase 4-7
               - User specifies which gaps to prioritize
               - Fact graph may need additional expansion
               - Loop until user is satisfied

     ROLL BACK → Restore backup, remove Rosetta
               - cp {agent_path}.pre-rosetta.bak {agent_path}
               - rm {agent_path}.pre-rosetta.bak
               - Keep Rosetta KB on disk (may retry later)
               - Do NOT delete expanded fact graph (it's valid research)

═══════════════
```

## Decision Matrix

| Delta Score | Grade | Default Action |
|---|---|---|
| ≥ 80% | A | **Accept** — Rosetta provides clear, measurable improvement |
| 50-79% | B | **Improve** — Good improvement but gaps remain. Expand fact graph, re-test. |
| 20-49% | C | **Improve** — Modest improvement. KB needs significant expansion. |
| < 20% | D | **Roll Back** — Negligible benefit. Reconsider approach or KB quality. |
| Negative | F | **Roll Back** — Rosetta made things WORSE. Investigate immediately. |

## The Dynamic Research Loop

This is what makes the plugin work for ANY technology:

```
rosetta-bootstrap → rosetta-validate → rosetta-upgrade
      ↑                                        │
      │                                        ▼
      │              ┌──────── expansion ──────┤
      │              │                          │
      │              ▼                          │
      └──── new rules, new anti-patterns, new docs
```

Each `upgrade` run discovers gaps → expands the fact graph → the expanded graph feeds back into `validate` (which now catches those gaps) → and `bootstrap` uses the richer graph for new technologies.

## Usage

```
/rosetta-upgrade agents/kotlin-implementer/agent.md --kb .rosetta/docs/kotlin/ --challenge universal-crud
```

Or in the plugin context, the LLM discovers the agent path and KB path from the project structure.

---

*rosetta-upgrade — Plugin skill for agentic-rosetta*
*Strategy: Measure before/after, expand fact graph with discovered gaps, gate on user decision.*
