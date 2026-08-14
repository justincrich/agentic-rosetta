---
name: rosetta-validate
description: "Adversarially stress-test any Rosetta knowledge base. Challenge an agent to implement a universal coding exercise using ONLY the KB, then run an adversarial reviewer against 28 universal anti-patterns to grade the fact graph and emit refactor recommendations. Trigger: /rosetta-validate {tech} [--challenge {id}]"
---

# Rosetta Validate

> **Purpose**: Dynamically validate any Rosetta knowledge base by challenging an agent to implement a universal coding exercise using ONLY the KB, then adversarially review the result to grade the fact graph.
>
> **Why prompt-based?** Languages and frameworks change. Hardcoded probes (like `mutableStateOf in ViewModel`) only work for one technology. Prompt-based review asks the LLM to reason about the output, which generalizes across Swift, Kotlin, GPUI, Tauri, or anything new.

## When to Use

- After creating or updating a Rosetta knowledge base
- Before declaring a technology "Rosetta-ready"
- When you suspect a Rosetta KB has drifted from current best practices
- As part of CI for knowledge base maintenance

## Algorithm

```
INPUT: technology_slug (e.g., "swift", "kotlin", "gpui", "tauri")
INPUT: challenge_id (optional — default: "universal-crud")

[1] DISCOVER ROSETTA (via the registry — never hardcode the path)
    Resolve the KB location from the single index (Rosetta is the brain; agents/skills look it up,
    they don't assume a folder):
        python3 "$PLUGIN/scripts/rosetta/registry.py" --root "$PROJECT" graph-for {tech_or_agent}
        python3 "$PLUGIN/scripts/rosetta/registry.py" --root "$PROJECT" validate
    The registry reports the SLUG; it does not fix the filesystem root.
    Where that dir physically lives is a CONFIGURED root, not an assumption —
    resolve it (searches configured root, then project, then device, so a KB written
    before any config existed is still found):
        RESOLVER = first existing of
          "$ROSETTA_CONFIG"                               # explicit override
          "<this skill's dir>/assets/rosetta_config.py"    # ships with the skill
          "${ROSETTA_HOME:-$HOME/.rosetta}/bin/rosetta_config.py"
        KB_DIR = $(python3 "$RESOLVER" find --class docs --slug <slug>)
        (resolver absent → fall back to ./.rosetta/docs/<slug>/ and say so)
    Then read:
        $KB_DIR/README.md
        $KB_DIR/fact-graph.json
        $KB_DIR/ROSETTA.md
        all concept docs in $KB_DIR/*.md
    Count: rules, anti-patterns, widgets, traits
    Also assert the agent bodies are in sync:
        bash "$PLUGIN/scripts/kb-delivery/embed-graph.sh" --root "$PROJECT" --check --all
    (or `/rosetta-sync --check`). If any consumer's block drifted from the graph,
    the KB under test is not what agents actually received.

[2] SELECT CHALLENGE
    Choose from UNIVERSAL CHALLENGE LIBRARY below
    The challenge must be implementable in the target technology
    If not, fall back to "universal-crud" (always works)

[3] CHALLENGE PROMPT ( implementer mode )
    "You are an implementer agent. You have access ONLY to the
     Rosetta KB at $KB_DIR (registry-reported slug, resolved root). You may NOT use any
     external knowledge, StackOverflow, or training priors.

     Implement this spec: {challenge_spec}
     Write the minimum code to satisfy all acceptance criteria.
     Follow every rule in the fact graph. Avoid every anti-pattern.
     After writing each file, briefly cite which KB doc you used."

[4] SELF-REVIEW PROMPT ( reviewer mode )
    "You are an adversarial reviewer. Review the code written above.
    For each acceptance criterion, rate: PASS / FAIL / PARTIAL.
    Then scan for these UNIVERSAL ANTI-PATTERNS (adapted to {tech}):
    {universal_anti_patterns}

    For each anti-pattern found, cite the file and line.
    For each prevented anti-pattern, cite the KB rule that prevented it.

    Finally, grade the fact graph:
    - Coverage: % of anti-patterns that had KB rules
    - False negatives: anti-patterns with no KB rule
    - False positives: KB rules that don't apply to real code
    - Stub count: placeholder/TODO/empty implementations"

[5] COMPUTE GRADE
    Structural coverage = prevented / (prevented + missed)
    Subtle bug coverage = subtle_prevented / (subtle_prevented + subtle_missed)
    Stub discipline = 1.0 if zero stubs, else 0.0
    Overall grade = weighted average:
        0.4 * structural + 0.4 * subtle + 0.2 * stub

[6] EMIT REFACTOR RECOMMENDATIONS
    For each missed anti-pattern:
      - Suggest new fact-graph entry (rule or anti-pattern)
      - Suggest new doc section with example
    For each false positive:
      - Flag for removal or clarification
    Output: structured report + proposed changeset (added/changed/deprecated).
    Do NOT write fact-graph.json. Validation is an evaluation skill — it reads graphs, never writes.
    Feed the proposed changeset to `/rosetta-upgrade --topics "{gap}"` so the write goes through
    `write-graph.py --update`.
```

