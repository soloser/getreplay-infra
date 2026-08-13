#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

load_config
require_env MYSQL_DATABASE_URL
update_migrations

[ -x "$MIGRATIONS_DIR/bin/migrate_linux" ] || \
  fail "MySQL migration binary is missing or not executable: $MIGRATIONS_DIR/bin/migrate_linux"

log "Running MySQL migrations"
(
  cd "$MIGRATIONS_DIR"
  ./bin/migrate_linux -path=migrations/mysql -database="$MYSQL_DATABASE_URL" up
)
log "MySQL migrations completed"
