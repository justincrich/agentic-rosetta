# Architecture

How Rosetta machinery fits together. Invariants live in [`AGENTS.md`](AGENTS.md).
Storage schema lives in [`CONFIG.md`](CONFIG.md). Theory lives in [`WHY.md`](WHY.md).

This repo is the **plugin**. Knowledge bases and agent prompts live in a
**consumer** project that owns `agents/` — not here.
[`hackerpug-ai/agent-content`](https://github.com/hackerpug-ai/agent-content) is
the consumer this machinery is built against.

This repo also ships **no service**. The read-only MCP Worker that served a
published snapshot, and the publisher and device-sync client that staged it, were
removed and are being rebuilt in that consumer. Everything below is the write
path that produces a graph; how one is delivered is now the consumer's problem.

## Write path

```
/rosetta-research
        │
        ▼
engine/  [A0] preflight → [A1] orient → [A2] census
         [A3] fanout waves → [A4] ledger close
         [A5] reconcile → [A6] emit NRRA
        │
        ▼
  nrra.md + ledger.json  (+ provenance.json when sources were pinned)
        │
        ▼
/rosetta-bootstrap  or  /rosetta-upgrade
        │                    (research may --create only if the caller asked)
        ▼
write-graph.py --create | --update
        │
        ▼
<project>/.rosetta/docs/<slug>/
    fact-graph.json          ← only this file is the SoT
    provenance.json          ← source pins, not inside the graph
    fact-sources.json        ← per-fact locators, not inside the graph
    .history/…               ← snapshot before each refresh
    MIGRATION.md             ← human changelog, on demand
        │
        ▼
registry.py scan             →  <project>/.rosetta/registry.json
        │
        ▼
embed-graph.sh --sync        →  agents/<name>/agent.md
                                (only the <!-- rosetta-kb --> region)
```

Skills parse arguments, call these programs, and report. They do not invent a
second writer.

## Engine (`engine/`)

Mastra workflow. Workers are real ACP processes that inherit the **invoking**
harness and model (`@mastra/acp`). There is no in-process “just call the LLM”
fallback.

| Stage | Module | What it does |
|---|---|---|
| `[A0]` | `phases/preflight.ts` | Fail closed. Named codes: `RECURSION`, `HARNESS_UNRESOLVED`, `MODEL_UNRESOLVED`, `ACP_MISSING`, `RUNTIME`. |
| `[A1]` | `phases/orient.ts` | Build the decoder ring (nouns + traps) and its hash. |
| `[A2]` | `phases/census.ts` | Deterministic `git ls-files` / docs crawl. First-match class table. Group to `maxUnits` (repo default 40) / `docsMaxUnits` (default 10) / 250 KB. Overflow **merges** are logged, not silent drops. `--topics` skips census and fans those directives. |
| `[A3]` | `phases/fanout.ts` | Wave of ACP workers. Each worker gets one directive + a class prompt from `src/prompts/`. |
| `[A4]` | `phases/ledger.ts` | Close the coverage ledger. Open ledger → emit is `BLOCKED`. |
| `[A5]` | `phases/reconcile.ts` | Merge worker reports; contradictions become `surprises[]` / `gaps[]`. |
| `[A6]` | `phases/emit.ts` | Write the NRRA (markdown) + ledger JSON. |

`ROSETTA_WORKER=1` is set inside a worker. Preflight STOPs if it sees that flag
(no recursive fanout). `--model` is required at the CLI and never defaulted.

Zod schemas in `src/contract.ts` are the wire types: `ResearchRequest`,
`ResearchResult`, `Ledger`, `WorkerReport`, `DecoderRing`. Territory classes
(`orientation | reference | enforcement | scars | contracts | exemplars`) map
to fact-graph sections via `YIELDS_BY_CLASS`.

How to run it: [`engine/README.md`](engine/README.md). Contribution rules:
[`engine/AGENTS.md`](engine/AGENTS.md).

## Fact graph

Six lookup sections. If a fact is a key→value without losing meaning, it belongs
here. If it needs reasoning or tradeoffs, it belongs in a 3-lens markdown doc.
That split is in [`WHY.md`](WHY.md) — do not restate it in skill prose.

| Key | Contents |
|---|---|
| `v` | Version pins (`pkg`, `version`, `pinned` / `pinned_date`, `kb_version`) |
| `w` | Widgets / components |
| `r` | Rules (`r` / `nr` / `REJECT`) |
| `a` | API signatures |
| `x` | Anti-patterns (wrong → right), including version-tagged deprecations |
| `t` | Traits / interfaces |

Sidecars sit **next to** the graph, never inside it:

- `provenance.json` — source pins + `corpus_sha256`
- `fact-sources.json` — `section:key` → locator
- `.history/{date}-v{n}.json` + `.history/manifest.json` — rollback targets
- `MIGRATION.md` — what changed, loaded on demand

`kb_version` is derived from the section diff on `--update` (major / minor /
patch). Never write `"0.0.0"` as a fake upstream version. `--adopt` stamps
recoverable fields on a legacy graph without a ledger.

## Storage vs tracking

**Storage** answers “where do I write the next artifact?”
`assets/rosetta_config.py` decides. Precedence: CLI flag →
`<project>/.rosetta/config.json` → `$ROSETTA_HOME/config.json` (default
`~/.rosetta`) → built-in `"project"`.

Root values: `"project"` (repo you are standing in), `"device"` (`~/.rosetta`
itself, no nested `.rosetta/`), or any path (`~/somewhere` →
`~/somewhere/.rosetta/…`).

Reads search three places — configured root, then project, then device — and
take the first hit. A config change does not orphan an existing project-local
KB.

`~/.rosetta` is a **device home** (config + optional `device`-rooted artifacts).
It is **not** a global catalog of every KB on the machine.

**Tracking** answers “which graphs exist in *this* project, and which agents
consume them?” `scripts/rosetta/registry.py` walks **one** project
(`--root` / `$ROSETTA_PROJECT` / cwd). It never hardcodes a central repo. The
index is `<that project>/.rosetta/registry.json`.

Three layouts the walk recognizes:

```
.rosetta/docs/<slug>/fact-graph.json     # post-consolidation default
docs/<slug>/fact-graph.json              # legacy
agents/<name>/kb/fact-graph.json         # agent-local (e.g. morph-cloud)
```

Consumer discovery is by agent-directory name. Hand tables exist only for
spelling mismatches (`hetzner` → `hetzner-cloud`, `aisdk` → `ai-sdk`,
`spacetimedb` → `spacetime-db`) and one shared graph
(`cloudflare-rust` → `cloudflare-workers`). A new domain whose agent stem
matches its slug needs no table entry.

### Why `docs` defaults to `"project"`

`registry.py` and `embed-graph.sh` only see graphs **inside the repo that owns
`agents/`**. A knowledge base written somewhere else is created, then silently
never injected. `rosetta_config.py doctor` warns when the resolved docs root
has no sibling `agents/` directory.

A machine may still centralize `research` and `probes` (this machine’s
`~/.rosetta/config.json` points those two at `~/Projects/brain`). That is
opt-in configuration, not a plugin default, and it does not make brain a
hardcoded path in the code.

## Delivery into agents

`fact-graph.json` is the SoT. What agents *see* is a **generated render**
between stable sentinels:

```
<!-- rosetta-kb:begin domain="…" role="…" kb_version="…" -->
…render…
<!-- rosetta-kb:end -->
```

`generate_excerpt.py --embed` produces the block (default: lossless minified
JSON of the whole graph; `--embed-format markdown` is the legacy per-role
render). `embed-graph.sh` is the only thing that writes that region. Hand-authored
prose above/below the sentinels is never regenerated.

`--check` exits 0 only when every block matches a fresh render. `--sync`
without a known slug or agent name fails closed (no accidental fleet walk).

## Install and distribute

| Path | What it does | What it does not |
|---|---|---|
| Claude `/plugin install` | Copies the plugin tree, including `skills/*/assets/` | **Never runs `install.sh`.** That is why the resolver is vendored inside each skill. |
| `grok plugin install` | Reads `.grok-plugin/plugin.json` → `./grok-skills` | Does not want `~/.grok/skills/rosetta-*` drop-ins (those slash as `/user:…`) |
| `./sync.sh` | This machine’s distributor: skillshare extras + rematerialize Grok + strip colliding Grok drop-ins | Not for external checkouts |
| `./install.sh` | External checkouts: copy skill dirs into detected harnesses; create `~/.rosetta/config.json` if missing (`roots.default: "project"`) | Does not clobber an existing device config. Does not install language KBs. |
| `./uninstall.sh` | Removes the seven skill names | Keeps `~/.rosetta` unless `--purge` |

Grok slash names are short because the plugin is named `rosetta`. Canonical
ids stay `rosetta-research` etc. for Claude / Codex / OpenCode / skillshare.
`scripts/materialize-grok-skills.py` is the only writer of `grok-skills/`.

## Consumer project (not this repo)

A consumer that wants the embed pipeline looks like this:

```
<project>/
  agents/
    <slug>-planner/agent.md
    <slug>-implementer/agent.md
    <slug>-reviewer/agent.md
  .rosetta/
    config.json          # optional override of ~/.rosetta
    registry.json        # rebuilt by registry.py scan
    docs/<slug>/         # fact-graph + sidecars + 3-lens docs
    research/<slug>.md   # if research is rooted here
    probes/<slug>.md
```

Run `/rosetta-bootstrap`, `/rosetta-sync`, `/rosetta-upgrade` **from that
project** (or pass `--root`). Running them from this plugin checkout will
resolve `PROJECT` to *this* repo, which has no `agents/` and is the wrong
place to write a KB.

## Skills as wrappers

| Skill | Primitive it calls |
|---|---|
| `rosetta-research` | `engine/src/research.ts` |
| `rosetta-bootstrap` | research → `write-graph.py --create` → docs + triad scaffold → `/rosetta-sync` |
| `rosetta-upgrade` | Mode C (`--topics`): research specialty at current pin + `--update`. Mode B (`--url`): `provenance.py --check-stale` then research + `--update` + sync. Mode A: challenge delta then Mode C |
| `rosetta-sync` | `registry.py scan/validate` + `embed-graph.sh --sync` |
| `rosetta-validate` | reads graph + probes; `embed-graph.sh --check`; **does not write graphs** |
| `rosetta-ab-test` / `rosetta-stress-test` | evaluation; **do not write graphs** |

Slash names: Claude `/rosetta-research` (or `/agentic-rosetta:rosetta-research`
if namespaced). Grok `/rosetta:research`.
