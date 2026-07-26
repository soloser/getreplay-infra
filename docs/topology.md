# Production topology

Single host. **Caddy** terminates TLS (automatic, ACME email `blackangelnk@gmail.com`)
and reverse-proxies to the services below. Full config: [`../caddy/Caddyfile`](../caddy/Caddyfile).

## Domains → services

| Domain / path | Backend | Notes |
|---|---|---|
| `getreplay.gg` `/srv/*` | Go backend `localhost:3006` | |
| `getreplay.gg` `/api/*` | PHP-FPM (Laravel) `unix//run/php/php8.4-fpm.sock`, root `/var/www/fun-php/repo/src/public` | `/api` prefix stripped (`handle_path`) |
| `getreplay.gg` (everything else) | **Next.js frontend** `[::1]:3000` or `:3001` | blue-green; active port in imported snippet, IPv6 localhost only |
| `www.getreplay.gg` | — | 308 redirect → `getreplay.gg` |
| `app.getreplay.gg` | PHP-FPM (Laravel/Orchid admin), same root | `X-Frame-Options: SAMEORIGIN` |
| `storage.getreplay.gg` | static file_server | replays + Laravel public storage, CORS `*` |

### Frame/embed rule (main site)
- Default: `X-Frame-Options: DENY`.
- `/admin/replay-embed*` (`@replay_embed`): drops `X-Frame-Options`, sets
  `Content-Security-Policy: frame-ancestors https://app.getreplay.gg` — so replay-embed
  pages can be iframed **only** by the admin app. Don't "simplify" this to a blanket DENY.

## Services & ports

| Service | Port / socket | Runtime | Unit |
|---|---|---|---|
| Frontend (zone-map-ui) | TCP `[::1]:3000` (blue) / `[::1]:3001` (green) | **Node 20** (`/opt/node-20`) | `nextjs-blue.service` / `nextjs-green.service` |
| Go backend | TCP `localhost:3006` | Go | (add unit here when captured) |
| PHP API + admin | `unix//run/php/php8.4-fpm.sock` | PHP 8.4-FPM | php8.4-fpm.service |
| Caddy | 80/443 | — | caddy.service |

> ⚠️ There are **two Node runtimes** relevant here: the frontend must run **Node 20**
> (patched `sharp` needs ≥20.9), while other node tooling on the box may differ. Install
> Node 20 isolated (e.g. `/opt/node-20`) and reference it only from `nextjs.service` —
> see [`../frontend/DEPLOY.md`](../frontend/DEPLOY.md).

## Filesystem paths

| What | Path |
|---|---|
| Frontend release root | `/home/solo/getreplay-front/{repo,releases,blue,green,shared}` |
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
