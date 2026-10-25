---
name: dev-review
description: Review a teammate's pull request against its acceptance criteria and the review checklist, drafting comments for you to post.
argument-hint: <pull request id>
disable-model-invocation: true
---

# /dev-review

Review someone else's pull request so that a person can finish the review in
minutes. The skill reads and drafts. The person decides, comments, and votes.

Paths below are relative to this skill's folder. Run its scripts with
`uv run`, or with `python3` when that is 3.11 or later.

## Writes this skill makes without asking

None. It posts nothing on its own. When the person picks drafts to post, post
exactly those, through `scripts/post.py --skill dev-review`. Voting,
approving, and completing a pull request are always the person's.

## 1. Read the context

Read the `AGENTS.md` of the pull request's repository: this one when its
remote is that repository, or else `~/dev/<repo>/AGENTS.md`. Then gather:

```sh
uv run scripts/ticket.py --pr <id>
agent-cli ado pr get <id> --fields policies,reviewers,open_threads
agent-cli ado thread list <id> --fields id,at,author,text,replies
```

`ticket.py` prints the pull request, its work items, and each PBI's criteria.
The threads show what others have already raised, so the review does not
repeat it. A pull request with no linked work item, or a PBI with no
criteria, is a finding in itself. Review against the description instead.

Done when you have the criteria, the policy results, and the open threads.

## 2. Read the change

```sh
agent-cli ado diff get <id> --names-only
agent-cli ado diff get <id> --file '<glob>'
```

Read the code around each hunk at the pull request's source branch, with
`git fetch origin <source>` and `git show origin/<source>:<path>`. Leave the
person's working tree and current branch as they are.

Done when you have read every changed file, and the code each change calls or
is called by.

## 3. Judge it

Read `references/criteria.md` and `references/review-checklist.md`.

1. A failing policy (build, tests) is the first finding.
2. For each criterion, decide met, not met, or cannot tell, and give the
   evidence: the test that proves it, or the code that falls short.
3. Go through the checklist.

Keep only what the author must act on or know. Each finding has:

- where: the `at` that `diff get` prints, such as `REPO@REF:PATH:LINE`
- what is wrong and why it matters, concretely
- a suggested fix
- whether it blocks the merge

Leave out what linters and CI catch, matters of taste, and praise.

Done when every criterion has a verdict and every finding has all four parts.

## 4. Present the review

Show the person, in this order:

1. A verdict to consider (approve, approve with suggestions, or wait for the
   author) and the one-line reason.
2. A table of the criteria, with each verdict and its evidence.
3. The drafts, numbered, each written as it would be posted: at most 600
   characters, in the voice `references/style.md` describes, blocking ones
   first.

Then ask which drafts to post. Post only those, one call each:

```sh
uv run scripts/post.py --skill dev-review pr-comment <id> --at '<at>' --file - <<'EOF'
<draft>
EOF
```

The person casts the vote.
