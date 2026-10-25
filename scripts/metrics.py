#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Measure the team's delivery from Azure DevOps, for the weekly baseline.

  cycle time        hours from a work item first entering an active state to
                    entering a done state, for items finished in the window
  review turnaround hours from a pull request opening to its first vote by
                    someone other than its author, for pull requests opened in
                    the window; ones still waiting are counted apart
  silent items      items in an active state with no comment in --silent-days
  stale items       items in the same active state for over --stale-days

A skill that moves none of these numbers is cut. Run this weekly with --log,
which appends one JSON line per run, so the baseline and every week after it
sit in one file. Reads only; every call goes through agent-cli.

The team and area default to shared/team.toml's [ado] values. In a terminal
the summary is a colored table; piped, it is Markdown; --json prints the data.

Examples:
  metrics.py
  metrics.py --since 14d --area 'web\\orders' --log ~/team-metrics.jsonl
  metrics.py --type Task --type Bug --json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit("metrics.py needs Python 3.11 or later; run it with `uv run`.")

import tomllib

TEAM_FILE = Path(__file__).resolve().parent.parent / "shared" / "team.toml"

# ADO's state categories, which every process maps its own state names onto.
ACTIVE_CATEGORY = "InProgress"
DONE_CATEGORY = "Completed"
VOTES = frozenset({"approved", "approved with suggestions", "waiting for author", "rejected"})
SPAN_RE = re.compile(r"^(\d+)([hdw])$")
LIST_LIMIT = "1000"


class MetricsError(Exception):
    pass


