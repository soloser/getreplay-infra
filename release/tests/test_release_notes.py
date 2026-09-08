from __future__ import annotations

import base64
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


RELEASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE_DIR))

import prepare_candidate  # noqa: E402
import release_notes  # noqa: E402
import select_scope  # noqa: E402


class ReleaseNotesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.git("init", "--initial-branch=main")
        self.base = self.commit("Previous release")
        self.feature = self.commit("Исправить составы команд")
        self.head = self.commit("Show player names")
        self.artifact = "sha256:" + "b" * 64

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.source), "-c", "user.name=Release Test",
             "-c", "user.email=release@example.test", "-c", "commit.gpgsign=false", *args],
            check=True, capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"},
        ).stdout.strip()

    def commit(self, subject: str) -> str:
        self.git("commit", "--allow-empty", "-m", subject)
        return self.git("rev-parse", "HEAD")

    def entry(self, revision: str | None = None) -> dict:
        return {"revision": revision or self.head, "artifact": self.artifact}

    def candidate(self, revision: str | None = None) -> dict:
        return {
            "version": 1, "release_id": "candidate",
            "components": {name: self.entry(revision) for name in
                           ("frontend", "php", "go-match-updater", "go-demo-uploader")},
            "migrations": {},
        }

    def test_history_includes_changes_between_pins_but_excludes_baseline(self) -> None:
        note = release_notes.commit_notes(self.source, self.entry(), self.base)
        self.assertEqual("Show player names", note["subject"])
        self.assertEqual(2, note["total_commits"])
        self.assertEqual([self.head, self.feature], [c["revision"] for c in note["commits"]])
        self.assertEqual("Исправить составы команд", note["commits"][1]["subject"])

    def test_merge_history_includes_branch_commits_and_merge(self) -> None:
        self.git("checkout", "-b", "feature", self.base)
        side = self.commit("Fix sidebar from feature branch")
        self.git("checkout", "main")
        self.git("merge", "--no-ff", "feature", "-m", "Merge feature")
        head = self.git("rev-parse", "HEAD")
        note = release_notes.commit_notes(self.source, self.entry(head), self.base)
        self.assertEqual({self.feature, self.head, side, head}, {c["revision"] for c in note["commits"]})

    def test_missing_baseline_and_unchanged_pin_are_explicit(self) -> None:
        selected = select_scope.select_scope(self.candidate(), "frontend", "candidate-frontend")
        notes = release_notes.empty_notes()
        for base, expected in ((None, "pinned commit only"), (self.head, "0 commit(s)")):
            with self.subTest(base=base):
                notes["components"]["frontend"] = release_notes.commit_notes(self.source, self.entry(), base)
                rendered = release_notes.render_notes(selected, notes)
                self.assertIn("Show player names", rendered)
                self.assertIn(expected, rendered)

    def test_rollback_or_nonancestor_cannot_generate_notes(self) -> None:
        with self.assertRaises(subprocess.CalledProcessError):
            release_notes.commit_notes(self.source, self.entry(self.base), self.head)
        self.git("checkout", "-b", "other", self.base)
        other = self.commit("Unmerged change")
        with self.assertRaises(subprocess.CalledProcessError):
            release_notes.commit_notes(self.source, self.entry(other), self.head)
        with self.assertRaises(ValueError):
            release_notes.commit_notes(self.source, self.entry("--all"), self.base)

    def test_long_history_has_count_limit_and_full_compare_link(self) -> None:
        for index in range(release_notes.MAX_COMMITS):
            head = self.commit(f"Change {index}")
        notes = release_notes.empty_notes()
        notes["components"]["frontend"] = release_notes.commit_notes(self.source, self.entry(head), self.base)
        selected = select_scope.select_scope(self.candidate(head), "frontend", "candidate-frontend")
        rendered = release_notes.render_notes(selected, notes)
        self.assertEqual(50, len(notes["components"]["frontend"]["commits"]))
        self.assertIn("Showing the newest 50 of 52 commits", rendered)
        self.assertIn(f"/compare/{self.base}...{head}", rendered)

    def test_update_preserves_unrelated_notes_and_groups_go_services(self) -> None:
        previous = self.candidate(self.base)
        selected = select_scope.select_scope(self.candidate(), "go", "candidate-go")
        notes = release_notes.empty_notes()
        notes["components"]["php"] = {"subject": "Unrelated PHP notes"}
        updated = release_notes.update_notes(previous, selected, notes, self.source)
        self.assertEqual(notes["components"]["php"], updated["components"]["php"])
        self.assertNotIn("go-match-updater", notes["components"])
        rendered = release_notes.render_notes(selected, updated)
        self.assertIn("go-match-updater, go-demo-uploader", rendered)
        self.assertEqual(1, rendered.count("Исправить составы команд"))
        self.assertNotIn("Unrelated PHP notes", rendered)
        self.assertIn("previous candidate, not the running production version", rendered)

    def test_separate_baselines_are_retained_for_migrations(self) -> None:
        previous = self.candidate(self.base)
        previous["migrations"]["mysql"] = self.entry(self.base)
        selected = {"components": {}, "migrations": {"mysql": self.entry(), "clickhouse": self.entry()}}
        notes = release_notes.update_notes(previous, selected, release_notes.empty_notes(), self.source)
        self.assertEqual(self.base, notes["migrations"]["mysql"]["previous_revision"])
        self.assertIsNone(notes["migrations"]["clickhouse"]["previous_revision"])
        selected["migrations"].pop("mysql")
        refreshed = release_notes.update_notes(previous, selected, notes, self.source)
        self.assertEqual({"clickhouse"}, set(refreshed["migrations"]))

    def test_stale_revision_or_digest_never_displays_wrong_description(self) -> None:
        selected = select_scope.select_scope(self.candidate(), "frontend", "candidate-frontend")
        for field, value in (("revision", self.base), ("artifact", "sha256:" + "c" * 64)):
            with self.subTest(field=field):
                notes = release_notes.empty_notes()
                note = release_notes.commit_notes(self.source, self.entry(), self.base)
                note[field] = value
                notes["components"]["frontend"] = note
                rendered = release_notes.render_notes(selected, notes)
                self.assertIn("Commit messages unavailable", rendered)
                self.assertIn(self.head, rendered)
                self.assertNotIn("Show player names", rendered)
        self.assertIn("Commit messages unavailable", release_notes.render_notes(selected, release_notes.empty_notes()))

    def test_subjects_are_display_data_not_markdown_html_or_workflow_commands(self) -> None:
        subject = 'Fix [link](https://example.test) <b>html</b> `code` @team | *bold* $(touch marker)'
        head = self.commit(subject)
        note = release_notes.commit_notes(self.source, self.entry(head), self.base)
        self.assertEqual(subject, note["subject"])
        # Also verify stored notes with newlines/control characters cannot create log commands.
        note["subject"] += "\n::warning::injected\x1b[31m"
        notes = release_notes.empty_notes()
        notes["components"]["frontend"] = note
        selected = select_scope.select_scope(self.candidate(head), "frontend", "candidate-frontend")
        rendered = release_notes.render_notes(selected, notes)
        self.assertNotIn("<b>", rendered)
        self.assertNotIn("@team", rendered)
        self.assertNotIn("\n::warning::", rendered)
        self.assertNotIn("\x1b", rendered)
        self.assertIn(r"\[link\]", rendered)
        self.assertIn("&lt;b&gt;html&lt;/b&gt;", rendered)
        self.assertFalse((self.source / "marker").exists())

    def test_prepare_update_and_render_cli_round_trip(self) -> None:
        previous = self.candidate(self.base)
        updated, _ = prepare_candidate.update_candidate(previous, "frontend", self.head, self.artifact)
        for filename, payload in (("previous.json", previous), ("candidate.json", updated), ("notes.json", release_notes.empty_notes())):
            (self.root / filename).write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(0, release_notes.main([
            "update", "--previous", str(self.root / "previous.json"),
            "--candidate", str(self.root / "candidate.json"), "--scope", "frontend",
            "--source", str(self.source), "--notes", str(self.root / "notes.json"),
            "--markdown", str(self.root / "commits.md"),
        ]))
        notes = json.loads((self.root / "notes.json").read_text())
        self.assertEqual({"frontend"}, set(notes["components"]))
        selected = select_scope.select_scope(updated, "frontend", "candidate-frontend")
        (self.root / "selected.json").write_text(json.dumps(selected))
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(0, release_notes.main([
                "render", "--candidate", str(self.root / "selected.json"),
                "--notes", str(self.root / "notes.json"),
            ]))
        self.assertEqual((self.root / "commits.md").read_text().strip(), stdout.getvalue().strip())


