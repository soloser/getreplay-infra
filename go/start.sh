#!/bin/bash
# match-updater launcher (getreplay.gg Go API, Caddy /srv/* → :3006).
# Shared config + secrets come from getreplay-go.env (prod-only, gitignored).
# Only per-service params live here.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/getreplay-go.env"
[ -f "$ENV_FILE" ] || { echo "FATAL: missing $ENV_FILE (see getreplay-go.env.example)" >&2; exit 1; }
set -a; . "$ENV_FILE"; set +a

# --- per-service ---
: "${DEMO_QUEUE_MAX_MESSAGE_BYTES:=1000000}"
export DEMO_QUEUE_MAX_MESSAGE_BYTES
# ⚠ binds 0.0.0.0 — reachable externally unless firewalled. Caddy only needs localhost;
#   consider 127.0.0.1:3006 / [::1]:3006. See ../docs/topology.md.
export SERVER_TCP_ADDR=0.0.0.0:3006
export STATCHANNEL_PRINT_STATS=false
export DOWNLOADER_NUM_WORKERS=4
export PARSER_NUM_WORKERS=6
: "${PARSER_REPLAY_SAMPLING_RATE:=16}"
export PARSER_REPLAY_SAMPLING_RATE
# REQUIRED by match-updater (CheckRange ≥1) — reference match for the debug nade-stats
# endpoint (HandleDemoNadeStats). Omitting it makes match-updater fail validation on start.
export DEMO_MATCH_ID=112426

exec "$SCRIPT_DIR/match-updater"
