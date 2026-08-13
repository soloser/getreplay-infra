#!/bin/bash
# replay-converter, диапазон дат. Перегоняет в формат v2 реплеи матчей,
# завершившихся в заданном окне: пишет новые .replay2, переставляет
# matches.replay_name / replay_meta и удаляет старые файлы. Уже перегнанные
# реплеи пропускаются.
#
# Даты вводятся как YYYY-MM-DD, обе границы включительно (весь день).
#
# Примеры:
#   # весь январь 2025, только прикинуть выигрыш — ничего не меняется
#   sudo -u www-data ./replay-converter-range.sh 2025-01-01 2025-01-31
#
#   # тот же январь, перегнать по-настоящему (спросит подтверждение)
#   sudo -u www-data env DRY_RUN=false ./replay-converter-range.sh 2025-01-01 2025-01-31
#
#   # один день, без вопросов (для скрипта)
#   sudo -u www-data env DRY_RUN=false YES=true ./replay-converter-range.sh 2025-03-14 2025-03-14
#
# DRY_RUN=true по умолчанию — операция необратима, старые файлы удаляются.
# Идти лучше кусками по месяцу-два: так падение проще разобрать, а сводка
# по экономии остаётся читаемой.
#
# Читает ML_MYSQL_DSN, REPLAY_READER_REPLAY_DIR, REPLAY_WRITER_REPLAY_DIR,
# REPLAY_WRITER_REPLAY_COMPRESSION, RUN из getreplay-go.env.
set -euo pipefail

DATE_FROM="${1:-}"
DATE_TO="${2:-}"

usage() {
  echo "usage: $0 <YYYY-MM-DD> <YYYY-MM-DD>   # обе даты включительно" >&2
  echo "  например: sudo -u www-data env DRY_RUN=false $0 2025-01-01 2025-01-31" >&2
  exit 1
}

[[ "$DATE_FROM" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || usage
[[ "$DATE_TO"   =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || usage
# YYYY-MM-DD сравнивается как строка корректно.
if [[ "$DATE_FROM" > "$DATE_TO" ]]; then
  echo "FATAL: первая дата позже второй" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/replay-converter-common.sh"

command -v mysql >/dev/null 2>&1 || { echo "FATAL: нужен клиент mysql (apt install mariadb-client)" >&2; exit 1; }

# Сам конвертер отбирает матчи по id, а не по дате: это позволяет ему листать
# таблицу одним индексным диапазоном. Даты в id переводим здесь.
# Разбираем ML_MYSQL_DSN вида user:pass@tcp(host:port)/db — @ и : в пароле
# учтены (режем по последнему @ и первому : в паре логин/пароль).
DSN="${ML_MYSQL_DSN:-}"
[ -n "$DSN" ] || { echo "FATAL: ML_MYSQL_DSN не задан в $ENV_FILE" >&2; exit 1; }

DSN_CREDS="${DSN%@*}"
DSN_REST="${DSN##*@}"
DB_USER="${DSN_CREDS%%:*}"
DB_PASS="${DSN_CREDS#*:}"
DB_HOSTPORT="${DSN_REST#*(}"; DB_HOSTPORT="${DB_HOSTPORT%%)*}"
DB_HOST="${DB_HOSTPORT%%:*}"
DB_PORT="${DB_HOSTPORT##*:}"
DB_NAME="${DSN_REST#*/}"; DB_NAME="${DB_NAME%%\?*}"

[ -n "$DB_HOST" ] && [ -n "$DB_NAME" ] || {
  echo "FATAL: не разобрал ML_MYSQL_DSN, ожидался вид user:pass@tcp(host:port)/db" >&2
  exit 1
}

# Пароль отдаём через MYSQL_PWD, а не аргументом: аргументы видны в ps.
db_query() { MYSQL_PWD="$DB_PASS" mysql \
  --host="$DB_HOST" --port="${DB_PORT:-3306}" --user="$DB_USER" --database="$DB_NAME" \
  --batch --skip-column-names --execute="$1"; }

WINDOW="finished_at >= '$DATE_FROM 00:00:00'
    AND finished_at <  DATE_ADD('$DATE_TO 00:00:00', INTERVAL 1 DAY)
    AND replay_name IS NOT NULL AND replay_name <> ''"

read -r MIN_ID_Q MAX_ID_Q IN_WINDOW <<<"$(db_query "
  SELECT COALESCE(MIN(id), 0), COALESCE(MAX(id), 0), COUNT(*)
  FROM matches WHERE $WINDOW;")"

if [ "$IN_WINDOW" -eq 0 ]; then
  echo "В окне $DATE_FROM..$DATE_TO нет матчей с реплеями — делать нечего."
  exit 0
fi

# id растёт по времени вставки, а не по finished_at: матч, распарсенный сильно
# позже, получает больший id. Поэтому диапазон id может захватить соседей за
# пределами окна. Показываем оба числа, чтобы разница была видна до запуска.
# Лишний матч не проблема — он всё равно подлежит перегонке, просто раньше срока.
IN_SPAN="$(db_query "
  SELECT COUNT(*) FROM matches
  WHERE id BETWEEN $MIN_ID_Q AND $MAX_ID_Q
    AND replay_name IS NOT NULL AND replay_name <> '';")"

echo "Окно $DATE_FROM..$DATE_TO: матчей с реплеями $IN_WINDOW, id с $MIN_ID_Q по $MAX_ID_Q"
if [ "$IN_SPAN" -ne "$IN_WINDOW" ]; then
  echo "В диапазон id попадает $IN_SPAN матчей — на $((IN_SPAN - IN_WINDOW)) больше, чем в окне дат:"
  echo "  id нумеруются по времени добавления, поздно распарсенные матчи выбиваются из порядка."
fi

export MIN_ID="$MIN_ID_Q"
export MAX_ID="$MAX_ID_Q"
export DRY_RUN="${DRY_RUN:-true}"

if [ "$DRY_RUN" = "true" ]; then
  echo "Сухой прогон. Перегнать: DRY_RUN=false $0 $DATE_FROM $DATE_TO"
fi

exec "$BIN"
