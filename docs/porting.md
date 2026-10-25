# Port the skills to work

The repository is built at home, against a sandbox ADO project and the public
agent-cli. This guide moves it to the team's ADO, fills in the team's
settings, and connects the internal agent-cli. Do it once, in the ADO copy.

Every piece that changes here is marked `PORT:` in the source.
`uv run scripts/validate.py` lists the markers that remain, and you are done
when it lists none.

## Before you start

You need the internal agent-cli installed and signed in, so that
`agent-cli doctor` passes, and uv or Python 3.11 or later in WSL.

## 1. Move the repository into ADO

Remove the GitHub remote before anything real goes into the repository, so
the team's names never reach GitHub:

```sh
git -C ~/dev/team-skills remote remove origin
git -C ~/dev/team-skills remote add origin <the ADO repository's clone URL>
git -C ~/dev/team-skills push -u origin main
```

List the company and system names the skills must never hold, one per line,
in `~/.config/team-skills/banned-words.txt`. `validate.py` then fails on any
of them outside `team.toml`.

## 2. Check agent-cli's commands

The skills and scripts call the commands below. Run each one with `--help`
on the internal agent-cli and compare its flags and its `Returns:` line with
what the skills use. Where one differs, change the shared script under
`shared/scripts/` or the skill's `SKILL.md`, never a copy.

| Command | What the skills use |
|---|---|
| `ado workitem get` | `--comments`; `iteration`, `tags`, `parent`, `children`, `pull_requests`, `branches`, `comments`, `acceptance_criteria` |
| `ado workitem list` | `--iteration`, `--area`, `--team`, `--assignee @me`, `--state`, `--type`, `--text`, `--since`; `id`, `type`, `title`, `state`, `assignee`, `iteration`, `tags`, `priority` |
| `ado workitem update` | `--state`, `--assignee`, `--iteration`, `--tags`, `--field`, `--if-rev`, `--description-file`, `--acceptance-criteria-file`, `--dry-run` |
| `ado workitem create` | `--type`, `--title`, `--parent`, `--area`, `--iteration`, `--description-file`, `--acceptance-criteria-file`, `--field` |
| `ado workitem comment`, `ado workitem link` | `--text-file`; `--repo`, `--branch` |
| `ado workitem-type get`, `list` | `states` with `name` and `category`; `fields` with `ref` |
| `ado history get` | `states` with `state`, `since`, `until`; `changes` with `rev`, `date`, and `fields` of `field`, `old`, `new` |
| `ado tree get` | children with `remaining_work` |
| `ado sprint get`, `ado sprint list` | `path`, `name`, `start`, `finish`, `timeframe`, `working_days_left`, `totals`, `people`, `team_days_off` |
| `ado backlog list` | `id`, `rank` |
| `ado activity list` | `--person`, `--since`; `at`, `kind`, `action`, `id`, `title` |
| `ado pr list` | `--reviewer`, `--vote none`, `--author`, `--draft`; `id`, `title`, `author`, `created` |
| `ado pr get` | `status`, `is_draft`, `created`, `reviewers` with `vote`, `open_threads`, `work_items`, `source` |
| `ado pr create`, `update`, `comment`, `link`, `ado thread list`, `ado diff get` | as `post.py` and `/dev-review` call them |
| `doctor` | the ADO check's "signed in as" detail |

`sprint.py` works out who you are from the assignee on your own work items,
then from `agent-cli doctor`. If neither works, set `TEAM_SKILLS_ME` to your
sign-in address.

## 3. Fill in `shared/team.toml`

Fill each value, then delete its `PORT:` marker.

**Team and area.** Set `team` to the ADO team's name, and check that this
prints the current sprint:

```sh
agent-cli ado sprint get @current --team '<team>' --fields name,start,finish,people
```

Sprints, `@current`, and capacity all come through ADO's team-settings API.
If that API is unavailable in the organization, the internal agent-cli needs
a fallback that reads the sprint dates from the project's iterations, and the
capacity figures drop out of the views. Set `area` to the team's area path,
as `agent-cli ado workitem get <a team ticket> --fields area` prints it.

**People.** Add a `[[people]]` table for each member, with the display name
and the sign-in address that `--assignee` takes. The assignee on one of their
tickets shows the address.

**Tags.** Find the exact names the team uses:

```sh
agent-cli ado workitem list --iteration @current --team '<team>' --limit 200 --fields id,type,tags
```

Set `blocked`, `ready_for_qa`, and `ready_for_deployment` to those names, and
`on` to `task` or `pbi`, whichever carries them. Keep `ready_for_qa_when =
"merged"` if the team tags Ready for QA once the pull request merges. Any
other value turns the offer off, until a skill can see the deploy to QA.

**Time logs.** Fill in the internal agent-cli's time-log commands, as the
arguments after `agent-cli`, with `{person}`, `{since}`, `{until}`, `{task}`,
`{date}`, `{hours}`, and `{entry}` where the values go. `list` must print a
JSON list of entries, and `fields` names the keys holding each entry's id,
task, date, and hours. Set `block_hours` to the smallest entry the tracker
takes, and `time_off_task` to the ticket all time off goes to. Then check
both directions without writing anything:

```sh
uv run scripts/sync_shared.py
uv run skills/dev-sprint/scripts/sprint.py time
uv run skills/dev-sprint/scripts/post.py --skill dev-sprint --dry-run timelog <task> --date <day> --hours 1
```

Log one real hour on a test task and read `agent-cli ado history get <task>`.
Note whether the tracker changed Completed Work or Remaining Work by itself,
and add the answer to `DESIGN.md` under Time.

## 4. Check the work item types

```sh
agent-cli ado workitem-type get Task --fields fields
agent-cli ado workitem-type get '<backlog item type>' --fields fields
```

- The Task type has Remaining Work. `post.py` also writes Original Estimate
  where the type has it.
- The Task type's Activity field allows Testing, which QA tasks carry. If it
  has no Activity field, the skills leave it out.
- The backlog item type has Acceptance Criteria, and one of Story Points,
  Effort, or Size, which `sprint.py` reads as points.

## 5. Check reviewer mentions

`/dev-finish` asks reviewers in a pull request comment that mentions them,
because agent-cli cannot add a reviewer yet. Post a test comment with
`@<Display Name>` on a draft pull request and check that the person is
notified. If the internal agent-cli can add a reviewer, change step 6 of
`/dev-finish` to use it.

## 6. Check the harnesses

- Copilot CLI honors `disable-model-invocation`. Cursor and Copilot in VS
  Code document that they do.
- Cursor and VS Code attached to WSL read skills from the WSL home rather
  than the Windows profile. If they read the Windows profile, run
  `install.py --windows` after each pull.
- Developers' WSL installs have Python 3.11 or later, or uv.
- The branch policies can be enabled on every repository: a linked work item
  on every pull request, and the linked items completed on merge.

## 7. Validate and push

```sh
uv run scripts/sync_shared.py
uv run scripts/validate.py
```

Fix what it reports until it passes and lists no `PORT:` markers. Commit and
push to ADO.

## 8. Take the baseline and run each skill

Take the metrics baseline before the team gets the skills:

```sh
uv run ~/dev/team-skills/scripts/metrics.py --log ~/team-metrics.jsonl
```

Then use each v1 skill on three real tickets, and fix what each run exposes
with `/dev-feedback`. The skills go to the team together once all seven have
had their runs.
