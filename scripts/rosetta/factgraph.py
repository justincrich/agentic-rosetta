#!/usr/bin/env python3
"""factgraph.py — the shape of a fact-graph, in one place.

WHY THIS EXISTS
  The same shape was asserted three different ways:

    write-graph.validate_graph()   58 hand-written isinstance/.get checks
    provenance.py, generate_excerpt.py  ad-hoc .get() chains and per-call guards
    registry.py                    `except Exception: return "unknown"` (silent)

  A caller could not tell from the outside what a valid graph was. Now it can: this
  module is the answer, and the pydantic model is the readable form of it.

WHY THE GATE IS STILL A PORT, NOT A NAIVE @model_validator
  ``validate()`` below reproduces the existing rules EXACTLY, including the ones that are
  deliberately permissive:

    - a ``w`` node that is not a dict is accepted (only dict nodes are checked for t/i)
    - ``t`` may be absent or null, and is normalised to ``{}``

  Expressing those as a strict pydantic field type would REJECT graphs that are valid
  today. Tightening validation is a behaviour change and belongs in its own change with
  its own migration, not in a structural refactor. So:

    FactGraph  typed read access for code that wants fields, not dicts
    validate() the gate every writer calls before writing

  Both live here because they describe one decision: what a fact-graph is.

THE SENTINEL RULE
  A graph value must never contain the literal ``rosetta-kb:end``. ``embed-graph.sh``
  extracts the embedded region by sentinel, so a smuggled sentinel truncates the agent
  body and wedges the drift guard into a re-embed loop. This is checked on the whole
  serialised graph, because the sentinel can hide in any section.
"""
from __future__ import annotations

import json
from enum import Enum
from typing import Any

try:
    # pydantic is confined to scripts/rosetta/. The delivery renderer and the storage
    # resolver ship into harness directories as bare .py files and stay stdlib-only, so
    # they must never import this module.
    from pydantic import BaseModel, ConfigDict, Field
except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
    raise SystemExit(
        "factgraph: pydantic is required but not installed.\n"
        "  Install it with:  pip install --user 'pydantic>=2.6,<3'\n"
        "  (declared in pyproject.toml; only scripts/ needs it, never assets/)\n"
        f"  Underlying error: {exc}"
    ) from exc

#: Verdicts a rule in section ``r`` may carry.
VERDICTS = frozenset({"r", "nr", "REJECT"})

#: Present in a graph that must never be written: it would truncate the embed region.
END_SENTINEL = "rosetta-kb:end"

#: Graph sections, in writer iteration order.
SECTIONS = ("v", "w", "r", "a", "x", "t")


class SchemaError(Exception):
    """A graph that must not be written. ``check`` names the failed gate."""

    def __init__(self, check: str, detail: str):
        super().__init__(f"write-graph: FAIL {check}: {detail}")
        self.check = check
        self.detail = detail


def _fail(detail: str) -> None:
    raise SchemaError("schema", detail)


# ─────────────────────────── typed read access ───────────────────────────

class WNode(BaseModel):
    """A component / binding. ``t`` is the kind, ``i`` the instance identity."""

    model_config = ConfigDict(extra="allow")

    t: str
    i: str
    note: str | None = None


class Rule(BaseModel):
    """A rule in section ``r``: a verdict plus an optional note."""

    model_config = ConfigDict(extra="allow")

    verdict: str
    note: str | None = None


