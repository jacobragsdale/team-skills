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
from typing import Literal, TypeVar, assert_never

if sys.version_info < (3, 11):
    sys.exit("post.py needs Python 3.11 or later; run it with `uv run`.")

import tomllib

COMMENT_CAP = 600
DESCRIPTION_CAP = 2000
TITLE_CAP = 120
HOURS_TOLERANCE = 1e-9  # float slack when checking that hours are whole blocks and fit the day
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
    def build(cls, raw: str, *, skill: str, cap: int | None, kind: str, markdown: bool) -> Text:
        """`cap` is None when the person asked for a longer write (--long)."""
        text = FOOTER_RE.sub("", raw.strip()).rstrip()
        if not text:
            msg = f"the {kind} is empty"
            raise PostError(msg)
        if cap is not None and len(text) > cap:
            msg = f"the {kind} is {len(text)} characters and the cap is {cap}. Cut it down; pass --long only if the person asked for a longer write."
            raise PostError(msg)
        footer = f"Agent-written via /{skill}"
        return cls(f"{text}\n\n{f'_{footer}_' if markdown else footer}\n")


def read_stdin() -> str:
    # typeshed types sys.stdin as `TextIO | Any`: it is None without a console, where reading fails anyway.
    return sys.stdin.read()  # ty: ignore[unsound-return-statement]


def read_text(value: str | None, file: Path | None) -> str:
    if file is not None and value is not None:
        msg = "give the text or --file, not both"
        raise PostError(msg)
    if file == Path("-"):
        return read_stdin()
    if file is not None:
        try:
            return file.read_text(encoding="utf-8")
        except OSError as err:
            msg = f"cannot read {file}: {err.strerror}"
            raise PostError(msg) from err
    if value == "-":
        return read_stdin()
    if value is None:
        msg = "give the text, - for stdin, or --file"
        raise PostError(msg)
    return value


def check_title(title: str) -> str:
    title = title.strip()
    if not title:
        msg = "the title is empty"
        raise PostError(msg)
    if len(title) > TITLE_CAP:
        msg = f"the title is {len(title)} characters and the cap is {TITLE_CAP}"
        raise PostError(msg)
    return title


def agent_cli() -> str:
    found = os.environ.get("AGENT_CLI") or shutil.which("agent-cli")
    if found is None:
        msg = "agent-cli is not on PATH; install it, or set AGENT_CLI"
        raise PostError(msg, EXIT_SETUP)
    return found


