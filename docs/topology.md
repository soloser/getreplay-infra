# Production topology

Single host. **Caddy** terminates TLS (automatic, ACME email `blackangelnk@gmail.com`)
and reverse-proxies to the services below. Full config: [`../caddy/Caddyfile`](../caddy/Caddyfile).

## Domains → services

| Domain / path | Backend | Notes |
|---|---|---|
| `getreplay.gg` `/srv/*` | Go backend `localhost:3006` | |
| `getreplay.gg` `/api/*` | PHP-FPM (Laravel) `unix//run/php/php8.4-fpm.sock`, root `/var/www/fun-php/repo/src/public` | `/api` prefix stripped (`handle_path`) |
| `getreplay.gg` (everything else) | **Next.js frontend** `[::1]:3000` | IPv6 localhost only |
| `www.getreplay.gg` | — | 308 redirect → `getreplay.gg` |
| `app.getreplay.gg` | PHP-FPM (Laravel/Orchid admin), same root | `X-Frame-Options: SAMEORIGIN` |
| `storage.getreplay.gg` | static file_server | replays + Laravel public storage, CORS `*` |

### Frame/embed rule (main site)
- Default: `X-Frame-Options: DENY`.
- `/admin/replay-embed*` (`@replay_embed`): drops `X-Frame-Options`, sets
  `Content-Security-Policy: frame-ancestors https://app.getreplay.gg` — so replay-embed
  pages can be iframed **only** by the admin app. Don't "simplify" this to a blanket DENY.

## Services & ports

| Service | Port / socket | Runtime | User | Unit / entrypoint |
|---|---|---|---|---|
| Frontend (zone-map-ui) | `[::1]:3000` | **Node 20** (`/opt/node-20`) | solo | `nextjs.service` |
| Go backend | `localhost:3006` (per Caddy `/srv/*`) | Go | www-data | `go-app.service` → `/var/www/getreplay-go/start.sh` |
| Demo uploader | — (worker) | Go | www-data | `demo-uploader.service` → `/var/www/getreplay-go/demo-uploader.sh` |
| Node backend | (internal) | **system Node 18** | solo | `node-app.service` → `/home/solo/getreplay-node`, `/usr/bin/npm start`, EnvFile `.env` |
| PHP API + admin | `unix//run/php/php8.4-fpm.sock` | PHP 8.4-FPM | www-data | `php8.4-fpm.service` (distro) |
| Caddy | 80/443 | — | — | `caddy.service` |

> ⚠️ **Two Node runtimes coexist.** The frontend runs **Node 20** isolated at `/opt/node-20`
> (patched `sharp` needs ≥20.9), referenced only by `nextjs.service`. The
> `node-app.service` backend still runs on **system Node 18** (`/usr/bin/npm`). Installing
> Node 20 in `/opt` leaves system node untouched, so node-app is unaffected — do not replace
> system node. See [`../frontend/DEPLOY.md`](../frontend/DEPLOY.md).
>
> The Go units call helper scripts (`start.sh`, `demo-uploader.sh`) that hold the actual run
> command/flags/port — capture those too (they live in `/var/www/getreplay-go/`).

## Filesystem paths

| What | Path |
|---|---|
| Frontend | `/home/solo/getreplay-front` (git checkout = systemd WorkingDirectory) |
| PHP / Laravel | `/var/www/fun-php/repo/src/...` |
| Replay storage | `/var/www/getreplay-storage/` (`replays/` + `*.replay2`) |
| Laravel public storage | `/var/www/fun-php/repo/src/storage/app/public` |
| Caddyfile | `/etc/caddy/Caddyfile` |

## Deploying config changes

```bash
sudo cp caddy/Caddyfile /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy      # graceful
```
