# Go services deploy

Three Go binaries from `github.com/soloser/getreplay-go`, all living in `/var/www/getreplay-go/`:

| App | How it runs | Unit / trigger | Port |
|---|---|---|---|
| `match-updater` | long-running | `go-app.service` → `start.sh` | `:3006` (Caddy `/srv/*`) |
| `demo-uploader` | long-running | `demo-uploader.service` → `demo-uploader.sh` | `:3005` (uploads from PHP) |
| `highlight-extractor` | one-shot runner | **cron** hourly → `highlight-extractor-cron.sh` | — |

**Build happens on the server** (no more local cross-compile + scp). All runtime params live
in one file — [`getreplay-go.env`](getreplay-go.env.example) — sourced by every launcher; each
launcher only adds its per-service ports/worker-counts.

## Deploy — one command per app

```bash
/home/solo/infra/go/deploy.sh match-updater
/home/solo/infra/go/deploy.sh demo-uploader
/home/solo/infra/go/deploy.sh highlight-extractor
```

Each: `git reset --hard origin/main` in the source checkout → `go build` (CGO off, static) →
atomic-swap the binary in `/var/www/getreplay-go/` → for a service, restart it; for
highlight-extractor, (re)install its cron wrapper + `/etc/cron.d` entry. A failed build deploys
nothing. Config via env: `SRC`, `BIN_DIR`, `BRANCH`, `GO_BIN`, `LOG_DIR`.

## One-time server setup

```bash
# 1. Go toolchain (>= 1.24; go.mod pins toolchain go1.24.5, auto-fetched by a recent Go)
curl -fsSL https://go.dev/dl/go1.24.5.linux-amd64.tar.gz | sudo tar -xz -C /opt
#   put it on PATH for your deploy shell, or run deploy.sh with GO_BIN=/opt/go/bin/go
echo 'export PATH=/opt/go/bin:$PATH' >> ~/.profile

# 2. Source checkout (build reads from here)
git clone git@github.com:soloser/getreplay-go.git /home/solo/getreplay-go

# 3. The shared env file (secrets live only here, on prod)
cd /var/www/getreplay-go
cp /home/solo/infra/go/getreplay-go.env.example getreplay-go.env
sudo -e getreplay-go.env                        # fill secrets
sudo chown www-data:www-data getreplay-go.env   # services + cron run as www-data
sudo chmod 600 getreplay-go.env

# 4. Ensure the units are installed (once), then deploy each app
sudo cp /home/solo/infra/systemd/{go-app,demo-uploader}.service /etc/systemd/system/
sudo systemctl daemon-reload
/home/solo/infra/go/deploy.sh match-updater
/home/solo/infra/go/deploy.sh demo-uploader
/home/solo/infra/go/deploy.sh highlight-extractor   # also wires up the cron
```

## highlight-extractor: cron + backfill

`deploy.sh highlight-extractor` installs `/etc/cron.d/getreplay-highlight-extractor` (hourly at
:20, as www-data, logs to `/var/log/highlight-extractor.log`) and the wrapper. The runner is
idempotent, so overlap/re-runs are safe.

**One-off full backfill** (e.g. after a migration, or after importing old pro demos parsed long
after they finished — the hourly `LOOKBACK_DAYS=7` window would miss those):

```bash
sudo -u www-data env LOOKBACK_DAYS=3650 /var/www/getreplay-go/highlight-extractor-cron.sh
```

Other one-off knobs (env, read by the binary): `REEXTRACT=true` (delete + regenerate for the
window, after tuning detectors), `MATCH_ID=<id>` (single match).

## Notes

- Binaries build as your deploy user and run 0755, so the `www-data` services execute them fine.
- `getreplay-go.env` must be readable by `www-data` (owner www-data, 0600) — the services and the
  cron all run as www-data.
- Security: the two services bind `0.0.0.0:3005/3006` — reachable externally unless firewalled.
  Caddy only proxies localhost; consider switching to `127.0.0.1`. Flagged in the launchers.
