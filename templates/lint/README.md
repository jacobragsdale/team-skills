# How to adopt the lint baseline

Bring one repository onto the team's lint baseline in a single mechanical
pull request, with its `check` command passing the same day. Adopt first in
the repositories the team touches most. The rest adopt the next time someone
works in them.

Prerequisites:

- You can push a branch and open a pull request in the repository.
- The repository is cloned under `~/dev/`, its working tree is clean, and the
  team-skills repository is cloned at `~/dev/team-skills`.
- agent-cli is signed in, so the script can count the repository's open pull
  requests.
- The tools for each ecosystem the repository uses:
  - Python: ruff and ty on the `PATH`, or else uv, which runs them through
    `uvx`. The project's environment must be synced (for example with
    `uv sync`), so that ty can resolve imports.
  - TypeScript or Angular: ESLint 9.24 or later and its packages, installed
    with `npm install --save-dev eslint @eslint/js typescript-eslint
    eslint-config-prettier`, plus `angular-eslint` in an Angular workspace.
  - .NET: the .NET SDK, and one solution or project at the top of the
    repository.
  - Rust: cargo, with clippy and rustfmt.

To adopt the baseline, work in the repository's folder:

1. Create a branch from the default branch:

   ```sh
   git fetch origin
   git switch -c lint-baseline origin/DEFAULT_BRANCH
   ```

   `DEFAULT_BRANCH` is the repository's default branch, such as `main`.

2. Preview what the script will do:

   ```sh
   uv run ~/dev/team-skills/scripts/adopt_lint.py --plan
   ```

   An ecosystem listed under "Skipped" is missing a tool, or already has its
   own lint config. Install a missing tool and preview again. For an existing
   config, merge the template file the message names, from
   `~/dev/team-skills/templates/lint/`, into the repository's config by hand.
   Then pass `--keep-config` in this step and the next, so the script keeps
   your merged config and still makes the fixes and suppressions.

3. Run the adoption:

   ```sh
   uv run ~/dev/team-skills/scripts/adopt_lint.py
   ```

   For each ecosystem, the script applies the baseline config, runs the safe
   autofixes only, suppresses everything that remains, and runs the `check`
   command to confirm that it passes. It runs a formatter only when the
   repository that `origin` points to has no open pull requests, because a
   formatter rewrites whole files and conflicts with open branches. If
   agent-cli cannot count them, the script does not format, and says so.
   `--format no` and `--format yes` override that choice. `--only` takes a
   comma-separated list of `python`, `ts`, `dotnet`, and `rust`, to adopt some
   ecosystems now and the rest later.

4. Read the report. "Done" lists what changed, and "Skipped" lists what did
   not change and why. To retry a skipped ecosystem after fixing its cause,
   run the script again with `--allow-dirty --only <ecosystem>`, because the
   working tree now holds the first run's changes. "Follow up" lists the work
   left for cleanup pull requests, such as TypeScript compiler options that
   still fail. File a work item for each one.

5. Run the `check` command at the end of the report yourself, and confirm
   that it passes.

6. Record that command under `## Commands` in the repository's `AGENTS.md`.
   Add it to the pipeline that runs on pull requests, so CI runs the same
   command.

7. Commit, push, and open the pull request, linked to a work item for the
   adoption. Say in the description that the change is mechanical, so
   reviewers read the config and skim the suppressions.

To confirm it worked, check that CI passes on the pull request with the new
`check` command.

## Burn down the suppressions

From the day of adoption, new code must pass every rule in the baseline,
including inside feature pull requests. The rules are the files in this
folder. For example, the Python baseline flags `print()` calls. Remove the
existing suppressions in dedicated cleanup pull requests, never inside a
feature pull request, because lint churn in a feature diff makes review worse.

Start with the suppressions that turn a rule off for the whole repository,
because they leave new code unchecked too:

- .NET: the rules set to `suggestion` under "Suppressed at adoption" in
  `.editorconfig`.
- Rust: the lints set to `"allow"` in the team lint block of `Cargo.toml`.
- TypeScript: the compiler options the report left off.

The rest suppress single lines, so they can go one rule at a time: `# noqa`
and `# ty: ignore` comments in Python, and `eslint-suppressions.json` in
TypeScript.

## What the baseline contains

| Ecosystem | Files | Where they go |
|---|---|---|
| Python | `python/ruff.toml`, `python/ty.toml` | Copied to the repository root |
| Angular | `angular/eslint.config.mjs`, `angular/tsconfig.strict.json` | ESLint config copied; compiler options merged into `tsconfig.json` |
| Angular | `angular/tsconfig.angular-strict.json` | Turned on by hand, once `ng build` passes with it |
| TypeScript | `typescript/eslint.config.mjs`, `typescript/tsconfig.strict.json` | As for Angular |
| .NET | `dotnet/Directory.Build.props`, `dotnet/editorconfig` | Props copied; analyzer block appended to `.editorconfig` |
| Rust | `rust/lints.toml` | Merged into `Cargo.toml` as `[lints.*]`, or `[workspace.lints.*]` |

The script marks every file it writes, so running it again replaces its own
block rather than adding a second one. It never overwrites a config it did not
write.
