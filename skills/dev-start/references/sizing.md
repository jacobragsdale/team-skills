# Sizing the questions

Judge the change's size from the ticket and the code it touches, and question
the developer to match. Unclear requirements are cheapest to fix before any
code exists.

| Size | Looks like | Do |
|---|---|---|
| Tweak | A copy or config change, one obvious edit, or a bug with a clear repro | Ask nothing. State the criteria in one to three lines and ask the developer to confirm them. |
| Normal | One behavior change in one area | Ask one round of at most five questions, then state the criteria. |
| Major | Several PBIs' worth, a new integration, or a design decision nobody has made | Stop. Say it needs a design and a split into PBIs first (/dev-design, once it is installed). |

Ask only what the ticket, the code, and `AGENTS.md` cannot answer, and give
your recommended answer with each question so the developer can reply "yes".
