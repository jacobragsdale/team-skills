# Acceptance criteria

The criteria live on the PBI. Every skill reads them from there, and people
refer to a criterion by its number ("AC2").

Write a numbered list. Each criterion is one behavior a person can observe
from outside the code, with concrete values:

```markdown
1. When <situation>, <who or what> <observable result>.
```

- **Observable.** An API response, a page, a file, a row, a log line, or a
  job's result. Never how the code does it.
- **Concrete.** Real values, limits, messages, and status codes. "Handles
  errors" is not a criterion. "Answers 429 with `Retry-After`" is.
- **One behavior each,** so one test proves it and one QA step checks it.
- **A bug** gets a criterion for the fixed behavior, written from the repro.
- **Seven or fewer.** More means the PBI holds more than one change, so
  suggest splitting it.
- **Out of scope** goes on an optional last line, when a reader could
  reasonably expect something this PBI will not do.

Example:

```markdown
1. When a client sends its 101st request within a minute, the API answers 429
   with a `Retry-After` header that gives the seconds left in that minute.
2. When the minute ends, that client's next request succeeds.
3. Requests to `/health` are never limited.

Out of scope: separate limits per tenant.
```
