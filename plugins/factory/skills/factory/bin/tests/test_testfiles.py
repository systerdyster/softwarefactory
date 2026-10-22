import unittest

from support import SandboxTest, git

from fx import jobs, testfiles, workspace

TESTS_MD = "# Tests\n\n| File |\n|---|\n| `tests/test_answer.py` |\n\n## Coverage\n- AC1 — the answer\n"
RED = "import sys\nsys.path.insert(0, 'src')\nfrom app import answer\nassert answer() == 42\n"


class RedCheckTest(SandboxTest):
    def setUp(self):
        super().setUp()
        self.sb.create_workspace()
        self.job = self.sb.queue_job()
        self.jd = self.paths.job(self.job)
        workspace.switch(self.paths, self.cfg, self.job)

    def tests_first(self, test_text=RED):
        self.sb.write("tests/test_answer.py", test_text, base=self.sb.ws)
        self.sb.commit("Add failing tests for the answer", cwd=self.sb.ws)
        self.sb.write("tests.md", TESTS_MD, base=self.jd)

    def build(self):
        self.sb.write("src/app.py", "def answer():\n    return 42\n", base=self.sb.ws)
        return self.sb.commit("Answer 42", cwd=self.sb.ws)

    def test_tests_that_fail_on_their_own_commit_pass_the_check(self):
        self.tests_first()
        head = self.build()
        record = testfiles.red_check(self.paths, self.cfg, self.job)
        self.assertEqual((record["ok"], record["head"]), (True, head))
        self.assertEqual(jobs.read_json(self.jd / "red.json")["ok"], True)
        self.assertEqual(git(self.sb.ws, "rev-parse", "HEAD"), head)

    def test_tests_that_already_pass_prove_nothing(self):
        self.tests_first("assert True\n")
        self.build()
        record = testfiles.red_check(self.paths, self.cfg, self.job)
        self.assertFalse(record["ok"])
        self.assertIn("prove nothing", record["reason"])

    def test_tests_committed_after_the_code_are_refused(self):
        self.build()
        self.tests_first()
        record = testfiles.red_check(self.paths, self.cfg, self.job)
        self.assertFalse(record["ok"])
        self.assertIn("first commit", record["reason"])

    def test_an_exempted_job_without_tests_passes(self):
        self.build()
        self.sb.write("tests.md", "# Tests\n\n## Exemptions\n- AC1 — copy change only, approved in the plan\n", base=self.jd)
        self.assertTrue(testfiles.red_check(self.paths, self.cfg, self.job)["ok"])

    def test_listed_reads_the_file_column(self):
        self.sb.write("tests.md", "| File | Hash |\n|---|---|\n| `a.py` | |\n| b.py |\n", base=self.jd)
        self.assertEqual(testfiles.listed(self.paths, self.job), ["a.py", "b.py"])


if __name__ == "__main__":
    unittest.main()
