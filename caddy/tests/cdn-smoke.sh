#!/bin/sh
# Run inside the caddy image, with infra mounted read-only at /work and --network none.
set -eu
mkdir -p /tmp/static/releases/test/public /tmp/static/releases/test/_next/static /tmp/replays /tmp/media
printf 'browser-code' > /tmp/static/releases/test/_next/static/app.js
printf 'picture' > /tmp/static/releases/test/public/test.png
printf 'private-map' > /tmp/static/releases/test/_next/static/app.js.map
printf '0123456789' > /tmp/replays/test.replay2
printf 'legacy-gzip' > /tmp/replays/test.replay
printf 'video-data' > /tmp/media/test.mp4
printf 'reverse_proxy 127.0.0.1:18082\n' > /tmp/upstream.caddy
printf '{\n admin off\n auto_https off\n}\n' > /tmp/Caddyfile
sed -n '/^(cdn_public_headers)/,$p' /work/caddy/Caddyfile | sed \
  -e 's/assets-origin.getreplay.gg {/http:\/\/127.0.0.1:18080 {/' \
  -e 's/storage.getreplay.gg {/http:\/\/127.0.0.1:18081 {/' \
  -e '/^[[:space:]]*tls /d' \
  -e 's|/var/lib/getreplay-frontend/upstream.caddy|/tmp/upstream.caddy|g' \
  -e 's|/var/lib/getreplay-frontend/assets|/tmp/static|g' \
  -e 's|/var/www/getreplay-storage/replays|/tmp/replays|g' \
  -e 's|/var/www/getreplay-storage|/tmp|g' \
  -e 's|/var/www/fun-php/repo/src/storage/app/public|/tmp/media|g' >> /tmp/Caddyfile
cat >> /tmp/Caddyfile <<'CADDY'
http://127.0.0.1:18082 {
 header X-Test-Query "{query}"
 header X-Test-Accept "{header.Accept}"
 header X-Test-Cookie "{header.Cookie}"
 respond /_next/image "optimized-image"
}
CADDY
caddy run --config /tmp/Caddyfile > /tmp/caddy.log 2>&1 &
pid=$!
trap 'kill "$pid" 2>/dev/null || true' EXIT
for attempt in 1 2 3 4 5; do
  wget -q -O /dev/null http://127.0.0.1:18080/releases/test/public/test.png && break
  sleep 1
done
get() { wget -S -O /tmp/body "$@" 2>/tmp/headers; }
# BusyBox wget hides response headers on errors; inspect those with raw HTTP.
raw() {
  method=$1; port=$2; uri=$3; shift 3
  printf '%s\r\n' "$method $uri HTTP/1.1" "Host: 127.0.0.1:$port" 'Connection: close' 'Content-Length: 0' "$@" '' |
    nc -w 3 127.0.0.1 "$port" > /tmp/raw
  sed '/^\r$/q' /tmp/raw > /tmp/headers
  sed '1,/^\r$/d' /tmp/raw > /tmp/body
}
get http://127.0.0.1:18080/releases/test/_next/static/app.js
grep -q 'max-age=31536000, immutable' /tmp/headers
grep -q 'Access-Control-Allow-Origin: \*' /tmp/headers
[ "$(cat /tmp/body)" = browser-code ]
for path in /releases/test/public/missing.png /releases/test/_next/static/app.js.map /api/me /.env; do
  raw GET 18080 "$path"
  grep -q '404' /tmp/headers
  grep -q 'Cache-Control: no-store' /tmp/headers
done
raw GET 18081 /replays/test.replay2 'Range: bytes=2-5' 'Accept-Encoding: gzip'
grep -q '206' /tmp/headers
grep -q 'Content-Range: bytes 2-5/10' /tmp/headers
! grep -qi 'Content-Encoding:' /tmp/headers
[ "$(cat /tmp/body)" = 2345 ]
get http://127.0.0.1:18081/test.mp4
grep -q 'Cache-Control: public, max-age=3600' /tmp/headers
for path in /missing.mp4 /replays/missing.replay; do
  raw GET 18081 "$path"
  grep -q '404' /tmp/headers
  grep -q 'Cache-Control: no-store' /tmp/headers
  ! grep -qi 'Content-Encoding:' /tmp/headers
done
raw POST 18080 /releases/test/public/test.png
grep -q '405' /tmp/headers
raw OPTIONS 18081 /replays/test.replay2
grep -q '204' /tmp/headers
grep -q 'Access-Control-Allow-Headers: Range, Content-Type' /tmp/headers
get --header='Accept: image/avif' --header='Cookie: private=value' 'http://127.0.0.1:18080/releases/test/_next/image?url=test&w=640&q=75'
grep -q 'X-Test-Query: url=test&w=640&q=75' /tmp/headers
grep -q 'X-Test-Accept: image/webp' /tmp/headers
! grep -q 'private=value' /tmp/headers
[ "$(cat /tmp/body)" = optimized-image ]
printf '%s\n' 'PASS: static bytes, immutable cache, CORS, missing-file/error cache, source-map hiding, replay ranges, media TTL, methods, image proxy query/format.'
