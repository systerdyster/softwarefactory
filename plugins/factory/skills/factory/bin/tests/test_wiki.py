"""The wiki pass against real git: what state reads from the job folder and the branch, the agent's brief
and the PR comment."""

import json
import unittest

from support import SandboxTest, git

from fx import actions, brief, config, jobs, pr, queue, state, workspace
from fx.errors import FactoryError

WIKI_RULES = "[rules]\nwiki = Wiki/*.md\nwiki_external = Project.wiki\nwiki_guide = Wiki/Conventions.md\n\n"


class WikiTest(SandboxTest):
    def setUp(self):
        super().setUp()
        self.sb.write("Wiki/Page.md", "# Page\n\nThe answer is 41.\n")
        self.sb.write("Wiki/Conventions.md", "Write in the present tense.\n")
        self.sb.commit("Wiki")
        git(self.sb.root, "push", "origin", "main")
        self.paths.project.write_text(self.paths.project.read_text().replace("[jira]", WIKI_RULES + "[jira]"), encoding="utf-8")
        self.cfg = config.load(self.paths)
        self.job = self.sb.queue_job()
        self.jd = self.paths.job(self.job)
        queue.take(self.paths)
        workspace.open_(self.paths, self.cfg, self.job)
        self.sb.write("src/app.py", "def answer():\n    return 42\n", base=self.sb.ws)
        self.approved = self.sb.commit("Answer", cwd=self.sb.ws)
        self.sb.write("human-approval.md", f"Head: {self.approved}\nApproved-By: Fredrik\nAt: {jobs.now()}\n", base=self.jd)

    def facts(self):
        return state.gather(self.paths, self.cfg, self.job)["wiki"]

    def test_a_wiki_commit_inside_the_globs_is_the_same_approved_work(self):
        self.assertEqual((self.facts()["only_wiki"], self.facts()["attempts"]), (None, 0))
        self.sb.write("Wiki/Page.md", "# Page\n\nThe answer is 42.\n", base=self.sb.ws)
        self.sb.commit("Wiki: the answer", cwd=self.sb.ws)
        self.assertEqual((self.facts()["only_wiki"], self.facts()["stray"]), (True, []))

    def test_a_commit_that_also_touches_other_files_is_not(self):
        self.sb.write("Wiki/Page.md", "# Page\n\nThe answer is 42.\n", base=self.sb.ws)
        self.sb.write("src/app.py", "def answer():\n    return 43\n", base=self.sb.ws)
        self.sb.commit("Sneaky", cwd=self.sb.ws)
        self.assertEqual((self.facts()["only_wiki"], self.facts()["stray"]), (False, ["src/app.py"]))

    def test_the_report_and_the_spawns_since_the_approval_are_what_state_reads(self):
        self.sb.write("wiki.md", "Status: done\n\n## Nothing to document\nA fix.\n", base=self.jd)
        actions.log_spawn(self.paths, self.job, "wiki", 0, 1, self.approved)
        facts = self.facts()
        self.assertEqual((facts["status"], facts["attempts"]), ("done", 1))

    def test_the_record_ties_the_approved_head_to_the_head_the_wiki_pass_ended_on(self):
        self.sb.write("baseline.json", json.dumps({"green": True}), base=self.jd)
        self.sb.write("Wiki/Page.md", "# Page\n\nThe answer is 42.\n", base=self.sb.ws)
        after = self.sb.commit("Wiki: the answer", cwd=self.sb.ws)
        self.sb.write("wiki.md", "Status: done\n", base=self.jd)
        self.assertEqual(state.state(self.paths, self.cfg, self.job)["next"]["action"], "wiki-record")
        record = actions.wiki_record(self.paths, self.cfg, self.job)
        self.assertEqual((record["approved"], record["after"]), (self.approved, after))
        self.assertTrue(self.facts()["recorded"])
        self.assertEqual(state.state(self.paths, self.cfg, self.job)["next"]["action"], "rebase")

    def test_a_real_rebase_that_rewrites_the_wiki_commit_keeps_the_approval(self):
        self.sb.write("baseline.json", json.dumps({"green": True}), base=self.jd)
        self.sb.write("Wiki/Page.md", "# Page\n\nThe answer is 42.\n", base=self.sb.ws)
        self.sb.commit("Wiki: the answer", cwd=self.sb.ws)
        self.sb.write("wiki.md", "Status: done\n", base=self.jd)
        actions.wiki_record(self.paths, self.cfg, self.job)
        self.sb.push_target_change("README.md", "Someone else's change\n")
        self.assertEqual(actions.rebase(self.paths, self.cfg, self.job)["result"], "green")
        nxt = state.state(self.paths, self.cfg, self.job)
        self.assertEqual((nxt["stage"], nxt["next"]["action"]), ("merging", "pr"))

    def test_nothing_is_recorded_while_the_wiki_pass_has_left_pages_uncommitted(self):
        self.sb.write("baseline.json", json.dumps({"green": True}), base=self.jd)
        self.sb.write("Wiki/Page.md", "# Page\n\nThe answer is 42.\n", base=self.sb.ws)
        self.sb.write("wiki.md", "Status: done\n", base=self.jd)
        self.assertEqual(state.state(self.paths, self.cfg, self.job)["next"]["action"], "spawn-wiki")
        with self.assertRaises(FactoryError):
            actions.wiki_record(self.paths, self.cfg, self.job)

    def test_a_new_approval_starts_the_wiki_pass_over(self):
        self.sb.write("wiki.md", "Status: done\n", base=self.jd)
        actions._archive(self.paths, self.job, "wiki.md")
        self.assertFalse((self.jd / "wiki.md").exists())

    def test_the_brief_lists_the_pages_the_guide_and_the_other_wikis(self):
        text = "\n".join(brief.wiki(self.paths, self.cfg, self.job, {"head": self.approved}, {}))
        for expected in ("Wiki/Page.md", "Wiki/Conventions.md", "Project.wiki", "may not create, rename or delete"):
            self.assertIn(expected, text)

    def test_the_comment_carries_the_wiki_report_or_says_it_was_not_updated(self):
        self.assertIn("The wiki was not updated", pr.details(self.paths, self.cfg, self.job))
        self.sb.write("wiki.md", "Status: done\n\n## Changed\n- Wiki/Page.md — says 42\n\n## Other wikis affected\n"
                      "- Project.wiki — an editor page\n", base=self.jd)
        text = pr.details(self.paths, self.cfg, self.job)
        self.assertIn("Wiki/Page.md — says 42", text)
        self.assertIn("Project.wiki — an editor page", text)


if __name__ == "__main__":
    unittest.main()
