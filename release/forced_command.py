#!/usr/bin/env python3
"""OpenSSH forced-command entry point for the release-only identity."""

from __future__ import annotations

import os
import shlex
import sys

import getreplay_release


DELEGATED_RUNNER = (
    "/usr/bin/sudo",
    "-n",
    "-u",
    "solo",
    "--",
    "/usr/bin/python3",
    "/home/solo/infra/release/getreplay_release.py",
)


def parse_original_command(raw: str) -> list[str]:
    try:
        words = shlex.split(raw, posix=True)
    except ValueError as exc:
        raise getreplay_release.ReleaseError("invalid command syntax") from exc

    if not words or words[0] != "getreplay-release":
        raise getreplay_release.ReleaseError("only getreplay-release commands are allowed")

    argv = words[1:]
    if argv == ["status"]:
        return argv
    if len(argv) == 2 and argv[0] == "status":
        getreplay_release.validate_component(argv[1])
        return argv
    if len(argv) == 3 and argv[0] in {"preview", "deploy", "rollback"}:
        getreplay_release.validate_component(argv[1])
        getreplay_release.validate_revision(argv[2])
        return argv

    raise getreplay_release.ReleaseError("command shape is not allowed")


def delegated_argv(argv: list[str]) -> list[str]:
    return [*DELEGATED_RUNNER, *argv]


def main() -> int:
    try:
        argv = parse_original_command(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
    except getreplay_release.ReleaseError as exc:
        print(f'{{"status":"error","error":"{exc}"}}')
        return 2
    os.execv(DELEGATED_RUNNER[0], delegated_argv(argv))
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
