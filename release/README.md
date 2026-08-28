# Release-only gateway

`getreplay_release.py` is the only production command exposed to the agent's SSH
identity. It can inspect release status and deploy or roll back an allowlisted
application component to an exact commit already contained in `origin/main`.

It deliberately cannot run migrations, execute one-shot data jobs, edit databases,
accept branches, accept abbreviated SHAs, or pass arbitrary environment variables.
The Go one-shot components can be *installed* as a release, but this gateway cannot
run them.

## Server installation

Keep this repository at `/home/solo/infra`. Create a separate locked account; it has a
normal shell only because `sshd` needs one to start a forced command, but it gets no
password and no unrestricted authorized key:

```bash
sudo useradd --create-home --shell /bin/bash getreplay-release
sudo passwd --lock getreplay-release
sudo chown root:root /home/getreplay-release
sudo chmod 0755 /home/getreplay-release

sudo install -d -o root -g root -m 0755 /usr/local/libexec/getreplay-release
sudo install -o root -g root -m 0755 \
  /home/solo/infra/release/forced_command.py \
  /home/solo/infra/release/getreplay_release.py \
  /usr/local/libexec/getreplay-release/
```

Allow that account to run exactly the validated release program as the existing
`solo` deploy user. Create `/etc/sudoers.d/getreplay-release` with this one rule and
validate it with `visudo -cf /etc/sudoers.d/getreplay-release`:

```sudoers
getreplay-release ALL=(solo) NOPASSWD: /usr/bin/python3 /home/solo/infra/release/getreplay_release.py *
```

Create a root-owned SSH directory and `authorized_keys`. Prefix the dedicated public
key with the forced command below; replace only `<PUBLIC-KEY>` and never copy the
private key to the server or repository.

```bash
sudo install -d -o root -g root -m 0755 /home/getreplay-release/.ssh
sudoedit /home/getreplay-release/.ssh/authorized_keys
sudo chown root:root /home/getreplay-release/.ssh/authorized_keys
sudo chmod 0600 /home/getreplay-release/.ssh/authorized_keys
```

```text
restrict,command="/usr/bin/python3 /usr/local/libexec/getreplay-release/forced_command.py" <PUBLIC-KEY>
```

`restrict` disables PTY allocation, forwarding, agent forwarding and X11 forwarding.
The forced-command parser accepts only these shapes:

```text
getreplay-release status [component]
getreplay-release preview <component> <40-char-commit>
getreplay-release deploy <component> <40-char-commit>
getreplay-release rollback <component> <40-char-commit>
```

Allowed components are `frontend`, `php`, `go-match-updater`, `go-demo-uploader`,
`go-highlight-extractor`, `go-replay-converter`, and `go-stats-extractor`.

The forced command validates every token before delegating to the existing `solo`
deploy user. The release account itself gets no repository write access, database
credentials, service sudo rules, or general interactive login. The existing deploy
user keeps the service permissions already needed by the component scripts.

Verify the boundary before configuring the agent client:

```bash
ssh -i /path/to/release-only-key getreplay-release@SERVER \
  'getreplay-release status frontend'
ssh -i /path/to/release-only-key getreplay-release@SERVER 'id'
```

The first command must return JSON status; the second must be rejected by the forced
command. Also verify that SSH without a command does not open a shell.

## Local checks

```bash
python3 -m unittest discover -s release/tests -v
python3 release/getreplay_release.py preview frontend <40-char-commit>
```

Production setup is intentionally a separate, confirmed step: installing a public
key changes persistent access and must be reviewed against the exact target host.
