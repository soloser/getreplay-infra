from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest


RELEASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE_DIR))

import broker  # noqa: E402
import release_protocol  # noqa: E402


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

    def test_shell_and_path_injection_are_rejected(self) -> None:
        commands = (
            "bash",
            "getreplay-release status frontend",
            "getreplay-release status; id",
            "getreplay-release deploy frontend ../../etc/passwd",
            "getreplay-release migrate mysql release;id",
            "getreplay-release migrate production release-1",
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


if __name__ == "__main__":
    unittest.main()
