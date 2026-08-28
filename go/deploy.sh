#!/usr/bin/env bash
#
# Build a Go service ON THE SERVER and deploy it. One app per call:
#   ./deploy.sh match-updater
#   ./deploy.sh demo-uploader
#   ./deploy.sh highlight-extractor
#   ./deploy.sh replay-converter
#   ./deploy.sh stats-extractor
#
# Replaces the old flow (local `GOOS=linux GOARCH=amd64 go build` + scp + systemctl
# restart). Builds from the git checkout at $SRC, drops the binary in $BIN_DIR, keeps
# the launcher .sh in sync, then restarts the service — or, for the runners that have
# no service, installs their wrappers (highlight-extractor also gets a /etc/cron.d
# entry; replay-converter is run by hand).
set -euo pipefail

APP="${1:-}"

# ---- config (override via env) --------------------------------------------
SRC="${SRC:-/home/solo/getreplay-go}"           # git clone of github.com/soloser/getreplay-go
BIN_DIR="${BIN_DIR:-/var/www/getreplay-go}"      # binaries + launcher .sh + getreplay-go.env
BRANCH="${BRANCH:-main}"
REVISION="${REVISION:-}"
SOURCE_PREPARED="${SOURCE_PREPARED:-false}"
BUILD_USER="${BUILD_USER:-}"
GO_BIN="${GO_BIN:-go}"
LOG_DIR="${LOG_DIR:-/var/log}"
# ---------------------------------------------------------------------------

INFRA_GO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # this dir (infra/go on the server)
log() { printf '\033[1;34m[go-deploy %s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '\033[1;31m[go-deploy]\033[0m %s\n' "$*" >&2; exit 1; }

run_build() {
  if [ -n "$BUILD_USER" ]; then
    sudo -u "$BUILD_USER" -- env HOME="/home/$BUILD_USER" PATH="$PATH" "$@"
  else
    "$@"
  fi
}

case "$APP" in
  match-updater)       SERVICE="go-app.service";        LAUNCHER="start.sh" ;;
  demo-uploader)       SERVICE="demo-uploader.service"; LAUNCHER="demo-uploader.sh" ;;
  highlight-extractor) SERVICE="";                      LAUNCHER="" ;;
  replay-converter)    SERVICE="";                      LAUNCHER="" ;;
  stats-extractor)     SERVICE="";                      LAUNCHER="" ;;
  *) die "usage: $0 <match-updater|demo-uploader|highlight-extractor|replay-converter|stats-extractor>" ;;
esac

command -v "$GO_BIN" >/dev/null 2>&1 || die "Go not found ($GO_BIN) — install Go >= 1.24 (see README.md)"
[ -d "$SRC/.git" ] || die "no go checkout at $SRC — run: git clone git@github.com:soloser/getreplay-go.git $SRC"
[ -d "$BIN_DIR" ]  || die "missing $BIN_DIR"

if [ -n "$REVISION" ] && ! [[ "$REVISION" =~ ^[0-9a-f]{40}$ ]]; then
  die "REVISION must be a full lowercase 40-character commit SHA"
fi

if [ -n "$(run_build git -C "$SRC" status --porcelain --untracked-files=no)" ]; then
  die "Go checkout has local tracked changes; commit or remove them before deploying"
fi

if [ "$SOURCE_PREPARED" = "true" ]; then
  [ -n "$REVISION" ] || die "SOURCE_PREPARED=true requires REVISION"
  [ "$(run_build git -C "$SRC" rev-parse HEAD)" = "$REVISION" ] || die "prepared Go revision does not match $REVISION"
  log "using prepared revision $REVISION"
