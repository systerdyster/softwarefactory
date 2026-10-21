---
name: factory-project-template
description: Template for factory/project.md, copied by `factory-cli init-files` and filled in by /factory init
metadata:
  type: reference
---
# Factory project configuration

Written by `/factory init` and reviewed by a human. The agents read the prose sections; the scripts
read only the `ini` block under **Configuration**. Lives in `factory/` (gitignored), so it is per
machine — keep a copy wherever the team keeps setup notes if more than one person runs the factory.

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

[commands]
; run in factory/workspace/; {target} is where a single test's argument goes
build =
lint =
suite =
test =

[workspace]
; one command per line, run in the workspace; $FACTORY_REPO_ROOT is the main checkout
setup =
lockfiles =

[reviewers]
; optional: platform, ux, ui, codex (codex sends code to OpenAI: check the client agreement first)
enabled =

[paths]
; per reviewer: globs, or re:<regex> matched against the diff text
ui =
ux =
performance =
stability =
platform =

[jira]
enabled = false
; factory stage → Jira status name; leave empty to skip a transition
claimed =
building =
needs-human =
pr-open =
merged =
cancelled =
```
