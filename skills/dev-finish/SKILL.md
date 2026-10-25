---
name: dev-finish
description: Run the quality gates on this branch, open its pull request linked to the task, and write the QA task's test steps.
argument-hint: [task id]
disable-model-invocation: true
---

# /dev-finish

Finish the current branch: prove it meets the criteria, review it cold, open
the pull request, and give QA its steps. A human reviewer should only have to
judge the change, not find its basic problems.

Paths below are relative to this skill's folder. Run its scripts with
`uv run`, or with `python3` when that is 3.11 or later. Every ADO write goes
through `scripts/post.py --skill dev-finish`, which adds the footer and
enforces the length caps. Write what it posts in the shape
`references/style.md` gives.

## Writes this skill makes without asking

Running /dev-finish is the developer's consent to these writes, and only
these:

1. Commit the work on the task's branch and push it.
2. Open the pull request linked to the dev task, or update the one already
   open.
3. Write the QA task's description.
4. Comment on the dev task that the pull request is open.

Ask before any other write, including adding reviewers. A person always
closes or deletes work items, changes items that belong to someone else,
approves, and merges, so never turn on auto-complete.

## 1. Read the context

Read the repository's `AGENTS.md`. The dev task is the argument, or else the
number at the start of the branch name. If neither gives one, ask. Run
`scripts/ticket.py <task>` for the PBI's criteria and the QA task.

If the PBI has no criteria, stop and offer to draft them with the developer,
in the format `references/criteria.md` gives, before going on.

Done when you have the criteria, the dev task, and the QA task.

## 2. Gate: the check passes

Run the `check` command from `AGENTS.md`, which is the same one CI runs. Fix
what fails and run it again until it passes. Never skip, delete, or loosen a
test to get there. If `AGENTS.md` names no `check` command, say so, run the
tests and linters you can find, and suggest /dev-context.

Done when `check` passes on the current tree.

## 3. Gate: every criterion has a test

Read the whole change with `git diff origin/<default branch>...HEAD`, plus
anything uncommitted. For each criterion, name the test that proves it. When
a criterion has no test, write one now and make sure it fails without the
change. When behavior changed, the README, the API reference, or `AGENTS.md`
changes with it.

Done when every criterion maps to a named, passing test.

## 4. Gate: cold self-review

Give the change to one fresh-context subagent. It is the only subagent any
team skill uses. Brief it in these words, with the blanks filled in.
`<checklist>` is the absolute path of this skill's
`references/review-checklist.md`.

> Review a change you have not seen before. In `<repository path>`, run
> `git diff origin/<default branch>...HEAD`. Judge it against these
> acceptance criteria:
>
> <the criteria>
>
> and against the checklist in `<checklist>`. Report only problems a
> reviewer would block on or must know about, most serious first, each as
> `path:line: the problem, and why it matters`. Skip anything a linter or the
> test run catches. Do not edit any file.

If the harness has no subagents, review it yourself: re-read the whole diff
from the top as if someone else wrote it, against the criteria and
`references/review-checklist.md`.

Fix each finding that holds up. For one that does not, tell the developer
why. If the code changed, go back to step 2.

Done when every finding is fixed or answered, and `check` passes.

## 5. Open the pull request

Commit what is uncommitted, with a message that says what changed and why,
then push the branch. Write the description in the shape
`references/style.md` gives, and open the pull request:

```sh
uv run scripts/post.py --skill dev-finish pr-create --repo <repo> --source <branch> --title "<what it does>" --workitem <task> --file - <<'EOF'
<description>
EOF
```

Link the dev task only. The merge policy completes the items a pull request
links, and the PBI stays open until QA is done with it. The repository is the
last segment of `git remote get-url origin`. When a pull request is already
open for the branch, `pr-create` returns it; then replace its description
with `pr-describe <id>` if the description no longer fits the change.

Done when the pull request is open, linked to the dev task, and its
description fits the change.

## 6. Give QA its steps

Write the QA task's description from the criteria and the `## Verify` section
of `AGENTS.md`: one block per criterion, in the shape `references/style.md`
gives.

```sh
uv run scripts/post.py --skill dev-finish describe <qa task> --file - <<'EOF'
<steps>
EOF
```

If the QA task's description already holds text without the
"Agent-written via" footer, a person wrote it, so show the developer and ask
before replacing it. If the PBI has no QA task, ask whether to create one.

Done when the QA task's description covers every criterion.

## 7. Report

Comment on the dev task, in two sentences at most:

```sh
uv run scripts/post.py --skill dev-finish comment <task> "!<pr> is open for review. QA steps are on #<qa task>."
```

Then tell the developer the pull request's link, the gates it passed, and
anything left open.
