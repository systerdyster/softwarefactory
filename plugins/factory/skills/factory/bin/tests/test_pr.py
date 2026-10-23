import os
import unittest
from unittest import mock

from support import SandboxTest, git

from fx import actions, config, gitutil, jobs, pr, queue, state, workspace
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

    def test_the_description_is_the_summary_and_the_review_facts_go_in_the_comment(self):
        self.sb.write("tests.md", "## Exemptions\n\n- Logging: no observable output.\n", base=self.jd)
        self.sb.write("round-1/review-security.md", "VERDICT: PASS\n", base=self.jd)
        self.sb.write("round-1/review-codex.md", "VERDICT: PASS\nFALLBACK: claude\n", base=self.jd)
        self.sb.write("round-1/review-code.md", "VERDICT: PASS\n\n## Least sure\n\n1. The rounding in app.py:2.\n",
                      base=self.jd)
        self.sb.write("human-approval.md", "Head: 0123456789abcdef\nApproved-By: Fredrik\n", base=self.jd)
        self.assertEqual(pr.body(self.paths, self.cfg, self.job), "The answer is 42.\n")
        text = pr.details(self.paths, self.cfg, self.job)
        for expected in ("| security | PASS |", "Logging: no observable output.",
                         "The rounding in app.py:2.", "FALLBACK: claude", "Approved by Fredrik at `0123456789ab`"):
            self.assertIn(expected, text)

    def test_the_comment_lists_what_each_reviewer_still_holds_after_the_fix_rounds(self):
        self.sb.write("round-1/review-code.md", "VERDICT: CHANGES\n\n## Findings\n"
                      "- 🔴 BLOCKING `src/app.py:2` — wrong.\n- 🟡 NOTE `docs.md:1` — stale docs.\n", base=self.jd)
        self.sb.write("round-1/review-ui.md", "VERDICT: PASS\n\n## Findings\n- 🟡 NOTE `a.css:3` — not rendered.\n",
                      base=self.jd)
        self.sb.write("round-2/review-code.md", "VERDICT: PASS\n\n## Findings\n- 🟡 NOTE `docs.md:1` — stale docs.\n",
                      base=self.jd)
        text = pr.details(self.paths, self.cfg, self.job)
        self.assertIn("- **code** — 🟡 NOTE `docs.md:1` — stale docs.", text)
        self.assertIn("- **ui** — 🟡 NOTE `a.css:3` — not rendered.", text)
        self.assertNotIn("wrong.", text)

    def test_the_test_note_goes_in_the_comment_and_an_empty_one_leaves_it_out(self):
        self.assertNotIn("Test note", pr.details(self.paths, self.cfg, self.job))
        self.sb.write("test-note.md", "Öppna katalogen och byt vy.\n", base=self.jd)
        self.assertIn("### Test note\n\nÖppna katalogen och byt vy.", pr.details(self.paths, self.cfg, self.job))

    def test_a_review_that_never_rendered_the_change_is_flagged_for_the_developer(self):
        self.sb.write("round-1/review-ui.md", "VERDICT: PASS\n\nRENDERED: no — the project lists no render commands\n",
                      base=self.jd)
        self.sb.write("round-1/review-ux.md", "VERDICT: PASS\n\nRENDERED: catalog page at 360 and 1280px\n",
                      base=self.jd)
        text = pr.details(self.paths, self.cfg, self.job)
        self.assertIn("- **ui** — the project lists no render commands", text)
        self.assertNotIn("**ux**", text)

    def test_a_description_with_ai_attribution_is_refused(self):
        self.sb.write("pr-body.md", "## Answer\n\nThe answer is 42.\n\n🤖 Generated with [Claude Code](https://x)\n",
                      base=self.jd)
        with self.assertRaises(FactoryError):
            pr.open_or_update(self.paths, self.cfg, self.job)
        self.assertEqual(git(self.sb.origin, "branch", "--list", "factory/001-answer"), "")

    def test_agents_get_the_default_commit_convention_unless_the_project_has_one(self):
        self.assertIn("Subject imperative", self.cfg.commits)
        self.paths.project.write_text(self.paths.project.read_text().replace(
            "[jira]", "[handoff]\ncommits = Swedish, key first\n\n[jira]"), encoding="utf-8")
        self.assertEqual(config.load(self.paths).commits, "Swedish, key first")

    def test_gh_gets_a_short_description_and_one_details_comment_rewritten_on_update(self):
        log = self.sb.tmp / "gh.log"
        fake = self.sb.write("bin/gh", "#!/bin/sh\n"
                             f'echo "$@" >> {log}\n'
                             'case "$1 $2" in\n'
                             '  "pr view") echo \'{"number": 5, "url": "https://gh/pr/5", "state": "OPEN"}\' ;;\n'
                             '  "api -X") [ "$3" = POST ] && echo \'{"id": 77}\' || echo \'{}\' ;;\n'
                             'esac\n', base=self.sb.tmp)
        fake.chmod(0o755)
        self.paths.project.write_text(self.paths.project.read_text().replace("pr_tool = other", "pr_tool = gh"),
                                      encoding="utf-8")
        cfg = config.load(self.paths)
        old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{fake.parent}:{old_path}"
        try:
            first = pr.open_or_update(self.paths, cfg, self.job)
            second = pr.open_or_update(self.paths, cfg, self.job)
        finally:
            os.environ["PATH"] = old_path
        calls = log.read_text().splitlines()
        self.assertEqual((first["details_comment"], second["details_comment"], second.get("details_error")), (77, 77, None))
        self.assertEqual(sum("api -X POST repos/{owner}/{repo}/issues/5/comments" in c for c in calls), 1)
        self.assertEqual(sum("api -X PATCH repos/{owner}/{repo}/issues/comments/77" in c for c in calls), 1)

    def test_a_pr_is_opened_with_auto_complete_only_when_the_developer_chose_it(self):
        log = self.sb.tmp / "gh.log"
        fake = self.sb.write("bin/gh", "#!/bin/sh\n"
                             f'echo "$@" >> {log}\n'
                             'case "$1 $2" in\n'
                             '  "pr view") echo \'{"number": 5, "url": "https://gh/pr/5", "state": "OPEN"}\' ;;\n'
                             '  "api -X") echo \'{"id": 77}\' ;;\n'
                             'esac\n', base=self.sb.tmp)
        fake.chmod(0o755)
        self.paths.project.write_text(self.paths.project.read_text().replace("pr_tool = other", "pr_tool = gh")
                                      .replace("merge_mode = local-merge", "merge_mode = pr"), encoding="utf-8")
        cfg = config.load(self.paths)
        old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{fake.parent}:{old_path}"
        try:
            pr.open_or_update(self.paths, cfg, self.job)
            self.assertNotIn("pr merge", log.read_text())
            self.sb.write("human-approval.md", "Head: abc\nApproved-By: Fredrik\nAuto-Complete: yes\n", base=self.jd)
            self.assertTrue(pr.open_or_update(self.paths, cfg, self.job)["auto"])
        finally:
            os.environ["PATH"] = old_path
        self.assertIn("pr merge 5 --auto --merge", log.read_text())

    def test_a_plain_publish_takes_auto_complete_off_a_pr_that_had_it(self):
        log = self.sb.tmp / "calls.log"
        fake_body = ('#!/bin/sh\n'
                     f'echo "$(basename "$0") $@" >> {log}\n'
                     'case "$1 $2" in\n'
                     '  "pr view") echo \'{"number": 5, "url": "https://gh/pr/5", "state": "OPEN"}\' ;;\n'
                     '  "api -X") echo \'{"id": 77}\' ;;\n'
                     '  "devops invoke") echo \'{"id": 1}\' ;;\n'
                     '  "repos pr") echo \'{"pullRequestId": 5, "status": "active", "repository": {"webUrl": "https://az/r"}}\' ;;\n'
                     'esac\n')
        self.sb.write("bin/gh", fake_body, base=self.sb.tmp).chmod(0o755)
        self.sb.write("bin/az", fake_body, base=self.sb.tmp).chmod(0o755)
        old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.sb.tmp / 'bin'}:{old_path}"
        try:
            for tool, off in (("gh", "pr merge 5 --disable-auto"), ("az", "--auto-complete false")):
                self.paths.project.write_text(self.paths.project.read_text().replace("pr_tool = other", f"pr_tool = {tool}")
                                              .replace("pr_tool = gh", f"pr_tool = {tool}")
                                              .replace("pr_tool = az", f"pr_tool = {tool}")
                                              .replace("merge_mode = local-merge", "merge_mode = pr"), encoding="utf-8")
                cfg = config.load(self.paths)
                self.sb.write("human-approval.md", "Head: abc\nAuto-Complete: yes\n", base=self.jd)
                pr.open_or_update(self.paths, cfg, self.job)
                log.write_text("")
                self.sb.write("human-approval.md", "Head: abc\n", base=self.jd)
                self.assertFalse(pr.open_or_update(self.paths, cfg, self.job)["auto"])
                self.assertIn(off, log.read_text(), tool)
                self.assertNotIn("--auto --merge", log.read_text())
        finally:
            os.environ["PATH"] = old_path

    def test_nothing_is_pushed_when_auto_complete_cannot_be_switched_off(self):
        fake = self.sb.write("bin/gh", '#!/bin/sh\n'
                             'case "$1 $2" in\n'
                             '  "pr view") echo \'{"number": 5, "url": "https://gh/pr/5", "state": "OPEN"}\' ;;\n'
                             '  "api -X") echo \'{"id": 77}\' ;;\n'
                             '  "pr merge") case "$*" in *--disable-auto*) echo boom >&2; exit 1 ;; esac ;;\n'
                             'esac\n', base=self.sb.tmp)
        fake.chmod(0o755)
        self.paths.project.write_text(self.paths.project.read_text().replace("pr_tool = other", "pr_tool = gh")
                                      .replace("merge_mode = local-merge", "merge_mode = pr"), encoding="utf-8")
        cfg = config.load(self.paths)
        old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{fake.parent}:{old_path}"
        try:
            self.sb.write("human-approval.md", "Head: abc\nAuto-Complete: yes\n", base=self.jd)
            pr.open_or_update(self.paths, cfg, self.job)
            self.sb.write("human-approval.md", "Head: abc\n", base=self.jd)
            with mock.patch.object(pr, "_push") as push:
                with self.assertRaises(FactoryError):
                    pr.open_or_update(self.paths, cfg, self.job)
            push.assert_not_called()
        finally:
            os.environ["PATH"] = old_path

    def test_other_tool_pushes_and_leaves_the_pr_to_a_human(self):
        record = pr.open_or_update(self.paths, self.cfg, self.job)
        self.assertEqual(record["state"], "MANUAL")
        self.assertEqual(git(self.sb.origin, "rev-parse", "factory/001-answer"), self.head)

    def test_local_merge_in_a_clean_checkout_merges_there_and_the_working_tree_follows(self):
        record = pr.merge_local(self.paths, self.cfg, self.job)
        self.assertEqual((record["merged"], record["via"], record["tree_matches"]), (True, "checkout", True))
        self.assertEqual(git(self.sb.root, "rev-parse", "HEAD"), record["commit"])
        self.assertIn("return 42", (self.sb.root / "src" / "app.py").read_text())
        self.assertEqual(len(git(self.sb.root, "rev-list", "--parents", "-n", "1", "main").split()), 3)

    def test_local_merge_leaves_a_dirty_checkout_alone(self):
        self.sb.write("notes.txt", "mine\n")
        before = git(self.sb.root, "rev-parse", "main")
        record = pr.merge_local(self.paths, self.cfg, self.job)
        self.assertFalse(record["merged"])
        self.assertIn("uncommitted changes (notes.txt)", record["blocked"])
        self.assertEqual(git(self.sb.root, "rev-parse", "main"), before)

    def test_local_merge_waits_for_a_half_done_operation_in_the_checkout(self):
        (self.sb.root / ".git" / "MERGE_HEAD").write_text(self.head + "\n")
        self.assertIn("a merge is in progress", pr.merge_local(self.paths, self.cfg, self.job)["blocked"])

    def test_local_merge_refuses_while_another_worktree_holds_the_target(self):
        git(self.sb.root, "switch", "--detach")
        other = self.sb.tmp / "second-main"
        git(self.sb.root, "worktree", "add", str(other), "main")
        record = pr.merge_local(self.paths, self.cfg, self.job)
        self.assertFalse(record["merged"])
        self.assertIn(f"main is checked out in {other}", record["blocked"])

    def test_a_merge_the_checkouts_hook_refuses_is_aborted_and_reported(self):
        hook = self.sb.root / ".git" / "hooks" / "commit-msg"
        hook.write_text("#!/bin/sh\necho 'a body is required' >&2\nexit 1\n")
        hook.chmod(0o755)
        before = git(self.sb.root, "rev-parse", "main")
        record = pr.merge_local(self.paths, self.cfg, self.job)
        self.assertFalse(record["merged"])
        self.assertIn("a body is required", record["blocked"])
        self.assertEqual(git(self.sb.root, "rev-parse", "main"), before)
        self.assertIsNone(gitutil.operation_in_progress(self.sb.root))

    def test_local_merge_builds_a_no_ff_merge_without_touching_any_checkout(self):
        git(self.sb.root, "switch", "--detach")
        record = pr.merge_local(self.paths, self.cfg, self.job)
        self.assertTrue(record["merged"])
        parents = git(self.sb.root, "rev-list", "--parents", "-n", "1", "main").split()
        self.assertEqual(len(parents), 3)
        self.assertEqual(parents[2], self.head)
        self.assertEqual(jobs.read_json(self.jd / "merge.json")["commit"], parents[0])


