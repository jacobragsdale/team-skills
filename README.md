# Team skills

Explicitly invoked agent skills (`/dev-start`, `/dev-finish`, `/dev-review`,
and others) that carry an Azure DevOps work item from start to merge, for
teams that write their code with agents in Cursor and GitHub Copilot on WSL.

No skill is built yet. The repository holds the design that the v1 skills
will follow.

- [DESIGN.md](DESIGN.md) explains how the system fits together and why: the
  ticket backbone, acceptance criteria as the thread between skills, the
  quality gates, the shape of each repository's `AGENTS.md`, and the skill map.
- [AGENTS.md](AGENTS.md) holds the rules for changing this repository, for
  people and agents alike.

The skills reach external systems through
[agent-cli](https://github.com/jacobragsdale/agent-cli).
