from __future__ import annotations

import json
import io
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout


RELEASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE_DIR))

import prepare_candidate  # noqa: E402


class PrepareCandidateTest(unittest.TestCase):
    def candidate(self) -> dict[str, object]:
        return {
            "version": 1,
            "release_id": "candidate",
            "components": {
                "frontend": {"revision": "1" * 40, "artifact": "sha256:" + "2" * 64},
                "php": {"revision": "3" * 40, "artifact": "sha256:" + "4" * 64},
                "node": {"revision": "5" * 40, "artifact": "sha256:" + "6" * 64},
                "go-match-updater": {
                    "revision": "7" * 40,
                    "artifact": "sha256:" + "8" * 64,
                },
                "go-demo-uploader": {
                    "revision": "7" * 40,
                    "artifact": "sha256:" + "8" * 64,
                },
            },
            "migrations": {},
        }

    def test_single_component_update_preserves_every_other_entry(self) -> None:
        source = self.candidate()
        updated, names = prepare_candidate.update_candidate(
            source,
            "frontend",
            "a" * 40,
            "sha256:" + "b" * 64,
        )

        self.assertEqual(("frontend",), names)
        self.assertEqual(
            {"revision": "a" * 40, "artifact": "sha256:" + "b" * 64},
            updated["components"]["frontend"],
        )
        self.assertEqual(source["components"]["php"], updated["components"]["php"])
        self.assertEqual("1" * 40, source["components"]["frontend"]["revision"])

    def test_go_update_changes_only_go_components_already_in_candidate(self) -> None:
        updated, names = prepare_candidate.update_candidate(
            self.candidate(),
            "go",
            "a" * 40,
            "sha256:" + "b" * 64,
        )

        self.assertEqual(("go-match-updater", "go-demo-uploader"), names)
        for name in names:
            self.assertEqual("a" * 40, updated["components"][name]["revision"])
        self.assertNotIn("go-match-discovery-worker", updated["components"])
        self.assertEqual("1" * 40, updated["components"]["frontend"]["revision"])

    def test_migration_update_can_select_both_databases(self) -> None:
        source = self.candidate()
        updated, names = prepare_candidate.update_candidate(
            source,
            "migrations",
            "a" * 40,
            "sha256:" + "b" * 64,
            "both",
        )

        self.assertEqual(("mysql", "clickhouse"), names)
        self.assertEqual(source["components"], updated["components"])
        for database in names:
            self.assertEqual(
                {
                    "revision": "a" * 40,
                    "artifact": "sha256:" + "b" * 64,
                    "migration": f"{database}-{'a' * 12}",
                },
                updated["migrations"][database],
            )

    def test_single_database_replaces_old_migration_scope(self) -> None:
        source = self.candidate()
        source["migrations"] = {
            "clickhouse": {
                "revision": "9" * 40,
                "artifact": "sha256:" + "8" * 64,
                "migration": "clickhouse-999999999999",
            }
        }
        updated, names = prepare_candidate.update_candidate(
            source,
            "migrations",
            "a" * 40,
            "sha256:" + "b" * 64,
            "mysql",
        )

        self.assertEqual(("mysql",), names)
        self.assertEqual(("mysql",), tuple(updated["migrations"]))

    def test_invalid_revision_digest_scope_and_manifest_fail_closed(self) -> None:
        cases = (
            ("frontend", "main", "sha256:" + "b" * 64, self.candidate()),
            ("frontend", "a" * 40, "b" * 64, self.candidate()),
            ("frontend", "a" * 40, "sha256:" + "b" * 64, {"version": 1}),
        )
        for scope, revision, artifact, payload in cases:
            with self.subTest(scope=scope, revision=revision, artifact=artifact):
                with self.assertRaises(prepare_candidate.CandidateError):
                    prepare_candidate.update_candidate(payload, scope, revision, artifact)

        with self.assertRaises(prepare_candidate.CandidateError):
            prepare_candidate.update_candidate(
                self.candidate(),
                "migrations",
                "a" * 40,
                "sha256:" + "b" * 64,
            )
        with self.assertRaises(prepare_candidate.CandidateError):
            prepare_candidate.update_candidate(
                self.candidate(),
                "frontend",
                "a" * 40,
                "sha256:" + "b" * 64,
                "mysql",
            )

    def test_cli_writes_stable_json_and_machine_readable_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "candidate.json"
            output = root / "updated.json"
            source.write_text(json.dumps(self.candidate()), encoding="utf-8")

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = prepare_candidate.main(
                    [
                        "--input",
                        str(source),
                        "--output",
                        str(output),
                        "--scope",
                        "node",
                        "--revision",
                        "a" * 40,
                        "--artifact",
                        "sha256:" + "b" * 64,
                    ]
                )

            self.assertEqual(0, result)
            self.assertTrue(output.read_text(encoding="utf-8").endswith("\n"))
            self.assertEqual("a" * 40, json.loads(output.read_text())["components"]["node"]["revision"])
            summary = json.loads(stdout.getvalue())
            self.assertEqual(["5" * 40], summary["previous_revisions"])

    def test_migration_cli_reports_previous_selected_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "candidate.json"
            output = root / "updated.json"
            candidate = self.candidate()
            candidate["migrations"] = {
                "mysql": {
                    "revision": "9" * 40,
                    "artifact": "sha256:" + "8" * 64,
                    "migration": "mysql-999999999999",
                }
            }
            source.write_text(json.dumps(candidate), encoding="utf-8")

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = prepare_candidate.main(
                    [
                        "--input",
                        str(source),
                        "--output",
                        str(output),
                        "--scope",
                        "migrations",
                        "--database",
                        "mysql",
                        "--revision",
                        "a" * 40,
                        "--artifact",
                        "sha256:" + "b" * 64,
                    ]
                )

            self.assertEqual(0, result)
            self.assertEqual(["9" * 40], json.loads(stdout.getvalue())["previous_revisions"])


if __name__ == "__main__":
    unittest.main()