class MergedDetectionTest(SandboxTest):
    def setUp(self):
        super().setUp()
        self.job = self.sb.queue_job()
        queue.take(self.paths)
        workspace.open_(self.paths, self.cfg, self.job)
        self.sb.write("src/app.py", "def answer():\n    return 42\n", base=self.sb.ws)
        self.sb.commit("Answer", cwd=self.sb.ws)
        self.sb.write("src/extra.py", "X = 1\n", base=self.sb.ws)
        self.sb.commit("Extra", cwd=self.sb.ws)
        self.record = pr.open_or_update(self.paths, self.cfg, self.job)
        self.branch = "factory/001-answer"

    def merge_on_target(self, *args):
        git(self.sb.root, "fetch", "origin")
        git(self.sb.root, "switch", "--detach", "origin/main")
        git(self.sb.root, *args)
        git(self.sb.root, "push", "origin", "HEAD:main")
        git(self.sb.root, "fetch", "origin")

    def state(self):
        return jobs.read_json(self.jd / "pr.json")["state"]

    @property
    def jd(self):
        return self.paths.job(self.job)

    def squash_on_target(self):
        self.merge_on_target("merge", "--squash", self.branch)
        git(self.sb.root, "commit", "-m", "Squash")
        git(self.sb.root, "push", "origin", "HEAD:main")
        git(self.sb.root, "fetch", "origin")

    def as_gh(self, pr_state):
        """The job's PR as a gh PR whose state the fake gh reports."""
        fake = self.sb.write("bin/gh", f'#!/bin/sh\necho \'{{"number": 5, "url": "u", "state": "{pr_state}"}}\'\n',
                             base=self.sb.tmp)
        fake.chmod(0o755)
        record = jobs.read_json(self.jd / "pr.json")
        jobs.write_json(self.jd / "pr.json", {**record, "tool": "gh", "number": 5, "state": "OPEN"})
        old = os.environ["PATH"]
        os.environ["PATH"] = f"{fake.parent}:{old}"
        self.addCleanup(os.environ.__setitem__, "PATH", old)

    def test_a_branch_that_is_not_in_the_target_stays_open(self):
        self.assertIsNone(pr.check_merged(self.paths, self.cfg, self.job))
        self.assertEqual(self.state(), "MANUAL")

    def test_a_merge_commit_is_proof_without_a_pr_tool(self):
        self.merge_on_target("merge", "--no-ff", "-m", "Merge", self.branch)
        self.assertEqual(pr.check_merged(self.paths, self.cfg, self.job), "merged")
        self.assertEqual(self.state(), "MERGED")

    def test_a_squash_is_only_a_suspicion_without_a_pr_tool_and_asked_once(self):
        self.squash_on_target()
        self.assertEqual(pr.check_merged(self.paths, self.cfg, self.job), "suspected")
        self.assertEqual(self.state(), "MANUAL")
        self.assertIsNone(pr.check_merged(self.paths, self.cfg, self.job))

    def test_confirming_a_suspicion_lets_the_job_finish(self):
        self.squash_on_target()
        pr.check_merged(self.paths, self.cfg, self.job)
        pr.confirm_merged(self.paths, self.cfg, self.job)
        self.assertEqual(state.state(self.paths, self.cfg, self.job)["next"]["action"], "finish")

    def test_the_same_content_arriving_another_way_does_not_close_a_pr_the_tool_says_is_open(self):
        self.as_gh("OPEN")
        self.squash_on_target()
        self.assertIsNone(pr.check_merged(self.paths, self.cfg, self.job))
        self.assertEqual(self.state(), "OPEN")

    def test_a_pr_the_tool_says_is_merged_is_merged(self):
        self.as_gh("MERGED")
        self.squash_on_target()
        self.assertEqual(pr.check_merged(self.paths, self.cfg, self.job), "merged")

    def test_a_closed_pr_is_left_alone(self):
        record = jobs.read_json(self.jd / "pr.json")
        jobs.write_json(self.jd / "pr.json", {**record, "state": "CLOSED"})
        self.merge_on_target("merge", "--no-ff", "-m", "Merge", self.branch)
        self.assertIsNone(pr.check_merged(self.paths, self.cfg, self.job))

    def test_wait_wakes_when_the_target_took_the_branch(self):
        self.merge_on_target("merge", "--no-ff", "-m", "Merge", self.branch)
        self.assertEqual(actions.wait(self.paths, self.cfg, timeout=5, interval=0.1), {"woken_by": "merged"})
        self.assertEqual(state.state(self.paths, self.cfg, self.job)["next"]["action"], "finish")

    def test_wait_wakes_to_ask_when_only_the_content_arrived(self):
        self.squash_on_target()
        self.assertEqual(actions.wait(self.paths, self.cfg, timeout=5, interval=0.1), {"woken_by": "merge-suspected"})

    def test_wait_times_out_while_the_target_has_not_moved(self):
        self.assertEqual(actions.wait(self.paths, self.cfg, timeout=0.3, interval=0.1), {"woken_by": "timeout"})


