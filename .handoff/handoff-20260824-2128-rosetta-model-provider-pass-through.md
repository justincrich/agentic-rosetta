# HANDOFF — Rosetta engine: pass-through models, optional --provider, pi harness, provider-key error education

**Written** 2026-08-25T03:28:52Z by OpenCode (z-ai/glm-5.3)
**Repo** agentic-rosetta · **Branch** main · **HEAD** cdc3bc4
**How to use this**: read §1–§2, run the checks in §4, then start at §2 "Start Here".
Claims are labeled VERIFIED / CLAIMED / ASSUMED — re-verify anything not VERIFIED
before you rely on it. Raw evidence is in §10.

**This is a PLAN handoff. Zero implementation code has been written.** The complete
design (including three user decisions that exist nowhere else) is in §5 and §2.
Losing this document means re-deriving all of it.

## 1. Mission

Remove the hardcoded per-harness model rosters from the Rosetta fanout engine so any
model string passes through to the ACP binary, add an optional `--provider` param
(used by open-source harnesses like pi, ignored by model-provided harnesses), add
`pi` as a fifth supported harness, and classify downstream ACP worker errors
(auth/key, unknown model, quota) into educational messages that tell the calling
agent what to fix. Done = `bun test` + `bun run typecheck` green in `engine/`,
docs/AGENTS rules updated to match, grok skill pack rematerialized.

**Out of scope**: Python scripts (`scripts/`, `assets/rosetta_config.py`) — they have
no model/provider concepts. Skills other than a one-line harness-list touch in
`skills/rosetta-research/SKILL.md`. Any provider-prefixed model convention for
claude/codex/opencode/grok. Version bumps beyond CHANGELOG narrative.

## 2. Start Here

First action — delete the roster gate in `engine/src/resolve-caller.ts`:

1. **`engine/src/contract.ts`** — line 19: change Harness enum to
   `z.enum(["claude", "codex", "opencode", "grok", "pi"])`. Add optional
   `provider: z.string().optional()` to `CallerIdentity` (schema at lines 22–30).
2. **`engine/src/resolve-caller.ts`** —
   - DELETE `STATIC_ROSTERS` (lines 15–27) and `fetchRoster()` (lines 96–106) entirely.
   - `validateModel()` (lines 108–119): keep ONLY the empty-check that throws
     `MODEL_MISSING`; delete the roster check that throws `MODEL_UNRESOLVED`.
     The `harness` param becomes unused — drop it or keep signature stable for callers.
   - `detectHarness()` (lines 29–41): add pi detection — return `"pi"` when
     `env.PI_CODING_AGENT` or `env.PI_SESSION_ID` is set (pi exports both to child
     processes; VERIFIED this session, see §5).
   - `acpEntrypoint()` (lines 58–94): add `case "pi"`: `which("pi-acp")`; if missing
     throw `ResolveError("ACP_MISSING", ...)` with install hint (`npm install -g
     pi-acp` — it is NOT installed on this machine). Return
     `{ command: <pi-acp path>, args: [], note: "pi-acp adapter (spawns pi --mode rpc)" }`.
   - `resolveCaller()` (lines 121–151): accept optional `provider` in opts; store on
     CallerIdentity. **Provider composition rule**: if `provider` is set, harness is
     `pi`, and model does NOT already contain `/`, pass model as `provider + "/" + model`
     to the worker (pi natively supports `provider/model` — VERIFIED from `pi --help`:
     `--model <pattern>  Model pattern or ID (supports "provider/id")`). For all other
     harnesses ignore provider (model-provided harnesses).
3. **`engine/src/phases/preflight.ts`** — line 21: remove the `validateModel(harness,
   req.caller.model)` call and its import (the non-empty check is already enforced in
   `resolveCaller`). Keep everything else.
