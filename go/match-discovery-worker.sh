#!/bin/bash
# Discovers matches that need refresh and publishes durable demo-download work.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/getreplay-go.env"
[ -f "$ENV_FILE" ] || { echo "FATAL: missing $ENV_FILE (see getreplay-go.env.example)" >&2; exit 1; }
set -a; . "$ENV_FILE"; set +a

: "${DEMO_QUEUE_MAX_MESSAGE_BYTES:=1000000}"
export DEMO_QUEUE_MAX_MESSAGE_BYTES

exec "$SCRIPT_DIR/match-discovery-worker"
