#!/bin/bash
# replay-converter, один матч. Перегоняет реплей матча в формат v2: пишет новый
# .replay2, переставляет matches.replay_name / replay_meta и удаляет старый файл.
# Уже перегнанные реплеи пропускаются.
#
# Пример:
#   sudo -u www-data ./replay-converter-match.sh 12345                 # прикинуть (dry-run)
#   sudo -u www-data env DRY_RUN=false ./replay-converter-match.sh 12345   # перегнать
#
# DRY_RUN=true по умолчанию — операция необратима, старый файл удаляется.
# Сухой прогон точно считает выигрыш: он пишет временную копию и сразу её убирает,
# БД и исходный файл не трогает.
#
# Читает ML_MYSQL_DSN, REPLAY_READER_REPLAY_DIR, REPLAY_WRITER_REPLAY_DIR,
# REPLAY_WRITER_REPLAY_COMPRESSION, RUN из getreplay-go.env.
set -euo pipefail

MATCH_ID_ARG="${1:-}"
if ! [[ "$MATCH_ID_ARG" =~ ^[0-9]+$ ]] || [ "$MATCH_ID_ARG" -eq 0 ]; then
  echo "usage: $0 <match-id>" >&2
  echo "  например: sudo -u www-data env DRY_RUN=false $0 12345" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/replay-converter-common.sh"

export MATCH_ID="$MATCH_ID_ARG"
export DRY_RUN="${DRY_RUN:-true}"

if [ "$DRY_RUN" = "true" ]; then
  echo "Сухой прогон для матча $MATCH_ID. Перегнать: DRY_RUN=false $0 $MATCH_ID"
fi

exec "$BIN"
