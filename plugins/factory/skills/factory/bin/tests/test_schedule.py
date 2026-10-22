import unittest

from support import SandboxTest, git

from fx import jobs, schedule, workspace

AVAILABLE = ["code", "security", "performance", "stability", "ui", "codex"]


class DecideTest(unittest.TestCase):
    def test_first_round_is_the_main_reviewer_alone_without_a_reason_for_more(self):
        self.assertEqual(list(schedule.decide(AVAILABLE, True, ["src/app.py"], "", {}, {})), ["code"])

    def test_a_specialist_named_in_the_plan_joins_the_first_round(self):
        chosen = schedule.decide(AVAILABLE, True, [], "", {}, {}, named=["security"])
        self.assertEqual(chosen, {"code": "main review", "security": "named in plan.md"})

    def test_path_patterns_pull_in_a_specialist(self):
        chosen = schedule.decide(AVAILABLE, True, ["src/components/Button.tsx"], "", {}, {"ui": ["src/components/**"]})
        self.assertIn("ui", chosen)
        self.assertNotIn("stability", chosen)

    def test_content_pattern_matches_the_diff_text(self):
        chosen = schedule.decide(AVAILABLE, True, ["src/repo.py"], "+ q = 'SELECT * FROM t'", {},
                                 {"performance": ["re:SELECT"]})
        self.assertIn("performance", chosen)

    def test_later_rounds_rerun_only_those_that_said_changes(self):
        verdicts = {n: "PASS" for n in AVAILABLE} | {"ui": "CHANGES"}
        self.assertEqual(list(schedule.decide(AVAILABLE, False, ["src/app.py"], "", verdicts,
                                              {"ui": ["src/**"]})), ["ui"])

    def test_globs(self):
        self.assertTrue(schedule.matches("*.scss", "src/a/b/site.scss"))
        self.assertTrue(schedule.matches("src/components/**", "src/components/a/b.tsx"))
        self.assertTrue(schedule.matches("**/*.cs", "Foo.cs"))
        self.assertFalse(schedule.matches("src/*.py", "src/a/b.py"))


class ScheduleFileTest(SandboxTest):
    enabled = "ui"

    def test_writes_scheduled_md_with_head_base_and_the_plans_specialists(self):
        job = self.sb.queue_job()
        self.sb.write("plan.md", "# Plan\n\nSpecialists: security\n", base=self.paths.job(job))
        workspace.open_(self.paths, self.cfg, job)
        self.sb.write("src/components/answer.py", "ANSWER = 42\n", base=self.sb.ws)
        head = self.sb.commit("Answer", cwd=self.sb.ws)
        result = schedule.schedule(self.paths, self.cfg, job, 1, 1)
        self.assertEqual(result["reviewers"], ["code", "security", "ui"])
        sched = jobs.scheduled(self.paths.round_dir(job, 1))
        self.assertEqual((sched["head"], sched["previous"]), (head, None))
        self.assertEqual(sched["base"], git(self.sb.root, "rev-parse", "origin/main"))


if __name__ == "__main__":
    unittest.main()