else
  log "update source ($BRANCH)"
  run_build git -C "$SRC" fetch --prune origin
  TARGET="origin/$BRANCH"
  if [ -n "$REVISION" ]; then
    run_build git -C "$SRC" cat-file -e "$REVISION^{commit}" 2>/dev/null || die "commit is not available after fetch: $REVISION"
    run_build git -C "$SRC" merge-base --is-ancestor "$REVISION" "origin/$BRANCH" \
      || die "commit is not contained in origin/$BRANCH: $REVISION"
    TARGET="$REVISION"
  fi
  run_build git -C "$SRC" reset --hard "$TARGET"
fi
log "at $(run_build git -C "$SRC" rev-parse --short HEAD)"

log "build $APP (CGO off — a static binary, like the old cross-build)"
tmp="$BIN_DIR/.$APP.new.$$"
trap 'rm -f "$tmp"' EXIT
( cd "$SRC" && run_build env CGO_ENABLED=0 "$GO_BIN" build -o "$tmp" "./cmd/$APP" ) || die "build failed — nothing deployed"
chmod 0755 "$tmp"
mv -f "$tmp" "$BIN_DIR/$APP"          # atomic swap; safe on Linux even while the old binary runs
trap - EXIT
log "installed $BIN_DIR/$APP"

case "$APP" in
  match-updater|demo-uploader)
    install -m 0755 "$INFRA_GO/$LAUNCHER" "$BIN_DIR/$LAUNCHER"   # keep the launcher in sync
    log "restart $SERVICE"
    sudo systemctl restart "$SERVICE"
    log "done — $APP live ($SERVICE)"
    ;;

  highlight-extractor)
    # cron runner. Install its wrapper + /etc/cron.d entry (idempotent).
    install -m 0755 "$INFRA_GO/highlight-extractor-cron.sh" "$BIN_DIR/highlight-extractor-cron.sh"
    sudo install -m 0644 "$INFRA_GO/cron/getreplay-highlight-extractor" /etc/cron.d/getreplay-highlight-extractor
    sudo touch "$LOG_DIR/highlight-extractor.log"
    sudo chown www-data:www-data "$LOG_DIR/highlight-extractor.log" 2>/dev/null || true
    log "done — highlight-extractor built; cron installed (hourly at :20)"
    log "one-off backfill: sudo -u www-data env LOOKBACK_DAYS=3650 $BIN_DIR/highlight-extractor-cron.sh"
    ;;

  replay-converter)
    # Разовый runner, запускается руками: ни сервиса, ни крона. Ставим обёртки.
    install -m 0755 "$INFRA_GO/replay-converter-common.sh" "$BIN_DIR/replay-converter-common.sh"
    install -m 0755 "$INFRA_GO/replay-converter-match.sh" "$BIN_DIR/replay-converter-match.sh"
    install -m 0755 "$INFRA_GO/replay-converter-range.sh" "$BIN_DIR/replay-converter-range.sh"
    log "done — replay-converter built; обёртки в $BIN_DIR"
    log "один матч:  sudo -u www-data env DRY_RUN=false $BIN_DIR/replay-converter-match.sh <id>"
    log "диапазон:   sudo -u www-data $BIN_DIR/replay-converter-range.sh <min-id> <max-id>"
    ;;

  stats-extractor)
    # Разовый runner для добора статистики (ретейки, клатчи) из старых реплеев:
    # новые матчи считает сам парсер. Запускается руками, обёртки рядом с бинарём.
    install -m 0755 "$INFRA_GO/stats-extractor-common.sh" "$BIN_DIR/stats-extractor-common.sh"
    install -m 0755 "$INFRA_GO/stats-extractor-match.sh" "$BIN_DIR/stats-extractor-match.sh"
    install -m 0755 "$INFRA_GO/stats-extractor-range.sh" "$BIN_DIR/stats-extractor-range.sh"
    log "done — stats-extractor built; обёртки в $BIN_DIR"
    log "один матч:  sudo -u www-data $BIN_DIR/stats-extractor-match.sh <id>"
    log "диапазон:   sudo -u www-data $BIN_DIR/stats-extractor-range.sh <min-id> <max-id>"
    ;;
esac