def agent_cli(*args: str) -> object:
    binary = os.environ.get("AGENT_CLI") or shutil.which("agent-cli")
    if not binary:
        raise MetricsError("agent-cli is not on PATH; install it, or set AGENT_CLI")
    done = subprocess.run([binary, "ado", *args], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise MetricsError(f"agent-cli ado {' '.join(args)} failed:\n{done.stderr.strip()}")
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError as err:
        raise MetricsError(f"agent-cli ado {' '.join(args)} printed no JSON: {err}") from err


def rows(value: object, what: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(r, dict) for r in value):
        raise MetricsError(f"{what}: expected a JSON list of objects")
    return [{str(k): v for k, v in r.items()} for r in value]


def obj(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise MetricsError(f"{what}: expected a JSON object")
    return {str(k): v for k, v in value.items()}


def stamp(value: object, what: str) -> datetime:
    if not isinstance(value, str):
        raise MetricsError(f"{what}: expected a timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as err:
        raise MetricsError(f"{what}: {value!r} is not a timestamp") from err
    if parsed.tzinfo is None:
        raise MetricsError(f"{what}: {value!r} has no time zone")
    return parsed.astimezone(UTC)


def number(row: dict[str, object], key: str, what: str) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, str) and value.isdigit():
            return int(value)
        raise MetricsError(f"{what}: {key} is not a number")
    return value


def repeated(flag: str, values: list[str]) -> list[str]:
    return [part for value in values for part in (flag, value)]


def rfc3339(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_moment(text: str, now: datetime) -> datetime:
    """A span back from now (36h, 7d, 2w), a date, or an RFC 3339 time."""
    span = SPAN_RE.match(text)
    if span:
        amount, unit = int(span.group(1)), span.group(2)
        return now - timedelta(hours=amount * {"h": 1, "d": 24, "w": 168}[unit])
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as err:
        raise MetricsError(
            f"{text!r} is not a span such as 7d, a date, or an RFC 3339 time"
        ) from err
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


@dataclass(frozen=True)
class TypeStates:
    active: list[str]
    done: list[str]

    @classmethod
    def of(cls, item_type: str, active: list[str] | None, done: list[str] | None) -> TypeStates:
        """The given state names, or else the type's states in each category."""
        if active and done:
            return cls(active, done)
        what = f"work item type {item_type}"
        info = obj(agent_cli("workitem-type", "get", item_type, "--fields", "states"), what)
        by_category: dict[str, list[str]] = {}
        for state in rows(info.get("states"), what):
            name, category = state.get("name"), state.get("category")
            if isinstance(name, str) and isinstance(category, str):
                by_category.setdefault(category, []).append(name)
        found = cls(
            active or by_category.get(ACTIVE_CATEGORY, []),
            done or by_category.get(DONE_CATEGORY, []),
        )
        if not found.active or not found.done:
            raise MetricsError(
                f"{what} has no {ACTIVE_CATEGORY} or {DONE_CATEGORY} states; pass them"
            )
        return found


@dataclass(frozen=True)
class Scope:
    states: dict[str, TypeStates]
    area: str | None
    iteration: str | None
    team: str | None
    repos: list[str]

    def list_args(self, item_type: str) -> list[str]:
        out = ["--type", item_type]
        if self.area:
            out += ["--area", self.area]
        if self.iteration:
            out += ["--iteration", self.iteration]
        if self.team:
            out += ["--team", self.team]
        return out


@dataclass
class Summary:
    count: int = 0
    median_hours: float | None = None
    p85_hours: float | None = None
    samples: list[dict[str, object]] = field(default_factory=list)

    @classmethod
    def of(cls, samples: list[tuple[str, float]]) -> Summary:
        hours = sorted(h for _, h in samples)
        if not hours:
            return cls()
        p85 = hours[max(0, math.ceil(0.85 * len(hours)) - 1)]
        return cls(
            count=len(hours),
            median_hours=round(statistics.median(hours), 1),
            p85_hours=round(p85, 1),
            samples=[{"id": key, "hours": round(h, 1)} for key, h in samples],
        )


def state_spans(item_id: int) -> list[tuple[str, datetime]]:
    what = f"history of {item_id}"
    history = obj(
        agent_cli("history", "get", str(item_id), "--field", "state", "--fields", "states"), what
    )
    spans = []
    for state in rows(history.get("states"), what):
        name = state.get("state")
        if not isinstance(name, str):
            raise MetricsError(f"{what}: a state has no name")
        spans.append((name, stamp(state.get("since"), what)))
    return spans


def cycle_time(scope: Scope, since: datetime, until: datetime) -> Summary:
    samples = []
    for item_type, states in scope.states.items():
        window = ["--since", rfc3339(since), "--until", rfc3339(until)]
        found = agent_cli(
            "workitem",
            "list",
            *scope.list_args(item_type),
            *repeated("--state", states.done),
            *window,
            "--limit",
            LIST_LIMIT,
            "--fields",
            "id",
        )
        for row in rows(found, "finished work items"):
            item_id = number(row, "id", "finished work item")
            spans = state_spans(item_id)
            started = next((at for name, at in spans if name in states.active), None)
            finished = next((at for name, at in reversed(spans) if name in states.done), None)
            if started is None or finished is None or not since <= finished <= until:
                continue
            samples.append((str(item_id), (finished - started).total_seconds() / 3600))
    return Summary.of(samples)


@dataclass
class Reviews:
    turnaround: Summary
    waiting: int
    oldest_waiting_hours: float | None


def review_turnaround(scope: Scope, since: datetime, until: datetime) -> Reviews:
    opened: dict[str, tuple[str, datetime]] = {}
    for repo in scope.repos or [None]:
        args = [
            "pr",
            "list",
            "--status",
            "all",
            "--since",
            rfc3339(since),
            "--until",
            rfc3339(until),
        ]
        args += ["--limit", LIST_LIMIT, "--fields", "id,author,created,is_draft"]
        if repo:
            args += ["--repo", repo]
        for pr in rows(agent_cli(*args), "pull requests"):
            if pr.get("is_draft") is True:
                continue
            author = pr.get("author")
            opened[str(number(pr, "id", "pull request"))] = (
                author if isinstance(author, str) else "",
                stamp(pr.get("created"), "pull request created"),
            )
    first_vote: dict[str, datetime] = {}
    people_args = ["person", "list", "--limit", LIST_LIMIT, "--fields", "id,name"]
    if scope.team:
        people_args += ["--team", scope.team]
    # activity list takes the sign-in address (id); pull requests name the author.
    people = {
        str(p["id"]): str(p.get("name", ""))
        for p in rows(agent_cli(*people_args), "people")
        if isinstance(p.get("id"), str)
    }
    for address, name in sorted(people.items()):
        activity = agent_cli(
            "activity",
            "list",
            "--person",
            address,
            "--since",
            rfc3339(since),
            "--limit",
            LIST_LIMIT,
            "--fields",
            "at,kind,action,id",
        )
        for event in rows(activity, f"activity of {address}"):
            pr_id = str(event.get("id"))
            if event.get("kind") != "pr" or event.get("action") not in VOTES or pr_id not in opened:
                continue
            if opened[pr_id][0] == name:
                continue
            at = stamp(event.get("at"), "vote")
            first_vote[pr_id] = min(at, first_vote.get(pr_id, at))
    samples = [
        (pr_id, (first_vote[pr_id] - created).total_seconds() / 3600)
        for pr_id, (_, created) in opened.items()
        if pr_id in first_vote
    ]
    waiting = [
        (until - created).total_seconds() / 3600
        for pr_id, (_, created) in opened.items()
        if pr_id not in first_vote
    ]
    return Reviews(
        turnaround=Summary.of(samples),
        waiting=len(waiting),
        oldest_waiting_hours=round(max(waiting), 1) if waiting else None,
    )


@dataclass
class Attention:
    active: int
    silent: list[int]
    stale: list[int]


def attention(scope: Scope, now: datetime, silent_days: int, stale_days: int) -> Attention:
    items = []
    for item_type, states in scope.states.items():
        found = agent_cli(
            "workitem",
            "list",
            *scope.list_args(item_type),
            *repeated("--state", states.active),
            "--limit",
            LIST_LIMIT,
            "--fields",
            "id",
        )
        items += rows(found, "active work items")
    silent, stale = [], []
    for row in items:
        item_id = number(row, "id", "active work item")
        detail = obj(
            agent_cli("workitem", "get", str(item_id), "--comments", "1", "--fields", "comments"),
            "work item",
        )
        comments = rows(detail.get("comments") or [], f"comments of {item_id}")
        latest = max((stamp(c.get("date"), "comment date") for c in comments), default=None)
        if latest is None or now - latest > timedelta(days=silent_days):
            silent.append(item_id)
        spans = state_spans(item_id)
        if spans and now - spans[-1][1] > timedelta(days=stale_days):
            stale.append(item_id)
    return Attention(active=len(items), silent=silent, stale=stale)


def team_defaults() -> dict[str, str]:
    """[ado] team and area from shared/team.toml, when they are filled in."""
    try:
        config = tomllib.loads(TEAM_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as err:
        raise MetricsError(f"{TEAM_FILE}: {err}") from err
    section = config.get("ado")
    if not isinstance(section, dict):
        return {}
    return {
        k: v.strip() for k in ("team", "area") if isinstance(v := section.get(k), str) and v.strip()
    }


def summary_rows(result: dict[str, object]) -> list[tuple[str, str, str]]:
    def hours(value: object) -> str:
        return "-" if value is None else f"{value} h"

    cycle = obj(result["cycle_time"], "cycle")
    reviews = obj(result["review_turnaround"], "reviews")
    turnaround = obj(reviews["turnaround"], "turnaround")
    work = obj(result["attention"], "attention")
    silent, stale = work["silent"], work["stale"]
    return [
        (
            "Cycle time",
            f"median {hours(cycle['median_hours'])}, p85 {hours(cycle['p85_hours'])}",
            f"{cycle['count']} finished items",
        ),
        (
            "Review turnaround",
            f"median {hours(turnaround['median_hours'])}, p85 {hours(turnaround['p85_hours'])}",
            f"{turnaround['count']} pull requests; {reviews['waiting']} still waiting"
            f" (oldest {hours(reviews['oldest_waiting_hours'])})",
        ),
        (
            "Silent items",
            f"{len(silent) if isinstance(silent, list) else silent} of {work['active']} active",
            f"no comment in {result['silent_days']} days",
        ),
        (
            "Stale items",
            f"{len(stale) if isinstance(stale, list) else stale} of {work['active']} active",
            f"in the same state over {result['stale_days']} days",
        ),
    ]


def render(result: dict[str, object], *, terminal: bool, color: bool) -> str:
    title = f"Window: {result['since']} to {result['until']}"
    table = [("Measure", "Value", "Over"), *summary_rows(result)]
    if not terminal:
        lines = [title, "", "| " + " | ".join(table[0]) + " |", "|---|---|---|"]
        return "\n".join(lines + ["| " + " | ".join(row) + " |" for row in table[1:]])
    widths = [max(len(row[n]) for row in table) for n in range(3)]

    def paint(text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if color else text

    def line(left: str, mid: str, right: str) -> str:
        return left + mid.join("─" * (w + 2) for w in widths) + right

    def cells(row: tuple[str, str, str], code: str | None) -> str:
        parts = [
            " " + (paint(c, code) if code else c) + " " * (w - len(c) + 1)
            for c, w in zip(row, widths, strict=True)
        ]
        return "│" + "│".join(parts) + "│"

    out = [paint(title, "36"), line("┌", "┬", "┐"), cells(table[0], "1"), line("├", "┼", "┤")]
    out += [cells(row, None) for row in table[1:]]
    out.append(line("└", "┴", "┘"))
    return "\n".join(out)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--since", default="7d", help="window start: 7d, 2w, a date, or RFC 3339 (default 7d)"
    )
    parser.add_argument("--until", help="window end (default now)")
    parser.add_argument("--type", action="append", help="work item type, repeatable (default Task)")
    parser.add_argument(
        "--active-state",
        action="append",
        help="repeatable (default: the type's In Progress states)",
    )
    parser.add_argument(
        "--done-state", action="append", help="repeatable (default: the type's Completed states)"
    )
    parser.add_argument("--area", help="only this area path, children included")
    parser.add_argument("--iteration", help="only this iteration, or @current")
    parser.add_argument(
        "--team",
        help="the team whose people and sprints count (default: team.toml, then [ado] team)",
    )
    parser.add_argument(
        "--repo", action="append", default=[], help="only these repositories' pull requests"
    )
    parser.add_argument("--silent-days", type=int, default=3)
    parser.add_argument("--stale-days", type=int, default=7)
    parser.add_argument("--log", type=Path, help="append the result to this file as one JSON line")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a summary")
    args = parser.parse_args(argv)
    now = datetime.now(UTC).replace(microsecond=0)
    try:
        defaults = team_defaults()
        args.team = args.team or defaults.get("team")
        args.area = args.area or defaults.get("area")
        until = parse_moment(args.until, now) if args.until else now
        since = parse_moment(args.since, until)
        scope = Scope(
            states={
                t: TypeStates.of(t, args.active_state, args.done_state)
                for t in args.type or ["Task"]
            },
            area=args.area,
            iteration=args.iteration,
            team=args.team,
            repos=args.repo,
        )
        reviews = review_turnaround(scope, since, until)
        result: dict[str, object] = {
            "measured_at": rfc3339(now),
            "since": rfc3339(since),
            "until": rfc3339(until),
            "scope": asdict(scope),
            "silent_days": args.silent_days,
            "stale_days": args.stale_days,
            "cycle_time": asdict(cycle_time(scope, since, until)),
            "review_turnaround": asdict(reviews),
            "attention": asdict(attention(scope, now, args.silent_days, args.stale_days)),
        }
    except MetricsError as err:
        print(f"metrics.py: {err}", file=sys.stderr)
        return 1
    if args.log:
        with args.log.expanduser().open("a", encoding="utf-8") as log:
            log.write(json.dumps(result, separators=(",", ":")) + "\n")
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        terminal = sys.stdout.isatty()
        print(render(result, terminal=terminal, color=terminal and not os.environ.get("NO_COLOR")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
