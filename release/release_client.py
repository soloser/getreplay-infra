"""Bounded Unix-socket client for the root-owned release broker."""

from __future__ import annotations

import json
from pathlib import Path
import socket
from typing import Any

import release_protocol


DEFAULT_SOCKET = Path("/run/getreplay-release/control.sock")
MAX_RESPONSE_BYTES = 1024 * 1024


class ClientError(RuntimeError):
    """The local broker could not return a valid response."""


def send_request(
    request: release_protocol.Request,
    *,
    socket_path: Path = DEFAULT_SOCKET,
    timeout: float = 1900,
) -> dict[str, Any]:
    payload = release_protocol.encode_request(request)
    response = bytearray()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(socket_path))
            client.sendall(payload)
            client.shutdown(socket.SHUT_WR)
            while True:
                chunk = client.recv(min(65536, MAX_RESPONSE_BYTES + 1 - len(response)))
                if not chunk:
                    break
                response.extend(chunk)
                if len(response) > MAX_RESPONSE_BYTES:
                    raise ClientError("broker response is too large")
    except (OSError, TimeoutError) as exc:
        raise ClientError(f"release broker is unavailable: {exc}") from exc
    try:
        decoded = json.loads(response)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClientError("release broker returned invalid JSON") from exc
    if not isinstance(decoded, dict) or decoded.get("status") not in {"ok", "error"}:
        raise ClientError("release broker returned an invalid envelope")
    return decoded
