---
name: rosetta-upgrade
description: "Upgrade a Rosetta specialist agent — two modes. BENCHMARK (default): run a challenge without Rosetta, apply the KB, re-run, compute the delta, expand the fact graph with discovered gaps. REFRESH (--url): research a framework's CURRENT state from a URL, diff it against the existing KB, update the fact graph + docs to the latest version, and re-inject into the agent triad so it's aware of the new hotness. Both gate on Accept / Improve / Roll Back."
---

# Rosetta Upgrade

> **Purpose**: Upgrade a specialist agent's Rosetta knowledge — either by **measuring** the impact of applying the KB and expanding it to close gaps (Mode A), or by **refreshing** the KB to the latest framework version from a URL and re-injecting it into the agent triad (Mode B). Both gate the change on user approval.

## Mode Dispatch

This skill has **two modes**, selected by whether `--url` is present:

| Mode | Trigger | What it does |
|---|---|---|
| **A — Benchmark** | no `--url` (default) | Before/after challenge test → compute delta → expand fact graph with discovered gaps. *Knowledge stays at the same framework version; the graph gets richer.* |
| **B — Refresh** | `--url <docs/release URL>` present | Research the framework's CURRENT state → diff vs the existing KB → bump the fact graph + docs to the latest version → re-inject into the agent triad. *Knowledge moves forward in time; the agent learns the new hotness and the now-deprecated old ways.* |

```
INVOCATION DISPATCH
  IF args contain --url  → run § Mode B — Refresh Algorithm (R1–R8)
  ELSE                   → run § Mode A — Benchmark Algorithm (Phases 1–7)
```

Both modes share the same safety spine: **back up before mutating, show the change, gate on ACCEPT / IMPROVE / ROLL BACK.** Both honor the NO-STUB invariant inherited from `rosetta-research` — every applied fact traces to a real source; nothing is fabricated to look productive.

## When to Use

**Mode A — Benchmark:**
- After creating a Rosetta KB for a technology (via `rosetta-bootstrap`)
- Before declaring a specialist agent "Rosetta-ready"
- When evaluating whether a Rosetta KB actually improves agent output
- When you discover new anti-patterns and want to expand the fact graph

**Mode B — Refresh:**
- A new framework version shipped and the KB's pinned version is now stale
- You have a URL (release notes, breaking-changes page, migration guide, current docs) and want the KB + agents brought up to date
- You want the agent to actively avoid APIs that were correct at bootstrap time but are now deprecated

## Mode A — Benchmark Algorithm

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

---

## Mode B — Refresh (`--url`) Algorithm

> **Purpose**: Bring a KB — and the agents that embed it — up to the latest framework version. Point at a URL, research what's current, diff against the existing KB, update the fact graph + docs, and re-inject into the agent triad so the agents are aware of the new hotness (and the now-deprecated old ways).

This mode does **not** run a coding challenge. It is a *knowledge refresh*: research → diff → apply → re-inject → validate → gate.

### The Versioning Model (how old info is held & indexed)

The `fact-graph.json` is injected **verbatim into every agent's working memory**, so it must stay lean (~800 tokens). It therefore carries **no per-fact history**. Three artifacts hold and index version history instead, each with one job:

| Artifact | Job | Lifetime |
|---|---|---|
| `.rosetta/docs/{tech}/fact-graph.json` | **Latest only** — always reflects the current framework version. Stays lean + always-on. | overwritten each refresh |
| `.rosetta/docs/{tech}/.history/{date}-v{kb_version}.json` | **Hold old info** — a deterministic snapshot of the prior fact graph, taken before each refresh. Auditable + enables rollback. | append-only archive |
| `.rosetta/docs/{tech}/MIGRATION.md` | **Index the changes** — one section per refresh: what's new / changed / deprecated (old→new table) + source URLs. Loaded on demand. | append-only log |

**How the agent becomes "aware of the new hotness" without bloating injection.** The most decision-relevant kind of version awareness for code generation is *"the thing I'd reflexively reach for is now deprecated."* That is exactly a **wrong → right pair**, which already has a home: the fact graph's `x` (anti-pattern) section. Every old→new transition becomes a **version-tagged `x` entry**:

```json
"x": { "electron-trpc (pre-v43)": "native ipc invoke/handle (v43+) — electron-trpc unmaintained" }
```

The agent now actively *avoids* the old API through the same channel it already trusts, and only reaches `MIGRATION.md` when it needs the deep "why."

**Deprecation aging (keeps the graph lean across many refreshes).** Only the most recent transition (current major) stays in the always-on `x`. Older transitions (`pre-v40`, …) are retired from the graph and live in `MIGRATION.md` only. The `v` section gains a `supersedes` pointer and a bumped `kb_version`.

### Algorithm

