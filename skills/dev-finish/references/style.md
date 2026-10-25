# Writing in Azure DevOps

Everything an agent writes in ADO is brief, plain, and marked. `post.py` adds
the footer and enforces the length caps. These rules cover the rest.

- **A work item comment** is at most two sentences: what happened, and what
  comes next.
- **A pull request description** is two to four sentences on what changed and
  why, then a spaced list of the changes, then "How to test".
- **A QA task description** has one short block per criterion: the steps, the
  expected result, the test data, and where to test.
- Write in plain words and the reader's terms. Use no greeting, no emoji, and
  no restating of the ticket back to the people who wrote it.
- Refer to work items as `#1234` and pull requests as `!431`.
- Mention a person (`@<Display Name>`) only when they must act.
- Never paste logs, stack traces, or secrets. Say where to find them instead.

A pull request description:

```markdown
<Two to four sentences: what changed, why, and anything a reviewer must know.>

- <one change>

- <another change>

**How to test:** <the command or the steps, and what to expect.>
```

A QA task description, one block per criterion:

```markdown
**AC1.** <the criterion, shortened>
Where: <environment and URL, endpoint, or job>
Data: <the test data, and how to get it>
Steps: <numbered steps, in one line if short>
Expect: <the observable result>
```