4. **`engine/src/worker.ts`** — in `runWorker()`'s `attempt()` catch (line ~163 in
   current file; note the file has uncommitted cwd-threading edits, see §4):
   replace the generic `` `worker error: ${message}` `` with classified errors.
   `caller` is in scope via closure — use `caller.harness` and `caller.model` in the
   messages. Patterns (match lowercased message + any `.stderr` on the error):
   - AUTH: `/api.?key|unauthorized|authentication|invalid.*key|401|403/` →
     `` `[AUTH] ${harness} rejected the request — the configured API key is missing, invalid, or does not support model "${model}". Check the key/provider config for ${harness} (for pi: `pi auth` shows provider readiness). Raw: ${msg}` ``
   - MODEL: `/model.*not.?found|does.?not.?exist|is.?not.?available|unrecognized.*model|unknown.*model|no.*model.*matched/` →
     `` `[MODEL] the model "${model}" was not recognized by ${harness}. The engine passes model strings through unvalidated — verify the exact name the harness expects (e.g. run its model-list command). Raw: ${msg}` ``
   - QUOTA: `/rate.?limit|quota|billing|insufficient|429|payment|credit/` →
     `` `[QUOTA] rate limit or billing cap hit on ${harness}. Raw: ${msg}` ``
   - else: keep `` `worker error: ${msg}` ``.
   **Also**: `runWorker()` currently retries once via `narrowedRetryPrompt` on ANY
   failure (line 168). A config/auth/model error will fail identically on retry —
   skip the retry when the first failure reason starts with `[AUTH]`, `[MODEL]`, or
   `[QUOTA]`. Simplest: have the catch return the classification on the reject
   (e.g. `reason` plus a `fatal: true` flag or just prefix-match the reason string).
5. **`engine/src/research.ts`** — add `--provider NAME` optional flag (parsed with
   existing `arg()` helper), pass into `resolveCaller({ model, harness, provider })`.
   Update `--help` text (lines 33–55): harness list becomes `claude|codex|opencode|grok|pi`,
   add `--provider NAME   optional; for open-source harnesses (pi) — composes to provider/model`.
6. **Tests** — `engine/tests/resolve-caller.test.ts`:
   - REPLACE the "refuses an unknown model" test (lines 24–33) with one asserting an
     unknown model string passes through: `resolveCaller({ model: "definitely-not-a-model", harness: "grok", env: { GROK_AGENT: "1" } })` returns caller with
     `.model === "definitely-not-a-model"` and ACP args containing the string.
   - ADD: empty model still throws `MODEL_MISSING`.
   - ADD: `detectHarness({ PI_CODING_AGENT: "pi" })` → `"pi"` (and PI_SESSION_ID).
   - ADD: provider composition — `resolveCaller({ model: "glm-5.3", provider: "zai", harness: "pi", env: {...} })` composes model to `"zai/glm-5.3"`; without provider, model stays bare; a model already containing `/` is not double-prefixed. NOTE: pi entrypoint requires `pi-acp` on PATH — it is NOT installed on this machine, so either install it (`npm install -g pi-acp`) to make the test run for real, or the pi-path tests will hit ACP_MISSING. Prefer installing pi-acp (see §7).
   - ADD: worker error-classification unit tests (export the classifier from
     worker.ts as a pure function `classifyWorkerError(err, caller)` and test the
     three patterns + fallback).
   - CHECK `engine/tests/run-research.test.ts` (uses `resolveCaller({ model: "grok-4.6", harness: "grok" })` at lines 28, 50, 88) — should still pass since grok-4.6 passes through.
7. **Docs** —
   - `engine/AGENTS.md` rule 3 currently says "`validateModel` must keep rejecting an
     empty or unknown model" — CHANGE to: rejects only an empty model; unknown model
     names pass through to the ACP binary, which surfaces provider/key errors that
     the worker classifies. Also add `pi` wherever harness lists appear, and note
     `pi` uses `pi-acp` (npm) as its ACP server.
   - `engine/README.md` — update CLI docs (repo rule: CLI change ⇒ README update in
     same commit).
   - `skills/rosetta-research/SKILL.md` line ~54: harness list `claude|codex|opencode|grok`
     → add `|pi`. Then run `python3 scripts/materialize-grok-skills.py` and
     `scripts/assert-no-drift.sh --scripts-only`.
   - `CHANGELOG.md` — new entry under a 0.4.6 (or unreleased) heading.
8. **Verify** (from repo root): `( cd engine && bun test && bun run typecheck )`,
   `python3 scripts/materialize-grok-skills.py --check`,
   `scripts/assert-no-drift.sh --scripts-only`.

## 3. State of Play

- All current-state claims below were VERIFIED this session by reading files at HEAD
  cdc3bc4 plus the uncommitted diff (see §4 — the working tree is DIRTY).
- `STATIC_ROSTERS` is referenced only by `fetchRoster()` and `validateModel()` —
  both in `engine/src/resolve-caller.ts` — plus one test expectation
  (`engine/tests/resolve-caller.test.ts:30` expects code `MODEL_UNRESOLVED`).
  VERIFIED via grep across `engine/` excluding node_modules.
