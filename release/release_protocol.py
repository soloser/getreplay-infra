"""Strict request protocol shared by the SSH gateway and release broker."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import shlex
from typing import Mapping, Sequence


PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 16 * 1024
COMPONENTS = (
    "frontend",
    "php",
    "node",
    "go-match-updater",
    "go-demo-uploader",
    "go-match-discovery-worker",
    "go-demo-downloader-worker",
    "go-demo-processor-worker",
    "go-highlight-extractor",
    "go-replay-converter",
    "go-stats-extractor",
)
DATABASES = ("mysql", "clickhouse")
OPERATIONS = ("status", "stage", "promote")
RELEASE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class ReleaseError(RuntimeError):
    """A request that is outside the release broker contract."""


@dataclasses.dataclass(frozen=True)
class Request:
    operation: str
    target: str | None = None
    release_id: str | None = None
    preview: bool = False
    manifest: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "version": PROTOCOL_VERSION,
            "operation": self.operation,
            "target": self.target,
            "release_id": self.release_id,
            "preview": self.preview,
            "manifest": self.manifest,
        }

    def audit_dict(self) -> dict[str, object]:
        result = self.as_dict()
        manifest = result.pop("manifest")
        result["manifest_sha256"] = (
            hashlib.sha256(str(manifest).encode("ascii")).hexdigest()
            if manifest is not None
            else None
        )
        return result


def validate_release_id(value: str) -> str:
    if not RELEASE_ID_RE.fullmatch(value) or ".." in value:
        raise ReleaseError("release_id must be 1-64 lowercase letters, digits, dot, dash or underscore")
    return value


def validate_request(request: Request) -> Request:
    if request.operation not in OPERATIONS:
        raise ReleaseError(f"operation is not allowed: {request.operation}")
    if request.operation == "status":
        if (
            request.target is not None
            or request.release_id is not None
            or request.preview
            or request.manifest is not None
        ):
            raise ReleaseError("status cannot contain target, release_id, preview or manifest")
        return request
    if request.target is not None or request.release_id is None:
        raise ReleaseError(f"{request.operation} requires release_id and cannot contain a target")
    validate_release_id(request.release_id)
    if request.operation == "stage":
        if request.preview or not isinstance(request.manifest, str) or not request.manifest:
            raise ReleaseError("stage requires a base64 manifest and cannot be previewed")
        return request
    if request.manifest is not None:
        raise ReleaseError("promote cannot contain a manifest")
    return request


def parse_argv(argv: Sequence[str]) -> Request:
    words = list(argv)
    preview = False
    if words[:1] == ["preview"]:
        preview = True
        words = words[1:]
    if words[:1] == ["status"]:
        if preview or len(words) != 1:
            raise ReleaseError("allowed shape: status")
        return validate_request(Request("status"))
    if len(words) == 3 and words[0] == "stage":
        if preview:
            raise ReleaseError("stage cannot be previewed")
        return validate_request(Request("stage", None, words[1], False, words[2]))
    if len(words) != 2 or words[0] != "promote":
        raise ReleaseError(
            "allowed shapes: stage <release_id> <base64-manifest> or "
            "[preview] promote <release_id>"
        )
    return validate_request(Request("promote", None, words[1], preview))


def parse_ssh_command(raw: str) -> Request:
    try:
        words = shlex.split(raw, posix=True)
    except ValueError as exc:
        raise ReleaseError("invalid command syntax") from exc
    if not words or words[0] != "getreplay-release":
        raise ReleaseError("only getreplay-release commands are allowed")
    return parse_argv(words[1:])


def encode_request(request: Request) -> bytes:
    payload = json.dumps(validate_request(request).as_dict(), separators=(",", ":"), sort_keys=True)
    encoded = payload.encode("utf-8") + b"\n"
    if len(encoded) > MAX_REQUEST_BYTES:
        raise ReleaseError("request is too large")
    return encoded


def decode_request(raw: bytes) -> Request:
    if not raw or len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
        raise ReleaseError("request must be one bounded JSON line")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseError("request is not valid JSON") from exc
    expected = {"version", "operation", "target", "release_id", "preview", "manifest"}
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise ReleaseError("request fields do not match protocol version 1")
    if payload["version"] != PROTOCOL_VERSION:
        raise ReleaseError("unsupported protocol version")
    if not isinstance(payload["operation"], str):
        raise ReleaseError("operation must be a string")
    if payload["target"] is not None and not isinstance(payload["target"], str):
        raise ReleaseError("target must be a string or null")
    if payload["release_id"] is not None and not isinstance(payload["release_id"], str):
        raise ReleaseError("release_id must be a string or null")
    if not isinstance(payload["preview"], bool):
        raise ReleaseError("preview must be a boolean")
    if payload["manifest"] is not None and not isinstance(payload["manifest"], str):
        raise ReleaseError("manifest must be a string or null")
    return validate_request(
        Request(
            payload["operation"],
            payload["target"],
            payload["release_id"],
            payload["preview"],
            payload["manifest"],
        )
    )
