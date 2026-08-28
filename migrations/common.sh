#!/usr/bin/env bash

set -euo pipefail

MIGRATIONS_INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-$MIGRATIONS_INFRA_DIR/.env}"

log() {
  printf '[migrations] %s\n' "$*"
}

fail() {
  printf '[migrations] ERROR: %s\n' "$*" >&2
  exit 1
}

require_env() {
  local name="$1"
  [ -n "${!name:-}" ] || fail "$name is not set in $ENV_FILE"
}

load_config() {
  [ -f "$ENV_FILE" ] || fail "Environment file not found: $ENV_FILE (copy .env.example to .env)"

  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a

  MIGRATIONS_DIR="${MIGRATIONS_DIR:-/home/solo/fun-migrations/migrations}"
  MIGRATIONS_REMOTE="${MIGRATIONS_REMOTE:-origin}"
  MIGRATIONS_BRANCH="${MIGRATIONS_BRANCH:-main}"
  REVISION="${REVISION:-}"
  SOURCE_PREPARED="${SOURCE_PREPARED:-false}"

  [ -d "$MIGRATIONS_DIR/.git" ] || fail "Git repository not found: $MIGRATIONS_DIR"
  command -v git >/dev/null 2>&1 || fail "git is not installed"
}

update_migrations() {
  local current_branch

  current_branch="$(git -C "$MIGRATIONS_DIR" branch --show-current)"
  [ "$current_branch" = "$MIGRATIONS_BRANCH" ] || \
    fail "Expected branch $MIGRATIONS_BRANCH, found ${current_branch:-detached HEAD} in $MIGRATIONS_DIR"

  if [ -n "$(git -C "$MIGRATIONS_DIR" status --porcelain --untracked-files=no)" ]; then
    fail "Migration checkout has local tracked changes: $MIGRATIONS_DIR"
  fi

  if [ "$SOURCE_PREPARED" = "true" ]; then
    [ -n "$REVISION" ] || fail "SOURCE_PREPARED=true requires REVISION"
    [ "$(git -C "$MIGRATIONS_DIR" rev-parse HEAD)" = "$REVISION" ] || \
      fail "Prepared migrations revision does not match $REVISION"
    log "Using prepared migrations revision $REVISION"
    return 0
  fi

  log "Pulling $MIGRATIONS_REMOTE/$MIGRATIONS_BRANCH"
  git -C "$MIGRATIONS_DIR" pull --ff-only "$MIGRATIONS_REMOTE" "$MIGRATIONS_BRANCH"
}
