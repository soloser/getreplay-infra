# Frontend deploy — zone-map-ui (Next.js)

Production frontend runs as `nextjs.service` (systemd, Node 20, `[::1]:3000`) behind
Caddy. This replaces the old "pull + build in the live folder" flow, which broke the
site during every build.

- App repo: `zone-map-ui` (separate git repo). It owns `.nvmrc` (Node 20 pin) and
  `engines`. This infra repo owns *how* it's deployed.
- Node: **20 LTS**. Node 18 is EOL and can't run the patched `sharp` (image optimization).
- Build gate: `next build` (ESLint disabled in build via `next.config.ts`; type-check
  still runs). Validate app changes with `npx tsc --noEmit`.

Scripts/units in this dir:
- [`deploy.sh`](deploy.sh) — atomic release + symlink swap + health-check + auto-rollback.
- [`../systemd/nextjs.service`](../systemd/nextjs.service) — the unit.

---

## Why the old flow broke

`git pull && npm install && npm run build && systemctl restart` ran **inside the live
directory**. `next build` rewrites `.next/` in place → the running server served HTML
referencing chunk hashes that no longer existed → 500s. A failed build left the site
down until fixed by hand. And prod was on Node 18 while local was Node 20.

The fix: **build in a fresh release dir, then flip one symlink.** A failed build or
failed health check never touches the live site.

---

## One-time server setup

### 1. Install Node 20, isolated from other node tooling

```bash
sudo mkdir -p /opt/node-20
curl -fsSL https://nodejs.org/dist/v20.20.2/node-v20.20.2-linux-x64.tar.xz \
  | sudo tar -xJ --strip-components=1 -C /opt/node-20
/opt/node-20/bin/node --version   # v20.20.2
```

### 2. Create the release layout

```bash
sudo mkdir -p /opt/zone-map-ui/{releases,shared}
sudo chown -R deploy:deploy /opt/zone-map-ui

git clone <ZONE_MAP_UI_REPO_URL> /opt/zone-map-ui/repo   # the APP repo, not infra

# NEXT_PUBLIC_* are baked at build time — put the real env here:
mv /path/to/.env.production /opt/zone-map-ui/shared/.env.production
```

### 3. Install the systemd unit

Edit paths/user in [`../systemd/nextjs.service`](../systemd/nextjs.service), then:

```bash
sudo cp ../systemd/nextjs.service /etc/systemd/system/nextjs.service
sudo systemctl daemon-reload
sudo systemctl enable nextjs.service
```

### 4. First release

```bash
/path/to/infra/frontend/deploy.sh     # creates releases/<ts> + current symlink, starts service
```

Caddy already proxies `getreplay.gg` → `[::1]:3000` (see [`../caddy/Caddyfile`](../caddy/Caddyfile)),
so no Caddy change is needed for the basic flow.

---

## Deploying an update (the new normal)

```bash
/path/to/infra/frontend/deploy.sh
```

Fetch `main` → build in a new release dir (site stays up on the old one) → atomic symlink
swap → `systemctl restart` → health-check `http://[::1]:3000/`. On failure it **auto-rolls
back** to the previous release. Keeps the newest 5 releases.

Downtime is now only the ~1–2s systemd restart. To remove even that, see blue-green below.

### Rollback (manual)

```bash
ls -dt /opt/zone-map-ui/releases/*/
ln -sfn /opt/zone-map-ui/releases/<ts> /opt/zone-map-ui/current
sudo systemctl restart nextjs.service
```

---

## True zero-downtime (blue-green with Caddy) — optional next step

Caddy reloads gracefully (drains old connections), so it's ideal for blue-green:

- `nextjs-blue.service`  → `[::1]:3000`
- `nextjs-green.service` → `[::1]:3001`

Flow: build the new release → start/restart the **idle** color → health-check it directly
→ point the frontend `reverse_proxy` in the Caddyfile at the new port → `caddy reload`.
The active color keeps serving until Caddy flips, so the restart blip disappears. (Follow-up;
the symlink flow already removes the "broken during/after build" failures, the real pain.)

---

## Verifying versions on the server

```bash
node --version                                             # >= 20.9
systemctl show nextjs.service -p ExecStart -p Environment  # what node the service uses
/opt/node-20/bin/node -e "console.log(require('sharp').versions)"   # sharp must load
cd /opt/zone-map-ui/current && npm ls sharp next postcss prismjs --depth=0
curl -sS -o /dev/null -w '%{http_code}\n' http://[::1]:3000/         # backend reachable?
```

---

## Toward CI (later)

- **Pinned Node** (`.nvmrc` + `engines` in the app repo) → CI uses the same version. Add
  `engine-strict=true` to a prod/CI `.npmrc` once every machine is on Node ≥20.19 so a
  wrong Node fails fast.
- **`deploy.sh`** is host-agnostic (config via env). A CI job can SSH in and run it, or
  build an artifact and rsync it.
- For artifact deploys, enable `output: 'standalone'` in `next.config.ts`: `next build`
  emits a self-contained `.next/standalone` (own `server.js` + trimmed `node_modules`), so
  prod needs no `npm ci`. (Also copy `public/` and `.next/static` alongside it.)
