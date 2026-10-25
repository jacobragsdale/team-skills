---
name: dev-context
description: Write or refresh this repository's AGENTS.md and docs/app-context.md, with every operational fact confirmed read-only.
disable-model-invocation: true
---

# /dev-context

Write the two files every other team skill reads about this repository.
`AGENTS.md` holds what every session needs. `docs/app-context.md` holds the
operational facts for each environment, which only /dev-verify, /dev-support,
and this skill read. Run it once per repository, and again whenever the facts
drift.

`<skill>` stands for this skill's folder, `~/.agents/skills/dev-context` once
installed, and other paths are relative to it. Run its scripts with
`uv run`, or with `python3` when that is 3.11 or later.

## Writes this skill makes without asking

Only `AGENTS.md` and `docs/app-context.md`, in the working tree. The skill
writes nothing to ADO and changes nothing in any environment: it confirms
facts with agent-cli read commands only, production included. Opening a pull
request for the files asks first.

## 1. Read what is there

Read `assets/AGENTS.template.md` and `assets/app-context.template.md`, the
fixed shapes of the two files. Then read the repository's current `AGENTS.md`
and `docs/app-context.md`, if they exist. Keep every rule, gotcha, and fact a
person wrote. When one is now wrong, ask before removing it.

Done when you know the shapes and what the current files already say.

## 2. Read the repository

Take facts from the code and its configuration, not from memory:

- **What it is:** the README and the package metadata.
- **Commands:** the build files, package scripts, Makefile, and the CI
  pipeline definition. `check` is the one command, or a short chain, that
  runs locally everything CI checks: lint, types, and tests.
- **System:** the sibling repositories it calls or is called by, from client
  configuration, shared schemas, and the repositories under `~/dev/` that
  mention this one. Read a sibling's `~/dev/<sibling>/AGENTS.md` when it
  exists, and keep the two descriptions consistent.
- **Verify:** how a change shows on QA: the endpoint, UI page, or job to
  exercise, and the test data to use.
- **Operations:** deployment manifests, Helm values, pipeline stages, DAG
  files, per-environment settings files, and connection names.

Done when every section of both templates has a candidate fact or an open
question.

## 3. Confirm each operational fact, read-only

Confirm every fact before `docs/app-context.md` states it, so other skills
can trust the file. Use only commands that `--help` marks `Read.`:

| Fact | Confirm with |
|---|---|
| A deployment | `agent-cli k8s deployment get <name> --cluster <c> --namespace <ns> --fields id,ready` |
| A DAG | `agent-cli airflow dag get <dag> --instance <i> --fields id,paused,schedule` |
| A Control-M job | `agent-cli controlm definition get <server/folder/job> --instance <i>` |
| A database connection | `agent-cli sql connection list --fields name,kind,database` |
| A Datadog service | `agent-cli dd service get <service> --env <env> --fields id,spans,errors` |
| A log query | `agent-cli dd log list '<query>' --since 1h --limit 5` |
| A key vault | `agent-cli kv vault get <vault> --fields id` |
| A pipeline | `agent-cli ado pipeline get <pipeline> --fields name,repo,yaml` |

For anything else, `agent-cli search "<what you want to confirm>"` finds the
command. A fact you cannot confirm stays out of the file. List it for the
developer with the reason, such as "no access to the prod cluster".

Done when each fact meant for `docs/app-context.md` is confirmed or listed as
left out.

## 4. Write the files

Write both files in the shapes of the templates, and fill every placeholder
or delete its line:

- `AGENTS.md` stays near 80 lines and holds only what every session needs.
  Anything operational goes to `docs/app-context.md`.
- `## Rules` holds at most ten rules, each one a linter cannot check.
- `## Gotchas` keeps every existing entry.
- In the TOML block, keep only the domains this system uses. A domain
  table's keys are agent-cli flags for that domain, and its lists name what
  exists there.

Then check the block, and print each environment to read it back:

```sh
uv run <skill>/scripts/ctx.py --check
uv run <skill>/scripts/ctx.py --env <name>
```

Done when `ctx.py --check` passes and every environment prints as intended.

## 5. Report

Show the developer the diff of both files, then list the facts confirmed and
the facts left out, with the reasons. If the repository has no `check`
command, say so plainly: /dev-finish and CI depend on one, and the team's
lint adoption procedure adds one.

Offer to open a pull request for the files. The branch policy needs a linked
work item, so ask the developer which one. On a yes, commit on a new branch,
push it, and open the pull request in the shape `references/style.md` gives:

```sh
uv run <skill>/scripts/post.py --skill dev-context pr-create --repo <repo> --source <branch> --title "Agent context for <repo>" --workitem <id> --file - <<'EOF'
<description>
EOF
```
