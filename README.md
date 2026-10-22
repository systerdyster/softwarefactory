# Software factory

A Claude Code plugin that turns an approved case into a PR: `/factory add` frames a case with the
developer into a spec and a plan, and `/factory run` builds queued jobs: a fresh builder makes the
change with meaningful tests for the behaviour it changes, a main reviewer checks it (with specialists
where the change calls for them), and the developer approves before the PR. Python standard library
only. It was first built from a blueprint prompt; this repo is now the source.

## The flow

1. **Builder** — the change, with tests where behaviour changes (logic and helpers above all), TDD
   where it helps, and for a bug a test that reproduces it: tests.md records how it failed before the
   fix. tests.md maps each acceptance criterion to its test. No commit order is imposed.
2. **verify** — the project's tests, build and lint (`[factory] verify`, default lint, build, suite);
   red sends the builder back within the round. A result counts only for the same head and commands.
   The builder runs targeted checks while it works; this is the one full run. A rebase that leaves
   HEAD unchanged reuses a green result; one that moves the code is checked again.
3. **Tidy** — once per job, after round 1's first green verify: a fresh agent makes the change smaller
   and simpler without changing behaviour — needless comments, tests that add nothing (never one
   tests.md maps to a criterion or a reproduction), dead code, needless complexity in the files the
   change touches, and whatever the change left without a user, wherever it lives. Then verify again;
   red, unfinished or uncommitted resets the branch to the builder's result. round-1/tidy.md lists each
   removal for the reviewer and the PR.
4. **Review**: the main reviewer (`code`) always; a specialist (security, performance, stability, ui,
   ux, platform, codex) when plan.md's `Specialists:` line names it or its `[paths]` pattern matches.
   The factory reads each verdict from the findings — CHANGES only for a BLOCKING one — and merges all
   findings into one `findings.md`, same `path:line` = one item.
5. **Fix rounds** rerun only the reviewers that blocked, on the diff since their last look.
6. **Needs-human, ready** — every job stops for the developer's approval, with the diff size against
   `max_diff` and the main review's least-sure list. Then rebase, the project's checks, PR.

spec.md's `## Decided` is settled for every agent; project.md's `[rules] docs` (a wiki's
architecture and component pages) are rules every brief lists.

## Several jobs at once

- **One worktree per job**, `factory/workspaces/<id>/`, on the job's branch: created at `start`
  (workspace_setup there — for pnpm its own node_modules from the shared store), kept through
  building, review, fixes and waiting for approval, removed at finish or cancel. Setup reruns only
  when a listed lockfile changes.
- **At most `[factory] parallel` active jobs** (1 or more, default 2; `/factory init` asks the
  developer). The tests fill three slots with their own worktrees and ports; real agents have run
  at most two. `F next --lease` returns them: jobs holding a
  slot keep it, a job coming back from waiting (a rework) takes the next free one, then the queue.
  A job waiting for a human keeps its worktree but holds no slot.
- **One orchestrator**: `F lease acquire` (a stale lease is taken over only with `--takeover`); `F
  next` refuses without the current lease, so a second orchestrator takes no job.
- **Explicit job identity**: `ws run <job>`, `git -C factory/workspaces/<job>`, logs under
  `factory/logs/<job>/`, verify results in the job's round folders.
- **Agent runs** (`factory/runs/`): `F brief` reserves the run and refuses while a live one holds the
  same work, so a builder attempt or a reviewer is never started twice, also after a restart. The
  plugin's PostToolUse hook on the Agent call attaches the `agentId` Claude Code returns; SubagentStop
  ends it; a reservation no agent took expires after three minutes, a running agent that goes silent
  after the suite timeout. state says `wait-agent` while a job's agent runs.
- **Binding from the brief, not the path**: the guard allows a write only through a live running run
  whose `agent_id` is the hook's, in that run's job and role. A done, aborted or expired run's agent
  writes nothing; without `agent_id` nothing is written. If an agent's id matches no run, the reason
  is recorded and `F next` stops the factory — it never falls back to weaker isolation.
- **Ports**: each job has one port, `FACTORY_PORT`, kept in its workspace.json — the same for all its
  commands, never another job's, re-picked when something outside holds it. A `[server] command` is
  started on it for the commands in `[server] for`, waited for and stopped; a test command may also
  start its own server on it. `FACTORY_OUTPUT` is the job's own folder.
- **Shared data**: the commands in `[factory] serial` (migrations, seeding, tests writing a shared
  database) share one lock; everything else runs in parallel.
- **Order between jobs** is explicit: `queue add --after <id>` waits until that job is done. Known
  overlaps in shared contracts, styles or dependencies are noted at intake; nothing tries to predict
  file conflicts.
