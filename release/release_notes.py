#!/usr/bin/env python3
"""Build reviewed commit notes and render them for a selected release manifest."""

from __future__ import annotations

import argparse
import copy
import html
import json
from pathlib import Path
import re
import subprocess
from typing import Sequence

from select_scope import SCOPES, ScopeError, select_scope


MAX_COMMITS = 50
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORIES = {
    "frontend": "soloser/getreplay-front",
    "php": "soloser/getreplay-php",
    "node": "soloser/getreplay-node",
    "go": "soloser/getreplay-go",
    "migrations": "soloser/getreplay-migrations",
}


def empty_notes() -> dict:
    return {"version": 1, "components": {}, "migrations": {}}


def git(source: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(source), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()


def commit_notes(source: Path, entry: dict, previous: str | None) -> dict:
    revision = entry["revision"]
    if not SHA_RE.fullmatch(revision) or (previous is not None and not SHA_RE.fullmatch(previous)):
        raise ValueError("commit notes require full lowercase commit SHAs")
    subject = git(source, "show", "-s", "--format=%s", revision)
    commits = []
    total = 0
    if previous is not None:
        git(source, "merge-base", "--is-ancestor", previous, revision)
        revision_range = f"{previous}..{revision}"
        total = int(git(source, "rev-list", "--count", revision_range))
        history = git(
            source, "log", "--topo-order", f"--max-count={MAX_COMMITS}",
            "--format=%H%x09%s", revision_range,
        )
        for line in history.splitlines():
            sha, _, title = line.partition("\t")
            commits.append({"revision": sha, "subject": title})
    return {
        "revision": revision,
        "artifact": entry["artifact"],
        "subject": subject,
        "previous_revision": previous,
        "total_commits": total,
        "commits": commits,
    }


def update_notes(previous: dict, selected: dict, notes: dict, source: Path) -> dict:
    """Refresh selected entries only, retaining unrelated reviewed notes."""
    if notes.get("version") != 1:
        raise ValueError("unsupported commit notes version")
    result = copy.deepcopy(notes)
    cache = {}
    for section in ("components", "migrations"):
        for name, entry in selected[section].items():
            base = previous[section].get(name, {}).get("revision")
            key = (entry["revision"], entry["artifact"], base)
            if key not in cache:
                cache[key] = commit_notes(source, entry, base)
            result[section][name] = copy.deepcopy(cache[key])
        if section == "migrations" and selected[section]:
            result[section] = {name: result[section][name] for name in selected[section]}
    return result


def markdown_text(value: str) -> str:
    # Subjects are data, including Markdown/HTML, control characters and @mentions.
    single_line = " ".join("".join(c if c.isprintable() else " " for c in value).split())
    escaped = html.escape(single_line, quote=False).replace("@", "&#64;")
    return re.sub(r"([\\`*_\[\]|])", r"\\\1", escaped)


def render_notes(selected: dict, notes: dict) -> str:
    """Show only notes matching the exact selected revision and archive digest."""
    lines = ["## Selected release commits", ""]
    groups = {}
    for section in ("migrations", "components"):
        for name, entry in selected[section].items():
            scope = "migrations" if section == "migrations" else "go" if name.startswith("go-") else name
            note = notes.get(section, {}).get(name) if notes.get("version") == 1 else None
            if not isinstance(note, dict) or any(note.get(key) != entry[key] for key in ("revision", "artifact")):
                note = None
            # Group services sharing both the source and the reviewed comparison.
            key = (scope, entry["revision"], entry["artifact"], json.dumps(note, sort_keys=True))
            if key not in groups:
                groups[key] = ([], entry, note)
            groups[key][0].append(name)
    for (scope, revision, _, _), (names, entry, note) in groups.items():
        if scope not in REPOSITORIES or not SHA_RE.fullmatch(revision):
            raise ValueError("invalid source in selected release")
        url = "https://github.com/" + REPOSITORIES[scope]
        lines.extend(["### " + ", ".join(markdown_text(name) for name in names), ""])
        subject = " — " + markdown_text(note["subject"]) if note else ""
        lines.append(f"- Revision: [{revision[:12]}]({url}/commit/{revision}){subject}")
        if note is None:
            lines.extend(["- Commit messages unavailable for this exact revision/digest. Prepare a new candidate to refresh them.", ""])
            continue
        base = note["previous_revision"]
        if base is None:
            lines.extend(["- Previous candidate revision unavailable; showing the pinned commit only.", ""])
            continue
        if not SHA_RE.fullmatch(base):
            raise ValueError("invalid previous revision in commit notes")
        total = note["total_commits"]
        lines.extend([
            f"- Changes since previous candidate [{base[:12]}]({url}/commit/{base}): {total} commit(s).",
            "- This comparison is against the previous candidate, not the running production version.",
            "",
        ])
        for commit in note["commits"]:
            sha = commit["revision"]
            if not SHA_RE.fullmatch(sha):
                raise ValueError("invalid revision in commit notes")
            lines.append(f"- [{sha[:12]}]({url}/commit/{sha}) — {markdown_text(commit['subject'])}")
        if total > len(note["commits"]):
            lines.append(f"- Showing the newest {len(note['commits'])} of {total} commits.")
        if total:
            lines.append(f"- [Full comparison]({url}/compare/{base}...{revision})")
        lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    update = commands.add_parser("update")
    update.add_argument("--previous", type=Path, required=True)
    update.add_argument("--candidate", type=Path, required=True)
    update.add_argument("--scope", choices=SCOPES[1:], required=True)
    update.add_argument("--source", type=Path, required=True)
    update.add_argument("--notes", type=Path, required=True)
    update.add_argument("--markdown", type=Path, required=True)
    render = commands.add_parser("render")
    render.add_argument("--candidate", type=Path, required=True)
    render.add_argument("--notes", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
        notes = json.loads(args.notes.read_text(encoding="utf-8"))
        if args.command == "update":
            previous = json.loads(args.previous.read_text(encoding="utf-8"))
            selected = select_scope(candidate, args.scope, "candidate")
            notes = update_notes(previous, selected, notes, args.source)
            rendered = render_notes(selected, notes)
            args.notes.write_text(json.dumps(notes, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            args.markdown.write_text(rendered, encoding="utf-8")
        else:
            print(render_notes(candidate, notes))
    except (OSError, ValueError, KeyError, TypeError, ScopeError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
