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

STABILITY_FIXED = """VERDICT: PASS

## Findings
- 🟡 NOTE `src/aria.ts:12` — the test helper and this one differ for a hidden root.
"""


class VerdictTest(unittest.TestCase):
    def test_only_a_blocking_finding_makes_changes(self):
        self.assertEqual(jobs.verdict(CODE), "CHANGES")
        self.assertEqual(jobs.verdict(UI), "PASS")

    def test_a_numbered_blocking_finding_is_read(self):
        text = "VERDICT: CHANGES\n\n## Findings\n1. BLOCKING `a.py:1` — broken output\n"
        self.assertEqual(jobs.review(text), {"verdict": "CHANGES", "problem": None})
        self.assertTrue(findings.parse(text)[0]["blocking"])

    def test_a_blocking_finding_wins_over_a_pass_line(self):
        self.assertEqual(jobs.verdict("VERDICT: PASS\n\n## Findings\n- **BLOCKING** `a.py:1` — x\n"), "CHANGES")

    def test_changes_with_nothing_marked_blocking_is_sent_back_not_passed(self):
        r = jobs.review(STABILITY)
        self.assertIsNone(r["verdict"])
        self.assertIn("no finding is marked BLOCKING", r["problem"])

    def test_blocking_written_outside_a_finding_item_is_sent_back(self):
        r = jobs.review("VERDICT: PASS\n\n## Findings\n### BLOCKING: `a.py:1` returns the wrong total\n")
        self.assertIsNone(r["verdict"])
        self.assertIn("not a finding item", r["problem"])

    def test_a_note_that_mentions_blocking_is_fine(self):
        text = "VERDICT: PASS\n\n## Findings\n- 🟡 NOTE `a.py:1` — a NOTE, not BLOCKING: cost unmeasured\n"
        self.assertEqual(jobs.verdict(text), "PASS")

    def test_blocking_outside_findings_does_not_count(self):
        self.assertEqual(jobs.verdict("VERDICT: PASS\n\n## Earlier findings\n- 🔴 BLOCKING `a.py:1` — fixed\n"), "PASS")

    def test_a_review_without_a_verdict_line_is_sent_back(self):
        r = jobs.review("## Findings\n- 🔴 BLOCKING `a.py:1` — x\n")
        self.assertEqual((r["verdict"], "line 1" in r["problem"]), (None, True))


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
        for name, text in (("code", CODE), ("ui", UI), ("stability", STABILITY_FIXED)):
            self.sb.write(f"review-{name}.md", text, base=rd)
        result = findings.write(self.paths, job, 1)
        self.assertEqual((result["blocking"], result["notes"]), (1, 2))
        text = (rd / "findings.md").read_text()
        self.assertLess(text.index("status-dot.css"), text.index("## Notes"))
        self.assertIn("**code, ui** — one item, named by 3 findings:", text)


if __name__ == "__main__":
    unittest.main()
