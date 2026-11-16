#!/usr/bin/env python3
"""sync_agents.py — mirror a published Rosetta snapshot onto this device.

WHY THIS EXISTS
  A development device needs the Claude Code and Codex agent files, and today the only
  way to get them is a full consumer checkout: ``~/.claude/agents`` is a symlink into
  that checkout and the projector hardcodes its path. The hosted MCP service publishes the
  same projection as static assets (``manifest.json`` + ``files.json`` + ``targets/<t>/…``),
  so a device can hold the files without holding the checkout.

WHAT A RUN DOES
  1. Refuses anything but an https base URL (exit 2) and refuses to leave the token
     behind: it is read from ``$ROSETTA_MCP_TOKEN``, sent as a bearer header, never
     printed, and never copied onto a redirect (a redirect is followed *without* it).
  2. Reads ``manifest.json`` and the snapshot inventory (``files.json``, served as the
     snapshot asset ``/files/files.json``) and validates every path in both: relative,
     no ``..``, not absolute, no empty / ``.`` / backslash / NUL segment.
  3. Refuses to write through a symlink. The destination and every existing path under it
     must be a real directory or file; a symlinked destination is moved aside only with
     ``--adopt``, to ``<dest>.bak-<timestamp>``.
  4. Verifies each file's sha256 *before* writing it (download → hash → compare →
     atomic replace). A mismatch refuses the run and leaves the device untouched.
  5. Mirrors the snapshot's shared documents (``manifest.json``'s ``share`` block) into
     ``$HOME/.rosetta/share`` whenever the snapshot publishes them. This is not optional and
     not target-scoped: the prompts cite those files, so a run that skipped them would hand the
     device a prompt pointing at nothing.
  6. Repeats, or reports, the harness side effects the snapshot declares per target: the device
     sync applies ``opencode-agent-model-unpin`` to the device's own ``opencode.json`` (the same
     key removal agentshare does), and reports every ``not-applied`` effect with its reason. A
     side effect is never silently ignored.
  7. Never deletes a file it did not create: the paths it wrote are recorded in
     ``~/.rosetta/sync-state.json`` and only a recorded path that is gone from the new
     snapshot *and* still holds the recorded bytes is removed.

EXIT CODES
  0  synced (or already current)      1  transport/IO failure
  2  refused: bad URL, bad path, bad hash, symlink, missing token

Usage:
    ROSETTA_MCP_TOKEN=… python3 scripts/kb-delivery/sync_agents.py \\
        --url https://rosetta-kb-mcp.justin-d51.workers.dev
    … --targets claude            # one tree
    … --adopt                     # move a symlinked destination aside, then write

This tool never talks to a consumer checkout and never runs the projector: the published
snapshot is the only input, which is what makes it usable on a brain-less device.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2

DEFAULT_URL = "https://rosetta-kb-mcp.justin-d51.workers.dev"
TOKEN_ENV = "ROSETTA_MCP_TOKEN"
MANIFEST_PATH = "/manifest.json"
# ``files.json`` is a metadata file at the snapshot root; the Worker serves the snapshot
# under ``/files/<snapshot path>``, so the inventory is the asset ``files.json``.
INVENTORY_PATHS = ("/files/files.json", "/files.json")
STATE_RELPATH = Path(".rosetta") / "sync-state.json"
STATE_SCHEMA = 1
SHARE_STATE_KEY = "share"
READ_TIMEOUT_SECONDS = 120
DOWNLOAD_WORKERS = 8
SHA256_RE = re.compile(r"[0-9a-f]{64}")


class Refused(RuntimeError):
    """A policy or integrity refusal: nothing is written, exit code 2."""


class Failed(RuntimeError):
    """A transport or filesystem failure: exit code 1."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ── the HTTP seam ──────────────────────────────────────────────────────


