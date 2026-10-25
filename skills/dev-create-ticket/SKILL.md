---
name: dev-create-ticket
description: Turn something that needs doing into a PBI or Bug with concrete criteria, a dev task, and a QA task.
argument-hint: <what needs doing>
disable-model-invocation: true
---

# /dev-create-ticket

Give work a ticket before any code exists. Work without a ticket never shows
in the sprint, has no criteria to review against, and leaves QA nothing to
test. The criteria agreed here are what /dev-start, /dev-finish, /dev-review,
and QA use.

`<skill>` stands for this skill's folder, `~/.agents/skills/dev-create-ticket`
once installed, and other paths are relative to it. Run its scripts with
`uv run`, or with `python3` when that is 3.11 or later. Every ADO write goes
through `scripts/post.py --skill dev-create-ticket`, which adds the footer
and enforces the length caps. Write what it posts in the shape
`references/style.md` gives.

## Writes this skill makes without asking

None until the developer approves the draft in step 4. Then only these:

1. Create the PBI or Bug, with its criteria, under the team's area path. It
   goes to the backlog, or into the current sprint when the developer will
   start it now.
2. Create its dev task.
3. Create its QA task.

It sets no points, priority, or rank, which belong to refinement, and it
assigns nothing: /dev-start claims the task.

## 1. Read the context

Read the repository's `AGENTS.md`, and `assets/team.toml` for the team and
its area path. An empty value there means agent-cli's default. Find the
type names this project uses:

```sh
agent-cli ado workitem-type list --fields name
```

The backlog item is the type the team's backlog holds, such as Product
Backlog Item, User Story, or Issue. A defect is a Bug.

Done when you know the area path and both type names.

## 2. Check it is new

```sh
agent-cli ado workitem list --text '<two or three key words>' --area '<area>' --limit 10 --fields id,type,title,state,iteration
```

If an open item already covers the work, show it and offer `/dev-start <id>`
on it instead.

Done when nothing open covers it, or the developer chose to go on anyway.

## 3. Make the criteria concrete

Read `references/sizing.md` and `references/criteria.md`, and question the
developer at the depth the size calls for. A major change stops here. For a
bug, get the repro: the steps, the expected result, the actual result, and
where it happens.

Done when the developer has confirmed criteria that meet
`references/criteria.md`.

## 4. Show the draft

Show the developer:

- the type and the title, which says what changes in at most 120 characters
- the description: two to four sentences on why, plus the repro for a bug
- the criteria
- the dev task's title, and the QA task's, `QA: <title>`
- where it lands: the backlog, or the current sprint if they will start now

Wait for the developer to approve or correct it.

Done when the developer has approved the draft.

## 5. Create it

Write the description and the criteria to two files in a temporary folder.
When the item starts now, add `--iteration '<sprint path>'` to all three
calls, with the path from
`agent-cli ado sprint get @current --team '<team>' --fields path`.

```sh
uv run <skill>/scripts/post.py --skill dev-create-ticket create --type '<type>' --title '<title>' --area '<area>' --file <description.md> --criteria-file <criteria.md>
uv run <skill>/scripts/post.py --skill dev-create-ticket create --type Task --parent <id> --title '<dev task title>' --area '<area>'
uv run <skill>/scripts/post.py --skill dev-create-ticket create --type Task --parent <id> --title 'QA: <title>' --area '<area>' --field "Activity=Testing"
```

Leave out `--area` when `team.toml` gives none, and `--field
"Activity=Testing"` when the Task type has no Activity field. If the type
has no Acceptance Criteria field, as in the Basic process, ask before
putting the criteria in the description instead.

Done when the item and both tasks exist.

## 6. Report

List each item created, one line each, with its id. If the developer will
start now, tell them to run `/dev-start <dev task>`. It finds the criteria
already in place, so it goes straight to claiming the task and agreeing the
plan. Otherwise the item waits on the backlog for refinement.