- **Integration** is one job at a time, with the developer's approval per job and no new automatic
  merge. When the target moves, an approved job rebases and runs the checks again before its PR —
  an open PR too: `pr status` fetches the target, the branch is rebased and checked, and the same PR
  is updated. A clean rebase keeps the approval and, when nothing moved, reruns nothing; a rebase
  that needed conflict resolution goes back through the builder and review and needs approval again.

## Install

Once per developer, from this repo as a local marketplace:

```
/plugin marketplace add ~/Projects/Private/software-factory
/plugin install factory@software-factory          # user scope
```

Use user scope while the marketplace is a local path: project scope writes the path into the
repo's committed settings, which breaks for everyone else. Then, in each project: `/factory init`.

A plugin from a local-directory marketplace loads in place, so edits here take effect at the next
session start or `/reload-plugins`, with no version bump. To try a change without the installed
copy: `claude --plugin-dir ~/Projects/Private/software-factory/plugins/factory`.

## Layout

```
.claude-plugin/marketplace.json
plugins/factory/
  .claude-plugin/plugin.json
  bin/factory-cli, factory-dashboard      on PATH while the plugin is enabled
  hooks/hooks.json                        the PreToolUse guard for factory agents
  skills/factory/
    SKILL.md, reference/                  routes init | add | run | stop | approve | rework | cancel | status
    bin/factory.py, hook.py, fx/          every mechanical step; prints JSON, exits non-zero on failure
    bin/tests/                            unittest, real temp git repos with a bare origin
    server.py, dashboard.html             dashboard + inbox, 127.0.0.1 with a token
  agents/factory-*.md                     builder, tidy, review-code (main), review-{security,performance,
                                          stability,platform,ux,ui}, codex-fallback
```

A personal tool: in a project the factory changes no committed file. It leaves only `factory/`
(runtime state, ignored through `.git/info/exclude`) and the allowlist that `/factory init` adds to
`.claude/settings.local.json`.

Run the tests with `python3 -m unittest discover -s plugins/factory/skills/factory/bin/tests` from
the repo root (or `-s tests` from `skills/factory/bin`).

## Decisions taken while building, beyond the blueprint

- **One plugin-level PreToolUse hook** (`hook.py`) enforces the per-role limits. Plugins ignore
  `hooks` in agent frontmatter, so the hook runs for every matching tool call in every session,
  returns at once unless `agent_type` is a `factory:` agent, and fails closed for an unknown factory
  agent name. Reviewers: read-only git, the project commands `[reviewers] commands` lists, and their
  own review file. Builder: the workspace, build.md and tests.md, never push/switch/rebase/amend/
  `--no-verify`. No agent may edit the plugin's files or spawn agents.
  Shell operators are refused, so every command matches the allowlist plainly. `workspace check`
  after review stays as the second line of defence.
- **Agents run project commands through `factory-cli ws run <job> <name>`**, named in project.md's
  `[commands]`, so one allowlist rule (`Bash(factory-cli:*)`) covers every project's build, lint and
  test without listing free-form shell, and it does not change between plugin versions.
- **The project root comes from where the command runs** (`FACTORY_ROOT`, else `CLAUDE_PROJECT_DIR`,
  else git), since the code no longer sits inside the project.
- **Agents are spawned by their scoped name**, `factory:factory-builder` and so on.
- **`F brief` writes every agent prompt from `state`**, and refuses an agent state didn't ask for. The
  orchestrator never composes a brief.
- **A full-suite `verify` after every builder round**, before reviewers spend a round on red code. A red
  verify reruns the builder in the same round (max 3 attempts), not a new review round.
- **Stuck detection**: an agent spawned three times for the same head without progress goes to
  needs-human instead of looping.
- **Rebase conflicts stay in progress** and go to the builder; `rebase --continue` is the script's. A clean green rebase keeps
  the approval (heads are tracked as equivalent); anything an agent resolved is reviewed again.
