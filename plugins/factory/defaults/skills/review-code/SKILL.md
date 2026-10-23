---
name: review-code
description: "Main code review rulebook — correctness, the project's written rules, design, tests, and a baseline of security, performance and stability. Use when reviewing a change before it is committed or merged, by hand or as a software factory's main Mage."
model: opus
---

# Review: main

What a careful senior checks before putting their name on a change. Specialists (security,
performance, stability, ui, ux, platform) may run beside this review when the change gives them a
reason; when one runs, leave its depth to it, but never skip the baseline below because one might. As
a factory Mage, the review contract says how to report it.

**The project's written rules come first.** Read the rule files and docs — CLAUDE.md, AGENTS.md, the
wiki's architecture and component pages, committed review skills — before the diff. What a config file
happens to enumerate is not the whole rulebook. A change that breaks a written rule is BLOCKING; cite
the file and the rule.

## Checks

**Correct**
- Logic errors: off-by-one, wrong operator, inverted condition, unhandled empty/null/duplicate/
  concurrent case, wrong error propagated, a branch that can't be reached or can't be left.
- Departures from plan.md that build.md doesn't explain.

**Fits the codebase**
- Existing patterns followed. A new approach to wiring, state, mounting or structure nobody approved
  is BLOCKING.
- Reuse: a helper, component or utility that already exists and was re-implemented.
- Low coupling: third-party libraries behind an adapter, no reaching across layers.
- Nothing left behind — dead code, unused exports, debug output, commented-out blocks.

**Tests** — judge their quality; tests.md maps them to the criteria. The project's own test rules,
levels and tools come first.
- **Would they catch the fault they are there for?** Read the assertion against the behaviour: a test
  that would still pass if the change were wrong protects nothing. A test that already passed before
  the change can be right — it protects behaviour that must not move.
- **Edge cases that matter here** — empty, missing, duplicate, boundary, the error path — covered where
  breaking them would cost something.
- **Missing coverage** of changed logic or a helper that could cleanly have a test is BLOCKING when the
  behaviour matters; padding is a NOTE: getters, the framework's own behaviour, a mock being called,
  implementation details a refactor would break.
- **A bug fix** has a test that reproduces the bug, and tests.md's Reproduction says how it failed
  before the fix. That failure must be the wrong behaviour — not a broken import, a timeout or a
  broken test environment. Without a credible reproduction the fix is unproven: BLOCKING.
- Readable, and one test file per source file unless the project does otherwise.
- An exemption that is really "hard to test": a criterion with an observable effect can be tested.

**Baseline security** — untrusted input reaching SQL, HTML, a path, a redirect or a shell without
encoding or parameterisation; a new endpoint or handler with no authorisation decision; a secret in
the diff; personal data in logs or URLs. Each is BLOCKING.

**Baseline performance** — a remote call (database, HTTP, content repository) inside a loop; an
unbounded load; work added to every request that could be done once. Unbounded per-request work is
BLOCKING.

**Baseline stability** — a path that crashes on missing or malformed data; an external call with no
timeout; a scheduled job, event subscription or cache with no stated reason or failure path.

**Readable** — names say what things are; functions do one thing. Comments follow the project's
comment rule: one the change added that the rule doesn't allow is a NOTE naming the line.
Documentation the change wrote follows the project's documentation rule; a page that pastes code,
reads as a changelog or contradicts another page is a NOTE.

**Complexity** — grade what the change leaves behind, 1–10. 7–8 is a NOTE naming a specific refactor;
9–10 is BLOCKING. Put `Complexity: n/10 — <one line>` at the end of your review.

## Not findings

- Style the project's formatter or linter owns.
- Preference without a consequence ("I'd have written it differently").
