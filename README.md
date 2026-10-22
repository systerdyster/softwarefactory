# Software factory

A Claude Code plugin that turns an approved case into a PR: `/factory add` frames a case with the
developer into a spec and a plan, and `/factory run` builds queued jobs: a fresh builder writes the
tests first and then the change, a main reviewer checks it (with specialists where the change calls
for them), and the developer approves before the PR. Python standard library only. Built from the
blueprint in `~/Brain/library/claude-code/blueprint-software-factory.md`; this repo is now the source.

## The flow

1. **Builder**, round 1: the plan's tests in the branch's first commit, listed in tests.md, then the
   change. **red-check** runs those tests on that first commit and requires them to fail.
2. **verify** — the full suite; red sends the builder back within the round.
3. **Review**: the main reviewer (`code`) always; a specialist (security, performance, stability, ui,
   ux, platform, codex) when plan.md's `Specialists:` line names it or its `[paths]` pattern matches.
   The factory reads each verdict from the findings — CHANGES only for a BLOCKING one — and merges all
   findings into one `findings.md`, same `path:line` = one item.
4. **Fix rounds** rerun only the reviewers that blocked, on the diff since their last look.
5. **Needs-human, ready** — every job stops for the developer's approval, with the diff size against
   `max_diff` and the main review's least-sure list. Then rebase, suite, PR.

spec.md's `## Decided` is settled for every agent; project.md's `[rules] docs` (a wiki's
architecture and component pages) are rules every brief lists.

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
  agents/factory-*.md                     builder, review-code (main), review-{security,performance,
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
- **Agents run project commands through `factory-cli ws run <name>`**, named in project.md's
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
  every-reviewer-every-round. Added: red-check, verdict from BLOCKING only (stability had returned
  CHANGES for a finding it called non-blocking), the merged findings list (code and ui had reported the
  same CSS; code and stability had given opposite advice on one helper), spec.md's `Decided`,
  `[rules] docs` (the code reviewer had missed rules that live in the wiki) and render commands for ui
  and ux (they had rendered nothing although the project has browser tests).

## Proven, and not yet

Proven by the tests and by a scratch install: every script, the state machine, one job end to end
through the CLI (queue → tests first → red-check → two builder rounds → merged findings → review →
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

## Gotchas

- The workspace is a full repo copy at `factory/workspace/` inside the checkout. Tools that read
  `.gitignore` themselves, or no ignore file at all (some watchers, linters, test globs, IDE indexers),
  don't see `.git/info/exclude` and will see it; init checks for this and reports it.
- `workspace_setup` output (node_modules, copied `.env`) must be gitignored in the project, or the
  workspace never reads as clean.
