import unittest

import support  # noqa: F401

from fx import jobs
from fx.errors import FactoryError

BUILD = """# Build log

## Round 1
Followed the plan.
Status: done

## Round 2
Objection: plan
The plan assumes an API that does not exist.
Status: objection

## Round 2 (attempt 2)
Working
"""


class ParsersTest(unittest.TestCase):
    def test_build_sections_read_round_attempt_and_status(self):
        sections = jobs.build_sections(BUILD)
        self.assertEqual([(s["round"], s["attempt"], s["status"]) for s in sections],
                         [(1, 1, "done"), (2, 1, "objection"), (2, 2, None)])
        self.assertEqual(sections[1]["objection"], "plan")

    def test_a_review_counts_as_written_only_from_its_first_line(self):
        self.assertEqual(jobs.verdict("VERDICT: PASS\n..."), "PASS")
        self.assertIsNone(jobs.verdict("Some text\nVERDICT: PASS"))

    def test_cancellation_is_read_from_the_first_line_only(self):
        self.assertEqual(jobs.decision("CANCELLED\n\nReason: duplicate"), "CANCELLED")
        self.assertIsNone(jobs.decision("Reason: CANCELLED\n"))

    def test_job_ids(self):
        for good in ("OPT-123", "AB2-7", "001-fix-login"):
            self.assertEqual(jobs.check_id(good), good)
        for bad in ("opt-1", "../etc", "1-x", "001_fix"):
            with self.assertRaises(FactoryError):
                jobs.check_id(bad)


if __name__ == "__main__":
    unittest.main()
