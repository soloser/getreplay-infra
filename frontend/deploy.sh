#!/usr/bin/env bash
# Build an isolated inactive slot, check readiness, gracefully switch Caddy.
set -euo pipefail

# ---- config (override via env) --------------------------------------------
APP_ROOT="${APP_ROOT:-/home/solo/getreplay-front}"   # source checkout; never built in place
BRANCH="${BRANCH:-main}"
REVISION="${REVISION:-}"
SOURCE_PREPARED="${SOURCE_PREPARED:-false}"
SERVICE="${SERVICE:-nextjs.service}"
NODE_BIN="${NODE_BIN:-/opt/node-20/bin}"             # isolated Node install
SLOTS_ROOT="${SLOTS_ROOT:-/home/solo/getreplay-front-slots}"
UPSTREAM="${UPSTREAM:-/var/lib/getreplay-frontend/upstream.caddy}"
CADDY_CONFIG="${CADDY_CONFIG:-/etc/caddy/Caddyfile}"
LOCK_FILE="${LOCK_FILE:-/var/lib/getreplay-frontend/deploy.lock}"
HEALTH_PATH="${HEALTH_PATH:-/en}"
DRAIN_SECONDS="${DRAIN_SECONDS:-30}"
HEALTH_ATTEMPTS="${HEALTH_ATTEMPTS:-30}"
BUILD_USER="${BUILD_USER:-solo}"
# ---------------------------------------------------------------------------

