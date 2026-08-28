#!/usr/bin/env python3
"""Least-privilege release gateway for getreplay production components."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Sequence


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_STATE_DIR = Path("/home/solo/.local/state/getreplay-release")
INFRA_ROOT = Path(__file__).resolve().parents[1]


@dataclasses.dataclass(frozen=True)
class Component:
    repository: Path
    deploy_script: Path
    deploy_args: tuple[str, ...] = ()
    service: str | None = None
    health_url: str | None = None
    revision_env: str = "REVISION"
    repository_env: str = ""


COMPONENTS: dict[str, Component] = {
    "frontend": Component(
        repository=Path("/home/solo/getreplay-front"),
        deploy_script=INFRA_ROOT / "frontend/deploy.sh",
        service="nextjs.service",
        health_url="http://[::1]:3000/",
        repository_env="APP_ROOT",
    ),
    "php": Component(
        repository=Path("/var/www/fun-php/repo"),
        deploy_script=INFRA_ROOT / "php/deploy.sh",
        service="php8.4-fpm.service",
        repository_env="REPO_ROOT",
    ),
    "go-match-updater": Component(
        repository=Path("/home/solo/getreplay-go"),
        deploy_script=INFRA_ROOT / "go/deploy.sh",
        deploy_args=("match-updater",),
        service="go-app.service",
        repository_env="SRC",
    ),
    "go-demo-uploader": Component(
        repository=Path("/home/solo/getreplay-go"),
        deploy_script=INFRA_ROOT / "go/deploy.sh",
        deploy_args=("demo-uploader",),
        service="demo-uploader.service",
        repository_env="SRC",
    ),
    "go-highlight-extractor": Component(
        repository=Path("/home/solo/getreplay-go"),
        deploy_script=INFRA_ROOT / "go/deploy.sh",
        deploy_args=("highlight-extractor",),
        repository_env="SRC",
    ),
    "go-replay-converter": Component(
        repository=Path("/home/solo/getreplay-go"),
        deploy_script=INFRA_ROOT / "go/deploy.sh",
        deploy_args=("replay-converter",),
        repository_env="SRC",
    ),
    "go-stats-extractor": Component(
        repository=Path("/home/solo/getreplay-go"),
        deploy_script=INFRA_ROOT / "go/deploy.sh",
        deploy_args=("stats-extractor",),
        repository_env="SRC",
    ),
}


class ReleaseError(RuntimeError):
    pass


def validate_component(name: str) -> Component:
    try:
        return COMPONENTS[name]
    except KeyError as exc:
        raise ReleaseError(f"component is not allowed: {name}") from exc


def validate_revision(revision: str) -> str:
    if not SHA_RE.fullmatch(revision):
        raise ReleaseError("revision must be a full lowercase 40-character commit SHA")
    return revision


def command_for(name: str, revision: str) -> tuple[list[str], dict[str, str]]:
    component = validate_component(name)
    revision = validate_revision(revision)
    command = [str(component.deploy_script), *component.deploy_args]
    environment = {
        "PATH": "/opt/node-20/bin:/usr/local/bin:/usr/bin:/bin",
        "HOME": "/home/solo",
        "BRANCH": "main",
        component.revision_env: revision,
    }
    if component.repository_env:
        environment[component.repository_env] = str(component.repository)
    ssh_auth_sock = os.environ.get("SSH_AUTH_SOCK")
    if ssh_auth_sock:
        environment["SSH_AUTH_SOCK"] = ssh_auth_sock
    return command, environment


def git_head(repository: Path) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def service_state(service: str | None) -> str | None:
    if service is None:
        return None
    completed = subprocess.run(
        ["systemctl", "is-active", service],
        check=False,
        capture_output=True,
        text=True,
    )
    state = completed.stdout.strip()
    return state or "unknown"


def state_dir() -> Path:
    return DEFAULT_STATE_DIR


def state_path(name: str) -> Path:
    return state_dir() / f"{name}.json"


def read_state(name: str) -> dict[str, object] | None:
    path = state_path(name)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_state(name: str, payload: dict[str, object]) -> None:
    root = state_dir()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = state_path(name)
    temporary = destination.with_suffix(f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(destination)


def status_payload(name: str) -> dict[str, object]:
    component = validate_component(name)
    return {
        "component": name,
        "checkout_revision": git_head(component.repository),
        "last_release": read_state(name),
        "service": component.service,
        "service_state": service_state(component.service),
    }


def preview_payload(action: str, name: str, revision: str) -> dict[str, object]:
    command, _ = command_for(name, revision)
    component = COMPONENTS[name]
    return {
        "action": action,
        "component": name,
        "revision": revision,
        "branch_constraint": "origin/main",
        "repository": str(component.repository),
        "deploy_command": command,
        "database_migrations": False,
        "arbitrary_shell": False,
    }


def execute_release(action: str, name: str, revision: str) -> dict[str, object]:
    command, environment = command_for(name, revision)
    previous = read_state(name)
    root = state_dir()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = root / "release.lock"

    with lock_path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        completed = subprocess.run(command, check=False, env=environment)
        if completed.returncode != 0:
            raise ReleaseError(f"deploy script failed with exit code {completed.returncode}")

        payload = {
            "action": action,
            "component": name,
            "revision": revision,
            "previous_revision": previous.get("revision") if previous else None,
            "released_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        write_state(name, payload)
        return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("component", nargs="?", choices=sorted(COMPONENTS))

    for action in ("preview", "deploy", "rollback"):
        action_parser = subparsers.add_parser(action)
        action_parser.add_argument("component", choices=sorted(COMPONENTS))
        action_parser.add_argument("revision")

    return parser


def run(argv: Sequence[str]) -> dict[str, object] | list[dict[str, object]]:
    args = build_parser().parse_args(list(argv))
    if args.action == "status":
        names = [args.component] if args.component else sorted(COMPONENTS)
        return [status_payload(name) for name in names]
    if args.action == "preview":
        return preview_payload(args.action, args.component, args.revision)
    return execute_release(args.action, args.component, args.revision)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = run(sys.argv[1:] if argv is None else argv)
    except ReleaseError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({"status": "ok", "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
