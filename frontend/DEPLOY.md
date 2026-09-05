# Frontend deploy without the build-time outage

`deploy.sh` builds a separate checkout snapshot while the current Next.js process
continues serving. It alternates `[::1]:3000` and `[::1]:3001`, starts the candidate,
requires HTTP 200 from `/en`, then gracefully reloads Caddy for **both** domains.
After a 30-second drain it disables/stops the old service and removes its managed
slot. There is no release archive or automatic rollback after a successful switch.

## One-time migration on the server

Keep the existing `nextjs.service` running. Review the diff against the actual
`/etc/caddy/Caddyfile` before applying it; preserve any server-only changes.
The only necessary Caddy change is replacing both frontend `reverse_proxy` blocks
with `import /etc/caddy/frontend-upstream.caddy` (see the tracked Caddyfile).
Do not overwrite this upstream file on subsequent infrastructure updates: it
records the active port. The initial value 3000 is only for legacy migration.

```bash
cd /home/solo/infra
sudo cp systemd/nextjs@.service /etc/systemd/system/
sudo systemctl daemon-reload
# FIRST installation only; refuses to replace an existing active-port file:
sudo test ! -e /etc/caddy/frontend-upstream.caddy && \
  sudo install -m 644 caddy/frontend-upstream.caddy /etc/caddy/frontend-upstream.caddy
# After reviewing/merging the Caddyfile changes:
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

At this stage both domains still use the legacy service on port 3000.
Install the updated trusted release adapter through the existing
[`release/install-server.sh`](../release/install-server.sh) procedure if using
GitHub release buttons. It copies `frontend/deploy.sh`; the template unit and Caddy
migration above must be installed first. No release manifest changes are needed.

## Deploy

```bash
sudo /home/solo/infra/frontend/deploy.sh
```

The first deployment builds slot 3001, then disables the legacy `nextjs.service`
only after switching traffic. Later deployments alternate template instances.
The source checkout remains `/home/solo/getreplay-front`; `.next` and `node_modules`
there are never rebuilt or deleted by this script. Builds use `git archive HEAD`
and copy `.env`, `.env.local`, `.env.production`, `.env.production.local` privately
into the candidate. Node 20 is selected through `/opt/node-20/bin`.

GitHub's existing `SOURCE_PREPARED=true`, pinned `REVISION`, and `BUILD_USER=solo`
contract is supported. Manual deploy fetches `origin/main` (or `BRANCH`) and can
select a full `REVISION` contained in that branch. Rollback is another deployment
of the previous revision, rebuilding it through the same path.

Config: `APP_ROOT`, `BRANCH`, `REVISION`, `SOURCE_PREPARED`, `BUILD_USER`, `NODE_BIN`,
`SLOTS_ROOT`, `UPSTREAM`, `CADDY_CONFIG`, `LOCK_FILE`, `HEALTH_PATH`, `HEALTH_ATTEMPTS`, `DRAIN_SECONDS`.
`SERVICE` is only the legacy unit name. Changes to `SLOTS_ROOT`, `BUILD_USER`, or
`NODE_BIN` must also be reflected in the installed template unit. Ports stay fixed
at 3000/3001. Run the adapter as root; npm and Git run as the build user.

## Failures and verification

- Install/build/readiness failures leave the old service and route active.
- Validation/reload failures restore the previous upstream and reload it before
  stopping the candidate. If that recovery reload fails, both services remain up
  and a `/run/lock/getreplay-frontend.lock.recovery` marker blocks further deploys.
  Inspect Caddy's actual active configuration, reconcile it with the upstream file,
  validate/reload successfully, then remove that marker before retrying.
- Concurrent direct deploys are rejected by `flock`; release-button source
  preparation also remains covered by the existing release gateway lock.
- After a successful reload, interruption/cleanup failure leaves the new version
  live and may leave the retired service running; inspect units before retrying.
- Enabled active units survive reboot. The deployed Caddyfile and upstream file
  must stay consistent; do not reset the upstream to the tracked initial value.

```bash
cat /etc/caddy/frontend-upstream.caddy
systemctl status nextjs@3000.service nextjs@3001.service
curl --noproxy '*' -I 'http://[::1]:3001/en' # substitute active port
curl -I https://getreplay.gg/en
curl -I https://app.getreplay.gg/en
python3 -m unittest discover -s frontend/tests -v
```

Tests run the real shell with command doubles: no actual npm build, systemd or
production changes. A production smoke check is still needed after installation.

## Bounds

The server needs resources for the live app plus a build and briefly two app
processes. This prevents deliberate build-time shutdown; it is not host-level HA
or protection against OOM. Requests exceeding the drain interval may be interrupted.
The previous build's static chunks are carried forward for one deployment (not
its runtime), which helps already-open tabs. Old server actions/data can still
require a page refresh; no indefinite version-skew guarantee is made.

References: [Caddy graceful reload](https://caddyserver.com/docs/command-line#caddy-reload),
[Next.js self-hosting/version skew](https://nextjs.org/docs/app/guides/self-hosting).
