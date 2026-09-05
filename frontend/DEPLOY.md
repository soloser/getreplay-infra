# Выкат фронтенда без остановки на время сборки

Этот релиз меняет только механизм выката готового фронтенда. `getreplay.gg`
остаётся фронтендом, `app.getreplay.gg` — Laravel/Orchid. Дашборд, перенос
авторизации и `FRONT_URL` в этот релиз не входят.

## Подготовленные ветки

- `codex/frontend-blue-green-release` — от актуального main, со свежим
  `release/candidate.json`, без маршрутизации app-домена на Next.js.
- `codex/dashboard-app-domain-only` — сохранённая отдельная infra-ветка дашборда
  (коммит `b1445b2`). Frontend/PHP этой фичи остаются в своих отдельных ветках
  `codex/product-dashboard-app-domain`. Их пока не выкатывать.

Не запускайте release-кнопку из старой `codex/product-dashboard-app-domain`:
в ней старый `candidate.json`. После принятия нового PR запускайте **Deploy frontend**
из main. Кнопка выбирает только frontend, а не PHP/Go/миграции.

## Однократная установка — с текущего состояния сервера

Уже скопированные `nextjs@.service` и `/etc/caddy/frontend-upstream.caddy`
можно оставить. Caddy пока не менялся, `nextjs.service` продолжает работать.
Не останавливайте его вручную.

После получения подготовленного релиза в `/home/solo/infra`:

```bash
cd /home/solo/infra
sudo ./frontend/install-server.sh
```

Скрипт установит шаблон и доверенный frontend-адаптер, создаст каталоги слотов
и `/var/lib/getreplay-frontend`. Если есть старый upstream-файл, перенесёт его
значение порта в `/var/lib/getreplay-frontend/upstream.caddy`. Существующий новый
файл не перезаписывается. Старый файл не удаляется, приложение не запускается,
Caddy не перезагружается.

Обновите **только broker** существующего release gateway, когда нет текущего релиза:

```bash
sudo install -o root -g root -m 755 release/broker.py /usr/local/libexec/getreplay-release/broker.py
sudo systemctl restart getreplay-release-broker.service
sudo systemctl is-active getreplay-release-broker.service
```

Он сохраняет строгий sandbox, разрешая запись дополнительно только в два
подготовленных каталога: `/home/solo/getreplay-front-slots` и
`/var/lib/getreplay-frontend`. Права на весь `/etc/caddy`, `/home` или `/run/lock`
не открываются. Полный `release/install-server.sh` ради этого обновления не нужен;
при новой установке gateway он сам вызовет frontend installer.

В **существующем серверном** `/etc/caddy/Caddyfile` замените только блок
фронтенда `reverse_proxy [::1]:3000 { ... }` у `getreplay.gg` на:

```caddy
import /var/lib/getreplay-frontend/upstream.caddy
```

Блок `app.getreplay.gg` оставьте как есть. Если вы успели добавить старый import,
замените его путь на новый. Затем:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile &&
  sudo caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

Проверьте оба сайта: фронтенд по-прежнему работает, админка доступна как раньше.
Теперь можно запускать **Deploy frontend** из main после слияния нового PR.
Сам факт установки файлов не выкатывает новую версию приложения.

## Поведение деплоя

1. Git-снимок выбранной ревизии и `.env`, `.env.local`, `.env.production`,
   `.env.production.local` копируются в свободный слот 3000/3001. Env-файлы
   имеют режим 600 и не выводятся в логи.
2. `npm ci` и `npm run build` выполняются как `solo` через Node 20, параллельно
   работающему приложению; его `.next` и `node_modules` не меняются.
3. Новый `nextjs@<port>.service` запускается на IPv6 loopback. Требуются HTTP 200
   от `/en` и активное состояние сервиса.
4. Общий upstream-файл атомарно заменяется, Caddy валидируется и плавно перезагружается.
5. Через 30 секунд старый сервис отключается; старый управляемый слот удаляется.
   Исходный checkout не удаляется. Истории релизов нет: откат — повторная сборка
   предыдущей ревизии.

Ошибка сборки/запуска/проверки оставляет текущую версию работающей. Ошибка
переключения восстанавливает старый upstream и выполняет reload перед остановкой
кандидата. Если восстановительный reload тоже не удался, остаются оба процесса и
маркер `/var/lib/getreplay-frontend/deploy.lock.recovery`; нужно сверить активный
конфиг Caddy с файлом, успешно применить его, затем убрать маркер.

Lock не допускает два одновременных запуска адаптера. Отсутствующий import или
оставшийся прямой frontend upstream блокирует неполную миграцию. Число доменов
не фиксировано. При прерывании после успешного reload новая версия остаётся
активной, но может потребоваться завершить остановку старого сервиса.

## Настройки и проверки

`APP_ROOT`, `BRANCH`, `REVISION`, `SOURCE_PREPARED`, `BUILD_USER`, `NODE_BIN`,
`SLOTS_ROOT`, `UPSTREAM`, `CADDY_CONFIG`, `LOCK_FILE`, `HEALTH_PATH`,
`HEALTH_ATTEMPTS`, `DRAIN_SECONDS` переопределяются через env. `SERVICE` — имя
старого сервиса. Изменения путей, пользователя и Node необходимо согласовать
с systemd-шаблоном и sandbox broker; штатная установка использует defaults.

```bash
cat /var/lib/getreplay-frontend/upstream.caddy
systemctl status nextjs@3000.service nextjs@3001.service
curl --noproxy '*' -I 'http://[::1]:3001/en' # подставить активный порт
curl -I https://getreplay.gg/en
python3 -m unittest discover -s frontend/tests -v
python3 -m unittest discover -s release/tests -v
```

Локальные тесты используют command doubles; они не заменяют проверку реального
systemd/Caddy после установки. Серверу нужны ресурсы на работающий фронтенд плюс
сборку и кратковременно второй процесс. Очень длинные запросы могут выйти за
30-секундный drain. Статика предыдущей сборки переносится на один релиз для
открытых вкладок, но старые server actions/data могут потребовать обновления страницы.

Справка: [Caddy reload](https://caddyserver.com/docs/command-line#caddy-reload),
[Next.js self-hosting](https://nextjs.org/docs/app/guides/self-hosting).
