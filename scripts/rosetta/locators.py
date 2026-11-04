#!/usr/bin/env python3
"""locators.py — what counts as a URL, and where does it point?

WHY THIS EXISTS
  All of this lived in provenance.py, and the valid-TLD knowledge existed in THREE
  places within that one file:

    line ~50  _HOST_RE      a hand-written alternation (com|org|net|...|gov)
    line ~53  _OK_TLDS      a frozenset repeating the same 19 entries
    line ~500 an inline {"com","org",...,"cloud"} subset, used for scheme-less promotions

  Three copies of one fact, in one file. Editing a TLD meant finding all three, and the
  third was the easiest to miss because it is a literal inside a branch.

THE TWO SETS ARE NOT A DUPLICATE
  ``TLDS`` is what a real URL may end in. ``SCHEMELESS_TLDS`` is the stricter subset
  allowed when the string did NOT start with ``http://`` — a bare ``foo.dev`` typed in
  prose is more likely a version string or a file name than a host. They answer
  different questions and are kept separate, with the narrower one derived by name, not
  by copy-paste.

WHY THE REGEX IS BUILT FROM THE SET
  So the alternation cannot drift from the frozenset. Longest-first ordering matters:
  ``co`` is a prefix of ``com`` and ``cloud``, so a short-first alternation would
  mis-parse ``example.com/``. ``_tld_alternation`` guarantees the safe order.
"""
from __future__ import annotations

import ipaddress
import re
import urllib.parse

#: Every TLD this pipeline will accept at the end of a real URL.
TLDS = frozenset({
    "com", "org", "net", "io", "dev", "ai", "app", "so", "cloud", "rs", "sh",
    "to", "co", "gg", "ms", "tv", "me", "info", "edu", "gov",
})

#: The narrower allow-list for a host with no ``http(s)://`` in front of it.
SCHEMELESS_TLDS = frozenset({"com", "org", "net", "io", "dev", "ai", "app", "cloud"})


def _tld_alternation(tlds: frozenset[str]) -> str:
    """Longest first, so ``co`` can never shadow ``com`` or ``cloud``."""
    return "|".join(sorted(tlds, key=lambda t: (-len(t), t)))


_URL_RE = re.compile(r"https?://[^\s)\]>\"'`,]+")
_HOST_RE = re.compile(
    rf"(?<![\w./@-])((?:[A-Za-z0-9-]+\.)+(?:{_tld_alternation(TLDS)})"
    r"(?:/[^\s)\]>\"'`,]*)?)",
    re.I,
)


def find_urls(text: str) -> list[str]:
    """Every ``http(s)://`` URL in prose."""
    return _URL_RE.findall(text)


def find_hosts(text: str) -> list[str]:
    """Every scheme-less host in prose (``developers.cloudflare.com/workers``)."""
    return _HOST_RE.findall(text)


def sanitize_url(raw: str | None) -> str | None:
    """Keep a fetchable http(s)/file URL; drop localhost, templates, and junk hosts.

    Returns ``None`` rather than raising: this runs over LLM-produced prose, where a
    malformed candidate is the normal case, not an error.
    """
    text = (raw or "").strip()
    text = re.sub(r"\s+\(.*$", "", text)
    text = text.rstrip(".,;:)\"'")
    if not text:
        return None
    if any(ch in text for ch in "{}<> \t\n"):
        return None
    if text.startswith("file:"):
        return text
    if not re.match(r"^https?://", text, re.I):
        if re.match(r"^(?:github|gitlab|bitbucket)\.com/", text, re.I):
            text = "https://" + text
        elif re.match(r"^(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:[/?#]|$)", text):
            text = "https://" + text
        else:
            return None

    try:
        parsed = urllib.parse.urlparse(text)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    if host in {"localhost", "::1"}:
        return None
    try:
        ipaddress.ip_address(host)
        return text
    except ValueError:
        pass
    if "." not in host:
        return None
    if host.startswith("_") or host.endswith(".internal") or ".svc." in host:
        return None
    tld = host.rsplit(".", 1)[-1]
    if tld not in TLDS:
        return None

    # Scheme-less promotions: reject CamelCase code tokens (Dispatchers.IO, Cargo.to)
    # and toy two-letter hosts (e.me, t3.me). An explicit https:// keeps them if the
    # TLD is valid, because the author demonstrably meant a URL.
    original = (raw or "").strip()
    if not re.match(r"^https?://", original, re.I):
        raw_host = original.split("/")[0].split(":")[0]
        if any(c.isupper() for c in raw_host):
            return None
        labels = host.split(".")
        if len(labels) < 2 or len(labels[0]) <= 2:
            return None
        if tld not in SCHEMELESS_TLDS:
            return None
    return text


#: Hosts whose two-segment path is a repository root rather than a docs page.
REPO_HOSTS = frozenset({"github.com", "gitlab.com", "bitbucket.org"})


def classify_locator(url: str) -> str:
    """``repo`` for a git remote root; ``docs`` for everything else."""
    if url.startswith("file:"):
        return "repo"
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host in REPO_HOSTS:
        parts = [p for p in parsed.path.split("/") if p]
        if parts and parts[-1].endswith(".git"):
            parts = parts[:-1] + [parts[-1][:-4]]
        if len(parts) == 2:
            return "repo"
    return "docs"
