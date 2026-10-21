---
name: factory-review-contract
description: The output contract every factory reviewer follows — verdict line, finding tags, kill-before-report, re-review of earlier findings
metadata:
  type: reference
---

# Review contract — shared by every factory reviewer

Your agent file holds your rubric. This file holds what every reviewer's output must look like, so
the scripts and the approver can read all of them the same way.

## What you review

The change is `git -C factory/workspace diff <base>..<head>` with the base and head from
`scheduled.md` (the brief prints the command). Read whatever surrounding code you need to judge it —
a hunk is not enough context. Judge it against spec.md and plan.md: the approved intent is the bar,
together with the project's own rules, which win over your rubric where they are stricter or
different.

You only read. You may write exactly one file: your review. The factory checks after the round that
the workspace is unchanged, and discards the round if it isn't.

## The file

```markdown
VERDICT: PASS | CHANGES

Reviewer: <name> · Round <n> · Head <sha>

## Findings
- 🔴 BLOCKING `path:line` — what fails, for which input, and why (rule or failure it causes)
- 🟠 MAJOR `path:line` — …
- 🟡 MINOR `path:line` — …

## Earlier findings          (only when you reviewed an earlier round)
- <finding> — fixed | not fixed | disputed by the builder: <your judgement of the dispute>

## Checked and cleared
- <candidate that died> — <what proved it wrong, file:line>
```

- **Line 1 is the verdict, nothing before it.** `CHANGES` when at least one BLOCKING or MAJOR finding
  stands; otherwise `PASS` (MINOR findings may remain under a PASS).
- **BLOCKING**: a clear project-rule violation, a broken build or test, or a security regression.
  **MAJOR**: a real correctness, performance, stability or architecture concern a careful senior
  reviewer would refuse to approve. **MINOR**: everything lighter.
- Every finding names the concrete failure: where, with what input or condition, and what goes wrong.
  "Best practice" is not a citation.

## Kill every finding before you report it

Your rubric produces candidates. Before reporting one, try to prove it wrong: read the guard upstream,
the library's real signature, the configuration that applies, the test that already covers it. The
most confident-sounding candidates die most often. Report what survives, with its evidence; list what
died under **Checked and cleared** — that list is what makes a PASS mean something. A candidate that
survives but can't be proven is reported as such ("mechanism stands, cost unmeasured") and is never
MAJOR on a hunch.

## Scope

Review the change, not the codebase. A pre-existing problem the change doesn't touch or worsen is not
a finding (mention it in one line at the end if it matters). Don't ask for work outside the approved
spec: a missing feature the spec didn't ask for is not a finding; a spec criterion the change doesn't
meet is.

Return one line to the orchestrator: `VERDICT: PASS|CHANGES — <n> BLOCKING, <n> MAJOR, <n> MINOR`.
