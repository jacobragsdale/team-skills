#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Print a work item with its backlog item, criteria, and tasks, as JSON.

Give a work item id (a task, a PBI, a user story, a bug) and get back the
backlog item above it (the PBI) with its acceptance criteria, and the PBI's
tasks split into dev tasks and the QA task, each with its Remaining Work
(none when nobody estimated it). A task whose title starts with "QA" is the
QA task. Give --pr instead to do the same for every work item a
pull request links.

Reads only; every call goes through agent-cli.

Examples:
  ticket.py 1234
  ticket.py --pr 431
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass

if sys.version_info < (3, 11):
    sys.exit("ticket.py needs Python 3.11 or later; run it with `uv run`.")

# Task-level types sit under a backlog item; anything else is the backlog item.
TASK_TYPES = frozenset({"task"})
QA_PREFIX = "qa"
ITEM_FIELDS = (
    "id,type,title,state,assignee,iteration,tags,parent,description,acceptance_criteria,"
    "branches,pull_requests,url"
)


class TicketError(Exception):
    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Task:
    id: int
    title: str
    state: str
    assignee: str | None
    remaining_work: float | None


@dataclass(frozen=True)
class Item:
    id: int
    type: str
    title: str
    state: str
    assignee: str | None
    iteration: str
    tags: list[str]
    parent: int | None
    url: str
    description: str
    acceptance_criteria: str
    branches: list[str]
    pull_requests: list[str]


@dataclass(frozen=True)
class Ticket:
    item: Item
    pbi: Item | None
    dev_tasks: list[Task]
    qa_task: Task | None


def agent_cli(*args: str) -> object:
    binary = os.environ.get("AGENT_CLI") or shutil.which("agent-cli")
    if not binary:
        raise TicketError("agent-cli is not on PATH; install it, or set AGENT_CLI", 3)
    done = subprocess.run([binary, "ado", *args], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise TicketError(
            f"agent-cli ado {' '.join(args)} failed:\n{done.stderr.strip()}", done.returncode
        )
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError as err:
        raise TicketError(f"agent-cli ado {' '.join(args)} printed no JSON: {err}") from err


def as_dict(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TicketError(f"{what}: expected a JSON object")
    return {str(k): v for k, v in value.items()}


def as_list(value: object, what: str) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TicketError(f"{what}: expected a JSON list")
    return value


def text(row: dict[str, object], key: str, what: str, *, required: bool = True) -> str:
    value = row.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise TicketError(f"{what}: {key} is missing or not text")
    return value


def number(row: dict[str, object], key: str, what: str) -> int:
    value = row.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise TicketError(f"{what}: {key} is missing or not a number")
    return value


def optional_number(row: dict[str, object], key: str, what: str) -> int | None:
    return None if row.get(key) is None else number(row, key, what)


def optional_text(row: dict[str, object], key: str, what: str) -> str | None:
    return text(row, key, what) if row.get(key) is not None else None


def hours(row: dict[str, object], key: str, what: str) -> float | None:
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TicketError(f"{what}: {key} is not a number")
    return float(value)


def refs(row: dict[str, object], key: str, label: str, what: str) -> list[str]:
    out = []
    for entry in as_list(row.get(key), f"{what}: {key}"):
        ref = as_dict(entry, f"{what}: {key}")
        out.append(f"{text(ref, 'repo', what)}:{ref.get(label)}")
    return out


def get_item(item_id: int) -> Item:
    what = f"work item {item_id}"
    row = as_dict(
        agent_cli("workitem", "get", str(item_id), "--comments", "0", "--fields", ITEM_FIELDS),
        what,
    )
    return Item(
        id=number(row, "id", what),
        type=text(row, "type", what),
        title=text(row, "title", what),
        state=text(row, "state", what),
        assignee=optional_text(row, "assignee", what),
        iteration=text(row, "iteration", what, required=False),
        tags=[str(t) for t in as_list(row.get("tags"), f"{what}: tags")],
        parent=optional_number(row, "parent", what),
        url=text(row, "url", what),
        description=text(row, "description", what, required=False),
        acceptance_criteria=text(row, "acceptance_criteria", what, required=False),
        branches=refs(row, "branches", "name", what),
        pull_requests=refs(row, "pull_requests", "id", what),
    )


def tasks_of(pbi_id: int) -> list[Task]:
    what = f"the tree of {pbi_id}"
    tree = as_dict(agent_cli("tree", "get", str(pbi_id), "--depth", "1"), what)
    out = []
    for entry in as_list(tree.get("children"), what):
        child = as_dict(entry, what)
        if text(child, "type", what).lower() not in TASK_TYPES:
            continue
        out.append(
            Task(
                id=number(child, "id", what),
                title=text(child, "title", what),
                state=text(child, "state", what),
                assignee=optional_text(child, "assignee", what),
                remaining_work=hours(child, "remaining_work", what),
            )
        )
    return out


def ticket(item_id: int) -> Ticket:
    item = get_item(item_id)
    if item.type.lower() not in TASK_TYPES:
        pbi: Item | None = item
    elif item.parent is not None:
        pbi = get_item(item.parent)
    else:
        pbi = None
    tasks = tasks_of(pbi.id) if pbi is not None else []
    qa = [t for t in tasks if t.title.strip().lower().startswith(QA_PREFIX)]
    return Ticket(
        item=item,
        pbi=pbi,
        dev_tasks=[t for t in tasks if t not in qa],
        qa_task=qa[0] if qa else None,
    )


def pull_request(pr_id: int) -> dict[str, object]:
    what = f"pull request {pr_id}"
    pr = as_dict(
        agent_cli(
            "pr",
            "get",
            str(pr_id),
            "--comments",
            "0",
            "--fields",
            "id,repo,title,author,status,is_draft,source,target,description,work_items,url",
        ),
        what,
    )
    ids = []
    for entry in as_list(pr.get("work_items"), what):
        if not isinstance(entry, int):
            raise TicketError(f"{what}: work_items holds a non-number")
        ids.append(entry)
    tickets = [asdict(ticket(i)) for i in ids]
    return {"pr": pr, "tickets": tickets}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("id", nargs="?", type=int, help="a work item id")
    target.add_argument("--pr", type=int, help="a pull request id")
    args = parser.parse_args(argv)
    try:
        result = pull_request(args.pr) if args.pr is not None else asdict(ticket(args.id))
    except TicketError as err:
        print(f"ticket.py: {err}", file=sys.stderr)
        return err.code
    json.dump(result, sys.stdout, indent=1)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
