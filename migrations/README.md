# Production database migrations

The commands in this directory update the production migrations checkout with a
fast-forward-only pull and then apply all pending `up` migrations.

## One-time setup

Create the production-only environment file and fill in the real connection
URLs. The file is ignored by Git and should only be readable by the deployment
user.

```bash
cd /home/solo/infra/migrations
cp .env.example .env
chmod 600 .env
editor .env
```

The default migrations checkout is `/home/solo/fun-migrations/migrations` on
branch `main`. Override `MIGRATIONS_DIR`, `MIGRATIONS_REMOTE`, or
`MIGRATIONS_BRANCH` in `.env` if the production layout changes. The `.env` file
uses shell syntax, so keep connection URLs quoted and URL-encode reserved
characters in credentials.

## Run

MySQL:

```bash
/home/solo/infra/migrations/mysql.sh
```

ClickHouse:

```bash
/home/solo/infra/migrations/clickhouse.sh
```

Each command stops before migration execution if the checkout is on a different
branch, has tracked local changes, cannot be fast-forwarded, or lacks the
required database URL.
