---
name: rosetta-ab-test
description: "Measure real-world impact of Rosetta KB injection by running the same test suite before/after and computing improvement delta"
---

# Rosetta A/B Upgrade Test

> **Purpose**: Measure the real-world impact of applying Rosetta to a specialist agent. Run the SAME test suite before and after Rosetta injection, compute the improvement delta, and let the user accept, refine, or roll back.

## When to Use

- After creating a Rosetta KB for a technology
- Before declaring a specialist agent "Rosetta-ready"
- When evaluating whether a Rosetta KB actually improves agent output
- As a gating step in the Rosetta pipeline (after scaffold + fill, before deploy)

## Algorithm

```
INPUT: agent_path (e.g., brain/agents/kotlin-implementer/agent.md)
INPUT: rosetta_kb (e.g., brain/.rosetta/docs/kotlin/)
INPUT: challenge_id (optional — default: "universal-crud")

═══════════════════════════════════════════════════════════
PHASE 1 — BACKUP
═══════════════════════════════════════════════════════════
[1] BACKUP the agent file
    cp {agent_path} {agent_path}.pre-rosetta.bak
    Record: agent file size, line count, existing bootstrap status

═══════════════════════════════════════════════════════════
PHASE 2 — BASELINE (without Rosetta)
═══════════════════════════════════════════════════════════
[2] LOAD the current agent (no Rosetta bootstrap)
    Read: {agent_path}
    Confirm: no Rosetta fact graph in bootstrap

[3] RUN TEST SUITE against the bare agent

    TEST SUITE COMPONENTS:

    A. UNIVERSAL CHALLENGE (from rosetta-stress-test)
       - Dispatch agent with challenge spec
       - Agent implements using ONLY its existing knowledge (no Rosetta KB)
       - Capture: files written, lines of code

    B. ANTI-PATTERN SCAN (28 universal probes)
       - Scan the output for every universal anti-pattern
       - Record: which anti-patterns found, file:line

    C. ACCEPTANCE CRITERIA CHECK
       - For each AC in the challenge: PASS / FAIL / PARTIAL
       - Record: how many ACs satisfied

    D. STATIC VALIDATION (if rosetta-validate available)
       - Run schema check on any existing KB artifacts
       - Run hallucination probes

[4] RECORD BASELINE METRICS
    {
      "phase": "baseline",
      "agent": "{agent_path}",
      "has_rosetta": false,
      "challenge": "{challenge_id}",
      "metrics": {
        "anti_patterns_found": 11,
        "anti_patterns_details": [...],
        "acceptance_criteria_passed": 4,
        "acceptance_criteria_total": 10,
        "stubs_found": 2,
        "files_written": 10,
        "lines_of_code": 280
      }
    }

═══════════════════════════════════════════════════════════
PHASE 3 — APPLY ROSETTA
═══════════════════════════════════════════════════════════
[5] INJECT ROSETTA BOOTSTRAP into the agent

    Read the Rosetta KB:
    - {rosetta_kb}/fact-graph.json
    - {rosetta_kb}/README.md (hard rules, mental model, false friends)
    - {rosetta_kb}/ROSETTA.md (Rosetta quick-card)

    Construct the bootstrap section:
    ```
    ## Facts (JSON Fact Graph)
    ```json
    {compact fact graph — verbatim from fact-graph.json}
    ```

    ## Hard Rules
    {extracted from README.md hard rules section}

    ## Quick Reference
    - Mental model: {from README.md}
    - Doc map: {from README.md doc map}

    ### ⚠️ False Friends
    {from README.md false friends table}
    ```

[6] WRITE MODIFIED AGENT
    Insert the bootstrap between the frontmatter (---) and the existing content.
    The agent now has Rosetta facts injected at session start.

    Verify: agent file has been modified
    Verify: JSON fact graph in bootstrap matches canonical fact-graph.json

═══════════════════════════════════════════════════════════
PHASE 4 — AFTER (with Rosetta)
═══════════════════════════════════════════════════════════
[7] RUN THE SAME TEST SUITE

    Repeat EXACTLY the same tests from PHASE 2:
    - Same challenge spec
    - Same acceptance criteria
    - Same anti-pattern scan
    - Same static validation

    The only difference: the agent now has the Rosetta bootstrap.

[8] RECORD AFTER METRICS
    {
      "phase": "after",
      "agent": "{agent_path}",
      "has_rosetta": true,
      "challenge": "{challenge_id}",
      "metrics": {
        "anti_patterns_found": 2,
        "anti_patterns_details": [...],
        "acceptance_criteria_passed": 8,
        "acceptance_criteria_total": 10,
        "stubs_found": 0,
        "files_written": 13,
        "lines_of_code": 410
      }
    }

═══════════════════════════════════════════════════════════
PHASE 5 — COMPUTE DELTA
═══════════════════════════════════════════════════════════
[9] DIFF THE METRICS

    Calculate improvement per dimension:

    anti_pattern_delta = baseline.found - after.found
    anti_pattern_prev_pct = anti_pattern_delta / baseline.found * 100

    ac_improvement = after.passed - baseline.passed
    ac_delta_pct = (after.passed - baseline.passed) / baseline.total * 100

    stub_delta = baseline.stubs - after.stubs

    overall_improvement = weighted average of all dimensions

[10] RENDER COMPARISON REPORT

    ```
    ╔══════════════════════════════════════════════════════════════╗
    ║  ROSETTA A/B UPGRADE REPORT                                  ║
    ╠══════════════════════════════════════════════════════════════╣
    ║  Agent:     kotlin-implementer                               ║
    ║  KB:        brain/.rosetta/docs/kotlin/                               ║
    ║  Challenge: universal-crud                                   ║
    ╠══════════════════════════════════════════════════════════════╣
    ║                                                              ║
    ║  Metric              Before     After      Delta             ║
    ║  ─────────────────   ──────     ─────     ─────              ║
    ║  Anti-patterns       11         2         -9 (81.8%) ✅      ║
    ║  ACs passed          4/10       8/10      +4 (40.0%) ✅      ║
    ║  Stubs found         2          0         -2        ✅       ║
    ║  Files written       10         13        +3                ║
    ║  Lines of code       280        410       +130              ║
    ║                                                              ║
    ║  OVERALL IMPROVEMENT:  +62%                                   ║
    ║  GRADE: B+ (good improvement, some gaps remain)             ║
    ║                                                              ║
    ║  Remaining gaps (after Rosetta):                             ║
    ║    - Flow.collect in suspend fun (no KB rule)               ║
    ║    - Filtering in view body (KB rule exists but weak)       ║
    ╠══════════════════════════════════════════════════════════════╣
    ║                                                              ║
    ║  What would you like to do?                                  ║
    ║                                                              ║
    ║  [1] ACCEPT  — Keep Rosetta, finalize agent                 ║
    ║  [2] IMPROVE — Refine KB based on gaps, then re-test       ║
    ║  [3] ROLL BACK — Restore backup, remove Rosetta            ║
    ║                                                              ║
    ╚══════════════════════════════════════════════════════════════╝
    ```

═══════════════════════════════════════════════════════════
PHASE 6 — USER DECISION
═══════════════════════════════════════════════════════════

[11] ACCEPT (Keep Rosetta)
     - Remove backup file: rm {agent_path}.pre-rosetta.bak
     - Record decision: "Rosetta accepted on {date} with {score}% improvement"
     - Write decision log to {rosetta_kb}/APPLIED.log
     - Commit the modified agent (if in git repo)

[12] IMPROVE (Refine KB → Re-test)
     - User specifies which gaps to fix
     - Feed them to `/rosetta-upgrade --topics "{gaps}"` so `write-graph.py --update` is the writer
     - Do not edit fact-graph.json from this skill (evaluation family reads, never writes)
     - GOTO PHASE 4 after the upgrade run
     - Loop until user is satisfied

[13] ROLL BACK (Restore backup)
     - Restore agent from backup: cp {agent_path}.pre-rosetta.bak {agent_path}
     - Remove backup: rm {agent_path}.pre-rosetta.bak
     - Record decision: "Rosetta rolled back on {date}"
     - Agent is restored to pre-Rosetta state
     - Rosetta KB is NOT deleted (may be retried later)

═══════════════════════════════════════════════════════════
```

