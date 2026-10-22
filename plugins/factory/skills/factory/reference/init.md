---
name: factory-init
description: /factory init — inspect the repo, write factory/project.md and the permission allowlist for human review, create and prove the workspace
metadata:
  type: reference
---

# /factory init

**Goal:** a `factory/project.md` a human has reviewed, a permission allowlist so background agents
never stall on a prompt, and a workspace proven to pass the project's tests, build and lint — before any job
starts. `F` is `factory-cli`, which the plugin puts on PATH.

Re-running init on a configured repo is safe: show what exists, and change only what the human asks.
When project.md lacks a section the template has (an older init), go through that section's questions
and add it.

## 1. Create the runtime folder

`F init-files` creates `factory/` (jobs, inbox, logs), a `project.md` from the template, `backlog.md`,
and, unless git already ignores it, adds `/factory/` to `.git/info/exclude`. Nothing committed
changes: the factory is the developer's own tool, not the team's.

## 2. Inspect the repo and fill in project.md

Read the configuration files themselves — never a readme's claim about them — and fill in every
section of `factory/project.md`:

- **Stack** — languages, frameworks and versions, from the project and package files.
- **Commands** — `build`, `lint`, `suite` (the full suite), `test` (a single test file or name, with
  `{target}` where the argument goes), plus any other the agents will need (`install`, `format`,
  `typecheck`). Run each once in the main checkout's terms to confirm it exists; don't guess flags.
- **What verify runs** — `[factory] verify`: the command names that together run the project's tests,
  build and lint. Without it the factory runs `lint`, `build` and `suite`, each where defined. Read the
  scripts: when one command already runs another (a `test` script that lints first, a `build` that
  type-checks), list only the covering one, so nothing runs twice; when something is in neither
  (`typecheck`), add it.
- **Test conventions** — read existing tests, at least three: runner, location, file naming, test
  naming, altitude (unit / component / integration / end-to-end, and what this project prefers for
  what), assertion style and libraries, fixtures and builders in use. Quote a short real example.
- **workspace_setup** — what a fresh worktree needs before the checks pass. Every job gets its own
  worktree and its own dependencies: for pnpm `pnpm install --frozen-lockfile` (its own node_modules,
  from pnpm's shared store; never share node_modules between worktrees). Copy gitignored local config
  from the main checkout (`.env`, `appsettings.Development.json`, user-secrets…) with
  `$FACTORY_REPO_ROOT`, never an absolute path. List the `lockfiles` (and install settings such as
  `.npmrc`) whose change means setup must rerun.
- **Jobs at once** — `[factory] parallel` (1 or more, default 2). **Ask the developer** how many jobs
  to build at once, after you have read what worktrees would share (below), and tell them the cost:
  each job is its own worktree with its own dependencies and its own agents running at the same time,
  and jobs are still integrated one at a time. Read the commands for what the worktrees would share:
  - **Ports and output.** Each job has one port, `$FACTORY_PORT`, the same for all its commands, and
    its own `$FACTORY_OUTPUT`. A dev server, browser test or screenshot command must use them —
    change the command in project.md (`pnpm dev --port $FACTORY_PORT`, a Playwright config reading
    the variable), or the project's config if it can't take them, which is a change for the human to
    decide. A test runner that starts its own server keeps doing so on that port. If the tests need
    a server the command doesn't start, set `[server] command` and list the commands in `for`: the
    factory starts it on the port, waits until it listens, and stops it after.
  - **Shared data.** Migrations, seeding or tests that write a shared database go under
    `[factory] serial`; they share one lock. Read-only use of a shared backend needs nothing.
  - If something can't be separated, say so and propose `parallel = 1`.
- **Written rules** — `[rules] docs`: globs of the project's own rule pages beyond CLAUDE.md and
  AGENTS.md — a wiki's architecture and component pages, ADRs, a styleguide (`Wiki/*.md`,
  `docs/adr/**`). Every agent's brief lists them as rules. Look for them; ask which count as rules.
- **Code comments** — `[rules] comments`: when agents write them. Left empty, the factory's default
  applies: none by default, one only where the why is a trap the code can't show, the surrounding
  file's style kept. Note what the project already does (section headers, a required comment) — a
  written rule in the docs wins over the default — and let the developer confirm.
- **Documentation voice** — `[rules] writing`: how agents write wiki pages and other docs. Left
  empty, the factory's default applies: plain framing, then the exact ingredients, then the gotcha;
  present tense, no pasted code, one page per fact that needs upkeep, the neighbours' language. Note
  a wiki-conventions page or skill if the project has one — its rules win — and let the developer
  confirm.