---

## Universal Challenge Library

These challenges are designed to stress-test the SAME set of concepts across any technology:

### Challenge 1: "universal-crud" (default)

**Scope**: Single-screen app that loads a list of items from local storage, displays them, allows search/filter, supports pull-to-refresh, and navigates to a detail screen.

**What it tests**:
- State management (local vs shared vs persistent)
- Reactive data queries (database → UI)
- Navigation / routing
- Async operations (loading, error, retry)
- Conditional UI (loading / empty / error / success states)
- Forms / input validation
- Accessibility / theming

**Technology-specific variants**:
- **Mobile**: offline-first with local DB
- **Web**: client-side routing + API calls
- **Desktop**: same as mobile but with window chrome
- **Rust/GPUI**: entity/view/state separation

### Challenge 2: "realtime-search"

**Scope**: Search bar with debounced query, real-time results, highlighting, empty states, error handling.

**What it tests**:
- Debouncing / throttling
- Reactive streams / state updates
- Cancel previous searches on new input
- Loading spinner during search
- Error state + retry

### Challenge 3: "auth-flow"

**Scope**: Login form → validation → API call → token storage → conditional routing → logout.

**What it tests**:
- Form validation (sync + async)
- Secure storage / keychain
- Conditional routing (auth guards)
- Error states (network, credentials)
- Session management

### Challenge 4: "sync-conflict"

**Scope**: Offline-first app that creates items offline, then syncs on reconnect. Tests conflict resolution when server has newer data.

**What it tests**:
- Local-first architecture
- Background sync
- Conflict resolution strategies
- Optimistic UI updates
- Retry with backoff

---

## Universal Anti-Pattern Prompt Template

When reviewing ANY technology, check for these universal anti-patterns adapted to the target framework:

```
UNIVERSAL ANTI-PATTERNS (adapt per technology):

A. STATE MANAGEMENT
   1. Using wrong state primitive (e.g., local state for shared data)
   2. Directly mutating state from child instead of event-up pattern
   3. Missing state initialization (undefined/null until first load)
   4. State split across multiple sources of truth

B. LIFECYCLE / MEMORY
   5. Resource leak (stream subscription, timer, listener not cleaned up)
   6. Holding reference to destroyed scope / view / component
   7. Calling lifecycle-sensitive API after teardown

C. REACTIVE DATA
   8. Blocking operation on UI thread
   9. Infinite collection of reactive stream (never completes)
   10. Missing error handling on async/reactive chain
   11. Fetching all data then filtering client-side (N+1 or full-scan)

D. NAVIGATION / ROUTING
   12. Navigating between sibling states (loading/success/error are states, not routes)
   13. Hardcoded route strings instead of typed navigation
   14. Navigation state not restored on config change / process death

E. STYLING / ACCESSIBILITY
   15. Hardcoded colors / spacing / fonts outside theme system
   16. Missing accessibility labels on interactive elements
   17. Fixed-size text (not respecting user font preference)
   18. Touch target smaller than recommended minimum

F. DEPENDENCY INJECTION
   19. Manual instantiation bypassing DI graph
   20. Service locator / global singleton for testable deps
   21. Constructor injection through entire view tree (prop drilling)

G. ASYNC / CONCURRENCY
   22. Fire-and-forget async without cancellation / error handling
   23. Race condition (multiple parallel writes to same state)
   24. Deadlock or blocking await on UI thread

H. COMPLETENESS
   25. Stub / placeholder / TODO in required behavior
   26. Happy-path only (no error state handling)
   27. Missing empty state
   28. Loading state missing or flickers
```

