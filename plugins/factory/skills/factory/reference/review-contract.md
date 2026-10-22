---
name: factory-review-contract
description: The output contract every factory reviewer follows — verdict line, BLOCKING or NOTE, kill-before-report, settled decisions, fix rounds
metadata:
  type: reference
---

# Review contract — shared by every factory reviewer

Your agent file holds your rubric. This file holds what every reviewer's output must look like, so
the scripts can read all of them the same way and merge them into one list for the builder.

## What you review

The change is `git -C factory/workspace diff <base>..<head>` with the base and head from
`scheduled.md` (the brief prints the command). Read whatever surrounding code you need to judge it —
a hunk is not enough context. Judge it against spec.md and plan.md, and against the project's written
rules the brief lists — CLAUDE.md, AGENTS.md and the docs such as a wiki's architecture and component
pages. Those win over your rubric where they are stricter or different.

You only read, and you run only the project commands the brief offers you. You may write exactly one
file: your review. The factory checks after the round that the workspace is unchanged, and discards
the round if it isn't.

## Settled is settled

spec.md's `## Decided` section records what the developer already decided, including outcomes they
expected (a large diff, a deliberate exemption). Don't report a finding against a decision there
unless you name a **new circumstance** the decision didn't know — then say what it is.

## The file

```markdown
VERDICT: PASS | CHANGES

Reviewer: <name> · Round <n> · Head <sha>

## Findings
- 🔴 BLOCKING `path:line` — what fails, for which input, and why (rule or failure it causes)
- 🟡 NOTE `path:line` — …

## Earlier findings          (only in a fix round)
- <your earlier finding> — resolved | not resolved (then it is also listed under Findings again)

## Checked and cleared
- <candidate that died> — <what proved it wrong, file:line>
```

- **Two severities.** **BLOCKING** must change before the job goes to the developer: a written project
  rule broken, an acceptance criterion not met, or a defect with a concrete failure — wrong output,
  crash, data loss, a security or accessibility regression, unbounded work per request. **NOTE** is
  everything else; the builder fixes notes where cheap, and the rest reach the developer with the PR.
- **The factory derives the verdict from your findings**: CHANGES exactly when a finding item under
  Findings (a `-` bullet or a numbered `1.` item) starts with BLOCKING. Write the matching word on
  line 1. A report it can't read safely — CHANGES with no finding marked BLOCKING, or BLOCKING outside
  a finding item such as a heading — is sent back to you to rewrite; it never counts as a pass.
- Put every finding's location in backticks as `path:line`. The factory merges findings from different
  reviewers that name the same location into one item, so a duplicate costs nothing and a
  contradiction becomes visible.
- Every finding names the concrete failure: where, with what input or condition, and what goes wrong.
  "Best practice" is not a citation.

## Kill every finding before you report it

Your rubric produces candidates. Before reporting one, try to prove it wrong: read the guard upstream,
the library's real signature, the configuration that applies, the test that already covers it. The
most confident-sounding candidates die most often. Report what survives, with its evidence; list what
died under **Checked and cleared** — that list is what makes a PASS mean something. A candidate that
survives but can't be proven is a NOTE that says so ("mechanism stands, cost unmeasured"), never
BLOCKING on a hunch.

## Fix rounds

From round 2 the brief gives you the previous round's findings.md and the diff since your last review.
Say for each of your own findings whether it is resolved. Report a new BLOCKING only for a defect the
fixes introduced, or one you can show was missed and breaks something. No new notes on code the round
didn't change.

## Scope

Review the change, not the codebase. A pre-existing problem the change doesn't touch or worsen is not
a finding (mention it in one line at the end if it matters). Don't ask for work outside the approved
spec: a missing feature the spec didn't ask for is not a finding; a spec criterion the change doesn't
meet is.

Return one line to the orchestrator: `VERDICT: PASS|CHANGES — <n> BLOCKING, <n> NOTE`.
