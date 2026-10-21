import unittest

from support import SandboxTest, git

from fx import jobs, schedule, workspace

ENABLED = ["security", "code", "performance", "stability", "ui", "codex"]


class DecideTest(unittest.TestCase):
    def test_first_round_runs_every_enabled_reviewer(self):
        self.assertEqual(list(schedule.decide(ENABLED, True, [], "", {}, {})), ENABLED)

    def test_code_change_reruns_security_code_and_codex_plus_those_that_said_changes(self):
        verdicts = {n: "PASS" for n in ENABLED} | {"performance": "CHANGES"}
        chosen = schedule.decide(ENABLED, False, ["src/app.py"], "", verdicts, {})
        self.assertEqual(list(chosen), ["security", "code", "performance", "codex"])

    def test_path_patterns_pull_in_a_passed_reviewer(self):
        verdicts = {n: "PASS" for n in ENABLED}
        chosen = schedule.decide(ENABLED, False, ["src/components/Button.tsx"], "", verdicts,
                                 {"ui": ["src/components/**"]})
        self.assertIn("ui", chosen)
        self.assertNotIn("stability", chosen)

    def test_content_pattern_matches_the_diff_text(self):
        verdicts = {n: "PASS" for n in ENABLED}
        chosen = schedule.decide(ENABLED, False, ["src/repo.py"], "+ q = 'SELECT * FROM t'", verdicts,
                                 {"performance": ["re:SELECT"]})
        self.assertIn("performance", chosen)

    def test_a_reviewer_with_no_verdict_this_cycle_is_scheduled(self):
        verdicts = {n: "PASS" for n in ENABLED if n != "ui"}
        self.assertIn("ui", schedule.decide(ENABLED, False, [], "", verdicts, {}))

    def test_globs(self):
        self.assertTrue(schedule.matches("*.scss", "src/a/b/site.scss"))
        self.assertTrue(schedule.matches("src/components/**", "src/components/a/b.tsx"))
        self.assertTrue(schedule.matches("**/*.cs", "Foo.cs"))
        self.assertFalse(schedule.matches("src/*.py", "src/a/b.py"))


class ScheduleFileTest(SandboxTest):
    enabled = "ui"

    def test_writes_scheduled_md_with_head_and_base(self):
        self.sb.create_workspace()
        job = self.sb.queue_job()
        workspace.switch(self.paths, self.cfg, job)
        self.sb.write("src/app.py", "def answer():\n    return 42\n", base=self.sb.ws)
        head = self.sb.commit("Answer", cwd=self.sb.ws)
        result = schedule.schedule(self.paths, self.cfg, job, 1, 1)
        self.assertEqual(result["reviewers"], ["security", "code", "performance", "stability", "ui"])
        sched = jobs.scheduled(self.paths.round_dir(job, 1))
        self.assertEqual(sched["head"], head)
        self.assertEqual(sched["base"], git(self.sb.root, "rev-parse", "origin/main"))


if __name__ == "__main__":
    unittest.main()
