#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Make a skill's Azure DevOps writes through agent-cli, brief and marked.

Every write a skill makes goes through this script. It appends the footer
"Agent-written via /<skill>" to any text, rejects text over the length cap,
and offers no verb for closing or deleting work items, approving, or
merging, because those always need a person. The footer is italic on pull requests, which
store Markdown, and plain on work items, because agent-cli's Markdown to
HTML conversion has no italics.

Caps (characters, footer excluded): a comment 600, a description 2000.
--long lifts the cap, and is used only when the person asked for a longer
write. Pasted logs and code blocks count toward the cap for now.

Tags: a skill sets or clears only the team's own Blocked and Ready for QA
tags, named in the skill's assets/team.toml, and never replaces the other
tags an item carries. Ready for deployment is a person's to set.

Time: the timelog verbs write only the person's own entries, through the
commands team.toml names. A day never goes over the day's hours (8), and
every entry is a whole number of blocks (1 hour), at least one.

Text comes from a positional argument, or from a file (--file); either one
given as - reads stdin. Pass --dry-run to see the agent-cli request without
making it.

Examples:
  post.py --skill dev-start comment 1234 'Plan: add the retry, red tests first.'
  post.py --skill dev-start update 1234 --state 'In Progress' --assignee @me
  post.py --skill dev-sprint update 1234 --add-tag 'Ready for QA' --remaining 0
  post.py --skill dev-finish pr-create --repo web --source 1234-retry \\
      --title '1234 Retry on 429' --file pr.md --workitem 1234
  post.py --skill dev-sprint timelog 1234 --date 2026-10-21 --hours 5
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
from datetime import date
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit("post.py needs Python 3.11 or later; run it with `uv run`.")

import tomllib

COMMENT_CAP = 600
DESCRIPTION_CAP = 2000
TITLE_CAP = 120
SKILL_RE = re.compile(r"^dev-[a-z0-9]+(-[a-z0-9]+)*$")
FOOTER_RE = re.compile(r"\n*_?Agent-written via /[a-z0-9-]+_?\s*$")
# ADO's state categories that close or remove an item. A person moves an item there.
CLOSING_CATEGORIES = frozenset({"Resolved", "Completed", "Removed"})
REMAINING_WORK = "Microsoft.VSTS.Scheduling.RemainingWork"
ORIGINAL_ESTIMATE = "Microsoft.VSTS.Scheduling.OriginalEstimate"
# The skill's own copy of team.toml, or the source when run from shared/.
TEAM_FILES = ("assets/team.toml", "team.toml")
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


def has_field(item_id: str, ref: str) -> bool:
    item_type = read(["workitem", "get", item_id, "--comments", "0", "--fields", "type"])["type"]
    info = read(["workitem-type", "get", str(item_type), "--fields", "fields"])
    fields = info.get("fields")
    return any(isinstance(f, dict) and f.get("ref") == ref for f in fields or [])


def hours_value(value: float, flag: str) -> str:
    if value < 0:
        raise PostError(f"{flag} cannot be negative")
    return f"{value:g}"


