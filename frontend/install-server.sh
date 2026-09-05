#!/usr/bin/env bash
# Provision only frontend deployment assets; never reload Caddy or deploy an app.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo 'run with sudo' >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_ROOT=/var/lib/getreplay-frontend
SLOTS_ROOT=/home/solo/getreplay-front-slots
DEPLOY_ROOT=/usr/local/libexec/getreplay-release/deploy/frontend
LEGACY_UPSTREAM=/etc/caddy/frontend-upstream.caddy

# Parents must exist before systemd constructs the executor mount sandbox.
install -d -o root -g root -m 755 "$STATE_ROOT" "$DEPLOY_ROOT"
exec 9>"$STATE_ROOT/deploy.lock"
flock -n 9 || { echo 'frontend deployment is running' >&2; exit 1; }
# Also coordinate with the first version of the deployment adapter.
exec 8>/run/lock/getreplay-frontend.lock
flock -n 8 || { echo 'legacy frontend deployment is running' >&2; exit 1; }
install -d -o solo -g "$(id -gn solo)" -m 755 "$SLOTS_ROOT"

if [ ! -e "$STATE_ROOT/upstream.caddy" ]; then
  source_file="$INFRA_ROOT/caddy/frontend-upstream.caddy"
  # Preserve any port selected by the earlier instructions; never reset it to 3000.
  if [ -f "$LEGACY_UPSTREAM" ]; then source_file="$LEGACY_UPSTREAM"; fi
  case "$(cat "$source_file")" in
    'reverse_proxy [::1]:3000 {'$'\n    header_up X-Forwarded-Host {host}\n}'|'reverse_proxy [::1]:3001 {'$'\n    header_up X-Forwarded-Host {host}\n}') ;;
    *) echo 'unexpected upstream configuration; reconcile it before installation' >&2; exit 1 ;;
  esac
  install -o root -g root -m 644 "$source_file" "$STATE_ROOT/upstream.caddy"
fi
install -o root -g root -m 644 "$INFRA_ROOT/systemd/nextjs@.service" /etc/systemd/system/nextjs@.service
install -o root -g root -m 755 "$SCRIPT_DIR/deploy.sh" "$DEPLOY_ROOT/deploy.sh"
systemctl daemon-reload
printf '%s\n' 'Frontend assets installed. Update the release broker and the existing frontend Caddy import as described in frontend/DEPLOY.md.'