- Second validation gate: `engine/src/phases/preflight.ts:21` calls `validateModel`.
- Worker error handling: `engine/src/worker.ts` `attempt()` catch returns generic
  `worker error: <msg>`; `makeWorker()` (line ~145) sits OUTSIDE the try/catch — an
  `AcpAgent` constructor throw propagates to callers (`fanout.ts` mapPool has no
  catch; `orient.ts` catches broadly). Current implementation does not construct with
  invalid input in practice, so leave as-is unless trivial to wrap.
- Engine never touches API keys; auth failures surface from the ACP binary through
  `worker.generate()` rejections. VERIFIED (no "provider"/"key" strings in engine src).
- **Landed**: nothing — this session was read-only planning.
- **In progress**: nothing of ours.
- **Broken**: nothing known-broken at HEAD.

## 4. Perishable — Check Before Touching Anything

Observed 2026-08-25T03:28Z. Re-run `git status --porcelain=v1` before working.

**The working tree is DIRTY with 4 modified engine files that are NOT from the
handoff session** (this session was read-only until writing this file). Do not
revert them; they are prior in-flight work:

```
 M engine/src/phases/fanout.ts        (threads req.sources.repoPath as cwd into runWorker)
 M engine/src/prompts/enforcement.md  (fact value-shape mandate for section r)
 M engine/src/resolve-caller.ts       (adds "deepseek/deepseek-v4-flash" to opencode roster — MOOT once rosters are deleted; deleting the rosters subsumes this hunk)
 M engine/src/worker.ts               (cwd? param threading through acpOptionsFromCaller/makeWorker/runWorker)
?? .claude/  ?? .pi/                  (untracked dirs, unknown provenance — inspect before deleting)
```

Diffstat: 4 files, +12 −4. Full diff in §10. The `worker.ts` cwd threading overlaps
the file this plan edits — implement the plan ON TOP of the dirty state (do not
checkout/reset first), or you will destroy the cwd feature and the enforcement
prompt fix.

**Extra worktree**: `.claude/worktrees/imp-upgrade-acp-driver-harness-model-1787366648`
on branch `improvement/imp-upgrade-acp-driver-harness-model-1787366648` at 6ec4c28 —
zero commits ahead of main (VERIFIED: `git log main..<branch>` empty). The branch
NAME suggests a related prior effort ("upgrade acp driver harness model"); there is
no content in it. Safe to ignore or prune, but ask the user before removing.

**No live processes** started by this session (a one-shot `pi -p` probe exited).
No stashes. No locks.

## 5. Decisions — Do Not Undo Without Reading

These three decisions came from the user in this session and exist nowhere else:

- **Delete the model rosters entirely (do not keep as comments).** User: "models
  change so fast encoding them will be rough." The rosters were labeled
  "Empirically recorded (T5 / R1)" — empirical snapshots rot; pass-through + downstream
  error classification replaces them.
- **Add an OPTIONAL `--provider` param.** User: "will be used for open-source
  harnesses, not model provided harnesses." Concretely: pi takes `--provider`/`provider/model`;
  claude/codex/opencode/grok ignore it. Never required, never defaulted.
- **Add `pi` as a fifth supported harness.** pi is an open-source coding agent
  (npm `@earendil-works/pi-coding-agent`). Its ACP server is the separate npm
  package `pi-acp` (v0.0.33, bridges ACP JSON-RPC over stdio to `pi --mode rpc`).
- **`engine/AGENTS.md` rule 3 changes on purpose.** The old text mandated rejecting
  "unknown" models. The user explicitly overrode it. Update the rule; do not
  preserve the old wording in a comment.
- **MODEL_UNRESOLVED ceases to exist** as an error code. The remaining model error
  is `MODEL_MISSING` (empty string) only. `--model` remains required, never
  defaulted — that half of the rule is untouched.

Environment facts VERIFIED this session (2026-08-24/25, this machine):

- `pi` 0.84.3 on PATH via nvm node v24.14.0 (`which pi` →
  `/Users/justinrich/.nvm/versions/node/v24.14.0/bin/pi`).
- `pi --help` documents `--provider <name>` (default google) and
  `--model <pattern>` supporting `"provider/id"` — composition target for the
  provider param.
- pi exports to child processes: `PI_MODEL`, `PI_CODING_AGENT`, `PI_PROVIDER`,
  `PI_SESSION_ID`, `PI_REASONING_LEVEL` (verified by running a pi child that listed
  its own env). Use `PI_CODING_AGENT` / `PI_SESSION_ID` for harness detection
  (PI_MODEL/PI_PROVIDER are values, not markers).
