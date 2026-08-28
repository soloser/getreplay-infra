from __future__ import annotations

from pathlib import Path
import sys
import unittest


RELEASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE_DIR))

import forced_command  # noqa: E402
import getreplay_release  # noqa: E402


class ReleaseValidationTest(unittest.TestCase):
    def test_preview_accepts_only_an_allowlisted_component_and_full_sha(self) -> None:
        revision = "a" * 40

        payload = getreplay_release.preview_payload("preview", "frontend", revision)

        self.assertEqual("frontend", payload["component"])
        self.assertEqual(revision, payload["revision"])
        self.assertFalse(payload["database_migrations"])
        self.assertFalse(payload["arbitrary_shell"])

    def test_revision_rejects_shell_metacharacters_and_short_hashes(self) -> None:
        for revision in ("abc123", "a" * 40 + ";id", "A" * 40):
            with self.subTest(revision=revision):
                with self.assertRaises(getreplay_release.ReleaseError):
                    getreplay_release.validate_revision(revision)

    def test_unknown_component_is_rejected(self) -> None:
        with self.assertRaises(getreplay_release.ReleaseError):
            getreplay_release.validate_component("migrations")

    def test_forced_command_accepts_only_the_release_program(self) -> None:
        revision = "1" * 40

        self.assertEqual(
            ["preview", "php", revision],
            forced_command.parse_original_command(f"getreplay-release preview php {revision}"),
        )

        for command in ("bash", "getreplay-release status; id", "git status"):
            with self.subTest(command=command):
                with self.assertRaises(getreplay_release.ReleaseError):
                    forced_command.parse_original_command(command)

    def test_forced_command_delegates_only_validated_arguments_to_the_deploy_user(self) -> None:
        revision = "3" * 40

        argv = forced_command.delegated_argv(["deploy", "frontend", revision])

        self.assertEqual(list(forced_command.DELEGATED_RUNNER), argv[:-3])
        self.assertEqual(["deploy", "frontend", revision], argv[-3:])

    def test_deploy_command_is_an_argument_vector_with_a_minimal_environment(self) -> None:
        command, environment = getreplay_release.command_for("go-match-updater", "2" * 40)

        self.assertEqual([str(getreplay_release.INFRA_ROOT / "go/deploy.sh"), "match-updater"], command)
        self.assertEqual("2" * 40, environment["REVISION"])
        self.assertEqual("main", environment["BRANCH"])
        self.assertNotIn("MYSQL_DSN", environment)
        self.assertNotIn("CH_STORAGE_CLICKHOUSE_DSN", environment)


if __name__ == "__main__":
    unittest.main()
