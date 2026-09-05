#!/usr/bin/env python3
"""Update one source scope in a reviewed GetReplay release candidate."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import tempfile
from typing import Mapping, Sequence

import broker


SCOPES = ("frontend", "php", "node", "go", "migrations")
DATABASES = ("mysql", "clickhouse")
DATABASE_CHOICES = ("both", *DATABASES)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class CandidateError(RuntimeError):
    """The requested candidate update is invalid or unsafe."""


def _component_names(candidate: Mapping[str, object], scope: str) -> tuple[str, ...]:
    if scope not in SCOPES or scope == "migrations":
        raise CandidateError(f"unsupported candidate scope: {scope}")
    components = candidate.get("components")
    if not isinstance(components, Mapping):
        raise CandidateError("candidate components must be an object")
    names = (
        tuple(name for name in components if isinstance(name, str) and name.startswith("go-"))
        if scope == "go"
        else (scope,)
    )
    if not names or any(name not in components for name in names):
        raise CandidateError(f"candidate does not contain scope: {scope}")
    return names


def _migration_names(database: str | None) -> tuple[str, ...]:
    if database not in DATABASE_CHOICES:
        raise CandidateError("migrations require database: both, mysql or clickhouse")
    return DATABASES if database == "both" else (database,)


def update_candidate(
    payload: object,
    scope: str,
    revision: str,
    artifact: str,
    database: str | None = None,
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Return a validated candidate with only the selected source scope updated."""
    if not SHA_RE.fullmatch(revision):
        raise CandidateError("revision must be a full lowercase commit SHA")
    if not DIGEST_RE.fullmatch(artifact):
        raise CandidateError("artifact must be a lowercase sha256 digest")
    try:
        broker._validate_manifest(payload, "candidate")
    except broker.BrokerError as exc:
        raise CandidateError(f"candidate manifest is invalid: {exc}") from exc
    assert isinstance(payload, Mapping)
    candidate = copy.deepcopy(dict(payload))
    if scope == "migrations":
        names = _migration_names(database)
        migrations = candidate["migrations"]
        assert isinstance(migrations, dict)
        migrations.clear()
        for name in names:
            migrations[name] = {
                "revision": revision,
                "artifact": artifact,
                "migration": f"{name}-{revision[:12]}",
            }
    else:
        if database is not None:
            raise CandidateError("database can only be set for migrations")
        names = _component_names(candidate, scope)
        components = candidate["components"]
        assert isinstance(components, dict)
        for name in names:
            components[name] = {"revision": revision, "artifact": artifact}
    broker._validate_manifest(candidate, "candidate")
    return candidate, names


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as destination:
        temporary = Path(destination.name)
        json.dump(payload, destination, indent=2)
        destination.write("\n")
    temporary.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scope", choices=SCOPES, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--database", choices=DATABASE_CHOICES)
    args = parser.parse_args(argv)

    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        candidate, names = update_candidate(
            payload,
            args.scope,
            args.revision,
            args.artifact,
            args.database,
        )
        assert isinstance(payload, Mapping)
        entries = payload["migrations" if args.scope == "migrations" else "components"]
        assert isinstance(entries, Mapping)
        previous_revisions = sorted(
            {
                str(entries[name]["revision"])
                for name in names
                if name in entries and isinstance(entries[name], Mapping)
            }
        )
        _write_json(args.output, candidate)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, CandidateError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "artifact": args.artifact,
                "components": names,
                "previous_revisions": previous_revisions,
                "revision": args.revision,
                "scope": args.scope,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
