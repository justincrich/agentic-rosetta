---
artifact: rosetta-research
tech: "Widget Platform"
tech_slug: "widget-platform"
status: "complete"
run_id: "run-fixtures-2026-11"
decoder_ring_hash: "3f7c2a91d4e8b056"
---

# Rosetta Research: Widget Platform

A neutral fixture knowledge base. It exists so the registry / delivery tests have a shared
graph with the shape a real one carries — identity, widgets, rules, API signatures,
anti-patterns, type contracts — without any product's facts living in the plugin's code.

## 9. Fact-Graph Seed (draft JSON, ≥ MEDIUM confidence only)
```json
{
  "v": {
    "pkg": "widgetctl",
    "version": "4.2.0",
    "runtime": "portable worker runtime",
    "languages": ["TypeScript", "Rust (wasm)"],
    "entry_points": "export default { fetch }",
    "coverage": "platform-wide: requests, scheduled jobs, queues and object storage"
  },
  "w": {
    "Handler": {"t": "sl", "i": "widgetctl", "note": "the default export the runtime calls per request"},
    "Store": {"t": "binding", "i": "widgetctl:store", "note": "eventually consistent key/value binding"},
    "Queue": {"t": "binding", "i": "widgetctl:queue", "note": "at-least-once delivery binding"},
    "AssetBucket": {"t": "binding", "i": "widgetctl:bucket", "note": "strongly consistent object store"}
  },
  "r": {
    "r_no_global_mutable_state": "REJECT",
    "r_no_global_mutable_state_note": "No global mutable state between requests; every request may get its own instance.",
    "r_no_blocking_io": "REJECT",
    "r_no_blocking_io_note": "No blocking I/O: the runtime has no filesystem, no child processes and no require().",
    "r_cpu_limits": "r",
    "r_cpu_limits_note": "10 ms of CPU per request by default",
    "r_memory_limit": "r",
    "r_memory_limit_note": "128 MB of memory per instance",
    "r_startup_budget": "r",
    "r_startup_budget_note": "cold start must stay under 1 s",
    "r_entry_points": "r",
    "r_entry_points_note": "export a default object; ES modules only",
    "r_store_eventual": "r",
    "r_store_eventual_note": "the key/value binding is eventually consistent (about 60 s)",
    "r_storage_choice": "nr",
    "r_storage_choice_note": "pick the binding by consistency need, not by convenience"
  },
  "a": {
    "fetch_handler": "export default { async fetch(request, env) { ... } }",
    "store_get": "await env.STORE.get(key)",
    "store_put": "await env.STORE.put(key, value)",
    "queue_send": "await env.QUEUE.send(message)",
    "bucket_get": "await env.BUCKET.get(key)",
    "bucket_put": "await env.BUCKET.put(key, body)"
  },
  "x": {
    "filesystem calls in a handler": "use the object-store binding",
    "a module-level cache variable": "put shared state in the store or a durable object",
    "an unbounded loop in a handler": "bound the work and return early",
    "the store for transactional state": "use the strongly consistent binding"
  },
  "t": {
    "Env": "the bindings object the runtime passes to the handler",
    "Handler": "export default { fetch }",
    "Message": "a queue message body (JSON)"
  }
}
```
