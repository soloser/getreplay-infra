#!/usr/bin/env bash
#
# One-command deploy for zone-map-ui (single systemd service, in-place build).
#
# Runs the right Node regardless of the login shell's PATH (npm resolves its own
# node via `#!/usr/bin/env node`, so /opt/node-20 must be first in PATH — the exact
# gotcha that otherwise runs the build under system Node 18). Verifies the Node major
# against .nvmrc before touching anything.
#
# The service is stopped for the build: `npm ci` wipes node_modules and `next build`
# rewrites .next in place, which would break the live server if it kept serving.
# So there's a downtime window ≈ install + build. A failed build leaves the service
# stopped — fix and re-run (or `sudo systemctl start nextjs.service` to bring the old
# build back only if node_modules/.next survived). For zero-downtime later, see the
# blue-green note in DEPLOY.md.
#
# Usage:  ./deploy.sh          (config via env vars below)
set -euo pipefail

# ---- config (override via env) --------------------------------------------
APP_ROOT="${APP_ROOT:-/home/solo/getreplay-front}"   # the git checkout = systemd WorkingDirectory
BRANCH="${BRANCH:-main}"
REVISION="${REVISION:-}"
SERVICE="${SERVICE:-nextjs.service}"
NODE_BIN="${NODE_BIN:-/opt/node-20/bin}"             # isolated Node install
HEALTH_URL="${HEALTH_URL:-http://[::1]:3000/}"       # matches `next start -H ::1`
# ---------------------------------------------------------------------------

log() { printf '\033[1;34m[deploy %s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '\033[1;31m[deploy]\033[0m %s\n' "$*" >&2; exit 1; }

# --- pick the correct Node (first in PATH so npm's `env node` finds it too) ---
export PATH="$NODE_BIN:$PATH"
command -v node >/dev/null 2>&1 || die "node not found in $NODE_BIN — install it or set NODE_BIN"
cd "$APP_ROOT" || die "missing $APP_ROOT"

if [ -n "$REVISION" ] && ! [[ "$REVISION" =~ ^[0-9a-f]{40}$ ]]; then
  die "REVISION must be a full lowercase 40-character commit SHA"
fi

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  die "frontend checkout has local tracked changes; commit or remove them before deploying"
fi

# --- guard: running Node major must match .nvmrc ---
if [ -f .nvmrc ]; then
  want="$(tr -dc '0-9.' < .nvmrc)"; want_major="${want%%.*}"
  have_major="$(node -p 'process.versions.node.split(".")[0]')"
  [ "$want_major" = "$have_major" ] \
    || die ".nvmrc wants Node $want but $NODE_BIN is $(node -v). Install /opt/node-$want_major or set NODE_BIN."
fi
log "using $(node -v) from $NODE_BIN"

log "fetch origin/$BRANCH"
git fetch --prune origin
TARGET="origin/$BRANCH"
if [ -n "$REVISION" ]; then
  git cat-file -e "$REVISION^{commit}" 2>/dev/null || die "commit is not available after fetch: $REVISION"
  git merge-base --is-ancestor "$REVISION" "origin/$BRANCH" \
    || die "commit is not contained in origin/$BRANCH: $REVISION"
  TARGET="$REVISION"
fi
git reset --hard "$TARGET"   # untracked files (.env.production, node_modules, .next) are kept

log "stop $SERVICE (build window begins)"
sudo systemctl stop "$SERVICE"

log "npm ci"
npm ci
log "npm run build"
npm run build

log "start $SERVICE"
sudo systemctl start "$SERVICE"

# --- health check ---
log "health check $HEALTH_URL"
ok=0; code=""
for _ in $(seq 1 15); do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$HEALTH_URL" || true)"
  case "$code" in 200|301|302|307|308) ok=1; break ;; esac
  sleep 1
done
[ "$ok" = "1" ] || die "service not healthy (last code: ${code:-none}) — check: journalctl -u $SERVICE -n 50 ; tail /var/log/nextjs.log"

log "done — $(git rev-parse --short HEAD) live"
