# Team skills design

Team skills is a small set of explicitly invoked agent skills that carry an
Azure DevOps (ADO) work item from "start" to "merged" for a development team
whose code is written entirely by agents. The work item is the backbone, and
its acceptance criteria are the thread every skill pulls on. Knowledge
specific to the firm stays out of the skills. It lives in each repository's
`AGENTS.md` and `docs/app-context.md`, and in
[agent-cli](https://github.com/jacobragsdale/agent-cli) configuration.

This page records the decisions and the reasons behind them. Each skill's
internals are designed separately, against a real ticket, when the skill is
built.

## Goals and scope

The first goal is the team's throughput. Faster work for the lead and more
consistent output across the team matter, but they come second. The team
already writes all of its code with agents, in Cursor and GitHub Copilot on
Windows and WSL. Writing code is no longer the constraint. Delivery stalls in
three places instead: reviewing each other's pull requests, verifying that a
change works beyond unit tests, and requirements that were never made
concrete. The system targets those three.

The skills cover general software delivery: starting work, finishing it,
reviewing it, verifying it, supporting it in production, and releasing it.
They contain no company names, system names, hostnames, or data, so the same
skills work in any repository. A skill that needs a repository's facts reads
them from that repository's context files or gets them from agent-cli.

Skills follow the [agentskills.io](https://agentskills.io) format. Cursor,
Copilot in VS Code, and Copilot CLI all discover skills in `~/.agents/skills/`,
so one install location serves all three.

## Where knowledge lives

Material for agents can live in four places. Each piece goes in the lowest
place that works.

1. **Tools** (agent-cli, MCP servers) decide what an agent can reach: ADO,
   AKS, Kubernetes, SQL, Airflow, Datadog, Key Vault.
2. **Context** (each repository's `AGENTS.md` and `docs/app-context.md`)
   holds facts about one system: where it runs, what it talks to, how to check
   it. Context is versioned with the code and edited through pull requests.
3. **Standards** decide how the team writes code.
4. **Workflows** are multi-step procedures with gates and judgment calls.

Standards are deliberately not skills. A standards skill is loaded on almost
every edit, so its weight is paid constantly, and it only helps when an agent
happens to load it. Linters, type checkers, and CI enforce standards instead,
and they work whether or not an agent is involved. The few judgment rules a
linter cannot check (validate at boundaries, no silent fallbacks) become a
checklist inside the review workflow, loaded only while reviewing. Every skill
in this repository is therefore a workflow.

The same principle extends further: put a fix in the environment before adding
an instruction. ADO branch policies require a linked work item on every pull
request and complete the linked items on merge, so ticket linking and most
state changes happen without anyone remembering to do them. A posting script
enforces the brevity and attribution rules instead of a paragraph asking the
agent to be brief. Steps that are deterministic (fetching a ticket and its
parent, formatting a comment, computing metrics) are bundled scripts, and
prose is reserved for judgment.

## Explicit invocation

Every skill runs only when a person types its slash command. Skills set
`disable-model-invocation: true` and share the `dev-` prefix, so typing
`/dev-` lists the whole loop and no name collides with a built-in command
such as `/review`. A developer always knows when they are starting a review or
a production investigation, so automatic triggering adds weight and surprises
without saving any effort. Descriptions are written for a person reading the
slash menu, not as trigger text for a model.

## The ticket backbone

Every workflow starts from a work item and ends by writing back to it. The
team's ticket problems were work done without a ticket, states left stale,
pull requests left unlinked, and no progress notes. All four disappear when
updating the ticket is a side effect of doing the work rather than a separate
chore.

Product backlog items (PBIs) carry one or more dev tasks and a QA task. The
skills divide the work between them as follows:

- The PBI holds the acceptance criteria. `/dev-start` accepts either a PBI or
  a task, and always walks up to the PBI to read or write the criteria.
- `/dev-start` sets the dev task to In Progress, and creates a QA task when
  the PBI has none.
- `/dev-finish` links the pull request to the dev task, so the merge policy
  completes the task. It also writes the QA task's description: for each
  criterion, the steps, the expected result, the test data, and where to test.
- No skill moves a PBI to Done. QA or a person does that.

Invoking a skill is consent for the writes that skill declares up front. For
example, `/dev-start 1234` may set the task In Progress, assign it, create the
branch, and post the plan without asking. Anything outside the declared list
asks first. Some actions always need a person: closing or deleting work items,
editing someone else's items, approving pull requests, and merging.

Agent writes in ADO are brief and marked. A work item comment is at most two
sentences. A pull request description is two to four sentences, then a spaced
list of changes, then "How to test". Every write ends with a footer such as
`_Agent-written via /dev-finish_`, which also tells the maintainer which skill
to fix when a write reads badly. All ADO writes go through one shared script,
`post.py`, which appends the footer, rejects text over the length cap (about
600 characters, with one explicit override), and then calls agent-cli.

## Acceptance criteria as the thread

Testable acceptance criteria tie the skills into one system.

- `/dev-start` makes the criteria concrete and writes them to the PBI.
- `/dev-finish` checks the change against them and turns them into QA steps.
- `/dev-review` judges the pull request against them.
- `/dev-verify` proves each one with evidence.
- QA tests against them.
- `/dev-release` lists them.

Unclear requirements are cheapest to fix before any code exists, so
`/dev-start` questions the developer, with depth matched to the ticket's size.
A tweak, or a bug with an obvious repro, gets no questions: the agent states
the criteria in one to three lines and the developer confirms them. A normal
change gets one round of five questions or fewer. A major feature goes through
`/dev-design` first, which questions the developer in full, writes a design
doc, and splits the work into PBIs that each carry criteria.

## Quality gates

Every change passes four gates in order. Together they are the answer to "high
quality from the start".

1. **Plan before code.** Above a tweak, the agent posts its approach and the
   developer approves it.
2. **Red first.** Each criterion gets a failing test before the fix, and a bug
   always starts from a failing repro.
3. **Repo check green.** The repository's `check` command, the same one CI
   runs, passes locally.
4. **Cold self-review.** In `/dev-finish`, a fresh-context subagent reviews
   the diff against the criteria and the review checklist before the pull
   request opens. Where the harness has no subagents, the same agent re-reads
   the diff cold.

The first two gates catch the expensive mistakes: building the wrong thing,
and building an untested thing. The last two reduce what a human reviewer has
to wade through, which is the team's first bottleneck.

## Repository context

Every repository gets an `AGENTS.md` in one fixed shape, kept to about 80
lines. Every skill reads it explicitly as its first step, so the skills do not
depend on whether a harness loads it automatically.

```markdown
# <repo>: one line on what it is and who uses it
## System        part of <system>; sibling repos; how this repo connects
## Commands      setup · check (what /dev-finish and CI run) · test · run locally
## Environments  one-line pointer to docs/app-context.md
## Verify        how to prove a change on QA: which endpoint, UI, or job, and what data
## Rules         at most ten repo rules a linter cannot check
## Gotchas       things that tripped agents up (grows through /dev-feedback)
```

`AGENTS.md` loads in every session, so it holds only what every session needs.
Operational facts (log locations, pod names, Datadog queries, Airflow DAG ids,
SQL connection names, per environment) live in `docs/app-context.md` instead.
Only `/dev-support`, `/dev-verify`, and `/dev-context` read that file, and only
when they need it. Its machine-readable part is a fenced TOML block under
`## Environments`, which renders as code in ADO's file viewer. A bundled
script, `ctx.py --env qa`, reads that block and prints ready-to-paste
agent-cli flags.

`/dev-context` writes both files. It reads the repository, confirms each
operational fact live and read-only through agent-cli (the pod names exist,
the DAG ids are real), and writes the files in the fixed shape. It runs once
per repository, and again whenever the facts drift.

Systems that span several repositories need no separate system document. The
`## System` section of each `AGENTS.md` names the system and its sibling
repositories, and says in two to four lines how this repository connects to
them. Every repository is cloned under `~/dev/` inside WSL, so an agent that
needs a sibling opens `~/dev/<sibling>/AGENTS.md`. A few duplicated lines cost
less than a system document nobody maintains. `/dev-design` can create one if
a major feature needs it.

## Safety and cost

Production is read-only for every skill. Skills may read production logs,
metrics, job state, and data. They never write production data, never apply
changes to a cluster, and never trigger a production deploy. Infrastructure
and deployment changes, such as AKS node pools, are pull requests to the
deployments repository and go out through its pipelines.

Multi-agent fan-out is off everywhere except for `/dev-finish`'s single
cold-review subagent. Every subagent spends the team's requests and tokens,
and one agent with good context is cheaper than several shallow ones. A person
can still ask for fan-out explicitly.

## Skill map

The system has twelve skills at maturity, shipped in three phases. Each
phase's skills are proven before the next phase starts.

| Phase | Skill | Job |
|---|---|---|
| v1 | `/dev-context` | Write or refresh a repo's `AGENTS.md` and `docs/app-context.md`, with facts confirmed read-only |
| v1 | `/dev-start` | Claim the task, make the criteria concrete, create the branch, agree a plan |
| v1 | `/dev-finish` | Run the gates, open the linked pull request, write the QA task |
| v1 | `/dev-review` | Review a teammate's pull request against the criteria and the checklist, drafting comments for a person to post |
| v1 | `/dev-feedback` | Turn a skill's failure in the current session into a pull request to this repository |
| v2 | `/dev-verify` | Prove the change works on dev or QA and post an evidence summary |
| v2 | `/dev-pause` | Post a progress or blocker note and write a handoff for the next session |
| v2 | `/dev-support` | Triage a production issue read-only and open or update a Bug |
| v2 | `/dev-sprint` | Show the lead the team's sprint health: stale items, blockers, what needs attention |
| v3 | `/dev-debug` | Diagnose a hard bug with a red repro loop |
| v3 | `/dev-design` | Question a major feature in full, write a design doc, split it into PBIs |
| v3 | `/dev-release` | Gather merged work, write release notes, run the deploy checklist and post-deploy check |

`metrics.py` ships with v1 so the baseline exists before the first skill does.

The v1 set covers the path every ticket takes (start, finish, review) and
the two things that path depends on. `/dev-context` produces the files the
other skills read, and `/dev-feedback` is how fixes flow back. `/dev-verify`
waits for v2 because it needs the `docs/app-context.md` files that
`/dev-context` produces.

Some needs are deliberately left out. Answering QA testers' questions is
something an agent does well without a skill. Documentation is a
`/dev-finish` checklist item ("behavior changed, so update the README or API
reference"). Quantitative data analysis and infrastructure work are served by
agent-cli plus repository context, not by workflows.

## Repository layout

```text
AGENTS.md            authoring rules for anyone sending a pull request
DESIGN.md            this page
skills/dev-<name>/   one self-contained skill per folder
shared/              snippets several skills need: criteria format, style rule, review checklist, post.py
templates/           the AGENTS.md and app-context.md shapes, and lint/ baseline configs
scripts/             install.py, metrics.py, validate.py, sync_shared.py
fixtures/<skill>/    notes on failures that changed a skill
```

An installed skill cannot rely on files outside its own folder, so each skill
is fully self-contained. `sync_shared.py` copies snippets from `shared/` into
the skills that use them, and the validator fails when a copy drifts from its
source.

Every script is a single file of stdlib-only Python 3.11 or later with a
PEP 723 header. That makes the scripts network-free, so a corporate proxy or
certificate problem cannot break them, and they run under `uv run` or plain
`python3`.

Installation is a clone and one command. Clone the repository to
`~/dev/team-skills` in WSL and run `uv run scripts/install.py`, which
symlinks each skill into `~/.agents/skills/`. Updating is `git pull`. There is
no package or marketplace step to fail.

The prerequisites on a developer's machine are WSL, agent-cli, every
repository cloned under `~/dev/`, and Python 3.11 or later (or uv) in WSL.

## Rollout and feedback

A skill reaches the team after the maintainer has used it on three real
tickets at work and fixed what those runs exposed. It then goes to the whole
team at once. There is no shakedown against a sandbox ADO organization,
because agent-cli is already validated against the real services.

Developers report a skill that misbehaved by running `/dev-feedback` in the
session where it happened. The agent drafts the fix from its own context and
opens a pull request to this repository, and the maintainer owns every merge.
A failure can be concrete ("posted a 40-line PR description") or a matter of
feel ("too chatty", "asked questions it could have answered"). Both count.

Each fix ships with a fixture: a short, loose note in `fixtures/<skill>/` that
says what was asked, what went wrong or felt wrong, and which rule changed. A
fixture has no required format and no automated harness. It does three jobs.
It explains why a rule exists. It gives a regression check, since its prompt
can be replayed when the skill changes. And it gives a pruning signal: after a
model upgrade, replay the fixtures without the rule, and if the agent now
behaves anyway, delete the rule. That last job keeps the skills getting
shorter over time instead of only longer.

Success is measured from ADO, weekly, with a baseline taken before v1 ships:

- cycle time, from a work item starting to finishing
- pull request review turnaround
- active items with no note in the last three days
- stale items

A skill that moves none of these numbers is cut.

The repository is built at home, pushed once to GitHub, then moved into ADO.
From then on it lives only in ADO, because feedback pull requests and their
fixtures carry internal context.

## Lint adoption

Dropping standards skills makes linters responsible for code quality, and many
repositories carry years of lint debt. Each repository adopts the baseline
configs in `templates/lint/` through one mechanical pull request. The configs
cover ruff and ty for Python, ESLint and strict `tsconfig` for Angular, .NET
analyzers with `.editorconfig` for C#, and clippy for Rust.

1. Apply the template config.
2. Autofix the safe fixes only: `ruff check --fix` without unsafe fixes,
   `eslint --fix`, `dotnet format`, `cargo clippy --fix`.
3. Bulk-suppress everything that remains (`ruff check --add-noqa`, ESLint's
   suppressions file, .NET rule severities, a clippy allow-list), so `check`
   is green on the first day.
4. From then on, only new code must be clean. Suppressions are burned down in
   dedicated cleanup pull requests, never inside feature pull requests, because
   lint churn inside a feature diff makes review worse.

Formatters rewrite whole files and conflict with open branches, so the
adoption pull request runs a formatter only when the repository has no open
pull requests. The repositories the team touches most adopt first. The rest
adopt the next time someone works in them. The procedure is
`scripts/adopt_lint.py` plus a how-to guide, not a skill. `/dev-context`
records each repository's `check` command, and reports any repository that has
none.

## Deferred decisions

- Repo-aware defaults in agent-cli: commands that resolve their target from
  the current repository. `ctx.py` covers the need for now.
- The length cap's edge cases in `post.py`: whether pasted logs and code
  blocks count, and the exact override. These are decided with real comments
  in front of the maintainer. Until then `post.py` caps comments at 600
  characters and descriptions (pull requests, QA steps, criteria) at 2,000,
  because the pull request shape above does not fit in 600. It counts every
  character, and `--long` lifts the cap. The footer is italic on pull
  requests and plain on work items, because agent-cli's conversion of
  Markdown to work item HTML has no italics.
- Each skill's internals, designed when that skill is built.

## To verify at work

- Copilot CLI honors `disable-model-invocation`. Cursor and Copilot in VS Code
  document that they do.
- Cursor and VS Code attached to WSL read skills from the WSL home rather than
  the Windows profile. If they read the Windows profile, `install.py` also
  copies skills to `%USERPROFILE%\.agents\skills`.
- Developers' WSL installs have Python 3.11 or later, or uv.
- The branch policies can be enabled on every repository.
