---
name: rosetta-upgrade
description: "Upgrade a Rosetta KB. Three modes. EXPAND (--topics): research a specialty at the current framework pin and merge it into the graph. REFRESH (--url, no --topics): research the framework's current version, diff, bump, re-inject. BENCHMARK (default): challenge before/after, then expand remaining gaps via --topics. All graph writes go through write-graph.py --update. Gate on Accept / Improve / Roll Back."
---

# Rosetta Upgrade

> **Purpose**: Upgrade a specialist agent's Rosetta knowledge — **expand** coverage at the current framework pin (Mode C), **refresh** the pin when the framework moved (Mode B), or **measure** whether the KB helps and then expand remaining gaps (Mode A). Graph writes go through `write-graph.py --update`. Every mode gates on user approval.

## Mode Dispatch

`--topics` is the expand switch. `--url` without `--topics` is the version-refresh switch. Neither is the benchmark.

| Mode | Trigger | What it does |
|---|---|---|
| **C — Expand** | `--topics` present (even if `--url` is too) | Research those specialties at the **current** framework pin → diff → merge into the existing graph + 3-lens docs → re-inject. *Knowledge stays at the same version; the graph gets a named specialty.* Extra `--url` values are research sources, not a pin bump. |
| **B — Refresh** | `--url` present, no `--topics` | Research the framework's CURRENT state → diff vs the existing KB → bump the fact graph + docs to the latest version → re-inject. *Knowledge moves forward in time.* Scope a refresh with `--focus`, not `--topics`. |
| **A — Benchmark** | neither flag (default) | Before/after challenge test → compute delta → expand remaining gaps by invoking Mode C. *Same pin; graph gets richer from measured misses.* |

```
INVOCATION DISPATCH
  IF --topics is present
       AND its value is empty/whitespace → STOP (usage). Do not fall through to A or B.
       ELSE → run § Mode C — Expand Algorithm (C0–C6)
  ELIF args contain --url  → run § Mode B — Refresh Algorithm (R0–R8)
  ELSE                     → run § Mode A — Benchmark Algorithm (Phases 1–7)
```

All three share the same safety spine: **back up before mutating, show the change, gate on ACCEPT / IMPROVE / ROLL BACK.** All honor the NO-STUB invariant inherited from `rosetta-research` — every applied fact traces to a real source; nothing is fabricated to look productive. Do not hand-edit `fact-graph.json`.

## When to Use

**Mode C — Expand (`--topics`):**
- The current KB is missing a specialty (SwiftData on a Swift KB, Durable Objects on Cloudflare Workers, evals on Mastra)
- `/rosetta-validate` / ab-test / stress-test emitted gaps — this is the apply path those skills already name
- You know the hole; you do not want a coding challenge or a version bump

**Mode B — Refresh (`--url`, no `--topics`):**
- A new framework version shipped and the KB's pinned version is now stale
- You have a URL (release notes, breaking-changes page, migration guide, current docs) and want the KB + agents brought up to date
- You want the agent to actively avoid APIs that were correct at bootstrap time but are now deprecated

**Mode A — Benchmark (default):**
- After creating a Rosetta KB (via `rosetta-bootstrap`)
- Before declaring a specialist agent "Rosetta-ready"
- When evaluating whether a Rosetta KB actually improves agent output

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
    at the resolved probes path (`rosetta_config.py path --class probes --slug {tech}`),
    apply it too.
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
[7] Embed the generated knowledge block into each agent body:
    /rosetta-sync {tech_slug}        # or: bash scripts/kb-delivery/embed-graph.sh --sync {tech_slug}
    This hard-overwrites each consumer's body with a fresh dense per-role render of the
    (now expanded) fact-graph between the rosetta-kb:begin/end sentinels (REJECT rules first).
    Rosetta owns the distribution; agents know nothing. Generated, not hand-pasted.
[8] (Handled by embed-graph.sh: the block is inserted/replaced between the sentinels,
    or appended after the frontmatter if absent. Hand-authored prose is never touched.)
[9] Verify: `scripts/kb-delivery/embed-graph.sh --check <agent>` exits 0 for each triad agent.

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

═══════════════ PHASE 6 — EXPAND VIA MODE C (no in-place graph mutation) ═══════════════
Measurement stays in Phases 1–5. Mutation is Mode C — do not duplicate its write path here.