class FactGraph(BaseModel):
    """Typed view of a fact-graph. Permissive on extras by design — see the module docstring.

    Sections are kept as ``Any``-valued dicts rather than fully-modelled trees because the
    facts inside them are open-ended by nature (an API signature is a string, a type
    contract may be a string or a nested object). The value of the model is that ``v`` is
    no longer a mystery dict: its well-known fields are named and typed.
    """

    model_config = ConfigDict(extra="allow")

    v: dict[str, Any] = Field(default_factory=dict)
    w: dict[str, Any] = Field(default_factory=dict)
    r: dict[str, Any] = Field(default_factory=dict)
    a: dict[str, Any] = Field(default_factory=dict)
    x: dict[str, Any] = Field(default_factory=dict)
    t: dict[str, Any] = Field(default_factory=dict)

    @property
    def kb_version(self) -> str:
        return str(self.v.get("kb_version") or "")

    @property
    def upstream_version(self) -> Any:
        return self.v.get("version")

    def rules_by_verdict(self, verdict: str) -> dict[str, Any]:
        """Rule keys whose verdict matches, ignoring ``*_note`` companions."""
        return {
            key: value
            for key, value in self.r.items()
            if not key.endswith("_note") and verdict_of(value) == verdict
        }


def verdict_of(value: Any) -> str | None:
    """A rule value may carry its verdict inline (``"REJECT"``) or as a dict field."""
    if isinstance(value, dict):
        found = value.get("verdict")
        return str(found) if found is not None else None
    if isinstance(value, str):
        return value
    return None


def parse(graph: dict) -> FactGraph:
    """Typed view of an already-validated graph."""
    return FactGraph.model_validate(graph)


# ─────────────────────────────── the gate ───────────────────────────────

def validate(graph: dict) -> None:
    """Raise ``SchemaError`` unless ``graph`` may be written. Normalises ``t`` to ``{}``.

    Called by every writer before it persists. A pass here is the only permission to
    write a fact-graph.
    """
    if not isinstance(graph, dict):
        _fail("graph is not an object")

    if END_SENTINEL in json.dumps(graph):
        _fail(f"graph contains literal {END_SENTINEL} (would poison embed)")

    _validate_v(graph)
    _validate_w(graph)
    _validate_r(graph)
    _validate_x(graph)
    _validate_a(graph)
    _validate_t(graph)


def _validate_v(graph: dict) -> None:
    v = graph.get("v")
    if not isinstance(v, dict):
        _fail("v missing")
    if not v.get("kb_version"):
        _fail("v needs kb_version")
    if v.get("version") == "0.0.0":
        _fail('v.version must not be the fake "0.0.0"')


def _validate_w(graph: dict) -> None:
    w = graph.get("w") or {}
    if not isinstance(w, dict):
        _fail("w must be an object")
    for name, node in w.items():
        if isinstance(node, dict) and not (node.get("t") and node.get("i")):
            _fail(f"w.{name} needs t + i")


def _validate_r(graph: dict) -> None:
    r = graph.get("r") or {}
    if not isinstance(r, dict):
        _fail("r must be an object")
    for key, value in r.items():
        if key.endswith("_note"):
            continue
        verdict = verdict_of(value)
        if verdict not in VERDICTS:
            _fail(f"r.{key} verdict {verdict!r} not in {sorted(VERDICTS)}")


def _validate_x(graph: dict) -> None:
    x = graph.get("x") or {}
    if not isinstance(x, dict):
        _fail("x must be an object")
    for key, value in x.items():
        if isinstance(value, dict):
            if not (value.get("wrong") or value.get("right") or value.get("to")):
                _fail(f"x.{key} pair incomplete")
        elif not isinstance(value, str) or not value:
            _fail(f"x.{key} empty")


def _validate_a(graph: dict) -> None:
    a = graph.get("a") or {}
    if not isinstance(a, dict):
        _fail("a must be an object")
    for key, value in a.items():
        if not isinstance(value, str) or not value.strip():
            _fail(f"a.{key} must be a non-empty string")


def _validate_t(graph: dict) -> None:
    t = graph.get("t")
    if t is None:
        # Normalisation, not validation: an absent type-contract section is written as {}.
        graph["t"] = {}
        return
    if not isinstance(t, dict):
        _fail("t must be an object")
    for key, value in t.items():
        if isinstance(value, str):
            if not value.strip():
                _fail(f"t.{key} must be a non-empty string")
        elif isinstance(value, dict):
            if not value:
                _fail(f"t.{key} empty")
        else:
            _fail(f"t.{key} must be a string or object")
