import unittest

from support import SandboxTest

from fx import findings, jobs

CODE = """VERDICT: CHANGES

## Findings
- 🔴 BLOCKING `src/status-dot.css:60` — the modifier sets real properties.
- 🟡 NOTE `src/avatar.css:34`, `src/breadcrumb.css:51` — `fill: currentColor` is left behind.

## Earlier findings
- 🔴 BLOCKING `src/old.py:1` — fixed
"""

UI = """VERDICT: PASS

## Findings
- 🟡 NOTE `src/breadcrumb.css:51` — `fill` does nothing on a glyph.
- 🟡 NOTE `src/avatar.css:34` — same leftover
  on the avatar.
"""

STABILITY = """VERDICT: CHANGES

## Findings
### 1. Two helpers can drift (non-blocking, should fix)
- the test helper and `src/aria.ts:12` differ for a hidden root.
"""


class VerdictTest(unittest.TestCase):
    def test_only_a_blocking_finding_makes_changes(self):
        self.assertEqual(jobs.verdict(CODE), "CHANGES")
        self.assertEqual(jobs.verdict(UI), "PASS")

    def test_a_changes_line_over_non_blocking_findings_reads_as_pass(self):
        self.assertEqual(jobs.verdict(STABILITY), "PASS")

    def test_blocking_outside_findings_does_not_count(self):
        self.assertEqual(jobs.verdict("VERDICT: PASS\n\n## Earlier findings\n- 🔴 BLOCKING `a.py:1` — fixed\n"), "PASS")

    def test_an_unfinished_review_has_no_verdict(self):
        self.assertIsNone(jobs.verdict("## Findings\n- 🔴 BLOCKING `a.py:1` — x\n"))


class MergeTest(unittest.TestCase):
    def test_the_same_location_from_two_reviewers_is_one_item(self):
        groups = findings.merge({"code": findings.parse(CODE), "ui": findings.parse(UI)})
        self.assertEqual(len(groups), 2)
        leftover = next(g for g in groups if not g["blocking"])
        self.assertEqual(leftover["reviewers"], ["code", "ui"])
        self.assertEqual(len(leftover["entries"]), 3)

    def test_continuation_lines_belong_to_their_bullet(self):
        self.assertIn("on the avatar.", findings.parse(UI)[1]["text"])


class WriteTest(SandboxTest):
    def test_findings_md_lists_blocking_first_and_counts(self):
        job = self.sb.queue_job()
        rd = self.paths.round_dir(job, 1)
        for name, text in (("code", CODE), ("ui", UI), ("stability", STABILITY)):
            self.sb.write(f"review-{name}.md", text, base=rd)
        result = findings.write(self.paths, job, 1)
        self.assertEqual((result["blocking"], result["notes"]), (1, 2))
        text = (rd / "findings.md").read_text()
        self.assertLess(text.index("status-dot.css"), text.index("## Notes"))
        self.assertIn("**code, ui** — one item, named by 3 findings:", text)


if __name__ == "__main__":
    unittest.main()
