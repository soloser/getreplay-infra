#!/bin/bash
# replay-converter, диапазон id. Перегоняет в формат v2 реплеи матчей с id в
# заданном диапазоне (обе границы включительно): пишет новые .replay2,
# переставляет matches.replay_name / replay_meta и удаляет старые файлы.
# Матчи без реплея и уже перегнанные пропускаются.
#
# Примеры:
#   # только прикинуть выигрыш — ничего не меняется
#   sudo -u www-data ./replay-converter-range.sh 1 1000
#
#   # перегнать по-настоящему (спросит подтверждение и покажет, сколько матчей)
#   sudo -u www-data env DRY_RUN=false ./replay-converter-range.sh 1 1000
#
#   # без вопросов, для скрипта
#   sudo -u www-data env DRY_RUN=false YES=true ./replay-converter-range.sh 1 1000
#
# DRY_RUN=true по умолчанию — операция необратима, старые файлы удаляются.
# Идти лучше кусками по несколько сотен id: так падение проще разобрать, а
# сводка по экономии остаётся читаемой.
#
# Читает ML_MYSQL_DSN, REPLAY_READER_REPLAY_DIR, REPLAY_WRITER_REPLAY_DIR,
# REPLAY_WRITER_REPLAY_COMPRESSION, RUN из getreplay-go.env.
set -euo pipefail

MIN_ID_ARG="${1:-}"
MAX_ID_ARG="${2:-}"

usage() {
  echo "usage: $0 <min-id> <max-id>   # обе границы включительно" >&2
  echo "  например: sudo -u www-data env DRY_RUN=false $0 1 1000" >&2
  exit 1
}

[[ "$MIN_ID_ARG" =~ ^[0-9]+$ ]] || usage
[[ "$MAX_ID_ARG" =~ ^[0-9]+$ ]] || usage
[ "$MIN_ID_ARG" -gt 0 ] || usage

if [ "$MIN_ID_ARG" -gt "$MAX_ID_ARG" ]; then
  echo "FATAL: min-id больше max-id" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/replay-converter-common.sh"

export MIN_ID="$MIN_ID_ARG"
export MAX_ID="$MAX_ID_ARG"
export DRY_RUN="${DRY_RUN:-true}"

if [ "$DRY_RUN" = "true" ]; then
  echo "Сухой прогон для id $MIN_ID..$MAX_ID. Перегнать: DRY_RUN=false $0 $MIN_ID $MAX_ID"
fi

exec "$BIN"