log() { printf '\033[1;34m[deploy %s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '\033[1;31m[deploy]\033[0m %s\n' "$*" >&2; exit 1; }

# Run as root (release gateway already does), with npm/git under BUILD_USER.
[ "$(id -u)" -eq 0 ] || die "run with sudo (builds run as BUILD_USER=$BUILD_USER)"
exec 9>"$LOCK_FILE"
flock -n 9 || die "another frontend deployment is running"
[ ! -e "$LOCK_FILE.recovery" ] || die "resolve previous Caddy reload failure: $LOCK_FILE.recovery"
[ -f "$UPSTREAM" ] || die "install blue-green server setup first; see frontend/DEPLOY.md"
[ "$(awk -v upstream="$UPSTREAM" '$1 == "import" && $2 == upstream && NF == 2 {n++} END {print n+0}' "$CADDY_CONFIG")" -ge 1 ] \
  || die "frontend route must import $UPSTREAM; complete the Caddy migration first"
# Reject a partial migration, without assuming how many domains exist.
if awk '$1 == "reverse_proxy" {for (i=2; i<=NF; i++) {if ($i ~ /^#/) break; if ($i == "[::1]:3000" || $i == "[::1]:3001") found=1}} END {exit !found}' "$CADDY_CONFIG"; then
  die "replace remaining hardcoded frontend reverse_proxy blocks with import $UPSTREAM"
fi
[[ "$DRAIN_SECONDS" =~ ^[0-9]+$ ]] || die "DRAIN_SECONDS must be an integer"
[[ "$HEALTH_ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || die "HEALTH_ATTEMPTS must be positive"
old_config="$(cat "$UPSTREAM")"
case "$old_config" in
  'reverse_proxy [::1]:3000 {'$'\n    header_up X-Forwarded-Host {host}\n}') active=3000; idle=3001 ;;
  'reverse_proxy [::1]:3001 {'$'\n    header_up X-Forwarded-Host {host}\n}') active=3001; idle=3000 ;;
  *) die "unexpected frontend upstream configuration" ;;
esac
candidate="nextjs@$idle.service"
previous="nextjs@$active.service"
if systemctl is-active --quiet "$SERVICE"; then
  [ "$active" = 3000 ] || die "legacy service conflicts with blue-green routing"
  previous="$SERVICE"
fi
systemctl is-active --quiet "$previous" || die "active frontend service is not running: $previous"
caddy validate --config "$CADDY_CONFIG" --adapter caddyfile

candidate_started=0
switch_attempted=0
committed=0
write_upstream() {
  printf '%s\n' "$1" > "$UPSTREAM.next"
  chmod 644 "$UPSTREAM.next"
  mv -f "$UPSTREAM.next" "$UPSTREAM"
}
on_exit() {
  local status=$?
  trap - EXIT
  if [ "$status" -ne 0 ] && [ "$committed" -eq 0 ]; then
    if [ "$switch_attempted" -eq 1 ]; then
      write_upstream "$old_config"
      if ! caddy reload --config "$CADDY_CONFIG" --adapter caddyfile; then
        touch "$LOCK_FILE.recovery"
        log "Caddy recovery failed; BOTH services retained. Resolve $LOCK_FILE.recovery before retry."
        exit "$status"
      fi
    fi
    if [ "$candidate_started" -eq 1 ]; then
      systemctl disable --now "$candidate" || true
    fi
  fi
  exit "$status"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

run_build() {
  if [ -n "$BUILD_USER" ]; then
    sudo -u "$BUILD_USER" -- env HOME="/home/$BUILD_USER" PATH="$PATH" "$@"
  else
    "$@"
  fi
}

# --- pick the correct Node (first in PATH so npm's `env node` finds it too) ---
export PATH="$NODE_BIN:$PATH"
command -v node >/dev/null 2>&1 || die "node not found in $NODE_BIN — install it or set NODE_BIN"
cd "$APP_ROOT" || die "missing $APP_ROOT"

if [ -n "$REVISION" ] && ! [[ "$REVISION" =~ ^[0-9a-f]{40}$ ]]; then
  die "REVISION must be a full lowercase 40-character commit SHA"
fi

if [ -n "$(run_build git status --porcelain --untracked-files=no)" ]; then
  die "frontend checkout has local tracked changes; commit or remove them before deploying"
fi

if [ "$SOURCE_PREPARED" = "true" ]; then
  [ -n "$REVISION" ] || die "SOURCE_PREPARED=true requires REVISION"
  [ "$(run_build git rev-parse HEAD)" = "$REVISION" ] || die "prepared frontend revision does not match $REVISION"
  log "using prepared revision $REVISION"
else
  log "fetch origin/$BRANCH"
  run_build git fetch --prune origin
  TARGET="origin/$BRANCH"
  if [ -n "$REVISION" ]; then
    run_build git cat-file -e "$REVISION^{commit}" 2>/dev/null || die "commit is not available after fetch: $REVISION"
    run_build git merge-base --is-ancestor "$REVISION" "origin/$BRANCH" \
      || die "commit is not contained in origin/$BRANCH: $REVISION"
    TARGET="$REVISION"
  fi
  run_build git reset --hard "$TARGET"   # untracked files (.env.production, node_modules, .next) are kept
fi

# Only the inactive slot is replaced. The active tree, .next and dependencies
# remain untouched, including on the first migration from nextjs.service.
systemctl stop "$candidate"
install -d -o "$BUILD_USER" -g "$(id -gn "$BUILD_USER")" -m 755 "$SLOTS_ROOT"
slot="$SLOTS_ROOT/$idle"
[ ! -L "$slot" ] || die "slot must not be a symlink: $slot"
rm -rf -- "$slot"
install -d -o "$BUILD_USER" -g "$(id -gn "$BUILD_USER")" -m 755 "$slot"
run_build git archive HEAD | run_build tar -x -C "$slot"
# Copy Next's production env inputs, including ignored files. Never print them.
for name in .env .env.local .env.production .env.production.local; do
  if [ -f "$APP_ROOT/$name" ]; then
    run_build install -m 600 "$APP_ROOT/$name" "$slot/$name"
  fi
done
revision="$(run_build git rev-parse HEAD)"
cd "$slot"
# --- guard: running Node major must match .nvmrc ---
if [ -f .nvmrc ]; then
  want="$(tr -dc '0-9.' < .nvmrc)"; want_major="${want%%.*}"
  have_major="$(node -p 'process.versions.node.split(".")[0]')"
  [ "$want_major" = "$have_major" ] \
    || die ".nvmrc wants Node $want but $NODE_BIN is $(node -v). Install /opt/node-$want_major or set NODE_BIN."
fi
log "using $(node -v) from $NODE_BIN"

log "build $revision in inactive slot $idle; $previous keeps serving"
run_build npm ci
run_build npm run build
[ -s .next/BUILD_ID ] || die "build did not produce .next/BUILD_ID"

# Keep the previous build's static chunks for tabs already open during deploy.
# Only current build chunks are copied, avoiding unbounded history accumulation.
if [ "$previous" = "$SERVICE" ]; then old_tree="$APP_ROOT"; else old_tree="$SLOTS_ROOT/$active"; fi
if [ -d "$old_tree/.next/static" ]; then
  run_build cp -a .next/static .next/current-static
  static_source="$old_tree/.next/current-static"
  [ -d "$static_source" ] || static_source="$old_tree/.next/static"
  run_build cp -an "$static_source/." .next/static/
fi
candidate_started=1
systemctl enable --now "$candidate"
log "check candidate on [::1]:$idle$HEALTH_PATH"
ok=0
for _ in $(seq 1 "$HEALTH_ATTEMPTS"); do
  code="$(curl --noproxy '*' -s -o /dev/null -w '%{http_code}' --max-time 3 "http://[::1]:$idle$HEALTH_PATH" || true)"
  if [ "$code" = 200 ] && systemctl is-active --quiet "$candidate"; then ok=1; break; fi
  sleep 1
done
[ "$ok" = 1 ] || die "candidate failed readiness; old frontend remains live"

log "switch Caddy to $idle"
switch_attempted=1
write_upstream "$(printf 'reverse_proxy [::1]:%s {\n    header_up X-Forwarded-Host {host}\n}' "$idle")"
caddy validate --config "$CADDY_CONFIG" --adapter caddyfile
caddy reload --config "$CADDY_CONFIG" --adapter caddyfile
committed=1
log "new version live; draining old requests for $DRAIN_SECONDS seconds"
sleep "$DRAIN_SECONDS"
systemctl disable --now "$previous"
# No release history: remove only the retired managed slot, never the source checkout.
if [ "$previous" != "$SERVICE" ]; then
  rm -rf -- "$SLOTS_ROOT/$active"
fi
log "done — $revision live on $candidate"
