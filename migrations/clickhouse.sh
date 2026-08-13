#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

load_config
require_env CLICKHOUSE_DATABASE_URL
update_migrations

[ -x "$MIGRATIONS_DIR/migrate.sh" ] || \
  fail "ClickHouse migration script is missing or not executable: $MIGRATIONS_DIR/migrate.sh"

log "Running ClickHouse migrations"
(
  cd "$MIGRATIONS_DIR"
  ./migrate.sh migrations/clickhouse/ "$CLICKHOUSE_DATABASE_URL" up
)
log "ClickHouse migrations completed"
