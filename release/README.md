# Release-only gateway

`getreplay_release.py` is the only production command exposed to the agent's SSH
identity. It can inspect release status and deploy or roll back an allowlisted
application component to an exact commit already contained in `origin/main`.

It deliberately cannot run migrations, execute one-shot data jobs, edit databases,
accept branches, accept abbreviated SHAs, or pass arbitrary environment variables.
The Go one-shot components can be *installed* as a release, but this gateway cannot
run them.

## Server installation

Keep this repository at `/home/solo/infra`, then add a dedicated public key to
`/home/solo/.ssh/authorized_keys` with a forced command. Replace the placeholder with
the reviewed public key; never commit the key itself here.

```text
restrict,command="/usr/bin/python3 /home/solo/infra/release/forced_command.py" <PUBLIC-KEY>
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

The existing deploy user still needs its narrowly scoped `sudo` rights for service
restart/reload and the files already installed by the component scripts. Do not give
the release identity a general interactive login or database credentials.

## Local checks

```bash
python3 -m unittest discover -s release/tests -v
python3 release/getreplay_release.py preview frontend <40-char-commit>
```

Production setup is intentionally a separate, confirmed step: installing a public
key changes persistent access and must be reviewed against the exact target host.
