# Review checklist

This checklist covers the judgment a linter cannot make. Linting, types,
formatting, and the test run belong to the repository's `check` command and
CI, so a review does not repeat them.

## Criteria

- Every acceptance criterion is met, and a named test proves each one.
- The change holds nothing the criteria did not ask for: no unrequested
  features, refactors, or configuration changes riding along.

## Correctness

- Input is validated where it enters: requests, messages, files,
  configuration, and database rows. Code past that point works with typed
  values, not raw dictionaries.
- There are no silent fallbacks. A caught error is handled, or logged with its
  context and raised again. It never becomes a default value that hides an
  outage.
- Errors say what failed and on which input, without leaking secrets or
  personal data.
- The edge cases the criteria imply are covered: empty, missing, duplicate,
  too large, concurrent, and retried.
- Times are timezone-aware, and money is never a float.
- Every external call has a timeout, and retries are bounded and only repeat
  operations that are safe to repeat.

## Tests

- A bug fix comes with a test that failed before the fix.
- Tests check behavior, not implementation. No test only asserts that a mock
  was called.
- No test was deleted, skipped, or loosened to make the suite pass.

## Operations

- The logs and metrics are enough to diagnose the new code path in
  production.
- Configuration and schema changes work with what is deployed now, or the
  pull request says how they roll out.
- No secrets, hostnames, or values specific to one environment are in the
  code.

## Readability

- Names say what things are, and a reader can follow the change without the
  pull request description.
- Dead code, debug output, and commented-out code are gone.
- When behavior changed, the README, the API reference, or `AGENTS.md` changed
  with it.
