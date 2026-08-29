# Production Node GC deployment

The Node service is a production-only singleton. It is deployed only through **Deploy Node GC**
in the infra repository. The workflow selects the immutable `node` entry from
`release/candidate.json`; the server adapter builds a fresh release directory, atomically moves
`/home/solo/getreplay-node-releases/current`, restarts `node-app.service` once, and requires the
revision-2 GC safety health response. A failed health check restores the previous symlink and
systemd unit.

Before the first Node release, update `/home/solo/infra` on production and rerun the release
installer with the already reviewed GitHub release public key:

```bash
cd /home/solo/infra
git pull --ff-only origin main
sudo ./release/install-server.sh /path/to/github-getreplay-release.pub
```

The installer does not restart Node. It installs the allowlisted adapter and initializes the
`current` symlink to the existing checkout. Never put the Steam credentials in the candidate,
workflow inputs, repository, or command line.