---

## Decision Matrix

Based on the delta, the tool recommends a default action:

| Delta Score | Grade | Default Action | Rationale |
|---|---|---|---|
| ≥ 80% | A | **Accept** | Rosetta provides clear, measurable improvement |
| 50-79% | B | **Improve** | Good improvement but significant gaps remain |
| 20-49% | C | **Improve** | Modest improvement; KB needs work |
| < 20% | D | **Roll Back** | Rosetta provides negligible benefit; reconsider approach |
| Negative | F | **Roll Back** | Rosetta made things WORSE; investigate immediately |

The user ALWAYS has final say, regardless of the recommendation.

---

## Test Suite Components

### A. Universal Challenge (from rosetta-stress-test)

Same challenges as the stress test:
- `universal-crud` — offline-first task manager
- `realtime-search` — debounced search with async results
- `auth-flow` — login form with validation + token storage
- `sync-conflict` — offline-first with conflict resolution

### B. Anti-Pattern Scan (28 probes)

The 28 universal anti-patterns from `rosetta-stress-test`, grouped by category:
A. State Management (1-4)
B. Lifecycle / Memory (5-7)
C. Reactive Data (8-11)
D. Navigation / Routing (12-14)
E. Styling / Accessibility (15-18)
F. Dependency Injection (19-21)
G. Async / Concurrency (22-24)
H. Completeness (25-28)

### C. Acceptance Criteria Check

Each challenge has 6-10 acceptance criteria. For each AC:
- **PASS**: Implementation satisfies the criterion
- **FAIL**: Implementation does not satisfy the criterion
- **PARTIAL**: Criterion partially satisfied (e.g., happy path only)

### D. Static Validation

If `rosetta-validate` tooling is available:
- Schema compliance check
- Agent drift detection
- Cross-reference integrity
- Hallucination probe coverage

---

## Manual Workflow (No Tooling Required)

If the Python tooling isn't available, the A/B test can be run manually:

1. **Backup**: `cp agent.md agent.md.pre-rosetta.bak`
2. **Baseline**: Run the challenge without Rosetta, record metrics
3. **Apply**: Manually insert the Rosetta bootstrap into agent.md (see templates below)
4. **After**: Run the same challenge with Rosetta, record metrics
5. **Delta**: Compare metrics
6. **Decide**: Accept, Improve, or Roll Back

### Bootstrap Injection Template

The bootstrap section inserted between frontmatter and content:

```markdown
## Facts (JSON Fact Graph)
```json
{paste fact-graph.json verbatim}
```

## Hard Rules
{paste hard rules from README.md}

## Quick Reference
- Mental model: {from README.md}
- Doc map: {from README.md}

### ⚠️ False Friends
{paste false friends table from README.md}
```
```

---

## Related

- `rosetta-stress-test` — the challenge library and anti-pattern probes used here
- `agent-plan-rosetta` — creates the Rosetta KB that this skill tests
- `rosetta-validate` — deterministic schema validation (runs as part of the test suite)
- `harness-adapters/` — how to inject Rosetta into specific harnesses

---

*rosetta-ab-test — Created: 2026-06-01*
*Strategy: Measure before/after behavioral delta, gate on user decision to accept, improve, or roll back.*
