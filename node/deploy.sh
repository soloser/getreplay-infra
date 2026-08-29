#!/usr/bin/env bash

set -euo pipefail

SRC="${SRC:-/home/solo/getreplay-node}"
RELEASE_ROOT="${RELEASE_ROOT:-/home/solo/getreplay-node-releases}"
CURRENT_LINK="${CURRENT_LINK:-$RELEASE_ROOT/current}"
REVISION="${REVISION:-}"
SOURCE_PREPARED="${SOURCE_PREPARED:-false}"
BUILD_USER="${BUILD_USER:-solo}"
SERVICE="${SERVICE:-node-app.service}"
NODE_BIN="${NODE_BIN:-/usr/bin}"
UNIT_SOURCE="${UNIT_SOURCE:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/node-app.service}"
UNIT_TARGET="${UNIT_TARGET:-/etc/systemd/system/node-app.service}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:3012/health}"
HEALTH_ATTEMPTS="${HEALTH_ATTEMPTS:-90}"
HEALTH_INTERVAL_SECONDS="${HEALTH_INTERVAL_SECONDS:-2}"

log() { printf '[node-deploy] %s\n' "$*"; }
fail() { printf '[node-deploy] ERROR: %s\n' "$*" >&2; exit 1; }

run_build() {
  sudo -u "$BUILD_USER" -- env HOME="/home/$BUILD_USER" PATH="$PATH" "$@"
}

[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] || fail "REVISION must be a full lowercase commit SHA"
[[ "$HEALTH_ATTEMPTS" =~ ^[0-9]+$ ]] && [ "$HEALTH_ATTEMPTS" -ge 1 ] || \
  fail "HEALTH_ATTEMPTS must be a positive integer"
[[ "$HEALTH_INTERVAL_SECONDS" =~ ^[0-9]+$ ]] || \
  fail "HEALTH_INTERVAL_SECONDS must be a non-negative integer"
[ "$SOURCE_PREPARED" = "true" ] || fail "Node deploy requires a release-prepared immutable source"
[ -d "$SRC/.git" ] || fail "Node source checkout is missing: $SRC"
[ -f "$SRC/.env" ] || fail "production Node environment file is missing: $SRC/.env"
[ -f "$UNIT_SOURCE" ] || fail "trusted node-app unit is missing"
[ -f "$UNIT_TARGET" ] || fail "installed node-app unit is missing; rerun release/install-server.sh"
[ -d "$RELEASE_ROOT" ] || fail "release root is missing; rerun release/install-server.sh"
[ -L "$CURRENT_LINK" ] || fail "current Node release link is missing; rerun release/install-server.sh"

export PATH="$NODE_BIN:$PATH"
command -v node >/dev/null 2>&1 || fail "node is unavailable from $NODE_BIN"
command -v npm >/dev/null 2>&1 || fail "npm is unavailable from $NODE_BIN"
command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"

run_build git -C "$SRC" cat-file -e "$REVISION^{commit}"
run_build git -C "$SRC" merge-base --is-ancestor "$REVISION" origin/main

release_dir="$RELEASE_ROOT/$REVISION"
temporary_dir=""
previous_target=""
unit_backup="$RELEASE_ROOT/.node-app.service.previous.$$"
link_candidate="$RELEASE_ROOT/.current.new.$$"
health_body="$RELEASE_ROOT/.health.$$"
rollback_needed=0

write_unit_in_place() {
  local source="$1"

  # The release executor may write this allowlisted file, but its parent directory
  # intentionally stays read-only. Keep the inode and replace only its contents.
  command cat "$source" > "$UNIT_TARGET"
  cmp -s "$source" "$UNIT_TARGET" || fail "node-app unit verification failed"
}

on_exit() {
  status=$?
  if [ -n "${temporary_dir:-}" ] && [[ "$temporary_dir" == "$RELEASE_ROOT/."* ]]; then
    rm -rf -- "$temporary_dir"
  fi
  rm -f -- "$link_candidate" "$health_body"
  if [ "$status" -ne 0 ] && [ "$rollback_needed" -eq 1 ]; then
    log "deployment failed; restoring previous Node release"
    ln -s "$previous_target" "$link_candidate"
    mv -Tf "$link_candidate" "$CURRENT_LINK"
    write_unit_in_place "$unit_backup"
    systemctl daemon-reload || true
    systemctl restart "$SERVICE" || true
  fi
  rm -f -- "$unit_backup"
  exit "$status"
}
trap on_exit EXIT

if [ -f "$release_dir/.getreplay-release-ready" ] && \
   [ "$(tr -d '\r\n' < "$release_dir/.getreplay-release-ready")" = "$REVISION" ]; then
  log "reusing prepared release $REVISION"
else
  [ ! -e "$release_dir" ] || fail "incomplete release directory already exists: $release_dir"
  temporary_dir="$(mktemp -d "$RELEASE_ROOT/.${REVISION}.XXXXXX")"
  build_group="$(id -gn "$BUILD_USER")"
  chown "$BUILD_USER:$build_group" "$temporary_dir"
  run_build bash -c 'set -euo pipefail; git -C "$1" archive --format=tar "$2" | tar -xf - -C "$3"' \
    node-release "$SRC" "$REVISION" "$temporary_dir"
  (
    cd "$temporary_dir"
    run_build npm ci --omit=dev --no-audit --no-fund
  )
  printf '%s\n' "$REVISION" > "$temporary_dir/.getreplay-release-ready"
  chown "$BUILD_USER:$build_group" "$temporary_dir/.getreplay-release-ready"
  mv "$temporary_dir" "$release_dir"
  temporary_dir=""
fi

previous_target="$(readlink -f "$CURRENT_LINK")"
[ -d "$previous_target" ] || fail "current Node release target is invalid"

cp -p "$UNIT_TARGET" "$unit_backup"
rollback_needed=1

write_unit_in_place "$UNIT_SOURCE"
systemctl daemon-reload
ln -s "$release_dir" "$link_candidate"
mv -Tf "$link_candidate" "$CURRENT_LINK"

log "restarting $SERVICE once"
systemctl restart "$SERVICE"

healthy=0
for _ in $(seq 1 "$HEALTH_ATTEMPTS"); do
  if curl --fail --silent --show-error --max-time 3 "$HEALTH_URL" > "$health_body" 2>/dev/null && \
     python3 -c 'import json,sys; p=json.load(sys.stdin); c=p.get("capabilities") or {}; q=p.get("gcRequestQueue") or {}; assert p.get("status")=="ok" and p.get("steamConnected") is True and c.get("serializedMatchListRequests") is True and c.get("lateResponseQuarantine") is True and c.get("matchListTimeoutSessionRecovery") is True and c.get("productionSafetyRevision")==2 and q.get("mode")=="serial" and q.get("pending")==0 and isinstance(q.get("maxPending"),int) and q.get("maxPending")>0 and q.get("quarantined") is False' \
       < "$health_body" 2>/dev/null; then
    healthy=1
    break
  fi
  if [ "$HEALTH_INTERVAL_SECONDS" -gt 0 ]; then
    sleep "$HEALTH_INTERVAL_SECONDS"
  fi
done

[ "$healthy" -eq 1 ] || fail "new Node release did not pass the production safety health gate"
rollback_needed=0
log "done — Node $REVISION is live and productionSafetyRevision 2 is healthy"