- `pi-acp` is NOT installed (`which pi-acp` → not found). README says Zed runs it
  via `npx -y pi-acp` or global install; global-install form is
  `{ command: "pi-acp", args: [] }`.

## 6. Dead Ends & Traps

- **TRAP: `pai-acp` is NOT Agent Client Protocol.** `~/.pi/agent/npm/node_modules/pai-acp`
  (v0.1.22, installed as a pi extension) is "Active Context Pruning" — a context-
  compression extension. It is a name collision with the real ACP adapter `pi-acp`
  (npm, v0.0.33, author deepstereo). Symptom: grepping `~/.pi` for "acp" finds pai-acp
  and `~/.pi/acp.log` (written by pai-acp's runtime, full of `[turn]`/`[compress]`
  lines) and suggests pi already speaks ACP. It does not — you need the separate
  `pi-acp` npm package.
- **TRAP: `pi --mode rpc` is pi's own JSON-RPC mode, not ACP.** The adapter
  `pi-acp` is the bridge. Do not point the engine at `pi --mode rpc` directly.
- **TRAP: the zsh `===` noise.** Running `echo ===` in this shell prints
  `zsh:1: == not found` (glob/equals option). Cosmetic; use `echo "---"` in probes.
- **Open verification item (first thing to check when implementing the pi path):**
  whether `pi-acp` forwards the ACP client's per-session model selection (which
  `@mastra/acp`'s `AcpAgent.model` sets) into `pi --mode rpc`'s `--model` flag.
  UNVERIFIED — pi-acp isn't installed. After `npm install -g pi-acp`, run a
  one-worker smoke test (`bun run engine/src/research.ts --tech smoke --model
  <provider/model> --harness pi --topics "one" --max-waves 1` or the existing
  `tests/worker.integration.test.ts` pattern). If the model is NOT forwarded,
  options: pass model via pi-acp CLI args if it accepts any (README shows none), or
  set `PI_MODEL`/`PI_PROVIDER` env in the caller.acp.env map (pi reads env — its
  launcher `~/.local/bin/pi-local` shows pi runs fine under a scrubbed env with
  explicit vars). Pick the env-var route if CLI forwarding is absent.
- Claude harness ACP remains `claude-code-acp` (installed, v0.16.2); codex still
  fails closed with ACP_MISSING (no ACP server) — do not "fix" codex as part of this.

## 7. Blockers

- `pi-acp` is not installed on this machine. To run pi-path tests for real:
  `npm install -g pi-acp`. The user asked for pi support, so installing it is
  in-scope — but it is a global machine change; mention it in the PR/commit message.
  If you skip installing, pi-path tests can only assert ACP_MISSING behavior.
- Nothing else blocked. No user input required to start §2.

## 8. Map — Pointers, Not Payloads

| What | Where |
|---|---|
| Rosters + validateModel + fetchRoster (to delete/soften) | `engine/src/resolve-caller.ts:15-27,96-119` |
| resolveCaller (add provider) | `engine/src/resolve-caller.ts:121-151` |
| acpEntrypoint (add pi case) | `engine/src/resolve-caller.ts:58-94` |
| detectHarness (add pi markers) | `engine/src/resolve-caller.ts:29-41` |
| Harness enum + CallerIdentity schema | `engine/src/contract.ts:19,22-31` |
| Preflight roster gate (remove) | `engine/src/phases/preflight.ts:21` |
| Worker catch + retry-once (classify + skip retry) | `engine/src/worker.ts` (`attempt()` catch; final two lines of `runWorker`) |
| CLI flag parsing + help text | `engine/src/research.ts:10-14,31-72` |
| Tests to rewrite | `engine/tests/resolve-caller.test.ts:24-33` |
| Tests using resolveCaller (should keep passing) | `engine/tests/run-research.test.ts:28,50,88` |
| Engine rule 3 to amend | `engine/AGENTS.md` (Rules section) |
| Skill harness list | `skills/rosetta-research/SKILL.md:54` |
| Skill rematerialize + drift gates | `python3 scripts/materialize-grok-skills.py` · `scripts/assert-no-drift.sh --scripts-only` |
| pi-acp adapter README (usage/env) | https://npm.im/pi-acp (readme fetched this session) |
| pi-acp limitation re model forwarding | §6 open verification item |

## 9. Environment & Bootstrap

