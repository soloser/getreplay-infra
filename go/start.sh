#!/bin/bash
# match-updater launcher (getreplay.gg Go API, Caddy /srv/* → :3006).
# Committable: real secrets are sourced from getreplay-go.env (prod-only, gitignored).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/getreplay-go.env"
[ -f "$ENV_FILE" ] || { echo "FATAL: missing $ENV_FILE (see getreplay-go.env.example)" >&2; exit 1; }

# Secrets: ML_MYSQL_DSN, CH_STORAGE_CLICKHOUSE_DSN, JWT_SECRET, STEAM_API_TOKEN
set -a; . "$ENV_FILE"; set +a

# --- non-secret config ---
export ML_MYSQL_READ_TIMEOUT=2m
# ⚠ binds 0.0.0.0 — publicly reachable unless the firewall blocks :3006. Caddy only
#   needs localhost; consider 127.0.0.1:3006 / [::1]:3006. See infra/docs/topology.md.
export SERVER_TCP_ADDR=0.0.0.0:3006
export STATCHANNEL_PRINT_STATS=false
export DOWNLOADER_NUM_WORKERS=4
export DOWNLOADER_TARGET_DIR="/var/www/getreplay-go/downloads"
export UPLOADS_DIR="/var/www/getreplay-go/downloads"
export PARSER_NUM_WORKERS=6
export PARSER_REPLAY_SAMPLING_RATE=16
export MATCHUPDATER_REPLAY_DIR="/var/www/getreplay-storage/replays"
export REPLAY_WRITER_REPLAY_DIR="/var/www/getreplay-storage/replays"
export REPLAY_READER_REPLAY_DIR="/var/www/getreplay-storage/replays"
export REPLAY_WRITER_REPLAY_COMPRESSION="zstd"
export RUN=true

# DEMO_MATCH_ID is a debug single-match override (bound only by match-updater). The
# author's run-prod-example.sh does NOT set it in prod — left disabled. Uncomment only
# for a one-off reprocess of a specific match.
# export DEMO_MATCH_ID=112426

exec "$SCRIPT_DIR/match-updater"
