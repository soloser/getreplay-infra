#!/usr/bin/env python3
"""Administrative CLI for the local getreplay release broker."""

from __future__ import annotations

import json
import sys
from typing import Sequence

import release_client
import release_protocol


def main(argv: Sequence[str] | None = None) -> int:
    try:
        request = release_protocol.parse_argv(sys.argv[1:] if argv is None else argv)
        result = release_client.send_request(request)
    except (release_protocol.ReleaseError, release_client.ClientError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
