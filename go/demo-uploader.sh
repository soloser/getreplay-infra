#!/bin/bash
# demo-uploader launcher. Standalone /upload service (separate binary from match-updater;
# PHP/Orchid forwards demo uploads here over SERVICE_TOKEN).
# Committable: real secrets are sourced from getreplay-go.env (prod-only, gitignored).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/getreplay-go.env"
[ -f "$ENV_FILE" ] || { echo "FATAL: missing $ENV_FILE (see getreplay-go.env.example)" >&2; exit 1; }

# Secrets used here: ML_MYSQL_DSN, CH_STORAGE_CLICKHOUSE_DSN, SERVICE_TOKEN.
# (The shared env file also carries JWT_SECRET/STEAM_API_TOKEN for match-updater;
#  demo-uploader does not read them — harmless.)
set -a; . "$ENV_FILE"; set +a

# --- non-secret config (trimmed to what demo-uploader actually binds) ---
export ML_MYSQL_READ_TIMEOUT=2m
# ⚠ binds 0.0.0.0 — reachable externally unless firewalled. Uploads come from PHP on the
#   same host, so 127.0.0.1:3005 is likely enough. See infra/docs/topology.md.
export SERVER_TCP_ADDR=0.0.0.0:3005
export UPLOADS_DIR="/var/www/getreplay-go/downloads"
export REPLAY_WRITER_REPLAY_DIR="/var/www/getreplay-storage/replays"
export REPLAY_WRITER_REPLAY_COMPRESSION="zstd"
export PARSER_NUM_WORKERS=1
export PARSER_REPLAY_SAMPLING_RATE=8
export RUN=true

exec "$SCRIPT_DIR/demo-uploader"
