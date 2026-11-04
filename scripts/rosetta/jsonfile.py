#!/usr/bin/env python3
"""jsonfile.py — one home for reading, writing, and hashing JSON artifacts.

WHY THIS EXISTS
  Three modules each grew their own atomic write and their own canonical dump:

    provenance.write_json      mkdir + "<name>.tmp" + dumps(indent=2, sort_keys) + replace
    write-graph.atomic_write   mkdir + ".json.tmp"  + dumps(indent=2, sort_keys) + replace
    write-graph.graph_sha      dumps(sort_keys, separators) -> sha256
    provenance.canonical_json  dumps(sort_keys, separators) -> str

  Four implementations of "write this JSON durably" and "hash this JSON stably". The
  reason to care is not tidiness: an atomic write is what stops a half-written
  fact-graph from being read by a concurrent skill run.

THE ascii QUESTION — read before changing a digest
  ``canonical_json`` passes ``ensure_ascii=False``; ``body_digest`` passes the default
  ``True``. That is not an oversight, it is compatibility:

    - ``canonical_json`` has always backed corpus digests, which are compared against
      values already stored in on-disk ``provenance.json`` files.
    - ``body_digest`` has always backed ``_meta.assimilation.graph_sha256``. Every graph
      already stamped on disk was hashed with escaped non-ASCII, so switching it would
      make ``write-graph.py --check`` report "file was hand-edited?" on every KB that
      contains a non-ASCII character.

  Changing either one is a data migration, not a refactor.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

#: Keys that carry provenance about the graph rather than facts in it. Excluded from
#: every content digest, so re-stamping does not change the identity of the knowledge.
META_KEY = "_meta"


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def canonical_json(value: Any) -> str:
    """Sorted, compact, unescaped. The form corpus digests are computed over."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def graph_body(graph: dict) -> dict:
    """The graph without ``_meta`` — the part whose change means the knowledge changed."""
    return {k: v for k, v in graph.items() if k != META_KEY}


def body_digest(graph: dict) -> str:
    """Stable content hash of a graph, ``_meta`` excluded. See the ascii note above."""
    return sha256_bytes(
        json.dumps(graph_body(graph), sort_keys=True, separators=(",", ":")).encode()
    )


def read_json(path: Path) -> Any:
    """Strict read: raises ``FileNotFoundError`` / ``json.JSONDecodeError``.

    Callers that want a tolerant read add their own fallback. Swallowing the error here
    is how ``registry.py`` ended up unable to tell a broken graph from a missing one.
    """
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _atomic_text(path: Path, text: str) -> None:
    """Write ``text`` to a sibling temp file, fsync, then rename over ``path``.

    The rename is the atomic part: a reader sees either the whole previous file or the
    whole new one, never a truncated graph.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        # Never leave a stray temp file for a later glob to trip over.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_json(path: Path, value: Any, *, indent: int | None = 2) -> None:
    """Serialise with sorted keys and write atomically."""
    _atomic_text(path, json.dumps(value, indent=indent, sort_keys=True) + "\n")


def write_text(path: Path, text: str) -> None:
    """Atomic text write, for Markdown companions (README, MIGRATION)."""
    _atomic_text(path, text)
