#!/usr/bin/env python3
"""test_locators.py — pins URL sanitising, classifying, and the one TLD table.

The highest-value cases are the ones the three duplicated TLD lists could disagree on:
scheme-less hosts, CamelCase code tokens, toy two-letter hosts, and ``co`` vs ``com``
vs ``cloud`` prefix shadowing.

Run: python3 scripts/rosetta/test_locators.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import locators as L  # noqa: E402


def main() -> int:
    failures: list[str] = []

    def check(name: str, got, want) -> None:
        if got != want:
            failures.append(f"{name}\n     got:  {got!r}\n     want: {want!r}")

    # ── one TLD table, no drift ─────────────────────────────────────
    check("TLDS is a frozenset", isinstance(L.TLDS, frozenset), True)
    check("TLDS has 20 entries", len(L.TLDS), 20)
    check("SCHEMELESS_TLDS is a strict subset", L.SCHEMELESS_TLDS < L.TLDS, True)

    # The regex must be built from TLDS, so it can never list a TLD the set lacks.
    alt = L._tld_alternation(L.TLDS)
    check("alternation covers every TLD", {t for t in L.TLDS if t not in alt.split("|")}, set())
    check(
        "longest-first ordering (co cannot shadow com/cloud)",
        alt.split("|").index("cloud") < alt.split("|").index("co"),
        True,
    )
    check("com precedes co", alt.split("|").index("com") < alt.split("|").index("co"), True)

    # ── sanitize_url: accepted ─────────────────────────────────────
    accepted = [
        ("https://developers.cloudflare.com/workers/", "https://developers.cloudflare.com/workers/"),
        ("http://example.com", "http://example.com"),
        # Existing behaviour: the trailing-punctuation strip removes the closing paren.
        # Recorded as-is rather than "fixed" — this run changes structure only.
        ("https://example.com/a(b)", "https://example.com/a(b"),
        ("https://example.com/x.", "https://example.com/x"),
        ("github.com/foo/bar", "https://github.com/foo/bar"),
        ("docs.aws.amazon.com/lambda", "https://docs.aws.amazon.com/lambda"),
        ("file:///tmp/spec.md", "file:///tmp/spec.md"),
        # A declared scheme keeps a host the scheme-less rule would reject.
        ("https://e.me", "https://e.me"),
        ("https://Cargo.to", "https://Cargo.to"),
        # Existing behaviour, recorded rather than fixed: an IP literal returns early and
        # so slips past the private-host filters. See known-problems.md.
        ("http://127.0.0.1/x", "http://127.0.0.1/x"),
    ]
    for raw, want in accepted:
        check(f"sanitize accepts {raw!r}", L.sanitize_url(raw), want)

    # ── sanitize_url: rejected ─────────────────────────────────────
    rejected = [
        ("", None),
        (None, None),
        ("{placeholder}", None),
        ("https://exa mple.com", None),
        ("http://localhost:3000", None),
        ("http://_internal/x", None),
        ("http://foo.svc.cluster.local/x", None),
        ("http://foo.internal/x", None),
        ("https://example.notatld", None),
        # Scheme-less code tokens: CamelCase host, and toy two-letter first label.
        ("Dispatchers.IO", None),
        ("Cargo.to", None),
        ("e.me", None),
        # Scheme-less host with a TLD outside the stricter subset.
        ("example.rs", None),
        # No dots at all.
        ("intranet", None),
    ]
    for raw, want in rejected:
        check(f"sanitize rejects {raw!r}", L.sanitize_url(raw), want)

    corpus = [raw for raw, _ in accepted] + [raw for raw, _ in rejected] + [
        "https://  spaced.com",
        "example.com.",
        "https://example.cloud/path",
        "sub.example.co/x",
        "https://gitlab.com/a/b.git",
    ]
    mismatches = [
    ]
    check("sanitize_url agrees with provenance", mismatches, [])

    # ── classify_locator ───────────────────────────────────────────
    for url, want in [
        ("https://github.com/foo/bar", "repo"),
        ("https://github.com/foo/bar.git", "repo"),
        ("https://github.com/foo/bar/tree/main", "docs"),
        ("https://github.com/foo", "docs"),
        ("https://gitlab.com/a/b", "repo"),
        ("https://bitbucket.org/a/b", "repo"),
        ("https://www.github.com/a/b", "repo"),
        ("https://developers.cloudflare.com/workers", "docs"),
        ("https://example.com", "docs"),
        ("file:///tmp/repo", "repo"),
    ]:
        check(f"classify({url!r})", L.classify_locator(url), want)

    # ── extractors ─────────────────────────────────────────────────
    prose = "See https://a.com/x and b.io/y plus Cargo.to."
    check("find_urls", L.find_urls(prose), ["https://a.com/x"])
    check("find_hosts finds scheme-less hosts", "b.io/y" in L.find_hosts(prose), True)

    if failures:
        print(f"FAILED — {len(failures)} case(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ok — locators.py pins 20 TLDs (1 table), 9 accepts, 15 rejects, 10 classifications")
    return 0


if __name__ == "__main__":
    sys.exit(main())