class LocalTargetTest(SandboxTest):
    """A gitflow project: no remote is read or written, and the local target is ahead of origin because
    finished cases are batched there until a person pushes."""
    merge_mode = "local-merge"

    def setUp(self):
        super().setUp()
        self.paths.project.write_text(self.paths.project.read_text().replace("remote = origin", "remote = none"),
                                      encoding="utf-8")
        self.cfg = config.load(self.paths)
        self.sb.write("batched.txt", "an earlier case, not pushed\n")
        self.batched = self.sb.commit("Earlier case")
        self.origin_head = git(self.sb.root, "rev-parse", "origin/main")
        self.job = self.sb.queue_job()
        queue.take(self.paths)

    def test_a_merge_that_is_undone_by_hand_does_not_read_as_merged(self):
        actions.start(self.paths, self.cfg, self.job)
        self.sb.write("src/app.py", "def answer():\n    return 42\n", base=self.sb.ws)
        self.sb.commit("Answer", cwd=self.sb.ws)
        actions.rebase(self.paths, self.cfg, self.job)
        record = pr.merge_local(self.paths, self.cfg, self.job)
        self.assertEqual(state.state(self.paths, self.cfg, self.job)["next"]["action"], "finish")
        git(self.sb.root, "reset", "--hard", record["previous"])
        self.assertNotEqual(state.state(self.paths, self.cfg, self.job)["next"]["action"], "finish")

    def test_the_job_starts_from_the_local_target_and_merges_on_top_of_it_without_touching_origin(self):
        self.assertEqual(self.cfg.base_ref, "main")
        actions.start(self.paths, self.cfg, self.job)
        self.sb.write("src/app.py", "def answer():\n    return 42\n", base=self.sb.ws)
        head = self.sb.commit("Answer", cwd=self.sb.ws)
        self.assertEqual(actions.rebase(self.paths, self.cfg, self.job)["result"], "green")
        git(self.sb.root, "switch", "--detach")
        self.assertTrue(pr.merge_local(self.paths, self.cfg, self.job)["merged"])
        self.assertTrue(gitutil.is_ancestor(self.batched, "main", self.sb.root))
        self.assertTrue(gitutil.is_ancestor(head, "main", self.sb.root))
        self.assertEqual(git(self.sb.root, "rev-parse", "origin/main"), self.origin_head)
        self.assertEqual(state.state(self.paths, self.cfg, self.job)["next"]["action"], "finish")
        self.assertFalse(actions.finish(self.paths, self.cfg, self.job)["remote_deleted"])


if __name__ == "__main__":
    unittest.main()
