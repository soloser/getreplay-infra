# Frontend deploy — zone-map-ui (Next.js)

Single systemd service `nextjs.service` (User `solo`, `next start -H ::1` on `[::1]:3000`,
**Node 20**) behind Caddy. Deploy is one command: [`deploy.sh`](deploy.sh).

- App repo `zone-map-ui` owns `.nvmrc` (Node 20 pin) + `engines`. This infra repo owns
  *how* it deploys.
- Node 20 lives isolated at `/opt/node-20` (system Node 18 stays for `node-app.service`).
- Validate app changes with `npx tsc --noEmit` (ESLint is disabled during `next build`).

## Deploy

```bash
/path/to/infra/frontend/deploy.sh
```

It picks Node from `/opt/node-20` (prepends it to `PATH` so `npm` and its `#!/usr/bin/env node`
both resolve to Node 20, not system 18), checks the Node major against `.nvmrc`, then:
`git reset --hard origin/main` → **stop service** → `npm ci` → `npm run build` → **start** →
health-check `http://[::1]:3000/`.

The service is stopped for the build on purpose: `npm ci` wipes `node_modules` and `next build`
rewrites `.next` in place, which would break a live server that kept serving. **Downtime ≈ the
build time** (a minute or two). That's the trade for a simple single-version deploy; do it at low
traffic. A failed build leaves the service stopped — fix and re-run.

Config via env vars (defaults in the script): `APP_ROOT`, `BRANCH`, `SERVICE`, `NODE_BIN`,
`HEALTH_URL`.

`sudo` is used for `systemctl stop/start`. For a truly promptless one command, allow just those
via a `/etc/sudoers.d/` drop-in.

## One-time server setup

```bash
# 1. Node 20, isolated (does not touch system node 18)
sudo mkdir -p /opt/node-20
curl -fsSL https://nodejs.org/dist/v20.20.2/node-v20.20.2-linux-x64.tar.xz \
  | sudo tar -xJ --strip-components=1 -C /opt/node-20
/opt/node-20/bin/node -v   # v20.20.2

# 2. Point the service at Node 20 (this repo's unit already does; install or use a drop-in)
sudo cp <infra>/systemd/nextjs.service /etc/systemd/system/nextjs.service
sudo systemctl daemon-reload
#   (alternatively keep the old unit and override just ExecStart+PATH via `systemctl edit`)

# 3. Deploy
<infra>/frontend/deploy.sh
```

The git checkout stays at `/home/solo/getreplay-front`; `deploy.sh` does `git reset --hard
origin/main` there. Untracked files (`.env.production`, `node_modules`, `.next`) are preserved.

## Verify

```bash
systemctl show nextjs.service -p ExecStart          # → /opt/node-20/bin/npm ...
/opt/node-20/bin/node -e "console.log(require('sharp').versions)"   # sharp loads on Node 20
cd /home/solo/getreplay-front && /opt/node-20/bin/npm audit --omit=dev   # runtime deps: 0
curl -sI 'http://[::1]:3000/' | head -1             # backend up
curl -sI 'https://getreplay.gg/' | head -1          # site via Caddy
```

## Rollback

```bash
cd /home/solo/getreplay-front
git reset --hard <previous-commit>
sudo systemctl stop nextjs.service
/opt/node-20/bin/npm ci && /opt/node-20/bin/npm run build
sudo systemctl start nextjs.service
```

## Future: zero-downtime

To remove the build-window downtime later, run two services on `[::1]:3000` / `[::1]:3001`
(blue-green): build the idle one, health-check it, then flip Caddy's `reverse_proxy` port with a
graceful `systemctl reload caddy`. Not set up now — this simple single-version flow is the
current design.