class _NoTokenRedirect(urllib.request.HTTPRedirectHandler):
    """Follow a redirect, but never carry the bearer token onto it.

    urllib's default handler copies every header of the original request onto the new
    one, which is how a credential leaks to a host the caller did not choose. This handler
    strips ``Authorization`` from both header maps and refuses a non-https target: the
    device proved its identity to one origin and that proof does not travel.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, N802
        target = urllib.parse.urlsplit(newurl)
        if target.scheme.lower() != "https":
            raise Refused(
                f"refusing a {target.scheme or 'relative'} redirect to {newurl!r}: this tool syncs over https only"
            )
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        for key in [name for name in new.headers if name.lower() == "authorization"]:
            del new.headers[key]
        for key in [name for name in new.unredirected_hdrs if name.lower() == "authorization"]:
            del new.unredirected_hdrs[key]
        print(f"[sync] following redirect without the bearer token: {newurl}", file=sys.stderr)
        return new


class SnapshotClient:
    """Reads the published snapshot. Holds the token; never renders it."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url
        self._token = token
        self._local = threading.local()

    def _opener(self) -> urllib.request.OpenerDirector:
        # One opener per thread: a handler chain carries per-connection state, and the
        # files of a target are fetched concurrently.
        opener = getattr(self._local, "opener", None)
        if opener is None:
            opener = urllib.request.build_opener(_NoTokenRedirect())
            self._local.opener = opener
        return opener

    def get(self, path: str) -> bytes:
        if not path.startswith("/"):
            path = "/" + path
        url = self.base_url + path
        request = urllib.request.Request(url, method="GET")
        request.add_header("Authorization", f"Bearer {self._token}")
        request.add_header("Accept", "application/json, */*")
        request.add_header("User-Agent", "rosetta-sync/1")
        try:
            with self._opener().open(request, timeout=READ_TIMEOUT_SECONDS) as response:
                return response.read()
        except Refused:
            raise
        except urllib.error.HTTPError as error:
            # The credential is never echoed: only the status and URL are named.
            if error.code in (401, 403):
                raise Refused(
                    f"{url}: HTTP {error.code} — the service rejected the bearer token; "
                    f"check ${TOKEN_ENV} (the value is never printed by this tool)"
                ) from None
            if error.code == 404:
                raise Failed(f"{url}: HTTP 404 — the snapshot does not publish this file") from None
            raise Failed(f"{url}: HTTP {error.code}") from None
        except urllib.error.URLError as error:
            raise Failed(f"{url}: {error.reason}") from None
        except TimeoutError:
            raise Failed(f"{url}: timed out after {READ_TIMEOUT_SECONDS}s") from None

    def get_json(self, path: str) -> object:
        body = self.get(path)
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise Failed(f"{path}: not readable as JSON ({error})") from None


def normalize_base_url(raw: str) -> str:
    """``https://host`` and nothing else: no userinfo, no query, no fragment."""
    parsed = urllib.parse.urlsplit(raw.strip())
    if parsed.scheme.lower() != "https":
        raise Refused(
            f"--url {raw!r}: this tool accepts https URLs only "
            f"(got scheme {parsed.scheme or '(none)'}); a bearer token never travels in the clear"
        )
    if not parsed.netloc or parsed.hostname is None:
        raise Refused(f"--url {raw!r}: no host")
    if parsed.username or parsed.password:
        raise Refused(f"--url {raw!r}: credentials in the URL are refused; export ${TOKEN_ENV} instead")
    if parsed.query or parsed.fragment:
        raise Refused(f"--url {raw!r}: a query string or fragment is not part of a service URL")
    return f"https://{parsed.netloc}{parsed.path.rstrip('/')}"


# ── path validation ────────────────────────────────────────────────────


def validate_relative_path(raw: object, *, label: str) -> tuple[str, ...]:
    """Split a snapshot path, or refuse it.

    Every path the service names is data, not a destination: it must stay inside the tree
    this tool owns. Absolute paths, ``..`` (in any spelling), empty segments, ``.``, a
    backslash and NUL are all refused before anything is downloaded.
    """
    if not isinstance(raw, str) or raw == "":
        raise Refused(f"{label}: expected a non-empty relative path, got {raw!r}")
    if raw.startswith("/") or raw.startswith("\\"):
        raise Refused(f"{label}: {raw!r} is absolute; snapshot paths are relative")
    if "\x00" in raw:
        raise Refused(f"{label}: {raw!r} contains a NUL byte")
    if "\\" in raw:
        raise Refused(f"{label}: {raw!r} contains a backslash")
    parts = tuple(raw.split("/"))
    for part in parts:
        if part in ("", ".", ".."):
            raise Refused(f"{label}: {raw!r} has a {part!r} segment and can escape the target")
    return parts