- **Reviewers** — every job gets the main review (`code`); security, performance and stability are
  specialists available everywhere. Enable the optional ones that fit (`platform` for a CMS or
  framework with traps, `ux` and `ui` for user-facing front ends). Leave `codex` off and say why: it
  sends code to OpenAI, so the client agreement must allow it, and each developer needs the Codex CLI
  and their own OpenAI access.
- **Path patterns** per specialist — globs (`src/components/**`, `*.scss`) or `re:` patterns matched
  against the diff text (`re:\bIMemoryCache\b`). A specialist runs in a job's first round when the plan
  names it or one of its patterns matches the change, so set patterns for security (auth, endpoints,
  data access), performance and stability too.
- **Render commands** — `[reviewers] commands`: the project commands the ui and ux reviewers may run
  to see the change, e.g. a browser test project that screenshots components (Playwright, Vitest
  browser mode). Say in project.md what each one renders and where its screenshots land; their output
  must be ignored by git, or the workspace check discards the round. Without one the reviewers can
  only read code, and say so.
- **Platform** — for the platform reviewer: the platform, version, and any project-specific traps.
- `max_rounds` (default 3), `max_diff` (changed lines flagged to the approving developer; default 800), remote,
  PR tool (`gh`, `az` or `other`, from the remote URL).
- **Hand-off** — how a finished job reaches the team. These are the team's choices, not facts in the
  repo, so **ask about each one** even when you have a good guess; propose the guess as the
  recommendation:
  - **Target branch** — always confirm, even when only one branch exists (`main`, or `develop` in a
    gitflow project). Never a silent default.
  - **merge_mode** — `pr` (recommended), `local-merge` or `auto-merge`.
  - **Branch name** — the `[handoff] branch` pattern. Propose one from the existing branches and merge
    history (`feature/{key}-{slug}`, `bugfix/{key}`…); `factory/{id}` only if the team has no
    convention.
  - **Draft and reviewers** — open the PR as a draft or ready for review; which people or groups to
    add as reviewers, and which are required.
  - **Linking** — where the Jira key goes (`jira_link`: title, body, both, none) and the issue URL
    prefix (`jira_url`); for `az`, whether to link the Azure Boards work item.
  - **Language and template** — the language the team writes PR descriptions in, and the project's
    PR template if there is one (look for `.azuredevops/pull_request_template.md`,
    `.github/pull_request_template.md`, `docs/pull_request_template.md`) — confirm it rather than
    assume.
  - **Commit messages** — `commits`: how the agents write them. Look for a written convention in the
    rule files and docs (language, prefixes, case key, body length, a commit-msg hook) and read the
    recent history. Left empty, the factory's default applies: an imperative short subject, a 2–3
    line body only for the why, no case key in the subject when the branch carries it. Propose the
    default, or what the project writes down, and let the developer confirm. AI attribution
    (`Co-Authored-By`, "Generated with") is never allowed, whatever this says.
- **Jira** — on or off, and the factory-stage → Jira-status mapping, including where a cancelled
  issue goes. Read the project's workflow statuses from Jira rather than guessing names.

Then **show the human the whole file and ask them to review it**. Ask about anything you could not
establish, one question at a time with a recommendation.

## 3. Propose the allowlist

`F allowlist` prints the permission rules the agents need in `.claude/settings.local.json`: the
factory script (through which agents run the project's build, lint and test commands) and the git
commands agents use in a job's worktree. Rules already in the shared `.claude/settings.json` count as
present. `git push` is deliberately absent; only the orchestrator pushes, through the script. Show the
list, and on the human's yes run `F allowlist --write`. Local settings are not committed.

The per-command limits (no shell operators, no switching or pushing, reviewers running only their
listed commands and writing only their review) are enforced by the plugin's PreToolUse
hook, which recognises each factory agent by name, not by the allowlist.

## 4. Prove the target

`F suite` — a throwaway worktree at the target branch, workspace_setup run in it, the project's
checks (`[factory] verify`) once, then removed. Each job later gets its own worktree,
`factory/workspaces/<id>/`, when it starts.

**If a check is red, stop.** Report the failing check and what failed from the log it names. The factory does not
start on a red target; fixing it is human work that comes first. If setup fails, report which step and
its output.

Check that the project's tooling ignores `factory/` (watchers, linters, test globs, IDE indexers),
since each job's worktree is a full copy of the repo inside the checkout. Tools that read `.gitignore`
themselves don't see `.git/info/exclude`. Where one would scan `factory/`, tell the human which tool
and what it would take — don't edit the project's committed config on your own.

## Done means

project.md reviewed by a human; allowlist written or consciously skipped; `F suite` green. Report
those three lines.
