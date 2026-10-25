---
name: dev-start
description: Claim a work item, make its acceptance criteria concrete, create its branch, and agree a plan before any code.
argument-hint: <work item id>
disable-model-invocation: true
---

# /dev-start

Start work on a PBI or a task. The criteria agreed here are what /dev-finish
checks, /dev-review judges, and QA tests, so make them concrete before any
code exists. Unclear requirements are cheapest to fix now.

Paths below are relative to this skill's folder. Run its scripts with
`uv run`, or with `python3` when that is 3.11 or later. Every ADO write goes
through `scripts/post.py --skill dev-start`, which adds the footer and
enforces the length caps. Write what it posts in the shape
`references/style.md` gives.

## Writes this skill makes without asking

Running /dev-start is the developer's consent to these writes, and only these:

1. Assign the dev task to the developer (`@me`) when it is unassigned.
2. Set the dev task to In Progress.
3. Write the confirmed criteria to the PBI.
4. Create a QA task under the PBI when it has none.
5. Create the task's branch in ADO, linked to the task.
6. Post the approved plan on the dev task, as a comment.

Skip any write that is already in place, so running the skill again is safe.
Ask before any other write. A person always closes or deletes work items,
changes items that belong to someone else, approves, and merges.

## 1. Read the context

Read the repository's `AGENTS.md`. Then run `scripts/ticket.py <id>`, which
prints the item, the PBI above it with its criteria, the PBI's dev tasks, and
its QA task.

- When the id is a PBI, its dev task is the one assigned to the developer, or
  else the only unassigned one. If that leaves none or several, ask which. If
  the PBI has no dev task, ask whether to create one.
- When the dev task is assigned to someone else, stop and ask. Taking it from
  them is the developer's call.
- When nothing sits above a task, say so and ask where the criteria live.

Done when you know the dev task, the PBI, and whether a QA task exists.

## 2. Make the criteria concrete

Read `references/criteria.md`. Judge the change's size from the PBI, the
task, and the code it touches, and question the developer to match:

| Size | Looks like | Do |
|---|---|---|
| Tweak | A copy or config change, one obvious edit, or a bug with a clear repro | Ask nothing. State the criteria in one to three lines and ask the developer to confirm them. |
| Normal | One behavior change in one area | Ask one round of at most five questions, then state the criteria. |
| Major | Several PBIs' worth, a new integration, or a design decision nobody has made | Stop. Say it needs a design and a split into PBIs first (/dev-design, once it is installed). |

Ask only what the ticket, the code, and `AGENTS.md` cannot answer, and give
your recommended answer with each question so the developer can reply "yes".
When the PBI already has criteria that meet `references/criteria.md`, confirm
them and write nothing.

Once the developer confirms, put the criteria on the PBI:

```sh
uv run scripts/post.py --skill dev-start describe <pbi> --field acceptance-criteria --file - <<'EOF'
1. When ...
EOF
```

If the PBI's type has no Acceptance Criteria field, as in the Basic process,
ask before adding the criteria to its description.

Done when the developer has confirmed criteria that meet
`references/criteria.md`, and the PBI holds them.

## 3. Claim the task

```sh
uv run scripts/post.py --skill dev-start update <task> --state "In Progress" --assignee @me
```

Use the state in the task type's `InProgress` category, which
`agent-cli ado workitem-type get Task --fields states` shows. Leave out
`--assignee` when the task is already the developer's.

When the PBI has no QA task, create one. /dev-finish writes its description.

```sh
uv run scripts/post.py --skill dev-start create --type Task --parent <pbi> --title "QA: <PBI title>" --field "Activity=Testing"
```

Done when the task is In Progress and assigned to the developer, and the PBI
has a QA task.

## 4. Create the branch

Take the repository's name from the last segment of
`git remote get-url origin`. Create the branch in ADO, linked to the task,
then switch to it:

```sh
uv run scripts/post.py --skill dev-start branch <task> --repo <repo>
git fetch origin && git switch <branch>
```

The default name is `<id>-<title-slug>`. Pass `--branch` when `AGENTS.md`
sets another convention. If the working tree has uncommitted changes, ask
before switching.

Done when the branch is checked out and linked to the task.

## 5. Agree the plan

For a tweak, skip to step 6. Otherwise read the code the change touches, then
show the developer the plan:

- the approach, in two or three sentences
- each criterion, with the test that will prove it and where the test goes
- the files to change
- the risks, and anything you are unsure of

Wait for the developer to approve or correct it. Then post a summary of the
approved plan on the dev task, in two sentences at most:

```sh
uv run scripts/post.py --skill dev-start comment <task> "Plan: <approach>. Tests first for AC1 to AC3."
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
