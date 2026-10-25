---
name: dev-sprint
description: Show your sprint, a teammate's, or the team's by stage, then fix your own tickets and fill in your time.
argument-hint: [team | <name>]
disable-model-invocation: true
---

# /dev-sprint

Show where the sprint stands, then make the fixes the developer picks. With
no argument it is the developer's own sprint. `team` is the whole team, for
the lead or anyone else, and a teammate's name is theirs. Everyone reads the
same ADO data, so nobody keeps a separate status.

`<skill>` stands for this skill's folder, `~/.agents/skills/dev-sprint` once
installed, and other paths are relative to it. Run its scripts with
`uv run`, or with `python3` when that is 3.11 or later. Every ADO write goes
through `uv run <skill>/scripts/post.py --skill dev-sprint`, which adds the
footer, enforces the length caps, allows only the team's own tags, and keeps
each day's time at or under the day's hours. Write what it posts in the
shape `references/style.md` gives.

## Writes this skill makes without asking

None. It makes only the fixes the developer picks in step 4, and only on the
developer's own items:

1. A progress note on a task, as a comment.
2. Setting or clearing the team's Blocked tag, with the reason as a comment.
3. Setting the team's Ready for QA tag, once the pull request has merged.
4. Setting Remaining Work to the hours the developer gives.
5. Moving a task into the current sprint.
6. Linking a pull request to its task.
7. Moving a task from New to In Progress once work on it exists.
8. Time entries, from the table the developer approved in step 5.

It never closes or completes an item, reassigns one, sets Ready for
deployment, adds a tag the team does not already use, or changes anything
that belongs to someone else. The team and teammate views write nothing.

## 1. Read the context

Read the current repository's `AGENTS.md`, when there is one. Read
`assets/team.toml` for the team's tag names. Where its values are still
placeholders, the view falls back to agent-cli's defaults and says which
parts are not set up yet. Pass that on.

The argument picks the view: none for the developer's own, `team` for the
team, and anything else is a teammate's name.

## 2. Show the view

```sh
uv run <skill>/scripts/sprint.py --format md view
uv run <skill>/scripts/sprint.py --format md view --team
uv run <skill>/scripts/sprint.py --format md view --person '<name>'
```

Show the output as printed, so its tables render in the chat. For the team
or a teammate, add at most three lines on what stands out, and stop there.

Done when the developer sees the view.

## 3. Write the standup lines

Turn the "Since <day>" section into at most three lines the developer can
read out at standup: what moved, what comes next, and anything blocking.
Keep the ids.

## 4. Offer the fixes

The view ends with the fixes it found. Check each one against the evidence
before offering it, and draft whatever it needs. The evidence is:

- ADO: the pull request's state, votes, and threads
  (`agent-cli ado pr get <id>`), and the item's comments and history
  (`agent-cli ado workitem get <id>`, `agent-cli ado history get <id>`).
- Local git in the repositories under `~/dev/`: the branch, unpushed
  commits, and the time of the last commit.
- This session, when it worked on the item.

Never read other sessions' history. Each write below follows
`uv run <skill>/scripts/post.py --skill dev-sprint`:

| Fix | The write |
|---|---|
| Progress note: two sentences at most, from the commits and PR activity since the last note | `comment <id> "<note>"` |
| Blocked: set it only when the developer names the blocker, clear it when the evidence shows it is gone, and comment the reason in one line | `update <id> --add-tag "<Blocked>"` or `--remove-tag "<Blocked>"` |
| Ready for QA, once the PR has merged | `update <id> --add-tag "<Ready for QA>"` |
| Remaining Work: ask how many hours are left, and suggest 0 once the PR is open | `update <id> --remaining <hours>` |
| Move into the current sprint | `update <id> --iteration "<sprint path>"` |
| Link a PR to its task | `pr-link <pr> --workitem <task>` |
| Start a task that already has a branch or PR | `update <id> --state "<In Progress state>"` |
| Fill in time | Step 5 |

The tag names are the ones in `assets/team.toml`. Add any fix the evidence
shows that the view missed. Present them as one numbered list, each with what
it changes and why, and with any note written out in full. Ask which to make,
make exactly those, one call each, and list what was written.

Done when every fix the developer picked is made, or they picked none.

## 5. Fill in time

When the developer picks the time fix, or asks to fill in their week:

```sh
uv run <skill>/scripts/sprint.py --format md time
```

It shows each working day since Monday of last week, with the hours logged
and a proposal for each gap. The rules behind the proposal:

- Every working day holds exactly the day's hours (8), no more, no less.
- Entries are whole blocks (1 hour), at least one block each.
- A day goes evenly to the developer's dev and QA tasks that were active
  that day. The leftover goes to the most active one.
- A team day off goes to the time-off ticket in `assets/team.toml`.
- A day with no active task gets no proposal.

Show the table. For each day it could not fill, ask where the time went. A
day off goes to the time-off ticket. Let the developer change any entry,
keeping each day at exactly the day's hours. Once they approve the table,
write each entry:

```sh
uv run <skill>/scripts/post.py --skill dev-sprint timelog <task> --date <YYYY-MM-DD> --hours <h>
```

Change or delete an existing entry only when the developer asks, with
`timelog-update <entry> --date <day> --hours <h>` or
`timelog-delete <entry> --date <day>`. When `post.py` refuses an entry, show
why and fix the table, never the rule. If time logging is not set up yet,
say so: the team-skills repository's `docs/porting.md` covers it.

Done when every day in the table holds exactly the day's hours, or the
developer stopped.
