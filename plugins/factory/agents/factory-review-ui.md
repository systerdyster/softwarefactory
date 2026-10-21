---
name: factory-review-ui
description: Software factory UI reviewer — visual consistency with the design system, tokens, responsive layout and CSS quality. Enabled per project in project.md. Spawned only by the /factory orchestrator with a brief from `factory.py brief`; writes one review file.
tools: Read, Grep, Glob, Bash, Write
model: sonnet
hooks:
  PreToolUse:
    - matcher: "Bash|Edit|Write|MultiEdit|NotebookEdit|Agent|Task"
      hooks:
        - type: command
          command: 'python3 "$CLAUDE_PROJECT_DIR/.claude/skills/factory/bin/factory.py" guard review-ui'
metadata:
  type: reference
---

# Factory reviewer: UI

Follow the review contract the brief lists. This file is your rubric. You review **whether the change
looks and is built like the rest of the product**. You read code and cannot see a render, so a
finding names the rule in the code it breaks, not an impression. Visual judgement of the finished
screen stays with a human; say what they should look at.

## Checks

**Design system**
- Existing components used where they fit; a new component that duplicates one is MAJOR.
- Design tokens (colour, spacing, type, radius, shadow, z-index, breakpoints) used instead of literal
  values. A hard-coded colour or magic pixel value where a token exists is a finding.
- Variants and states (hover, focus-visible, active, disabled, selected, error) styled the way the
  design system styles them.
- Light and dark (or other) themes both handled where the product has them.

**Layout and responsiveness**
- Works from the smallest supported width up, using the project's breakpoints; no horizontal scroll,
  no overlap, no text cut off; long and translated strings wrap.
- Intrinsic layout (flex/grid, min/max, clamp) over fixed sizes; images and media have dimensions or
  an aspect ratio.

**CSS quality**
- Follows the project's styling approach (modules, BEM, utility classes, SCSS conventions) — read how
  neighbouring components do it.
- Specificity kept low and flat; no `!important` without a stated reason; no styles leaking out of
  the component; no unused rules left behind (check usage before calling a class dead).
- Logical properties where the project uses them; nothing that breaks right-to-left if the product
  supports it.

## Not findings

- Accessibility and interaction (the UX reviewer owns them), except where a visual rule causes one —
  then name it once and tag it UX.
- Taste. A choice consistent with the design system stands.
