#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Make a skill's Azure DevOps writes through agent-cli, brief and marked.

Every write a skill makes goes through this script. It appends the footer
"Agent-written via /<skill>" to any text, rejects text over the length cap,
and offers no verb for closing, deleting, approving, or merging, because
those always need a person. The footer is italic on pull requests, which
store Markdown, and plain on work items, because agent-cli's Markdown to
HTML conversion has no italics.

Caps (characters, footer excluded): a comment 600, a description 2000.
--long lifts the cap, and is used only when the person asked for a longer
write. Pasted logs and code blocks count toward the cap for now.

Text comes from a positional argument, or from a file (--file); either one
given as - reads stdin. Pass --dry-run to see the agent-cli request without
making it.

Examples:
  post.py --skill dev-start comment 1234 'Plan: add the retry, red tests first.'
  post.py --skill dev-start update 1234 --state 'In Progress' --assignee @me
  post.py --skill dev-finish pr-create --repo web --source 1234-retry \\
      --title '1234 Retry on 429' --file pr.md --workitem 1234
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit("post.py needs Python 3.11 or later; run it with `uv run`.")

COMMENT_CAP = 600
DESCRIPTION_CAP = 2000
TITLE_CAP = 120
SKILL_RE = re.compile(r"^dev-[a-z0-9]+(-[a-z0-9]+)*$")
FOOTER_RE = re.compile(r"\n*_?Agent-written via /[a-z0-9-]+_?\s*$")
# States that close or remove an item. A person moves an item there.
# ADO's state categories that close or remove an item. A person moves an item there.
CLOSING_CATEGORIES = frozenset({"Resolved", "Completed", "Removed"})
EXIT_USAGE = 2
EXIT_SETUP = 3


class PostError(Exception):
    """A write that must not go out as asked."""

    def __init__(self, message: str, code: int = EXIT_USAGE) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Text:
    """Agent-written text with the footer applied and the cap checked."""

    body: str

    @classmethod
    def build(
        cls, raw: str, *, skill: str, cap: int, kind: str, long: bool, markdown: bool
    ) -> Text:
        text = FOOTER_RE.sub("", raw.strip()).rstrip()
        if not text:
            raise PostError(f"the {kind} is empty")
        if not long and len(text) > cap:
            raise PostError(
                f"the {kind} is {len(text)} characters and the cap is {cap}. "
                "Cut it down; pass --long only if the person asked for a longer write."
            )
        footer = f"Agent-written via /{skill}"
        return cls(f"{text}\n\n{f'_{footer}_' if markdown else footer}\n")


def read_text(value: str | None, file: Path | None) -> str:
    if file is not None and value is not None:
        raise PostError("give the text or --file, not both")
    if file == Path("-"):
        return sys.stdin.read()
    if file is not None:
        try:
            return file.read_text(encoding="utf-8")
        except OSError as err:
            raise PostError(f"cannot read {file}: {err.strerror}") from err
    if value == "-":
        return sys.stdin.read()
    if value is None:
        raise PostError("give the text, - for stdin, or --file")
    return value


def check_title(title: str) -> str:
    title = title.strip()
    if not title:
        raise PostError("the title is empty")
    if len(title) > TITLE_CAP:
        raise PostError(f"the title is {len(title)} characters and the cap is {TITLE_CAP}")
    return title


def agent_cli() -> str:
    found = os.environ.get("AGENT_CLI") or shutil.which("agent-cli")
    if not found:
        raise PostError("agent-cli is not on PATH; install it, or set AGENT_CLI", EXIT_SETUP)
    return found


