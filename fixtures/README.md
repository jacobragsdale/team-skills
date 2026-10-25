# Fixtures

A fixture is a short note on a failure that changed a skill. Each fix to a
skill's behavior adds one, at `fixtures/<skill>/<YYYY-MM-DD>-<slug>.md`. The
format is loose. A note says what was asked, what went wrong or felt wrong,
and which rule changed. For example:

```markdown
Asked: /dev-finish on a two-line config fix.
Went wrong: the pull request description ran to 40 lines, most of it
restating the diff.
Rule changed: references/style.md now gives the description's shape, and
post.py caps descriptions at 2000 characters.
```

A fixture explains why its rule exists. Its prompt can be replayed when the
skill changes, as a regression check. After a model upgrade, replay the
fixtures without their rules: a rule the agent now follows anyway gets
deleted, so the skills get shorter over time instead of only longer.
