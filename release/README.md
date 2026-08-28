# Human-approved production releases

Production is deployed from the manual **Deploy production** workflow in this
repository. The workflow stages the reviewed `release/candidate.json`, previews the
exact plan, and promotes it through one forced-command SSH identity.

The agent has no production key, shell, sudo rule, database credential, or Docker
socket access. The private release key exists only in the protected GitHub
`production` Environment. A person with access to that Environment starts and
approves the workflow.

## Security boundary

The SSH identity accepts only these commands:

```text
getreplay-release status
getreplay-release stage <release-id> <base64-json-manifest>
getreplay-release preview promote <release-id>
getreplay-release promote <release-id>
```

The forced command parses the request and forwards one bounded JSON message to a
root-owned broker over `/run/getreplay-release/control.sock`. There is no shell
fallback. The broker validates every field, installs staged manifests as root-owned
mode `0600` files, maps promotion to one root-owned adapter, serializes releases,
snapshots the manifest, and writes structured audit events to the systemd journal.

The adapter fetches only full commits contained in each repository's `origin/main`,
verifies a deterministic `git archive` SHA-256 digest, and calls root-owned
deployment entrypoints. Git fetches and application builds run as the existing
`solo` deployment user; only the fixed adapter controls service operations and the
application of allowlisted migrations.

This is intentionally systemd + GitHub Actions, not Kubernetes. The product runs on
one host, so Kubernetes would add another privileged control plane without solving a
current scaling or availability requirement.

## What the button deploys

[`candidate.json`](candidate.json) pins the reviewed frontend, PHP, Go, MySQL
migration, and ClickHouse migration commits plus their source archive digests. The
fixed order is migrations, PHP, Go, and frontend, followed by the existing service
and HTTP health checks.

Changing the candidate or workflow requires review from `@soloser` through
`.github/CODEOWNERS`. Protect `main`; otherwise an account able to push directly to
`main` could alter the workflow or candidate before the human presses the button.

## One-time server installation

Generate the workflow key on a trusted machine. Do not put the private key on the
production server or agent workstation:

```bash
ssh-keygen -t ed25519 -f github-getreplay-release -C github-getreplay-release
```

Copy only `github-getreplay-release.pub` to the server. After this infra commit is on
the server, run:

```bash
cd /home/solo/infra
git pull --ff-only origin main
sudo ./release/install-server.sh /path/to/github-getreplay-release.pub
```

The installer installs root-owned code under
`/usr/local/libexec/getreplay-release`, creates the locked `getreplay-release`
account, replaces that dedicated account's `authorized_keys` with the supplied key,
and enables the broker. It refuses to proceed if an unexpected sudoers file exists
or required production paths are missing.

Verify the boundary:

```bash
sudo systemctl status getreplay-release-broker.service --no-pager
sudo -l -U getreplay-release
sudo journalctl -u getreplay-release-broker.service -n 30 --no-pager
```

`sudo -l` must report no allowed commands for `getreplay-release`.

## GitHub Environment

Create a GitHub Environment named `production` for `soloser/getreplay-infra`.

Variables:

- `PRODUCTION_RELEASE_HOST` — production hostname or IP;
- `PRODUCTION_RELEASE_PORT` — normally `22`;
- `PRODUCTION_RELEASE_USER` — `getreplay-release`.

Secrets:

- `PRODUCTION_RELEASE_SSH_KEY` — contents of `github-getreplay-release`;
- `PRODUCTION_RELEASE_KNOWN_HOSTS` — the reviewed `ssh-keyscan` line for the server.

Require reviewer `@soloser` and restrict deployments to protected `main`. Protect
the repository's `main` branch with pull requests and CODEOWNER review. The workflow
has only `contents: read`, uses no third-party action, exposes no deployment inputs,
and is serialized by the `getreplay-production` concurrency group.

## Running a release

Open **Actions → Deploy production → Run workflow** on `main`, approve the protected
`production` Environment, and read the staged preview before promotion. The workflow
always deploys `candidate.json` from the exact infra commit running the workflow.

Inspect the last result:

```bash
/usr/bin/python3 /usr/local/libexec/getreplay-release/getreplay_release.py status
sudo journalctl -u getreplay-release-broker.service -n 100 --no-pager
```

## Preparing the next candidate

Update SHAs only after the commits are on `origin/main`. Recalculate each digest:

```bash
git archive --format=tar <full-commit-sha> | sha256sum
```

Use the same digest for every Go component pinned to the same Go commit. Run checks,
merge an owner-reviewed pull request to protected `main`, then press the same button.

## Local checks

```bash
python3 -m unittest discover -s release/tests -v
python3 release/broker.py check
bash -n release/install-server.sh frontend/deploy.sh go/deploy.sh php/deploy.sh migrations/*.sh
```
