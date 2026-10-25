# <repo>: <one line on what it is and who uses it>

## System

Part of <system>. Sibling repositories, each cloned at `~/dev/<name>`:

- `<sibling>`: <what it does, in one line>

<Two to four lines on how this repository connects to them: what it calls,
what calls it, and what data passes between them.>

## Commands

- Setup: `<command>`
- Check: `<command>` (CI runs the same command; /dev-finish runs it before
  opening a pull request)
- Test: `<command>`
- Run locally: `<command>`

## Environments

Operational facts for each environment (logs, pods, jobs, connections) live in
[docs/app-context.md](docs/app-context.md). Read it only when a task needs them.

## Verify

How to prove a change works on QA:

- <the endpoint, UI page, or job to exercise, and how to reach it>
- <the test data to use, and where it comes from>
- <what a correct result looks like>

## Rules

Rules a linter cannot check, at most ten.

1. <rule>

## Gotchas

Things that tripped agents up. /dev-feedback adds to this list.

- <gotcha>
