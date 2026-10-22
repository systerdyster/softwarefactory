---
name: factory-orchestrator
description: /factory run — the build loop; how the orchestrator asks state, performs each next action, spawns fresh agents, and handles inbox commands, objections, cancel and Jira
metadata:
  type: reference
---

# /factory run — the orchestrator

You build queued jobs — up to two at once (`[factory] parallel`) — until the queue is empty and every
job is finished: a job waiting for the developer's approval or for its PR keeps the run going. You
spawn every agent, you are the only writer of `factory/board.json`, and you are the only session that
pushes. Run this session on sonnet (`claude --model sonnet`); if it isn't, say so once and continue.
`F` is `factory-cli`, which the plugin puts on PATH.

**Mechanical questions have scripts; you trust their output.** You never decide from memory where a
job stands: **ask `F state <id>` before every action** and do exactly the `next.action` it names. You
make a judgement call only where this file says so — objection rulings, and nothing else about a
job's flow. A restarted orchestrator loses nothing: everything is in the job folders and git.

**Every job is separate.** Each has its own worktree, `factory/workspaces/<id>/`, on its own branch,
kept from start until it is finished or cancelled. Every command and brief names its job; an agent
is bound to the job its brief names. A failure or a wait in one job never holds up the other.

## Start

1. `F lease acquire` — one orchestrator at a time. Keep the `lease` it returns for this run. It
   refuses while another orchestrator holds the lease: if the human says that session is gone, use
   `--takeover` (allowed once the lease is stale) or `--takeover --force`. Never take over on your own.
2. `F stop --clear`, then `F board`. Check `factory/project.md` exists; if not, stop and point to
   `/factory init`.
3. **Dashboard** — run `F dashboard` with the Bash tool in the background (`run_in_background`). Read
   its output file once for the line `Factory dashboard: http://127.0.0.1:…/?token=…` and give the
   human that URL. It serves until this session ends; don't start a second one in the same run.
4. Arm the inbox watch (see **Inbox**), then process any pending inbox commands.
5. **Agents from before** — `F run list`. A live run left by an earlier session holds its work, and
   state says `wait-agent` for it until it ends or goes silent. If the human confirms that session
   and its agents are gone, `F run abort <run>` frees the work now; never abort on your own guess.

## The loop

Repeat:

1. **Inbox** — process pending commands (below).
2. **Waiting jobs** — for each job whose stage is `pr-open`, `F pr status <id>` (at most once per ten
   minutes per job). It fetches the target too: a merged PR makes the next action `finish`; a target
   that moved under an open PR makes it `rebase`, and the job takes the next free slot.
3. **Pick** — `F next --lease <lease>`. `jobs` lists the jobs to work on now: those already holding a
   build slot, then a job coming back from waiting (a rework), then new jobs taken from the queue —
   at most two. A job waiting for a human (needs-human, an open PR) keeps its worktree but holds no
   slot; one beyond the slots is listed under `waiting_for_slot`; a job queued `--after` another that
   isn't done is listed under `blocked`. `action` is `stop` (after `/factory stop`, once nothing is
   active) or `idle` (the queue is empty and every job is finished): then `F board`, report (below),
   `F lease release --lease <lease>` and end the run. `action` is `wait` when nothing can be built
   now but jobs wait for a human or a PR: report once (the first time), then run `F wait` (Bash
   timeout 600000) — it returns when an inbox command is pending, or after nine minutes — and go
   round the loop again. Don't end the run on `wait`.
4. **Act** — for **each** job in `jobs`, ask `F state <id>` and perform its `next.action` from the
   table below, then its **Jira** to-dos. Spawn agents in the background, so one job's agents run
   while you act on the other; when an agent returns, ask that job's state again. Never let one job
   wait on the other.
5. **Board** — `F board`.

### Actions

