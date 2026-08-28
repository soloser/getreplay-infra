# Go services deploy

Five Go binaries from `github.com/soloser/getreplay-go`, all living in `/var/www/getreplay-go/`:

| App | How it runs | Unit / trigger | Port |
|---|---|---|---|
| `match-updater` | long-running | `go-app.service` → `start.sh` | `:3006` (Caddy `/srv/*`) |
| `demo-uploader` | long-running | `demo-uploader.service` → `demo-uploader.sh` | `:3005` (uploads from PHP) |
| `highlight-extractor` | one-shot runner | **cron** hourly → `highlight-extractor-cron.sh` | — |
| `replay-converter` | one-shot runner | **by hand** → `replay-converter-{match,range}.sh` | — |
| `stats-extractor` | one-shot runner | **by hand** → `stats-extractor-{match,range}.sh` | — |

**Build happens on the server** (no more local cross-compile + scp). All runtime params live
in one file — [`getreplay-go.env`](getreplay-go.env.example) — sourced by every launcher; each
launcher only adds its per-service ports/worker-counts.

## Deploy — one command per app

```bash
/home/solo/infra/go/deploy.sh match-updater
/home/solo/infra/go/deploy.sh demo-uploader
/home/solo/infra/go/deploy.sh highlight-extractor
/home/solo/infra/go/deploy.sh replay-converter
/home/solo/infra/go/deploy.sh stats-extractor
```

Each: `git reset --hard origin/main` in the source checkout → `go build` (CGO off, static) →
atomic-swap the binary in `/var/www/getreplay-go/` → for a service, restart it; for
highlight-extractor, (re)install its cron wrapper + `/etc/cron.d` entry. A failed build deploys
nothing. Config via env: `SRC`, `BIN_DIR`, `BRANCH`, `GO_BIN`, `LOG_DIR`.

## One-time server setup

```bash
# 1. Go toolchain (>= 1.24; go.mod pins toolchain go1.24.5, auto-fetched by a recent Go)
curl -fsSL https://go.dev/dl/go1.24.5.linux-amd64.tar.gz | sudo tar -xz -C /opt
#   put it on PATH for your deploy shell, or run deploy.sh with GO_BIN=/opt/go/bin/go
echo 'export PATH=/opt/go/bin:$PATH' >> ~/.profile

# 2. Source checkout (build reads from here)
git clone git@github.com:soloser/getreplay-go.git /home/solo/getreplay-go

# 3. The shared env file (secrets live only here, on prod)
cd /var/www/getreplay-go
cp /home/solo/infra/go/getreplay-go.env.example getreplay-go.env
sudo -e getreplay-go.env                        # fill secrets
sudo chown www-data:www-data getreplay-go.env   # services + cron run as www-data
sudo chmod 600 getreplay-go.env

# 4. Ensure the units are installed (once), then deploy each app
sudo cp /home/solo/infra/systemd/{go-app,demo-uploader}.service /etc/systemd/system/
sudo systemctl daemon-reload
/home/solo/infra/go/deploy.sh match-updater
/home/solo/infra/go/deploy.sh demo-uploader
/home/solo/infra/go/deploy.sh highlight-extractor   # also wires up the cron
```

## highlight-extractor: cron + backfill

`deploy.sh highlight-extractor` installs `/etc/cron.d/getreplay-highlight-extractor` (hourly at
:20, as www-data, logs to `/var/log/highlight-extractor.log`) and the wrapper. The runner is
idempotent, so overlap/re-runs are safe.

**One-off full backfill** (e.g. after a migration, or after importing old pro demos parsed long
after they finished — the hourly `LOOKBACK_DAYS=7` window would miss those):

```bash
sudo -u www-data env LOOKBACK_DAYS=3650 /var/www/getreplay-go/highlight-extractor-cron.sh
```

Other one-off knobs (env, read by the binary): `REEXTRACT=true` (delete + regenerate for the
window, after tuning detectors), `MATCH_ID=<id>` (single match).

## replay-converter: перегонка архива в формат v2

Разовая операция: переписывает старые реплеи (`.replay.gz` и `.replay2` версии 1) в формат v2 —
покадровые данные квантованными дельтами, **−66%** на файл. Переставляет `matches.replay_name` /
`replay_meta` и удаляет старый файл. Уже перегнанные пропускает. Порядок операций подобран так,
чтобы матч оставался рабочим при падении на любом шаге: новый файл → БД → удаление старого.

