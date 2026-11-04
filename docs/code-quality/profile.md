# Code organization plan — agentic-rosetta

Stack pages: `~/Projects/brain/docs/clean-code/stacks/node-typescript.md` (engine).
No `python-stdlib-cli` stack page exists yet, so the Python rules below are derived from the code's
own evidence and from the shared principles page, and are marked **not researched**.
Last reviewed: 2026-11-04.

## What this repo is

One-source machinery for seven Rosetta skills: a TypeScript fanout engine that researches a
technology, Python writers that turn that research into a `fact-graph.json`, and a delivery layer
that embeds each graph into agent bodies. The plugin installs onto other repos; it hosts no
knowledge base of its own.

## The four trees, and what each owns

| Tree | Job | May import |
|---|---|---|
| `engine/src/` | Fanout `[A0]`–`[A6]`, ACP workers, Zod wire contracts | npm deps, `engine/` only |
| `scripts/rosetta/` | The **writers**: fact-graph mutation, registry, provenance, versioning | stdlib, pydantic, `scripts/rosetta/*` |
| `scripts/kb-delivery/` | Graph → embeddable render, cohort assignment | stdlib, `scripts/rosetta/*` |
| `scripts/*.py`, `assets/` | Plugin-level tooling: Grok pack materializer, storage resolver | **stdlib only** |
| `skills/`, `grok-skills/` | Prose. `grok-skills/` is **generated** | nothing |

Dependencies point one way: `kb-delivery` → `rosetta`; `rosetta` never imports `kb-delivery`.

## Where each kind of code goes

| Kind of code | Home | Rule |
|---|---|---|
| A decision with one right answer (a path, a version bump, a slug, a URL verdict) | a pure module in `scripts/rosetta/` | exactly one home; no I/O inside |
| The shape of a file that crosses a process boundary | a typed model in `scripts/rosetta/factgraph.py` | pydantic; the model *is* the validator |
| Reading/writing a file | `scripts/rosetta/jsonfile.py` | atomic writes only |
| Network pinning / staleness probing | its own module | never called from a pure decision |
| A CLI verb | `main(argv)` in the entry script | the entry is a thin adapter; logic lives in a module |
| Wire shapes crossing TS↔Python | `engine/src/contract.ts` | change the Zod source first |
| A constant two modules both need | the module that owns the decision | never re-typed in a second table |

## `scripts/rosetta/` module map

| Module | Owns | Pure? |
|---|---|---|
| `resolve.py` | Which project a run is about (5-tier precedence) | reads env + filesystem |
| `naming.py` | Agent name → (stem, role), agent → graph slug, graph path → slug, `ROLES` | yes |
| `versions.py` | Semver parse/increment, the `kb_version` bump rule | yes |
| `locators.py` | What counts as a URL, where it points, the one TLD table | yes |
| `factgraph.py` | The fact-graph shape: pydantic model + the write gate | yes |
| `jsonfile.py` | Atomic JSON/text write, strict read, canonical digest | disk |
| `provenance.py` | **Facade** over versions/locators/jsonfile; pins, staleness, companion files | disk + network |
| `registry.py` | Exhaustive graph index and consumer mapping | disk |
| `roots.py` | Where the KB goes (deliberately does NOT walk up — see `resolve.py`) | env + subprocess |
| `write-graph.py` | **Entry point**: the only writer of `fact-graph.json` | — |

## Conventions the code already holds (keep them)

1. **Stdlib-only for anything that ships into a harness.** `assets/rosetta_config.py` is copied as a
   bare `.py` into 14 harness directories and `$ROSETTA_HOME/bin/`. It must never import pydantic.
   pydantic is confined to `scripts/rosetta/` (the writer surface), and is declared in
   `pyproject.toml`.
2. **Fail closed, name the check.** `write-graph.py` raises `GateError(check, detail)`; the engine
   returns a named preflight code. Errors never degrade into silent success — including a missing
   pydantic, which must say so and exit non-zero rather than validating less.
3. **Honest degradation where a fallback is stated.** `rosetta_config.py` documents exactly which
   tier wins and never invents one.
4. **Real-filesystem selftests.** `test_*.py` build temp projects from `scripts/testdata/`. No
   mocks of the filesystem or of the writers.
5. **One writer per artifact.** `fact-graph.json` ← `write-graph.py`. The `rosetta-kb` region ←
   `embed-graph.sh`. `grok-skills/` ← `materialize-grok-skills.py`.
6. **`--root` / `$ROSETTA_PROJECT` always win**, and nothing hardcodes `~/Projects/brain`.
7. **Drift gates over hand-copies.** Where a file is deliberately replicated (`rosetta_config.py`
   ×14, `grok-skills/` ×7), a gate proves the copies identical.

## Where each shared decision lives (after this plan)

| Decision | One home | Consumed by |
|---|---|---|
| Repo root | `scripts/rosetta/resolve.py` | `registry`, `generate_excerpt`, `write-graph` |
| Agent name → (stem, role) | `scripts/rosetta/naming.py` | `registry`, `generate_excerpt` |
| Agent name → graph slug | `scripts/rosetta/naming.py` | `registry`, `generate_excerpt` |
| Graph path → slug (3 layouts) | `scripts/rosetta/naming.py` | `registry`, `generate_excerpt` |
| `ROLES` | `scripts/rosetta/naming.py` | both |
| Semver parse/inc/bump | `scripts/rosetta/versions.py` | `provenance`, `write-graph` |
| Canonical JSON + atomic write | `scripts/rosetta/jsonfile.py` | `provenance`, `write-graph`, `registry` |
| Valid TLDs | `scripts/rosetta/locators.py` | `provenance` |
| URL sanitise / classify | `scripts/rosetta/locators.py` | `provenance` |
| The fact-graph shape | `scripts/rosetta/factgraph.py` | `write-graph`, `provenance`, `registry` |

## Where the code is heading

From 90 days of subjects and `AGENTS.md`: graph **composition** (`_meta.composed_with`,
multi-slug register) is the live feature; KB **drift/refresh** (`--topics`, backfill pins,
provenance) is the live maintenance axis. Both need the graph shape and the slug/version decisions
to have one home each — which is what this plan builds.

## Python conventions applied here

- a module that answers a question is pure; a module that touches disk says so in its name;
- `argparse` at the edge, `main(argv) -> int` as the only entry, exit codes as the API;
- `from __future__ import annotations` + builtin generics (`list[str]`, `dict[str, Any]`);
- a typed model over a bare `dict` for anything crossing a file boundary;
- errors raised, not printed: `GateError` in scripts, `SystemExit` at the CLI boundary.