def join_under(root: Path, parts: tuple[str, ...], *, label: str) -> Path:
    """``root`` joined with validated parts, still checked to stay under ``root``."""
    candidate = root.joinpath(*parts)
    try:
        resolved_root = root.resolve(strict=False)
        resolved = candidate.resolve(strict=False)
        common = Path(os.path.commonpath([str(resolved_root), str(resolved)]))
    except (OSError, ValueError) as error:
        raise Refused(f"{label}: {candidate} could not be resolved ({error})") from None
    if common != resolved_root:
        raise Refused(f"{label}: {candidate} lands outside {resolved_root}")
    return candidate


# ── manifest + inventory ───────────────────────────────────────────────


def _field(entry: object, key: str, *, label: str) -> object:
    if not isinstance(entry, dict):
        raise Refused(f"{label}: expected an object, got {type(entry).__name__}")
    if key not in entry:
        raise Refused(f"{label}: missing {key!r}")
    return entry[key]


def load_manifest(client: SnapshotClient) -> dict:
    manifest = client.get_json(MANIFEST_PATH)
    if not isinstance(manifest, dict):
        raise Refused("manifest.json: not a JSON object")
    for key in ("snapshot_id", "targets", "agents", "kbs"):
        if key not in manifest:
            raise Refused(f"manifest.json: missing {key!r}")
    if not isinstance(manifest["targets"], dict) or not manifest["targets"]:
        raise Refused("manifest.json: 'targets' must be a non-empty object")
    for name, entry in manifest["targets"].items():
        if not isinstance(name, str) or name in ("", ".", "..") or "/" in name:
            raise Refused(f"manifest.json: target name {name!r} is not a plain name")
        validate_relative_path(
            _field(entry, "dest", label=f"manifest.json targets.{name}"),
            label=f"manifest.json targets.{name}.dest",
        )
    for index, agent in enumerate(manifest["agents"]):
        validate_relative_path(
            _field(agent, "path", label=f"manifest.json agents[{index}]"),
            label=f"manifest.json agents[{index}].path",
        )
    for index, kb in enumerate(manifest["kbs"]):
        validate_relative_path(
            _field(kb, "path", label=f"manifest.json kbs[{index}]"),
            label=f"manifest.json kbs[{index}].path",
        )
    if "share" in manifest:
        share = manifest["share"]
        validate_relative_path(_field(share, "dest", label="manifest.json share"), label="manifest.json share.dest")
        prefix = _field(share, "prefix", label="manifest.json share")
        if not isinstance(prefix, str) or not prefix.endswith("/"):
            raise Refused(f"manifest.json share.prefix {prefix!r} must be a directory prefix ending in '/'")
        validate_relative_path(prefix.rstrip("/"), label="manifest.json share.prefix")
    if "targets" in manifest:
        for name, entry in manifest["targets"].items():
            effects = entry.get("side_effects") if isinstance(entry, dict) else None
            if effects is None:
                continue
            if not isinstance(effects, list):
                raise Refused(f"manifest.json targets.{name}.side_effects: expected a list")
            for index, effect in enumerate(effects):
                label = f"manifest.json targets.{name}.side_effects[{index}]"
                validate_relative_path(_field(effect, "path", label=label), label=f"{label}.path")
                handling = _field(effect, "handling", label=label)
                if handling not in ("sync-applies", "not-applied"):
                    raise Refused(
                        f"{label}.handling {handling!r} is neither 'sync-applies' nor 'not-applied'"
                    )
    return manifest


