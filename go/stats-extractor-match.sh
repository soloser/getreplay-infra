#!/bin/bash
# stats-extractor, один матч. Достаёт из реплея статистику по раундам —
# ретейки (cs2.retakes) и клатчи (cs2.clutches) — и пишет её в ClickHouse.
#
# Пропуск устроен пофактно: расчёт, по которому у матча уже есть строки,
# пропускается, а недостающий досчитывается. Пересчитать всё-таки можно через
# FORCE=true — дублей не будет, таблицы на ReplacingMergeTree.
#
# Примеры:
#   sudo -u www-data ./stats-extractor-match.sh 12345                     # посчитать и записать
#   sudo -u www-data env DRY_RUN=true ./stats-extractor-match.sh 12345    # только показать
#   sudo -u www-data env ONLY=clutch ./stats-extractor-match.sh 12345     # только клатчи
#   sudo -u www-data env FORCE=true ./stats-extractor-match.sh 12345      # пересчитать заново
#
# В отличие от replay-converter операция ничего не удаляет и не переписывает
# файлы, поэтому DRY_RUN по умолчанию выключен.
#
# Читает ML_MYSQL_DSN, CH_STORAGE_CLICKHOUSE_DSN, REPLAY_READER_REPLAY_DIR, RUN
# из getreplay-go.env.
set -euo pipefail

MATCH_ID_ARG="${1:-}"
if ! [[ "$MATCH_ID_ARG" =~ ^[0-9]+$ ]] || [ "$MATCH_ID_ARG" -eq 0 ]; then
  echo "usage: $0 <match-id>" >&2
  echo "  например: sudo -u www-data $0 12345" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/stats-extractor-common.sh"

export MATCH_ID="$MATCH_ID_ARG"
export DRY_RUN="${DRY_RUN:-false}"
export FORCE="${FORCE:-false}"
export ONLY="${ONLY:-}"

exec "$BIN"
