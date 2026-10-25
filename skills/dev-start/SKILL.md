---
name: dev-start
description: Claim a work item, make its acceptance criteria concrete, create its branch, and agree a plan before any code.
argument-hint: [work item id]
disable-model-invocation: true
---

# /dev-start

Start work on a PBI or a task. The criteria agreed here are what /dev-finish
checks, /dev-review judges, and QA tests, so make them concrete before any
code exists. Unclear requirements are cheapest to fix now.

`<skill>` stands for this skill's folder, `~/.agents/skills/dev-start` once
installed, and other paths are relative to it. Run its scripts with
`uv run`, or with `python3` when that is 3.11 or later. Every ADO write goes
through `scripts/post.py --skill dev-start`, which adds the footer and
enforces the length caps. Write what it posts in the shape
`references/style.md` gives.

## Writes this skill makes without asking

Running /dev-start is the developer's consent to these writes, and only these:

1. Assign the dev task to the developer (`@me`) when it is unassigned.
2. Set the dev task to In Progress.
3. Move the dev task into the current sprint when it sits in no sprint or
   in one that has ended.
4. Write the confirmed criteria to the PBI. When a person wrote the
   criteria already there, only after the developer confirms replacing them.
5. Create a QA task under the PBI when it has none.
6. Create the task's branch in ADO, linked to the task.
7. Write the dev task's estimate when it has none, as part of the approved
   plan.
8. Post the approved plan on the dev task, as a comment.

Skip any write that is already in place, so running the skill again is safe.
Ask before any other write, including moving the PBI into the sprint. A
person always closes or deletes work items, changes items that belong to
someone else, approves, and merges.

## 1. Read the context

Read the repository's `AGENTS.md`, and `assets/team.toml` for the team's
name. With no id, find the next task in the current sprint:

```sh
uv run <skill>/scripts/sprint.py --format md next
```

Offer the top task, and take its id when the developer says yes. When none
is waiting, say so and offer /dev-create-ticket.

Then run `uv run <skill>/scripts/ticket.py <id>`, which prints the item, its
sprint, the PBI above it with its criteria, the PBI's dev tasks with their
Remaining Work, and its QA task.

- When the id is a PBI, its dev task is the one assigned to the developer, or
  else the only unassigned one. If that leaves none or several, ask which. If
  the PBI has no dev task, ask whether to create one.
- When the dev task is assigned to someone else, stop and ask. Taking it from
  them is the developer's call.
- When nothing sits above a task, say so and ask where the criteria live.

Done when you know the dev task, the PBI, and whether a QA task exists.

## 2. Make the criteria concrete

Read `references/sizing.md` and `references/criteria.md`, judge the change's
size from the PBI, the task, and the code it touches, and question the
developer at the depth the size calls for. When the PBI already has criteria
that meet `references/criteria.md`, confirm them and write nothing.

When the PBI's criteria fall short and do not end in the "Agent-written via"
footer, a person wrote them. Show your version and ask the developer to
confirm replacing theirs. One yes is enough.

Once the developer confirms, put the criteria on the PBI:

```sh
uv run <skill>/scripts/post.py --skill dev-start describe <pbi> --field acceptance-criteria --file - <<'EOF'
1. When ...
EOF
```

If the PBI's type has no Acceptance Criteria field, as in the Basic process,
ask before adding the criteria to its description.

Done when the developer has confirmed criteria that meet
`references/criteria.md`, and the PBI holds them.

## 3. Claim the task

```sh
uv run <skill>/scripts/post.py --skill dev-start update <task> --state "In Progress" --assignee @me
```

Use the state in the task type's `InProgress` category, which
`agent-cli ado workitem-type get Task --fields states` shows. Leave out
`--assignee` when the task is already the developer's.

Keep the task in the sprint. List the team's sprints, leaving out `--team`
when `team.toml` gives none:

```sh
agent-cli ado sprint list --team '<team>' --fields path,timeframe
```

When the task's sprint is the current one or a future one, leave it. When
it is in no sprint or one that has ended, move it:

```sh
uv run <skill>/scripts/post.py --skill dev-start update <task> --iteration '<current sprint path>'
```

When the PBI sits in no current or future sprint, ask before moving it the
same way, because moving it changes the sprint's scope.

When the PBI has no QA task, create one. /dev-finish writes its description.

```sh
uv run <skill>/scripts/post.py --skill dev-start create --type Task --parent <pbi> --title "QA: <PBI title>" --field "Activity=Testing"
```

Done when the task is In Progress, assigned to the developer, and in the
current sprint, and the PBI has a QA task.

## 4. Create the branch

Take the repository's name from the last segment of
`git remote get-url origin`. Create the branch in ADO, linked to the task,
then switch to it:

```sh
uv run <skill>/scripts/post.py --skill dev-start branch <task> --repo <repo>
git fetch origin && git switch <branch>
```

The default name is `<id>-<title-slug>`. Pass `--branch` when `AGENTS.md`
sets another convention. If the working tree has uncommitted changes, ask
before switching.

Done when the branch is checked out and linked to the task.

## 5. Agree the plan

For a tweak, propose only an estimate when the task has none, write it once
the developer agrees, and skip to step 6. Otherwise read the code the change
touches, then show the developer the plan:

- the approach, in two or three sentences
- each criterion, with the test that will prove it and where the test goes
- the files to change
- the risks, and anything you are unsure of
- the estimate in hours, when the dev task's Remaining Work is empty

Wait for the developer to approve or correct it. Write the agreed estimate,
which sets Original Estimate where the process has one, and Remaining Work:

```sh
uv run <skill>/scripts/post.py --skill dev-start update <task> --estimate <hours>
```

Then post a summary of the approved plan on the dev task, in two sentences
at most:

```sh
uv run <skill>/scripts/post.py --skill dev-start comment <task> "Plan: <approach>. Tests first for AC1 to AC3."
```

Done when the developer has approved the plan and its summary is on the task.

## 6. Hand over to the work

List for the developer what this run wrote to ADO, one line per write. Then
do the work in this order:

1. **Red first.** For each criterion, write its test, run it, and watch it
   fail for the expected reason. A bug starts from a failing repro.
2. Make the tests pass with the smallest change that meets the criteria.
3. Run the `check` command from `AGENTS.md` until it passes.
4. Run /dev-finish.
