#!/bin/bash
# highlight-extractor cron wrapper. Runner: one pass over newly-parsed matches, extract
# feed highlights from .replay2 into the `highlights` table, then exits. Idempotent —
# skips matches that already have highlights unless REEXTRACT=true.
#
# Reads ML_MYSQL_DSN, ML_MYSQL_READ_TIMEOUT, REPLAY_READER_REPLAY_DIR, RUN from getreplay-go.env
# (no ClickHouse needed). Installed next to the binary by `deploy.sh highlight-extractor`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/getreplay-go.env"
[ -f "$ENV_FILE" ] || { echo "FATAL: missing $ENV_FILE (see getreplay-go.env.example)" >&2; exit 1; }
set -a; . "$ENV_FILE"; set +a

# Lookback window (days) — matches selected by finished_at. Cron default 7 (margin for
# parse delay). Override for a one-off backfill:
#   sudo -u www-data env LOOKBACK_DAYS=3650 ./highlight-extractor-cron.sh
# Other one-off knobs the binary accepts as env: REEXTRACT=true, MATCH_ID=<id>.
export LOOKBACK_DAYS="${LOOKBACK_DAYS:-7}"

exec "$SCRIPT_DIR/highlight-extractor"
