#!/usr/bin/env bash
#
# Atomic, near-zero-downtime deploy for zone-map-ui (Next.js under systemd).
#
# Key properties:
#   - Build happens in a fresh release dir, NOT in the live directory.
#     A failing `npm ci` / `npm run build` never touches the running site.
#   - The live site is swapped by flipping one symlink (atomic).
#   - After restart a health check runs; if it fails, the previous release
#     is restored automatically.
#
# This script lives in the infra repo but operates on the app checkout on the
# server ($APP_ROOT). Layout there (create once — see DEPLOY.md):
#   $APP_ROOT/
#     repo/                     git checkout of zone-map-ui (source only)
#     releases/<timestamp>/     one full build per deploy
#     current -> releases/...   symlink the systemd unit points at
#     shared/.env.production    env file, symlinked into every release
#
# Usage:  ./deploy.sh          (config via env vars below)

set -euo pipefail

# ---- config (override via env) --------------------------------------------
APP_ROOT="${APP_ROOT:-/opt/zone-map-ui}"
BRANCH="${BRANCH:-main}"
SERVICE="${SERVICE:-nextjs.service}"
# Caddy proxies the site to [::1]:3000, so health-check the same IPv6 backend.
HEALTH_URL="${HEALTH_URL:-http://[::1]:3000/}"
KEEP="${KEEP:-5}"                                     # releases to retain
# ---------------------------------------------------------------------------

REPO="$APP_ROOT/repo"
RELEASES="$APP_ROOT/releases"
CURRENT="$APP_ROOT/current"
SHARED_ENV="$APP_ROOT/shared/.env.production"
TS="$(date +%Y-%m-%d_%H%M%S)"
REL="$RELEASES/$TS"

log() { printf '\033[1;34m[deploy %s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }

# Remember the live target so we can roll back to it.
PREV="$(readlink -f "$CURRENT" 2>/dev/null || true)"

# If we fail BEFORE the swap, drop the half-built release; the live site is
# still the old one and was never touched.
cleanup_failed() {
  if [ -d "$REL" ] && [ "$(readlink -f "$CURRENT" 2>/dev/null || true)" != "$REL" ]; then
    rm -rf "$REL"
    log "removed half-built release $TS (live site untouched)"
  fi
}
trap cleanup_failed ERR

log "fetch origin/$BRANCH"
git -C "$REPO" fetch --prune origin
git -C "$REPO" reset --hard "origin/$BRANCH"

log "stage release $TS"
mkdir -p "$REL"
cp -a "$REPO/." "$REL/"
# NEXT_PUBLIC_* are inlined at build time, so the env must exist BEFORE build.
ln -sfn "$SHARED_ENV" "$REL/.env.production"

log "npm ci + build (live site still on old release)"
cd "$REL"
npm ci
npm run build

log "swap current -> $TS (atomic)"
ln -sfn "$REL" "$CURRENT"

log "restart $SERVICE"
sudo systemctl restart "$SERVICE"

# ---- health check with auto-rollback --------------------------------------
log "health check $HEALTH_URL"
ok=0
code=""
for _ in $(seq 1 15); do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$HEALTH_URL" || true)"
  case "$code" in
    200|301|302|307|308) ok=1; break ;;
  esac
  sleep 1
done

if [ "$ok" != "1" ]; then
  log "HEALTH CHECK FAILED (last code: ${code:-none}) — rolling back"
  if [ -n "$PREV" ] && [ -d "$PREV" ]; then
    ln -sfn "$PREV" "$CURRENT"
    sudo systemctl restart "$SERVICE"
    log "rolled back to $(basename "$PREV")"
  else
    log "no previous release to roll back to — leaving as-is for inspection"
  fi
  exit 1
fi
log "healthy — live on $TS"

# ---- prune old releases (keep newest $KEEP, never the live one) ------------
live="$(readlink -f "$CURRENT")"
ls -dt "$RELEASES"/*/ 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
  [ "$(readlink -f "$old")" = "$live" ] && continue
  rm -rf "$old"
done

log "done"
