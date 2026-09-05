# Production topology

Single host. **Caddy** terminates TLS (automatic, ACME email `blackangelnk@gmail.com`)
and reverse-proxies to the services below. Full config: [`../caddy/Caddyfile`](../caddy/Caddyfile).

## Domains → services

| Domain / path | Backend | Notes |
|---|---|---|
| `getreplay.gg` `/srv/*` | Go backend `localhost:3006` | |
| `getreplay.gg` `/api/*` | PHP-FPM (Laravel) `unix//run/php/php8.4-fpm.sock`, root `/var/www/fun-php/repo/src/public` | `/api` prefix stripped (`handle_path`) |
| `getreplay.gg` (everything else) | **Next.js frontend** active `[::1]:3000` / `[::1]:3001` | IPv6 localhost only |
| `www.getreplay.gg` | — | 308 redirect → `getreplay.gg` |
| `app.getreplay.gg` `/backend*`, `/vendor/orchid/*`, `/storage/*` | PHP-FPM/static (Laravel/Orchid), same root | Admin stays isolated under `/backend`; `X-Frame-Options: SAMEORIGIN` |
| `app.getreplay.gg` `/api/*` | PHP-FPM (Laravel API), same root | Same routing contract as the main domain |
| `app.getreplay.gg` `/srv/*` | Go backend `localhost:3006` | HTTP + WebSocket, same-origin for the product UI |
| `app.getreplay.gg` (everything else) | **Next.js frontend** active `[::1]:3000` / `[::1]:3001` | Canonical authenticated product host; `X-Robots-Tag: noindex, nofollow` |
| `storage.getreplay.gg` | static file_server | replays + Laravel public storage, CORS `*` |

### Frame/embed rule (main site)
- Default: `X-Frame-Options: DENY`.
- `/admin/replay-embed*` (`@replay_embed`): drops `X-Frame-Options`, sets
  `Content-Security-Policy: frame-ancestors https://app.getreplay.gg` — so replay-embed
  pages can be iframed **only** by the admin app. Don't "simplify" this to a blanket DENY.

### Auth handoff between public and app hosts

- Laravel production config must set `FRONT_URL=https://app.getreplay.gg/token`.
- The frontend build sets `NEXT_PUBLIC_APP_URL=https://app.getreplay.gg`.
- OAuth starts on `getreplay.gg`, then Laravel sends a short-lived single-use handoff code
  to the app token landing page in the URL fragment. The app exchanges it through its
  same-origin `/api/auth/handoff/consume` route; JWT values are not placed in URLs.
- An existing `getreplay.gg` localStorage session uses the same handoff before the old
  origin-scoped token is removed.

## Services & ports

| Service | Port / socket | Runtime | User | Unit / entrypoint |
|---|---|---|---|---|
| Frontend (zone-map-ui) | `[::1]:3000` / `[::1]:3001` | **Node 20** (`/opt/node-20`) | solo | `nextjs@3000.service` / `nextjs@3001.service` |
| Go backend (match-updater + Kafka pipeline) | `0.0.0.0:3006` (Caddy `/srv/*`) | Go + Kafka | www-data | `go-app.service` → `start.sh` |
| Demo uploader | `0.0.0.0:3005` (uploads from PHP) | Go | www-data | `demo-uploader.service` → `demo-uploader.sh` |
| Highlight extractor | — (one-shot runner) | Go | www-data | **cron** `/etc/cron.d/getreplay-highlight-extractor` (hourly :20) → `highlight-extractor-cron.sh` |
| Highlight feed builder | — (one-shot runner) | PHP 8.4 CLI + Redis | www-data | **cron** `/etc/cron.d/getreplay-highlight-feed` (daily 03:40) → `highlight-feed-cron.sh` |
| Node backend | (internal) | **system Node 18** | solo | `node-app.service` → `/home/solo/getreplay-node`, `/usr/bin/npm start`, EnvFile `.env` |
| PHP API + admin | `unix//run/php/php8.4-fpm.sock` | PHP 8.4-FPM | www-data | `php8.4-fpm.service` (distro) |
| Redis | `127.0.0.1:6379` | Redis 7 / distro package | redis | `redis-server.service`; highlight feed uses database `2` |
| Kafka | `127.0.0.1:9092` | Apache Kafka 4.3.1, KRaft | kafka | `kafka.service`; `kafka-topics.service` provisions topics |
| Caddy | 80/443 | — | — | `caddy.service` |

> ⚠️ **Two Node runtimes coexist.** The frontend runs **Node 20** isolated at `/opt/node-20`
> (patched `sharp` needs ≥20.9), referenced by the `nextjs@.service` template. The
> `node-app.service` backend still runs on **system Node 18** (`/usr/bin/npm`). Installing
> Node 20 in `/opt` leaves system node untouched, so node-app is unaffected — do not replace
> system node. See [`../frontend/DEPLOY.md`](../frontend/DEPLOY.md).
>
> The Go units call helper scripts (`start.sh`, `demo-uploader.sh`) that hold the actual run
> command/flags/port. Both live in `/var/www/getreplay-go/` and source the same mode-0600
> `getreplay-go.env`. Match discovery, download, parse/persist, and event delivery consume Kafka
> inside the single `match-updater` process.
>
> Kafka is one persistent broker on this host. It removes application-process queue loss, not
> host/disk failure; host-level HA requires at least three brokers on independent hosts.

## Filesystem paths

| What | Path |
|---|---|
| Frontend | `/home/solo/getreplay-front` (git checkout = systemd WorkingDirectory) |
| PHP / Laravel | `/var/www/fun-php/repo/src/...` |
| PHP cron wrappers | `/var/www/fun-php/bin/` |
| Replay storage | `/var/www/getreplay-storage/` (`replays/` + `*.replay2`) |
| Kafka data | `/var/lib/kafka/` (persistent KRaft metadata + topic log) |
| Kafka config | `/etc/kafka/getreplay-server.properties` |
| Laravel public storage | `/var/www/fun-php/repo/src/storage/app/public` |
| Caddyfile | `/etc/caddy/Caddyfile` |

## Deploying config changes

Application deployment entrypoints:

- Go: `go/deploy.sh <app>`
- Kafka/systemd first install: `kafka/README.md` (human-reviewed privileged procedure)
- PHP: `php/deploy.sh`
- Frontend: `frontend/deploy.sh`

See [`../php/README.md`](../php/README.md) for native Redis/PHP prerequisites and
the first highlight-feed rebuild.

```bash
sudo cp caddy/Caddyfile /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy      # graceful
```

Frontend blue-green units/routes apply after the one-time migration in
[`frontend/DEPLOY.md`](../frontend/DEPLOY.md). Until then `nextjs.service` serves port 3000.
