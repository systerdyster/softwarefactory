---
name: factory-init
description: /factory init — inspect the repo, write factory/project.md and the permission allowlist for human review, create and prove the workspace
metadata:
  type: reference
---

# /factory init

**Goal:** a `factory/project.md` a human has reviewed, a permission allowlist so background agents
never stall on a prompt, and a workspace proven to build and pass the full suite — before any job
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
- **Test conventions** — read existing tests, at least three: runner, location, file naming, test
  naming, altitude (unit / component / integration / end-to-end, and what this project prefers for
  what), assertion style and libraries, fixtures and builders in use. Quote a short real example.
- **workspace_setup** — what a fresh worktree needs before the suite passes: dependency install, and
  copying gitignored local config from the main checkout (`.env`, `appsettings.Development.json`,
  user-secrets…). Use `$FACTORY_REPO_ROOT` for the main checkout's root, never an absolute path.
  List the `lockfiles` whose change means setup must rerun.
- **Reviewers** — the optional ones that fit (`platform` for a CMS or framework with traps, `ux` and
  `ui` for user-facing front ends). Leave `codex` off and say why: it sends code to OpenAI, so the
  client agreement must allow it, and each developer needs the Codex CLI and their own OpenAI access.
- **Path patterns** per reviewer — globs (`src/components/**`, `*.scss`) or `re:` patterns matched
  against the diff text (`re:\bIMemoryCache\b`), so later rounds pull a reviewer back in when its area
  changes.
- **Platform** — for the platform reviewer: the platform, version, and any project-specific traps.
- `max_rounds` (default 3), `max_diff` (changed lines that force escalation; default 800), remote,
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
- **Jira** — on or off, and the factory-stage → Jira-status mapping, including where a cancelled
  issue goes. Read the project's workflow statuses from Jira rather than guessing names.

Then **show the human the whole file and ask them to review it**. Ask about anything you could not
establish, one question at a time with a recommendation.

## 3. Propose the allowlist

`F allowlist` prints the permission rules the agents need in `.claude/settings.local.json`: the
factory script (through which agents run the project's build, lint and test commands) and the git
commands agents use in the workspace. Rules already in the shared `.claude/settings.json` count as
present. `git push` is deliberately absent; only the orchestrator pushes, through the script. Show the
list, and on the human's yes run `F allowlist --write`. Local settings are not committed.

The per-command limits (no shell operators, no switching or pushing, the builder never touching
test-writer's files, reviewers writing only their review) are enforced by the plugin's PreToolUse
hook, which recognises each factory agent by name, not by the allowlist.

## 4. Create and prove the workspace

1. `F workspace create` — the worktree at `factory/workspace/`, detached at the target branch, with
   workspace_setup run.
2. `F suite` — the full suite once in the workspace.

**If the suite is red, stop.** Report the failing tests from the log it names. The factory does not
start on a red target; fixing it is human work that comes first. If setup fails, report which step and
its output.

Check that the project's tooling ignores `factory/` (watchers, linters, test globs, IDE indexers),
since the workspace is a full copy of the repo inside the checkout. Tools that read `.gitignore`
themselves don't see `.git/info/exclude`. Where one would scan `factory/`, tell the human which tool
and what it would take — don't edit the project's committed config on your own.

## Done means

project.md reviewed by a human; allowlist written or consciously skipped; `F workspace check` clean;
`F suite` green. Report those four lines.