def load_inventory(client: SnapshotClient, manifest: dict) -> dict[str, dict]:
    """``{snapshot path: {"sha256": …, "bytes": …}}``, validated, matching the manifest."""
    last: Failed | None = None
    for path in INVENTORY_PATHS:
        try:
            document = client.get_json(path)
            break
        except Failed as error:
            last = error
    else:  # pragma: no cover - both paths failed
        raise Failed(f"the snapshot publishes no inventory ({last})")
    if not isinstance(document, dict) or not isinstance(document.get("files"), dict):
        raise Refused("files.json: not an object with a 'files' inventory")
    recorded_id = document.get("snapshot_id")
    if recorded_id != manifest["snapshot_id"]:
        raise Refused(
            f"files.json records snapshot {recorded_id!r} but manifest.json is {manifest['snapshot_id']!r}; "
            "the service is serving two versions — retry, and if it persists the publish is broken"
        )
    inventory: dict[str, dict] = {}
    for raw_path, entry in document["files"].items():
        parts = validate_relative_path(raw_path, label="files.json")
        digest = _field(entry, "sha256", label=f"files.json {raw_path}")
        size = _field(entry, "bytes", label=f"files.json {raw_path}")
        if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
            raise Refused(f"files.json {raw_path}: sha256 {digest!r} is not a lower-case hex digest")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise Refused(f"files.json {raw_path}: bytes {size!r} is not a size")
        inventory["/".join(parts)] = {"sha256": digest, "bytes": size}
    return inventory


def plan_for_targets(manifest: dict, inventory: dict[str, dict], requested: list[str] | None) -> dict[str, dict]:
    """For each published target, the files to write and where they land."""
    published = manifest["targets"]
    names = requested if requested else sorted(published)
    unknown = [name for name in names if name not in published]
    if unknown:
        raise Refused(
            f"--targets names {unknown} but the snapshot publishes {sorted(published)}"
        )
    plan: dict[str, dict] = {}
    for name in names:
        dest = published[name]["dest"]
        prefix = f"targets/{name}/"
        files: dict[str, dict] = {}
        for asset_path, entry in inventory.items():
            if not asset_path.startswith(prefix):
                continue
            relative = asset_path[len(prefix) :]
            validate_relative_path(relative, label=f"targets/{name} file")
            files[relative] = {
                "asset": asset_path,
                "sha256": entry["sha256"],
                "bytes": entry["bytes"],
            }
        if not files:
            raise Refused(
                f"the snapshot publishes target {name!r} (dest {dest}) but no files under {prefix}"
            )
        declared = published[name].get("files")
        if isinstance(declared, int) and not isinstance(declared, bool) and declared != len(files):
            raise Refused(
                f"the snapshot says target {name!r} has {declared} files but the inventory lists "
                f"{len(files)}; the publish is inconsistent"
            )
        plan[name] = {"dest": dest, "files": files}
    return plan


def plan_share(manifest: dict, inventory: dict[str, dict]) -> dict | None:
    """The snapshot's shared documents: ``docs/…`` assets and where they land on the device."""
    block = manifest.get("share")
    if block is None:
        return None
    dest = block["dest"]
    prefix = block["prefix"]
    files: dict[str, dict] = {}
    for asset_path, entry in inventory.items():
        if not asset_path.startswith(prefix):
            continue
        relative = asset_path[len(prefix) :]
        validate_relative_path(relative, label="share file")
        files[relative] = {"asset": asset_path, "sha256": entry["sha256"], "bytes": entry["bytes"]}
    if not files:
        raise Refused(
            f"the manifest publishes a share section ({prefix} -> {dest}) but the inventory lists "
            "no file under it; the publish is inconsistent"
        )
    declared = block.get("files")
    if isinstance(declared, int) and not isinstance(declared, bool) and declared != len(files):
        raise Refused(
            f"the snapshot says the share section has {declared} files but the inventory lists "
            f"{len(files)}; the publish is inconsistent"
        )
    return {"dest": dest, "files": files}


# ── the device tree ────────────────────────────────────────────────────


def _symlink_at(path: Path) -> bool:
    return os.path.islink(path)


