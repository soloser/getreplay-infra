#!/bin/bash
# demo-uploader launcher. Standalone /upload service (separate binary from match-updater;
# PHP/Orchid forwards demo uploads here over SERVICE_TOKEN).
# Shared config + secrets come from getreplay-go.env; only per-service params live here.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/getreplay-go.env"
[ -f "$ENV_FILE" ] || { echo "FATAL: missing $ENV_FILE (see getreplay-go.env.example)" >&2; exit 1; }
set -a; . "$ENV_FILE"; set +a

# --- per-service ---
# ⚠ binds 0.0.0.0 — uploads come from PHP on the same host, so 127.0.0.1:3005 is likely
#   enough. See ../docs/topology.md.
export SERVER_TCP_ADDR=0.0.0.0:3005
export PARSER_NUM_WORKERS=1
export PARSER_REPLAY_SAMPLING_RATE=8

exec "$SCRIPT_DIR/demo-uploader"
