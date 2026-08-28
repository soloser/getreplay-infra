#!/usr/bin/env python3
"""OpenSSH forced-command entry point for the release-only identity."""

from __future__ import annotations

import json
import os

import release_client
import release_protocol


def main() -> int:
    try:
        request = release_protocol.parse_ssh_command(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
        result = release_client.send_request(request)
    except (release_protocol.ReleaseError, release_client.ClientError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
