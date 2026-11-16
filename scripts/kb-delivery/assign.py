#!/usr/bin/env python3
"""assign.py — deterministic per-session kb_source assignment (Phase 2 T2.3 coin-flip).

The A/B in the KB-delivery observability plan needs each session assigned to a cohort
(condensed | full) BEFORE dispatch, recorded, and STABLE: the same session_key must always map
to the same cohort so the assignment is reproducible and the warehouse can attribute outcomes.

This is a pure hash — no randomness at call time, no state. hash(salt + session_key) maps to
[0,1); below --ratio => 'full', else 'condensed'. Deterministic means the assignment is
auditable and cannot drift between dispatch and analysis.

Pure function, no I/O. Tested in test_assign.py.
"""
from __future__ import annotations

import argparse
import hashlib
import sys

DEFAULT_SALT = "kb-delivery-v1"
DEFAULT_RATIO = 0.5  # 50% full / 50% condensed


def assign(session_key: str, salt: str = DEFAULT_SALT, ratio: float = DEFAULT_RATIO) -> str:
    if not session_key:
        raise ValueError("session_key is required for a deterministic assignment")
    if not 0.0 <= ratio <= 1.0:
        raise ValueError("ratio must be in [0, 1]")
    h = hashlib.sha256(f"{salt}:{session_key}".encode("utf-8")).hexdigest()
    val = int(h[:8], 16) / 0xFFFFFFFF  # uniform in [0, 1)
    return "full" if val < ratio else "condensed"


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--session-key", required=True)
    p.add_argument("--salt", default=DEFAULT_SALT)
    p.add_argument("--ratio", type=float, default=DEFAULT_RATIO, help="fraction assigned 'full' (0..1)")
    args = p.parse_args(argv)
    try:
        print(assign(args.session_key, args.salt, args.ratio))
    except ValueError as e:
        sys.stderr.write(f"assign: {e}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
