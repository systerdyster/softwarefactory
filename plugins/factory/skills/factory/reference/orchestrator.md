---
name: factory-orchestrator
description: /factory run — the build loop; how the orchestrator asks state, performs each next action, spawns fresh agents, and handles inbox commands, objections, cancel and Jira
metadata:
  type: reference
---

# /factory run — the orchestrator

You build queued jobs, one at a time, until the queue is empty. You spawn every agent, you are the
only writer of `factory/board.json`, and you are the only session that pushes. Run this session on
sonnet (`claude --model sonnet`); if it isn't, say so once and continue.
`F` is `python3 .claude/skills/factory/bin/factory.py`.

**Mechanical questions have scripts; you trust their output.** You never decide from memory where a
job stands: **ask `F state <id>` before every action** and do exactly the `next.action` it names. You
make a judgement call only where this file says so — objection rulings, and nothing else about a
job's flow. A restarted orchestrator loses nothing: everything is in the job folders and git.

## Start

1. `F stop --clear`, then `F board`.
2. Check `factory/project.md` exists and `F workspace check` passes; if not, stop and point to
   `/factory init`.
3. Arm the inbox watch (see **Inbox**), then process any pending inbox commands.

## The loop

Repeat:

1. **Inbox** — process pending commands (below).
2. **Waiting jobs** — for each job whose stage is `pr-open`, `F pr status <id>` (at most once per ten
   minutes per job). A merged PR makes its next action `finish`.
3. **Pick** — `F next`. It returns the active job (one that is neither waiting nor finished), or takes
   the next queued job, or says `stop` (after `/factory stop`) or `idle` (queue empty). A job waiting
   for a human — needs-human, an open PR — is not active and doesn't block the next job.
   On `stop` or `idle`: `F board`, report (below) and end the run.
4. **Act** — perform the job's `next.action` from the table below. Then do its **Jira** to-dos.
5. **Board** — `F board`.

### Actions

| next.action | Do |
|---|---|
| `start` | `F start <id>`. It switches the workspace to the job's new branch from the target, reruns setup if a lockfile differs, and runs the full suite. **Red: the target is broken.** Report the failing tests from the log, `F stop`, and end the run — fixing it is human work. |
| `switch` | `F workspace switch <id>` (refuses unless the workspace is clean). |
| `spawn-test-writer` | Spawn `factory-test-writer` with `F brief <id> test-writer` as its prompt, verbatim. |
| `tests-record` | `F tests record <id>`. On failure it marks tests.md so state sends test-writer back; continue the loop. |
| `spawn-builder` | Spawn `factory-builder` with `F brief <id> builder`. When it returns, run `F tests check <id>`. |
| `tests-restore` | `F tests restore <id>`. This is a violation: the builder changed test-writer's files. Note it for the report. |
| `verify` | `F verify <id>` — the full suite on the builder's result. Red is not an error to handle: state sends the builder back. |
| `schedule` | `F schedule <id>` — writes round-N/scheduled.md. |
| `spawn-reviewers` | See **Reviewers**. |
| `review-reset` | `F review-reset <id>` — discards the round's reviews (and any workspace change a reviewer made), so the round reruns. |
| `judge-objection` | See **Objections**. |
| `spawn-approver` | Spawn `factory-approver` with `F brief <id> approver`. |
| `rebase` | `F rebase <id>` — fetch, rebase on the target, re-record test hashes, full suite. Conflicts leave the rebase in progress; state routes them. |
| `rebase-continue` | `F rebase <id> --continue`. |
| `pr` | `F pr open <id>` — pushes the branch and opens or updates the PR (enables auto-merge in `auto-merge` mode). |
| `merge-local` | `F merge <id>`. |
| `finish` | `F finish <id>` — removes the branches, returns the workspace to the target, marks `[x]`. |
| `cancel` | `F cancel <id> --reason "<reason>"` (a cancelled job whose branch still exists). |
| `needs-human` | Nothing to do; the job waits for `/factory approve`, `rework` or `cancel`. Report its reason. |
| `wait-pr` | Nothing; the job is waiting on its PR. |

