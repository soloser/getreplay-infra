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
  systemd/               # service units (nextjs, go-app, demo-uploader, node-app)
  frontend/              # zone-map-ui deploy: deploy.sh (one command) + DEPLOY.md
  go/                    # Go launch scripts (start.sh, demo-uploader.sh) + env template
  php/                   # captured php-fpm / cli configs
  docs/topology.md       # domains, services, ports, filesystem paths
```

## Start here

- **What runs where:** [`docs/topology.md`](docs/topology.md)
- **Deploy the frontend:** [`frontend/DEPLOY.md`](frontend/DEPLOY.md)
- **Caddy config:** [`caddy/Caddyfile`](caddy/Caddyfile) — after edits:
  `sudo cp caddy/Caddyfile /etc/caddy/Caddyfile && sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy`

## Conventions

- **Never commit secrets.** Env files (`.env.production`, credentials) live only on the
  server under `shared/`; put templates here if useful, not real values.
- **Atomic deploys:** build in a fresh release dir, swap a `current` symlink, health-check,
  auto-rollback on failure. See `frontend/deploy.sh`.
- **Node 20** for the frontend, installed isolated at `/opt/node-20` so it doesn't clash
  with other node tooling on the box.

## Roadmap

- [x] Frontend one-command deploy (`frontend/deploy.sh`, single service, auto Node 20).
- [ ] Capture the rest of prod config: Laravel queue/scheduler units (or supervisor),
      relevant crons, the Go `start.sh`/`demo-uploader.sh` entrypoints. See "what to copy".
- [ ] Zero-downtime frontend (blue-green: two ports + graceful `caddy reload`) — deferred.
- [ ] CI: build artifact + remote deploy.
- [ ] Consider `output: 'standalone'` for the frontend to drop `npm ci` on prod.

## What to copy from prod into this repo

Config that lives outside the app repos and should be versioned here (⚠️ **never** commit
secrets — `.env`, TLS keys, DB passwords):

- **systemd** — done: nextjs, go-app, demo-uploader, node-app. Still worth capturing any
  Laravel queue/scheduler unit + highlight-extractor timer. Enumerate:
  `systemctl list-unit-files --state=enabled`.
- **PHP** — done (`php/`): fpm `php.ini`, `pool.d/*.conf`, `conf.d/*`, cli `php.ini`.
- **Queue workers** — if via supervisor: `/etc/supervisor/conf.d/*.conf`.
- **Cron** — `crontab -l` per relevant user + `/etc/cron.d/*` (e.g. Laravel `schedule:run`).
- **Go entrypoints** — `/var/www/getreplay-go/{start.sh,demo-uploader.sh}` are captured in
  `go/` (secrets externalized to a gitignored `getreplay-go.env`).
- **Caddy** — `caddy/Caddyfile` (done).
