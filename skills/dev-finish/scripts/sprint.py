#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Show a sprint by stage, for you or the whole team, with fixes and time to fill.

  view              your sprint: the headline, what needs you, your items by
                    stage, the next item to pick up, what you did since your
                    last working day, and fixes for your own items
  view --team       the team: the headline, a row per person, what needs
                    attention, and what is likely to miss
  view --person N   one teammate's sprint, read-only
  time              your hours per working day since Monday of last week, and
                    a proposal that fills each gap to exactly the day's hours
  next              the top unassigned tasks in the current sprint
  reviewers FILE    one or two teammates to review a change to these files

A task's stage comes from its state, its pull request, and the team's tags:
not started, in progress, in review, approved, merged, ready for QA, ready for
deployment, done. Silent means no comment in 3 days and stale means the same
state for 7, as in metrics.py.

Reads only; every call goes through agent-cli, and the writes go through
post.py. The team's facts come from team.toml beside this skill. In a
terminal the output is colored tables; piped, it is Markdown, so an agent chat
renders it as tables. --format json prints the data instead.

Examples:
  sprint.py view
  sprint.py view --team
  sprint.py view --person 'Sam Lee'
  sprint.py time --format md
  sprint.py reviewers src/orders/client.py src/orders/retry.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import TypeVar

if sys.version_info < (3, 11):
    sys.exit("sprint.py needs Python 3.11 or later; run it with `uv run`.")

import tomllib

SILENT_DAYS = 3  # as metrics.py counts silent items
STALE_DAYS = 7  # as metrics.py counts stale items
LAST_DAYS = 2  # in the sprint's last working days, open work is likely to miss
PLANNING_GRACE = timedelta(days=1)  # items added on the first day were planned
REVIEW_MONTHS = 12
WORKERS = 8
LIST_LIMIT = "1000"
TEAM_FILES = ("assets/team.toml", "team.toml")
TASK_TYPES = frozenset({"task"})
QA_PREFIX = "qa"
APPROVING = frozenset({"approved", "approved with suggestions"})
HOLDING = frozenset({"waiting for author", "rejected"})
POINT_FIELDS = ("Story Points", "Effort", "Size")
STAGES = {
    "todo": "Not started",
    "doing": "In progress",
    "review": "In review",
    "approved": "Approved",
    "merged": "Merged",
    "qa": "Ready for QA",
    "deploy": "Ready for deployment",
    "done": "Done",
}
OPEN_STAGES = ("todo", "doing", "review")
EXIT_SETUP = 3

T = TypeVar("T")
R = TypeVar("R")


class SprintError(Exception):
    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


# --- agent-cli -------------------------------------------------------------


def binary() -> str:
    found = os.environ.get("AGENT_CLI") or shutil.which("agent-cli")
    if not found:
        raise SprintError("agent-cli is not on PATH; install it, or set AGENT_CLI", EXIT_SETUP)
    return found