```
INPUT: tech              (e.g., "electron" — derives KB + triad paths)
INPUT: --url             (1+ URLs: release notes / breaking-changes / migration guide / current docs)
INPUT: --role            (planner | implementer | reviewer | all — default: all)
INPUT: --base-framework  (optional; default: read from existing ROSETTA.md)
INPUT: --depth           (quick | normal | deep — default: normal; passed through to rosetta-research)

═══════════════ R1 — RESOLVE & GUARD ═══════════════
[1] Locate the KB: .rosetta/docs/{tech}/
    IF missing → ERROR "No KB for {tech}. Run /rosetta-bootstrap {tech} first." STOP.
[2] Read current state:
    - fact-graph.json  → capture v (version, pinned, kb_version)
      NOTE: KBs bootstrapped before Refresh mode may not carry kb_version in `v` — it lives in the
      README header ("KB version: 1.0.0"). If v.kb_version is absent, read it from the README line;
      default to "1.0.0". This first refresh ADDS kb_version (and supersedes) to the `v` section.
    - README.md, ROSETTA.md, concept docs, .rosetta/probes/{tech}.md
[3] Resolve the agent triad. Check BOTH layouts (same discovery Mode A uses for agent_path):
    - agents/{tech}-{planner,implementer,reviewer}/agent.md
    - .rosetta/sandbox/agents/{tech}-{role}/agent.md
    Record which roles actually exist; --role narrows the set (default: all existing).

═══════════════ R2 — SNAPSHOT (hold old info) ═══════════════   [deterministic]
[4] cp .rosetta/docs/{tech}/fact-graph.json
       → .rosetta/docs/{tech}/.history/{date}-v{old_kb_version}.json
[5] Append to .rosetta/docs/{tech}/.history/manifest.json:
       { "date": "{ISO}", "old_version": "{old_v}", "old_kb_version": "{old_kb}",
         "source_urls": [{--url values}], "status": "in-progress" }
    (This snapshot is the rollback target and the auditable record of "what it used to be.")

═══════════════ R3 — RESEARCH CURRENT (download the info) ═══════════════
[6] Invoke the research skill so acquisition degrades gracefully (brain skills → jina/exa → WebFetch):
       /rosetta-research "{tech}" --repo-url {primary --url}
           --base-framework {base}
           --focus "release notes, breaking changes, deprecations, migration since {current_version}"
           --topics {any additional --url values or focus areas}
           --depth {depth}
    (If your harness cannot invoke skills, execute skills/rosetta-research/SKILL.md inline as a
     procedure — it is written as an algorithm, not an API.)
[7] CONSUME the returned NRRA (Normalized Rosetta Research Artifact): version pins, w/r/a/x/t,
    gaps[], sources[]. The NRRA's sections are already 1:1 with the fact graph's v/w/r/a/x/t.
    NO-STUB INVARIANT (cardinal): every fact must trace to a real fetched source. Unknowns stay
    in the NRRA's gaps[] and flow to needs_review[] below — they are NEVER applied as facts.

═══════════════ R4 — DIFF / ANALYZE ═══════════════   [probabilistic → structured-output boundary]
[8] Compare the fresh NRRA's fact-graph seed against the current fact-graph.json, per section
    (v/w/r/a/x/t). Emit a CHANGESET (validate the shape before applying — this is the deterministic
    boundary between agentic analysis and deterministic writes):

    CHANGESET {
      version_delta: { old: {v}, new: {v} },
      added:    [ { section, key, value, source } ],          # in fresh, absent in current; ≥ MEDIUM confidence ONLY
      changed:  [ { section, key, old, new, source } ],       # same key, new value (API moved, rule flipped, version bumped)
      deprecated:[ { key, old, new, source } ],               # current facts the research marks superseded/removed
      needs_review: [ { note, why } ]                         # unconfirmed / LOW confidence — surfaced, never auto-applied
    }

    RULES:
    - Demote LOW-confidence findings to needs_review (mirror bootstrap's JSON-vs-prose rule).
    - Every entry in added/changed/deprecated MUST cite a source. No source → needs_review.
    - If the CHANGESET is empty (framework already current) → report "no changes", write a no-op
      MIGRATION entry, mark the snapshot manifest "no-op", and SKIP to R8 (nothing to gate but the report).
      Do NOT invent deprecations to look productive.

═══════════════ R5 — APPLY TO KB (update the knowledge graph; lean-current) ═══════════════   [deterministic writes]
[9]  Bump v in fact-graph.json:
       version → new, pinned → {date}, kb_version → incremented, supersedes → "{old_kb_version}"
[10] Apply added / changed / removed to w/r/a/x/t so fact-graph.json reflects the LATEST ONLY.
[11] For each deprecation (and each changed API) → add a VERSION-TAGGED x entry:
       "x": { "{old} (pre-v{N})": "{new} (v{N}+) — {one-line why}" }
[12] Apply DEPRECATION AGING: retire pre-(N-1) version-tagged x transitions OUT of the graph;
     they survive only in MIGRATION.md. (Keeps the always-on graph lean over time.)
[13] Update concept docs (3-lens):
       - PATTERNS & CODE lens → new API/snippets
       - REVIEW & GOTCHAS lens → add the deprecation as a false friend + scan-checklist item
       - Create a new concept doc (3-lens) for a genuinely new concept; add its row to the README doc map
[14] Update probes: add the new version-transition anti-patterns to .rosetta/probes/{tech}.md
     (so future rosetta-validate runs catch a regression to the old API).
[15] Write MIGRATION.md — PREPEND a section:
       ## {old_version} → {new_version} ({date})
       **New:** …    **Changed:** …
       **Deprecated:**
       | old (pre-v{N}) | new (v{N}+) | why |
       **Sources:** {fetched URLs}
[16] Update README.md: bump the version line (e.g. "**Electron**: 43.0.0 · **KB version**: 1.1.0 ·
     **Last updated**: {date}") and append new entries to its Sources list.

═══════════════ R6 — RE-INJECT INTO THE TRIAD ═══════════════
[17] FOR EACH role in scope:
       cp {agent_path} {agent_path}.pre-refresh.bak
[18] Re-inject the updated bootstrap into each agent.md — same construction as Mode A [6]–[9]:
       - JSON fact graph (verbatim, now bumped)
       - Hard rules (prose)
       - Quick reference (mental model + doc map)
       - False friends table (now INCLUDING the version-tagged transitions)
[19] VERIFY: the JSON embedded in each agent == the canonical fact-graph.json.

═══════════════ R7 — VALIDATE (reuse the existing gate) ═══════════════
[20] /rosetta-validate {tech}
     → confirm the refreshed KB still passes the 28 universal anti-patterns AND the new
       version-transition probes from [14].
     (OPTIONAL: run Mode A on the implementer for a before/after focused on the new probes.)

═══════════════ R8 — USER DECISION ═══════════════
[21] SHOW the user:
       - version_delta (old → new)
       - CHANGESET summary (added / changed / deprecated counts + highlights) + needs_review[]
       - MIGRATION.md preview
       - rosetta-validate result
       - modified files + the snapshot path (.history/{date}-v{old}.json)

[22] USER CHOOSES:

     ACCEPT   → Keep the refresh.
                - rm each {agent_path}.pre-refresh.bak
                - Mark the .history/manifest.json entry "accepted"; set new_version
                - Append {kb}/APPLIED.log:
                  "Refreshed {old_v}→{new_v} on {date}: +{n} facts, {m} deprecations"
                - Keep .history/ snapshot + MIGRATION.md

     IMPROVE  → User flags specific CHANGESET items or needs_review[] entries to research deeper.
                - Loop R3–R7 on the flagged subset (e.g. /rosetta-research --topics "{item}")
                - Until the user is satisfied

     ROLL BACK → Undo the refresh, keep the research.
                - Restore fact-graph.json from .history/{date}-v{old_kb_version}.json
                - Restore each agent from {agent_path}.pre-refresh.bak, then rm the .bak
                - Revert touched docs (README / ROSETTA / MIGRATION / concept docs / probes) via
                  `git checkout -- {paths}` (they are tracked)
                - KEEP the .history/ snapshot and the fresh NRRA (valid research — may retry later)
                - Mark the .history/manifest.json entry "rolled-back"

═══════════════
```