class DeploymentNotesTest(unittest.TestCase):
    def test_runner_shows_only_selected_notes_before_any_release_command(self) -> None:
        if shutil.which("jq") is None:
            self.skipTest("runner requires jq")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "bin"
            binary.mkdir()
            (binary / "python3").symlink_to(sys.executable)
            fixture = root / "fixtures"
            fixture.mkdir()
            for name in ("select_scope.py", "release_notes.py", "candidate.json", "candidate-notes.json"):
                shutil.copyfile(RELEASE_DIR / name, fixture / name)
            candidate = json.loads((fixture / "candidate.json").read_text())
            notes = release_notes.empty_notes()
            for name, entry in candidate["components"].items():
                notes["components"][name] = {
                    **entry, "subject": "Selected frontend change" if name == "frontend" else "Unselected source change",
                    "previous_revision": None, "total_commits": 0, "commits": [],
                }
            (fixture / "candidate-notes.json").write_text(json.dumps(notes))
            scripts = {
                "curl": '''import base64, json, os, pathlib, sys
url = sys.argv[-1]
assert url.endswith("?ref=" + "a" * 40)
name = url.split("/contents/release/", 1)[1].split("?", 1)[0]
assert name in {"select_scope.py", "release_notes.py", "candidate.json", "candidate-notes.json"}
payload = (pathlib.Path(os.environ["RELEASE_TEST_ROOT"]) / "fixtures" / name).read_bytes()
print(json.dumps({"content": base64.b64encode(payload).decode()}))
''',
                "ssh": '''import os, pathlib, sys
root = pathlib.Path(os.environ["RELEASE_TEST_ROOT"])
summary = (root / "summary.md").read_text()
assert "Selected frontend change" in summary
assert "Unselected source change" not in summary
with (root / "ssh.jsonl").open("a") as output:
    import json
    output.write(json.dumps(sys.argv[-1]) + "\\n")
''',
            }
            for name, script in scripts.items():
                (binary / name).write_text("#!/usr/bin/env python3\n" + script)
                (binary / name).chmod(0o700)
            result = subprocess.run(
                ["bash", str(RELEASE_DIR / "run-production-scope.sh")],
                capture_output=True, text=True,
                env={
                    "PATH": f"{binary}:/usr/bin:/bin", "RUNNER_TEMP": str(root),
                    "GITHUB_API_URL": "https://api.example.test", "GITHUB_REPOSITORY": "example/infra",
                    "GITHUB_SHA": "a" * 40, "GITHUB_TOKEN": "test-token",
                    "GITHUB_STEP_SUMMARY": str(root / "summary.md"),
                    "RELEASE_HOST": "release.example.test", "RELEASE_PORT": "22",
                    "RELEASE_USER": "release-test", "RELEASE_KEY": "test-key",
                    "RELEASE_KNOWN_HOSTS": "test-known-hosts", "RELEASE_SCOPE": "frontend",
                    "RELEASE_TEST_ROOT": str(root),
                },
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("Selected frontend change", result.stdout)
            self.assertNotIn("Unselected source change", result.stdout)
            commands = [json.loads(line) for line in (root / "ssh.jsonl").read_text().splitlines()]
            self.assertEqual(3, len(commands))
            staged = json.loads(base64.b64decode(commands[0].split()[-1]))
            self.assertEqual({"frontend"}, set(staged["components"]))
            self.assertEqual(candidate["components"]["frontend"], staged["components"]["frontend"])
            self.assertEqual("getreplay-release preview promote candidate-frontend", commands[1])
            self.assertEqual("getreplay-release promote candidate-frontend", commands[2])
            self.assertEqual([], list(root.glob("getreplay-release-ssh.*")))


if __name__ == "__main__":
    unittest.main()
