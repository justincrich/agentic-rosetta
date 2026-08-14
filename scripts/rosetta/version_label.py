#!/usr/bin/env python3
"""One version-label implementation for excerpt + registry.

Accepts both ``v.pinned_date`` (new writes) and ``v.pinned`` (legacy graphs).
"""
from __future__ import annotations

import hashlib
from typing import Any


def pin_date(graph: dict[str, Any]) -> str:
    """Return the pin date from either key, or empty string."""
    v = graph.get("v") or {}
    meta = graph.get("_meta") or {}
    if not isinstance(v, dict):
        v = {}
    if not isinstance(meta, dict):
        meta = {}
    for candidate in (
        v.get("pinned_date"),
        v.get("pinned"),
        meta.get("pinned_date"),
        meta.get("pinned"),
    ):
        if candidate:
            return str(candidate)
    return ""


def kb_edition(graph: dict[str, Any]) -> str:
    """Human KB edition (not upstream). ``unknown`` if nothing is stored."""
    v = graph.get("v") or {}
    meta = graph.get("_meta") or {}
    if not isinstance(v, dict):
        v = {}
    if not isinstance(meta, dict):
        meta = {}
    version = v.get("kb_version") or meta.get("version")
    if version is None or version == "":
        # Do not fall back to v.version — that is the *upstream* number.
        return "unknown"
    return str(version)


def format_kb_version(graph: dict[str, Any], raw_bytes: bytes) -> str:
    """``<kb_version> @ <pin> sha:<8>`` — pin half is present when either pin key exists."""
    version = kb_edition(graph)
    pinned = pin_date(graph)
    sha8 = hashlib.sha256(raw_bytes).hexdigest()[:8]
    pinned_part = f" @ {pinned}" if pinned else ""
    return f"{version}{pinned_part} sha:{sha8}"
