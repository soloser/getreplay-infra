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
| Go backend (match-updater) | `0.0.0.0:3006` (Caddy `/srv/*`) | Go | www-data | `go-app.service` → `start.sh` |
| Demo uploader | `0.0.0.0:3005` (uploads from PHP) | Go | www-data | `demo-uploader.service` → `demo-uploader.sh` |
| Match discovery worker | — | Go + Kafka | www-data | `match-discovery-worker.service` |
| Demo downloader worker | — | Go + Kafka | www-data | `demo-downloader-worker.service` |
| Demo processor worker | — | Go + Kafka | www-data | `demo-processor-worker.service` |
| Highlight extractor | — (one-shot runner) | Go | www-data | **cron** `/etc/cron.d/getreplay-highlight-extractor` (hourly :20) → `highlight-extractor-cron.sh` |
| Highlight feed builder | — (one-shot runner) | PHP 8.4 CLI + Redis | www-data | **cron** `/etc/cron.d/getreplay-highlight-feed` (daily 03:40) → `highlight-feed-cron.sh` |
| Node backend | (internal) | **system Node 18** | solo | `node-app.service` → `/home/solo/getreplay-node`, `/usr/bin/npm start`, EnvFile `.env` |
| PHP API + admin | `unix//run/php/php8.4-fpm.sock` | PHP 8.4-FPM | www-data | `php8.4-fpm.service` (distro) |
| Redis | `127.0.0.1:6379` | Redis 7 / distro package | redis | `redis-server.service`; highlight feed uses database `2` |
| Kafka | `127.0.0.1:9092` | Apache Kafka 4.3.1, KRaft | kafka | `kafka.service`; `kafka-topics.service` provisions topics |
| Caddy | 80/443 | — | — | `caddy.service` |

> ⚠️ **Two Node runtimes coexist.** The frontend runs **Node 20** isolated at `/opt/node-20`
> (patched `sharp` needs ≥20.9), referenced only by `nextjs.service`. The
> `node-app.service` backend still runs on **system Node 18** (`/usr/bin/npm`). Installing
> Node 20 in `/opt` leaves system node untouched, so node-app is unaffected — do not replace
> system node. See [`../frontend/DEPLOY.md`](../frontend/DEPLOY.md).
>
> The Go units call helper scripts (`start.sh`, `demo-uploader.sh`) that hold the actual run
> command/flags/port. Queue workers use their matching `*-worker.sh` launchers. All live in
> `/var/www/getreplay-go/` and source the same mode-0600 `getreplay-go.env`.
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
