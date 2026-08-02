#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/var/www/fun-php/repo}"
APP_ROOT="${APP_ROOT:-${REPO_ROOT}/src}"
BRANCH="${BRANCH:-main}"
PHP_BIN="${PHP_BIN:-/usr/bin/php}"
APP_USER="${APP_USER:-www-data}"
COMPOSER_BIN="${COMPOSER_BIN:-composer}"
REDIS_CLI="${REDIS_CLI:-redis-cli}"
REDIS_DEPLOY_HOST="${REDIS_DEPLOY_HOST:-127.0.0.1}"
REDIS_DEPLOY_PORT="${REDIS_DEPLOY_PORT:-6379}"
PHP_FPM_SERVICE="${PHP_FPM_SERVICE:-php8.4-fpm.service}"
BIN_DIR="${BIN_DIR:-/var/www/fun-php/bin}"
CRON_DIR="${CRON_DIR:-/etc/cron.d}"
INFRA_PHP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

maintenance_enabled=0
install_changed=0

log() {
  printf '[php-deploy] %s\n' "$*"
}

fail() {
  printf '[php-deploy] ERROR: %s\n' "$*" >&2
  exit 1
}

run_artisan() {
  (
    cd "$APP_ROOT"
    sudo -u "$APP_USER" -- "$PHP_BIN" artisan "$@"
  )
}

install_if_changed() {
  local source_file="$1"
  local target_file="$2"
  local mode="$3"

  install_changed=0

  if sudo test -f "$target_file" && sudo cmp -s "$source_file" "$target_file"; then
    log "Unchanged: $target_file"
    return 0
  fi

  sudo install -m "$mode" "$source_file" "$target_file"
  install_changed=1
  log "Updated: $target_file"
}

on_exit() {
  local status=$?

  if [ "$status" -ne 0 ] && [ "$maintenance_enabled" -eq 1 ]; then
    printf '[php-deploy] Deployment failed; Laravel remains in maintenance mode. Fix the error and run: cd %s && sudo -u %s -- %s artisan up\n' \
      "$APP_ROOT" "$APP_USER" "$PHP_BIN" >&2
  fi
}

trap on_exit EXIT

[ -d "$REPO_ROOT/.git" ] || fail "Git repository not found: $REPO_ROOT"
[ -f "$APP_ROOT/artisan" ] || fail "Laravel artisan not found: $APP_ROOT/artisan"
[ -f "$APP_ROOT/.env" ] || fail "Laravel environment file not found: $APP_ROOT/.env"
[ -x "$PHP_BIN" ] || fail "PHP binary is not executable: $PHP_BIN"
id "$APP_USER" >/dev/null 2>&1 || fail "Laravel runtime user does not exist: $APP_USER"
command -v "$COMPOSER_BIN" >/dev/null 2>&1 || fail "Composer not found: $COMPOSER_BIN"
command -v "$REDIS_CLI" >/dev/null 2>&1 || fail "redis-cli not found: $REDIS_CLI"
command -v flock >/dev/null 2>&1 || fail "flock is required (package util-linux)"
command -v systemctl >/dev/null 2>&1 || fail "systemctl is required"
[ -f "$INFRA_PHP_DIR/highlight-feed-cron.sh" ] || fail "Cron wrapper source is missing"
[ -f "$INFRA_PHP_DIR/cron/getreplay-highlight-feed" ] || fail "Cron definition source is missing"

if [ -n "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no)" ]; then
  fail "PHP checkout has local tracked changes; commit or remove them before deploying"
fi

sudo -v

if ! "$PHP_BIN" -m | awk '{ print tolower($0) }' | grep -qx redis; then
  fail "phpredis is not loaded. Install php8.4-redis and restart php8.4-fpm"
fi

redis_reply="$("$REDIS_CLI" -h "$REDIS_DEPLOY_HOST" -p "$REDIS_DEPLOY_PORT" ping 2>/dev/null)" || \
  fail "Redis is unavailable at $REDIS_DEPLOY_HOST:$REDIS_DEPLOY_PORT"
[ "$redis_reply" = "PONG" ] || fail "Unexpected Redis PING response: $redis_reply"

sudo systemctl is-active --quiet "$PHP_FPM_SERVICE" || \
  fail "$PHP_FPM_SERVICE is not active"
sudo systemctl enable --now cron >/dev/null

log "Fetching origin/$BRANCH"
git -C "$REPO_ROOT" fetch --prune origin
git -C "$REPO_ROOT" merge-base --is-ancestor HEAD "origin/$BRANCH" || \
  fail "PHP checkout contains commits not present in origin/$BRANCH"

log "Enabling Laravel maintenance mode"
run_artisan down --retry=60
maintenance_enabled=1

log "Fast-forwarding $REPO_ROOT to origin/$BRANCH"
git -C "$REPO_ROOT" merge --ff-only "origin/$BRANCH"

log "Installing production Composer dependencies"
(
  cd "$APP_ROOT"
  COMPOSER_ALLOW_SUPERUSER=1 "$COMPOSER_BIN" install \
    --no-dev \
    --prefer-dist \
    --optimize-autoloader \
    --no-interaction
)

log "Refreshing Laravel configuration cache"
run_artisan config:clear
run_artisan config:cache

sudo install -d -m 0755 "$BIN_DIR"
install_if_changed \
  "$INFRA_PHP_DIR/highlight-feed-cron.sh" \
  "$BIN_DIR/highlight-feed-cron.sh" \
  0755

cron_changed=0
for source_file in "$INFRA_PHP_DIR"/cron/*; do
  [ -f "$source_file" ] || continue
  target_file="$CRON_DIR/$(basename "$source_file")"

  install_if_changed "$source_file" "$target_file" 0644
  if [ "$install_changed" -eq 1 ]; then
    cron_changed=1
  fi
done

sudo touch /var/log/highlight-feed.log
sudo chown www-data:www-data /var/log/highlight-feed.log

if [ "$cron_changed" -eq 1 ]; then
  log "Cron definitions changed; cron will detect files in $CRON_DIR automatically"
else
  log "Cron definitions are already current"
fi

log "Reloading $PHP_FPM_SERVICE"
sudo systemctl reload "$PHP_FPM_SERVICE"

log "Disabling Laravel maintenance mode"
run_artisan up
maintenance_enabled=0

log "PHP deployment completed"
