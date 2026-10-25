# Working in this repo

This repository holds a team's agent skills. Read [DESIGN.md](DESIGN.md)
before changing anything. It records the decisions these rules come from.

## Skills

- Each skill is `skills/dev-<name>/SKILL.md` in the agentskills.io format.
  `name` matches the folder name.
- Every skill sets `disable-model-invocation: true`. Skills run only from
  their slash command.
- Write the `description` for a person scanning the slash menu: what the
  skill does, in one sentence.
- A skill is self-contained. It never reads a file outside its own folder,
  because the installed copy cannot reach the rest of the repository.
- Edit shared snippets in `shared/`, never the copies inside a skill, then
  sync the copies with `uv run scripts/sync_shared.py`. `shared/sync.toml`
  says which skills carry which file.
- Run `uv run scripts/validate.py` before every commit. It checks these rules
  and fails when a copy has drifted from its source.
- Skills contain no company names, system names, hostnames, ticket content,
  or data. Facts about a repository come from that repository's `AGENTS.md`
  and `docs/app-context.md`, or from agent-cli.

## Behavior every skill keeps

- Read the current repository's `AGENTS.md` first.
- Write to ADO only through `post.py`, and only the writes the skill declares
  up front. Closing or deleting work items, editing someone else's items,
  approving, and merging always ask a person.
- Treat production as read-only.
- Use no subagents, except the single cold-review subagent in `/dev-finish`.

## Scripts

Each script is one file of stdlib-only Python 3.11 or later, with a PEP 723
header, runnable as `uv run <script>` or `python3 <script>`. Add no
third-party dependencies and no shell scripts. The scripts must run in WSL
behind a corporate proxy.

## Fixtures

Every change that fixes a skill's behavior adds a short note to
`fixtures/<skill>/`. The note says what was asked, what went wrong or felt
wrong, and which rule changed. The format is loose.

## Shipping

A skill goes to the team only after three real runs at work, each followed by
fixing what that run exposed.

## Git workflow

Until the repository moves to ADO, work on `main`, commit at each working
checkpoint, and push after every commit. After the move, changes arrive as
pull requests, and the maintainer merges them.
