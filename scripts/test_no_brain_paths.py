#!/usr/bin/env python3
"""test_no_brain_paths.py — this repository holds machinery only.

Every agent prompt and every knowledge base now lives in the **consumer repository**,
``~/Projects/agent-content``; the device storage config points there and the read-only MCP
service serves the same content (see CONFIG.md). A machine no longer needs a brain checkout,
so no file in this repository may send a run back to it.

Two cases, both against the real checkout (``git ls-files``, the real tree, the real AGENTS.md):

  ac27  no tracked machinery file hardcodes the retired brain checkout, and no agent prompt
        is committed here.
  ac32  no tracked ``fact-graph.json`` sits outside ``scripts/testdata/``, and AGENTS.md names
        the consumer repository that owns ``agents/`` and the KBs.

References that are *not* a hardcoded path — a docstring example, a "never hardcode this"
prohibition, the publisher's external-oracle default — are listed in ``ALLOWED`` with the
reason each one is tolerated. The list is not a dumping ground: an entry whose file no longer
mentions the path fails as stale, so a retired exemption cannot rot into a blanket permission.

Run: python3 scripts/test_no_brain_paths.py                 # every case
     python3 scripts/test_no_brain_paths.py -k ac27         # one case
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: The checkout this sprint retires. Assembled from parts so this scanner does not match its
#: own needle (a literal here would make the file flag itself).
RETIRED_CHECKOUT = "/".join(("Projects", "brain"))

#: Fixtures may hold real agent prompts and graphs: that is what they are for.
FIXTURE_ROOT = "scripts/testdata/"


@dataclass(frozen=True)
class Exemption:
    """A file (glob) whose reference to the retired checkout is not a hardcoded path."""

    pattern: str
    reason: str


#: Every entry names the reason its reference cannot be a hardcoded destination. Files in this
#: task's write scope are deliberately absent — they were fixed, not excused.
ALLOWED: tuple[Exemption, ...] = (
    Exemption(
        "assets/rosetta_config.py",
        "docstring example value inside the resolver itself; the resolver has no built-in path",
    ),
    Exemption(
        "skills/*/assets/rosetta_config.py",
        "byte-identical copy of assets/rosetta_config.py, kept in step by ./sync-assets.sh",
    ),
    Exemption(
        "grok-skills/*/assets/rosetta_config.py",
        "generated from skills/*/assets by scripts/materialize-grok-skills.py; edit the source",
    ),
    Exemption(
        "grok-skills/*/SKILL.md",
        "generated from skills/*/SKILL.md; the line is a prohibition, not a path",
    ),
    Exemption(
        "skills/AGENTS.md",
        "prohibition ('Never hardcode ...'); this tree is outside RETIRE-01's write scope",
    ),
    Exemption(
        "skills/*/SKILL.md",
        "prohibition in the canonical skill body; outside RETIRE-01's write scope",
    ),
    Exemption(
        "scripts/kb-delivery/*",
        "publisher/parity-oracle default (ROSETTA_PUBLISH_ROOT -> a consumer checkout) and its "
        "docstrings; outside RETIRE-01's write scope",
    ),
    Exemption(
        "scripts/rosetta/resolve.py",
        "docstring stating the rule it obeys ('never hardcodes ...'); outside write scope",
    ),
    Exemption(
        "scripts/rosetta/test_registry.py",
        "docstring describing the live-fleet floor check; outside write scope",
    ),
    Exemption(
        "scripts/testdata/materialize.py",
        "fixture docstring naming the tree it replaces; outside write scope",
    ),
    Exemption(
        "docs/code-quality/profile.md",
        "pointer to brain's own code-quality stack page (brain still owns docs/clean-code) and "
        "one prohibition",
    ),
)


# ───────────────────────────── real checkout ─────────────────────────────

def tracked_files() -> list[str]:
    """Every git-tracked path, from the real index. Raises RuntimeError when git cannot answer."""
    if not (REPO / ".git").exists():
        raise RuntimeError(f"{REPO} is not a git checkout — nothing to scan")
    proc = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git ls-files failed ({proc.returncode}): "
                           f"{proc.stderr.decode(errors='replace').strip()}")
    names = [n for n in proc.stdout.decode("utf-8", "surrogateescape").split("\0") if n]
    if not names:
        raise RuntimeError(f"git ls-files listed 0 files under {REPO} — the scan would prove nothing")
    return names


def read_text(rel: str) -> str:
    return (REPO / rel).read_bytes().decode("utf-8", "replace")


def occurrences(rel: str) -> list[tuple[int, str]]:
    """(line number, stripped line) for every line naming the retired checkout."""
    hits = []
    for number, line in enumerate(read_text(rel).splitlines(), start=1):
        if RETIRED_CHECKOUT in line:
            hits.append((number, line.strip()))
    return hits


def exemption_for(rel: str) -> Exemption | None:
    for entry in ALLOWED:
        if fnmatch.fnmatchcase(rel, entry.pattern):
            return entry
    return None


# ───────────────────────────── cases ─────────────────────────────

def case_ac27() -> list[str]:
    """No tracked machinery file hardcodes the retired checkout; no prompt is committed here."""
    failures: list[str] = []
    tracked = tracked_files()
    print(f"  files scanned: {len(tracked)} (git ls-files in {REPO})")
    if len(tracked) <= 50:
        failures.append(f"only {len(tracked)} tracked files scanned — the scan proves too little")

    violations: list[str] = []
    excused: list[tuple[str, Exemption, list[tuple[int, str]]]] = []
    for rel in tracked:
        hits = occurrences(rel)
        if not hits:
            continue
        entry = exemption_for(rel)
        if entry is None:
            for number, line in hits:
                violations.append(f"{rel}:{number}: {line[:120]}")
        else:
            excused.append((rel, entry, hits))
    for line in violations:
        failures.append(f"hardcoded retired checkout: {line}")

    print(f"  references to the retired checkout: {sum(len(h) for _, _, h in excused)} line(s) "
          f"in {len(excused)} file(s), each with a stated reason:")
    for rel, entry, hits in excused:
        print(f"    {rel}:{','.join(str(n) for n, _ in hits)}")
        print(f"      reason: {entry.reason}")

    for entry in ALLOWED:
        if not any(entry is e for _, e, _ in excused):
            failures.append(f"stale exemption {entry.pattern!r} — no tracked file matches it any "
                            f"more; delete the entry ({entry.reason})")

    prompts = [rel for rel in tracked
               if re.search(r"(^|/)agent\.md$", rel) and not rel.startswith(FIXTURE_ROOT)]
    for rel in prompts:
        failures.append(f"agent prompt committed to the machinery repo: {rel}")
    print(f"  tracked agent prompt files outside {FIXTURE_ROOT}: {len(prompts)}")

    agent_dirs = sorted({rel.split("/agents/")[0] + "/agents/"
                         for rel in tracked
                         if "/agents/" in rel and not rel.startswith(FIXTURE_ROOT)})
    for rel in agent_dirs:
        failures.append(f"agent tree committed to the machinery repo: {rel}")
    print(f"  tracked agent trees outside {FIXTURE_ROOT}: {len(agent_dirs)}")

    if not failures:
        print(f"  ok — {len(tracked)} files scanned, 0 hardcoded {RETIRED_CHECKOUT} paths "
              f"({sum(len(h) for _, _, h in excused)} line(s) in {len(excused)} file(s) are an "
              f"example, a prohibition or an external oracle, each with a stated reason), "
              f"0 committed prompts")
    return failures


def case_ac32() -> list[str]:
    """Machinery only: no tracked fact-graph outside the fixtures, and AGENTS.md names the consumer."""
    failures: list[str] = []
    tracked = tracked_files()
    graphs = [rel for rel in tracked if rel.endswith("fact-graph.json")]
    stray = [rel for rel in graphs if not rel.startswith(FIXTURE_ROOT)]
    for rel in stray:
        failures.append(f"fact-graph.json committed to the machinery repo: {rel}")
    print(f"  tracked fact-graph.json files: {len(graphs)} "
          f"({len(graphs) - len(stray)} under {FIXTURE_ROOT}, {len(stray)} elsewhere)")

    agents_md = read_text("AGENTS.md")
    blocks = [b for b in re.split(r"\n\s*\n", agents_md) if "agent-content" in b]
    if not blocks:
        failures.append("AGENTS.md never names agent-content — the consumer repo is unrecorded")
    else:
        owning = [b for b in blocks
                  if "agents/" in b and ("KB" in b or "knowledge base" in b or "knowledge-base" in b)]
        if not owning:
            failures.append("AGENTS.md names agent-content but not as the repo that owns agents/ "
                            "and the KBs")
        else:
            first = " ".join(owning[0].split())
            print(f"  AGENTS.md consumer paragraph: {first[:200]}")
    print(f"  AGENTS.md paragraphs naming agent-content: {len(blocks)}")

    if not failures:
        print(f"  ok — {len(graphs)} fact-graphs all under {FIXTURE_ROOT}; AGENTS.md names "
              f"agent-content as the consumer repo that owns agents/ and the KBs")
    return failures


CASES = {
    "ac27": case_ac27,
    "ac32": case_ac32,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-k", "--case", action="append", default=None,
                    choices=sorted(CASES), help="run one case (repeatable; default: all)")
    args = ap.parse_args(argv)
    selected = args.case or sorted(CASES)

    failed = False
    try:
        for name in selected:
            print(f"── {name} ──")
            failures = CASES[name]()
            for line in failures:
                print(f"  FAIL {line}")
            failed = failed or bool(failures)
            if not failures:
                print(f"  PASS {name}")
            print()
    except RuntimeError as exc:
        print(f"  FAIL {exc}", file=sys.stderr)
        return 1

    if failed:
        print(f"FAILED — {', '.join(selected)}", file=sys.stderr)
        return 1
    print(f"ok — {', '.join(selected)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
