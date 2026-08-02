#!/usr/bin/env bash

set -euo pipefail

APP_ROOT="${PHP_APP_ROOT:-/var/www/fun-php/repo/src}"
PHP_BIN="${PHP_BIN:-/usr/bin/php}"
GO_HIGHLIGHT_EXTRACTOR="${GO_HIGHLIGHT_EXTRACTOR:-/var/www/getreplay-go/highlight-extractor-cron.sh}"
LOCK_FILE="${HIGHLIGHT_FEED_LOCK_FILE:-${APP_ROOT}/storage/framework/highlight-feed.lock}"

[ -d "$APP_ROOT" ] || { echo "Laravel app not found: $APP_ROOT" >&2; exit 1; }
[ -x "$PHP_BIN" ] || { echo "PHP binary is not executable: $PHP_BIN" >&2; exit 1; }
[ -x "$GO_HIGHLIGHT_EXTRACTOR" ] || { echo "Go highlight extractor wrapper is not executable: $GO_HIGHLIGHT_EXTRACTOR" >&2; exit 1; }
command -v flock >/dev/null 2>&1 || { echo "flock is required" >&2; exit 1; }

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "Highlight feed job is already running; skipping"
  exit 0
fi

echo "Running highlight extractor"
"$GO_HIGHLIGHT_EXTRACTOR"

echo "Rebuilding highlight feed"
cd "$APP_ROOT"
"$PHP_BIN" artisan highlights:rebuild-feed --no-interaction
