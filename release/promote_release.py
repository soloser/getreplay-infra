#!/usr/bin/env python3
"""Promote one validated GetReplay source release on the production host."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import re
from typing import Any, Mapping, Sequence


HERE = Path(__file__).resolve().parent
LIB_ROOT = HERE if (HERE / "broker.py").is_file() else HERE.parent
sys.path.insert(0, str(LIB_ROOT))

import broker  # noqa: E402
import release_protocol  # noqa: E402


DEPLOY_USER = "solo"
DEPLOY_ROOT = Path("/usr/local/libexec/getreplay-release/deploy")
REPOSITORIES = {
    "frontend": Path("/home/solo/getreplay-front"),
    "php": Path("/var/www/fun-php/repo"),
    "node": Path("/home/solo/getreplay-node"),
    "go": Path("/home/solo/getreplay-go"),
    "migrations": Path("/home/solo/fun-migrations/migrations"),
}
COMPONENT_REPOSITORY = {
    "frontend": "frontend",
    "php": "php",
    "node": "node",
    "go-match-updater": "go",
    "go-demo-uploader": "go",
    "go-match-discovery-worker": "go",
    "go-demo-downloader-worker": "go",
    "go-demo-processor-worker": "go",
    "go-highlight-extractor": "go",
    "go-replay-converter": "go",
    "go-stats-extractor": "go",
}
GO_COMPONENT_APP = {
    # Start downstream consumers before their upstream producers on first rollout.
    "go-demo-processor-worker": "demo-processor-worker",
    "go-demo-downloader-worker": "demo-downloader-worker",
    "go-match-discovery-worker": "match-discovery-worker",
    "go-match-updater": "match-updater",
    "go-demo-uploader": "demo-uploader",
    "go-highlight-extractor": "highlight-extractor",
    "go-replay-converter": "replay-converter",
    "go-stats-extractor": "stats-extractor",
}
GO_COMPONENT_ORDER = tuple(GO_COMPONENT_APP)
BASE_ENV = {
    "HOME": "/home/solo",
    "LANG": "C.UTF-8",
    "PATH": "/opt/go/bin:/opt/node-20/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
}


class PromotionError(RuntimeError):
    """A release cannot be promoted safely."""


def _redact(text: str) -> str:
    return re.sub(r"([a-z][a-z0-9+.-]*://)[^/@\s]+@", r"\1***@", text, flags=re.IGNORECASE)


def _run(
    command: Sequence[str],
    *,
    user: str | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    effective_env = {**BASE_ENV, **(env or {})}
    argv = list(command)
    if user is not None:
        argv = [
            "/usr/sbin/runuser",
            "--user",
            user,
            "--",
            "/usr/bin/env",
            *(f"{key}={value}" for key, value in sorted(effective_env.items())),
            *argv,
        ]
        process_env = {"PATH": BASE_ENV["PATH"]}
    else:
        process_env = effective_env
    try:
        completed = subprocess.run(
            argv,
            check=True,
            cwd=str(cwd) if cwd is not None else "/",
            env=process_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if not capture:
            if completed.stdout:
                sys.stdout.write(completed.stdout)
            if completed.stderr:
                sys.stderr.write(completed.stderr)
        return completed
    except subprocess.CalledProcessError as exc:
        if exc.stdout:
            sys.stdout.write(exc.stdout)
        if exc.stderr:
            sys.stderr.write(_redact(exc.stderr))
        detail = (exc.stderr or exc.stdout or "").strip()
        suffix = f": {_redact(detail[-2000:])}" if detail else ""
        raise PromotionError(f"command failed ({Path(command[0]).name}){suffix}") from exc


def _trusted_executable(path: Path) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PromotionError(f"deployment entrypoint is missing: {path}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not stat.S_IMODE(metadata.st_mode) & stat.S_IXUSR
    ):
        raise PromotionError(f"deployment entrypoint is not root-owned and immutable: {path}")
    return path


def release_sources(manifest: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """Collapse component entries to one pinned source per repository."""
    sources: dict[str, dict[str, str]] = {}
    for component, entry in manifest["components"].items():
        repository = COMPONENT_REPOSITORY[component]
        candidate = {"revision": entry["revision"], "artifact": entry["artifact"]}
        if repository in sources and sources[repository] != candidate:
            raise PromotionError(f"components from {repository} must use one revision and digest")
        sources[repository] = candidate
    for database, entry in manifest["migrations"].items():
        candidate = {"revision": entry["revision"], "artifact": entry["artifact"]}
        if "migrations" in sources and sources["migrations"] != candidate:
            raise PromotionError("MySQL and ClickHouse migrations must use one revision and digest")
        sources["migrations"] = candidate
    return sources


def deployment_order(manifest: Mapping[str, Any]) -> list[str]:
    """Return the fixed, reviewable production promotion order."""
    result = [f"migration:{name}" for name in ("mysql", "clickhouse") if name in manifest["migrations"]]
    if "php" in manifest["components"]:
        result.append("component:php")
    if "node" in manifest["components"]:
        result.append("component:node")
    result.extend(
        f"component:{name}" for name in GO_COMPONENT_ORDER if name in manifest["components"]
    )
    if "frontend" in manifest["components"]:
        result.append("component:frontend")
    return result


def _archive_digest(repository: Path, revision: str) -> str:
    command = [
        "/usr/sbin/runuser",
        "--user",
        DEPLOY_USER,
        "--",
        "/usr/bin/git",
        "-C",
        str(repository),
        "archive",
        "--format=tar",
        revision,
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdout is not None
    digest = hashlib.sha256()
    for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
        digest.update(chunk)
    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    return_code = process.wait()
    if return_code != 0:
        raise PromotionError(f"could not archive {repository.name}: {stderr[-2000:].strip()}")
    return f"sha256:{digest.hexdigest()}"


def _prepare_source(name: str, entry: Mapping[str, str], *, checkout: bool = True) -> None:
    repository = REPOSITORIES[name]
    if not (repository / ".git").is_dir():
        raise PromotionError(f"production checkout is missing: {repository}")
    dirty = _run(
        ["/usr/bin/git", "-C", str(repository), "status", "--porcelain", "--untracked-files=no"],
        user=DEPLOY_USER,
        capture=True,
    ).stdout.strip()
    if dirty:
        raise PromotionError(f"production checkout has tracked changes: {repository}")
    revision = entry["revision"]
    _run(["/usr/bin/git", "-C", str(repository), "fetch", "--prune", "origin"], user=DEPLOY_USER)
    _run(["/usr/bin/git", "-C", str(repository), "cat-file", "-e", f"{revision}^{{commit}}"], user=DEPLOY_USER)
    _run(
        ["/usr/bin/git", "-C", str(repository), "merge-base", "--is-ancestor", revision, "origin/main"],
        user=DEPLOY_USER,
    )
    if checkout:
        _run(["/usr/bin/git", "-C", str(repository), "reset", "--hard", revision], user=DEPLOY_USER)
    actual_digest = _archive_digest(repository, revision)
    if actual_digest != entry["artifact"]:
        raise PromotionError(
            f"source digest mismatch for {name}: expected {entry['artifact']}, got {actual_digest}"
        )


def _preflight_go_components(manifest: Mapping[str, Any]) -> None:
    """Build every selected Go command before any deployment mutation runs."""
    components = manifest["components"]
    for component in GO_COMPONENT_ORDER:
        if component not in components:
            continue
        app = GO_COMPONENT_APP[component]
        _run(
            [
                "/opt/go/bin/go",
                "build",
                "-mod=readonly",
                "-o",
                "/dev/null",
                f"./cmd/{app}",
            ],
            user=DEPLOY_USER,
            cwd=REPOSITORIES["go"],
            env={"CGO_ENABLED": "0"},
        )


def _deploy(manifest: Mapping[str, Any]) -> None:
    common = {"BUILD_USER": DEPLOY_USER, "SOURCE_PREPARED": "true"}
    migrations = manifest["migrations"]
    for database in ("mysql", "clickhouse"):
        if database not in migrations:
            continue
        script = _trusted_executable(DEPLOY_ROOT / "migrations" / f"{database}.sh")
        _run(
            [str(script)],
            user=DEPLOY_USER,
            env={
                "ENV_FILE": "/home/solo/infra/migrations/.env",
                "MIGRATIONS_DIR": str(REPOSITORIES["migrations"]),
                "REVISION": migrations[database]["revision"],
                "SOURCE_PREPARED": "true",
            },
        )

    components = manifest["components"]
    if "php" in components:
        script = _trusted_executable(DEPLOY_ROOT / "php" / "deploy.sh")
        _run([str(script)], env={**common, "REVISION": components["php"]["revision"]})

    if "node" in components:
        script = _trusted_executable(DEPLOY_ROOT / "node" / "deploy.sh")
        _run([str(script)], env={**common, "REVISION": components["node"]["revision"]})

    for component in GO_COMPONENT_ORDER:
        if component not in components:
            continue
        script = _trusted_executable(DEPLOY_ROOT / "go" / "deploy.sh")
        _run(
            [str(script), GO_COMPONENT_APP[component]],
            env={
                **common,
                "GO_BIN": "/opt/go/bin/go",
                "REVISION": components[component]["revision"],
            },
        )

    if "frontend" in components:
        script = _trusted_executable(DEPLOY_ROOT / "frontend" / "deploy.sh")
        _run([str(script)], env={**common, "REVISION": components["frontend"]["revision"]})


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--manifest", required=True, type=Path)
    result.add_argument("--release-id", required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if os.geteuid() != 0:
        print(json.dumps({"status": "error", "error": "promotion adapter must run as root"}))
        return 2
    try:
        release_protocol.validate_release_id(args.release_id)
        payload = broker._read_trusted_json(args.manifest, 0)
        manifest = broker._validate_manifest(payload, args.release_id)
        sources = release_sources(manifest)
        for name in ("migrations", "php", "node", "go", "frontend"):
            if name in sources:
                _prepare_source(name, sources[name], checkout=name != "node")
        _preflight_go_components(manifest)
        _deploy(manifest)
        result = {
            "status": "ok",
            "release_id": args.release_id,
            "order": deployment_order(manifest),
            "sources": sources,
        }
        print(json.dumps(result, sort_keys=True))
        return 0
    except (broker.BrokerError, release_protocol.ReleaseError, PromotionError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