def call(argv: list[str]) -> object:
    done = subprocess.run([binary(), *argv], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise SprintError(f"agent-cli {' '.join(argv)} failed:\n{done.stderr.strip()}")
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError as err:
        raise SprintError(f"agent-cli {' '.join(argv)} printed no JSON: {err}") from err


def ado(*args: str) -> object:
    return call(["ado", *args])


def rows(value: object, what: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(r, dict) for r in value):
        raise SprintError(f"{what}: expected a JSON list of objects")
    return [{str(k): v for k, v in r.items()} for r in value]


def obj(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SprintError(f"{what}: expected a JSON object")
    return {str(k): v for k, v in value.items()}


def parallel(fn: Callable[[T], R], items: Iterable[T]) -> list[R]:
    work = list(items)
    if len(work) < 2:
        return [fn(x) for x in work]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return list(pool.map(fn, work))


def stamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as err:
        raise SprintError(f"{value!r} is not a timestamp") from err
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def text(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    return value if isinstance(value, str) else ""


def number(row: dict[str, object], key: str, what: str) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, str) and value.isdigit():
            return int(value)
        raise SprintError(f"{what}: {key} is not a number")
    return value


def real(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def rfc3339(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_start(day: date) -> datetime:
    """Local midnight at the start of `day`, in UTC."""
    return datetime.combine(day, time.min).astimezone().astimezone(UTC)


def weekdays(first: date, last: date) -> list[date]:
    out, day = [], first
    while day <= last:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


# --- team.toml ---------------------------------------------------------------


@dataclass(frozen=True)
class Person:
    name: str
    address: str


@dataclass(frozen=True)
class Tags:
    blocked: str
    ready_for_qa: str
    ready_for_deployment: str
    on_pbi: bool
    qa_when_merged: bool


@dataclass(frozen=True)
class Entry:
    id: str
    task: int
    day: date
    hours: float


@dataclass(frozen=True)
class TimeLog:
    commands: dict[str, list[str]]
    fields: dict[str, str]
    day_hours: float
    block_hours: float
    time_off_task: int | None

    @property
    def ported(self) -> bool:
        return bool(self.commands.get("list"))

    def entries(self, person: str, first: date, last: date) -> list[Entry]:
        values = {"person": person, "since": first.isoformat(), "until": last.isoformat()}
        argv = []
        for arg in self.commands["list"]:
            for key, value in values.items():
                arg = arg.replace("{" + key + "}", value)
            argv.append(arg)
        out = []
        for row in rows(call(argv), "time entries"):
            entry = {key: row.get(name) for key, name in self.fields.items()}
            when, task = str(entry.get("date") or "")[:10], str(entry.get("task"))
            try:
                day, hours = date.fromisoformat(when), float(str(entry.get("hours")))
            except ValueError as err:
                raise SprintError(f"a time entry has no date or hours: {row}") from err
            if first <= day <= last:
                out.append(
                    Entry(str(entry.get("id")), int(task) if task.isdigit() else 0, day, hours)
                )
        return out


@dataclass(frozen=True)
class Team:
    name: str | None
    area: str | None
    people: list[Person]
    tags: Tags
    timelog: TimeLog

    def team_args(self) -> list[str]:
        return ["--team", self.name] if self.name else []

    def area_args(self) -> list[str]:
        return ["--area", self.area] if self.area else []

    def find(self, query: str) -> Person | None:
        wanted = query.strip().lower()
        exact = [p for p in self.people if wanted in (p.name.lower(), p.address.lower())]
        if exact:
            return exact[0]
        partial = [p for p in self.people if wanted in p.name.lower()]
        if len(partial) > 1:
            names = ", ".join(p.name for p in partial)
            raise SprintError(f"{query!r} matches {names}; give more of the name")
        return partial[0] if partial else None

    def display(self, address: str | None) -> str:
        if not address:
            return "unassigned"
        for person in self.people:
            if person.address.lower() == address.lower():
                return person.name
        return address


def table(config: dict[str, object], name: str) -> dict[str, object]:
    value = config.get(name)
    return {str(k): v for k, v in value.items()} if isinstance(value, dict) else {}


def option(section: dict[str, object], key: str) -> str | None:
    value = section.get(key)
    return (value.strip() or None) if isinstance(value, str) else None


def load_team() -> Team:
    here = Path(__file__).resolve().parent.parent
    path = next((here / n for n in TEAM_FILES if (here / n).is_file()), None)
    if path is None:
        raise SprintError("no team.toml beside this skill; run scripts/sync_shared.py", EXIT_SETUP)
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as err:
        raise SprintError(f"{path}: {err}", EXIT_SETUP) from err
    ado_section, tags, log = table(config, "ado"), table(config, "tags"), table(config, "timelog")
    people = []
    raw_people = config.get("people", [])
    for entry in raw_people if isinstance(raw_people, list) else []:
        if isinstance(entry, dict) and entry.get("name") and entry.get("address"):
            people.append(Person(str(entry["name"]), str(entry["address"])))
    on = option(tags, "on") or "task"
    if on not in ("task", "pbi"):
        raise SprintError(f"{path}: [tags] on is task or pbi, not {on!r}", EXIT_SETUP)
    commands = {}
    for verb in ("list", "create", "update", "delete"):
        value = log.get(verb)
        commands[verb] = [str(v) for v in value] if isinstance(value, list) else []
    fields = {"id": "id", "task": "task", "date": "date", "hours": "hours"}
    raw_fields = log.get("fields")
    if isinstance(raw_fields, dict):
        fields.update({str(k): str(v) for k, v in raw_fields.items()})
    day_hours, block = real(log.get("day_hours", 8)), real(log.get("block_hours", 1))
    if day_hours is None or block is None or block <= 0:
        raise SprintError(f"{path}: [timelog] day_hours and block_hours are numbers", EXIT_SETUP)
    off = log.get("time_off_task")
    return Team(
        name=option(ado_section, "team"),
        area=option(ado_section, "area"),
        people=people,
        tags=Tags(
            blocked=option(tags, "blocked") or "Blocked",
            ready_for_qa=option(tags, "ready_for_qa") or "Ready for QA",
            ready_for_deployment=option(tags, "ready_for_deployment") or "Ready for deployment",
            on_pbi=on == "pbi",
            qa_when_merged=(option(tags, "ready_for_qa_when") or "merged") == "merged",
        ),
        timelog=TimeLog(
            commands=commands,
            fields=fields,
            day_hours=day_hours,
            block_hours=block,
            time_off_task=off if isinstance(off, int) and off > 0 else None,
        ),
    )


def whoami() -> str:
    """Your sign-in address, which ADO prints as an assignee."""
    configured = os.environ.get("TEAM_SKILLS_ME")
    if configured:
        return configured
    mine = rows(
        ado("workitem", "list", "--assignee", "@me", "--limit", "1", "--fields", "assignee"),
        "your work items",
    )
    if mine and text(mine[0], "assignee"):
        return text(mine[0], "assignee")
    for check in rows(call(["doctor"]), "agent-cli doctor"):
        found = re.search(r"signed in as (\S+)", text(check, "detail"))
        if check.get("domain") == "ado" and found:
            return found.group(1)
    raise SprintError(
        "cannot tell who you are; set TEAM_SKILLS_ME to your sign-in address", EXIT_SETUP
    )


# --- the sprint and its work ---------------------------------------------------


@dataclass(frozen=True)
class Load:
    address: str
    items: int
    remaining: float
    capacity_left: float | None


@dataclass(frozen=True)
class Sprint:
    path: str
    name: str
    start: date
    finish: date
    days_left: int | None
    points: float
    points_done: float
    remaining: float
    loads: list[Load]
    days_off: frozenset[date]

    def working_days(self) -> list[date]:
        return [d for d in weekdays(self.start, self.finish) if d not in self.days_off]

    def day_number(self, today: date) -> int:
        return len([d for d in self.working_days() if d <= today])

    def load(self, address: str) -> Load | None:
        return next((p for p in self.loads if p.address.lower() == address.lower()), None)


def off_days(ranges: object, what: str) -> frozenset[date]:
    out: set[date] = set()
    for entry in rows(ranges or [], what):
        first, last = stamp(entry.get("start")), stamp(entry.get("end"))
        if first and last:
            out.update(weekdays(first.date(), last.date()))
    return frozenset(out)


def load_sprint(team: Team, which: str = "@current") -> Sprint:
    what = f"sprint {which}"
    try:
        row = obj(ado("sprint", "get", which, *team.team_args()), what)
    except SprintError as err:
        raise SprintError(
            f"{err}\nThe sprint is read through the team's settings: set [ado] team in"
            " team.toml, or see docs/porting.md if the team API is unavailable.",
            EXIT_SETUP,
        ) from err
    start, finish = stamp(row.get("start")), stamp(row.get("finish"))
    if start is None or finish is None:
        raise SprintError(f"{what} has no dates; give the sprint dates in ADO", EXIT_SETUP)
    totals = obj(row.get("totals") or {}, f"{what}: totals")
    loads = [
        Load(
            address=text(p, "name"),
            items=number(p, "items", what) if p.get("items") is not None else 0,
            remaining=real(p.get("remaining_work")) or 0.0,
            capacity_left=real(p.get("capacity_left")),
        )
        for p in rows(row.get("people") or [], f"{what}: people")
    ]
    left = row.get("working_days_left")
    return Sprint(
        path=text(row, "path"),
        name=text(row, "name"),
        start=start.date(),
        finish=finish.date(),
        days_left=left if isinstance(left, int) and not isinstance(left, bool) else None,
        points=real(totals.get("points")) or 0.0,
        points_done=real(totals.get("points_done")) or 0.0,
        remaining=real(totals.get("remaining_work")) or 0.0,
        loads=loads,
        days_off=off_days(row.get("team_days_off"), f"{what}: days off"),
    )


@dataclass
class PullRequest:
    id: int
    repo: str
    title: str
    author: str
    status: str
    draft: bool
    created: datetime | None
    votes: list[str]
    open_threads: int
    work_items: list[int]
    source: str

    @property
    def approved(self) -> bool:
        votes = {v.lower() for v in self.votes}
        return bool(votes & APPROVING) and not votes & HOLDING


def load_pr(pr_id: int) -> PullRequest:
    what = f"pull request {pr_id}"
    row = obj(
        ado(
            "pr",
            "get",
            str(pr_id),
            "--comments",
            "0",
            "--fields",
            "id,repo,title,author,status,is_draft,created,reviewers,open_threads,work_items,source",
        ),
        what,
    )
    reviewers = rows(row.get("reviewers") or [], f"{what}: reviewers")
    threads = row.get("open_threads")
    items = row.get("work_items")
    return PullRequest(
        id=pr_id,
        repo=text(row, "repo"),
        title=text(row, "title"),
        author=text(row, "author"),
        status=text(row, "status"),
        draft=row.get("is_draft") is True,
        created=stamp(row.get("created")),
        votes=[text(r, "vote") for r in reviewers if text(r, "vote")],
        open_threads=threads if isinstance(threads, int) else 0,
        work_items=[i for i in items if isinstance(i, int)] if isinstance(items, list) else [],
        source=text(row, "source"),
    )


@dataclass
class Span:
    state: str
    since: datetime
    until: datetime | None


@dataclass
class Item:
    id: int
    type: str
    title: str
    state: str
    category: str
    assignee: str | None
    iteration: str
    tags: list[str]
    priority: int | None
    detailed: bool = False
    parent: int | None = None
    children: list[int] = field(default_factory=list)
    prs: list[int] = field(default_factory=list)
    branches: list[str] = field(default_factory=list)
    last_comment: datetime | None = None
    criteria: str = ""
    spans: list[Span] = field(default_factory=list)
    entered: datetime | None = None
    tag_since: dict[str, datetime] = field(default_factory=dict)
    points: float | None = None
    remaining: float | None = None
    estimate: float | None = None

    @property
    def is_task(self) -> bool:
        return self.type.lower() in TASK_TYPES

    @property
    def is_qa(self) -> bool:
        return self.is_task and self.title.strip().lower().startswith(QA_PREFIX)

    @property
    def done(self) -> bool:
        return self.category in ("Completed", "Removed")

    @property
    def active(self) -> bool:
        return self.category == "InProgress"

    @property
    def since(self) -> datetime | None:
        return self.spans[-1].since if self.spans else None

    def has_tag(self, name: str) -> bool:
        return name.lower() in {t.lower() for t in self.tags}


class States:
    """Each work item type's states and their categories, read once per type."""

    def __init__(self) -> None:
        self.by_type: dict[str, dict[str, str]] = {}

    def load(self, types: Iterable[str]) -> None:
        wanted = sorted({t for t in types if t.lower() not in self.by_type})
        for name, states in zip(wanted, parallel(self.read, wanted), strict=True):
            self.by_type[name.lower()] = states

    @staticmethod
    def read(item_type: str) -> dict[str, str]:
        what = f"work item type {item_type}"
        info = obj(ado("workitem-type", "get", item_type, "--fields", "states"), what)
        return {text(s, "name"): text(s, "category") for s in rows(info.get("states"), what)}

    def category(self, item_type: str, state: str) -> str:
        states = self.by_type.get(item_type.lower(), {})
        return next((c for s, c in states.items() if s.lower() == state.lower()), "")

    def first(self, item_type: str, category: str) -> str | None:
        states = self.by_type.get(item_type.lower(), {})
        return next((s for s, c in states.items() if c == category), None)

    def named(self, item_type: str, category: str) -> list[str]:
        return [s for s, c in self.by_type.get(item_type.lower(), {}).items() if c == category]


def split_tags(value: object) -> set[str]:
    if not isinstance(value, str):
        return set()
    return {t.strip() for t in value.split(";") if t.strip()}


def read_history(item: Item, sprint_path: str, now: datetime) -> None:
    what = f"history of {item.id}"
    history = obj(ado("history", "get", str(item.id), "--fields", "states,changes"), what)
    spans = []
    for row in rows(history.get("states") or [], f"{what}: states"):
        since = stamp(row.get("since"))
        if since is not None:
            spans.append(Span(text(row, "state"), since, stamp(row.get("until"))))
    item.spans = spans
    latest: dict[str, object] = {}
    changes = rows(history.get("changes") or [], f"{what}: changes")
    for change in sorted(changes, key=lambda c: number(c, "rev", what)):
        at = stamp(change.get("date")) or now
        for entry in rows(change.get("fields") or [], f"{what}: fields"):
            name = text(entry, "field")
            latest[name] = entry.get("new")
            if name == "Iteration Path":
                item.entered = at if entry.get("new") == sprint_path else None
            elif name == "Tags":
                old, new = split_tags(entry.get("old")), split_tags(entry.get("new"))
                for tag in new - old:
                    item.tag_since[tag.lower()] = at
                for tag in old - new:
                    item.tag_since.pop(tag.lower(), None)
    item.remaining = real(latest.get("Remaining Work"))
    item.estimate = real(latest.get("Original Estimate"))
    item.points = next((real(latest[f]) for f in POINT_FIELDS if real(latest.get(f))), None)


def read_detail(item: Item) -> None:
    what = f"work item {item.id}"
    row = obj(
        ado(
            "workitem",
            "get",
            str(item.id),
            "--comments",
            "1",
            "--fields",
            "parent,children,pull_requests,branches,comments,acceptance_criteria",
        ),
        what,
    )
    parent, children = row.get("parent"), row.get("children")
    item.parent = parent if isinstance(parent, int) else None
    item.children = (
        [c for c in children if isinstance(c, int)] if isinstance(children, list) else []
    )
    item.prs = sorted({number(r, "id", what) for r in rows(row.get("pull_requests") or [], what)})
    item.branches = [text(b, "name") for b in rows(row.get("branches") or [], what)]
    comments = rows(row.get("comments") or [], f"{what}: comments")
    item.last_comment = max((d for c in comments if (d := stamp(c.get("date")))), default=None)
    item.criteria = re.sub(r"<[^>]+>|\s+", " ", text(row, "acceptance_criteria")).strip()


@dataclass
class Board:
    team: Team
    sprint: Sprint
    states: States
    now: datetime
    items: dict[int, Item]
    prs: dict[int, PullRequest]

    def parent_of(self, item: Item) -> Item | None:
        return self.items.get(item.parent) if item.parent is not None else None

    def tagged(self, item: Item, name: str) -> datetime | None:
        """When `item` got the tag `name`, reading the PBI's tags when the team tags PBIs."""
        holder = (self.parent_of(item) or item) if self.team.tags.on_pbi else item
        if not holder.has_tag(name):
            return None
        return holder.tag_since.get(name.lower(), holder.since or self.now)

    def stage(self, item: Item) -> tuple[str, datetime | None]:
        """The item's stage, and since when it has been in it."""
        tags = self.team.tags
        parent = self.parent_of(item)
        if parent is not None and parent.done:
            return "done", parent.since
        if item.is_qa:
            if item.done:
                return "done", item.since
            return ("qa" if item.active else "todo"), item.since
        for key, tag in (("deploy", tags.ready_for_deployment), ("qa", tags.ready_for_qa)):
            if (at := self.tagged(item, tag)) is not None:
                return key, at
        prs = [self.prs[p] for p in item.prs if p in self.prs]
        if any(p.status == "completed" for p in prs):
            return "merged", item.since if item.done else None
        if open_prs := [p for p in prs if p.status == "active" and not p.draft]:
            first = min((p.created for p in open_prs if p.created), default=None)
            return ("approved" if any(p.approved for p in open_prs) else "review"), first
        if item.done:
            return "done", item.since
        return ("doing" if item.category in ("InProgress", "Resolved") else "todo"), item.since

    def units(self) -> list[Item]:
        """The rows of work: tasks, and backlog items that have no tasks of their own."""
        return [
            i
            for i in self.items.values()
            if i.category != "Removed" and (i.is_task or (i.detailed and not i.children))
        ]

    def flags(self, item: Item) -> list[str]:
        stage, _ = self.stage(item)
        out = []
        if self.tagged(item, self.team.tags.blocked) is not None:
            out.append("blocked")
        if item.detailed and item.active:
            if item.last_comment is None or self.now - item.last_comment > timedelta(
                days=SILENT_DAYS
            ):
                out.append("silent")
            if item.since and self.now - item.since > timedelta(days=STALE_DAYS):
                out.append("stale")
        if not item.assignee and stage != "done":
            out.append("unassigned")
        start = day_start(self.sprint.start)
        if item.entered and item.entered > start + PLANNING_GRACE:
            out.append("added mid-sprint")
        unestimated = item.remaining is None and item.estimate is None
        work = item.detailed and item.is_task and not item.is_qa
        if work and unestimated and stage in ("todo", "doing"):
            out.append("no estimate")
        if stage == "merged" and self.team.tags.qa_when_merged:
            out.append("not tagged Ready for QA")
        left = self.sprint.days_left
        if left is not None and left <= LAST_DAYS and stage in OPEN_STAGES:
            out.append("likely to miss")
        return out


def gather(team: Team, sprint: Sprint, now: datetime, focus: set[str] | None) -> Board:
    """The sprint's work items, read in detail for `focus` (addresses), or all of them."""
    listed = rows(
        ado(
            "workitem",
            "list",
            "--iteration",
            sprint.path,
            *team.area_args(),
            *team.team_args(),
            "--limit",
            LIST_LIMIT,
            "--fields",
            "id,type,title,state,assignee,iteration,tags,priority",
        ),
        "the sprint's work items",
    )
    states = States()
    states.load(text(r, "type") for r in listed)
    items: dict[int, Item] = {}
    for row in listed:
        if text(row, "iteration") != sprint.path:
            continue
        item_type, state = text(row, "type"), text(row, "state")
        tags = row.get("tags")
        priority = row.get("priority")
        item = Item(
            id=number(row, "id", "work item"),
            type=item_type,
            title=text(row, "title"),
            state=state,
            category=states.category(item_type, state),
            assignee=text(row, "assignee") or None,
            iteration=text(row, "iteration"),
            tags=[str(t) for t in tags] if isinstance(tags, list) else [],
            priority=priority if isinstance(priority, int) else None,
        )
        items[item.id] = item
    wanted = [i for i in items.values() if wants_detail(i, focus)]

    def detail(item: Item) -> None:
        read_detail(item)
        read_history(item, sprint.path, now)
        item.detailed = True

    parallel(detail, wanted)
    if team.tags.on_pbi:
        parents = {i.parent for i in wanted if i.parent in items and not items[i.parent].detailed}
        parallel(
            lambda p: read_history(items[p], sprint.path, now), sorted(p for p in parents if p)
        )
    pr_ids = sorted({p for i in wanted for p in i.prs})
    prs = {pr.id: pr for pr in parallel(load_pr, pr_ids)}
    return Board(team, sprint, states, now, items, prs)


def wants_detail(item: Item, focus: set[str] | None) -> bool:
    if focus is None:
        return True
    if item.assignee:
        return item.assignee.lower() in focus
    return item.is_task and not item.done


# --- output ----------------------------------------------------------------


@dataclass(frozen=True)
class Styled:
    text: str
    color: str


Cell = str | Styled


@dataclass(frozen=True)
class Heading:
    text: str


@dataclass(frozen=True)
class Para:
    text: Cell


@dataclass(frozen=True)
class Bullets:
    lines: Sequence[Cell]


@dataclass(frozen=True)
class Table:
    headers: list[str]
    rows: list[list[Cell]]
    flexible: int  # the column that shrinks to fit the terminal


Block = Heading | Para | Bullets | Table

COLORS = {
    "bold": "1",
    "dim": "2",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
}
STAGE_COLORS = {
    "todo": "dim",
    "doing": "blue",
    "review": "yellow",
    "approved": "green",
    "merged": "magenta",
    "qa": "cyan",
    "deploy": "cyan",
    "done": "green",
}


def plain(cell: Cell) -> str:
    return cell.text if isinstance(cell, Styled) else cell


def paint(cell: Cell, color: bool) -> str:
    if not isinstance(cell, Styled) or not color:
        return plain(cell)
    return f"\033[{COLORS[cell.color]}m{cell.text}\033[0m"


def clip(value: str, width: int) -> str:
    return value if len(value) <= width else value[: max(1, width - 1)] + "…"


def markdown(blocks: list[Block]) -> str:
    out: list[str] = []
    for block in blocks:
        if isinstance(block, Heading):
            out += [f"### {block.text}", ""]
        elif isinstance(block, Para):
            out += [plain(block.text), ""]
        elif isinstance(block, Bullets):
            out += [f"- {plain(line)}" for line in block.lines] + [""]
        else:
            cells = [[plain(c).replace("|", "\\|") for c in row] for row in block.rows]
            out.append("| " + " | ".join(block.headers) + " |")
            out.append("|" + "|".join("---" for _ in block.headers) + "|")
            out += ["| " + " | ".join(row) + " |" for row in cells]
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def terminal(blocks: list[Block], color: bool) -> str:
    width = shutil.get_terminal_size((100, 24)).columns
    out: list[str] = []
    for block in blocks:
        if isinstance(block, Heading):
            out += ["", paint(Styled(block.text, "cyan"), color) if color else block.text.upper()]
        elif isinstance(block, Para):
            out.append(paint(block.text, color))
        elif isinstance(block, Bullets):
            out += [f"  • {paint(line, color)}" for line in block.lines]
        else:
            out += box(block, width, color)
    return "\n".join(out).lstrip("\n") + "\n"


def box(block: Table, width: int, color: bool) -> list[str]:
    widths = [len(h) for h in block.headers]
    for row in block.rows:
        widths = [max(w, len(plain(c))) for w, c in zip(widths, row, strict=True)]
    # Shrink the flexible column to 24, then the widest of the rest, until the table fits.
    while sum(widths) + 3 * len(widths) + 1 > width:
        floors = [24 if n == block.flexible else 10 for n in range(len(widths))]
        over = [n for n, w in enumerate(widths) if w > floors[n]]
        if not over:
            break
        pick = block.flexible if block.flexible in over else max(over, key=lambda n: widths[n])
        widths[pick] -= 1

    def line(left: str, mid: str, right: str) -> str:
        return left + mid.join("─" * (w + 2) for w in widths) + right

    def cells(row: list[Cell]) -> str:
        parts = []
        for cell, w in zip(row, widths, strict=True):
            clipped = clip(plain(cell), w)
            shown = Styled(clipped, cell.color) if isinstance(cell, Styled) else clipped
            parts.append(" " + paint(shown, color) + " " * (w - len(clipped) + 1))
        return "│" + "│".join(parts) + "│"

    header = [Styled(h, "bold") for h in block.headers]
    out = [line("┌", "┬", "┐"), cells(list(header)), line("├", "┼", "┤")]
    out += [cells(row) for row in block.rows]
    out.append(line("└", "┴", "┘"))
    return out


def emit(blocks: list[Block], data: object, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(data, indent=1, default=str))
    elif fmt == "term":
        print(terminal(blocks, color=not os.environ.get("NO_COLOR")), end="")
    else:
        print(markdown(blocks), end="")


def days_since(moment: datetime | None, now: datetime) -> str:
    if moment is None:
        return "-"
    days = (now - moment).days
    return "<1 d" if days < 1 else f"{days} d"


def hours(value: float | None) -> str:
    return "-" if value is None else f"{value:g} h"


def item_row(board: Board, item: Item, *, owner: bool) -> list[Cell]:
    stage, since = board.stage(item)
    flags = board.flags(item)
    row: list[Cell] = [f"#{item.id}", item.title]
    if owner:
        row.append(board.team.display(item.assignee))
    row += [
        Styled(STAGES[stage], STAGE_COLORS[stage]),
        days_since(since, board.now),
        ", ".join(f"!{p}" for p in item.prs) or "-",
        Styled(", ".join(flags), "red" if "blocked" in flags else "yellow") if flags else "",
    ]
    return row


def item_data(board: Board, item: Item) -> dict[str, object]:
    stage, since = board.stage(item)
    return {
        "id": item.id,
        "type": item.type,
        "title": item.title,
        "qa": item.is_qa,
        "state": item.state,
        "assignee": item.assignee,
        "parent": item.parent,
        "stage": stage,
        "stage_since": since,
        "prs": item.prs,
        "remaining_work": item.remaining,
        "flags": board.flags(item),
    }


def stage_order(board: Board) -> Callable[[Item], tuple[int, int]]:
    order = list(STAGES)
    return lambda i: (order.index(board.stage(i)[0]), i.id)


# --- views -------------------------------------------------------------------


@dataclass(frozen=True)
class Fix:
    kind: str
    item: int | None
    what: str
    why: str
    write: str | None


def headline(board: Board, today: date) -> str:
    sprint = board.sprint
    total = len(sprint.working_days())
    left = sprint.days_left if sprint.days_left is not None else "?"
    return (
        f"{sprint.name}: day {sprint.day_number(today)} of {total}, {left} working days left"
        f" (ends {sprint.finish.isoformat()})"
    )


def reviews_owed(person: str, author: str) -> list[dict[str, object]]:
    """The pull requests waiting on `person`'s vote that `author` (their address) did not open."""
    owed = rows(
        ado(
            "pr",
            "list",
            "--reviewer",
            person,
            "--vote",
            "none",
            "--draft",
            "false",
            "--limit",
            "100",
            "--fields",
            "id,repo,title,author,created",
        ),
        "reviews owed",
    )
    owed = [r for r in owed if text(r, "author").lower() != author.lower()]
    return sorted(owed, key=lambda r: text(r, "created"))


def time_line(team: Team, person: str, today: date) -> tuple[str, list[date], dict[date, float]]:
    """Your hours per working day since Monday of last week, and the days not full."""
    first = today - timedelta(days=today.weekday() + 7)
    days = weekdays(first, today)
    if not team.timelog.ported:
        return "Time: not set up yet (team.toml [timelog]; see docs/porting.md)", [], {}
    logged: dict[date, float] = defaultdict(float)
    for entry in team.timelog.entries(person, first, today):
        logged[entry.day] += entry.hours
    gaps = [d for d in days if logged[d] < team.timelog.day_hours]
    shown = " · ".join(f"{d:%a} {logged[d]:g}" if logged[d] else f"{d:%a} —" for d in days)
    return f"Time: {shown}", gaps, dict(logged)


def since_last_day(today: date) -> tuple[date, list[str]]:
    last = today - timedelta(days=1)
    while last.weekday() >= 5:
        last -= timedelta(days=1)
    events = rows(
        ado(
            "activity",
            "list",
            "--person",
            "@me",
            "--since",
            rfc3339(day_start(last)),
            "--limit",
            "200",
            "--fields",
            "at,kind,action,id,title,count",
        ),
        "your activity",
    )
    grouped: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    commits: Counter[str] = Counter()
    for event in sorted(events, key=lambda e: text(e, "at")):
        kind, ident = text(event, "kind"), str(event.get("id"))
        if kind == "commit":
            commits[ident.split("@", 1)[0]] += 1
            continue
        key = (kind, ident, text(event, "title"))
        action = text(event, "action")
        if action and action not in grouped[key]:
            grouped[key].append(action)
    lines = []
    for (kind, ident, title), actions in grouped.items():
        mark = {"pr": "!", "workitem": "#", "comment": "#"}.get(kind, f"{kind} ")
        lines.append(f"{mark}{ident} {title}: {', '.join(actions)}")
    lines += [f"{n} commits in {repo}" for repo, n in sorted(commits.items())]
    return last, lines


def ranks(team: Team) -> dict[int, int]:
    found = rows(
        ado(
            "backlog",
            "list",
            "--level",
            "stories",
            *team.team_args(),
            "--limit",
            LIST_LIMIT,
            "--fields",
            "id,rank",
        ),
        "the backlog",
    )
    return {number(r, "id", "backlog item"): number(r, "rank", "backlog item") for r in found}


def next_up(board: Board, limit: int) -> tuple[list[Item], str | None]:
    """The unassigned tasks not started, by their PBI's place on the backlog."""
    candidates = [
        i
        for i in board.units()
        if i.is_task and not i.is_qa and not i.assignee and board.stage(i)[0] == "todo"
    ]
    note = None
    try:
        rank = ranks(board.team)
    except SprintError:
        rank, note = {}, "the backlog's order could not be read, so this is by priority"
    far = sys.maxsize

    def key(item: Item) -> tuple[int, int, int]:
        place = rank.get(item.parent or item.id, far)
        return place, item.priority or 9, item.id

    return sorted(candidates, key=key)[:limit], note


def view_me(
    team: Team, which: str, now: datetime, today: date
) -> tuple[list[Block], dict[str, object]]:
    me = whoami()
    sprint = load_sprint(team, which)
    board = gather(team, sprint, now, {me.lower()})
    mine = sorted(
        (i for i in board.units() if (i.assignee or "").lower() == me.lower()),
        key=stage_order(board),
    )
    load = sprint.load(me)
    owed = reviews_owed("@me", me)
    authored = rows(
        ado("pr", "list", "--author", "@me", "--limit", "100", "--fields", "id"), "your PRs"
    )
    authored_ids = [number(r, "id", "pull request") for r in authored]
    for pr in parallel(load_pr, [p for p in authored_ids if p not in board.prs]):
        board.prs[pr.id] = pr
    my_prs = [board.prs[p] for p in authored_ids]
    time_text, gaps, _ = time_line(team, "@me", today)
    last_day, activity = since_last_day(today)
    picks, pick_note = next_up(board, 1)
    outside = outside_sprint(board)

    blocks: list[Block] = [Heading(f"Your sprint: {sprint.name}"), Para(headline(board, today))]
    if load is not None:
        capacity = hours(load.capacity_left) if load.capacity_left is not None else "not set"
        blocks.append(Para(f"Remaining: {hours(load.remaining)} against {capacity} capacity left"))
    blocks.append(Para(time_text))

    needs: list[Cell] = []
    for item in mine:
        flags = board.flags(item)
        if "blocked" in flags:
            needs.append(Styled(f"#{item.id} {item.title} is blocked", "red"))
    for pr in owed[:5]:
        waited = days_since(stamp(pr.get("created")), now)
        needs.append(
            f"Review !{pr.get('id')} {text(pr, 'title')} by {text(pr, 'author')} (waiting {waited})"
        )
    if len(owed) > 5:
        needs.append(f"…and {len(owed) - 5} more reviews you owe")
    for pr in my_prs:
        if pr.status == "active" and pr.open_threads:
            needs.append(f"!{pr.id} {pr.title} has {pr.open_threads} open threads")
    for item in mine:
        if "silent" in board.flags(item):
            quiet = f"in {days_since(item.last_comment, now)}" if item.last_comment else "yet"
            needs.append(f"#{item.id} {item.title} has no note {quiet}")
    blocks += [Heading("Needs you"), Bullets(needs or ["Nothing right now."])]

    blocks.append(Heading("Your items by stage"))
    if mine:
        blocks.append(
            Table(
                ["Item", "Title", "Stage", "In stage", "PR", "Flags"],
                [item_row(board, i, owner=False) for i in mine],
                1,
            )
        )
    else:
        blocks.append(Para("Nothing in this sprint is assigned to you."))

    blocks.append(Heading("Next to pick up"))
    if picks:
        pick = picks[0]
        parent = board.parent_of(pick)
        under = f" under #{parent.id} {parent.title}" if parent else ""
        blocks.append(Para(f"#{pick.id} {pick.title}{under} (/dev-start {pick.id} claims it)"))
    else:
        blocks.append(Para("No unassigned task is waiting in this sprint."))
    if pick_note:
        blocks.append(Para(Styled(pick_note, "dim")))

    blocks += [Heading(f"Since {last_day:%A}"), Bullets(activity or ["No activity in ADO."])]

    fixes = my_fixes(board, mine, my_prs, outside, gaps)
    if fixes:
        blocks.append(Heading("Fixes /dev-sprint can make"))
        blocks.append(
            Table(
                ["#", "Fix", "Why", "Write"],
                [
                    [str(n), f.what, f.why, f"`{f.write}`" if f.write else "drafted by the agent"]
                    for n, f in enumerate(fixes, 1)
                ],
                2,
            )
        )
    data = {
        "me": me,
        "sprint": sprint,
        "items": [item_data(board, i) for i in mine],
        "reviews_owed": owed,
        "my_prs": my_prs,
        "time_gaps": gaps,
        "next": [item_data(board, i) for i in picks],
        "since": last_day,
        "activity": activity,
        "fixes": fixes,
    }
    return blocks, data


def outside_sprint(board: Board) -> list[dict[str, object]]:
    """Your active tasks planned in no sprint, or in one that has ended."""
    states = board.states
    states.load(["Task"])
    active = states.named("Task", "InProgress")
    if not active:
        return []
    found = rows(
        ado(
            "workitem",
            "list",
            "--assignee",
            "@me",
            "--type",
            "Task",
            *[a for s in active for a in ("--state", s)],
            "--limit",
            "200",
            "--fields",
            "id,title,state,iteration",
        ),
        "your active tasks",
    )
    sprints = rows(
        ado("sprint", "list", *board.team.team_args(), "--fields", "path,timeframe"),
        "the team's sprints",
    )
    planned = {text(s, "path") for s in sprints if text(s, "timeframe") in ("current", "future")}
    return [r for r in found if text(r, "iteration") not in planned]


def my_fixes(
    board: Board,
    mine: list[Item],
    my_prs: list[PullRequest],
    outside: list[dict[str, object]],
    gaps: list[date],
) -> list[Fix]:
    tags = board.team.tags
    fixes = []
    for item in mine:
        stage, _ = board.stage(item)
        flags = board.flags(item)
        if "silent" in flags:
            fixes.append(
                Fix("note", item.id, f"Progress note on #{item.id}", "no note in over 3 days", None)
            )
        if "blocked" in flags:
            fixes.append(
                Fix(
                    "blocked",
                    item.id,
                    f"Check Blocked on #{item.id}",
                    f"tagged {days_since(board.tagged(item, tags.blocked), board.now)}"
                    " ago; clear it if it no longer holds",
                    f'update {item.id} --remove-tag "{tags.blocked}"',
                )
            )
        if "not tagged Ready for QA" in flags:
            target = item.parent if tags.on_pbi and item.parent else item.id
            fixes.append(
                Fix(
                    "ready-for-qa",
                    target,
                    f"Tag #{target} {tags.ready_for_qa}",
                    "its pull request has merged",
                    f'update {target} --add-tag "{tags.ready_for_qa}"',
                )
            )
        if stage == "todo" and (item.prs or item.branches) and not item.done:
            state = board.states.first(item.type, "InProgress")
            if state:
                fixes.append(
                    Fix(
                        "start",
                        item.id,
                        f"Move #{item.id} to {state}",
                        "it has a branch or pull request",
                        f'update {item.id} --state "{state}"',
                    )
                )
        if stage in ("review", "approved") and item.remaining:
            fixes.append(
                Fix(
                    "remaining",
                    item.id,
                    f"Remaining Work on #{item.id} to 0",
                    f"{hours(item.remaining)} left though its PR is open",
                    f"update {item.id} --remaining 0",
                )
            )
        if "no estimate" in flags and stage == "doing":
            fixes.append(
                Fix(
                    "remaining",
                    item.id,
                    f"Remaining Work on #{item.id}",
                    "in progress with no estimate; ask how many hours are left",
                    f"update {item.id} --remaining <hours>",
                )
            )
    for row in outside:
        item_id = number(row, "id", "task")
        fixes.append(
            Fix(
                "sprint",
                item_id,
                f"Move #{item_id} into {board.sprint.name}",
                f"{text(row, 'state')} but planned in {text(row, 'iteration') or 'no sprint'}",
                f'update {item_id} --iteration "{board.sprint.path}"',
            )
        )
    for pr in my_prs:
        if pr.status == "active" and not pr.work_items:
            found = re.match(r"(?:refs/heads/)?(?:\w+/)?(\d+)-", pr.source)
            target = f"--workitem {found.group(1)}" if found else "--workitem <task>"
            fixes.append(
                Fix(
                    "link",
                    None,
                    f"Link !{pr.id} to its task",
                    "it links no work item",
                    f"pr-link {pr.id} {target}",
                )
            )
    if gaps:
        days = ", ".join(f"{d:%a %d}" for d in gaps)
        fixes.append(
            Fix(
                "time",
                None,
                f"Fill time for {days}",
                "the days are not full",
                "sprint.py time, then timelog per entry",
            )
        )
    return fixes


def view_person(
    team: Team, which: str, query: str, now: datetime, today: date
) -> tuple[list[Block], dict[str, object]]:
    person = team.find(query)
    address = person.address if person else query
    name = person.name if person else query
    sprint = load_sprint(team, which)
    board = gather(team, sprint, now, {address.lower()})
    theirs = sorted(
        (i for i in board.units() if (i.assignee or "").lower() == address.lower()),
        key=stage_order(board),
    )
    if not theirs and person is None:
        raise SprintError(f"{query!r} is not in team.toml and has nothing in {sprint.name}")
    load = sprint.load(address)
    owed = reviews_owed(address, address)
    blocks: list[Block] = [Heading(f"{name}: {sprint.name}"), Para(headline(board, today))]
    if load is not None:
        capacity = hours(load.capacity_left) if load.capacity_left is not None else "not set"
        blocks.append(Para(f"Remaining: {hours(load.remaining)} against {capacity} capacity left"))
    needs: list[Cell] = [
        Styled(f"#{i.id} {i.title} is blocked", "red")
        for i in theirs
        if "blocked" in board.flags(i)
    ]
    needs += [f"Owes a review on !{r.get('id')} {text(r, 'title')}" for r in owed[:5]]
    needs += [f"#{i.id} {i.title} is silent" for i in theirs if "silent" in board.flags(i)]
    blocks += [Heading("Needs attention"), Bullets(needs or ["Nothing right now."])]
    blocks.append(Heading("Items by stage"))
    if theirs:
        blocks.append(
            Table(
                ["Item", "Title", "Stage", "In stage", "PR", "Flags"],
                [item_row(board, i, owner=False) for i in theirs],
                1,
            )
        )
    else:
        blocks.append(Para("Nothing in this sprint is assigned to them."))
    data = {
        "person": address,
        "sprint": sprint,
        "items": [item_data(board, i) for i in theirs],
        "reviews_owed": owed,
    }
    return blocks, data


def view_team(
    team: Team, which: str, now: datetime, today: date
) -> tuple[list[Block], dict[str, object]]:
    sprint = load_sprint(team, which)
    board = gather(team, sprint, now, None)
    units = sorted(board.units(), key=stage_order(board))
    addresses = {p.address.lower(): p for p in team.people}
    for item in units:
        if item.assignee and item.assignee.lower() not in addresses:
            addresses[item.assignee.lower()] = Person(item.assignee, item.assignee)
    people = sorted(addresses.values(), key=lambda p: p.name.lower())
    owed = dict(
        zip(
            [p.address for p in people],
            parallel(lambda p: len(reviews_owed(p.address, p.address)), people),
            strict=True,
        )
    )
    unfilled: dict[str, str] = {}
    if team.timelog.ported:

        def gap_count(person: Person) -> str:
            _, gaps, _ = time_line(team, person.address, today)
            return str(len(gaps))

        unfilled = dict(zip([p.address for p in people], parallel(gap_count, people), strict=True))

    blocks: list[Block] = [Heading(f"Team sprint: {sprint.name}"), Para(headline(board, today))]
    capacity = [p.capacity_left for p in sprint.loads if p.capacity_left is not None]
    work = f"Points: {sprint.points_done:g} done of {sprint.points:g}."
    if capacity:
        left = sum(capacity)
        verdict = (
            "on track" if sprint.remaining <= left else (f"short by {sprint.remaining - left:g} h")
        )
        work += f" Remaining {hours(sprint.remaining)} against {hours(left)} capacity left:"
        work += f" {verdict}."
    else:
        work += f" Remaining {hours(sprint.remaining)}; no capacity is set."
    blocks.append(Para(work))

    people_rows: list[list[Cell]] = []
    for person in people:
        theirs = [i for i in units if (i.assignee or "").lower() == person.address.lower()]
        counts = Counter(board.stage(i)[0] for i in theirs)
        stages = ", ".join(f"{counts[s]} {STAGES[s].lower()}" for s in STAGES if counts[s])
        load = sprint.load(person.address)
        people_rows.append(
            [
                person.name,
                str(len(theirs)),
                stages or "-",
                hours(load.remaining) if load else "-",
                hours(load.capacity_left) if load and load.capacity_left is not None else "-",
                str(owed.get(person.address, 0)),
                unfilled.get(person.address, "-"),
            ]
        )
    headers = [
        "Person",
        "Items",
        "By stage",
        "Remaining",
        "Capacity left",
        "Owes reviews",
        "Days unfilled",
    ]
    blocks.append(Heading("People"))
    if people_rows:
        blocks.append(Table(headers, people_rows, 2))
    else:
        blocks.append(Para("No one is assigned work; list the team in team.toml [[people]]."))

    attention: dict[str, list[str]] = defaultdict(list)
    for item in units:
        owner = f" ({team.display(item.assignee)})" if item.assignee else ""
        label = f"#{item.id} {item.title}{owner}"
        for flag in board.flags(item):
            if flag != "likely to miss":
                attention[flag].append(label)
    waiting = sorted(
        (p for p in board.prs.values() if p.status == "active" and not p.draft and not p.approved),
        key=lambda p: p.created or now,
    )
    lines: list[Cell] = []
    if attention["blocked"]:
        lines.append(Styled(f"Blocked: {'; '.join(attention['blocked'])}", "red"))
    if waiting:
        lines.append(
            "Waiting longest for review: "
            + "; ".join(f"!{p.id} {p.title} ({days_since(p.created, now)})" for p in waiting[:5])
        )
    for flag, label in (
        ("silent", "Silent (no note in 3 days)"),
        ("stale", "Stale (same state over 7 days)"),
        ("not tagged Ready for QA", f"Merged, not tagged {team.tags.ready_for_qa}"),
        ("unassigned", "Unassigned"),
        ("added mid-sprint", "Added mid-sprint"),
        ("no estimate", "No estimate"),
    ):
        if attention[flag]:
            lines.append(f"{label}: {'; '.join(attention[flag])}")
    backlog = [i for i in board.items.values() if not i.is_task and i.detailed and not i.done]
    if missing := [f"#{i.id} {i.title}" for i in backlog if not i.criteria]:
        lines.append(f"No acceptance criteria: {'; '.join(missing)}")
    if unpointed := [f"#{i.id} {i.title}" for i in backlog if i.points is None]:
        lines.append(f"No points: {'; '.join(unpointed)}")
    blocks += [Heading("Needs attention"), Bullets(lines or ["Nothing right now."])]

    if sprint.days_left is not None and sprint.days_left <= LAST_DAYS:
        missing_work = [i for i in units if "likely to miss" in board.flags(i)]
        blocks.append(Heading("Likely to miss"))
        if missing_work:
            blocks.append(
                Table(
                    ["Item", "Title", "Owner", "Stage", "In stage", "PR", "Flags"],
                    [item_row(board, i, owner=True) for i in missing_work],
                    1,
                )
            )
        else:
            blocks.append(Para("Everything open is past review."))
    keys = (
        "person",
        "items",
        "stages",
        "remaining",
        "capacity_left",
        "owes_reviews",
        "days_unfilled",
    )
    data = {
        "sprint": sprint,
        "people": [dict(zip(keys, map(plain, row), strict=True)) for row in people_rows],
        "attention": attention,
        "waiting_for_review": [p.id for p in waiting],
        "items": [item_data(board, i) for i in units],
    }
    return blocks, data


# --- time --------------------------------------------------------------------


@dataclass(frozen=True)
class Proposal:
    day: date
    task: int
    hours: float


def active_days(
    spans: list[Span], category: Callable[[str], str], now: datetime, days: list[date]
) -> set[date]:
    out = set()
    for span in spans:
        if category(span.state) != "InProgress":
            continue
        until = span.until or now
        for day in days:
            if span.since < day_start(day + timedelta(days=1)) and until > day_start(day):
                out.add(day)
    return out


def split(blocks: int, tasks: list[int], weight: Counter[int]) -> dict[int, int]:
    """Whole blocks evenly across tasks, at least one each, the leftover to the most active."""
    ranked = sorted(tasks, key=lambda t: (-weight[t], t))
    if blocks < len(ranked):
        return dict.fromkeys(ranked[:blocks], 1)
    base, extra = divmod(blocks, len(ranked))
    shares = dict.fromkeys(ranked, base)
    shares[ranked[0]] += extra
    return shares


def view_time(
    team: Team, now: datetime, today: date, first: date | None
) -> tuple[list[Block], dict[str, object]]:
    log = team.timelog
    if not log.ported:
        raise SprintError(
            "time logging is not set up: team.toml [timelog] has no `list`"
            " command yet. docs/porting.md says how to fill it in.",
            EXIT_SETUP,
        )
    first = first or today - timedelta(days=today.weekday() + 7)
    days = weekdays(first, today)
    entries = log.entries("@me", first, today)
    logged: dict[date, float] = defaultdict(float)
    for entry in entries:
        logged[entry.day] += entry.hours

    states = States()
    states.load(["Task"])
    active_states = states.named("Task", "InProgress")
    fields = "id,title,state"
    changed = rows(
        ado(
            "workitem",
            "list",
            "--assignee",
            "@me",
            "--type",
            "Task",
            "--since",
            rfc3339(day_start(first)),
            "--limit",
            LIST_LIMIT,
            "--fields",
            fields,
        ),
        "your tasks",
    )
    current = rows(
        ado(
            "workitem",
            "list",
            "--assignee",
            "@me",
            "--type",
            "Task",
            *[a for s in active_states for a in ("--state", s)],
            "--limit",
            LIST_LIMIT,
            "--fields",
            fields,
        ),
        "your active tasks",
    )
    titles = {number(r, "id", "task"): text(r, "title") for r in [*changed, *current]}
    tasks = [
        Item(
            id=i,
            type="Task",
            title=t,
            state="",
            category="",
            assignee=None,
            iteration="",
            tags=[],
            priority=None,
        )
        for i, t in sorted(titles.items())
    ]
    parallel(lambda item: read_history(item, "", now), tasks)
    active = {
        item.id: active_days(item.spans, lambda s: states.category("Task", s), now, days)
        for item in tasks
    }

    events = rows(
        ado(
            "activity",
            "list",
            "--person",
            "@me",
            "--since",
            rfc3339(day_start(first)),
            "--limit",
            LIST_LIMIT,
            "--fields",
            "at,kind,id",
        ),
        "your activity",
    )
    pr_ids = sorted(
        {
            int(str(e.get("id")))
            for e in events
            if e.get("kind") == "pr" and str(e.get("id")).isdigit()
        }
    )
    pr_items = {pr.id: pr.work_items for pr in parallel(load_pr, pr_ids)}
    weight: dict[date, Counter[int]] = defaultdict(Counter)
    for event in events:
        at = stamp(event.get("at"))
        ident = str(event.get("id"))
        if at is None or not ident.isdigit():
            continue
        day = at.astimezone().date()
        linked = pr_items.get(int(ident), []) if event.get("kind") == "pr" else [int(ident)]
        for task in linked:
            if task in titles:
                weight[day][task] += 1

    holidays: set[date] = set()
    holiday_note = None
    try:
        sprints = rows(
            ado("sprint", "list", *team.team_args(), "--fields", "path,start,finish"),
            "the team's sprints",
        )
        overlapping = [
            text(s, "path")
            for s in sprints
            if (s_start := stamp(s.get("start")))
            and (s_end := stamp(s.get("finish")))
            and s_start.date() <= today
            and s_end.date() >= first
        ]
        for sprint in parallel(lambda p: load_sprint(team, p), overlapping):
            holidays |= sprint.days_off
    except SprintError:
        holiday_note = "Team days off could not be read; mark them yourself."

    block = log.block_hours
    proposals: list[Proposal] = []
    table_rows: list[list[Cell]] = []
    for day in days:
        have = logged[day]
        gap = log.day_hours - have
        note: Cell = ""
        planned: dict[int, int] = {}
        if gap < 0:
            note = Styled(f"over by {-gap:g} h; fix it in the time tracker", "red")
        elif gap == 0:
            note = Styled("full", "green")
        elif day in holidays:
            if log.time_off_task:
                planned = {log.time_off_task: int(gap // block)}
                note = "team day off"
            else:
                note = Styled("team day off; team.toml names no time-off ticket", "yellow")
        else:
            working = [t for t, on in active.items() if day in on]
            if working:
                planned = split(int(gap // block), working, weight[day])
                if gap % block:
                    note = Styled(f"{gap % block:g} h does not fit a {block:g} h block", "yellow")
            else:
                note = Styled("no task was active; say where the time went", "yellow")
        proposals += [Proposal(day, t, n * block) for t, n in planned.items() if n]
        shown = ", ".join(f"#{t} {n * block:g} h" for t, n in planned.items() if n) or "-"
        table_rows.append([f"{day:%a %Y-%m-%d}", f"{have:g} h", shown, note])

    blocks: list[Block] = [
        Heading(f"Your time since {first:%A %Y-%m-%d}"),
        Para(f"Each working day holds exactly {log.day_hours:g} h, in {block:g} h blocks."),
        Table(["Day", "Logged", "Proposed", "Note"], table_rows, 2),
    ]
    if holiday_note:
        blocks.append(Para(Styled(holiday_note, "yellow")))
    named = {p.task for p in proposals}
    if named:
        off = {log.time_off_task: "time off"} if log.time_off_task else {}
        blocks += [
            Heading("Tasks"),
            Bullets([f"#{t} {titles.get(t) or off.get(t, '')}" for t in sorted(named)]),
        ]
    data = {
        "since": first,
        "day_hours": log.day_hours,
        "block_hours": block,
        "logged": {d.isoformat(): h for d, h in logged.items()},
        "entries": entries,
        "proposal": proposals,
    }
    return blocks, data


# --- next and reviewers ------------------------------------------------------------


def view_next(
    team: Team, which: str, now: datetime, limit: int
) -> tuple[list[Block], dict[str, object]]:
    sprint = load_sprint(team, which)
    board = gather(team, sprint, now, set())
    picks, note = next_up(board, limit)
    blocks: list[Block] = [Heading(f"Next to pick up in {sprint.name}")]
    if picks:
        table_rows: list[list[Cell]] = []
        for item in picks:
            parent = board.parent_of(item)
            table_rows.append(
                [f"#{item.id}", item.title, f"#{parent.id} {parent.title}" if parent else "-"]
            )
        blocks.append(Table(["Task", "Title", "Under"], table_rows, 1))
    else:
        blocks.append(Para("No unassigned task is waiting in this sprint."))
    if note:
        blocks.append(Para(Styled(note, "dim")))
    return blocks, {"sprint": sprint, "next": [item_data(board, i) for i in picks]}


def view_reviewers(team: Team, files: list[str]) -> tuple[list[Block], dict[str, object]]:
    me = whoami().lower()
    done = subprocess.run(
        [
            "git",
            "log",
            "--no-merges",
            f"--since={REVIEW_MONTHS}.months",
            "--format=%ae%x09%an%x09%at",
            "--",
            *files,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise SprintError(f"git log failed:\n{done.stderr.strip()}")
    latest: dict[str, datetime] = {}
    names: dict[str, str] = {}
    for line in done.stdout.splitlines():
        email, name, at = [*line.split("\t"), "", "", ""][:3]
        if not at.isdigit():
            continue
        when = datetime.fromtimestamp(int(at), UTC)
        key = email.lower()
        names.setdefault(key, name)
        latest[key] = max(when, latest.get(key, when))
    if team.people:
        pool = [p for p in team.people if p.address.lower() != me]
    else:
        pool = [Person(names[e], e) for e in latest if e != me]

    def touched(person: Person) -> datetime | None:
        by_address = latest.get(person.address.lower())
        if by_address:
            return by_address
        return next((latest[e] for e, n in names.items() if n.lower() == person.name.lower()), None)

    owed = parallel(lambda p: len(reviews_owed(p.address, p.address)), pool)
    ranked = sorted(
        zip(pool, owed, strict=True),
        key=lambda pair: (-(t.date().toordinal() if (t := touched(pair[0])) else 0), pair[1]),
    )[:2]
    suggestions = []
    for person, count in ranked:
        when = touched(person)
        why = f"last changed these files {when:%Y-%m-%d}" if when else "has not changed them"
        suggestions.append(
            {"name": person.name, "address": person.address, "why": f"{why}; owes {count} reviews"}
        )
    blocks: list[Block] = [Heading("Suggested reviewers")]
    blocks.append(
        Bullets(
            [f"{s['name']}: {s['why']}" for s in suggestions]
            or ["No teammate found; add the roster to team.toml."]
        )
    )
    return blocks, {"files": files, "suggestions": suggestions}


# --- main ------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--format",
        choices=("auto", "term", "md", "json"),
        default="auto",
        help="auto: colored tables in a terminal, Markdown when piped",
    )
    verbs = parser.add_subparsers(dest="verb", required=True, metavar="VERB")
    p = verbs.add_parser("view", help="your sprint, the team's, or one teammate's")
    scope = p.add_mutually_exclusive_group()
    scope.add_argument("--team", action="store_true", help="the whole team")
    scope.add_argument("--person", help="one teammate, by name or address")
    p.add_argument("--sprint", default="@current", help="@current, @previous, a path or a name")
    p = verbs.add_parser("time", help="your hours per day, and a proposal for the gaps")
    p.add_argument("--since", type=date.fromisoformat, help="first day (default: Monday last week)")
    p = verbs.add_parser("next", help="the top unassigned tasks in the current sprint")
    p.add_argument("--limit", type=int, default=3)
    p.add_argument("--sprint", default="@current", help="@current, @next, a path or a name")
    p = verbs.add_parser("reviewers", help="teammates to review a change to these files")
    p.add_argument("files", nargs="+")
    args = parser.parse_args(argv)
    fmt = args.format
    if fmt == "auto":
        fmt = "term" if sys.stdout.isatty() else "md"
    now = datetime.now(UTC).replace(microsecond=0)
    today = now.astimezone().date()
    try:
        team = load_team()
        if args.verb == "view" and args.team:
            blocks, data = view_team(team, args.sprint, now, today)
        elif args.verb == "view" and args.person:
            blocks, data = view_person(team, args.sprint, args.person, now, today)
        elif args.verb == "view":
            blocks, data = view_me(team, args.sprint, now, today)
        elif args.verb == "time":
            blocks, data = view_time(team, now, today, args.since)
        elif args.verb == "next":
            blocks, data = view_next(team, args.sprint, now, args.limit)
        else:
            blocks, data = view_reviewers(team, args.files)
    except SprintError as err:
        print(f"sprint.py: {err}", file=sys.stderr)
        return err.code
    emit(blocks, as_json(data), fmt)
    return 0


def as_json(value: object) -> object:
    if isinstance(value, dict):
        return {str(k): as_json(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [as_json(v) for v in value]
    if isinstance(value, datetime):
        return rfc3339(value)
    if isinstance(value, date):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return as_json({f.name: getattr(value, f.name) for f in fields(value)})
    return value


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