Every agent is spawned **fresh** with the Agent tool, `subagent_type` = the agent's name, and the
brief as the prompt — never resumed, never sent a message. The brief comes from `F brief`; never write
or edit one yourself. `F brief` refuses when state doesn't call for that agent; that means ask state
again. Agents return one line; don't act on it — ask state.

### Reviewers

`next.reviewers` lists who is still missing this round. **Spawn all of them in one message, in
parallel**, then wait until every one has finished:

- For each name except `codex`: `F brief <id> reviewer --reviewer <name>` → spawn
  `factory-review-<name>` with it. Generate every brief before spawning any.
- For `codex`: `F codex-review <id>`. Exit 3 means Codex is unavailable (not installed, not logged
  in, flags changed, failed): spawn `factory-codex-fallback` with `F brief <id> codex-fallback`.

**A round is done only when every scheduled reviewer's file exists** — never start the builder
mid-round. Then `F workspace check`: if the workspace changed, state says `review-reset`.

### Objections

When the builder stops on an objection, state routes it: a disputed test goes to test-writer first;
if test-writer amends, the builder continues. If test-writer stands firm, or the builder reports a
departure from the plan, state says `judge-objection` — your one judgement call.

Read the objection (build.md, the round's section), test-writer's reply if any, spec.md and plan.md.
Decide against **what was approved**, nothing else:

- **The answer is unambiguous within the approved spec and plan** → write
  `factory/jobs/<id>/round-<N>/ruling-<A>.md` starting `RULING: CONTINUE`, then the answer the builder
  needs, citing the spec or plan line that settles it.
- **Any answer would change scope, design or acceptance criteria** — or the spec is genuinely silent —
  → `RULING: NEEDS-HUMAN`, with the question a human must answer. The job goes to needs-human.

`<N>` and `<A>` are the `round` and `attempt` state gives. When in doubt, it's a human's call.

## Inbox

The dashboard, and `/factory approve|rework|cancel` typed in other sessions, write commands to
`factory/inbox/`. Watch it with the Monitor tool: command `F inbox watch`, description "factory inbox
commands", `timeout_ms` 1800000, re-armed on every expiry for as long as the run lasts. Each event
line names a file; `F inbox list` shows all pending ones.

Run each command **exactly like its terminal form** (below), then `F inbox done <file> --outcome
"<one line>"`. A command that is refused (wrong stage, unknown job) is still marked done, with the
refusal as its outcome.

## Commands, as the orchestrator runs them

- **approve `<id>`** — for a needs-human job: `F approve <id> --by "<who>"`. State then takes it
  through rebase, the full suite and `merge_mode`.
- **rework `<id>` `<note>`** — for a needs-human or open-PR job: `F rework <id> --note "<note>" --by
  "<who>"`. It records the note (and the PR's comments), starts a fresh round budget, and state runs a
  builder round, review and the approver again; the same PR is updated. If the job isn't the active
  one, it is picked up when the active job finishes or waits.
- **cancel `<id>` `[reason]`** — first stop the job's running agents (TaskStop on each background agent
  of that job), then `F cancel <id> --reason "<reason>"`: it closes an open PR, removes the branches,
  returns the workspace to the target, marks `[-]` and writes the reason to decision.md. For a Jira
  job, transition the issue per project.md's cancel mapping and unassign it — no comment — and
  `F jira-log` each.
- **stop** — `F stop`; finish the active job, then end.

## Jira

`F state` lists `jira_todo` for Jira jobs: transitions per project.md's mapping, an unassign on cancel,
and **one** comment at hand-back (PR opened, or merged in local-merge mode). Do each with the Jira
tools, skip a transition the issue is already in, and record it with `F jira-log <id> <event>` (the
event string from the to-do) so it is never repeated after a restart.

The comment is for content editors, not developers: a short, plain-language note on **how to test
the change** — where to go, what to do, what they should see — written from spec.md's acceptance
criteria. No code, no branch names, no review details. No other comments, ever.

## Report

At the end of a run, and whenever a job reaches needs-human or opens a PR: per job, one line — id,
title, stage, and the PR link or the needs-human reason. List test-file violations and codex
fallbacks. Keep it short; the board has the detail.
