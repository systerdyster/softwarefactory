---
name: review-ux
description: "UX review rulebook — interaction, accessibility (WCAG 2.2 AA), states and copy. Use when reviewing a change for how a person experiences it, by hand or as a software factory Mage's rulebook."
model: sonnet
---

# Review: UX

What to look for in a UX review: **how a person experiences the change** — whether they can use it, with any input method and ability, and whether it
behaves sensibly when things go wrong.

**Exercise it before you judge.** When the brief offers render commands (project.md's `[reviewers]
commands` — browser tests, an accessibility scan), run the ones that cover the changed flows and read
their output and screenshots: keyboard order, focus, names, states. Line 3 of your file is
`RENDERED: <what you ran>` or `RENDERED: no — <why>` (the factory reads it: a `no` is shown to the
developer at approval and in the PR); a finding that rests only on reading the code says so.
Run only what the brief lists; the project decides what it has. When one of those commands is an
accessibility check (axe or similar), quote what it reported, passes and violations alike. When none
is, say "no automated accessibility check in this project" in your file — never imply a scan ran.

**The accessibility bar is WCAG 2.2 Level AA.** A regression against it is BLOCKING. Where the
project's rules or design system say more, they win.

## Checks

**Accessible by construction**
- Semantic elements for what they are — a button is a `<button>`, a link navigates, headings form an
  outline, lists are lists. A `div` with a click handler is a finding.
- Every interactive control has an accessible name; icons-only controls are labelled; form fields
  have associated labels, and errors are tied to their field (`aria-describedby`) and announced.
- Keyboard: everything reachable and operable, visible focus, a logical order, no trap; dialogs and
  menus manage focus on open and close and take Esc.
- ARIA only where no native element does the job, and correct when used (roles, states kept in sync).
- Contrast, target size (24×24 CSS px minimum), and nothing conveyed by colour alone.
- Motion respects `prefers-reduced-motion`; content survives 200% zoom and 320px width without loss.

**States**
- Loading, empty, error and success states exist and say something useful. An error tells the user
  what happened and what to do. No state leaves the user without a way forward.
- Destructive or irreversible actions confirm or can be undone.
- Double submits, slow networks and partial data behave sensibly.

**Flow and copy**
- The change does what the acceptance criteria describe from the user's side, in the fewest steps the
  design allows.
- Copy is plain, consistent with the rest of the product, and localisable (no concatenated sentences,
  no text baked into images).

## Not findings

- Visual polish that belongs to the UI reviewer (spacing, tokens, visual consistency).
- A design decision the spec approved, even if you'd have chosen differently.
