---
artifact: rosetta-research
tech: "Core Library"
tech_slug: "core-lib"
status: "complete"
run_id: "run-fixtures-2026-11"
decoder_ring_hash: "3f7c2a91d4e8b056"
---

# Rosetta Research: Core Library

A neutral fixture knowledge base for the second graph: it is what a borrower composes from and
what the multi-graph render merges. Its facts are deliberately about a library, not a platform,
so a merge cannot look right by accident.

## 9. Fact-Graph Seed (draft JSON, ≥ MEDIUM confidence only)
```json
{
  "v": {
    "pkg": "corelib",
    "version": "2.3.1",
    "edition": "2024",
    "toolchain": "stable"
  },
  "w": {
    "Result": {"t": "type", "i": "corelib", "note": "a success-or-failure value"},
    "Option": {"t": "type", "i": "corelib", "note": "a present-or-absent value"},
    "Channel": {"t": "type", "i": "corelib", "note": "a bounded hand-off between tasks"}
  },
  "r": {
    "r_no_unwrap_in_library": "REJECT",
    "r_no_unwrap_in_library_note": "Never unwrap a fallible value in library code; return the error.",
    "r_no_panic_in_flow": "REJECT",
    "r_no_panic_in_flow_note": "A panic is not control flow; model the failure in the return type.",
    "r_prefer_result": "r",
    "r_prefer_result_note": "Return Result for anything that can fail",
    "r_no_wildcard_match": "r",
    "r_no_wildcard_match_note": "Match every variant so a new one breaks the build, not the runtime",
    "r_document_public_items": "r",
    "r_document_public_items_note": "Every public item carries a doc comment",
    "r_format_before_commit": "nr",
    "r_format_before_commit_note": "Run the formatter before committing"
  },
  "a": {
    "collect_results": "items.into_iter().map(fallible).collect::<Result<Vec<_>, _>>()",
    "unwrap_or_else": "value.unwrap_or_else(|| default())",
    "map_err": "fallible().map_err(Error::from)",
    "ok_or": "option.ok_or(Error::Missing)?",
    "match_option": "match option { Some(v) => v, None => return Err(Error::Missing) }"
  },
  "x": {
    "unwrap() in library code": "return the error",
    "a string as the error type": "use a typed error",
    "indexing a slice": "use get() and handle None",
    "clone() to silence the borrow checker": "restructure the ownership instead"
  },
  "t": {
    "Clone": "value semantics for the type",
    "Debug": "a printable diagnostic form",
    "Display": "the user-facing message",
    "Default": "a documented neutral value"
  }
}
```
