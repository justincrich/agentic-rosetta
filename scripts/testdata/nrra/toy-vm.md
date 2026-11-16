---
artifact: rosetta-research
tech: "Toy VM"
tech_slug: "toy-vm"
status: "complete"
run_id: "run-fixtures-2026-11"
decoder_ring_hash: "3f7c2a91d4e8b056"
---

# Rosetta Research: Toy VM

A neutral fixture knowledge base that lives with the agent that owns it
(``<agents>/toy-vm-implementer/kb/fact-graph.json``) rather than in the repo's docs directory.
It is the fixture for "an agent-local graph wins over the shared one".

## 9. Fact-Graph Seed (draft JSON, ≥ MEDIUM confidence only)
```json
{
  "v": {
    "pkg": "toyvm",
    "version": "1.4.0",
    "cli": "toyvm",
    "install": "pip install toyvm",
    "api_base": "https://api.example.com/v1"
  },
  "w": {
    "Client": {"t": "class", "i": "toyvm", "note": "the entry point for every call"},
    "Snapshot": {"t": "class", "i": "toyvm", "note": "an immutable image a machine boots from"},
    "Machine": {"t": "class", "i": "toyvm", "note": "a running instance created from a snapshot"}
  },
  "r": {
    "r_api_key_env": "REJECT",
    "r_api_key_env_note": "Never hardcode the API key; read it from the environment.",
    "r_snapshot_immutable": "REJECT",
    "r_snapshot_immutable_note": "A snapshot is immutable: boot a machine from it, never edit it.",
    "r_ttl_positive": "r",
    "r_ttl_positive_note": "Every machine carries a positive time to live",
    "r_sync_before_snapshot": "r",
    "r_sync_before_snapshot_note": "Flush pending writes before taking a snapshot",
    "r_cleanup_instances": "nr",
    "r_cleanup_instances_note": "Delete machines you created when the run ends"
  },
  "a": {
    "client_init": "client = Client()",
    "create_snapshot": "client.snapshots.create(image='toy-minimal', vcpus=1)",
    "start_machine": "machine = client.machines.start(snapshot=snapshot.id)",
    "exec": "machine.exec('uname -a')",
    "pause": "machine.pause()"
  },
  "x": {
    "creating a machine without a time to live": "set ttl= on every start",
    "reusing a snapshot as writable storage": "boot a machine and write there",
    "leaking a machine per test run": "delete it in a fixture teardown"
  },
  "t": {
    "ClientOps": "snapshots.create, machines.start, machines.list",
    "MachineOps": "exec, pause, resume, delete"
  }
}
```
