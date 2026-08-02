# PHP production deployment

Production PHP runs directly on the host. Redis and the PHP Redis extension must
therefore also be installed on the host; the Docker configuration is only for
local development.

## One-time host setup

The examples below assume Ubuntu, PHP 8.4, Laravel in
`/var/www/fun-php/repo/src`, and the infra repository in `/home/solo/infra`.

```bash
sudo apt update
sudo apt install redis-server php8.4-redis
sudo systemctl enable --now redis-server
sudo systemctl restart php8.4-fpm

redis-server --version
redis-cli ping
php -m | grep -i '^redis$'
```

Redis must be version 7 or newer, `redis-cli ping` must return `PONG`, and PHP CLI
must list `redis`. Check the FPM configuration separately because CLI and FPM
use different `php.ini` trees:

```bash
sudo php-fpm8.4 -i | grep -i 'Redis Support'
```

Keep Redis private to the server. In `/etc/redis/redis.conf`, retain loopback
binding and protected mode:

```text
bind 127.0.0.1 ::1
protected-mode yes
```

Persistence is recommended so a host restart does not empty the feed before the
next cron run:

```text
appendonly yes
```

After changing Redis configuration:

```bash
sudo systemctl restart redis-server
redis-cli ping
```

Laravel's production `.env` must contain the dedicated feed connection. This
does not change the application's default cache store:

```dotenv
REDIS_CLIENT=phpredis
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=null
REDIS_HIGHLIGHT_FEED_DB=2
REDIS_HIGHLIGHT_FEED_PREFIX=
HIGHLIGHT_FEED_REDIS_CONNECTION=highlight_feed
HIGHLIGHT_FEED_SIZE=5000
HIGHLIGHT_FEED_LOOKBACK_DAYS=30
HIGHLIGHT_FEED_CANDIDATES_PER_TYPE=5000
```

The deployment user must be able to update `/var/www/fun-php/repo`; Laravel's
`storage` and `bootstrap/cache` must remain writable by the PHP/cron user
`www-data`. The deployment script runs Artisan as that user. Composer must be
installed and available in `PATH`.

## Deploy

Update the infra checkout first, then run the PHP deployment script:

```bash
git -C /home/solo/infra pull --ff-only
/home/solo/infra/php/deploy.sh
```

The script:

1. verifies PHP, Composer, `flock`, the `redis` PHP extension, the local Redis
   connection, and PHP-FPM;
2. fetches the target branch and ensures the host cron daemon is enabled before
   maintenance mode;
3. enables maintenance mode, then fast-forwards the PHP checkout to
   `origin/main` (and stops on local tracked changes or a divergent branch
   instead of overwriting them);
4. runs production `composer install` and rebuilds Laravel's config cache;
5. installs changed cron wrappers and `/etc/cron.d` definitions;
6. gracefully reloads `php8.4-fpm` and disables maintenance mode.

Defaults can be overridden when the production layout differs:

```bash
REPO_ROOT=/srv/getreplay/php \
APP_USER=www-data \
PHP_FPM_SERVICE=php8.4-fpm.service \
/home/solo/infra/php/deploy.sh
```

For a non-default Redis endpoint, pass `REDIS_DEPLOY_HOST` and
`REDIS_DEPLOY_PORT`. `redis-cli` also honors `REDISCLI_AUTH` when authentication
is enabled.

If deployment fails after maintenance mode was enabled, it intentionally leaves
the application in maintenance mode. Fix the reported error, rerun the deploy,
or explicitly recover with:

```bash
cd /var/www/fun-php/repo/src
sudo -u www-data -- php artisan up
```

## Scheduled highlight feed

The deploy installs:

- `/var/www/fun-php/bin/highlight-feed-cron.sh`;
- `/etc/cron.d/getreplay-highlight-feed`.

At `03:40` server time the job takes a non-blocking lock, runs the deployed Go
highlight extractor, and only after it succeeds runs
`php artisan highlights:rebuild-feed`. Output goes to
`/var/log/highlight-feed.log`. The existing hourly Go extractor cron remains
unchanged.

After the first deployment, create the initial feed immediately instead of
waiting for the daily cron:

```bash
sudo -u www-data /var/www/fun-php/bin/highlight-feed-cron.sh
```

Verify the result:

```bash
sudo tail -n 100 /var/log/highlight-feed.log
redis-cli -n 2 --raw GET highlight_feed:current
sudo cat /etc/cron.d/getreplay-highlight-feed
```

The final API check must be made with a valid JWT belonging to user `id=1`; the
feed endpoint intentionally returns `401` without authentication and `403` for
other users.
