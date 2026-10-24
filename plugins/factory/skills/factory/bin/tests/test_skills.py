"""Where a named skill is read from: the job's worktree, the main checkout before a job exists, or the
factory's defaults — and that a missing one is an error, never skipped."""

import unittest

from support import SandboxTest

from fx import config, skills
from fx.errors import FactoryError


class SkillsTest(SandboxTest):
    def project(self, section):
        self.paths.project.write_text(self.paths.project.read_text().replace("[events]", f"{section}\n\n[events]"),
                                      encoding="utf-8")
        return config.load(self.paths)

    def test_intake_reads_the_projects_skills_from_the_main_checkout(self):
        self.sb.write(".claude/skills/jira-review/SKILL.md", "---\nname: jira-review\n---\n")
        cfg = self.project("[intake]\nread = skill:jira-review")
        self.assertEqual(skills.intake(self.paths, cfg),
                         {"read": str(self.paths.root / ".claude/skills/jira-review/SKILL.md"), "ready": None})

    def test_a_default_is_read_from_the_plugin(self):
        cfg = self.project("[intake]\nread = default:case-status")
        self.assertTrue(skills.intake(self.paths, cfg)["read"].endswith("defaults/skills/case-status/SKILL.md"))

    def test_a_job_reads_the_skill_on_its_own_branch(self):
        job = self.sb.queue_job()
        self.sb.open_job(job)
        self.sb.write(".claude/skills/tdd/SKILL.md", "---\nname: tdd\n---\n", base=self.sb.ws)
        cfg = self.project("[heroes]\ncoder = skill:tdd")
        self.assertEqual(skills.hero(self.paths, cfg, job, "coder"), [self.sb.ws / ".claude/skills/tdd/SKILL.md"])

    def test_a_missing_skill_is_an_error(self):
        cfg = self.project("[intake]\nready = skill:definition-of-ready")
        with self.assertRaisesRegex(FactoryError, "skill:definition-of-ready has no skill"):
            skills.intake(self.paths, cfg)


if __name__ == "__main__":
    unittest.main()