**Instructions for reviewer**: For each anti-pattern above, determine if the KB has a rule that would prevent it. If yes → mark PREVENTED and cite the rule. If no → mark MISSED and suggest a new rule.

---

## Output Format

### Grade Report

```yaml
technology: swift
challenge: universal-crud
files_written: 13
lines_of_code: ~450

structural_anti_patterns:
  total: 12
  prevented: 12
  missed: 0
  coverage: 100%

subtle_bugs:
  total: 5
  prevented: 2
  missed: 3
  coverage: 40%
  missed_details:
    - id: "fixed-font-size"
      file: EmptyStateView.swift:96
      issue: "Used .system(size: 64) for decorative icon"
      kb_gap: "No example for large decorative icons with Dynamic Type"
      suggested_fix: "Add anti-pattern: .system(size:) for icons → .font(.largeTitle) + .accessibilityHidden(true)"
    - id: "filter-in-body"
      file: TaskListView.swift:14
      issue: "Computed property filters allTasks in view body"
      kb_gap: "No pattern for dynamic @Query predicates with user-selected filters"
      suggested_fix: "Add dynamic predicate example to swiftdata.md"
    - id: "viewmodel-in-state"
      file: TaskListView.swift:6
      issue: "@State private var viewModel = TaskListViewModel()"
      kb_gap: "@Observable VM + @Environment pattern not prominent enough"
      suggested_fix: "Add complete @Environment injection example to state-management.md"

stub_discipline:
  stubs_found: 1
  files: [TaskAPIService.swift]
  grade: FAIL

overall_grade: B- (76%)
  - Structural: A+ (100%)
  - Subtle bugs: C (40%)
  - Stub discipline: F (1 stub)

recommended_fact_graph_changes:
  additions:
    - rule: "Fixed .system(size:) for decorative icons → use .font(.largeTitle)"
    - anti-pattern: "Filter in view body instead of @Query predicate → push to DB"
    - doc_section: "Dynamic @Query predicates with user-selected filters"
  removals: []
  clarifications:
    - doc: state-management.md
      section: @Observable + @Environment
      issue: "Pattern exists but not prominent; needs complete example"
```

### Fact Graph Diff

```json
{
  "x_add": {
    "Fixed .system(size:) for icons": "Use .font(.largeTitle) + .accessibilityHidden(true)",
    "Filter all rows in view body": "Use @Query(filter: #Predicate { ... }) to push filter to DB"
  },
  "r_add": {
    "Fixed .system(size:) for text/icons": "REJECT",
    "Filter in view body when predicate available": "REJECT"
  },
  "w_add": {
    "accessibilityHidden": {
      "t": "modifier",
      "i": "SwiftUI",
      "note": "Hides decorative elements from VoiceOver"
    }
  }
}
```

---

## Usage

### As a skill invocation

```
/rosetta-validate swift --challenge universal-crud
```

### As a manual process

1. Read this skill file
2. Pick a challenge from the Universal Challenge Library
3. Write the spec in a temporary directory
4. Implement using ONLY the target Rosetta KB
5. Run the Universal Anti-Pattern review
6. Grade and emit recommendations

---

## Related

- `rosetta-bootstrap` — creates the Rosetta knowledge base
- `rosetta-research` — graceful-degradation research used during bootstrap/gap-filling
- `rosetta-upgrade` — before/after upgrade test with fact-graph expansion
- The 28 universal anti-patterns are carried inline above; the canonical copy lives at the plugin's `reference/universal-anti-patterns.md`. A per-technology probe catalog (generated by `rosetta-bootstrap`) lives at the resolved probes path — `rosetta_config.py find --class probes --slug {tech}`.

---

*rosetta-validate — Plugin skill for agentic-rosetta*
*Strategy: Prompt-based adversarial review of KB effectiveness via universal challenges.*
