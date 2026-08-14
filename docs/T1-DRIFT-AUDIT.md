# T1 — 4-way drift audit and election

Recorded 2026-08-13. Hashes are SHA-256 prefixes of each `SKILL.md`.

## Hash census (pre-election)

| Skill | plugin | ~/.claude | ~/.config/opencode | ~/.codex | ~/.grok | elected |
|---|---|---|---|---|---|---|
| rosetta-research | b43d85ba33bd | 5604367f9654 | 0741ac1e21cc | 943ecf4acc4b | MISSING | rewritten in T11 (thin A0–A6 wrapper). Claude hunks kept: "Downstream delivery is a generated render"; `## Storage resolution`. Floor-parallelism clause **deleted**. |
| rosetta-bootstrap | 7faf7e92670f | 9dc07197a0c9 | b830cb176b95 | 0b473c69302b | MISSING | **claude** — generated-render / `rosetta-sync` distribution, not hand-pasted JSON |
| rosetta-upgrade | c608b70dfb38 | 2bac5ad61800 | bcb5af3df63a | 9a124bf726ff | MISSING | **claude** — embed-graph.sh distribution + resolver-driven locate |
| rosetta-validate | 8e86d922243f | 439ec0cd2aad | e4503e0887f8 | eb6092740eab | MISSING | **claude** — registry + resolver discover; embed-graph `--check` |
| rosetta-sync | — | 7d714c76b699 | MISSING | MISSING | MISSING | **claude** (only source). De-brained in T2. |
| rosetta-ab-test | — | 3a2d6c20e942 | 3a2d6c20e942 | 3a2d6c20e942 | 3a2d6c20e942 | **brain=skillshare** (already identical). Write paths stripped in T12. |
| rosetta-stress-test | — | ba4f3ae381e6 | ba4f3ae381e6 | ba4f3ae381e6 | ba4f3ae381e6 | **brain=skillshare** (already identical). Write paths stripped in T12. |

## Divergent hunks and election reasons

### rosetta-research

| Hunk | plugin | claude | elected | why |
|---|---|---|---|---|
| Storage paragraph wrapping | one long line citing `§ Storage` | split line + `§ Storage resolution` | claude | names the deterministic section |
| holocron-fail / resolver-absent table rows | longer fallback wording | shorter, same meaning | claude | matches the restructured section |
| NRRA `storage.local_path` | "see § Storage" | bare resolved path | claude | path is recorded, not described |
| **Downstream delivery is a generated render** | absent | present after §11 | **claude** | load-bearing: research must not self-limit graph richness |
| `## Storage` vs `## Storage resolution` | one section mixing holocron + resolver | holocron first, then a dedicated resolution section | **claude** | T1 AC-2 named this hunk |
| Floor parallelism (line 161) | "default OFF … you MAY fan 2–3 Task workers" | same | **deleted** (T11) | that clause is the bug this plan exists to kill |

### rosetta-bootstrap

| Hunk | plugin | claude | elected | why |
|---|---|---|---|---|
| [7] scaffold | paste JSON + hard rules into agent.md | `/rosetta-sync` / `embed-graph.sh --sync` generated block | **claude** | generated render is the distribution contract |
| [0] resolver | present in both | same idea, claude also points at embed | claude | |

### rosetta-upgrade

| Hunk | plugin | claude | elected | why |
|---|---|---|---|---|
| Phase 3 apply | construct bootstrap, insert between frontmatter | embed via `/rosetta-sync` | **claude** | same generated-render contract |
| Phase 6 [17] | re-inject JSON by hand | re-embed via embed-graph.sh | **claude** | T12 then turns the mutation into a follow-on `--topics` run |
| Versioning model | "verbatim into working memory, stay lean" | "graph is SoT; render is sized at delivery" | **claude** | matches the downstream-delivery paragraph |
| R1 locate | `$KB_DIR` from resolver | explicit RESOLVER lookup + `find` | **claude** | |

### rosetta-validate

| Hunk | plugin | claude | elected | why |
|---|---|---|---|---|
| [1] discover | resolver only | registry `graph-for` + resolver + `embed-graph.sh --check` | **claude** | tests what agents actually received |
| Challenge prompt path | `$KB_DIR (the resolved path)` | `$KB_DIR (registry-reported slug, resolved root)` | claude | |

## After T11/T12

Bootstrap / upgrade / validate elected bodies are the *baseline*. T11/T12 then rewrite them to route every graph write through `write-graph.py`, delete remaining `brain/` path assumptions, and make research a thin engine wrapper. Byte-identity across harnesses is T13–T16's job (`skillshare` + `assert-no-drift.sh`), not `install.sh`'s destructive `cp`.
