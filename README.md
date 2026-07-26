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
  systemd/               # service units (nextjs.service, …)
  frontend/              # zone-map-ui deploy: deploy.sh + DEPLOY.md runbook
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

- [ ] Capture Go backend + php-fpm systemd units here too.
- [ ] Blue-green frontend (two ports + Caddy reload) for true zero-downtime.
- [ ] CI: build artifact + remote deploy (see the CI section in `frontend/DEPLOY.md`).
- [ ] Consider `output: 'standalone'` for the frontend to drop `npm ci` on prod.
