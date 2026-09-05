# Отложенный выкат дашборда

Эта отдельная infra-ветка `codex/dashboard-app-domain-only` содержит только
отложенную маршрутизацию app-домена и её описание поверх подготовленного
frontend-only релиза. Её не нужно включать в текущий выкат фронтенда.

Разница с `codex/frontend-blue-green-release` — Caddy и документация топологии.
После принятия frontend-only PR можно отдельно интегрировать эту ветку.
`frontend/DEPLOY.md` описывает базовую установку; указания в нём сохранить
app-домен за Laravel относятся к первому, frontend-only этапу.

Перед активацией app-домена нужны:

- перенос frontend dashboard/auth-handoff из локального `48622af` и PHP API из
  `6d0faab` (ветки `codex/product-dashboard-app-domain`) на актуальные версии;
- проверки совместимости с более свежими исправлениями и новые release-кандидаты;
- согласованный переход PHP `FRONT_URL` на `https://app.getreplay.gg/token` и
  app-origin настройки фронтенда;
- проверка OAuth, переноса сессии, /dashboard, /backend и текущих URL.

Текущие закреплённые в release/candidate.json frontend/PHP не содержат эту фичу.
Поэтому эта ветка сама по себе не готовый production-релиз дашборда. Caddy меняет
основной обработчик app.getreplay.gg с Laravel на Next.js; это не чисто аддитивная
перемена. Во время текущей задачи production не изменялся.
