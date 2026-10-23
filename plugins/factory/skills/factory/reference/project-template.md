---
name: factory-project-template
description: Template for factory/project.md, copied by `factory-cli init-files` and filled in by /factory init
metadata:
  type: reference
---
# Factory project configuration

Written by `/factory init` and reviewed by a human. The agents read the prose sections; the scripts
read only the `ini` block under **Configuration**. Lives in `factory/`, which git ignores, so it is
per machine.

## Stack

<languages, frameworks and versions, read from the project files>

## Test conventions

- Runner:
- Location and file naming:
- Test naming:
- Altitude (what is tested at which level here):
- Assertion style and libraries:
- Fixtures, builders, helpers in use:
- Example (a short real test from the repo):

## Platform

<for the platform reviewer: platform and version, and this project's own traps — or "none">

## Configuration

```ini
[factory]
target_branch = main
remote = origin
; pr (default) | local-merge | auto-merge
merge_mode = pr
; gh | az | other
pr_tool = gh
max_rounds = 3
max_diff = 800
suite_timeout = 1800
; [commands] names that together run the project's tests, build and lint, without running anything
; twice (default: lint, build, suite — each where defined)
verify =
; jobs built at once, each in its own worktree: 1 or more
parallel = 2
; [commands] names that write shared data (migrations, seeding, tests against a shared database):
; they share one lock, so no two of them run at once in any job. Read-only use of a shared backend
; needs nothing here; every other command runs in parallel.
serial =

[commands]
; run in the job's worktree, factory/workspaces/<job>/; {target} is where a single test's argument goes.
; Each command gets $FACTORY_PORT — the job's own port, the same for all its commands and never
; another job's — and $FACTORY_OUTPUT, the job's own folder. A dev server or browser test must bind
; to $FACTORY_PORT (a test runner that starts its own server, like Playwright's webServer, passes it
; on) and write screenshots and reports under $FACTORY_OUTPUT.
build =
lint =
suite =
test =

[server]
; optional: a dev server the factory starts on $FACTORY_PORT before each command listed in `for`,
; waits for (up to `timeout` seconds) and stops after. Leave empty when a test command starts its own.
command =
for =
timeout = 120

[workspace]
; one command per line, run in each job's new worktree and again when a listed lockfile changes;
; $FACTORY_REPO_ROOT is the main checkout. Each worktree gets its own dependencies: for pnpm
; `pnpm install --frozen-lockfile` (its own node_modules, from pnpm's shared store).
setup =
lockfiles =

[rules]
; globs of the project's written rules beyond CLAUDE.md/AGENTS.md, read by every agent (e.g. Wiki/*.md)
docs =
; when agents write code comments; empty = none by default, only where the why is a trap the code
; can't show, keeping the surrounding file's style
comments =
; how agents write documentation (wiki pages, READMEs); empty = plain framing, then the exact
; ingredients, then the gotcha; present tense, no pasted code, one page per fact
writing =
; the wiki pages in this repo that a wiki agent keeps true before you approve a job (globs; existing pages
; only); empty = no wiki agent
wiki =
; wikis in other repos that a change may affect: named in the agent's report, never edited
wiki_external =
; the project's own wiki conventions (a file in the repo, e.g. a skill) the wiki agent follows
wiki_guide =

[mages]
; <name> = the rulebook the Mage reviews by: skill:<a skill in .claude/skills> or default:<the factory's
; defaults/skills/review-<name>>; codex = codex for the Codex CLI (it sends code to OpenAI: check the
; client agreement first). code is the main Mage and reviews every job; the others join when [paths]
; or the plan's Specialists line calls them. The rulebook's model: front matter picks the model.
code = default:code
security = default:security
performance = default:performance
stability = default:stability

[reviewers]
; project commands the ui and ux Mages may run to see the change (screenshots must be ignored by git)
commands =

[paths]
; a Mage joins a job's first round when one of these matches the change (or the plan names it)
; globs, or re:<regex> matched against the diff text
security =
performance =
stability =
ui =
ux =
platform =

[handoff]
; the job's branch: {id} job id, {key} case key (else the id), {slug} title in a few words
branch = factory/{id}
; open the PR as a draft
draft = false
; space- or comma-separated users or groups (az: optional reviewers; gh: handles or org/team)
reviewers =
required_reviewers =
; where the case key goes: title | body | both | none; case_url (the key is appended) makes it a link
case_link = title
case_url =
; az only: link the job's Azure Boards work item (given at /factory add)
work_items = false
; language of the PR description, and the project's PR template if it has one (repo-relative path)
language = en
; language of the test note (how someone else verifies the change by hand); empty = same as language
test_note_language =
pr_template =
; how agents write commit messages; empty = imperative short subject, a 2–3 line body only for the why,
; no case key in the subject when the branch carries it
commits =

[events]
; what the project does when a job reaches an event; empty = nothing. A shell command (run in the
; project root with FACTORY_EVENT, FACTORY_JOB, FACTORY_CASE, FACTORY_BRANCH, FACTORY_DEVELOPER,
; FACTORY_PR and, at the first hand-off, FACTORY_TEST_NOTE), or skill:<name> [args] for a skill the
; session runs with its own tools (a tracker reached only through them), told the same as key=value.
; Handlers must be safe to run twice. e.g. started = skill:case-status In Progress
; once the spec is approved at /factory add
claimed =
; each time the job is built: first, after a rework, after a rebase
started =
; each time the job waits for the developer: ready, a question, stopped
waiting =
; PR opened, or merged locally; carries the test note the first time
handed-off =
; merged
completed =
; cancelled
cancelled =
```