[14] Join remaining gaps into a --topics list. INVOKE § Mode C — Expand Algorithm (C0–C6)
        with tech={technology} and --topics "{gap1}, {gap2}, …"
     Mode C researches, diffs, writes through write-graph.py --update, syncs, and
     gates. needs_review[] is never auto-applied. Do NOT edit fact-graph.json by hand.

[15] OPTIONAL: re-run Phases 4–5. Remaining gaps → another Mode C --topics run, not a hand edit.

═══════════════ PHASE 7 — USER DECISION (benchmark embed) ═══════════════
Mode C already gated any graph write. This gate is the agent embed + .pre-rosetta.bak.

[16] SHOW the user:
     - The improvement delta
     - What gaps were found and (if Phase 6 ran) expanded
     - The modified files

[17] USER CHOOSES:

     ACCEPT  → Keep Rosetta + expanded fact graph
               - Remove backup: rm {agent_path}.pre-rosetta.bak
               - Log: "Rosetta accepted on {date} with {score}% improvement"
               - Write {kb}/APPLIED.log

     IMPROVE → User names remaining gaps → Mode C --topics on those → re-run Phase 4–7

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

```
rosetta-bootstrap → rosetta-validate → rosetta-upgrade
      ↑                    │                    │
      │                    │                    ├─ A measure, then C on remaining gaps
      │                    │                    ├─ C --topics  (specialty at current pin)
      │                    │                    └─ B --url     (framework moved)
      │                    ▼
      │         feed gaps as --topics ──► Mode C ──► write-graph.py --update
      └──── new rules, new anti-patterns, new docs
```

Validate / ab-test / stress-test **read** graphs. Mode C is the apply path they name. Each expand run feeds back into validate.

---

## Mode C — Expand (`--topics`) Algorithm

> **Purpose**: Add a specialty (or close named gaps) to an **existing** KB without moving the framework pin. Point at topics, research those directives, merge facts into the same graph, add or extend 3-lens concept docs, re-inject.

This mode does **not** run a coding challenge and does **not** run `provenance.py --check-stale`. Unchanged corpus is expected. Version-tagged deprecations and pin bumps belong in Mode B.

### Algorithm