def check_destination(home: Path, dest: str, *, adopt: bool) -> tuple[Path, str | None]:
    """Resolve ``dest`` under ``home`` and prove no component is a symlink.

    Returns ``(destination, adopted_backup_or_None)``. The walk starts at ``home`` (the one
    directory the caller owns): a component *above* it is the environment's business, while
    anything from ``home`` down to the destination and every existing entry under the
    destination must be a real path, or a write could land outside the tree.
    """
    dest_parts = validate_relative_path(dest, label="manifest target dest")
    destination = home.joinpath(*dest_parts)
    current = home
    for index, part in enumerate(dest_parts):
        current = current / part
        if not _symlink_at(current):
            continue
        is_destination = index == len(dest_parts) - 1
        if not (is_destination and adopt):
            raise Refused(
                f"{current} is a symlink; refusing to write through it. "
                + ("Re-run with --adopt to move this one aside." if is_destination else "Move it aside yourself.")
            )
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = current.with_name(f"{current.name}.bak-{stamp}")
        if backup.exists() or _symlink_at(backup):
            raise Refused(f"cannot adopt {current}: the backup path {backup} already exists")
        os.rename(current, backup)
        print(f"[sync] adopted {current} -> {backup}", file=sys.stderr)
        return destination, backup
    if destination.exists() and not destination.is_dir():
        raise Refused(f"{destination} exists and is not a directory")
    if destination.is_dir():
        for dirpath, dirnames, filenames in os.walk(destination, followlinks=False):
            here = Path(dirpath)
            for name in dirnames + filenames:
                victim = here / name
                if _symlink_at(victim):
                    raise Refused(
                        f"{victim} is a symlink inside the target; refusing to write through it "
                        "(only the destination itself can be adopted)"
                    )
    return destination, None


def read_state(path: Path) -> dict:
    if not path.is_file():
        return {"schema": STATE_SCHEMA, "targets": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": STATE_SCHEMA, "targets": {}}
    if not isinstance(state, dict) or not isinstance(state.get("targets"), dict):
        return {"schema": STATE_SCHEMA, "targets": {}}
    return state


def write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=1, sort_keys=True) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == payload:
        return
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".sync-state-", suffix=".tmp")
    with os.fdopen(handle, "w", encoding="utf-8") as sink:
        sink.write(payload)
    os.replace(temporary, path)


