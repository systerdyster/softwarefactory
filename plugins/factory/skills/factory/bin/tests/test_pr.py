import unittest

from support import SandboxTest, git

from fx import config, jobs, pr, workspace
from fx.errors import FactoryError


class PrTest(SandboxTest):
    merge_mode = "local-merge"

    def setUp(self):
        super().setUp()
        self.job = self.sb.queue_job()
        self.jd = self.paths.job(self.job)
        workspace.open_(self.paths, self.cfg, self.job)
        self.sb.write("src/app.py", "def answer():\n    return 42\n", base=self.sb.ws)
        self.head = self.sb.commit("Answer", cwd=self.sb.ws)

    def test_body_carries_summary_verdicts_exemptions_and_least_sure(self):
        self.sb.write("tests.md", "## Exemptions\n\n- Logging: no observable output.\n", base=self.jd)
        self.sb.write("round-1/review-security.md", "VERDICT: PASS\n", base=self.jd)
        self.sb.write("round-1/review-codex.md", "VERDICT: PASS\nFALLBACK: claude\n", base=self.jd)
        self.sb.write("round-1/review-code.md", "VERDICT: PASS\n\n## Least sure\n\n1. The rounding in app.py:2.\n",
                      base=self.jd)
        self.sb.write("human-approval.md", "Head: 0123456789abcdef\nApproved-By: Fredrik\n", base=self.jd)
        text = pr.body(self.paths, self.cfg, self.job)
        for expected in ("The answer is 42.", "| security | PASS |", "Logging: no observable output.",
                         "The rounding in app.py:2.", "FALLBACK: claude", "Approved by Fredrik at `0123456789ab`"):
            self.assertIn(expected, text)

    def test_a_description_with_ai_attribution_is_refused(self):
        self.sb.write("pr-body.md", "## Answer\n\nThe answer is 42.\n\n🤖 Generated with [Claude Code](https://x)\n",
                      base=self.jd)
        with self.assertRaises(FactoryError):
            pr.final_body(self.paths, self.cfg, self.job)

    def test_agents_get_the_default_commit_convention_unless_the_project_has_one(self):
        self.assertIn("Subject imperative", self.cfg.commits)
        self.paths.project.write_text(self.paths.project.read_text().replace(
            "[jira]", "[handoff]\ncommits = Swedish, key first\n\n[jira]"), encoding="utf-8")
        self.assertEqual(config.load(self.paths).commits, "Swedish, key first")

    def test_other_tool_pushes_and_leaves_the_pr_to_a_human(self):
        record = pr.open_or_update(self.paths, self.cfg, self.job)
        self.assertEqual(record["state"], "MANUAL")
        self.assertEqual(git(self.sb.origin, "rev-parse", "factory/001-answer"), self.head)

    def test_local_merge_refuses_while_the_target_is_checked_out(self):
        record = pr.merge_local(self.paths, self.cfg, self.job)
        self.assertFalse(record["merged"])
        self.assertIn("checked out", record["blocked"])

    def test_local_merge_builds_a_no_ff_merge_without_touching_any_checkout(self):
        git(self.sb.root, "switch", "--detach")
        record = pr.merge_local(self.paths, self.cfg, self.job)
        self.assertTrue(record["merged"])
        parents = git(self.sb.root, "rev-list", "--parents", "-n", "1", "main").split()
        self.assertEqual(len(parents), 3)
        self.assertEqual(parents[2], self.head)
        self.assertEqual(jobs.read_json(self.jd / "merge.json")["commit"], parents[0])


if __name__ == "__main__":
    unittest.main()