```
INPUT: tech              (e.g., "swift" — derives KB + triad paths)
INPUT: --topics          (required: comma-separated specialties / gaps)
INPUT: --url             (optional extra docs/repo sources — NOT a version bump)
INPUT: --role            (planner | implementer | reviewer | all — default: all)
INPUT: --base-framework  (optional; default: read from existing ROSETTA.md)
INPUT: --depth           (quick | normal | deep — default: normal)
INPUT: --model           (REQUIRED — this session's model; never omit, never invent)

Empty / whitespace-only --topics → STOP. Print usage. Do not fall through to A or B.

═══════════════ C0 — RESOLVE & GUARD ═══════════════   [deterministic]
[0] RESOLVE + LOCATE THE KB (never hardcode the path)
        RESOLVER = first existing of
          "$ROSETTA_CONFIG"
          "<this skill's dir>/assets/rosetta_config.py"
          "${ROSETTA_HOME:-$HOME/.rosetta}/bin/rosetta_config.py"
    KB_DIR    = $(python3 "$RESOLVER" find --class docs --slug {tech})
    PROBES_MD = $(python3 "$RESOLVER" path --class probes --slug {tech})
    Missing graph → ERROR "No KB for {tech}. Run /rosetta-bootstrap {tech} first." STOP.
    Read fact-graph.json (capture v.pkg / v.version / v.kb_version), README, ROSETTA,
    concept docs, provenance.json (if present).
    Resolve the triad:
    - agents/{tech}-{planner,implementer,reviewer}/agent.md
    - .rosetta/sandbox/agents/{tech}-{role}/agent.md
    --role narrows the set (default: all existing).

═══════════════ C1 — RESEARCH THE SPECIALTY ═══════════════
[1] Build research sources:
       - repo/docs URLs from provenance.json source pins (reuse the existing KB's corpus)
       - plus any extra --url values the caller passed (additional docs, not a pin bump)
[2] INVOKE /rosetta-research "{tech}" --topics "{topics}"
           [--repo-url / --docs-url from those sources]
           --base-framework {base} --depth {depth} --model {session model}
    Engine --topics skips census and fans those directives. Fail closed: no serial crawl.
    status=BLOCKED or open ledger → STOP. Write nothing.

═══════════════ C2 — DIFF (pin must not move) ═══════════════
[3] Compare the NRRA's fact-graph seed against current fact-graph.json per section
    (v/w/r/a/x/t). Emit a CHANGESET — canonical shape:
    scripts/rosetta/test_write_graph.py (dict keyed by section, not a list).
    Demote LOW / unsourced to needs_review[]. Never auto-apply needs_review[].
    If the NRRA wants v.pkg or v.version changed → needs_review, do NOT put it in
    added/changed. Print: "framework pin moves only under --url refresh (Mode B)."
    If the CHANGESET's applied sections are empty → report "specialty already covered"
    (or only needs_review remains). Skip C3 write. Still show the report at C6.
    Do NOT invent facts to look productive.

═══════════════ C3 — WRITE ═══════════════   [deterministic]
[4] python3 "$PLUGIN/scripts/rosetta/write-graph.py" --update {tech} \
        --changeset {changeset.json} --ledger {out}/ledger.json --nrra {out}/nrra.md
    No --root: the writer resolves the docs class through rosetta_config.py, matching
    $KB_DIR. The script snapshots to .history/, applies ≥ MEDIUM, bumps kb_version
    from the section diff (add-only → minor). Do not mutate fact-graph.json in the skill.

═══════════════ C4 — DOCS + PROBES ═══════════════
[5] For each specialty in --topics:
       IF README doc map already has a row → extend that 3-lens concept doc
       ELSE write $KB_DIR/{specialty-slug}.md (3-lens: capability / patterns / gotchas)
            and add a README doc-map row
    Append specialty anti-patterns to $PROBES_MD so later /rosetta-validate can catch a miss.
    Do not write a Mode B old→new MIGRATION table unless the NRRA actually deprecated
    something (rare at a fixed pin). If it did, write-graph.py already aged it.

═══════════════ C5 — RE-INJECT ═══════════════
[6] FOR EACH role in scope: cp {agent_path} {agent_path}.pre-expand.bak
[7] /rosetta-sync {tech}
    (or: bash "$PLUGIN/scripts/kb-delivery/embed-graph.sh" --root "$PROJECT" --sync {tech})
[8] VERIFY: embed-graph.sh --check <agent> exits 0 for each in-scope role.

═══════════════ C6 — USER DECISION ═══════════════
[9] SHOW: --topics list, changeset counts + highlights, needs_review[],
    new/extended concept docs, snapshot path, pin unchanged (v.pkg / v.version).

[10] USER CHOOSES:

     ACCEPT   → Keep the expand.
                - rm each {agent_path}.pre-expand.bak
                - Append {kb}/APPLIED.log:
                  "Expanded --topics {topics} on {date}: +{n} facts (pin {v.version} unchanged)"

     IMPROVE  → User flags CHANGESET items or needs_review[] to research deeper.
                - Loop C1–C6 on the flagged subset (--topics "{item}")
                - Until the user is satisfied

     ROLL BACK → Undo the expand, keep the research.
                - Restore fact-graph.json from the .history/ snapshot write-graph took
                - Restore each agent from {agent_path}.pre-expand.bak, then rm the .bak
                - Revert touched docs (README / concept docs / probes) via git checkout
                - KEEP the .history/ snapshot and the NRRA (valid research — may retry later)
```

### Decision guidance (Mode C)

| Situation | Default action |
|---|---|
| Changeset is clean, sourced, pin unchanged | **Accept** |
| High-value adds, but `needs_review[]` is non-empty | **Improve** — research the unconfirmed items |
| Changeset empty (specialty already covered) | Report no-op; nothing to accept |
| NRRA wants `v.version` / `v.pkg` changed | Leave in `needs_review[]`; tell the user to run Mode B `--url` |
| Research `BLOCKED` | **Roll Back** — never apply unsourced facts |

---

## Mode B — Refresh (`--url`, no `--topics`) Algorithm

> **Purpose**: Bring a KB — and the agents that embed it — up to the latest framework version. Point at a URL, research what's current, diff against the existing KB, update the fact graph + docs, and re-inject into the agent triad so the agents are aware of the new hotness (and the now-deprecated old ways).

This mode does **not** run a coding challenge. It is a *knowledge refresh*: research → diff → apply → re-inject → validate → gate.

### The Versioning Model (how old info is held & indexed)

The `fact-graph.json` is the **single source of truth**; a **generated** dense per-role render of it is embedded in each agent's body (via `embed-graph.sh`), so the graph's facts reach ~100% of sessions. The JSON itself may be arbitrarily rich — delivery is the generated render, sized per-role by `generate_excerpt.py`, and leanness is a *quality* goal for the render (front-loaded REJECT rules + role emphasis), **not a hard ceiling** on the graph. The graph therefore carries **no per-fact history**. Three artifacts hold and index version history instead, each with one job:

| Artifact | Job | Lifetime |
|---|---|---|
| `$KB_DIR/fact-graph.json` | **Latest only** — always reflects the current framework version. Source of truth for the generated render. | overwritten each refresh |
| `$KB_DIR/.history/{date}-v{kb_version}.json` | **Hold old info** — a deterministic snapshot of the prior fact graph, taken before each refresh. Auditable + enables rollback. | append-only archive |
| `$KB_DIR/MIGRATION.md` | **Index the changes** — one section per refresh: what's new / changed / deprecated (old→new table) + source URLs. Loaded on demand. | append-only log |

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
INPUT: --model           (REQUIRED — this session's model; never omit, never invent)
INPUT: --force           (optional; run the refresh even when the staleness probe reports *current*)

═══════════════ R0 — PREFLIGHT (skip fanout when the source has not moved) ═══════════════   [deterministic]
[0] IF $KB_DIR/provenance.json exists:
       python3 "$PLUGIN/scripts/rosetta/provenance.py" --check-stale {tech} --root "$PROJECT"
       exit 0  → sources are current. STOP unless --force is set.
                 Report "KB already current (corpus unchanged); pass --force to re-research."
       exit 10 → sources moved; continue to R1. Name the moved sources in the run log.
       exit 1  → undeterminable (legacy-unknown / ls-remote failed). Continue to R1
                 (do not treat as current).
    IF no provenance.json (legacy graph): continue to R1.

═══════════════ R1 — RESOLVE & GUARD ═══════════════
[1] RESOLVE + LOCATE THE KB (never hardcode the path)
        RESOLVER = first existing of
          "$ROSETTA_CONFIG"                               # explicit override
          "<this skill's dir>/assets/rosetta_config.py"    # ships with the skill
          "${ROSETTA_HOME:-$HOME/.rosetta}/bin/rosetta_config.py"
    KB_DIR    = $(python3 "$RESOLVER" find --class docs   --slug {tech})   # searches
                configured root, then project, then device — a KB written before the
                config existed is still found. Empty/exit!=0 → the ERROR below.
    PROBES_MD = $(python3 "$RESOLVER" path --class probes --slug {tech})
    IF missing → ERROR "No KB for {tech}. Run /rosetta-bootstrap {tech} first." STOP.
[2] Read current state:
    - fact-graph.json  → capture v (version, pinned, kb_version)
      NOTE: KBs bootstrapped before Refresh mode may not carry kb_version in `v` — it lives in the
      README header ("KB version: 1.0.0"). If v.kb_version is absent, read it from the README line;
      default to "1.0.0". This first refresh ADDS kb_version (and supersedes) to the `v` section.
    - README.md, ROSETTA.md, concept docs, $PROBES_MD
[3] Resolve the agent triad. Check BOTH layouts (same discovery Mode A uses for agent_path):
    - agents/{tech}-{planner,implementer,reviewer}/agent.md
    - .rosetta/sandbox/agents/{tech}-{role}/agent.md
    Record which roles actually exist; --role narrows the set (default: all existing).

═══════════════ R2 — SNAPSHOT (hold old info) ═══════════════   [deterministic]
[4] cp $KB_DIR/fact-graph.json
       → $KB_DIR/.history/{date}-v{old_kb_version}.json
[5] Append to $KB_DIR/.history/manifest.json:
       { "date": "{ISO}", "old_version": "{old_v}", "old_kb_version": "{old_kb}",
         "source_urls": [{--url values}], "status": "in-progress" }
    (This snapshot is the rollback target and the auditable record of "what it used to be.")

═══════════════ R3 — RESEARCH CURRENT (download the info) ═══════════════
[6] Invoke the research skill (engine only — fail closed, no serial crawl):
       /rosetta-research "{tech}" --repo-url {primary --url}
           --base-framework {base}
           --focus "release notes, breaking changes, deprecations, migration since {current_version}"
           --topics {additional --url values or --focus areas}
           --depth {depth}
           --model {session model}
    The --topics here is an *engine* argument (skip census). Caller --topics on
    /rosetta-upgrade already dispatched to Mode C; Mode B never sees it.
    status=BLOCKED or open ledger → STOP. Write nothing.
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
      # dict keyed by section (NOT a list). write-graph.py reads
      # (cs.get("added") or {}).get(section). Canonical shape:
      # scripts/rosetta/test_write_graph.py
      added: {
        "r": { "new-rule": { "verdict": "r", "rule": "…", "source": "https://…", "confidence": "HIGH" } }
      },
      changed: {
        "a": { "open": { "value": "open()", "source": "README.md:1", "confidence": "MEDIUM" } }
      },
      deprecated: {
        "x": { "old-pair": "replacement" }
      },
      needs_review: [ { note, why } ]   # unconfirmed / LOW — surfaced, never auto-applied
    }
    The mandated `source` field is stripped out of the graph value and written to
    fact-sources.json keyed "section:key". Do NOT put source/confidence inside fact-graph.json.

    RULES:
    - Demote LOW-confidence findings to needs_review (mirror bootstrap's JSON-vs-prose rule).
    - Every entry in added/changed/deprecated MUST cite a source. No source → needs_review.
    - If the CHANGESET is empty (framework already current) → report "no changes", write a no-op
      MIGRATION entry, mark the snapshot manifest "no-op", and SKIP to R8 (nothing to gate but the report).
      Do NOT invent deprecations to look productive.

═══════════════ R5 — APPLY TO KB (update the knowledge graph; lean-current) ═══════════════   [deterministic writes]
[9]  Build the changeset from the NRRA (added/changed/deprecated, ≥ MEDIUM only).
[10] python3 "$PLUGIN/scripts/rosetta/write-graph.py" --update {tech} \
        --changeset {changeset.json} --ledger {out}/ledger.json --nrra {out}/nrra.md
     No --root: the docs class is resolved from config, matching $KB_DIR. Pinning it to
     "$PROJECT" updated a different graph than the one the skill read whenever roots.docs
     was not "project".
     The script snapshots to .history/, applies the changeset, ages deprecations into
     MIGRATION.md, bumps kb_version. Do not mutate fact-graph.json in the skill.
[11] For each deprecation (and each changed API) → add a VERSION-TAGGED x entry:
       "x": { "{old} (pre-v{N})": "{new} (v{N}+) — {one-line why}" }
[12] Apply DEPRECATION AGING: retire pre-(N-1) version-tagged x transitions OUT of the graph;
     they survive only in MIGRATION.md. (Keeps the always-on graph lean over time.)
[13] Update concept docs (3-lens):
       - PATTERNS & CODE lens → new API/snippets
       - REVIEW & GOTCHAS lens → add the deprecation as a false friend + scan-checklist item
       - Create a new concept doc (3-lens) for a genuinely new concept; add its row to the README doc map
[14] Update probes: add the new version-transition anti-patterns to $PROBES_MD
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
[18] Re-embed the generated knowledge block from the now-bumped fact-graph (same path as Mode A):
         scripts/kb-delivery/embed-graph.sh agents/{tech_slug}-{role}
       The dense per-role render (incl. version-tagged transitions in x) is regenerated between
       the rosetta-kb sentinels. Generated, not hand-pasted.
[19] VERIFY: `scripts/kb-delivery/embed-graph.sh --check <agent>` exits 0 for each role
       (the embedded block is byte-identical to the generator output for this graph+role).

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
# Mode C — Expand (--topics): specialty / gaps at the current pin
/rosetta-upgrade swift --topics "SwiftData, CloudKit"
/rosetta-upgrade cloudflare-workers --topics "Durable Objects" \
    --url https://developers.cloudflare.com/durable-objects/
/rosetta-upgrade mastra --topics "evals, processors"

# Mode B — Refresh (--url, no --topics): bring the KB + triad up to the latest framework version
/rosetta-upgrade electron --url https://www.electronjs.org/docs/latest/breaking-changes
/rosetta-upgrade electron --url https://releases.electronjs.org/release --role implementer
/rosetta-upgrade tauri --url https://v2.tauri.app/start/migrate/ --base-framework React --depth deep

# Mode A — Benchmark (default): measure impact, then Mode C on remaining gaps
/rosetta-upgrade agents/kotlin-implementer/agent.md --kb .rosetta/docs/kotlin/ --challenge universal-crud
```

Run from the consumer project that owns `agents/`, or pass `--root` / `$ROSETTA_PROJECT`. The LLM discovers agent and KB paths from that project. `--topics` selects Expand; `--url` without `--topics` selects Refresh; neither selects Benchmark.

---

*rosetta-upgrade — Plugin skill for agentic-rosetta*
*Strategy: Expand (specialty at the current pin), Refresh (research the latest, bump the pin), or Benchmark (measure, then expand). Hold old info in `.history/` + `MIGRATION.md`. Always gate on the user's decision; never fabricate a fact.*
