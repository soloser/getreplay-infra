#!/usr/bin/env bash
#
# Build a Go service ON THE SERVER and deploy it. One app per call:
#   ./deploy.sh match-updater
#   ./deploy.sh demo-uploader
#   ./deploy.sh match-discovery-worker
#   ./deploy.sh demo-downloader-worker
#   ./deploy.sh demo-processor-worker
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

wait_for_service() {
  local service="$1"
  local stable_checks=0
  local previous_restarts=""
  local current_restarts

  for ((attempt = 1; attempt <= 30; attempt++)); do
    if sudo systemctl is-active --quiet "$service"; then
      if current_restarts="$(sudo systemctl show --property=NRestarts --value "$service" 2>/dev/null)" \
        && [ -n "$current_restarts" ]; then
        if [ "$current_restarts" = "$previous_restarts" ]; then
          stable_checks=$((stable_checks + 1))
        else
          stable_checks=1
          previous_restarts="$current_restarts"
        fi
        if [ "$stable_checks" -ge 8 ]; then
          return 0
        fi
      else
        stable_checks=0
        previous_restarts=""
      fi
    else
      stable_checks=0
      previous_restarts=""
    fi
    sleep 1
  done

  return 1
}

restart_and_wait() {
  local service="$1"
  sudo systemctl restart "$service" && wait_for_service "$service"
}

case "$APP" in
  match-updater)          SERVICE="go-app.service";                    LAUNCHER="start.sh" ;;
  demo-uploader)          SERVICE="demo-uploader.service";             LAUNCHER="demo-uploader.sh" ;;
  match-discovery-worker) SERVICE="match-discovery-worker.service";    LAUNCHER="match-discovery-worker.sh" ;;
  demo-downloader-worker) SERVICE="demo-downloader-worker.service";    LAUNCHER="demo-downloader-worker.sh" ;;
  demo-processor-worker)  SERVICE="demo-processor-worker.service";     LAUNCHER="demo-processor-worker.sh" ;;
  highlight-extractor)    SERVICE="";                                  LAUNCHER="" ;;
  replay-converter)       SERVICE="";                                  LAUNCHER="" ;;
  stats-extractor)        SERVICE="";                                  LAUNCHER="" ;;
  *) die "usage: $0 <match-updater|demo-uploader|match-discovery-worker|demo-downloader-worker|demo-processor-worker|highlight-extractor|replay-converter|stats-extractor>" ;;
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
target="$BIN_DIR/$APP"
binary_backup=""
launcher_target=""
launcher_backup=""
had_binary=false
had_launcher=false

cleanup_deploy_files() {
  rm -f -- "$tmp"
  [ -z "$binary_backup" ] || rm -f -- "$binary_backup"
  [ -z "$launcher_backup" ] || rm -f -- "$launcher_backup"
}

restore_previous_service_files() {
  if [ "$had_binary" = true ]; then
    mv -f -- "$binary_backup" "$target"
    binary_backup=""
  else
    rm -f -- "$target"
  fi
  if [ "$had_launcher" = true ]; then
    mv -f -- "$launcher_backup" "$launcher_target"
    launcher_backup=""
  else
    rm -f -- "$launcher_target"
  fi
}

trap cleanup_deploy_files EXIT
( cd "$SRC" && run_build env CGO_ENABLED=0 "$GO_BIN" build -o "$tmp" "./cmd/$APP" ) || die "build failed — nothing deployed"
chmod 0755 "$tmp"

if [ -n "$SERVICE" ]; then
  binary_backup="$BIN_DIR/.$APP.previous.$$"
  launcher_target="$BIN_DIR/$LAUNCHER"
  launcher_backup="$BIN_DIR/.$LAUNCHER.previous.$$"
  if [ -f "$target" ]; then
    cp -p -- "$target" "$binary_backup"
    had_binary=true
  fi
  if [ -f "$launcher_target" ]; then
    cp -p -- "$launcher_target" "$launcher_backup"
    had_launcher=true
  fi
fi

mv -f -- "$tmp" "$target"          # atomic swap; safe on Linux even while the old binary runs
log "staged $target"

case "$APP" in
  match-updater|demo-uploader|match-discovery-worker|demo-downloader-worker|demo-processor-worker)
    if ! install -m 0755 "$INFRA_GO/$LAUNCHER" "$launcher_target"; then
      restore_previous_service_files
      die "launcher install failed; previous $APP files restored"
    fi
    log "restart $SERVICE"
    if ! restart_and_wait "$SERVICE"; then
      log "readiness failed; restoring previous $APP files"
      restore_previous_service_files
      if [ "$had_binary" = true ] && restart_and_wait "$SERVICE"; then
        die "new $APP failed readiness; previous version restored and restarted"
      fi
      sudo systemctl stop "$SERVICE" || true
      die "new $APP failed readiness; rollback could not restore a healthy service"
    fi
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