### Decision guidance (Mode B)

Unlike Mode A's delta-score grades, Mode B is gated on the **shape of the changeset**, not a benchmark number:

| Situation | Default action |
|---|---|
| Changeset is clean, all entries sourced, validate passes | **Accept** |
| Some high-value changes confirmed, but `needs_review[]` is non-empty | **Improve** — research the unconfirmed items, then re-gate |
| Validate regressed (new graph fails a previously-passing probe) | **Improve** or **Roll Back** — the research or the apply was wrong |
| Changeset empty (already current) | Report no-op; nothing to accept |
| Research came back `BLOCKED` (no reachable source) | **Roll Back** — never apply unsourced facts |

## Usage

```
# Mode A — Benchmark (default): measure impact + expand the fact graph
/rosetta-upgrade agents/kotlin-implementer/agent.md --kb .rosetta/docs/kotlin/ --challenge universal-crud

# Mode B — Refresh (--url): bring the KB + triad up to the latest framework version
/rosetta-upgrade electron --url https://www.electronjs.org/docs/latest/breaking-changes
/rosetta-upgrade electron --url https://releases.electronjs.org/release --role implementer
/rosetta-upgrade tauri --url https://v2.tauri.app/start/migrate/ --base-framework React --depth deep
```

Or in the plugin context, the LLM discovers the agent path and KB path from the project structure. The presence of `--url` is what flips the skill from Benchmark (A) into Refresh (B).

---

*rosetta-upgrade — Plugin skill for agentic-rosetta*
*Strategy: Benchmark (measure before/after, expand the fact graph) or Refresh (research the latest, diff, bump the graph, re-inject). Hold old info in `.history/` + `MIGRATION.md`; keep the always-on graph lean and current. Always gate on the user's decision; never fabricate a fact.*