def team_config() -> dict[str, object]:
    here = Path(__file__).resolve().parent.parent
    for name in TEAM_FILES:
        path = here / name
        if path.is_file():
            try:
                return tomllib.loads(path.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as err:
                raise PostError(f"{path}: {err}", EXIT_SETUP) from err
    raise PostError("this skill has no assets/team.toml; tags and time need it", EXIT_SETUP)


def section(config: dict[str, object], name: str) -> dict[str, object]:
    value = config.get(name)
    return value if isinstance(value, dict) else {}


def settable_tags() -> set[str]:
    """The team's Blocked and Ready for QA tags; a person sets Ready for deployment."""
    tags = section(team_config(), "tags")
    names = {tags.get("blocked"), tags.get("ready_for_qa")}
    return {n for n in names if isinstance(n, str) and n}


@dataclass(frozen=True)
class TimeLog:
    """The team's time-log commands, from team.toml's [timelog]."""

    commands: dict[str, list[str]]
    fields: dict[str, str]
    day_hours: float
    block_hours: float

    @classmethod
    def load(cls) -> TimeLog:
        raw = section(team_config(), "timelog")
        commands = {}
        for verb in ("list", "create", "update", "delete"):
            value = raw.get(verb)
            commands[verb] = [str(v) for v in value] if isinstance(value, list) else []
        fields = raw.get("fields")
        names = {"id": "id", "task": "task", "date": "date", "hours": "hours"}
        if isinstance(fields, dict):
            names.update({str(k): str(v) for k, v in fields.items()})
        day = raw.get("day_hours", 8)
        block = raw.get("block_hours", 1)
        if not isinstance(day, int | float) or not isinstance(block, int | float) or block <= 0:
            raise PostError(
                "team.toml [timelog]: day_hours and block_hours are numbers", EXIT_SETUP
            )
        return cls(commands, names, float(day), float(block))

    def command(self, verb: str, **values: str) -> list[str]:
        template = self.commands[verb]
        if not template:
            raise PostError(
                f"time logging is not set up: team.toml [timelog] has no `{verb}` command yet."
                " docs/porting.md says how to fill it in.",
                EXIT_SETUP,
            )
        out = []
        for arg in template:
            for key, value in values.items():
                arg = arg.replace("{" + key + "}", value)
            out.append(arg)
        return out

    def entries(self, day: date) -> list[dict[str, object]]:
        """The person's own entries on `day`, as {id, task, date, hours}."""
        when = day.isoformat()
        argv = self.command("list", person="@me", since=when, until=when)
        done = subprocess.run([agent_cli(), *argv], capture_output=True, text=True, check=False)
        if done.returncode != 0:
            raise PostError(f"agent-cli {' '.join(argv)} failed:\n{done.stderr.strip()}", 1)
        try:
            rows = json.loads(done.stdout)
        except json.JSONDecodeError as err:
            raise PostError(f"agent-cli {' '.join(argv)} printed no JSON: {err}", 1) from err
        if not isinstance(rows, list):
            raise PostError(f"agent-cli {' '.join(argv)} printed no JSON list", 1)
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            entry = {key: row.get(name) for key, name in self.fields.items()}
            if str(entry["date"])[:10] == when:
                out.append(entry)
        return out

    def check_hours(self, hours: float) -> str:
        blocks = hours / self.block_hours
        if hours < self.block_hours or abs(blocks - round(blocks)) > 1e-9:
            raise PostError(
                f"{hours:g} h is not a whole number of {self.block_hours:g} h blocks, at least one"
            )
        return f"{hours:g}"

    def check_day(self, total: float, day: date) -> None:
        if total > self.day_hours + 1e-9:
            raise PostError(
                f"{day.isoformat()} would hold {total:g} h; a day holds {self.day_hours:g} h,"
                " no more"
            )


def logged(entries: list[dict[str, object]]) -> float:
    total = 0.0
    for entry in entries:
        hours = entry.get("hours")
        if isinstance(hours, int | float) and not isinstance(hours, bool):
            total += float(hours)
        elif isinstance(hours, str):
            try:
                total += float(hours)
            except ValueError as err:
                raise PostError(f"a time entry's hours are not a number: {hours!r}", 1) from err
    return total


def own_entry(entries: list[dict[str, object]], entry_id: str, day: date) -> dict[str, object]:
    for entry in entries:
        if str(entry.get("id")) == entry_id:
            return entry
    raise PostError(f"entry {entry_id} is not one of your entries on {day.isoformat()}")


def run(command: list[str], *, dry_run: bool) -> int:
    full = [agent_cli(), "ado", *command, *(["--dry-run"] if dry_run else [])]
    return subprocess.run(full, check=False).returncode


def run_timelog(argv: list[str], *, dry_run: bool) -> int:
    if dry_run:
        print(" ".join(["agent-cli", *argv]))
        return 0
    return subprocess.run([agent_cli(), *argv], check=False).returncode


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
        changes = (
            self.args.state,
            self.args.assignee,
            self.args.iteration,
            self.args.remaining,
            self.args.estimate,
        )
        if all(c is None for c in changes) and not self.args.add_tag and not self.args.remove_tag:
            raise PostError(
                "give --state, --assignee, --iteration, --remaining, --estimate, or a tag"
            )
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
        if self.args.iteration is not None:
            command += ["--iteration", self.args.iteration]
        if self.args.estimate is not None:
            hours = hours_value(self.args.estimate, "--estimate")
            if has_field(self.args.id, ORIGINAL_ESTIMATE):
                command += ["--field", f"{ORIGINAL_ESTIMATE}={hours}"]
            if self.args.remaining is None:
                command += ["--field", f"{REMAINING_WORK}={hours}"]
        if self.args.remaining is not None:
            hours = hours_value(self.args.remaining, "--remaining")
            command += ["--field", f"{REMAINING_WORK}={hours}"]
        if self.args.add_tag or self.args.remove_tag:
            command += ["--tags", self.tags()]
        if self.args.if_rev is not None:
            command += ["--if-rev", str(self.args.if_rev)]
        return command

    def tags(self) -> str:
        allowed = settable_tags()
        for tag in [*self.args.add_tag, *self.args.remove_tag]:
            if tag not in allowed:
                raise PostError(
                    f"{tag!r} is not a tag a skill may set; team.toml names {sorted(allowed)}"
                )
        item = read(["workitem", "get", self.args.id, "--comments", "0", "--fields", "tags"])
        current = item.get("tags")
        tags = [str(t) for t in current] if isinstance(current, list) else []
        removing = {t.lower() for t in self.args.remove_tag}
        tags = [t for t in tags if t.lower() not in removing]
        tags += [t for t in self.args.add_tag if t.lower() not in {x.lower() for x in tags}]
        return ",".join(tags)

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
        if self.args.area is not None:
            command += ["--area", self.args.area]
        if self.args.iteration is not None:
            command += ["--iteration", self.args.iteration]
        if self.args.file is not None:
            path = self.text_file("description.md", cap=DESCRIPTION_CAP, kind="description")
            command += ["--description-file", path]
        if self.args.criteria_file is not None:
            raw = read_text(None, self.args.criteria_file)
            criteria = Text.build(
                raw,
                skill=self.args.skill,
                cap=DESCRIPTION_CAP,
                kind="acceptance criteria",
                long=self.args.long,
                markdown=False,
            )
            path = self.scratch / "criteria.md"
            path.write_text(criteria.body, encoding="utf-8")
            command += ["--acceptance-criteria-file", str(path)]
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


def timelog(args: argparse.Namespace) -> list[str]:
    """The time-log command for one write, after checking the day stays within its hours."""
    log = TimeLog.load()
    day: date = args.date
    entries = log.entries(day)
    when = day.isoformat()
    if args.verb == "timelog":
        hours = log.check_hours(args.hours)
        log.check_day(logged(entries) + args.hours, day)
        return log.command("create", task=args.task, date=when, hours=hours)
    entry = own_entry(entries, args.entry, day)
    if args.verb == "timelog-update":
        hours = log.check_hours(args.hours)
        log.check_day(logged(entries) - logged([entry]) + args.hours, day)
        return log.command("update", entry=args.entry, date=when, hours=hours)
    return log.command("delete", entry=args.entry, date=when)


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

    p = verbs.add_parser(
        "update", help="set a work item's state, sprint, hours, or team tags, or assign it to @me"
    )
    p.add_argument("id")
    p.add_argument("--state")
    p.add_argument("--assignee", help="only @me")
    p.add_argument("--iteration", help="the sprint's path, as sprint get prints it")
    p.add_argument("--remaining", type=float, help="Remaining Work, in hours")
    p.add_argument(
        "--estimate", type=float, help="Original Estimate, in hours; sets Remaining Work too"
    )
    p.add_argument("--add-tag", action="append", default=[], help="a team tag from team.toml")
    p.add_argument("--remove-tag", action="append", default=[], help="a team tag from team.toml")
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
    p.add_argument("--area", help="the area path")
    p.add_argument("--iteration", help="the sprint's path; leave out for the backlog")
    p.add_argument("--file", type=Path, help="the description, as Markdown")
    p.add_argument("--criteria-file", type=Path, help="the acceptance criteria, as Markdown")
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

    p = verbs.add_parser("timelog", help="log your own hours on a task for one day")
    p.add_argument("task")
    p.add_argument("--date", type=date.fromisoformat, required=True, help="YYYY-MM-DD")
    p.add_argument("--hours", type=float, required=True)

    p = verbs.add_parser("timelog-update", help="change the hours of one of your own entries")
    p.add_argument("entry")
    p.add_argument("--date", type=date.fromisoformat, required=True, help="the entry's day")
    p.add_argument("--hours", type=float, required=True)

    p = verbs.add_parser("timelog-delete", help="delete one of your own entries")
    p.add_argument("entry")
    p.add_argument("--date", type=date.fromisoformat, required=True, help="the entry's day")

    args = parser.parse_args(argv)
    if not SKILL_RE.match(args.skill):
        parser.error(f"--skill must name a dev- skill, not {args.skill!r}")
    return args


def main(argv: list[str]) -> int:
    args = parse(argv)
    with tempfile.TemporaryDirectory(prefix="post-") as scratch:
        writer = Writer(args, Path(scratch))
        try:
            if args.verb.startswith("timelog"):
                return run_timelog(timelog(args), dry_run=args.dry_run)
            command = getattr(writer, args.verb.replace("-", "_"))()
            return run(command, dry_run=args.dry_run)
        except PostError as err:
            print(f"post.py: {err}", file=sys.stderr)
            return err.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
