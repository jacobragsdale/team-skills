# Team skills

Explicitly invoked agent skills that carry an Azure DevOps work item from
start to merge, for teams that write their code with agents in Cursor and
GitHub Copilot on WSL. Every skill starts with `/dev-`, runs only when a
person types its command, and reaches ADO and other services through
[agent-cli](https://github.com/jacobragsdale/agent-cli).

## Install

You need WSL, agent-cli signed in to your ADO organization (`agent-cli doctor`
passes), Python 3.11 or later or uv, and your repositories cloned under
`~/dev/`. The team's settings, such as its roster and tag names, come with
the clone in `shared/team.toml`.

```sh
git clone REPO_URL ~/dev/team-skills
uv run ~/dev/team-skills/scripts/install.py
```

`REPO_URL` is this repository's clone URL. `install.py` links each skill into
`~/.agents/skills/`, where Cursor, Copilot in VS Code, and Copilot CLI find
them, so `git pull` updates every skill at once. If your editor reads skills
from the Windows profile instead of the WSL home, also run `install.py
--windows`, and run it again after each pull. Type `/dev-` in an agent chat to
see the skills.

## The skills

A ticket's path is `/dev-create-ticket` when the work has no ticket yet,
`/dev-start`, the work itself, `/dev-finish`, and a teammate's `/dev-review`.
`/dev-sprint` shows where your sprint, or the team's, stands. Each skill makes
only the writes listed here without asking. No skill closes or deletes a work
item, approves, or merges.

| Skill | What it does | Writes without asking |
|---|---|---|
| `/dev-context` | Writes or refreshes a repository's `AGENTS.md` and `docs/app-context.md`, confirming each operational fact read-only. Run it once in each repository first. | The two files, in the working tree |
| `/dev-create-ticket <what>` | Turns something that needs doing into a PBI or Bug with concrete criteria, a dev task, and a QA task, in the backlog or the current sprint. | Nothing until you approve the draft, then the three items |
| `/dev-start [id]` | Claims the task, keeps it in the current sprint, makes the PBI's acceptance criteria concrete, creates the branch, and agrees a plan. With no id, it offers the next unassigned task. | Assigns and starts the task, moves it into the sprint, writes the criteria and the estimate, creates the QA task and the branch, and posts the plan |
| `/dev-finish` | Runs `check`, maps each criterion to a test, has a fresh agent review the diff, opens the pull request, suggests reviewers, and writes the QA steps. | Pushes the branch, opens the pull request linked to the task, sets Remaining Work to 0, writes the QA task, and comments on the task |
| `/dev-review [pr]` | Reviews a teammate's pull request against its criteria and the review checklist, and drafts comments. With no id, it lists the reviews you owe. | Nothing. It posts only the drafts you pick. |
| `/dev-sprint [team \| name]` | Shows your sprint, the team's, or a teammate's by stage, then offers fixes for your own tickets and fills in your time. | Nothing. It makes only the fixes you pick. |
| `/dev-feedback` | Turns a skill's misstep in the current session into a pull request to this repository, with a fixture. | A branch and a pull request here |

These are the v1 skills. The v2 and v3 skills in the
[skill map](DESIGN.md#skill-map) follow once v1 has been proven at work.

## See your sprint from a terminal

`/dev-sprint` runs a script you can also run yourself, with no agent and no
tokens. In a terminal it prints colored tables:

```sh
alias sprint='uv run ~/.agents/skills/dev-sprint/scripts/sprint.py'
sprint view            # your sprint
sprint view --team     # the team's
sprint time            # your hours since Monday of last week
```

## Measure the effect

Take the baseline before the skills reach the team, then run the same command
every week:

```sh
uv run ~/dev/team-skills/scripts/metrics.py --log ~/team-metrics.jsonl
```

It prints cycle time, pull request review turnaround, active items with no
comment in three days, and stale items, and appends the same numbers to the
log as one JSON line. The team and area default to `shared/team.toml`.
`--help` lists the filters for area, team, iteration, and repository.

## Move to work

The repository is built at home with placeholder team settings. Follow
[docs/porting.md](docs/porting.md) once, in the ADO copy, to fill in
`shared/team.toml`, connect the time logs, and check agent-cli.

## Prepare a repository

- Run `/dev-context` in the repository, so the other skills can read its
  commands, its systems, and how to verify a change.
- Adopt the team's lint baseline once, by following
  [How to adopt the lint baseline](templates/lint/README.md).

## Change a skill

Read [AGENTS.md](AGENTS.md) for the rules and [DESIGN.md](DESIGN.md) for the
reasons behind them. Edit shared files under `shared/`, then copy them into
the skills and check the repository:

```sh
uv run scripts/sync_shared.py
uv run scripts/validate.py
```

Every fix to a skill's behavior adds a note under `fixtures/<skill>/`, as
[fixtures/README.md](fixtures/README.md) describes. From inside an agent
session, `/dev-feedback` does all of this for you.
