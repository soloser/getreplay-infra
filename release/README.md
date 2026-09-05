# Human-approved production releases

Production is deployed from manual workflows in this repository. **Deploy production**
still promotes the complete reviewed `release/candidate.json`. The separate **Deploy
frontend**, **Deploy Node GC**, **Deploy PHP**, **Deploy Go services**, and **Deploy
migrations** buttons select only their fixed scope from that same reviewed candidate.
Every button owns a normal job gated by the protected `production` Environment so its
Environment secrets are resolved directly by GitHub. The job fetches the reviewed
`release/run-production-scope.sh` from its exact workflow commit, stages its reduced
manifest, previews the exact plan, and promotes it through the same forced-command SSH
identity and global production lock. Do not replace these jobs with a reusable-workflow
call: GitHub does not reliably expose protected Environment secrets through that boundary.

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

The persistent broker keeps `NoNewPrivileges`, `MemoryDenyWriteExecute`, and a
hidden home directory. For promotion it asks systemd to create one short-lived,
uniquely named executor with a fixed root-owned adapter and a narrow writable-path
list; the executor is collected immediately when it finishes. The adapter fetches
only full commits contained in each repository's `origin/main`, verifies a
deterministic `git archive` SHA-256 digest, and calls root-owned deployment
entrypoints. Git fetches and application builds run as the existing `solo`
deployment user; only the fixed adapter controls service operations and the
application of allowlisted migrations.

This is intentionally systemd + GitHub Actions, not Kubernetes. The product runs on
one host, so Kubernetes would add another privileged control plane without solving a
current scaling or availability requirement.

## What the button deploys

[`candidate.json`](candidate.json) pins the reviewed frontend, PHP, production Node GC, selected Go commands, and any
explicitly selected migrations plus their source archive digests. The protocol
supports separate Kafka workers for future scaling, but the current single-process candidate
deliberately omits them. The fixed order is migrations, PHP, Node, Go services and one-shot Go
tools, and frontend, followed by the existing service and
HTTP health checks.

Changing the candidate or workflow requires review from `@soloser` through
`.github/CODEOWNERS`. Protect `main`; otherwise an account able to push directly to
`main` could alter the workflow or candidate before the human presses the button.

Adding a component is also a one-time control-plane update. Before the first Node GC release,
update `/home/solo/infra`, rerun `release/install-server.sh`, and verify that
`/home/solo/getreplay-node-releases/current` points to the existing checkout. This installs the
Node adapter without restarting `node-app`; the **Deploy Node GC** workflow performs the first
controlled switch, one restart, the revision-2 health gate, and rollback if needed. Kafka itself
is installed once using the reviewed procedure in `kafka/README.md`; no additional Go unit is
introduced for the queue migration.

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

Open the component-specific workflow under **Actions**, press **Run workflow** on `main`,
approve the protected `production` Environment, and read the staged preview before promotion.
Use **Deploy production** only when every component in the candidate should move together.
Every button reads `candidate.json` from the exact infra commit running the workflow; the fixed
scope selector cannot add components or migrations that were not reviewed there.

Inspect the last result:

```bash
/usr/bin/python3 /usr/local/libexec/getreplay-release/getreplay_release.py status
sudo journalctl -u getreplay-release-broker.service -n 100 --no-pager
```

## Preparing the next candidate

The preferred path is the manual **Prepare release candidate** workflow in GitHub Actions.
It provides a component dropdown for frontend, PHP, Node GC, the currently selected Go
services, or migrations. For migrations, choose MySQL, ClickHouse, or both in the database
dropdown; the workflow replaces the candidate's migration scope with exactly that selection and
generates its audit identifier from the database and source revision. Leave revision as `main` to
pin the latest source main, including every earlier merged change, or enter a full commit SHA
already contained in source main. The workflow:

1. reads the private source repository with a read-only token;
2. verifies the requested commit is contained in its `main` branch;
3. refuses to move the selected source behind the revision in the current candidate;
4. calculates the deterministic `git archive` digest;
5. updates only the selected scope, runs the release checks, and creates or updates an
   owner-reviewed candidate pull request.

The preparer has no `production` Environment and receives none of its secrets. Configure it once:

- create a fine-grained token with read-only **Contents** access to `getreplay-front`,
  `getreplay-php`, `getreplay-node`, `getreplay-go`, and `getreplay-migrations`;
- save it in this repository as the Actions secret `RELEASE_CANDIDATE_SOURCE_TOKEN`;
- under **Settings → Actions → General → Workflow permissions**, allow read and write access and
  allow GitHub Actions to create pull requests.

Then open **Actions → Prepare release candidate → Run workflow**, keep the workflow branch on
`main`, choose the component, keep revision at `main` unless a reviewed older commit is required,
and optionally add a PR note. Review and merge the generated PR before using the matching deploy
button. A rerun for the same component safely rebuilds its automation-owned branch from current
infra `main` and updates the open PR.

For a manual component candidate, update SHAs only after the commits are on `origin/main` and
recalculate each digest:

```bash
git archive --format=tar <full-commit-sha> | sha256sum
```

Use the same digest for every Go component pinned to the same Go commit. Node uses the digest
of the pinned `getreplay-node` commit and is deployed independently with **Deploy Node GC**.
The current candidate omits optional standalone worker components because Match Updater owns the
complete Kafka pipeline. Run checks,
merge an owner-reviewed pull request to protected `main`, then press the same button.

## Local checks

```bash
python3 -m unittest discover -s release/tests -v
python3 release/broker.py check
bash -n release/install-server.sh release/run-production-scope.sh frontend/deploy.sh go/deploy.sh php/deploy.sh migrations/*.sh
```
