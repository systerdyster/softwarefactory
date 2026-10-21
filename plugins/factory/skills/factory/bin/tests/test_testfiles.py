import unittest

from support import SandboxTest, git

from fx import testfiles, workspace
from fx.errors import FactoryError


class TestFilesTest(SandboxTest):
    def setUp(self):
        super().setUp()
        self.sb.create_workspace()
        self.job = self.sb.queue_job()
        workspace.switch(self.paths, self.cfg, self.job)
        self.sb.write("tests/test_answer.py", "assert answer() == 42\n", base=self.sb.ws)
        self.sb.commit("Add failing tests", cwd=self.sb.ws)
        self.sb.write("tests.md", "# Tests\n\n| File | Hash |\n|---|---|\n| `tests/test_answer.py` | |\n\n"
                      "Status: red-confirmed\n", base=self.paths.job(self.job))

    def test_record_fills_hashes_from_the_committed_blob(self):
        result = testfiles.record(self.paths, self.job)
        self.assertEqual(result["files"], ["tests/test_answer.py"])
        rows = testfiles.listed(self.paths, self.job)
        self.assertEqual(rows[0]["hash"], git(self.sb.ws, "rev-parse", "HEAD:tests/test_answer.py"))
        self.assertTrue(testfiles.is_recorded(self.paths, self.job))

    def test_record_refuses_uncommitted_test_files(self):
        self.sb.write("tests/test_answer.py", "assert True\n", base=self.sb.ws)
        with self.assertRaises(FactoryError):
            testfiles.record(self.paths, self.job)
        self.assertIn("Status: record-failed", (self.paths.job(self.job) / "tests.md").read_text())

    def test_check_catches_an_uncommitted_edit_and_a_committed_one(self):
        testfiles.record(self.paths, self.job)
        self.sb.write("tests/test_answer.py", "assert True\n", base=self.sb.ws)
        self.assertFalse(testfiles.check(self.paths, self.job)["ok"])
        self.sb.commit("Weaken test", cwd=self.sb.ws)
        self.assertFalse(testfiles.check(self.paths, self.job)["ok"])

    def test_restore_puts_back_the_recorded_content_and_commits(self):
        testfiles.record(self.paths, self.job)
        (self.sb.ws / "tests/test_answer.py").unlink()
        self.sb.commit("Delete test", cwd=self.sb.ws)
        result = testfiles.restore(self.paths, self.job)
        self.assertEqual(result["restored"], ["tests/test_answer.py"])
        self.assertEqual((self.sb.ws / "tests/test_answer.py").read_text(), "assert answer() == 42\n")
        self.assertTrue(testfiles.check(self.paths, self.job)["ok"])
        self.assertIn("Restore factory-owned test files", git(self.sb.ws, "log", "-1", "--format=%s"))

    def test_hashes_survive_a_rebase_because_they_are_on_content(self):
        testfiles.record(self.paths, self.job)
        self.sb.push_target_change("README.md", "upstream\n")
        git(self.sb.ws, "rebase", "origin/main")
        self.assertTrue(testfiles.check(self.paths, self.job)["ok"])


if __name__ == "__main__":
    unittest.main()