def write_file_atomically(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_dir():
        raise Refused(f"{path} is a directory where the snapshot publishes a file")
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".sync-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as sink:
            sink.write(data)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def prune_target(destination: Path, previous: dict, planned: dict[str, dict]) -> int:
    """Remove files this tool wrote that the new snapshot no longer publishes.

    A file recorded in the state file is only removed while it still holds the bytes this
    tool wrote: once a person edits or replaces it, it is theirs and stays.
    """
    pruned = 0
    for relative, recorded_sha in sorted(previous.get("files", {}).items()):
        if relative in planned:
            continue
        try:
            parts = validate_relative_path(relative, label="recorded state path")
        except Refused:
            continue
        victim = join_under(destination, parts, label="recorded state path")
        if _symlink_at(victim) or not victim.is_file():
            continue
        if sha256_file(victim) != recorded_sha:
            continue
        victim.unlink()
        pruned += 1
        parent = victim.parent
        while parent != destination and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    return pruned


def _agent_names_for_target(plan: dict) -> set[str]:
    """The agent names a target's planned files belong to (``<name>.md`` / ``<name>/...``)."""
    names: set[str] = set()
    for relative in plan["files"]:
        if "/" in relative:
            names.add(relative.split("/", 1)[0])
        else:
            names.add(relative.rsplit(".", 1)[0])
    return names


def unpin_opencode_models(*, home: Path, path: str, names: set[str]) -> dict:
    """Remove ``agent.<name>.model`` from the device's ``opencode.json``.

    This is agentshare's OpenCode side effect, repeated on the device: OpenCode's inherit
    signal is key absence, so a pinned model in the config beats the profile that has none.
    Only the published agents are touched, an entry left empty is dropped (as agentshare does),
    and a file that is not readable JSON is left exactly as it is and reported.
    """
    result = {"applied": False, "pins_removed": 0, "detail": ""}
    parts = validate_relative_path(path, label="side effect path")
    config = join_under(home, parts, label="side effect path")
    if _symlink_at(config):
        result["detail"] = f"{config} is a symlink; refusing to rewrite it"
        return result
    if not config.is_file():
        result["applied"] = True
        result["detail"] = f"{path}: absent on this device, so there is no agent model pin to remove"
        return result
    try:
        document = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        result["detail"] = f"{path}: not readable as JSON ({error}); its agent model pins were left alone"
        return result
    if not isinstance(document, dict):
        result["detail"] = f"{path}: not a JSON object; its agent model pins were left alone"
        return result
    agents = document.get("agent")
    if not isinstance(agents, dict):
        result["applied"] = True
        result["detail"] = f"{path}: no agent section, so there is no agent model pin to remove"
        return result
    pinned = sorted(name for name in names if isinstance(agents.get(name), dict) and "model" in agents[name])
    if not pinned:
        result["applied"] = True
        result["detail"] = f"{path}: no published agent has a model pin"
        return result
    for name in pinned:
        del agents[name]["model"]
        if not agents[name]:
            del agents[name]
    # Same serialisation agentshare writes, so a device and a local projector run agree byte for byte.
    write_file_atomically(config, json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8"))
    result["applied"] = True
    result["pins_removed"] = len(pinned)
    result["detail"] = f"{path}: removed agent.<name>.model for {len(pinned)} agent(s)"
    return result


def apply_side_effects(*, home: Path, target: str, plan: dict, entry: dict) -> list[dict]:
    """Repeat or report every harness side effect the snapshot declares for this target."""
    records: list[dict] = []
    for effect in entry.get("side_effects") or []:
        record = {
            "name": effect["name"],
            "path": effect["path"],
            "handling": effect["handling"],
            "applied": False,
            "pins_removed": 0,
            "detail": str(effect.get("reason") or ""),
        }
        if effect["handling"] == "sync-applies":
            if effect["name"] == "opencode-agent-model-unpin":
                record.update(
                    unpin_opencode_models(home=home, path=effect["path"], names=_agent_names_for_target(plan))
                )
            else:
                record["detail"] = (
                    f"{effect['name']}: this sync has no implementation for it, so it was not applied"
                )
        records.append(record)
        state = "applied" if record["applied"] else "NOT applied"
        print(f"[sync] {target} side effect {effect['name']}: {state} — {record['detail']}", file=sys.stderr)
    return records


def sync_target(
    *,
    home: Path,
    name: str,
    plan: dict,
    target_entry: dict,
    snapshot_id: str,
    state: dict,
    client: SnapshotClient,
    adopt: bool,
) -> dict:
    destination, adopted = check_destination(home, plan["dest"], adopt=adopt)
    recorded = state["targets"].get(name)
    previous = recorded if isinstance(recorded, dict) else {}
    # A destination recorded under a different dest is a different tree: forget it rather
    # than prune a path this run does not own.
    if previous.get("dest") != plan["dest"]:
        previous = {}

    written = 0
    skipped = 0
    written_now: dict[str, str] = {}
    to_fetch: list[tuple[str, dict]] = []
    for relative, entry in sorted(plan["files"].items()):
        parts = validate_relative_path(relative, label=f"{name} file")
        target = join_under(destination, parts, label=f"{name} file")
        expected = entry["sha256"]
        if target.is_file() and not _symlink_at(target) and sha256_file(target) == expected:
            skipped += 1
            written_now[relative] = expected
            continue
        to_fetch.append((relative, entry))

    # Download concurrently, then verify and write each one in this thread. A file is only
    # written after its sha256 and its size match what the snapshot published, so a
    # truncated or swapped body never reaches the device.
    if to_fetch:
        with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS, thread_name_prefix="sync-fetch") as pool:
            futures: dict[Future[bytes], tuple[str, dict]] = {
                pool.submit(client.get, "/files/" + entry["asset"]): (relative, entry)
                for relative, entry in to_fetch
            }
            try:
                for future in as_completed(futures):
                    relative, entry = futures[future]
                    data = future.result()
                    digest = sha256_bytes(data)
                    if digest != entry["sha256"]:
                        raise Refused(
                            f"{entry['asset']}: sha256 {digest} does not match the published "
                            f"{entry['sha256']}; refusing to write it"
                        )
                    if len(data) != entry["bytes"]:
                        raise Refused(
                            f"{entry['asset']}: {len(data)} bytes but the snapshot publishes "
                            f"{entry['bytes']}; refusing to write it"
                        )
                    parts = validate_relative_path(relative, label=f"{name} file")
                    write_file_atomically(join_under(destination, parts, label=f"{name} file"), data)
                    written += 1
                    written_now[relative] = entry["sha256"]
            except BaseException:
                for pending in futures:
                    pending.cancel()
                raise

    pruned = prune_target(destination, previous, plan["files"])
    state["targets"][name] = {
        "dest": plan["dest"],
        "snapshot_id": snapshot_id,
        "files": written_now,
    }
    side_effects = apply_side_effects(
        home=home, target=name, plan=plan, entry=target_entry
    )
    return {
        "dest": plan["dest"],
        "written": written,
        "skipped": skipped,
        "pruned": pruned,
        "files": len(plan["files"]),
        "adopted": str(adopted) if adopted else None,
        "side_effects": side_effects,
    }


# ── entry point ────────────────────────────────────────────────────────


def run(args: argparse.Namespace) -> int:
    token = os.environ.get(TOKEN_ENV, "")
    if not token.strip():
        raise Refused(
            f"${TOKEN_ENV} is not set; export the service's shared bearer token "
            "(never pass it on the command line)"
        )
    base_url = normalize_base_url(args.url or DEFAULT_URL)
    requested = [part.strip() for part in args.targets.split(",") if part.strip()] if args.targets else None

    client = SnapshotClient(base_url, token)
    manifest = load_manifest(client)
    inventory = load_inventory(client, manifest)
    plan = plan_for_targets(manifest, inventory, requested)
    share = plan_share(manifest, inventory)

    home = Path.home()
    state_path = home / STATE_RELPATH
    state = read_state(state_path)

    results: dict[str, dict] = {}
    for name in sorted(plan):
        results[name] = sync_target(
            home=home,
            name=name,
            plan=plan[name],
            target_entry=manifest["targets"][name],
            snapshot_id=manifest["snapshot_id"],
            state=state,
            client=client,
            adopt=args.adopt,
        )
    share_result = None
    if share is not None:
        # The prompts cite these documents, so the shared tree is synced whatever targets the
        # caller asked for; skipping it would hand the device a prompt that points at nothing.
        share_result = sync_target(
            home=home,
            name=SHARE_STATE_KEY,
            plan=share,
            target_entry={},
            snapshot_id=manifest["snapshot_id"],
            state=state,
            client=client,
            adopt=args.adopt,
        )
    write_state(state_path, state)

    totals = {key: sum(result[key] for result in results.values()) for key in ("written", "skipped", "pruned")}
    if share_result is not None:
        for key in ("written", "skipped", "pruned"):
            totals[key] += share_result[key]
    summary = {
        "event": "sync.done",
        "url": base_url,
        "snapshot_id": manifest["snapshot_id"],
        "targets": results,
        **totals,
    }
    if share_result is not None:
        summary["share"] = share_result
    print(json.dumps(summary, sort_keys=True))
    for name, result in sorted(results.items()):
        print(
            f"[sync] {name}: {result['written']} written, {result['skipped']} unchanged, "
            f"{result['pruned']} pruned -> {home / result['dest']}",
            file=sys.stderr,
        )
    if share_result is not None:
        print(
            f"[sync] share: {share_result['written']} written, {share_result['skipped']} unchanged, "
            f"{share_result['pruned']} pruned -> {home / share_result['dest']}",
            file=sys.stderr,
        )
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mirror the published Rosetta agent snapshot into this device's "
        "Claude Code and Codex agent directories.",
    )
    parser.add_argument("--url", default=os.environ.get("ROSETTA_MCP_URL") or DEFAULT_URL,
                        help=f"service base URL (https only; default {DEFAULT_URL})")
    parser.add_argument("--targets", default=None,
                        help="comma-separated harness targets to sync (default: every target the snapshot publishes)")
    parser.add_argument("--adopt", action="store_true",
                        help="move a symlinked destination aside to <dest>.bak-<timestamp> instead of refusing")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except Refused as refusal:
        print(f"[sync] refused: {refusal}", file=sys.stderr)
        return EXIT_REFUSED
    except Failed as failure:
        print(f"[sync] failed: {failure}", file=sys.stderr)
        return EXIT_FAILED
    except OSError as error:
        print(f"[sync] failed: {error}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
