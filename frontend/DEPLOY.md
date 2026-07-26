# Frontend deploy — zone-map-ui (Next.js), blue-green

Zero-downtime blue-green deploy. Two long-running services behind Caddy:

| Color | Service | Port | WorkingDirectory |
|---|---|---|---|
| blue  | `nextjs-blue.service`  | `[::1]:3000` | `/home/solo/getreplay-front/blue`  |
| green | `nextjs-green.service` | `[::1]:3001` | `/home/solo/getreplay-front/green` |

Caddy proxies `getreplay.gg` to whichever port is active (via an imported snippet,
[`../caddy/frontend-upstream.caddy`](../caddy/frontend-upstream.caddy)). Each deploy builds
into a fresh release, brings up the **idle** color, health-checks it, then flips Caddy with a
graceful `reload` (drops zero connections). The old color keeps running.

- Node: **20 LTS**, isolated at `/opt/node-20` (system node is 18 — EOL, can't run patched `sharp`).
- App repo `zone-map-ui` owns `.nvmrc` + `engines`. This infra repo owns *how* it deploys.
- Validate app changes with `npx tsc --noEmit` (ESLint is disabled during `next build`).

Files here: [`deploy.sh`](deploy.sh), [`../systemd/nextjs-blue.service`](../systemd/nextjs-blue.service),
[`../systemd/nextjs-green.service`](../systemd/nextjs-green.service).
The old single unit is kept for reference: [`../systemd/nextjs.service`](../systemd/nextjs.service).

---

## Why this replaces the old flow

Old: `git pull && npm install && npm run build && systemctl restart` **in the live dir**.
`next build` rewrites `.next/` in place → the running server 500s until restart; a failed
build left the site down. Prod was Node 18, local Node 20 → "works locally, breaks on prod".

Blue-green fixes all three: builds never touch the live color, a failed build/health-check
never reaches users, and Node is pinned to 20 on both sides.

---

## One-time migration (from the current single-service setup)

Current prod: `nextjs.service`, `User=solo`, flat dir `/home/solo/getreplay-front`, system npm.

```bash
cd /home/solo/getreplay-front

# 1. Node 20, isolated (does not touch system node 18)
sudo mkdir -p /opt/node-20
curl -fsSL https://nodejs.org/dist/v20.20.2/node-v20.20.2-linux-x64.tar.xz \
  | sudo tar -xJ --strip-components=1 -C /opt/node-20
/opt/node-20/bin/node -v   # v20.20.2

# 2. New layout beside the current files
mkdir -p releases shared
git clone <ZONE_MAP_UI_REPO_URL> repo          # or: move the existing checkout into repo/
mv .env.production shared/ 2>/dev/null || true # NEXT_PUBLIC_* are baked at build time

# 3. Install both color units; stop the old single service
sudo cp <infra>/systemd/nextjs-blue.service /etc/systemd/system/
sudo cp <infra>/systemd/nextjs-green.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl disable --now nextjs.service    # retire the old one

# 4. Caddy: install the upstream snippet, switch the frontend handle to import it
sudo cp <infra>/caddy/frontend-upstream.caddy /etc/caddy/frontend-upstream.caddy
sudo cp <infra>/caddy/Caddyfile /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy

# 5. First deploy → lands on green (:3001), Caddy flips to it
<infra>/frontend/deploy.sh

# 6. Bring blue up on the same release so the pair is ready for future flips
ln -sfn "$(readlink -f green)" blue
sudo systemctl enable --now nextjs-blue.service
sudo systemctl enable nextjs-green.service     # (already started by deploy)
```

After this, both colors run; each deploy alternates between them.

---

## Deploying an update (the new normal)

```bash
/home/solo/getreplay-front/repo/.. # anywhere; the script uses absolute paths
<infra>/frontend/deploy.sh
```

Flow: pick idle color → build in `releases/<ts>` (live color untouched) → repoint idle
symlink → restart idle unit → health-check `http://[::1]:<idle>/` → **flip Caddy** (graceful
reload) → record active port → prune. On health failure it reverts the idle color and leaves
Caddy on the old port — **users never see it**.

### Rollback

The previous version is still running on the other color. To go back, just point Caddy at it:

```bash
# put the other port in the snippet, e.g. 3000:
printf 'reverse_proxy [::1]:3000 {\n    header_up X-Forwarded-Host {host}\n}\n' \
  | sudo tee /etc/caddy/frontend-upstream.caddy >/dev/null
sudo systemctl reload caddy
echo 3000 | sudo tee /home/solo/getreplay-front/shared/active-port
```

---

## Microsoft Clarity: keeping old assets alive

Symptom: after a deploy, Clarity session replays lose their styles — the old build's
content-hashed CSS/JS chunks (`/_next/static/...`) were deleted and replaced.

- **Blue-green alone** already keeps the **previous** version's server running until the next
  deploy, so its assets stay served → replays from just before a deploy render correctly. This
  covers the common case (one deploy back).
- **For several generations** (if you deploy often), enable the **static pool**: run deploys
  with `STATIC_POOL=1` (accumulates each release's `/_next/static` into
  `shared/static-pool/`, pruned after `STATIC_POOL_KEEP_DAYS`, default 30), then uncomment the
  `handle_path /_next/static/*` block in [`../caddy/Caddyfile`](../caddy/Caddyfile) and reload
  Caddy. Hashed filenames never collide, so many releases coexist cheaply, and Caddy serves
  them straight from disk (also offloads static traffic from Node). Enable the Caddy block
  ONLY after the pool is populated, or `/_next/static` 404s.

```bash
STATIC_POOL=1 <infra>/frontend/deploy.sh    # populate the pool, then flip the Caddy block on
```

---

## The 3 GB `.next` — is that too much?

Mostly normal, and it's the **cache**, not the app. `.next` breaks down as:

- `.next/static` + `.next/server` — the actual build, tens–hundreds of MB.
- `.next/cache/` — webpack build cache **and** `.next/cache/images/` — the on-disk
  **Image Optimization cache**. With `images.minimumCacheTTL: 31536000` (1 year) and avif+webp
  variants per source image/size, this grows and rarely evicts → almost certainly the bulk of
  the 3 GB.

What to do:

- **Share it across releases** (deploy.sh already symlinks `.next/cache` →
  `shared/next-cache`): otherwise every retained release would carry its own multi-GB cache.
  With sharing, each release's own `.next` is small and the big cache exists once.
- **Bound the image cache** if it keeps growing — a periodic prune, e.g. weekly cron:
  ```bash
  find /home/solo/getreplay-front/shared/next-cache/images -type f -mtime +30 -delete
  ```
  (Next re-optimizes on demand; a cold entry just costs one conversion.)
- Optional: drop `minimumCacheTTL` in `next.config.ts` so entries turn over sooner.

3 GB is not alarming on its own — the risk was multiplying it per release, which the shared
cache prevents.

---

## Verifying versions on the server

```bash
/opt/node-20/bin/node -v                                   # v20.x (>= 20.9)
systemctl show nextjs-blue.service -p ExecStart -p Environment
/opt/node-20/bin/node -e "console.log(require('sharp').versions)"   # sharp must load
cd /home/solo/getreplay-front/blue && npm ls sharp next postcss prismjs --depth=0
curl -sS -o /dev/null -w '%{http_code}\n' http://[::1]:3000/   # blue reachable?
curl -sS -o /dev/null -w '%{http_code}\n' http://[::1]:3001/   # green reachable?
```

---

## Toward CI (later)

- `deploy.sh` is host-agnostic (config via env). A CI job can SSH in and run it. For that,
  grant passwordless sudo for just the needed commands (`systemctl restart nextjs-*`,
  `systemctl reload caddy`, `cp` to the caddy snippet) via a `/etc/sudoers.d/` drop-in.
- `output: 'standalone'` in `next.config.ts` lets CI ship a self-contained build (no `npm ci`
  on prod). Also copy `public/` and `.next/static` alongside it.