def read(command: list[str]) -> dict[str, object]:
    done = subprocess.run([agent_cli(), "ado", *command], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        msg = f"agent-cli ado {' '.join(command)} failed:\n{done.stderr.strip()}"
        raise PostError(msg, 1)
    try:
        value: object = json.loads(done.stdout)
    except json.JSONDecodeError as err:
        msg = f"agent-cli ado {' '.join(command)} printed no JSON: {err}"
        raise PostError(msg, 1) from err
    if not isinstance(value, dict):
        msg = f"agent-cli ado {' '.join(command)} printed no JSON object"
        raise PostError(msg, 1)
    return {str(k): v for k, v in value.items()}


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
    msg = f"{item_type} has no state {state!r}; its states are {', '.join(names)}"
    raise PostError(msg)


def has_field(item_id: str, ref: str) -> bool:
    item_type = read(["workitem", "get", item_id, "--comments", "0", "--fields", "type"])["type"]
    info = read(["workitem-type", "get", str(item_type), "--fields", "fields"])
    fields = info.get("fields")
    return any(isinstance(f, dict) and f.get("ref") == ref for f in (fields if isinstance(fields, list) else []))


def hours_value(value: float, flag: str) -> str:
    if value < 0:
        msg = f"{flag} cannot be negative"
        raise PostError(msg)
    return f"{value:g}"


def team_config() -> dict[str, object]:
    here = Path(__file__).resolve().parent.parent
    for name in TEAM_FILES:
        path = here / name
        if path.is_file():
            try:
                return dict[str, object](tomllib.loads(path.read_text(encoding="utf-8")))
            except tomllib.TOMLDecodeError as err:
                msg = f"{path}: {err}"
                raise PostError(msg, EXIT_SETUP) from err
    msg = "this skill has no assets/team.toml; tags and time need it"
    raise PostError(msg, EXIT_SETUP)


def section(config: dict[str, object], name: str) -> dict[str, object]:
    value = config.get(name)
    return {str(k): v for k, v in value.items()} if isinstance(value, dict) else {}


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
            msg = "team.toml [timelog]: day_hours and block_hours are numbers"
            raise PostError(msg, EXIT_SETUP)
        return cls(commands, names, float(day), float(block))

    def command(self, verb: str, **values: str) -> list[str]:
        template = self.commands[verb]
        if not template:
            msg = f"time logging is not set up: team.toml [timelog] has no `{verb}` command yet. docs/porting.md says how to fill it in."
            raise PostError(msg, EXIT_SETUP)
        out = []
        for arg in template:
            filled = arg
            for key, value in values.items():
                filled = filled.replace("{" + key + "}", value)
            out.append(filled)
        return out

    def entries(self, day: date) -> list[dict[str, object]]:
        """The person's own entries on `day`, as {id, task, date, hours}."""
        when = day.isoformat()
        argv = self.command("list", person="@me", since=when, until=when)
        done = subprocess.run([agent_cli(), *argv], capture_output=True, text=True, check=False)
        if done.returncode != 0:
            msg = f"agent-cli {' '.join(argv)} failed:\n{done.stderr.strip()}"
            raise PostError(msg, 1)
        try:
            rows: object = json.loads(done.stdout)
        except json.JSONDecodeError as err:
            msg = f"agent-cli {' '.join(argv)} printed no JSON: {err}"
            raise PostError(msg, 1) from err
        if not isinstance(rows, list):
            msg = f"agent-cli {' '.join(argv)} printed no JSON list"
            raise PostError(msg, 1)
        out: list[dict[str, object]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            entry = {key: row.get(name) for key, name in self.fields.items()}
            if str(entry["date"])[:10] == when:
                out.append(entry)
        return out

    def check_hours(self, hours: float) -> str:
        blocks = hours / self.block_hours
        if hours < self.block_hours or abs(blocks - round(blocks)) > HOURS_TOLERANCE:
            msg = f"{hours:g} h is not a whole number of {self.block_hours:g} h blocks, at least one"
            raise PostError(msg)
        return f"{hours:g}"

    def check_day(self, total: float, day: date) -> None:
        if total > self.day_hours + HOURS_TOLERANCE:
            msg = f"{day.isoformat()} would hold {total:g} h; a day holds {self.day_hours:g} h, no more"
            raise PostError(msg)


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
                msg = f"a time entry's hours are not a number: {hours!r}"
                raise PostError(msg, 1) from err
    return total


def own_entry(entries: list[dict[str, object]], entry_id: str, day: date) -> dict[str, object]:
    for entry in entries:
        if str(entry.get("id")) == entry_id:
            return entry
    msg = f"entry {entry_id} is not one of your entries on {day.isoformat()}"
    raise PostError(msg)


def run(command: list[str], *, dry_run: bool) -> int:
    full = [agent_cli(), "ado", *command, *(["--dry-run"] if dry_run else [])]
    return subprocess.run(full, check=False).returncode


def run_timelog(argv: list[str], *, dry_run: bool) -> int:
    if dry_run:
        print(" ".join(["agent-cli", *argv]))
        return 0
    return subprocess.run([agent_cli(), *argv], check=False).returncode


# --- the parsed command ------------------------------------------------------
# argparse hands back an untyped Namespace. parse() turns it into one of the
# frozen verb classes below, so everything past it is typed.


@dataclass(frozen=True)
class Options:
    """The options every verb takes."""

    skill: str
    dry_run: bool
    long: bool


@dataclass(frozen=True)
class Comment:
    id: str
    text: str | None
    file: Path | None


@dataclass(frozen=True)
class Update:
    id: str
    state: str | None
    assignee: str | None
    iteration: str | None
    remaining: float | None
    estimate: float | None
    add_tag: tuple[str, ...]
    remove_tag: tuple[str, ...]
    if_rev: int | None


DescribeField = Literal["description", "acceptance-criteria"]
DESCRIBE_FIELDS: dict[str, DescribeField] = {"description": "description", "acceptance-criteria": "acceptance-criteria"}


@dataclass(frozen=True)
class Describe:
    id: str
    field: DescribeField
    if_rev: int | None
    file: Path


@dataclass(frozen=True)
class Create:
    type: str
    title: str
    parent: int | None
    area: str | None
    iteration: str | None
    file: Path | None
    criteria_file: Path | None
    field: tuple[str, ...]


@dataclass(frozen=True)
class Branch:
    id: str
    repo: str
    branch: str | None


@dataclass(frozen=True)
class PrCreate:
    repo: str
    source: str
    target: str | None
    title: str
    file: Path
    workitem: tuple[int, ...]
    draft: bool


@dataclass(frozen=True)
class PrDescribe:
    id: str
    file: Path


@dataclass(frozen=True)
class PrComment:
    id: str
    text: str | None
    file: Path | None
    at: str | None


@dataclass(frozen=True)
class PrLink:
    id: str
    workitem: int


@dataclass(frozen=True)
class TimelogCreate:
    task: str
    date: date
    hours: float


@dataclass(frozen=True)
class TimelogUpdate:
    entry: str
    date: date
    hours: float


@dataclass(frozen=True)
class TimelogDelete:
    entry: str
    date: date


Timelog = TimelogCreate | TimelogUpdate | TimelogDelete
AdoWrite = Comment | Update | Describe | Create | Branch | PrCreate | PrDescribe | PrComment | PrLink
Verb = AdoWrite | Timelog


class Writer:
    """Turns one ADO write into one agent-cli call."""

    def __init__(self, options: Options, scratch: Path) -> None:
        self.options = options
        self.scratch = scratch

    def text_file(self, name: str, raw: str, *, cap: int, kind: str, markdown: bool) -> str:
        text = Text.build(raw, skill=self.options.skill, cap=None if self.options.long else cap, kind=kind, markdown=markdown)
        path = self.scratch / name
        path.write_text(text.body, encoding="utf-8")
        return str(path)

    def command(self, verb: AdoWrite) -> list[str]:
        match verb:
            case Comment():
                path = self.text_file("comment.md", read_text(verb.text, verb.file), cap=COMMENT_CAP, kind="comment", markdown=False)
                return ["workitem", "comment", verb.id, "--text-file", path]
            case Update():
                return self.update(verb)
            case Describe():
                path = self.text_file(f"{verb.field}.md", read_text(None, verb.file), cap=DESCRIPTION_CAP, kind=verb.field.replace("-", " "), markdown=False)
                return ["workitem", "update", verb.id, f"--{verb.field}-file", path, *if_rev(verb.if_rev)]
            case Create():
                return self.create(verb)
            case Branch():
                return ["workitem", "link", verb.id, "--repo", verb.repo, *(["--branch", verb.branch] if verb.branch is not None else [])]
            case PrCreate():
                return self.pr_create(verb)
            case PrDescribe():
                path = self.text_file("pr.md", read_text(None, verb.file), cap=DESCRIPTION_CAP, kind="pull request description", markdown=True)
                return ["pr", "update", verb.id, "--description-file", path]
            case PrComment():
                path = self.text_file("comment.md", read_text(verb.text, verb.file), cap=COMMENT_CAP, kind="comment", markdown=True)
                return ["pr", "comment", verb.id, "--text-file", path, *(["--at", verb.at] if verb.at is not None else [])]
            case PrLink():
                return ["pr", "link", verb.id, "--workitem", str(verb.workitem)]
            case _:
                assert_never(verb)

    def update(self, verb: Update) -> list[str]:
        changes = (verb.state, verb.assignee, verb.iteration, verb.remaining, verb.estimate)
        if all(c is None for c in changes) and not verb.add_tag and not verb.remove_tag:
            msg = "give --state, --assignee, --iteration, --remaining, --estimate, or a tag"
            raise PostError(msg)
        command = ["workitem", "update", verb.id]
        if verb.state is not None:
            category = state_category(verb.id, verb.state)
            if category in CLOSING_CATEGORIES:
                msg = f"{verb.state!r} is a {category} state, which closes the item; a person does that"
                raise PostError(msg)
            command += ["--state", verb.state]
        if verb.assignee is not None:
            if verb.assignee != "@me":
                msg = "a skill assigns work only to @me; ask the person to reassign"
                raise PostError(msg)
            command += ["--assignee", "@me"]
        if verb.iteration is not None:
            command += ["--iteration", verb.iteration]
        if verb.estimate is not None:
            hours = hours_value(verb.estimate, "--estimate")
            if has_field(verb.id, ORIGINAL_ESTIMATE):
                command += ["--field", f"{ORIGINAL_ESTIMATE}={hours}"]
            if verb.remaining is None:
                command += ["--field", f"{REMAINING_WORK}={hours}"]
        if verb.remaining is not None:
            hours = hours_value(verb.remaining, "--remaining")
            command += ["--field", f"{REMAINING_WORK}={hours}"]
        if verb.add_tag or verb.remove_tag:
            command += ["--tags", tags(verb)]
        return [*command, *if_rev(verb.if_rev)]

    def create(self, verb: Create) -> list[str]:
        command: list[str] = ["workitem", "create", "--type", verb.type, "--title", check_title(verb.title)]
        if verb.parent is not None:
            command += ["--parent", str(verb.parent)]
        if verb.area is not None:
            command += ["--area", verb.area]
        if verb.iteration is not None:
            command += ["--iteration", verb.iteration]
        if verb.file is not None:
            path = self.text_file("description.md", read_text(None, verb.file), cap=DESCRIPTION_CAP, kind="description", markdown=False)
            command += ["--description-file", path]
        if verb.criteria_file is not None:
            path = self.text_file("criteria.md", read_text(None, verb.criteria_file), cap=DESCRIPTION_CAP, kind="acceptance criteria", markdown=False)
            command += ["--acceptance-criteria-file", path]
        for field in verb.field:
            command += ["--field", field]
        return command

    def pr_create(self, verb: PrCreate) -> list[str]:
        path = self.text_file("pr.md", read_text(None, verb.file), cap=DESCRIPTION_CAP, kind="pull request description", markdown=True)
        command = ["pr", "create", "--repo", verb.repo, "--source", verb.source]
        command += ["--title", check_title(verb.title), "--description-file", path]
        if verb.target is not None:
            command += ["--target", verb.target]
        for item in verb.workitem:
            command += ["--workitem", str(item)]
        if verb.draft:
            command += ["--draft", "true"]
        return command


def if_rev(rev: int | None) -> list[str]:
    return ["--if-rev", str(rev)] if rev is not None else []


def tags(verb: Update) -> str:
    """The item's tags after the change, keeping every tag the skill does not own."""
    allowed = settable_tags()
    for tag in [*verb.add_tag, *verb.remove_tag]:
        if tag not in allowed:
            msg = f"{tag!r} is not a tag a skill may set; team.toml names {sorted(allowed)}"
            raise PostError(msg)
    item = read(["workitem", "get", verb.id, "--comments", "0", "--fields", "tags"])
    current = item.get("tags")
    names = [str(t) for t in current] if isinstance(current, list) else []
    removing = {t.lower() for t in verb.remove_tag}
    names = [t for t in names if t.lower() not in removing]
    names += [t for t in verb.add_tag if t.lower() not in {x.lower() for x in names}]
    return ",".join(names)


def timelog(verb: Timelog) -> list[str]:
    """The time-log command for one write, after checking the day stays within its hours."""
    log = TimeLog.load()
    entries = log.entries(verb.date)
    when = verb.date.isoformat()
    match verb:
        case TimelogCreate():
            hours = log.check_hours(verb.hours)
            log.check_day(logged(entries) + verb.hours, verb.date)
            return log.command("create", task=verb.task, date=when, hours=hours)
        case TimelogUpdate():
            entry = own_entry(entries, verb.entry, verb.date)
            hours = log.check_hours(verb.hours)
            log.check_day(logged(entries) - logged([entry]) + verb.hours, verb.date)
            return log.command("update", entry=verb.entry, date=when, hours=hours)
        case TimelogDelete():
            own_entry(entries, verb.entry, verb.date)
            return log.command("delete", entry=verb.entry, date=when)
        case _:
            assert_never(verb)


T = TypeVar("T")


def optional(ns: argparse.Namespace, name: str, kind: type[T]) -> T | None:
    """One parsed argument, checked against the type its add_argument gives it."""
    value: object = getattr(ns, name, None)
    if value is None or isinstance(value, kind):
        return value
    msg = f"argument {name} parsed as {type(value).__name__}, not {kind.__name__}"
    raise TypeError(msg)


def required(ns: argparse.Namespace, name: str, kind: type[T]) -> T:
    value = optional(ns, name, kind)
    if value is None:
        msg = f"argument {name} is missing"
        raise TypeError(msg)
    return value


def repeated(ns: argparse.Namespace, name: str, kind: type[T]) -> tuple[T, ...]:
    value: object = getattr(ns, name)
    if not isinstance(value, list):
        msg = f"argument {name} parsed as {type(value).__name__}, not a list"
        raise TypeError(msg)
    out: list[T] = []
    for item in value:
        if not isinstance(item, kind):
            msg = f"argument {name} holds a {type(item).__name__}, not {kind.__name__}"
            raise TypeError(msg)
        out.append(item)
    return tuple(out)


def verb_from(ns: argparse.Namespace) -> Verb:
    """The typed verb for a parsed command line."""
    s, p = str, Path
    match required(ns, "verb", s):
        case "comment":
            return Comment(required(ns, "id", s), optional(ns, "text", s), optional(ns, "file", p))
        case "update":
            return Update(
                required(ns, "id", s),
                optional(ns, "state", s),
                optional(ns, "assignee", s),
                optional(ns, "iteration", s),
                optional(ns, "remaining", float),
                optional(ns, "estimate", float),
                repeated(ns, "add_tag", s),
                repeated(ns, "remove_tag", s),
                optional(ns, "if_rev", int),
            )
        case "describe":
            field = DESCRIBE_FIELDS[required(ns, "field", s)]
            return Describe(required(ns, "id", s), field, optional(ns, "if_rev", int), required(ns, "file", p))
        case "create":
            return Create(
                required(ns, "type", s),
                required(ns, "title", s),
                optional(ns, "parent", int),
                optional(ns, "area", s),
                optional(ns, "iteration", s),
                optional(ns, "file", p),
                optional(ns, "criteria_file", p),
                repeated(ns, "field", s),
            )
        case "branch":
            return Branch(required(ns, "id", s), required(ns, "repo", s), optional(ns, "branch", s))
        case "pr-create":
            return PrCreate(
                required(ns, "repo", s),
                required(ns, "source", s),
                optional(ns, "target", s),
                required(ns, "title", s),
                required(ns, "file", p),
                repeated(ns, "workitem", int),
                required(ns, "draft", bool),
            )
        case "pr-describe":
            return PrDescribe(required(ns, "id", s), required(ns, "file", p))
        case "pr-comment":
            return PrComment(required(ns, "id", s), optional(ns, "text", s), optional(ns, "file", p), optional(ns, "at", s))
        case "pr-link":
            return PrLink(required(ns, "id", s), required(ns, "workitem", int))
        case "timelog":
            return TimelogCreate(required(ns, "task", s), required(ns, "date", date), required(ns, "hours", float))
        case "timelog-update":
            return TimelogUpdate(required(ns, "entry", s), required(ns, "date", date), required(ns, "hours", float))
        case "timelog-delete":
            return TimelogDelete(required(ns, "entry", s), required(ns, "date", date))
        case other:
            msg = f"no verb {other!r}"
            raise TypeError(msg)


def text_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("text", nargs="?", help="the text, or - to read stdin")
    parser.add_argument("--file", type=Path, help="read the text from this Markdown file")


def parse(argv: list[str]) -> tuple[Options, Verb]:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skill", required=True, help="the skill writing, e.g. dev-finish")
    parser.add_argument("--dry-run", action="store_true", help="show the request only")
    parser.add_argument("--long", action="store_true", help="lift the cap; only on request")
    verbs = parser.add_subparsers(dest="verb", required=True, metavar="VERB")

    p = verbs.add_parser("comment", help="comment on a work item (cap 600)")
    p.add_argument("id")
    text_args(p)

    p = verbs.add_parser("update", help="set a work item's state, sprint, hours, or team tags, or assign it to @me")
    p.add_argument("id")
    p.add_argument("--state")
    p.add_argument("--assignee", help="only @me")
    p.add_argument("--iteration", help="the sprint's path, as sprint get prints it")
    p.add_argument("--remaining", type=float, help="Remaining Work, in hours")
    p.add_argument("--estimate", type=float, help="Original Estimate, in hours; sets Remaining Work too")
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

    ns = parser.parse_args(argv)
    options = Options(required(ns, "skill", str), required(ns, "dry_run", bool), required(ns, "long", bool))
    if not SKILL_RE.match(options.skill):
        parser.error(f"--skill must name a dev- skill, not {options.skill!r}")
    return options, verb_from(ns)


def main(argv: list[str]) -> int:
    options, verb = parse(argv)
    with tempfile.TemporaryDirectory(prefix="post-") as scratch:
        try:
            match verb:
                case TimelogCreate() | TimelogUpdate() | TimelogDelete():
                    return run_timelog(timelog(verb), dry_run=options.dry_run)
                case _:
                    return run(Writer(options, Path(scratch)).command(verb), dry_run=options.dry_run)
        except PostError as err:
            print(f"post.py: {err}", file=sys.stderr)
            return err.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
