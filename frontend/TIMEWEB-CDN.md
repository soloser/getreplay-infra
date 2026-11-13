# Timeweb CDN: фронтенд и медиа

Подготовлено для РФ и Европы. Используется pull CDN: Timeweb забирает файлы с
текущего сервера и кэширует их. S3, перенос 20–30 ГБ файлов и новый ключ Timeweb
в GitHub Actions не нужны. Изменения в репозитории сами по себе не включают CDN.

## Домены и источники

| Назначение | Домен посетителя | Источник Timeweb (HTTPS, Host и SNI) |
| --- | --- | --- |
| JS, CSS, шрифты, public-файлы, оптимизированные картинки | `static.getreplay.gg` | `assets-origin.getreplay.gg` |
| Загруженные фото/видео и реплеи | `media.getreplay.gg` | `storage.getreplay.gg` |

`getreplay.gg` продолжает отдавать HTML, API, авторизацию и WebSocket.
`app.getreplay.gg` остаётся админкой. CDN не исправит отказ PHP/Go/Next или
блокировку самого основного домена: это отдельная часть доступности.

## Однократное включение

1. Установить обновлённый доверенный адаптер на сервере:
   `sudo ./frontend/install-server.sh` из проверенной infra-ревизии.
   Он также устанавливает `publish-assets.py`. Действующий release gateway уже
   разрешает запись в `/var/lib/getreplay-frontend`; расширять sandbox не нужно.
   Нужен Python 3 (уже используется release gateway).
2. DNS A для `assets-origin.getreplay.gg` направить на текущий сервер. AAAA
   добавлять только при проверенной работоспособности IPv6. `storage.getreplay.gg`
   сохранить прямым источником, не превращать в CNAME собственного CDN.
3. Перенести новые CDN-блоки из `caddy/Caddyfile` в серверный конфиг, сверив
   существующие настройки. Применить после `caddy validate` через плавный reload.
   Caddy выпускает сертификат источника. Каталог assets находится в
   `/var/lib/getreplay-frontend/assets`; это постоянное хранилище вне слотов Next.
4. В Timeweb создать два CDN-ресурса с доменами и источниками из таблицы.
   Для HTTPS-запросов к источнику указать соответствующие Host и SNI.
   Создать CNAME `static` и `media` **на значения, выданные Timeweb**, включить
   HTTPS-сертификаты CDN и дождаться готовности обоих ресурсов.
5. Настроить кэширование по таблице ниже. Проверить, что источники доступны с CDN.
6. В существующем серверном `.env.production` фронтенда установить:

   ```dotenv
   NEXT_PUBLIC_STATIC_CDN_URL=https://static.getreplay.gg
   NEXT_PUBLIC_MEDIA_CDN_URL=https://media.getreplay.gg
   NEXT_PUBLIC_MEDIA_ORIGIN_URL=https://storage.getreplay.gg
   ```

   Параметры публичные и читаются **при сборке**; смена требует нового деплоя.
   Более приоритетная `.env.production.local` не должна переопределять их старыми
   значениями. Пример также есть в `zone-map-ui/cdn.env.example`.
7. В Laravel настроить существующие переменные:

   ```dotenv
   PUBLIC_STORAGE_URL=https://media.getreplay.gg
   NEXT_PUBLIC_REPLAY_URL=https://media.getreplay.gg/replays/
   ```

   `NEXT_ADMIN_REPLAY_IFRAME_URL` остаётся URL страницы проигрывателя фронтенда.
   Обновить Laravel config cache штатным PHP-деплоем. Имена файлов, каталог public,
   база данных и загрузка новых файлов не меняются. AttachmentPresenter и HTML
   статей с attachment ID уже используют PUBLIC_STORAGE_URL. Старые снимки CMS
   обновятся по своему TTL; фронтенд также заменяет старый storage-домен в media
   props. Вручную записанные URL в произвольном HTML нужно отдельно проверить.
8. Выбрать ревизию фронтенда с этой интеграцией через существующий release
   candidate и запустить обычный **Deploy frontend** в GitHub Actions.
   Никаких дополнительных секретов или шагов загрузки в Actions не требуется:
   workflow вызывает release gateway, а он — обновлённый серверный адаптер.

## Параметры CDN

