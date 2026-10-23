---
name: case-status
description: "Move a Jira case to a status, claim or release it, and post the hand-off test note — the handler a project's factory events point at, and usable by hand. Use when told to set a case's status, claim or unassign a case, or when a factory event names this skill. Takes `[--assign | --unassign] <status>` and `case=<KEY>`, optionally `test_note=<path>` and `developer=<name>`."
argument-hint: "[--assign | --unassign] <status> case=<KEY> [test_note=<path>] [developer=<name>]"
---

# Case status

Bring one Jira case to the state the arguments name, using the session's Jira tools. Without Jira tools
in the session, stop and say so; nothing else stands in for them.

## Arguments

- **`<status>`** — the workflow status to move the case to, by its name in this project's Jira. Empty
  means no transition.
- **`--assign`** — assign the case to the developer (`developer=`, else the current user).
- **`--unassign`** — remove the assignee.
- **`case=<KEY>`** — the case. Without it there is nothing to do; say so and stop.
- **`test_note=<path>`** — a file whose text goes on the case as one comment, exactly as written.

Other `key=value` arguments (`job`, `branch`, `pr`) are context; use them only where a rule below asks.

## Rules

- **Safe to run twice.** A case already in the status is not transitioned again; an assignee already
  set is not set again; a comment whose text is already the case's latest comment is not posted again.
  The factory may repeat an event after a restart.
- **The status must exist.** Read the case's available transitions; when none leads to the named
  status, stop and name the ones that exist. Never pick a nearby status on your own.
- **The test note is posted verbatim**, as the developer approved it. No greeting, no summary of the
  change, no link added. An empty file posts nothing.
- **No other comments**, and no other field changes.

Report one line: what changed on the case, or that nothing needed to.