| next.action | Do |
|---|---|
| `start` | `F start <id>`. It creates the job's worktree on its new branch from the target, runs workspace_setup there (its own dependencies), and runs the project's checks (`[factory] verify`: tests, build, lint). Running it again keeps the worktree. **Red: the target is broken.** Report the failing check from the log, `F stop` — fixing it is human work; the other job carries on. |
| `open-workspace` | `F workspace open <id>` — the job's worktree is missing (removed by hand); it is created again on the job's branch. |
| `spawn-builder` | `F brief <id> builder`, then spawn `factory-builder` in the background with it as its prompt, verbatim. The brief reserves the run; the plugin's hook attaches the agent to it when the Agent call returns. |
| `wait-agent` | Nothing: an agent of this job is reserved or running (`next.run`/`next.runs`). Ask again when it returns. |
| `verify` | `F verify <id>` — the project's tests, build and lint on the builder's result (or the tidy pass's, `next.attempt` = `tidy`), in project.md's `[factory] verify` order, stopping at the first red one. Red is not an error to handle: state sends the builder back, or discards the tidy pass. A result is reused only for the same head and the same commands. |
| `spawn-tidy` | Once per job, after round 1's first green verify: `F brief <id> tidy`, then spawn `factory-tidy` in the background with it as its prompt, verbatim. It makes the change smaller and simpler without changing behaviour and writes round-1/tidy.md. |
| `tidy-discard` | `F tidy-discard <id>` — the tidy pass didn't finish cleanly or turned the checks red: the branch goes back to the builder's green result, and the reason is recorded in tidy.md. Review then runs on the builder's result. |
| `schedule` | `F schedule <id>` — writes round-N/scheduled.md. |
| `spawn-reviewers` | See **Reviewers**. |
| `review-reset` | `F review-reset <id>` — discards the round's reviews (and any workspace change a reviewer made), so the round reruns. |
| `judge-objection` | See **Objections**. |
| `rebase` | `F rebase <id>` — fetch, rebase on the target, then the project's checks — unless the rebase left HEAD unchanged and that head already has a green verify with the same commands, which is reused. Conflicts leave the rebase in progress; state sends the builder to resolve them. |
| `rebase-continue` | `F rebase <id> --continue`. |
| `pr` | First write the description: `F pr body <id>` prints the facts (summary, verdicts, test exemptions, the main review's least-sure list, who approved). Write `factory/jobs/<id>/pr-body.md` from them in project.md's `[handoff] language`, following `pr_template` when it is set (fill its sections; leave none empty without saying why). Keep every fact; add none. Then `F pr open <id>` — pushes the branch and opens or updates the PR with that body, the title, draft state, reviewers and links from `[handoff]` (enables auto-merge in `auto-merge` mode). |
| `merge-local` | `F merge <id>`. |
| `finish` | `F finish <id>` — removes the job's worktree and branches, marks `[x]`. Other jobs' worktrees are not touched. |
| `cancel` | `F cancel <id> --reason "<reason>"` (a cancelled job whose branch still exists). |
| `needs-human` | Nothing to do; the job waits for `/factory approve`, `rework` or `cancel`. Report its reason. With `next.ready` every review passed and the job waits for the developer's approval — that approval is always a human's; never approve a job yourself. |
| `wait-pr` | Nothing; the job is waiting on its PR, on the current target. |

Every agent is spawned **fresh** with the Agent tool, `subagent_type` = `factory:` + the agent's name (plugin agents are scoped by the plugin, e.g. `factory:factory-builder`), and the
brief as the prompt — never resumed, never sent a message. The brief comes from `F brief`; never write
or edit one yourself, and keep its last line, `Factory run: <id>`.

**Runs.** `F brief` reserves an agent run and refuses while one already holds that work — the same
builder attempt, the same reviewer in the round — so nothing is started twice, also after a restart.
When the Agent call returns, the plugin's PostToolUse hook attaches the agentId Claude Code returned
to the run; SubagentStop ends it. The guard lets an agent write only through its own live run, in
that run's job. So:

- Spawn the agent right after its brief. A reservation that no agent took expires after three
  minutes, and the work can be briefed again.
- If the hook reports that a run could not be attached, that agent can write nothing: `F run abort
  <run>`, stop the agent (TaskStop) and ask state again.
- When an agent returns, `F run end <run>` (SubagentStop normally did it already), then ask state.
- A cancel aborts the job's runs; their agents lose write access at once.
- If `F next` says `stop` because agents can't be bound to their jobs, report its reason to the human
  and end the run: the factory doesn't build with weaker isolation. `F run binding-reset` once fixed.

`F brief` refuses when state doesn't call for that agent; that means ask state again. Agents return
one line; don't act on it — ask state.

### Reviewers

`next.reviewers` lists who is still missing this round — in round 1 the main reviewer (`code`) plus
any specialist the plan names or the change's paths call for; in later rounds only those whose
findings blocked. **Spawn all of them in one message, in parallel**, then wait until every one has
finished:

- For each name except `codex`: `F brief <id> reviewer --reviewer <name>` → spawn
  `factory-review-<name>` with it. Generate every brief before spawning any.
- For `codex`: `F codex-review <id>`. Exit 3 means Codex is unavailable (not installed, not logged
  in, flags changed, failed): spawn `factory-codex-fallback` with `F brief <id> codex-fallback`.

**A round is done only when every scheduled reviewer's file exists** — never start the builder
mid-round. Then `F workspace check`: if the workspace changed, state says `review-reset`. The factory
reads each review's verdict from its findings — CHANGES only for a BLOCKING one; a report it can't
read safely goes back to its reviewer, and state lists that reviewer again with the reason — and the builder's
next brief carries one merged list (`round-N/findings.md`), not the individual reviews.

### Objections

When the builder stops because the plan can't be followed, state says `judge-objection` — your one
judgement call.

Read the objection (build.md, the round's section), spec.md and plan.md.
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
"<one line>"`. A command that is refused (wrong stage, unknown job, already done) is still marked
done, with `--refused` and the reason as its outcome — the dashboard shows applied and refused apart.
The inbox refuses a second identical command for a job while the first is still pending.

## Commands, as the orchestrator runs them

- **approve `<id>`** — for a needs-human job: `F approve <id> --by "<who>"`. State then takes it
  through rebase, the project's checks and `merge_mode`. **Integration is one job at a time**: act on
  `pr`/`merge-local` for one job, and ask the other's state afterwards — once the target has moved,
  state sends it through rebase and the project's checks again before it is integrated. A rebase
  that needed conflict resolution goes back through the builder and review, and needs approval again.
- **rework `<id>` `<note>`** — for a needs-human or open-PR job: `F rework <id> --note "<note>" --by
  "<who>"`. It records the note (and the PR's comments), starts a fresh round budget, and state runs a
  builder round and review again, and the job comes back for approval; the same PR is updated. The
  job takes the next free build slot (`F next`).
- **cancel `<id>` `[reason]`** — first stop that job's running agents (TaskStop on each background
  agent of that job, none of the other's), then `F cancel <id> --reason "<reason>"`: it closes an open
  PR, removes the job's worktree and branches, marks `[-]` and writes the reason to decision.md. For a Jira
  job, transition the issue per project.md's cancel mapping and unassign it — no comment — and
  `F jira-log` each.
- **stop** — `F stop`; no new job is taken; finish the active ones, then end.

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
title, stage, and the PR link or the needs-human reason. List rejected review reports and codex fallbacks.
Keep it short; the board has the detail.
