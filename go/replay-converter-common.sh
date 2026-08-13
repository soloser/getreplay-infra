# Общая часть обёрток replay-converter: находит env и бинарь.
# Подключается через `. "$SCRIPT_DIR/replay-converter-common.sh"`, SCRIPT_DIR уже задан.
#
# Обёртки работают из каталога установки (по умолчанию /var/www/getreplay-go), где рядом
# лежат бинарь и общий getreplay-go.env. В исходном чекауте infra их запускать нельзя —
# там нет ни того, ни другого; ставит их `deploy.sh replay-converter`.

ENV_FILE="$SCRIPT_DIR/getreplay-go.env"
BIN="$SCRIPT_DIR/replay-converter"

not_installed_here() {
  echo "FATAL: $1" >&2
  echo >&2
  echo "  Обёртка запускается из каталога установки, рядом с бинарём и общим env," >&2
  echo "  а не из чекаута infra. Поставить и запустить:" >&2
  echo >&2
  echo "    /home/solo/infra/go/deploy.sh replay-converter" >&2
  echo "    cd /var/www/getreplay-go" >&2
  echo "    sudo -u www-data ./$(basename "$0") $2" >&2
  exit 1
}

[ -f "$ENV_FILE" ] || not_installed_here "не вижу $ENV_FILE" "$*"
[ -x "$BIN" ]      || not_installed_here "не вижу бинарь $BIN" "$*"

set -a; . "$ENV_FILE"; set +a
