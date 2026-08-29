#!/usr/bin/env python3
"""Select a fixed component scope from a reviewed release candidate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


SCOPES = ("all", "frontend", "node", "php", "go", "migrations")


class ScopeError(RuntimeError):
    """The reviewed candidate cannot satisfy the fixed deployment scope."""


def select_scope(payload: object, scope: str, release_id: str) -> dict[str, Any]:
    if scope not in SCOPES:
        raise ScopeError("unknown release scope")
    if not isinstance(payload, Mapping) or set(payload) != {
        "version",
        "release_id",
        "components",
        "migrations",
    }:
        raise ScopeError("candidate manifest has an unexpected shape")
    if payload["version"] != 1 or payload["release_id"] != "candidate":
        raise ScopeError("candidate manifest identity is invalid")
    components = payload["components"]
    migrations = payload["migrations"]
    if not isinstance(components, Mapping) or not isinstance(migrations, Mapping):
        raise ScopeError("candidate components and migrations must be objects")

    if scope == "all":
        selected_components = dict(components)
        selected_migrations = dict(migrations)
    elif scope == "migrations":
        selected_components = {}
        selected_migrations = dict(migrations)
    elif scope == "go":
        selected_components = {
            name: entry for name, entry in components.items() if name.startswith("go-")
        }
        selected_migrations = {}
    else:
        if scope not in components:
            raise ScopeError(f"candidate does not contain component: {scope}")
        selected_components = {scope: components[scope]}
        selected_migrations = {}

    if not selected_components and not selected_migrations:
        raise ScopeError(f"candidate scope is empty: {scope}")
    return {
        "version": 1,
        "release_id": release_id,
        "components": selected_components,
        "migrations": selected_migrations,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--scope", choices=SCOPES, required=True)
    result.add_argument("--release-id", required=True)
    result.add_argument("--input", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        selected = select_scope(payload, args.scope, args.release_id)
        args.output.write_text(
            json.dumps(selected, separators=(",", ":"), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0
    except (OSError, json.JSONDecodeError, ScopeError) as exc:
        print(f"scope selection failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