- **`auto-merge` = PR with auto-complete** (`gh pr merge --auto` / `az repos pr update --auto-complete`),
  so branch policies still apply. **`local-merge`** builds the `--no-ff` merge with plumbing and refuses
  when the target branch is checked out anywhere (moving the ref under a checkout would touch the
  developer's checkout); the job then goes to needs-human with the command to run.
- **approve / rework / cancel run only in the orchestrator.** Other sessions and the dashboard queue
  them in `factory/inbox/`; the orchestrator watches it with Monitor (`F inbox watch`).
- **Jira is done by the orchestrator through the session's Jira tools**, not by scripts; `state`
  lists the pending transitions and the one hand-back comment, and `jira-log` makes them idempotent
  across restarts.
- **Simplified after the first real job** (001-icon-ui-kit-sync, 2026-10-21: 19 agent starts, 13 of
  them reviews, ~54 minutes). Gone: the separate test-writer and its objection loop (eight assertions
  contradicted the approved design and cost a hand-off and a builder rerun for no code change), the
  approver agent (its escalation was foreseen in the spec, and a human approves every job anyway), and
  every-reviewer-every-round. Added: verdict from BLOCKING only, the merged findings list (code and ui
  had reported the same CSS; code and stability had given opposite advice on one helper), spec.md's
  `Decided`, `[rules] docs` (the code reviewer had missed rules that live in the wiki) and render
  commands for ui and ux (they had rendered nothing although the project has browser tests).
- **Tests follow the developer's preference, not a ritual.** A first version required every test in
  the branch's first commit and a red-check that they failed there; that made the order of work the
  rule. Now behaviour must have meaningful tests, a bug fix records its reproduction, and the reviewer
  judges test quality — a test that passed before the change can still protect behaviour. verify
  runs the project's tests, build and lint, not only `suite`. A review report the parser can't read
  safely (CHANGES with nothing marked BLOCKING, BLOCKING outside a finding item) goes back to its
  reviewer instead of reading as PASS.
- **A tidy agent, not a stricter reviewer.** The main reviewer already flagged dead code, padding
  tests and complexity, but only as NOTEs: they were fixed only if some other finding forced a fix
  round, so a job that passed round 1 carried its clutter into the PR. Making clutter BLOCKING would
  have cost a full builder and review round per comment. The tidy pass runs before review, so the
  reviewer sees the final code and no extra review round is spent; it runs once per job, since fix
  rounds are small. Its scope is the whole touched files (the PR may carry some cleanup of older code,
  listed in tidy.md) plus anything the change orphaned. Anything short of clean and green is
  discarded rather than repaired: the builder never fixes the tidy pass's mistakes.
- **The run ends when the queue is empty and every job is finished**, not when nothing can be built:
  only the orchestrator runs inbox commands and polls PRs, so a run that ended on a job waiting for
  approval left the dashboard's approve unread until the next `/factory run`. `next` now says `wait`
  while jobs wait for a human or a PR, and the orchestrator blocks in `F wait` until an inbox command
  arrives or nine minutes pass (under Bash's ten-minute limit, so PRs are still polled).
- **The dashboard computes the board when it is asked** (at most every two seconds), from the job
  folders, runs and git, instead of reading `board.json`: the orchestrator writes that only between
  its steps, so a job showed "building" for as long as an agent worked. Each job is a pipeline —
  start, build, verify, tidy, review (each reviewer running, waiting or done), approve, PR — with the
  running agent's time. Every git call runs with `GIT_OPTIONAL_LOCKS=0`, so these reads never take
  `index.lock` from under an agent's commit.
- **One dashboard per project**, recorded in `factory/dashboard.json` (URL, pid, port; mode 0600,
  removed on exit). `/factory run` starts it, or links to the one already running; started by hand
  it reads the queue and jobs without an orchestrator, and its header says so, since commands then
  wait in the inbox for the next run.

## Proven, and not yet

Proven by the tests and by a scratch install: every script, the state machine, one job end to end
through the CLI (queue → builder → verify red on lint → builder → verify green → tidy → verify green →
numbered BLOCKING review → merged findings → fix round → review →
human approval → rebase onto a moved target → PR → merged → cleaned up), the guard against a real hook invocation, the dashboard's
token/host/origin/path checks, `init-files`, `allowlist`, and Codex detection against
the installed CLI (0.153.4).

**Not yet run for real** — the build order says each stage is proven on a real job before the next:

- A real `/factory run` in a project: Claude Code spawning the agents, the plugin hook firing with real `agent_type` values,
  parallel reviewers, Monitor on the inbox.
- `gh` and `az` PR calls (create, edit, status, comments via `az devops invoke`, auto-complete) — written
  against their documented flags, exercised only with `pr_tool = other`.
- A real `codex exec` review (detection is verified; the call sends code to OpenAI, so it waits for a
  project that has enabled it).
- Jira claim, transitions and the hand-back comment.
- Two real jobs at once: the orchestrator alternating between them with background agents; the
  Agent call's returned `agentId` being the same value the hooks see as `agent_id` (documented as
  "identifier for the subagent run" and "unique identifier for the subagent", not documented as
  equal — if they differ, nothing writes and `F next` stops with the reason); PostToolUse firing for
  background Agent calls and SubagentStop for factory agents; two `pnpm install`s in parallel sharing
  the store; the project's dev server and browser tests honouring `$FACTORY_PORT` /
  `$FACTORY_OUTPUT`; a merge in Azure DevOps moving the target under the other job's open PR.

## Gotchas

- Each job's worktree is a full repo copy at `factory/workspaces/<id>/` inside the checkout. Tools that read
  `.gitignore` themselves, or no ignore file at all (some watchers, linters, test globs, IDE indexers),
  don't see `.git/info/exclude` and will see it; init checks for this and reports it.
- `workspace_setup` output (node_modules, copied `.env`) must be gitignored in the project, or the
  workspace never reads as clean.
