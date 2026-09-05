# getreplay infra

Single source of truth for production **infrastructure & deploy** — server config,
systemd units, and deploy scripts for getreplay.gg. The application code lives in the
sibling repos (`zone-map-ui`, `php`, `go`, …); this repo is *how it's configured and
shipped*, not *what* it does.

This is an independent git repo nested in `getreplay-workplace/` (like the app repos)
and gitignored by the parent.

## Layout

```
infra/
  caddy/Caddyfile        # prod Caddy config (TLS, routing) — source of truth
  systemd/               # service units (apps, Kafka, and durable queue workers)
  kafka/                 # Kafka KRaft config, topic provisioning, production runbook
  frontend/              # zone-map-ui deploy: deploy.sh (one command) + DEPLOY.md
  node/                  # production-only Steam GC deploy with health-gated rollback
  go/                    # Go deploy: deploy.sh <app> (builds on server) + launchers +
                         #   shared getreplay-go.env + highlight-extractor cron + README
  php/                   # native Laravel deploy + cron jobs + captured PHP config
  migrations/            # one-command MySQL and ClickHouse production migrations
  release/               # forced-command, release-only deployment gateway
  docs/topology.md       # domains, services, ports, filesystem paths
```

## Start here

- **What runs where:** [`docs/topology.md`](docs/topology.md)
- **Deploy the frontend:** [`frontend/DEPLOY.md`](frontend/DEPLOY.md)
- **Deploy PHP and the highlight feed:** [`php/README.md`](php/README.md)
- **Run database migrations:** [`migrations/README.md`](migrations/README.md)
- **Agent release access:** [`release/README.md`](release/README.md)
- **Caddy config:** [`caddy/Caddyfile`](caddy/Caddyfile) — after edits:
  `sudo cp caddy/Caddyfile /etc/caddy/Caddyfile && sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy`

## Conventions

- **Never commit secrets.** Env files (`.env.production`, credentials) live only on the
  server under `shared/`; put templates here if useful, not real values.
- **Durable demo queue:** Kafka is a loopback-only, persistent systemd service on the current
  single production host. It survives app deploys but is not host-level HA; see `kafka/README.md`.
- **Frontend deploys:** build the inactive slot, check readiness, gracefully switch
  Caddy, then retire the previous process. See `frontend/DEPLOY.md`.
- **Node 20** for the frontend, installed isolated at `/opt/node-20` so it doesn't clash
  with other node tooling on the box.

## Roadmap

- [x] Frontend one-command deploy (`frontend/deploy.sh`, isolated slots, auto Node 20).
- [x] Go services one-command deploy (`go/deploy.sh <app>`, builds on server) +
      highlight-extractor cron + shared `getreplay-go.env`.
- [x] Durable Kafka demo pipeline config with one all-in-one Match Updater service.
- [x] PHP one-command deploy (`php/deploy.sh`) + daily highlight-feed cron.
- [ ] Capture Laravel queue/scheduler units (or supervisor) and remaining crons.
- [x] Frontend builds alongside the live version (two ports + graceful `caddy reload`).
- [x] Human-approved component-specific GitHub Actions release buttons with one forced-command SSH gateway.
- [ ] Move source builds from production to prebuilt CI artifacts when release volume warrants it.
- [ ] Consider `output: 'standalone'` for the frontend to drop `npm ci` on prod.

## What to copy from prod into this repo

Config that lives outside the app repos and should be versioned here (⚠️ **never** commit
secrets — `.env`, TLS keys, DB passwords):

- **systemd** — done: nextjs, Go API/uploader/workers, Kafka, node-app. Enumerate more with
  `systemctl list-unit-files --state=enabled`.
- **PHP** — done (`php/`): deploy script, highlight-feed cron, fpm `php.ini`,
  `pool.d/*.conf`, `conf.d/*`, cli `php.ini`.
- **Go** — done (`go/`): `deploy.sh`, launchers, shared env template, highlight-extractor cron.
- **Queue workers** — if via supervisor: `/etc/supervisor/conf.d/*.conf` (still to capture).
- **Cron** — highlight extractor (`go/cron/`) and highlight feed (`php/cron/`) done.
  Still check `crontab -l` per user +
  `/etc/cron.d/*` for anything else (e.g. Laravel `schedule:run`).
- **Caddy** — `caddy/Caddyfile` (done).
