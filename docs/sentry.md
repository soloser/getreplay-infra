# Sentry for GetReplay

Organization: `sergei-5n`, US region. Team: `sergei`.

| Component | Project | Production configuration |
| --- | --- | --- |
| Next.js | `getreplay-front` (existing) | Existing frontend integration |
| Laravel API, Orchid, console/jobs | `getreplay-php` | `/var/www/fun-php/repo/src/.env` |
| Go services and one-shot processors | `getreplay-go` | `/var/www/getreplay-go/getreplay-go.env` |
| Steam GC Node service | `getreplay-node` | `/home/solo/getreplay-node/.env` |

Get each project's DSN from **Project Settings → Client Keys (DSN)** in Sentry.
Keep it in the existing runtime environment file. Do not replace the file or
overwrite database/Steam settings. PHP accepts `SENTRY_LARAVEL_DSN` (preferred)
or `SENTRY_DSN`; Go and Node use `SENTRY_DSN`.

Go also accepts a small `sentry.json` beside each deployed executable:
`{"dsn":"<project DSN>","environment":"production"}`. All five production
binaries share `/var/www/getreplay-go/`, so this config enables them together
without editing the separately protected database/queue credential file.
An explicitly present `SENTRY_DSN` takes precedence, including an empty value
to disable reporting. The file contains only the Sentry ingestion DSN and
environment; do not put database credentials or Sentry API tokens in it.

Set `SENTRY_ENVIRONMENT=production` on the server. For PHP and Node, set
`SENTRY_RELEASE` to the deployed commit when rolling out a release. Go derives
the release from its compiled VCS revision unless explicitly overridden, and
tags every event with the executable name (`service`). Do not set one fixed
`SENTRY_SERVICE` for all Go binaries.

Error events are enabled at 100%; request bodies, headers, cookies, user
profiles and arbitrary log context are removed from new backend events.
Tracing defaults to `0` for PHP and Node and is disabled for Go. This rollout
covers error tracking; no continuous profiling or log-stream ingestion is
enabled. To sample PHP/Node performance later, set
`SENTRY_TRACES_SAMPLE_RATE` to a value from `0` to `1` after review.

## Runtime environment

Edit the existing files yourself before starting the deployment workflows.
Replace each placeholder with the DSN for that specific Sentry project.

PHP (`/var/www/fun-php/repo/src/.env`):

```dotenv
SENTRY_LARAVEL_DSN=<getreplay-php DSN>
SENTRY_ENVIRONMENT=production
SENTRY_RELEASE=<deployed PHP commit SHA>
SENTRY_TRACES_SAMPLE_RATE=0
```

Node (`/home/solo/getreplay-node/.env`):

```dotenv
SENTRY_DSN=<getreplay-node DSN>
SENTRY_ENVIRONMENT=production
SENTRY_RELEASE=<deployed Node commit SHA>
SENTRY_TRACES_SAMPLE_RATE=0
```

Go (`/var/www/getreplay-go/getreplay-go.env`, loaded by the existing launchers):

```dotenv
SENTRY_DSN='<getreplay-go DSN>'
SENTRY_ENVIRONMENT=production
```

Go sets the release and service name automatically. PHP/Node `SENTRY_RELEASE`
is optional, but it must match the deployed revision if set; update it for
future deployments as well. No Sentry auth token is needed for error reporting.

## Deploy and verify

1. Configure PHP and Node's existing server environment files using the correct
   project DSNs and `SENTRY_ENVIRONMENT=production`. Add Go's variables to its
   existing shared environment file (the optional `sentry.json` is an alternative).
2. Include the PHP, Node and Go commits in the reviewed release candidate.
   Use the existing protected **Deploy PHP**, **Deploy Node GC** and **Deploy
   Go services** workflows from [the release runbook](../release/README.md).
   Rebuild/redeploy all five Go binaries, including the scheduled and manual
   processors. Updating their shared environment alone does not install SDKs.
3. PHP deployment runs package discovery and rebuilds Laravel's config cache.
   If only its environment changes, run `php artisan config:cache` as the
   application user and reload PHP-FPM. Restart long-lived PHP queue workers,
   if present. Node and long-lived Go services need a restart to read changed
   environment values. Cron/manual Go launchers source the shared file on each run.
4. Verify one synthetic event per project in Sentry, then check service health.
   The event's `environment`, `release` and Go `service` tag should be correct.

The Node singleton must never be started a second time for verification.
Its smoke command loads only instrumentation and does not import `App` or Steam:

```sh
# In the deployed Node release; systemd normally supplies the existing .env.
# When running manually, preload only its SENTRY_* variables, not Steam settings.
npm run sentry:test

# In the Laravel application, as the runtime user:
php artisan sentry:test

# In the Go checkout with only SENTRY_DSN and SENTRY_ENVIRONMENT exported:
go run ./cmd/sentry-test
```

`submitted`/successful flush is not proof of receipt: confirm the event in the
corresponding Sentry project. Empty DSN disables the integration. To roll back,
set the DSN explicitly empty and refresh/restart the relevant runtime as described above.

## Coverage and limitations

- PHP reports Laravel exceptions (HTTP, console and jobs) and error-level
  messages in the default log stack. To report a caught exception, use
  Laravel's `report($exception)`. Exception objects in the Sentry log channel
  are skipped to avoid double reporting by the exception handler.
- Node captures Express errors, handled `logger.error` calls, uncaught
  exceptions and rejected promises. Its existing reconnect/process behavior
  is retained. Fatal startup and signal shutdown flush pending events.
- Go attaches the official Logrus hook to the shared logger and preserves it
  across logger setup. Errors, fatal logs, recovered worker errors and main
  panics are captured. Fatal/panic logs flush before termination. Arbitrary
  `panic` in an unwrapped goroutine cannot be caught by a main-goroutine defer;
  use existing safe worker wrappers. `os.Exit` and `SIGKILL` bypass defers.
- Credentials are filtered in supported message/URL formats; application code
  must still avoid embedding secrets in arbitrary free-form exception text.

Local tests use an in-memory transport, including Laravel exception de-duplication,
Go logger reset and shutdown, and Node stack traces. Node regression tests do not
authenticate to Steam. No frontend build is required for this backend rollout.
