#!/bin/bash
# stats-extractor, диапазон id. Достаёт из реплеев статистику по раундам —
# ретейки (cs2.retakes) и клатчи (cs2.clutches) — для матчей с id в заданном
# диапазоне (обе границы включительно). Матчи без реплея пропускаются, реплей
# читается один раз на матч и отдаётся всем расчётам.
#
# Примеры:
#   sudo -u www-data ./stats-extractor-range.sh 1 1000                    # посчитать и записать
#   sudo -u www-data env DRY_RUN=true ./stats-extractor-range.sh 1 1000   # только показать
#   sudo -u www-data env ONLY=clutch ./stats-extractor-range.sh 1 1000    # только клатчи
#   sudo -u www-data env FORCE=true ./stats-extractor-range.sh 1 1000     # пересчитать заново
#
# Прогон безопасно перезапускать: повторный проход пропустит всё, что уже
# посчитано — причём по каждому расчёту отдельно. Идти всё же лучше кусками по
# несколько тысяч id: так сводка остаётся читаемой, а падение проще разобрать.
#
# В сводке counts (по матчам): scanned, processed, already_done, skipped (нет
# реплея), missing (файла нет на диске), failed. Плюс по строке на расчёт:
# processed, already_done, empty (считать было нечего), failed, rows.
#
# Читает ML_MYSQL_DSN, CH_STORAGE_CLICKHOUSE_DSN, REPLAY_READER_REPLAY_DIR, RUN
# из getreplay-go.env.
set -euo pipefail

MIN_ID_ARG="${1:-}"
MAX_ID_ARG="${2:-}"

usage() {
  echo "usage: $0 <min-id> <max-id>   # обе границы включительно" >&2
  echo "  например: sudo -u www-data $0 1 1000" >&2
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
. "$SCRIPT_DIR/stats-extractor-common.sh"

export MIN_ID="$MIN_ID_ARG"
export MAX_ID="$MAX_ID_ARG"
export DRY_RUN="${DRY_RUN:-false}"
export FORCE="${FORCE:-false}"
export ONLY="${ONLY:-}"

exec "$BIN"
