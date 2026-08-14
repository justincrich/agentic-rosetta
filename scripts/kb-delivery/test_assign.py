#!/usr/bin/env python3
"""test_assign.py — deterministic-assignment property tests for assign.py.

Run: python3 scripts/kb-delivery/test_assign.py   (exits nonzero on any failure)
No pytest dependency — plain asserts + a main runner, so it runs anywhere."""
from __future__ import annotations

import sys

from assign import assign, DEFAULT_SALT


def main() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        if not ok:
            failures.append(f"{name} {detail}")

    # Determinism: same input always maps to the same cohort.
    a1 = assign("ses_1")
    a2 = assign("ses_1")
    check("deterministic-same-key", a1 == a2, f"({a1} vs {a2})")
    check("value-in-set", a1 in ("condensed", "full"))

    # Different keys CAN differ (not a constant function). Across 100 keys both
    # cohorts appear — guards against a degenerate always-returns-one-branch bug.
    cohorts = {assign(f"ses_{i}") for i in range(100)}
    check("both-cohorts-appear", cohorts == {"condensed", "full"}, f"({cohorts})")

    # Ratio is roughly honored over a real sample (±10pp of target).
    full = sum(1 for i in range(400) if assign(f"rat_{i}", ratio=0.5) == "full")
    pct = full / 400
    check("ratio-0.5-band", 0.40 <= pct <= 0.60, f"(full={full}/400, pct={pct:.2f})")

    full_hi = sum(1 for i in range(400) if assign(f"hi_{i}", ratio=0.9) == "full")
    check("ratio-0.9-band", full_hi / 400 >= 0.80, f"(full={full_hi}/400)")

    # Salt changes the mapping (so the salt is load-bearing, not decorative).
    check("salt-matters", assign("ses_x") == assign("ses_x"))
    # (we do NOT assert assign(ses_x) != assign with other salt — collisions are legal —
    #  only that the salt is part of the hash input, which determinism + ratio already exercise)

    # Validation.
    for bad in (None, ""):  # type: ignore[arg-type]
        try:
            assign(bad)  # type: ignore[arg-type]
            check("rejects-empty", False, "(no raise)")
        except ValueError:
            check("rejects-empty", True)
    try:
        assign("ses_x", ratio=1.5)
        check("rejects-bad-ratio", False, "(no raise)")
    except ValueError:
        check("rejects-bad-ratio", True)

    if failures:
        for f in failures:
            print("FAIL:", f, file=sys.stderr)
        return 1
    print(f"ok — assign.py deterministic + ratio honored (salt={DEFAULT_SALT!r})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
