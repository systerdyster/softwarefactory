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

    def test_a_needs_human_ruling_reads_as_a_question_with_options(self):
        q = jobs.question("RULING: NEEDS-HUMAN\n\nQuestion: Which way?\n\nThe page reads it twice.\n\n## Options\n"
                          "- (a) One way — costs x [recommended]\n- b) Other way — costs y\n")
        self.assertEqual((q["question"], q["context"]), ("Which way?", "The page reads it twice."))
        self.assertEqual(q["options"], [{"key": "a", "text": "One way — costs x", "recommended": True},
                                        {"key": "b", "text": "Other way — costs y", "recommended": False}])

    def test_a_ruling_without_the_shape_is_shown_whole(self):
        q = jobs.question("RULING: NEEDS-HUMAN\n\nTake (a) or (b)?\n- (a) this\n")
        self.assertEqual((q["question"], q["context"], q["options"]), ("", "Take (a) or (b)?\n- (a) this", []))

    def test_a_line_is_added_at_the_end_of_its_section(self):
        text = "# S\n\n## Decided\n\n- one\n\n## Open questions\n\n- q\n"
        self.assertEqual(jobs.add_to_section(text, "Decided", "- two"),
                         "# S\n\n## Decided\n\n- one\n- two\n\n## Open questions\n\n- q\n")
        self.assertEqual(jobs.add_to_section("# S\n", "Decided", "- two"), "# S\n\n## Decided\n\n- two\n")

    def test_job_ids(self):
        for good in ("OPT-123", "AB2-7", "001-fix-login"):
            self.assertEqual(jobs.check_id(good), good)
        for bad in ("opt-1", "../etc", "1-x", "001_fix"):
            with self.assertRaises(FactoryError):
                jobs.check_id(bad)


if __name__ == "__main__":
    unittest.main()
