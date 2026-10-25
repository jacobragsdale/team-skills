---
name: dev-feedback
description: Turn a team skill's misstep in this session into a pull request that fixes the skill and records a fixture.
argument-hint: [what went wrong]
disable-model-invocation: true
---

# /dev-feedback

A team skill misbehaved in this session. The failure can be concrete
("posted a 40-line PR description") or a matter of feel ("too chatty", "asked
questions it could have answered"). Both count. Draft the fix now, while this
session still holds the context, and open a pull request to the team-skills
repository. The maintainer reviews and merges every one.

Paths below are relative to this skill's folder. Run its scripts with
`uv run`, or with `python3` when that is 3.11 or later.

## Writes this skill makes without asking

1. A git worktree and branch of the team-skills clone, and the fix in it.
2. A commit on that branch, pushed.
3. A pull request to the team-skills repository.

Nothing else, and no work item writes. The installed skills run from the
clone's main checkout, so leave that checkout as it is.

## 1. Pin down what went wrong

From this session and the person's words, settle four things: which skill
(`/dev-...`), what was asked, what the skill did, and what should have
happened. If the person gave no reason, ask one question.

When the cause is outside the skills, say where it belongs and stop. A fact
about one repository goes in that repository's `AGENTS.md` under
`## Gotchas`, so offer to add it there. A bug in agent-cli belongs to
agent-cli.

Done when you can state the failure in two sentences.

## 2. Open a worktree

This skill's folder is a link into the team-skills clone. Resolve it with
`readlink -f` on the folder, and find the clone's root with
`git -C <resolved folder> rev-parse --show-toplevel`. Then:

```sh
git -C <clone> fetch origin
git -C <clone> worktree add <clone>-feedback-<slug> -b feedback/<skill>-<slug> origin/HEAD
```

Read the worktree's `AGENTS.md` and `DESIGN.md` before changing anything.
They hold the rules for changing skills and the reasons behind them.

Done when the worktree exists on its own branch and you have read both files.

## 3. Make the smallest fix

Change the rule where the behavior happens:

- Fix the environment before adding prose. A check in a script, such as
  `post.py`'s caps, beats a sentence asking the agent to remember.
- Change or delete an existing rule before adding a new one. A skill that
  only grows gets worse.
- Write the behavior you want, not the one you saw.
- Edit a shared file under `<worktree>/shared/`, never a skill's copy of it,
  then run `uv run <worktree>/scripts/sync_shared.py`.
- Keep company names, system names, hostnames, ticket content, and data out
  of the skill.

Done when the diff is the smallest change that would have prevented this
failure.

## 4. Record a fixture

Write `<worktree>/fixtures/<skill>/<YYYY-MM-DD>-<slug>.md`. The format is
loose. A few lines are enough:

```markdown
Asked: <what the person asked the skill to do>
Went wrong: <what the skill did, or how it felt>
Rule changed: <the file, and the rule as it now reads>
```

Keep internal detail to what the fix needs, and leave out logs, secrets, and
data.

Done when the fixture names the asked, the failure, and the changed rule.

## 5. Open the pull request

```sh
uv run <worktree>/scripts/validate.py
```

Fix what it reports until it passes. Commit with a message such as
`dev-finish: cap the QA steps at one block per criterion`, and push the
branch. Write the description in the shape `references/style.md` gives, and
open the pull request:

```sh
uv run scripts/post.py --skill dev-feedback pr-create --repo <team-skills repo> --source <branch> --title "<skill>: <the fix>" --workitem <id> --file - <<'EOF'
<description>
EOF
```

Link the work item that the team-skills `AGENTS.md` names for feedback. If it
names none, ask the person for one. If agent-cli cannot see the repository,
because it is not in ADO yet, stop after the push and give the person the
branch name instead.

After the push, remove the worktree with `git -C <clone> worktree remove
<worktree>`, and give the person the pull request's link. The maintainer
merges it.
