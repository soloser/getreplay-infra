#!/usr/bin/env python3
"""Root-owned, allowlisted broker for immutable getreplay releases."""

from __future__ import annotations

import argparse
import base64
import binascii
import dataclasses
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import struct
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence

import release_protocol


DEFAULT_SOCKET = Path("/run/getreplay-release/control.sock")
DEFAULT_MANIFEST_ROOT = Path("/var/lib/getreplay-release/manifests")
DEFAULT_STATE_ROOT = Path("/var/lib/getreplay-release/state")
DEFAULT_ADAPTER_ROOT = Path("/usr/local/libexec/getreplay-release/adapters")
MAX_ADAPTER_OUTPUT = 64 * 1024
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class BrokerError(RuntimeError):
    """A fail-closed broker or release error."""


@dataclasses.dataclass(frozen=True)
class BrokerConfig:
    socket_path: Path = DEFAULT_SOCKET
    manifest_root: Path = DEFAULT_MANIFEST_ROOT
    state_root: Path = DEFAULT_STATE_ROOT
    adapter_root: Path = DEFAULT_ADAPTER_ROOT
    trusted_uid: int = 0
    adapter_timeout: int = 1800
    systemd_run: Path | None = Path("/usr/bin/systemd-run")


def _read_trusted_json(path: Path, trusted_uid: int) -> Mapping[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BrokerError(f"trusted file is unavailable: {path.name}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise BrokerError(f"trusted file is not regular: {path.name}")
        if metadata.st_uid != trusted_uid or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise BrokerError(f"trusted file ownership or mode is unsafe: {path.name}")
        with os.fdopen(descriptor, encoding="utf-8") as source:
            descriptor = -1
            payload = json.load(source)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrokerError(f"trusted file is not valid JSON: {path.name}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(payload, Mapping):
        raise BrokerError("release manifest must be an object")
    return payload


def _validate_artifact_entry(value: object, *, migration: bool) -> dict[str, str]:
    keys = {"revision", "artifact", "migration"} if migration else {"revision", "artifact"}
    if not isinstance(value, Mapping) or set(value) != keys:
        raise BrokerError("release manifest entry has unexpected fields")
    if not isinstance(value["revision"], str) or not SHA_RE.fullmatch(value["revision"]):
        raise BrokerError("manifest revision must be a full lowercase commit SHA")
    if not isinstance(value["artifact"], str) or not DIGEST_RE.fullmatch(value["artifact"]):
        raise BrokerError("manifest artifact must be an immutable sha256 digest")
    if migration and (
        not isinstance(value["migration"], str)
        or not release_protocol.RELEASE_ID_RE.fullmatch(value["migration"])
        or ".." in value["migration"]
    ):
        raise BrokerError("manifest migration identifier is invalid")
    return {key: str(value[key]) for key in keys}


def _validate_manifest(payload: object, release_id: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise BrokerError("release manifest must be an object")
    if set(payload) != {"version", "release_id", "components", "migrations"}:
        raise BrokerError("release manifest fields do not match version 1")
    if payload["version"] != 1 or payload["release_id"] != release_id:
        raise BrokerError("release manifest identity does not match request")
    components = payload["components"]
    migrations = payload["migrations"]
    if not isinstance(components, Mapping) or not isinstance(migrations, Mapping):
        raise BrokerError("manifest components and migrations must be objects")
    if not set(components).issubset(release_protocol.COMPONENTS):
        raise BrokerError("release manifest contains an unknown component")
    if not set(migrations).issubset(release_protocol.DATABASES):
        raise BrokerError("release manifest contains an unknown database")
    validated_components = {
        name: _validate_artifact_entry(value, migration=False) for name, value in components.items()
    }
    validated_migrations = {
        name: _validate_artifact_entry(value, migration=True) for name, value in migrations.items()
    }
    return {
        "version": 1,
        "release_id": release_id,
        "components": validated_components,
        "migrations": validated_migrations,
    }


def load_manifest(config: BrokerConfig, release_id: str) -> tuple[Path, dict[str, Any]]:
    release_protocol.validate_release_id(release_id)
    path = config.manifest_root / f"{release_id}.json"
    payload = _read_trusted_json(path, config.trusted_uid)
    return path, _validate_manifest(payload, release_id)


def stage_manifest(config: BrokerConfig, request: release_protocol.Request) -> dict[str, Any]:
    request = release_protocol.validate_request(request)
    if request.operation != "stage" or request.release_id is None or request.manifest is None:
        raise BrokerError("stage request is required")
    try:
        raw = base64.b64decode(request.manifest, validate=True)
        payload = json.loads(raw)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrokerError("staged manifest is not valid base64 JSON") from exc
    manifest = _validate_manifest(payload, request.release_id)
    config.manifest_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = config.manifest_root / f"{request.release_id}.json"
    temporary = config.manifest_root / f".{request.release_id}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            descriptor = -1
            json.dump(manifest, target, sort_keys=True)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        temporary.replace(destination)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
    return {
        "release_id": request.release_id,
        "components": sorted(manifest["components"]),
        "migrations": sorted(manifest["migrations"]),
        "manifest": str(destination),
    }


def _adapter_path(config: BrokerConfig) -> Path:
    name = "promote-release"
    path = config.adapter_root / name
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BrokerError(f"release adapter is not installed: {name}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != config.trusted_uid
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not stat.S_IMODE(metadata.st_mode) & stat.S_IXUSR
    ):
        raise BrokerError(f"release adapter ownership or mode is unsafe: {name}")
    return path


def status(config: BrokerConfig) -> dict[str, Any]:
    return {
        "last_release": _read_state(config.state_root / "last-release.json"),
        "last_failure": _failure_summary(_read_state(config.state_root / "last-failure.json")),
    }


def plan(config: BrokerConfig, request: release_protocol.Request) -> dict[str, Any]:
    request = release_protocol.validate_request(request)
    if request.operation == "status":
        return status(config)
    assert request.release_id is not None
    manifest_path, manifest = load_manifest(config, request.release_id)
    adapter = _adapter_path(config)
    return {
        "operation": request.operation,
        "release_id": request.release_id,
        "components": manifest["components"],
        "migrations": manifest["migrations"],
        "manifest": str(manifest_path),
        "adapter": str(adapter),
        "preview": request.preview,
        "arbitrary_shell": False,
    }


def _read_state(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _tail(source: Any, limit: int = MAX_ADAPTER_OUTPUT) -> str:
    source.seek(0, os.SEEK_END)
    size = source.tell()
    source.seek(max(0, size - limit))
    return source.read().decode("utf-8", errors="replace")


def _write_state(config: BrokerConfig, request: release_protocol.Request, result: Mapping[str, Any]) -> None:
    config.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = config.state_root / "last-release.json"
    temporary = destination.with_suffix(f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(destination)


def _write_failure(config: BrokerConfig, result: Mapping[str, Any]) -> None:
    config.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = config.state_root / "last-failure.json"
    temporary = destination.with_suffix(f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(destination)


def _extract_adapter_error(stdout: str, stderr: str) -> str | None:
    for line in reversed((stdout + "\n" + stderr).splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping) and isinstance(payload.get("error"), str):
            return str(payload["error"])[:2000]
    return None


def _failure_summary(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    return {
        key: payload.get(key)
        for key in ("release_id", "exit_code", "completed_at", "adapter_error")
    }


def _snapshot_manifest(config: BrokerConfig, release_plan: Mapping[str, Any]) -> Path:
    execution_root = config.state_root / "executions"
    execution_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    release_id = str(release_plan["release_id"])
    filename = f"{release_id}-{secrets.token_hex(8)}.json"
    destination = execution_root / filename
    payload = {
        "version": 1,
        "release_id": release_id,
        "components": release_plan["components"],
        "migrations": release_plan["migrations"],
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(destination, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as target:
        json.dump(payload, target, sort_keys=True)
        target.write("\n")
    return destination


def _adapter_command(
    config: BrokerConfig,
    adapter: str,
    execution_manifest: Path,
    release_id: str,
) -> list[str]:
    adapter_command = [
        adapter,
        "--manifest",
        str(execution_manifest),
        "--release-id",
        release_id,
    ]
    if config.systemd_run is None:
        return adapter_command
    unit = f"getreplay-release-executor-{secrets.token_hex(8)}"
    writable_paths = " ".join(
        (
            "-/home/solo/getreplay-front",
            "-/home/solo/getreplay-front-slots",
            "-/var/lib/getreplay-frontend",
            "-/home/solo/getreplay-go",
            "-/home/solo/getreplay-node",
            "-/home/solo/getreplay-node-releases",
            "-/home/solo/fun-migrations",
            "-/home/solo/.npm",
            "-/home/solo/.cache",
            "-/home/solo/go",
            "-/var/www/fun-php",
            "-/var/www/getreplay-go",
            "-/etc/cron.d",
            "-/etc/systemd/system/node-app.service",
            "-/var/log",
        )
    )
    return [
        str(config.systemd_run),
        "--quiet",
        "--wait",
        "--pipe",
        "--collect",
        "--service-type=exec",
        f"--unit={unit}",
        "--property=User=root",
        "--property=Group=root",
        "--property=UMask=0022",
        "--property=PrivateTmp=yes",
        "--property=ProtectHome=read-only",
        "--property=ProtectSystem=strict",
        f"--property=ReadWritePaths={writable_paths}",
        "--property=RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "--property=LockPersonality=yes",
        f"--property=RuntimeMaxSec={config.adapter_timeout}",
        "--",
        *adapter_command,
    ]


def execute(config: BrokerConfig, request: release_protocol.Request) -> dict[str, Any]:
    release_plan = plan(config, request)
    if request.preview:
        return release_plan
    config.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = config.state_root / "release.lock"
    with lock_path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        execution_manifest = _snapshot_manifest(config, release_plan)
        command = _adapter_command(
            config,
            str(release_plan["adapter"]),
            execution_manifest,
            str(release_plan["release_id"]),
        )
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    cwd="/",
                    env={"HOME": "/", "PATH": "/usr/bin:/bin"},
                    stdout=stdout,
                    stderr=stderr,
                    timeout=config.adapter_timeout,
                )
            except OSError as exc:
                raise BrokerError("release executor is unavailable") from exc
            adapter_stdout = _tail(stdout)
            adapter_stderr = _tail(stderr)
        result = {
            **release_plan,
            "execution_manifest": str(execution_manifest),
            "exit_code": completed.returncode,
            "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "adapter_stdout": adapter_stdout,
            "adapter_stderr": adapter_stderr,
        }
        if completed.returncode != 0:
            adapter_error = _extract_adapter_error(adapter_stdout, adapter_stderr)
            failure = {**result, "adapter_error": adapter_error}
            _write_failure(config, failure)
            detail = f": {adapter_error}" if adapter_error else ""
            raise BrokerError(
                f"release adapter failed with exit code {completed.returncode}{detail}; "
                "root details: /var/lib/getreplay-release/state/last-failure.json"
            )
        _write_state(config, request, result)
        return result


def handle(config: BrokerConfig, request: release_protocol.Request) -> dict[str, Any]:
    if request.operation == "status":
        result = status(config)
    elif request.operation == "stage":
        result = stage_manifest(config, request)
    else:
        result = execute(config, request)
    return {"status": "ok", "result": result}


def _receive(connection: socket.socket) -> bytes:
    data = bytearray()
    while True:
        chunk = connection.recv(release_protocol.MAX_REQUEST_BYTES + 1 - len(data))
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > release_protocol.MAX_REQUEST_BYTES:
            raise BrokerError("request is too large")
    return bytes(data)


def _peer_credentials(connection: socket.socket) -> dict[str, int | None]:
    if not hasattr(socket, "SO_PEERCRED"):
        return {"pid": None, "uid": None, "gid": None}
    raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    pid, uid, gid = struct.unpack("3i", raw)
    return {"pid": pid, "uid": uid, "gid": gid}


def _audit(event: str, **fields: object) -> None:
    print(
        json.dumps(
            {
                "at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "event": event,
                **fields,
            },
            sort_keys=True,
        ),
        flush=True,
    )


def serve(config: BrokerConfig) -> None:
    config.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    if config.socket_path.exists():
        metadata = config.socket_path.lstat()
        if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != config.trusted_uid:
            raise BrokerError("refusing to replace an unsafe socket path")
        config.socket_path.unlink()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(config.socket_path))
        config.socket_path.chmod(0o660)
        server.listen(16)
        while True:
            connection, _ = server.accept()
            with connection:
                peer = _peer_credentials(connection)
                try:
                    request = release_protocol.decode_request(_receive(connection))
                    _audit("request", peer=peer, request=request.audit_dict())
                    response = handle(config, request)
                    _audit(
                        "complete",
                        peer=peer,
                        operation=request.operation,
                        target=request.target,
                        release_id=request.release_id,
                        preview=request.preview,
                    )
                except (release_protocol.ReleaseError, BrokerError, subprocess.TimeoutExpired) as exc:
                    response = {"status": "error", "error": str(exc)}
                    _audit("error", peer=peer, error=str(exc))
                connection.sendall(json.dumps(response, sort_keys=True).encode("utf-8") + b"\n")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=("serve", "check"))
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(sys.argv[1:] if argv is None else argv)
    config = BrokerConfig()
    if args.command == "check":
        print(json.dumps({"status": "ok", "socket": str(config.socket_path)}, sort_keys=True))
        return 0
    try:
        serve(config)
    except BrokerError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
