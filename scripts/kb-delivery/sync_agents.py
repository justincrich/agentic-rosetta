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
  5. Never deletes a file it did not create: the paths it wrote are recorded in
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


def sync_target(
    *,
    home: Path,
    name: str,
    plan: dict,
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
    return {
        "dest": plan["dest"],
        "written": written,
        "skipped": skipped,
        "pruned": pruned,
        "files": len(plan["files"]),
        "adopted": str(adopted) if adopted else None,
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

    home = Path.home()
    state_path = home / STATE_RELPATH
    state = read_state(state_path)

    results: dict[str, dict] = {}
    for name in sorted(plan):
        results[name] = sync_target(
            home=home,
            name=name,
            plan=plan[name],
            snapshot_id=manifest["snapshot_id"],
            state=state,
            client=client,
            adopt=args.adopt,
        )
    write_state(state_path, state)

    totals = {key: sum(result[key] for result in results.values()) for key in ("written", "skipped", "pruned")}
    summary = {
        "event": "sync.done",
        "url": base_url,
        "snapshot_id": manifest["snapshot_id"],
        "targets": results,
        **totals,
    }
    print(json.dumps(summary, sort_keys=True))
    for name, result in sorted(results.items()):
        print(
            f"[sync] {name}: {result['written']} written, {result['skipped']} unchanged, "
            f"{result['pruned']} pruned -> {home / result['dest']}",
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
