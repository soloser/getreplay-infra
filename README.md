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
  caddy/
    Caddyfile             # prod Caddy config (TLS, routing) — source of truth
    frontend-upstream.caddy  # active blue/green upstream (swapped by deploy.sh)
  systemd/                # service units (nextjs-blue/green, old nextjs.service ref)
  frontend/              # zone-map-ui blue-green deploy: deploy.sh + DEPLOY.md runbook
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

- [x] Blue-green frontend (blue :3000 / green :3001 + Caddy graceful reload).
- [ ] Capture the rest of prod config: Go backend unit, php-fpm pool + php.ini,
      Laravel queue/scheduler units (or supervisor), relevant crons. See "what to copy" below.
- [ ] CI: build artifact + remote deploy (see the CI section in `frontend/DEPLOY.md`).
- [ ] Consider `output: 'standalone'` for the frontend to drop `npm ci` on prod.

## What to copy from prod into this repo

Config that lives outside the app repos and should be versioned here (⚠️ **never** commit
secrets — `.env`, TLS keys, DB passwords):

- **systemd** — every custom unit/timer in `/etc/systemd/system/*.{service,timer}`
  (frontend done; still need: Go backend, Laravel queue/scheduler, any cron-timers,
  demo-uploader, highlight-extractor). Enumerate: `systemctl list-unit-files --state=enabled`.
- **PHP** — `/etc/php/8.4/fpm/php.ini`, `/etc/php/8.4/fpm/pool.d/*.conf` (defines the
  `/run/php/php8.4-fpm.sock` socket, user, pm settings), custom drop-ins in
  `/etc/php/8.4/fpm/conf.d/`, and `/etc/php/8.4/cli/php.ini` if artisan relies on it.
- **Queue workers** — if via supervisor: `/etc/supervisor/conf.d/*.conf`.
- **Cron** — `crontab -l` per relevant user + `/etc/cron.d/*` (e.g. Laravel `schedule:run`).
- **Caddy** — `caddy/Caddyfile` (done) + `frontend-upstream.caddy`.

Suggested layout as you add them: `systemd/`, `php/{fpm,cli}/`, `cron/`, `supervisor/`.
