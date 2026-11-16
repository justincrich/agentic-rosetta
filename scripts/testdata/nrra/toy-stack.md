---
artifact: rosetta-research
tech: "Toy Stack"
tech_slug: "toy-stack"
status: "complete"
run_id: "run-fixtures-2026-11"
decoder_ring_hash: "3f7c2a91d4e8b056"
---

# Rosetta Research: Toy Stack

A neutral fixture whose graph is created with ``--composed-with core-lib``: its body carries a
copy of the other graph's facts under ``composed["core-lib"]`` and mirrors that edition in
``_meta.composed_with``. It is the committed shape of a borrowed KB.

## 9. Fact-Graph Seed (draft JSON, ≥ MEDIUM confidence only)
```json
{
  "v": {
    "pkg": "toy-stack",
    "version": "0.9.0",
    "runtime": "the widget platform plus the core library"
  },
  "w": {
    "Stack": {"t": "container", "i": "toy-stack", "note": "the two components wired together"}
  },
  "r": {
    "r_no_reimplementation": "REJECT",
    "r_no_reimplementation_note": "Call the core library instead of re-implementing its rules.",
    "r_pin_both_editions": "r",
    "r_pin_both_editions_note": "Record both editions so a stale copy is visible"
  },
  "a": {
    "wire": "Stack(core=corelib, platform=widgetctl)",
    "run": "stack.run(request)"
  },
  "x": {
    "copying a core rule into this graph": "compose it by value instead",
    "editing the borrowed facts": "refresh from the original"
  },
  "t": {
    "Stack": "the composed container"
  }
}
```