| Настройка | Значение |
| --- | --- |
| Origin protocol | HTTPS; исходный Host/SNI из таблицы доменов |
| Query string | Сохранять все параметры в запросе и в ключе кэша; обязательно `url`, `w`, `q` для `/_next/image` |
| TTL | Учитывать Cache-Control источника; не перекрывать no-store принудительным TTL |
| Ошибки | Не кэшировать 4xx/5xx, особенно 404 |
| Методы | GET, HEAD, OPTIONS; не направлять на CDN API/POST |
| Range | Сохранять Range, If-Range, Accept-Ranges, Content-Range, Content-Length |
| CORS | Сохранять заголовки источника; `Access-Control-Allow-Origin: *` для публичных файлов |
| Cookies / Authorization | Не отправлять источнику, не включать в ключ кэша |
| Медиа | Выключить gzip/Brotli и преобразования файлов; байты `.replay2` должны сохраняться точно |
| Большие файлы | Включить блочное кэширование Timeweb для медиа; проверить реальные `.replay2` с запросами диапазонов |
| Статика | Стандартное сжатие JS/CSS допустимо с корректным Vary: Accept-Encoding; не сжимать видео |

Caddy возвращает годовой immutable TTL только для версионных файлов frontend.
Для медиа — один час: реплей может быть пересобран под тем же именем. При срочной
замене медиа очистить конкретный URL в Timeweb. Для frontend очистка CDN при
обычных деплоях не нужна. Оптимизатор изображений всегда запрашивает WebP у Next,
поэтому один CDN-ключ не смешивает AVIF/WebP в зависимости от браузера.

## Автоматический деплой и откат

Адаптер создаёт уникальный `.cdn-release` в новом слоте **до сборки**. Next
фиксирует этот идентификатор в `assetPrefix` и в URL public-файлов. После сборки
публикуются только `.next/static` и `public` в `assets/releases/<release>`:
без серверной сборки, env-файлов, скрытых файлов и source maps. Симлинки запрещены.
Публикация каталога атомарная; существующие версии никогда не перезаписываются.

До запуска новой версии адаптер сравнивает байты двух реальных файлов через CDN
с результатом сборки (JS и public). Ошибка DNS/HTTPS/CDN, неверный маршрут,
нехватка места или ошибка копирования прерывает деплой, старая версия продолжает
работать. Затем действуют прежние readiness-проверка и плавное переключение Caddy.

Предыдущие каталоги CDN сохраняются после удаления старого Next-слота, поэтому
старые ссылки на JS/CSS и public не исчезают. Это не сохраняет старые server
actions/RSC: старым вкладкам иногда всё равно требуется обновление страницы.
История файлов занимает диск на origin. Автоудаления нет: контролировать размер
`assets/releases`, удалять только явно выбранные старые версии после принятого
срока поддержки вкладок. Начальная политика — хранить минимум 30 дней и никогда
не удалять версии работающих слотов. Не удалять весь `assets` при обычном деплое.

Для отката настройки CDN очистить обе NEXT_PUBLIC_*_CDN_URL и повторно собрать
фронтенд штатным Actions. Laravel вернуть `PUBLIC_STORAGE_URL` и replay base на
storage-домен и обновить config cache. Сохранённые CDN-файлы при откате не удалять.

Для ручной сборки с CDN создать `.cdn-release` с уникальным идентификатором
(буквы/цифры/`_`/`-`, до 96 символов). Копировать только git SHA нельзя: повторная
сборка той же ревизии с другой конфигурацией должна иметь новый URL.

## Проверки

Локальные воспроизводимые проверки, без production:

```bash
python3 -m unittest discover -s frontend/tests -v
bash -n frontend/deploy.sh frontend/install-server.sh
# Из корня infra; использует только одноразовый контейнер, без внешней сети:
docker run --rm --network none --entrypoint sh -v "$PWD:/work:ro" caddy:latest /work/caddy/tests/cdn-smoke.sh
# Из zone-map-ui:
npm run test:cdn
npx tsc --noEmit --incremental false
```

Перед включением трафика проверить реальные CDN-ссылки из собранной страницы:
JS/CSS/font, public-картинку, `/_next/image?url=...&w=...&q=...`, видео и реплей.
Для `.replay2` нужны HEAD и GET `Range: bytes=0-65535`: статус 206, точный
Content-Range, отсутствие Content-Encoding и совпадение байтов с источником.
Проверить второй диапазон, OPTIONS/CORS и отсутствующий файл (404 + no-store).
Повторить из РФ и Европы: локальный тест не проверяет доступность у провайдеров.
Проверить второй подряд деплой и доступность URL первого релиза.

Ссылки: [Timeweb CDN](https://timeweb.cloud/docs/cdn),
[источники и домены](https://timeweb.cloud/docs/cdn/settings/origin-and-domains),
[кэширование](https://timeweb.cloud/docs/cdn/settings/cache),
[большие файлы](https://timeweb.cloud/docs/cdn/settings/performance),
[Next assetPrefix](https://nextjs.org/docs/15/app/api-reference/config/next-config-js/assetPrefix).
