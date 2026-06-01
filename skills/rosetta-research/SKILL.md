---
name: rosetta-research
description: "Research an unknown technology for a Rosetta KB. 3-tier graceful degradation: delegate to /assimilate or /deep-research when present, else premium research MCPs (jina/exa) when provisioned, else built-in WebSearch/WebFetch/Read — always emits the same normalized research artifact. Trigger: /rosetta-research {tech} [{repo-url}]"
allowed-tools: WebSearch, WebFetch, Read, Task, mcp__jina__search_web, mcp__jina__parallel_search_web, mcp__jina__read_url, mcp__jina__parallel_read_url, mcp__jina__expand_query, mcp__jina__extract_pdf, mcp__plugin_exa-mcp-server_exa__web_search_exa, mcp__plugin_exa-mcp-server_exa__get_code_context_exa, mcp__holocron__storeDocumentTool, mcp__holocron__hybridSearchTool
---

# Rosetta Research

> **Purpose**: Acquire the facts a Rosetta knowledge base needs about an unknown technology — components, hard rules, API signatures, anti-patterns, traits, version pins, mental model — and return them in ONE normalized shape, regardless of what research tooling is available.
>
> **Why a separate skill?** Research is *acquisition*; `rosetta-bootstrap` is *synthesis*. Keeping acquisition behind a stable output contract means bootstrap (and `rosetta-upgrade`'s gap-filling) never has to know whether facts came from `/assimilate`, jina/exa, or plain `WebFetch`.

> **TOOL AVAILABILITY (read first).** Built-ins (`WebSearch`, `WebFetch`, `Read`) are required and assumed present. Everything else is OPTIONAL and degrades gracefully — if your harness has not provisioned jina/exa/holocron, or if invoking another skill (`/assimilate`, `/deep-research`) is unsupported, SKIP it and fall to the next tier. Never block on an optional capability.
> **Cross-harness note:** non-Claude harnesses (Codex, OpenCode, pi) may expose MCP tools under different names. The `mcp__…` names below are the CANONICAL HINT for the intended capability; match by intent, and if no equivalent exists, use the built-in floor. Detection is **attempt-and-observe** (try the preferred call; on error, fall through) — never a hard precondition.

> **NO-STUB INVARIANT (cardinal).** At no tier may this skill invent component names, version numbers, API signatures, or "TODO/placeholder" facts. Every entry in the artifact must trace to a REAL source (a delegated report, an MCP result, a fetched URL, or a read repo file). Unknowns are recorded in the artifact's `gaps[]` / `unknowns[]` — never faked. If even the built-in floor is impossible, emit a `status: BLOCKED` artifact and stop; do not fabricate.

## Inputs

```
INPUT: technology_name    (e.g., "GPUI", "Tauri v2", "SpacetimeDB")
INPUT: repository_url      (optional — the repo to assimilate)
INPUT: base_framework      (e.g., "React", "SwiftUI", "Rust" — what to map FROM)
INPUT: topics              (optional — explicit thin-topic list for gap mode)
INPUT: depth               (quick | normal | deep — default: normal)
INPUT: focus               (optional — narrow the research, e.g. "state management")
```

`depth` controls breadth/sources:
- **quick** → axes 1 (architecture), 2 (state), 6 (anti-patterns) + repo README only, 1-2 sources each
- **normal** (default) → all six axes, 2-3 sources each
- **deep** → all six axes + per-axis follow-up on gaps, 4-6 sources each

---

## The 3-Tier Ladder

The skill runs strictly top-to-floor. **It never fails closed:** every tier that cannot run cascades to the next, and Tier 3 (built-ins) is always reachable.

```
rosetta-research(tech, repo_url?, base_framework?, topics?, depth, focus?)

TIER 1 — DELEGATE TO RESEARCH SKILLS (best fidelity, optional)
  Detection = advertise-or-probe: if the orchestrator's available-skills/tools
  roster includes the skill, prefer it; else attempt one invocation and, on
  error/unsupported, treat as unavailable and fall through.

  REPO ANALYSIS path (have repo_url):
    IF `assimilate` available:
      INVOKE /assimilate {repo_url} --skip-confirm [--depth {depth}] [--focus {focus}]
      CONSUME its report (architecture, patterns, conventions, testing, deps, anti-patterns)
      → map into NORMALIZED ARTIFACT (§ Output Contract)
  TOPIC / GAP path (topics list, or no repo_url):
    IF `deep-research-swarm` available AND depth in {normal, deep}:
      INVOKE /deep-research-swarm "{tech} {topic}" --skip-confirm   (per topic cluster)
    ELSE IF `deep-research` available:
      INVOKE /deep-research "{tech} {topic}"
    ELSE IF `dependency-research` available:
      INVOKE dependency-research "{tech} {topic}"   (library-shaped, holocron-first)
    ELSE IF `research` available:
      INVOKE /research "{tech} {topic}" --new
    → CONSUME report(s) → map into NORMALIZED ARTIFACT
  IF any Tier-1 invocation ERRORS or is UNSUPPORTED → log, DROP the UNMET parts to Tier 2.

TIER 2 — SELF-CONTAINED LOOP ON PREMIUM MCPs (optional)
  Detected by try-and-fallback on first call (never a precondition flag).
  REPO ANALYSIS:
    TRY mcp__plugin_exa-mcp-server_exa__get_code_context_exa(
          query="{tech} architecture state routing async testing", repo hint)
    + mcp__jina__read_url on README / docs / key files discovered
    FALLBACK to Tier-3 fetch if a call errors.
  TOPIC RESEARCH:
    TRY mcp__jina__expand_query(topic)            (on error: keep original query)
    TRY mcp__jina__parallel_search_web(queries)   (on error: mcp__jina__search_web,
                                                    then mcp__plugin_exa-mcp-server_exa__web_search_exa)
    TRY mcp__jina__parallel_read_url(urls)        (on error: mcp__jina__read_url; skip URL on timeout)
    TRY mcp__jina__extract_pdf(url) for PDF docs  (FALLBACK Read / WebFetch)
  → NORMALIZED ARTIFACT. Any unrecoverable per-call failure → that sub-task falls to Tier 3.

TIER 3 — BUILT-IN FLOOR (always works) — see § Floor Loop
  WebSearch + WebFetch + Read only. SAME query set, SAME artifact.

STORAGE (§ Storage): holocron if present, else .rosetta/research/{tech_slug}.md. Never blocks.

BLOCKER (only if Tier 3 itself impossible):
  IF WebSearch AND WebFetch both unavailable AND repo_url unreadable:
    EMIT artifact with status=BLOCKED, reason, and what's needed.
    NEVER emit fabricated facts. Return to caller, which surfaces it to the user.
```

### Cascade / error-handling table

Modeled on `deep-research` and `research` graceful-degradation patterns.

| Failure point | Detection | Response | Cascade |
|---|---|---|---|
| `/assimilate` unsupported/errors | invocation error or harness can't call skills | log "Tier-1 repo unavailable" | → Tier 2 repo crawl |
| `/deep-research*` unsupported/errors | same | log "Tier-1 topic unavailable" | → Tier 2 topic loop |
| jina/exa tool "not found" | MCP error on first call | log "premium MCP absent" | → Tier 3 for that sub-task |
| `expand_query` fails | jina error | continue with original query | stay in tier |
| `parallel_search_web` fails | batch error | reduce to sequential `search_web`, then exa | stay in tier |
| URL read timeout (>30s) | read error | skip URL, continue | stay in tier |
| PDF extract fails | extract_pdf error | fall back to WebFetch / Read | stay/drop |
| Zero search results | empty across providers | broaden query / variations; if still empty mark topic LOW + record gap | note gap, continue |
| holocron store fails | store error | save `.rosetta/research/{tech}.md` | non-blocking |
| **Floor itself impossible** | WebSearch+WebFetch absent & no repo | **EMIT BLOCKER, do not stub** | return to caller |

---

## Floor Loop (Tier 3 — the contract-defining path)

Pure `WebSearch` + `WebFetch` + `Read`. Tiers 1 and 2 must produce the SAME artifact, just faster/richer.

**Query set — the six axes bootstrap needs** (chosen to pre-answer what `rosetta-validate` later probes):

1. **Architecture / mental model** — `"{tech} architecture overview"`, `"{tech} core concepts"`, `"{tech} vs {base_framework}"`
2. **State management** — `"{tech} state management"`
3. **Routing / navigation** — `"{tech} routing"` / `"{tech} navigation"`
4. **Async / concurrency** — `"{tech} async"`, `"{tech} concurrency"`
5. **Testing** — `"{tech} testing"`
6. **Anti-patterns / pitfalls** — `"{tech} common mistakes"`, `"{tech} anti-patterns"`, `"{tech} gotchas"`

**Loop:**

```
[T3.0] REPO READ (if repo_url):
   - WebFetch repo_url → extract README, detect language/build tool, top-level dirs
   - WebFetch the docs site / docs/ dir if discoverable
   - If repo is cloned locally / a path is given → Read README, package manifest,
     a few key source files
   - Extract: paradigm, primary building blocks, naming conventions, version/commit

[T3.1] FOR EACH axis in the query set (gated by depth):
   a) WebSearch(query) → collect top 3-6 URLs (prefer official docs, repo, changelog)
   b) WebFetch each top URL → extract: concepts, exact API snippets, code examples, version notes
      ERROR: fetch fails/timeout → skip URL, continue (never block)
   c) Note source credibility: official docs/repo = HIGH, maintainer blog = MED-HIGH,
      third-party blog/SO = MEDIUM, forum = LOW

[T3.2] SYNTHESIZE per axis:
   - Confidence per finding: HIGH (3+ credible / official-confirmed),
     MEDIUM (2 sources), LOW (single/weak)
   - Cross-reference; record contradictions explicitly

[T3.3] EXTRACT into the structures bootstrap [4]-[5] consume:
   - MENTAL-MODEL REGISTERS: the 3-register (or N-register) equivalent
   - HARD RULES: structural constraints ALWAYS true → fact-graph "r"
   - COMPONENTS: building blocks + import path + what-it-replaces → fact-graph "w"
   - API SIGNATURES: exact call expressions → fact-graph "a"
   - ANTI-PATTERNS (false friends): wrong → right pairs → fact-graph "x"
   - TRAITS/INTERFACES: type-level contracts → fact-graph "t"
   - VERSION PINS: pkg, version, commit, date → fact-graph "v"
   - FACT-GRAPH SEED: a draft of the 6-section JSON, populated where confidence ≥ MEDIUM
   - GAPS / UNKNOWNS: axes with LOW or no coverage

[T3.4] EMIT NORMALIZED ARTIFACT (below) + STORE (§ Storage)
```

> **Floor parallelism (default OFF).** The floor runs the manager-only loop (no `Task`), because subagent spawning is not uniformly available cross-harness and the floor must work everywhere. Tier 1 already gets parallelism for free when it delegates to `/assimilate` or `/deep-research-swarm`. On Claude Code specifically you MAY fan the six axes into 2-3 `Task` workers — but only if `Task` is available; otherwise run sequentially.

---

## Output Contract — Normalized Rosetta Research Artifact (NRRA)

ONE shape, identical whether produced by `/assimilate`, jina/exa, or `WebSearch`. Field names pre-map to the fact-graph's six sections so `rosetta-bootstrap` step [5] is near-mechanical.

```markdown
---
artifact: rosetta-research
tech: "{tech}"
tech_slug: "{kebab}"
base_framework: "{base_framework}"
repo_url: "{repo_url|null}"
research_tier: "delegated:/assimilate | delegated:/deep-research-swarm | premium-mcp | builtin-floor"
depth: "quick|normal|deep"
status: "complete | partial | BLOCKED"
overall_confidence: "HIGH|MEDIUM|LOW"
date: "{ISO-8601}"
storage: { holocron_id: "{id|null}", local_path: ".rosetta/research/{tech_slug}.md" }
---

# Rosetta Research: {tech}

## 1. Mental Model (registers)
- Paradigm: {one paragraph}
- Registers: {RegisterA} ↔ {RegisterB} ↔ {RegisterC}
- "How to think" vs {base_framework}: {2-4 bullets}

## 2. Version Pins        # → fact-graph "v"
| field | value | confidence |
| pkg | {name} | … |
| version | {x.y.z} | … |
| commit | {hash|null} | … |
| pinned_date | {ISO} | … |

## 3. Components / Widgets  # → fact-graph "w": {name:{t,i,note,src}}
| name | type(sl/sf/…) | import | replaces ({base}) | source_url | confidence |

## 4. Hard Rules           # → fact-graph "r" (r|nr|REJECT)
| rule_id | verdict | rationale | source | confidence |

## 5. API Signatures       # → fact-graph "a": {op: "exact code"}
| operation | exact_expression | source | confidence |

## 6. Anti-Patterns / False Friends   # → fact-graph "x": wrong → right
| wrong | right | why_it's_a_silent_trap | source | confidence |

## 7. Traits / Interfaces  # → fact-graph "t"
| name | signature_or_contract | source | confidence |

## 8. Per-Axis Findings (evidence for synthesis + concept docs)
### Architecture / State / Routing / Async / Testing
- {finding} (confidence, sources)

## 9. Fact-Graph Seed (draft JSON, ≥ MEDIUM confidence only)
```json
{ "v": {}, "w": {}, "r": {}, "a": {}, "x": {}, "t": {} }
```

## 10. Gaps & Unknowns     # axes thin/uncovered — bootstrap notes, never fabricates
- {axis}: {why thin} → suggested follow-up

## 11. Sources
[1] {title} — {url} (tier: official/blog/forum)
```

**Why this shape:** sections 2-7 are 1:1 with fact-graph `v/w/r/a/x/t`; section 1 feeds the README/ROSETTA mental model; sections 8/11 feed the 3-lens concept docs' PATTERNS + citations; section 10 keeps bootstrap honest about coverage and feeds `rosetta-upgrade` gap discovery. Confidence columns let bootstrap demote low-confidence facts to prose instead of asserting them in JSON.

---

## Storage (optional, non-blocking)

```
TRY mcp__holocron__storeDocumentTool({
      title: "Rosetta Research: {tech}",
      content: {NRRA markdown},
      metadata: { type:"research", method:"rosetta-research",
                  tech:"{tech_slug}", tier:"{research_tier}",
                  confidence:"{overall}", date:"{ISO}" }
    })
    → on success: set storage.holocron_id, DISPLAY id
FALLBACK (holocron tool absent OR store errors):
    SAVE NRRA to {PROJECT}/.rosetta/research/{tech_slug}.md
NEVER block the caller on storage outcome. The caller consumes the in-memory NRRA regardless.
```

This is the ONE filesystem write this skill makes, alongside the existing `.rosetta/` tree (`.rosetta/docs`, `.rosetta/probes`). New subdir: `.rosetta/research/`.

---

## Usage

```
# Repo path (bootstrap step [2])
/rosetta-research "GPUI" --repo-url https://github.com/zed-industries/zed --base-framework React --depth normal

# Topic / gap path (bootstrap step [3], or upgrade step [14])
/rosetta-research "Tauri v2" --topics "state management, IPC security" --base-framework React

# Standalone, no repo
/rosetta-research "SpacetimeDB" --base-framework Rust --depth quick
```

---

*rosetta-research — Plugin skill for agentic-rosetta*
*Strategy: delegate when you can, degrade when you must, never fake a fact.*
