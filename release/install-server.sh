#!/usr/bin/env bash

set -euo pipefail

PUBLIC_KEY_FILE="${1:-}"
DEPLOY_USER="${DEPLOY_USER:-solo}"
RELEASE_USER="getreplay-release"
RELEASE_GROUP="getreplay-release"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LIB_ROOT="/usr/local/libexec/getreplay-release"
DEPLOY_ROOT="$LIB_ROOT/deploy"

fail() {
  printf '[release-install] ERROR: %s\n' "$*" >&2
  exit 1
}

log() {
  printf '[release-install] %s\n' "$*"
}

[ "${EUID:-$(id -u)}" -eq 0 ] || fail "run this installer with sudo"
[ -n "$PUBLIC_KEY_FILE" ] || fail "usage: sudo ./release/install-server.sh /path/to/github-release.pub"
[ -f "$PUBLIC_KEY_FILE" ] || fail "public key file not found: $PUBLIC_KEY_FILE"
id "$DEPLOY_USER" >/dev/null 2>&1 || fail "production deploy user does not exist: $DEPLOY_USER"
[ ! -e "/etc/sudoers.d/$RELEASE_USER" ] || \
  fail "remove the unexpected sudoers file first: /etc/sudoers.d/$RELEASE_USER"

public_key="$(tr -d '\r\n' < "$PUBLIC_KEY_FILE")"
[[ "$public_key" =~ ^(ssh-ed25519|sk-ssh-ed25519@openssh.com)[[:space:]][A-Za-z0-9+/]+={0,3}([[:space:]].*)?$ ]] || \
  fail "the release key must be one Ed25519 public key"

for required_path in \
  /home/solo/getreplay-front/.git \
  /home/solo/getreplay-go/.git \
  /home/solo/fun-migrations/migrations/.git \
  /var/www/fun-php/repo/.git \
  /home/solo/infra/migrations/.env; do
  [ -e "$required_path" ] || fail "required production path is missing: $required_path"
done

getent group "$RELEASE_GROUP" >/dev/null || groupadd --system "$RELEASE_GROUP"
if ! id "$RELEASE_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "/home/$RELEASE_USER" \
    --shell /bin/sh --gid "$RELEASE_GROUP" "$RELEASE_USER"
fi
usermod --shell /bin/sh "$RELEASE_USER"
passwd --lock "$RELEASE_USER" >/dev/null 2>&1 || true

install -d -o root -g root -m 0755 "$LIB_ROOT" "$LIB_ROOT/adapters"
install -o root -g root -m 0755 \
  "$SCRIPT_DIR/broker.py" \
  "$SCRIPT_DIR/forced_command.py" \
  "$SCRIPT_DIR/getreplay_release.py" \
  "$SCRIPT_DIR/release_client.py" \
  "$SCRIPT_DIR/release_protocol.py" \
  "$LIB_ROOT/"
install -o root -g root -m 0755 \
  "$SCRIPT_DIR/promote_release.py" \
  "$LIB_ROOT/adapters/promote-release"

install -d -o root -g root -m 0755 \
  "$DEPLOY_ROOT/frontend" \
  "$DEPLOY_ROOT/go/cron" \
  "$DEPLOY_ROOT/php/cron" \
  "$DEPLOY_ROOT/migrations"
install -o root -g root -m 0755 "$INFRA_ROOT/frontend/deploy.sh" "$DEPLOY_ROOT/frontend/deploy.sh"
install -o root -g root -m 0755 "$INFRA_ROOT/go/"*.sh "$DEPLOY_ROOT/go/"
install -o root -g root -m 0644 "$INFRA_ROOT/go/cron/"* "$DEPLOY_ROOT/go/cron/"
install -o root -g root -m 0755 "$INFRA_ROOT/php/deploy.sh" "$INFRA_ROOT/php/highlight-feed-cron.sh" "$DEPLOY_ROOT/php/"
install -o root -g root -m 0644 "$INFRA_ROOT/php/cron/"* "$DEPLOY_ROOT/php/cron/"
install -o root -g root -m 0755 \
  "$INFRA_ROOT/migrations/common.sh" \
  "$INFRA_ROOT/migrations/mysql.sh" \
  "$INFRA_ROOT/migrations/clickhouse.sh" \
  "$DEPLOY_ROOT/migrations/"

install -o root -g root -m 0644 \
  "$INFRA_ROOT/systemd/getreplay-release-broker.service" \
  /etc/systemd/system/getreplay-release-broker.service

install -d -o root -g root -m 0755 "/home/$RELEASE_USER"
install -d -o root -g root -m 0755 "/home/$RELEASE_USER/.ssh"
authorized_keys="$(mktemp)"
trap 'rm -f "$authorized_keys"' EXIT
printf 'restrict,command="/usr/bin/python3 %s/forced_command.py" %s\n' "$LIB_ROOT" "$public_key" > "$authorized_keys"
install -o root -g root -m 0644 "$authorized_keys" "/home/$RELEASE_USER/.ssh/authorized_keys"

systemctl daemon-reload
systemctl enable --now cron
systemctl enable --now getreplay-release-broker.service
systemctl restart getreplay-release-broker.service

systemctl is-active --quiet getreplay-release-broker.service
/usr/bin/python3 "$LIB_ROOT/getreplay_release.py" status >/dev/null

log "installed release-only gateway"
log "release account: $RELEASE_USER (forced command only, no sudo, root-owned authorized_keys)"
log "next: add the private key and host key to the protected GitHub production environment"
