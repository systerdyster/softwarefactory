"""The wiki pass against real git: what state reads from the job folder and the branch, the agent's brief
and the PR comment."""

import unittest

from support import SandboxTest, git

from fx import actions, brief, config, pr, queue, state, workspace

WIKI_RULES = "[rules]\nwiki = Wiki/*.md\nwiki_external = Project.wiki\n\n"


class WikiTest(SandboxTest):
    def setUp(self):
        super().setUp()
        self.sb.write("Wiki/Page.md", "# Page\n\nThe answer is 41.\n")
        self.sb.write("Wiki/Conventions.md", "Write in the present tense.\n")
        self.sb.commit("Wiki")
        git(self.sb.root, "push", "origin", "main")
        self.paths.project.write_text(self.paths.project.read_text().replace("[events]", WIKI_RULES + "[events]"), encoding="utf-8")
        self.cfg = config.load(self.paths)
        self.job = self.sb.queue_job()
        self.jd = self.paths.job(self.job)
        queue.take(self.paths)
        workspace.open_(self.paths, self.cfg, self.job)
        self.sb.write("src/app.py", "def answer():\n    return 42\n", base=self.sb.ws)
        self.reviewed = self.sb.commit("Answer", cwd=self.sb.ws)
        self.sb.write("build.md", "# Build log\n\n## Round 1\nStatus: done\n", base=self.jd)

    def facts(self):
        return state.gather(self.paths, self.cfg, self.job)["wiki"]

    def started(self, round_number=1):
        actions.log_spawn(self.paths, self.job, "wiki", round_number, 1, self.reviewed)

    def test_a_wiki_commit_inside_the_globs_is_the_same_reviewed_work(self):
        self.assertEqual((self.facts()["only_wiki"], self.facts()["attempts"]), (None, 0))
        self.started()
        self.sb.write("Wiki/Page.md", "# Page\n\nThe answer is 42.\n", base=self.sb.ws)
        self.sb.commit("Wiki: the answer", cwd=self.sb.ws)
        facts = self.facts()
        self.assertEqual((facts["only_wiki"], facts["stray"], facts["before"]), (True, [], self.reviewed))

    def test_a_commit_that_also_touches_other_files_is_not(self):
        self.started()
        self.sb.write("Wiki/Page.md", "# Page\n\nThe answer is 42.\n", base=self.sb.ws)
        self.sb.write("src/app.py", "def answer():\n    return 43\n", base=self.sb.ws)
        self.sb.commit("Sneaky", cwd=self.sb.ws)
        self.assertEqual((self.facts()["only_wiki"], self.facts()["stray"]), (False, ["src/app.py"]))

    def test_the_report_and_the_spawns_of_the_latest_round_are_what_state_reads(self):
        self.sb.write("round-1/wiki.md", "Status: done\n\n## Nothing to document\nA fix.\n", base=self.jd)
        self.started()
        facts = self.facts()
        self.assertEqual((facts["round"], facts["status"], facts["attempts"]), (1, "done", 1))
        self.sb.write("build.md", "# Build log\n\n## Round 1\nStatus: done\n\n## Round 2\nStatus: done\n", base=self.jd)
        facts = self.facts()
        self.assertEqual((facts["round"], facts["status"], facts["attempts"]), (2, None, 0))

    def test_the_brief_lists_the_pages_the_other_wikis_and_the_rounds_report(self):
        text = "\n".join(brief.wiki(self.paths, self.cfg, self.job, {"head": self.reviewed}, {"round": 1}))
        for expected in ("Wiki/Page.md", "Wiki/Conventions.md", "Project.wiki", "may not create, rename or delete",
                         str(self.jd / "round-1" / "wiki.md")):
            self.assertIn(expected, text)

    def test_the_writer_follows_the_projects_wiki_skills(self):
        self.sb.write(".claude/skills/wiki-sync/SKILL.md", "---\nname: wiki-sync\n---\n# Sync\n", base=self.sb.ws)
        self.paths.project.write_text(self.paths.project.read_text().replace(
            "[events]", "[heroes]\nwriter = skill:wiki-sync\n\n[events]"), encoding="utf-8")
        cfg = config.load(self.paths)
        text = "\n".join(brief.wiki(self.paths, cfg, self.job, {"head": self.reviewed}, {"round": 1}))
        self.assertIn(str(self.sb.ws / ".claude/skills/wiki-sync/SKILL.md"), text)

    def test_the_comment_carries_the_wiki_report_or_says_it_was_not_updated(self):
        self.assertIn("The wiki was not updated", pr.details(self.paths, self.cfg, self.job))
        self.sb.write("round-1/wiki.md", "Status: done\n\n## Changed\n- Wiki/Page.md — says 42\n\n"
                      "## Other wikis affected\n- Project.wiki — an editor page\n", base=self.jd)
        text = pr.details(self.paths, self.cfg, self.job)
        self.assertIn("Wiki/Page.md — says 42", text)
        self.assertIn("Project.wiki — an editor page", text)


if __name__ == "__main__":
    unittest.main()
