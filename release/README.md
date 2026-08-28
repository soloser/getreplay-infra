# Release broker

This directory defines the production release handle used only by the manually
started GitHub Actions workflow. The agent has no production SSH key, deployment
API token, shell, sudo rule, database credential, or Docker socket access. The
workflow's forced SSH identity can only submit a bounded request to a root-owned
broker over `/run/getreplay-release/control.sock`.

The broker accepts these shapes:

```text
getreplay-release status
getreplay-release preview promote <release-id>
getreplay-release promote <release-id>
```

A release ID is useful only if a root-owned, non-group-writable manifest already
exists under `/var/lib/getreplay-release/manifests/<release-id>.json`. The manifest
binds an allowlisted component or database migration to both a full Git revision and
an immutable `sha256:` artifact digest. It cannot choose an executable. The broker
maps the complete release to one fixed root-owned adapter:

```text
/usr/local/libexec/getreplay-release/adapters/promote-release
```

This separation is intentional: trusted CI registers tested immutable artifacts as
the `candidate` manifest, while only a human with GitHub repository write access can
press **Run workflow**. Giving an agent permission to deploy any commit it can push
would also give that commit a code-execution path on production.

## Human-only button

The button is `.github/workflows/deploy-production.yml`. It has only the manual
`workflow_dispatch` trigger, deploys the fixed `candidate` manifest, uses a protected
`production` environment, and serializes releases with a concurrency group. It uses
no third-party actions and never checks repository code out onto the runner.

Configure the `production` GitHub Environment with:

- variables `PRODUCTION_RELEASE_HOST`, `PRODUCTION_RELEASE_PORT`, and
  `PRODUCTION_RELEASE_USER`;
- secrets `PRODUCTION_RELEASE_SSH_KEY` and `PRODUCTION_RELEASE_KNOWN_HOSTS`;
- deployment branch restriction: protected `main` only.

Protect `main` with required pull requests and required CODEOWNER review. The agent's
Git credential may create branches and pull requests but must not be able to push
directly to `main`. This is what prevents an agent-authored workflow change from
obtaining the production environment secret.

## Server installation preview

Do not install this until the production adapters and immutable artifacts exist.
The broker fails closed when an adapter or manifest is missing. On the production
host, review and run:

```bash
sudo groupadd --system getreplay-release
sudo useradd --system --create-home --shell /bin/sh --gid getreplay-release getreplay-release
sudo passwd --lock getreplay-release

sudo install -d -o root -g root -m 0755 /usr/local/libexec/getreplay-release
sudo install -d -o root -g root -m 0755 /usr/local/libexec/getreplay-release/adapters
sudo install -o root -g root -m 0755 \
  /home/solo/infra/release/broker.py \
  /home/solo/infra/release/forced_command.py \
  /home/solo/infra/release/getreplay_release.py \
  /home/solo/infra/release/release_client.py \
  /home/solo/infra/release/release_protocol.py \
  /usr/local/libexec/getreplay-release/
sudo install -o root -g root -m 0644 \
  /home/solo/infra/systemd/getreplay-release-broker.service \
  /etc/systemd/system/getreplay-release-broker.service

sudo install -d -o root -g root -m 0755 /var/lib/getreplay-release/manifests
sudo systemctl daemon-reload
sudo systemctl enable --now getreplay-release-broker.service
```

Install the workflow's dedicated public key in a root-owned
`/home/getreplay-release/.ssh/authorized_keys`. Keep the private key off the server
and out of every repository. The line must include the forced command:

```text
restrict,command="/usr/bin/python3 /usr/local/libexec/getreplay-release/forced_command.py" <PUBLIC-KEY>
```

The private key goes only into the GitHub `production` Environment secret; do not
place it on the agent workstation. There must be no
`/etc/sudoers.d/getreplay-release` file and the account must not be
in `sudo`, `docker`, `www-data`, or application groups. `restrict` disables PTY,
forwarding, agent forwarding and X11 forwarding. Verify the boundary:

```bash
ssh -i /path/to/release-only-key getreplay-release@SERVER \
  'getreplay-release status'
ssh -i /path/to/release-only-key getreplay-release@SERVER 'id'
sudo -l -U getreplay-release
```

The status command must return JSON, `id` must be rejected by the forced command,
and `sudo -l` must show no permitted commands.

Every accepted request, completion, and error is emitted as structured JSON to the
systemd journal together with Unix peer PID/UID/GID when the kernel exposes them.
Inspect it with `journalctl -u getreplay-release-broker.service`.

## Adapter contract

The `promote-release` adapter is the only privileged deployment implementation. It
must:

- be an executable regular file owned by root and not writable by group or others;
- accept exactly `--manifest PATH --release-id ID`;
- re-read and verify the named manifest and artifact digest;
- deploy every component and forward migration listed in the manifest, never accept
  a service, path, command, SQL statement or image name from the caller;
- use a root-owned production Compose file and deploy an image by digest;
- run migrations using database roles limited to schema changes, not the
  applications' unrestricted credentials;
- perform a health check and return non-zero on failure.

Do not adapt the old native `deploy.sh` scripts by running them as `solo`: build
scripts from a commit would inherit everything `solo` can do. The intended next
step is to build images in an isolated hosted CI runner, publish them by digest,
and add small Compose adapters here.

## Local checks

```bash
python3 -m unittest discover -s release/tests -v
python3 release/broker.py check
```
