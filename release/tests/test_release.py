from __future__ import annotations

import json
import base64
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


RELEASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE_DIR))

import broker  # noqa: E402
import promote_release  # noqa: E402
import release_protocol  # noqa: E402
import select_scope  # noqa: E402


class ReleaseProtocolTest(unittest.TestCase):
    def test_forced_command_accepts_only_whole_release_promotion(self) -> None:
        promotion = release_protocol.parse_ssh_command(
            "getreplay-release promote 20260828t120000z-release"
        )
        preview = release_protocol.parse_ssh_command(
            "getreplay-release preview promote 20260828t120000z-release"
        )

        self.assertEqual("promote", promotion.operation)
        self.assertIsNone(promotion.target)
        self.assertTrue(preview.preview)

    def test_stage_accepts_only_one_bounded_manifest_payload(self) -> None:
        encoded = base64.b64encode(b'{"version":1}').decode("ascii")
        request = release_protocol.parse_ssh_command(
            f"getreplay-release stage candidate {encoded}"
        )

        self.assertEqual("stage", request.operation)
        self.assertEqual("candidate", request.release_id)
        self.assertEqual(encoded, request.manifest)

    def test_shell_and_path_injection_are_rejected(self) -> None:
        commands = (
            "bash",
            "getreplay-release status frontend",
            "getreplay-release status; id",
            "getreplay-release deploy frontend ../../etc/passwd",
            "getreplay-release migrate mysql release;id",
            "getreplay-release migrate production release-1",
            "getreplay-release stage candidate payload extra",
            "getreplay-release preview stage candidate payload",
        )
        for command in commands:
            with self.subTest(command=command):
                with self.assertRaises(release_protocol.ReleaseError):
                    release_protocol.parse_ssh_command(command)

    def test_protocol_rejects_extra_json_fields(self) -> None:
        request = release_protocol.Request("status")
        payload = json.loads(release_protocol.encode_request(request))
        payload["command"] = "id"

        with self.assertRaises(release_protocol.ReleaseError):
            release_protocol.decode_request(json.dumps(payload).encode() + b"\n")


class BrokerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.manifests = self.root / "manifests"
        self.state = self.root / "state"
        self.adapters = self.root / "adapters"
        self.manifests.mkdir(mode=0o700)
        self.adapters.mkdir(mode=0o700)
        self.config = broker.BrokerConfig(
            socket_path=self.root / "broker.sock",
            manifest_root=self.manifests,
            state_root=self.state,
            adapter_root=self.adapters,
            trusted_uid=os.getuid(),
            systemd_run=None,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_manifest(self, release_id: str = "release-1") -> Path:
        path = self.manifests / f"{release_id}.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "release_id": release_id,
                    "components": {
                        "frontend": {
                            "revision": "a" * 40,
                            "artifact": "sha256:" + "b" * 64,
                        }
                    },
                    "migrations": {
                        "mysql": {
                            "revision": "c" * 40,
                            "artifact": "sha256:" + "d" * 64,
                            "migration": "mysql-c00000000000",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)
        return path

    def write_adapter(self, name: str) -> Path:
        path = self.adapters / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o700)
        return path

    def test_preview_resolves_only_trusted_manifest_and_fixed_adapter(self) -> None:
        self.write_manifest()
        adapter = self.write_adapter("promote-release")

        payload = broker.execute(
            self.config,
            release_protocol.Request("promote", None, "release-1", preview=True),
        )

        self.assertEqual(str(adapter), payload["adapter"])
        self.assertEqual("sha256:" + "b" * 64, payload["components"]["frontend"]["artifact"])
        self.assertFalse(payload["arbitrary_shell"])
        self.assertFalse(self.state.exists())

    def test_promotion_runs_only_a_fixed_adapter_and_records_state(self) -> None:
        self.write_manifest()
        self.write_adapter("promote-release")

        payload = broker.execute(
            self.config,
            release_protocol.Request("promote", None, "release-1"),
        )

        self.assertEqual(0, payload["exit_code"])
        self.assertEqual("mysql-c00000000000", payload["migrations"]["mysql"]["migration"])
        self.assertTrue((self.state / "last-release.json").is_file())
        snapshot = Path(payload["execution_manifest"])
        self.assertTrue(snapshot.is_file())
        self.assertEqual("release-1", json.loads(snapshot.read_text(encoding="utf-8"))["release_id"])

    def test_failed_adapter_records_root_only_diagnostics(self) -> None:
        self.write_manifest()
        adapter = self.adapters / "promote-release"
        adapter.write_text(
            '#!/bin/sh\necho \'{"status":"error","error":"checkout is dirty"}\'\nexit 2\n',
            encoding="utf-8",
        )
        adapter.chmod(0o700)

        with self.assertRaisesRegex(broker.BrokerError, "checkout is dirty"):
            broker.execute(
                self.config,
                release_protocol.Request("promote", None, "release-1"),
            )

        failure = self.state / "last-failure.json"
        self.assertTrue(failure.is_file())
        self.assertEqual(0, stat.S_IMODE(failure.stat().st_mode) & 0o077)
        self.assertEqual("checkout is dirty", json.loads(failure.read_text())["adapter_error"])
        self.assertEqual("checkout is dirty", broker.status(self.config)["last_failure"]["adapter_error"])

    def test_adapter_error_extraction_ignores_non_json_output(self) -> None:
        self.assertEqual(
            "safe failure",
            broker._extract_adapter_error(
                'npm output\n{"status":"error","error":"safe failure"}\n',
                "systemd noise",
            ),
        )

    def test_group_writable_manifest_and_adapter_are_rejected(self) -> None:
        manifest = self.write_manifest()
        manifest.chmod(0o620)
        with self.assertRaisesRegex(broker.BrokerError, "ownership or mode"):
            broker.load_manifest(self.config, "release-1")

        manifest.chmod(0o600)
        adapter = self.write_adapter("promote-release")
        adapter.chmod(stat.S_IRWXU | stat.S_IWGRP)
        with self.assertRaisesRegex(broker.BrokerError, "ownership or mode"):
            broker.plan(
                self.config,
                release_protocol.Request("promote", None, "release-1"),
            )

    def test_manifest_cannot_select_an_adapter(self) -> None:
        path = self.write_manifest()
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["components"]["frontend"]["adapter"] = "/bin/sh"
        path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(broker.BrokerError, "unexpected fields"):
            broker.load_manifest(self.config, "release-1")

    def test_stage_validates_and_atomically_installs_manifest(self) -> None:
        source = self.write_manifest()
        encoded = base64.b64encode(source.read_bytes()).decode("ascii")
        source.unlink()

        result = broker.stage_manifest(
            self.config,
            release_protocol.Request("stage", None, "release-1", False, encoded),
        )

        self.assertEqual("release-1", result["release_id"])
        staged = self.manifests / "release-1.json"
        self.assertTrue(staged.is_file())
        self.assertEqual(0, stat.S_IMODE(staged.stat().st_mode) & 0o077)

    def test_stage_rejects_invalid_base64_without_replacing_candidate(self) -> None:
        original = self.write_manifest()
        before = original.read_bytes()

        with self.assertRaisesRegex(broker.BrokerError, "base64 JSON"):
            broker.stage_manifest(
                self.config,
                release_protocol.Request("stage", None, "release-1", False, "not@base64"),
            )

        self.assertEqual(before, original.read_bytes())


class PromotionPlanTest(unittest.TestCase):
    def manifest(self) -> dict[str, object]:
        return {
            "components": {
                "frontend": {"revision": "a" * 40, "artifact": "sha256:" + "b" * 64},
                "php": {"revision": "c" * 40, "artifact": "sha256:" + "d" * 64},
                "node": {"revision": "7" * 40, "artifact": "sha256:" + "8" * 64},
                "go-demo-uploader": {
                    "revision": "e" * 40,
                    "artifact": "sha256:" + "f" * 64,
                },
                "go-match-updater": {
                    "revision": "e" * 40,
                    "artifact": "sha256:" + "f" * 64,
                },
                "go-match-discovery-worker": {
                    "revision": "e" * 40,
                    "artifact": "sha256:" + "f" * 64,
                },
                "go-demo-downloader-worker": {
                    "revision": "e" * 40,
                    "artifact": "sha256:" + "f" * 64,
                },
                "go-demo-processor-worker": {
                    "revision": "e" * 40,
                    "artifact": "sha256:" + "f" * 64,
                },
            },
            "migrations": {
                "mysql": {
                    "revision": "1" * 40,
                    "artifact": "sha256:" + "2" * 64,
                    "migration": "mysql-release",
                }
            },
        }

    def test_deployment_order_is_fixed(self) -> None:
        self.assertEqual(
            [
                "migration:mysql",
                "component:php",
                "component:node",
                "component:go-demo-processor-worker",
                "component:go-demo-downloader-worker",
                "component:go-match-discovery-worker",
                "component:go-match-updater",
                "component:go-demo-uploader",
                "component:frontend",
            ],
            promote_release.deployment_order(self.manifest()),
        )

    def test_queue_worker_components_map_to_fixed_commands(self) -> None:
        self.assertEqual(
            {
                "go-match-discovery-worker": "match-discovery-worker",
                "go-demo-downloader-worker": "demo-downloader-worker",
                "go-demo-processor-worker": "demo-processor-worker",
            },
            {
                component: promote_release.GO_COMPONENT_APP[component]
                for component in (
                    "go-match-discovery-worker",
                    "go-demo-downloader-worker",
                    "go-demo-processor-worker",
                )
            },
        )

    def test_go_components_must_share_one_source(self) -> None:
        manifest = self.manifest()
        manifest["components"]["go-demo-uploader"]["revision"] = "9" * 40

        with self.assertRaisesRegex(promote_release.PromotionError, "one revision"):
            promote_release.release_sources(manifest)

    def test_node_uses_its_own_immutable_source(self) -> None:
        sources = promote_release.release_sources(self.manifest())

        self.assertEqual(
            {"revision": "7" * 40, "artifact": "sha256:" + "8" * 64},
            sources["node"],
        )

    def test_go_preflight_builds_every_selected_command(self) -> None:
        manifest = self.manifest()
        for component in promote_release.GO_COMPONENT_ORDER:
            manifest["components"].setdefault(
                component,
                {"revision": "e" * 40, "artifact": "sha256:" + "f" * 64},
            )
        with mock.patch.object(promote_release, "_run") as run:
            promote_release._preflight_go_components(manifest)

        self.assertEqual(
            [
                f"./cmd/{promote_release.GO_COMPONENT_APP[component]}"
                for component in promote_release.GO_COMPONENT_ORDER
            ],
            [call.args[0][-1] for call in run.call_args_list],
        )
        for call in run.call_args_list:
            self.assertEqual(
                ["/opt/go/bin/go", "build", "-mod=readonly", "-o", "/dev/null"],
                call.args[0][:-1],
            )
            self.assertEqual(promote_release.DEPLOY_USER, call.kwargs["user"])
            self.assertEqual(promote_release.REPOSITORIES["go"], call.kwargs["cwd"])
            self.assertEqual({"CGO_ENABLED": "0"}, call.kwargs["env"])

    def test_main_preflights_after_source_preparation_and_before_deploy(self) -> None:
        order: list[str] = []
        manifest = self.manifest()
        source = {"revision": "e" * 40, "artifact": "sha256:" + "f" * 64}

        with (
            mock.patch.object(promote_release.os, "geteuid", return_value=0),
            mock.patch.object(promote_release.broker, "_read_trusted_json", return_value={}),
            mock.patch.object(promote_release.broker, "_validate_manifest", return_value=manifest),
            mock.patch.object(promote_release, "release_sources", return_value={"go": source}),
            mock.patch.object(
                promote_release,
                "_prepare_source",
                side_effect=lambda *_, **__: order.append("prepare"),
            ),
            mock.patch.object(
                promote_release,
                "_preflight_go_components",
                side_effect=lambda *_: order.append("preflight"),
            ),
            mock.patch.object(
                promote_release,
                "_deploy",
                side_effect=lambda *_: order.append("deploy"),
            ),
        ):
            result = promote_release.main(
                ["--manifest", "/tmp/release-test.json", "--release-id", "candidate"]
            )

        self.assertEqual(0, result)
        self.assertEqual(["prepare", "preflight", "deploy"], order)

    def test_committed_candidate_is_valid_and_has_baseline(self) -> None:
        payload = json.loads((RELEASE_DIR / "candidate.json").read_text(encoding="utf-8"))

        manifest = broker._validate_manifest(payload, "candidate")

        queue_workers = {
            "go-match-discovery-worker",
            "go-demo-downloader-worker",
            "go-demo-processor-worker",
        }
        baseline_components = set(release_protocol.COMPONENTS) - queue_workers
        self.assertEqual(baseline_components, set(manifest["components"]))
        self.assertTrue(queue_workers.isdisjoint(manifest["components"]))
        self.assertEqual(set(release_protocol.DATABASES), set(manifest["migrations"]))
        self.assertEqual(
            [
                "migration:mysql",
                "migration:clickhouse",
                "component:php",
                "component:node",
                "component:go-match-updater",
                "component:go-demo-uploader",
                "component:go-highlight-extractor",
                "component:go-replay-converter",
                "component:go-stats-extractor",
                "component:frontend",
            ],
            promote_release.deployment_order(manifest),
        )

    def test_persistent_broker_keeps_strict_process_sandbox(self) -> None:
        unit = (RELEASE_DIR.parent / "systemd" / "getreplay-release-broker.service").read_text(
            encoding="utf-8"
        )

        self.assertIn("NoNewPrivileges=yes", unit)
        self.assertIn("MemoryDenyWriteExecute=yes", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("ProtectHome=yes", unit)

    def test_executor_is_short_lived_and_path_restricted(self) -> None:
        command = broker._adapter_command(
            broker.BrokerConfig(),
            "/usr/local/libexec/getreplay-release/adapters/promote-release",
            Path("/var/lib/getreplay-release/state/executions/candidate.json"),
            "candidate",
        )

        self.assertEqual("/usr/bin/systemd-run", command[0])
        self.assertIn("--wait", command)
        self.assertIn("--collect", command)
        self.assertIn("--property=UMask=0022", command)
        self.assertIn("--property=ProtectSystem=strict", command)
        self.assertIn("-/home/solo/getreplay-node-releases", " ".join(command))
        self.assertIn("-/etc/systemd/system/node-app.service", " ".join(command))
        self.assertNotIn("--property=NoNewPrivileges=no", command)
        self.assertEqual("candidate", command[-1])


class ReleaseInfrastructureHardeningTest(unittest.TestCase):
    def test_node_deploy_is_atomic_health_gated_and_rollback_capable(self) -> None:
        source = (RELEASE_DIR.parent / "node" / "deploy.sh").read_text(encoding="utf-8")

        self.assertIn("getreplay-node-releases", source)
        self.assertIn('mv -Tf "$link_candidate" "$CURRENT_LINK"', source)
        self.assertIn("productionSafetyRevision", source)
        self.assertIn("restoring previous Node release", source)
        self.assertIn('systemctl restart "$SERVICE"', source)

    def test_release_installer_registers_node_without_restarting_it(self) -> None:
        source = (RELEASE_DIR / "install-server.sh").read_text(encoding="utf-8")

        self.assertIn("/home/solo/getreplay-node/.git", source)
        self.assertIn("$DEPLOY_ROOT/node/deploy.sh", source)
        self.assertNotIn("systemctl restart node-app", source)


class GitHubWorkflowScopeTest(unittest.TestCase):
    def test_component_buttons_use_fixed_scope_environment_jobs(self) -> None:
        workflow_root = RELEASE_DIR.parent / ".github" / "workflows"
        expected = {
            "deploy-production.yml": "all",
            "deploy-frontend.yml": "frontend",
            "deploy-node.yml": "node",
            "deploy-php.yml": "php",
            "deploy-go.yml": "go",
            "deploy-migrations.yml": "migrations",
        }

        for filename, scope in expected.items():
            with self.subTest(workflow=filename):
                source = (workflow_root / filename).read_text(encoding="utf-8")
                self.assertIn("environment: production", source)
                self.assertIn("group: getreplay-production", source)
                self.assertIn(f"RELEASE_SCOPE: {scope}", source)
                self.assertIn("secrets.PRODUCTION_RELEASE_SSH_KEY", source)
                self.assertIn("secrets.PRODUCTION_RELEASE_KNOWN_HOSTS", source)
                self.assertIn("release/run-production-scope.sh?ref=$GITHUB_SHA", source)
                self.assertNotIn("uses: ./.github/workflows/", source)
                self.assertNotIn("workflow_dispatch:\n    inputs:", source)

    def test_reviewed_runner_keeps_scope_selection_and_release_protocol(self) -> None:
        source = (RELEASE_DIR / "run-production-scope.sh").read_text(encoding="utf-8")

        self.assertIn("release/select_scope.py", source)
        self.assertIn("getreplay-release preview promote", source)
        self.assertIn("StrictHostKeyChecking=yes", source)
        self.assertIn("required environment value is empty", source)


class MigrationDeployTest(unittest.TestCase):
    def test_prepared_revision_does_not_require_a_branch_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "migrations"
            repository.mkdir()
            subprocess.run(
                ["git", "init", "--initial-branch=master", str(repository)],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "-C", str(repository), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repository), "config", "user.name", "Release test"],
                check=True,
            )
            (repository / "migration.sql").write_text("SELECT 1;\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "migration.sql"], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "commit", "-m", "test migration"],
                check=True,
                capture_output=True,
                text=True,
            )
            revision = subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            completed = subprocess.run(
                [
                    "bash",
                    "-c",
                    'source "$1"; MIGRATIONS_DIR="$2"; MIGRATIONS_BRANCH=main; '
                    'REVISION="$3"; SOURCE_PREPARED=true; update_migrations',
                    "migration-test",
                    str(RELEASE_DIR.parent / "migrations" / "common.sh"),
                    str(repository),
                    revision,
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertIn(f"Using prepared migrations revision {revision}", completed.stdout)


class NodeDeployIntegrationTest(unittest.TestCase):
    def write_executable(self, path: Path, source: str) -> None:
        path.write_text(source, encoding="utf-8")
        path.chmod(0o755)

    def prepare(self, root: Path) -> tuple[dict[str, str], Path, Path, Path, str]:
        origin = root / "origin.git"
        source = root / "source"
        subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
        subprocess.run(["git", "clone", str(origin), str(source)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.name", "Release test"], check=True)
        subprocess.run(["git", "-C", str(source), "checkout", "-b", "main"], check=True, capture_output=True)
        (source / "package.json").write_text('{"name":"node-release-test","version":"1.0.0"}\n')
        (source / "package-lock.json").write_text(
            '{"name":"node-release-test","version":"1.0.0","lockfileVersion":3,"packages":{}}\n'
        )
        subprocess.run(["git", "-C", str(source), "add", "package.json", "package-lock.json"], check=True)
        subprocess.run(["git", "-C", str(source), "commit", "-m", "old"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(source), "push", "-u", "origin", "main"], check=True, capture_output=True)
        old_revision = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        (source / "index.js").write_text("// production safety revision 2\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(source), "add", "index.js"], check=True)
        subprocess.run(["git", "-C", str(source), "commit", "-m", "new"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(source), "push", "origin", "main"], check=True, capture_output=True)
        revision = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(["git", "-C", str(source), "reset", "--hard", old_revision], check=True, capture_output=True)
        (source / ".env").write_text("TEST_ONLY=1\n", encoding="utf-8")

        release_root = root / "releases"
        release_root.mkdir()
        current = release_root / "current"
        current.symlink_to(source)
        unit_source = root / "node-app.service"
        unit_source.write_text("new unit\n", encoding="utf-8")
        unit_directory = root / "systemd"
        unit_directory.mkdir()
        unit_target = unit_directory / "node-app.service"
        unit_target.write_text("old unit\n", encoding="utf-8")

        fake_bin = root / "bin"
        fake_bin.mkdir()
        self.write_executable(
            fake_bin / "sudo",
            '#!/bin/sh\nwhile [ "$1" != "--" ]; do shift; done\nshift\nexec "$@"\n',
        )
        for command in ("node", "npm", "chown"):
            self.write_executable(fake_bin / command, "#!/bin/sh\nexit 0\n")
        self.write_executable(
            fake_bin / "systemctl",
            '#!/bin/sh\nprintf "%s\\n" "$*" >> "$FAKE_SYSTEMCTL_LOG"\nexit 0\n',
        )
        self.write_executable(
            fake_bin / "curl",
            '#!/bin/sh\n[ "$FAKE_HEALTH" = ok ] || exit 22\nprintf \'%s\\n\' \'{"status":"ok","steamConnected":true,"capabilities":{"serializedMatchListRequests":true,"lateResponseQuarantine":true,"matchListTimeoutSessionRecovery":true,"productionSafetyRevision":2},"gcRequestQueue":{"mode":"serial","pending":0,"maxPending":32,"quarantined":false}}\'\n',
        )
        self.write_executable(
            fake_bin / "readlink",
            '#!/bin/sh\n[ "$1" = -f ] && shift\npython3 -c \'import os,sys; print(os.path.realpath(sys.argv[1]))\' "$1"\n',
        )
        self.write_executable(
            fake_bin / "mv",
            '#!/bin/sh\nif [ "$1" = -Tf ]; then shift; python3 -c \'import os,sys; os.replace(sys.argv[1],sys.argv[2])\' "$1" "$2"; else exec /bin/mv "$@"; fi\n',
        )

        environment = {
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "SRC": str(source),
            "RELEASE_ROOT": str(release_root),
            "CURRENT_LINK": str(current),
            "REVISION": revision,
            "SOURCE_PREPARED": "true",
            "BUILD_USER": subprocess.run(
                ["id", "-un"], check=True, capture_output=True, text=True
            ).stdout.strip(),
            "NODE_BIN": str(fake_bin),
            "UNIT_SOURCE": str(unit_source),
            "UNIT_TARGET": str(unit_target),
            "HEALTH_URL": "http://127.0.0.1:3012/health",
            "HEALTH_ATTEMPTS": "1",
            "HEALTH_INTERVAL_SECONDS": "0",
            "FAKE_SYSTEMCTL_LOG": str(root / "systemctl.log"),
        }
        return environment, current, unit_target, source, revision

    def test_success_switches_to_immutable_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, current, unit_target, _, revision = self.prepare(Path(temporary))
            environment["FAKE_HEALTH"] = "ok"
            unit_target.parent.chmod(0o555)

            try:
                completed = subprocess.run(
                    [str(RELEASE_DIR.parent / "node" / "deploy.sh")],
                    env=environment,
                    capture_output=True,
                    text=True,
                )
            finally:
                unit_target.parent.chmod(0o755)

            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(revision, current.resolve().name)
            self.assertEqual("new unit\n", unit_target.read_text(encoding="utf-8"))

    def test_failed_health_restores_previous_release_and_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, current, unit_target, source, _ = self.prepare(Path(temporary))
            environment["FAKE_HEALTH"] = "fail"
            unit_target.parent.chmod(0o555)

            try:
                completed = subprocess.run(
                    [str(RELEASE_DIR.parent / "node" / "deploy.sh")],
                    env=environment,
                    capture_output=True,
                    text=True,
                )
            finally:
                unit_target.parent.chmod(0o755)

            self.assertNotEqual(0, completed.returncode)
            self.assertEqual(source.resolve(), current.resolve())
            self.assertEqual("old unit\n", unit_target.read_text(encoding="utf-8"))


class DeploymentCheckoutOwnerTest(unittest.TestCase):
    def test_deploy_scripts_run_git_as_the_build_user(self) -> None:
        scripts = {
            "go": RELEASE_DIR.parent / "go" / "deploy.sh",
            "php": RELEASE_DIR.parent / "php" / "deploy.sh",
            "frontend": RELEASE_DIR.parent / "frontend" / "deploy.sh",
            "node": RELEASE_DIR.parent / "node" / "deploy.sh",
        }

        for name, path in scripts.items():
            with self.subTest(script=name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("run_build git", source)
                self.assertNotIn("$(git ", source)
                self.assertNotRegex(source, r"(?m)^\s+git (?:-C |fetch|reset|merge)")


class ReleaseScopeSelectionTest(unittest.TestCase):
    def candidate(self) -> dict[str, object]:
        return {
            "version": 1,
            "release_id": "candidate",
            "components": {
                "frontend": {"revision": "a" * 40, "artifact": "sha256:" + "b" * 64},
                "node": {"revision": "c" * 40, "artifact": "sha256:" + "d" * 64},
                "php": {"revision": "e" * 40, "artifact": "sha256:" + "f" * 64},
                "go-match-updater": {
                    "revision": "1" * 40,
                    "artifact": "sha256:" + "2" * 64,
                },
            },
            "migrations": {
                "mysql": {
                    "revision": "3" * 40,
                    "artifact": "sha256:" + "4" * 64,
                    "migration": "mysql-release",
                }
            },
        }

    def test_single_component_scope_cannot_include_other_components(self) -> None:
        selected = select_scope.select_scope(self.candidate(), "node", "candidate-node")

        self.assertEqual("candidate-node", selected["release_id"])
        self.assertEqual({"node"}, set(selected["components"]))
        self.assertEqual({}, selected["migrations"])

    def test_go_scope_selects_only_go_components(self) -> None:
        selected = select_scope.select_scope(self.candidate(), "go", "candidate-go")

        self.assertEqual({"go-match-updater"}, set(selected["components"]))
        self.assertEqual({}, selected["migrations"])

    def test_missing_or_empty_scope_fails_closed(self) -> None:
        candidate = self.candidate()
        del candidate["components"]["node"]
        with self.assertRaisesRegex(select_scope.ScopeError, "does not contain"):
            select_scope.select_scope(candidate, "node", "candidate-node")

        candidate["components"] = {
            name: value
            for name, value in candidate["components"].items()
            if not name.startswith("go-")
        }
        with self.assertRaisesRegex(select_scope.ScopeError, "empty"):
            select_scope.select_scope(candidate, "go", "candidate-go")


if __name__ == "__main__":
    unittest.main()