Формат выкатывается в три шага (подробности в
[replay-format.md §8.1](https://github.com/soloser/zone-map-ui/blob/main/docs/replay-format.md)):
сначала фронт (он читает обе версии), потом `demo-uploader`, и только потом архив. Перегонка
не срочная — старые файлы продолжают работать сколько угодно.

**`DRY_RUN=true` по умолчанию** у обеих обёрток: операция необратима. Сухой прогон считает выигрыш
точно — пишет временную копию и сразу её убирает, БД и исходник не трогает.

```bash
cd /var/www/getreplay-go

# один матч
sudo -u www-data ./replay-converter-match.sh 12345                    # прикинуть
sudo -u www-data env DRY_RUN=false ./replay-converter-match.sh 12345  # перегнать

# диапазон id, обе границы включительно
sudo -u www-data ./replay-converter-range.sh 1 1000
sudo -u www-data env DRY_RUN=false ./replay-converter-range.sh 1 1000
```

Диапазон спрашивает подтверждение перед стартом и показывает в вопросе, сколько матчей попало в
выборку (`YES=true` пропускает — для скриптов). Идти лучше кусками по несколько сотен id: падение
проще разобрать, сводка читаемее.

В конце выводится сводка: `saved_total`, `saved_per_file`, `saved_percent`, `size_before` /
`size_after` и счётчики `converted` / `already_new` / `missing` / `failed` / `orphaned_files`.
`orphaned_files` — редкий случай: БД уже смотрит на новый файл, а старый удалить не вышло; данные
целы, просто занято место.

## stats-extractor: добор статистики по раундам из старых реплеев

Считает поверх уже записанных реплеев построундовую статистику и пишет её в ClickHouse:

| Расчёт | Таблица | Строка |
|---|---|---|
| `retake` | `cs2.retakes` | раунд с установленной бомбой |
| `clutch` | `cs2.clutches` | сторона раунда, у которой остался один живой (в 1v1 — обе) |

Слева в строке признаки ситуации на её старте (численность, HP, оружие, утилита, дистанции), справа
исход. Новые матчи считает сам парсер сразу после разбора демки, поэтому команда нужна только для
матчей, распарсенных до появления расчёта.

Прогон **безопасно перезапускать**, причём пропуск пофактный: матч с посчитанными ретейками, но без
клатчей, досчитается по клатчам и не тронет ретейки. Реплей при этом читается один раз на матч и
отдаётся всем расчётам.

- `ONLY=retake` / `ONLY=clutch` (через запятую) — гонять только выбранные расчёты, по умолчанию все;
- `DRY_RUN=true` — посчитать и показать объём, ничего не записывая;
- `FORCE=true` — пересчитать даже посчитанное. Дублей не будет: таблицы на `ReplacingMergeTree` и
  схлопывают строки по своему ключу, оставляя последнюю по `extracted_at`.

```bash
cd /var/www/getreplay-go

# один матч
sudo -u www-data ./stats-extractor-match.sh 12345
sudo -u www-data env DRY_RUN=true ./stats-extractor-match.sh 12345   # только показать

# диапазон id, обе границы включительно
sudo -u www-data ./stats-extractor-range.sh 1 1000
sudo -u www-data env ONLY=clutch ./stats-extractor-range.sh 1 1000   # добрать только клатчи
```

В сводке `counts` (по матчам): `scanned`, `processed`, `already_done`, `skipped` (нет реплея),
`missing` (файла нет на диске — для заархивированных матчей это норма), `failed`. Сумма исходов
должна сходиться со `scanned`. Плюс отдельная строка на каждый расчёт: `processed`, `already_done`,
`empty` (считать было нечего), `failed`, `rows`.

## Notes

- Binaries build as your deploy user and run 0755, so the `www-data` services execute them fine.
- `getreplay-go.env` must be readable by `www-data` (owner www-data, 0600) — the services and the
  cron all run as www-data.
- Security: the two services bind `0.0.0.0:3005/3006` — reachable externally unless firewalled.
  Caddy only proxies localhost; consider switching to `127.0.0.1`. Flagged in the launchers.