def read(command: list[str]) -> dict[str, object]:
    done = subprocess.run(
        [agent_cli(), "ado", *command], capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise PostError(f"agent-cli ado {' '.join(command)} failed:\n{done.stderr.strip()}", 1)
    try:
        value = json.loads(done.stdout)
    except json.JSONDecodeError as err:
        raise PostError(f"agent-cli ado {' '.join(command)} printed no JSON: {err}", 1) from err
    if not isinstance(value, dict):
        raise PostError(f"agent-cli ado {' '.join(command)} printed no JSON object", 1)
    return value


def state_category(item_id: str, state: str) -> str:
    """The category (Proposed, InProgress, Completed ...) of a state for this item's type."""
    item_type = read(["workitem", "get", item_id, "--comments", "0", "--fields", "type"])["type"]
    info = read(["workitem-type", "get", str(item_type), "--fields", "states"])
    states = info.get("states")
    names = []
    for entry in states if isinstance(states, list) else []:
        if isinstance(entry, dict) and str(entry.get("name", "")).lower() == state.lower():
            return str(entry.get("category"))
        if isinstance(entry, dict):
            names.append(str(entry.get("name")))
    raise PostError(f"{item_type} has no state {state!r}; its states are {', '.join(names)}")


def run(command: list[str], *, dry_run: bool) -> int:
    full = [agent_cli(), "ado", *command, *(["--dry-run"] if dry_run else [])]
    return subprocess.run(full, check=False).returncode


class Writer:
    """Turns one parsed command into one agent-cli call."""

    def __init__(self, args: argparse.Namespace, scratch: Path) -> None:
        self.args = args
        self.scratch = scratch

    def text_file(self, name: str, *, cap: int, kind: str) -> str:
        raw = read_text(getattr(self.args, "text", None), self.args.file)
        text = Text.build(
            raw,
            skill=self.args.skill,
            cap=cap,
            kind=kind,
            long=self.args.long,
            markdown=self.args.verb.startswith("pr-"),
        )
        path = self.scratch / name
        path.write_text(text.body, encoding="utf-8")
        return str(path)

    def comment(self) -> list[str]:
        path = self.text_file("comment.md", cap=COMMENT_CAP, kind="comment")
        return ["workitem", "comment", self.args.id, "--text-file", path]

    def update(self) -> list[str]:
        if self.args.state is None and self.args.assignee is None:
            raise PostError("give --state or --assignee")
        command = ["workitem", "update", self.args.id]
        if self.args.state is not None:
            category = state_category(self.args.id, self.args.state)
            if category in CLOSING_CATEGORIES:
                raise PostError(
                    f"{self.args.state!r} is a {category} state, which closes the item;"
                    " a person does that"
                )
            command += ["--state", self.args.state]
        if self.args.assignee is not None:
            if self.args.assignee != "@me":
                raise PostError("a skill assigns work only to @me; ask the person to reassign")
            command += ["--assignee", "@me"]
        if self.args.if_rev is not None:
            command += ["--if-rev", str(self.args.if_rev)]
        return command

    def describe(self) -> list[str]:
        field = self.args.field
        path = self.text_file(f"{field}.md", cap=DESCRIPTION_CAP, kind=field.replace("-", " "))
        command = ["workitem", "update", self.args.id, f"--{field}-file", path]
        if self.args.if_rev is not None:
            command += ["--if-rev", str(self.args.if_rev)]
        return command

    def create(self) -> list[str]:
        command = ["workitem", "create", "--type", self.args.type]
        command += ["--title", check_title(self.args.title)]
        if self.args.parent is not None:
            command += ["--parent", str(self.args.parent)]
        if self.args.file is not None:
            path = self.text_file("description.md", cap=DESCRIPTION_CAP, kind="description")
            command += ["--description-file", path]
        for field in self.args.field:
            command += ["--field", field]
        return command

    def branch(self) -> list[str]:
        command = ["workitem", "link", self.args.id, "--repo", self.args.repo]
        if self.args.branch is not None:
            command += ["--branch", self.args.branch]
        return command

    def pr_create(self) -> list[str]:
        path = self.text_file("pr.md", cap=DESCRIPTION_CAP, kind="pull request description")
        command = ["pr", "create", "--repo", self.args.repo, "--source", self.args.source]
        command += ["--title", check_title(self.args.title), "--description-file", path]
        if self.args.target is not None:
            command += ["--target", self.args.target]
        for item in self.args.workitem:
            command += ["--workitem", str(item)]
        if self.args.draft:
            command += ["--draft", "true"]
        return command

    def pr_describe(self) -> list[str]:
        path = self.text_file("pr.md", cap=DESCRIPTION_CAP, kind="pull request description")
        return ["pr", "update", self.args.id, "--description-file", path]

    def pr_comment(self) -> list[str]:
        path = self.text_file("comment.md", cap=COMMENT_CAP, kind="comment")
        command = ["pr", "comment", self.args.id, "--text-file", path]
        if self.args.at is not None:
            command += ["--at", self.args.at]
        return command

    def pr_link(self) -> list[str]:
        return ["pr", "link", self.args.id, "--workitem", str(self.args.workitem)]


def text_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("text", nargs="?", help="the text, or - to read stdin")
    parser.add_argument("--file", type=Path, help="read the text from this Markdown file")


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--skill", required=True, help="the skill writing, e.g. dev-finish")
    parser.add_argument("--dry-run", action="store_true", help="show the request only")
    parser.add_argument("--long", action="store_true", help="lift the cap; only on request")
    verbs = parser.add_subparsers(dest="verb", required=True, metavar="VERB")

    p = verbs.add_parser("comment", help="comment on a work item (cap 600)")
    p.add_argument("id")
    text_args(p)

    p = verbs.add_parser("update", help="set a work item's state, or assign it to @me")
    p.add_argument("id")
    p.add_argument("--state")
    p.add_argument("--assignee", help="only @me")
    p.add_argument("--if-rev", type=int, help="refuse unless the item is still at this rev")

    p = verbs.add_parser("describe", help="replace a work item's description or criteria")
    p.add_argument("id")
    p.add_argument("--field", choices=("description", "acceptance-criteria"), default="description")
    p.add_argument("--if-rev", type=int, help="refuse unless the item is still at this rev")
    p.add_argument("--file", type=Path, required=True, help="the Markdown to write")

    p = verbs.add_parser("create", help="create a work item, optionally under a parent")
    p.add_argument("--type", required=True, help="Task, Bug, 'Product Backlog Item' ...")
    p.add_argument("--title", required=True)
    p.add_argument("--parent", type=int)
    p.add_argument("--file", type=Path, help="the description, as Markdown")
    p.add_argument("--field", action="append", default=[], help="NAME=VALUE, repeatable")

    p = verbs.add_parser("branch", help="create a work item's branch and link it")
    p.add_argument("id")
    p.add_argument("--repo", required=True)
    p.add_argument("--branch", help="default: {id}-{title-slug}")

    p = verbs.add_parser("pr-create", help="open a pull request linked to work items")
    p.add_argument("--repo", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--target")
    p.add_argument("--title", required=True)
    p.add_argument("--file", type=Path, required=True, help="the description, as Markdown")
    p.add_argument("--workitem", type=int, action="append", default=[])
    p.add_argument("--draft", action="store_true")

    p = verbs.add_parser("pr-describe", help="replace a pull request's description")
    p.add_argument("id")
    p.add_argument("--file", type=Path, required=True, help="the description, as Markdown")

    p = verbs.add_parser("pr-comment", help="start a comment thread on a pull request")
    p.add_argument("id")
    text_args(p)
    p.add_argument("--at", help="REPO[@REF]:PATH[:LINE[-LINE]], as agent-cli diff get prints it")

    p = verbs.add_parser("pr-link", help="link a work item to a pull request")
    p.add_argument("id")
    p.add_argument("--workitem", type=int, required=True)

    args = parser.parse_args(argv)
    if not SKILL_RE.match(args.skill):
        parser.error(f"--skill must name a dev- skill, not {args.skill!r}")
    return args


def main(argv: list[str]) -> int:
    args = parse(argv)
    with tempfile.TemporaryDirectory(prefix="post-") as scratch:
        writer = Writer(args, Path(scratch))
        try:
            command = getattr(writer, args.verb.replace("-", "_"))()
            return run(command, dry_run=args.dry_run)
        except PostError as err:
            print(f"post.py: {err}", file=sys.stderr)
            return err.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
