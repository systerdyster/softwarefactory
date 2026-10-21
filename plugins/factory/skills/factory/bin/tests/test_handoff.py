import unittest

from support import SandboxTest

from fx import config, guard, pr, queue, workspace
from fx.errors import FactoryError
from fx.paths import branch_name, job_for_branch


def handoff(**values):
    lines = "\n".join(f"{k} = {v}" for k, v in values.items())
    return config.parse_text(f"# Project\n\n```ini\n[factory]\npr_tool = az\n\n[handoff]\n{lines}\n```\n")


class BranchPatternTest(unittest.TestCase):
    def test_default_keeps_the_factory_prefix(self):
        self.assertEqual(handoff().branch_pattern, "factory/{id}")

    def test_a_pattern_without_id_or_key_is_refused(self):
        with self.assertRaises(FactoryError):
            handoff(branch="feature/{slug}").branch_pattern

    def test_unknown_fields_are_refused(self):
        with self.assertRaises(FactoryError):
            handoff(branch="feature/{key}-{date}").branch_pattern

    def test_branch_uses_the_jira_key_and_a_short_slug(self):
        branch = queue.branch_for("feature/{key}-{slug}", "OPT-12", "Rate limiting för API:t, del två av tre", "OPT-12")
        self.assertEqual(branch, "feature/OPT-12-rate-limiting-for-api-t")

    def test_key_falls_back_to_the_id_for_free_text_jobs(self):
        self.assertEqual(queue.branch_for("feature/{key}", "003-dark-mode", "Dark mode"), "feature/003-dark-mode")

    def test_an_invalid_branch_name_is_refused(self):
        with self.assertRaises(FactoryError):
            queue.branch_for("feature..x/{id}", "001-a", "A")


class QueuedBranchTest(SandboxTest):
    def queue(self, job_id, branch):
        jd = self.paths.job(job_id)
        self.sb.write("spec.md", "# Spec\n", base=jd)
        self.sb.write("plan.md", "# Plan\n", base=jd)
        return queue.add(self.paths, job_id, "Title", jira=job_id, branch=branch)

    def test_the_branch_is_recorded_and_found_again_both_ways(self):
        self.queue("OPT-1", "feature/OPT-1-title")
        self.assertEqual(branch_name(self.paths, "OPT-1"), "feature/OPT-1-title")
        self.assertEqual(job_for_branch(self.paths, "feature/OPT-1-title"), "OPT-1")

    def test_two_active_jobs_may_not_share_a_branch(self):
        self.queue("OPT-1", "feature/same")
        with self.assertRaises(FactoryError):
            self.queue("OPT-2", "feature/same")

    def test_jobs_without_a_recorded_branch_keep_factory_prefix(self):
        self.sb.queue_job()
        self.assertEqual(branch_name(self.paths, "001-answer"), "factory/001-answer")
        self.assertEqual(job_for_branch(self.paths, "factory/001-answer"), "001-answer")

    def test_the_guard_finds_the_job_on_a_custom_branch(self):
        self.sb.create_workspace()
        self.queue("OPT-1", "feature/OPT-1-title")
        workspace.switch(self.paths, self.cfg, "OPT-1")
        self.assertEqual(guard._current_job(self.paths), "OPT-1")


class PrSettingsTest(unittest.TestCase):
    meta = {"title": "Rate limiting", "jira": "OPT-12", "work_item": "4711"}

    def test_title_carries_the_key_unless_told_otherwise(self):
        self.assertEqual(pr.title_for(handoff(), self.meta), "OPT-12: Rate limiting")
        self.assertEqual(pr.title_for(handoff(jira_link="body"), self.meta), "Rate limiting")

    def test_az_flags_for_draft_reviewers_and_work_items(self):
        cfg = handoff(draft="true", reviewers="Team A, anna@x.se", required_reviewers="Lead", work_items="true")
        self.assertEqual(pr.create_flags(cfg, self.meta, "az"),
                         ["--draft", "true", "--reviewers", "Team A", "anna@x.se",
                          "--required-reviewers", "Lead", "--work-items", "4711"])

    def test_gh_flags_join_all_reviewers(self):
        cfg = handoff(draft="yes", reviewers="alice", required_reviewers="org/leads")
        self.assertEqual(pr.create_flags(cfg, self.meta, "gh"), ["--draft", "--reviewer", "alice,org/leads"])

    def test_no_settings_means_no_flags(self):
        self.assertEqual(pr.create_flags(handoff(), self.meta, "az"), [])

    def test_jira_link_points_at_the_issue(self):
        self.assertEqual(pr.jira_line(handoff(jira_url="https://x.atlassian.net/browse/"), "OPT-12"),
                         "Jira: [OPT-12](https://x.atlassian.net/browse/OPT-12)")


class FinalBodyTest(SandboxTest):
    def setUp(self):
        super().setUp()
        self.job = self.sb.queue_job()
        self.jd = self.paths.job(self.job)

    def test_the_written_body_wins_over_the_facts(self):
        self.sb.write("pr-body.md", "## Sammanfattning\n\nSvaret är 42.\n", base=self.jd)
        self.assertEqual(pr.final_body(self.paths, self.cfg, self.job), "## Sammanfattning\n\nSvaret är 42.\n")

    def test_without_a_written_body_the_facts_are_used(self):
        self.assertIn("The answer is 42.", pr.final_body(self.paths, self.cfg, self.job))


if __name__ == "__main__":
    unittest.main()