**Build/Test**: `cd engine && bun install` (once) · `bun test` · `bun run typecheck`
(UNVERIFIED this session — no code was run; commands are from `engine/AGENTS.md`).
**Run**: `bun run engine/src/research.ts --tech NAME --model MODEL [--harness H]`.
Repo-wide verification list: root `AGENTS.md` "Verification" section — for this
change the minimum is engine tests + typecheck, plus
`python3 scripts/materialize-grok-skills.py --check` and
`scripts/assert-no-drift.sh --scripts-only` after the SKILL.md touch.
**Constraints**: never default `--model` (keep exit 2 on missing); fail closed (no
serial-crawl fallback); no mocks for ACP claims in tests; CLI changes update
`engine/README.md` in the same commit; version files bump per root `AGENTS.md`
(CHANGELOG narrative + installer files in one commit — bump `.claude-plugin/plugin.json`
and `.grok-plugin/plugin.json` to match the CHANGELOG entry, currently 0.4.5).
**Repos**: this repo is the machinery source; never hardcode consumer paths.

## 10. Evidence Appendix

Evidence sweep (2026-08-25T03:28Z, repo root):

```
$ git rev-parse --short HEAD
cdc3bc4
$ git branch --show-current
main
$ git status --porcelain=v1
 M engine/src/phases/fanout.ts
 M engine/src/prompts/enforcement.md
 M engine/src/resolve-caller.ts
 M engine/src/worker.ts
?? .claude/
?? .pi/
$ git log --oneline -5
cdc3bc4 feat(rosetta-upgrade): Mode C --topics expands a KB at the current pin
6ec4c28 fix(rosetta): KB destination follows config, not cwd; engine no longer crashes on --repo-url
8809920 engine: honor device limits.concurrency as default worker cap
2e0c30d docs: add AGENTS.md contracts and contributor architecture map
cc239b2 feat(rosetta): backfill now-pins from harvest plus judged --sources
$ git diff --stat
 engine/src/phases/fanout.ts       | 2 +-
 engine/src/prompts/enforcement.md | 4 ++++
 engine/src/resolve-caller.ts      | 2 +-
 engine/src/worker.ts              | 8 ++++++--
 4 files changed, 12 insertions(+), 4 deletions(-)
$ git stash list
(empty)
$ git worktree list
/Users/justinrich/Projects/agentic-rosetta                                                                    cdc3bc4 [main]
/Users/justinrich/Projects/agentic-rosetta/.claude/worktrees/imp-upgrade-acp-driver-harness-model-1787366648  6ec4c28 [improvement/imp-upgrade-acp-driver-harness-model-1787366648]
$ git log --oneline main..improvement/imp-upgrade-acp-driver-harness-model-1787366648
(empty — branch has no commits beyond main)
```

Uncommitted diff (pre-existing, not from this session):

```
diff --git a/engine/src/phases/fanout.ts b/engine/src/phases/fanout.ts
@@ -54,7 +54,7 @@ export async function fanoutWave(
-    const result = await runWorker(d, ring, caller);
+    const result = await runWorker(d, ring, caller, req.sources.repoPath);
diff --git a/engine/src/resolve-caller.ts b/engine/src/resolve-caller.ts
@@ -23,7 +23,7 @@
-  opencode: ["opencode", "claude-sonnet-4-6", "gpt-5.4", "grok-4.6"],
+  opencode: ["opencode", "claude-sonnet-4-6", "gpt-5.4", "grok-4.6", "deepseek/deepseek-v4-flash"],
diff --git a/engine/src/worker.ts b/engine/src/worker.ts
(+ cwd?: string threading through acpOptionsFromCaller / makeWorker / runWorker; +8 −2)
```

pi probes (this session):

```
$ which pi → /Users/justinrich/.nvm/versions/node/v24.14.0/bin/pi ; pi --version → 0.84.3
$ pi --help → "--provider <name>  Provider name (default: google)"
              "--model <pattern>  Model pattern or ID (supports \"provider/id\" and optional \":<thinking>\")"
$ which pi-acp → not found
$ npm view pi-acp → version 0.0.33, bin { 'pi-acp': 'dist/index.js' },
  "ACP adapter for pi coding agent", "communicates ACP JSON-RPC 2.0 over stdio …
  and spawns `pi --mode rpc`"
$ pi child env listing → PI_MODEL, PI_CODING_AGENT, PI_PROVIDER, PI_SESSION_ID, PI_REASONING_LEVEL
$ ~/.pi/agent/npm/node_modules/pai-acp → "Active Context Pruning" extension (name collision, NOT ACP)
```
