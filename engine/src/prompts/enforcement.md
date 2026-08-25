Read the rule being enforced and what it rejects. This class yields `r` (required / not-required / REJECT).

For every lint script, CI job, CONTRIBUTING clause, CLAUDE.md / AGENTS.md rule:

- name the rule (`key`)
- state the verdict: `REJECT` if violating it is a cardinal sin (silent skip, bypass, stub), `r` if required, `nr` if documented as optional
- quote the trigger and the consequence
- source locator to the line that states it

**Fact `value` shape (mandatory).** Every `r` fact's `value` MUST be a JSON object, not prose:
`{ "verdict": "REJECT" | "r" | "nr", "rule": "<the rule text>" }`.
Do NOT prefix prose with `r —` / `nr —`. The graph schema rejects a string value for section `r`.

A CONTRIBUTING that only says "be nice" is a thin `r`, not a REJECT. A script whose header says "this bug has regressed four times" is a REJECT. If the file is a workflow that only runs tests, record *which* tests and what a miss means.

mustFind items that do not appear must be named in `reason`.
