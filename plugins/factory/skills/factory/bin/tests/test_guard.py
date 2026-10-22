import io
import json
import unittest

from support import SandboxTest

from fx import actions, guard, workspace


class GuardTest(SandboxTest):
    def setUp(self):
        super().setUp()
        self.job = self.sb.queue_job()
        self.other = self.sb.queue_job("002-other", "Other")
        for job in (self.job, self.other):
            workspace.open_(self.paths, self.cfg, job)
            for agent in ("builder", "review-code", "review-ui", "review-security"):
                actions.log_spawn(self.paths, job, agent, 1, 1, None)
        self.jd = self.paths.job(self.job)
        self.ws = f"factory/workspaces/{self.job}"

    def call(self, role, tool, agent_id=None, **tool_input):
        event = {"tool_name": tool, "tool_input": tool_input}
        if agent_id:
            event["agent_id"] = agent_id
        return guard.run(role, stream=io.StringIO(json.dumps(event)), paths=self.paths)

    def bash(self, role, command, agent_id=None):
        return self.call(role, "Bash", agent_id, command=command)

    def write(self, role, path, agent_id=None):
        return self.call(role, "Write", agent_id, file_path=str(path), content="x")

    def test_reviewers_may_only_read_git(self):
        self.assertEqual(self.bash("review-security", f"git -C {self.ws} diff abc..def"), 0)
        self.assertEqual(self.bash("review-security", f"git -C {self.ws} log --oneline -5"), 0)
        self.assertEqual(self.bash("review-security", f"git -C {self.ws} commit -m x"), 2)
        self.assertEqual(self.bash("review-security", f"git -C {self.ws} diff --output=/tmp/x"), 2)

    def test_reviewers_run_only_the_commands_project_md_lists_for_them(self):
        self.assertEqual(self.bash("review-ui", f"factory-cli ws run {self.job} suite"), 2)
        self.sb.write("project.md", self.paths.project.read_text().replace(
            "[reviewers]\n", "[reviewers]\ncommands = echo\n"), base=self.paths.factory)
        self.assertEqual(self.bash("review-ui", f"factory-cli ws run {self.job} echo src/components"), 0)
        self.assertEqual(self.bash("review-ui", f"factory-cli ws run {self.job} suite"), 2)

    def test_reviewers_write_only_their_own_review_file(self):
        self.assertEqual(self.write("review-code", self.jd / "round-1" / "review-code.md"), 0)
        self.assertEqual(self.write("review-code", self.jd / "round-1" / "review-security.md"), 2)
        self.assertEqual(self.write("review-code", self.sb.ws / "src" / "app.py"), 2)

    def test_git_must_name_a_job_worktree(self):
        self.assertEqual(self.bash("builder", "git -C factory/workspace status"), 2)
        self.assertEqual(self.bash("builder", "git status"), 2)
        self.assertEqual(self.bash("builder", "git -C factory/workspaces status"), 2)
        self.assertEqual(self.bash("builder", f"git -C {self.ws}/src status"), 2)

    def test_shell_operators_and_other_programs_are_blocked(self):
        self.assertEqual(self.bash("builder", f"cd {self.ws} && npm test"), 2)
        self.assertEqual(self.bash("builder", f"git -C {self.ws} status; rm -rf /"), 2)
        self.assertEqual(self.bash("builder", "npm test"), 2)

    def test_no_agent_pushes_switches_or_rebases(self):
        for command in ("push origin HEAD", "switch main", "checkout main", "rebase origin/main", "reset --hard"):
            self.assertEqual(self.bash("builder", f"git -C {self.ws} {command}"), 2, command)

    def test_builder_commits_and_runs_project_commands_in_its_job(self):
        self.assertEqual(self.bash("builder", f"git -C {self.ws} add src/app.py"), 0)
        self.assertEqual(self.bash("builder", f"git -C {self.ws} commit -m 'Answer 42'"), 0)
        self.assertEqual(self.bash("builder", f"factory-cli ws run {self.job} test tests/x.py"), 0)
        self.assertEqual(self.bash("builder", f"git -C {self.ws} commit --amend -m x"), 2)

    def test_builder_owns_code_tests_and_its_two_job_files(self):
        self.assertEqual(self.write("builder", self.sb.ws / "src" / "app.py"), 0)
        self.assertEqual(self.write("builder", self.jd / "tests.md"), 0)
        self.assertEqual(self.write("builder", self.jd / "build.md"), 0)
        self.assertEqual(self.write("builder", self.jd / "spec.md"), 2)
        self.assertEqual(self.write("builder", self.jd / "baseline.json"), 2)
        self.assertEqual(self.write("builder", self.paths.factory / "board.json"), 2)

    def test_an_agent_is_bound_to_the_first_job_it_touches(self):
        self.assertEqual(self.write("builder", self.sb.ws / "src" / "app.py", "agent-a"), 0)
        self.assertEqual(self.write("builder", self.sb.ws_of(self.other) / "src" / "app.py", "agent-a"), 2)
        self.assertEqual(self.bash("builder", f"git -C factory/workspaces/{self.other} status", "agent-a"), 2)
        self.assertEqual(self.bash("builder", f"factory-cli ws run {self.other} suite", "agent-a"), 2)
        self.assertEqual(self.write("builder", self.paths.job(self.other) / "build.md", "agent-a"), 2)
        self.assertEqual(self.write("builder", self.sb.ws_of(self.other) / "src" / "app.py", "agent-b"), 0)

    def test_a_job_binds_no_more_agents_of_a_role_than_it_spawned(self):
        self.assertEqual(self.write("builder", self.sb.ws / "a.py", "agent-a"), 0)
        self.assertEqual(self.write("builder", self.sb.ws / "a.py", "agent-stray"), 2)
        self.assertEqual(self.write("review-code", self.jd / "round-1" / "review-code.md", "agent-c"), 0)

    def test_a_job_that_never_spawned_the_role_refuses_it(self):
        third = self.sb.queue_job("003-third", "Third")
        workspace.open_(self.paths, self.cfg, third)
        self.assertEqual(self.write("builder", self.sb.ws_of(third) / "a.py"), 2)

    def test_finishing_a_job_releases_its_agents(self):
        self.write("builder", self.sb.ws / "a.py", "agent-a")
        guard.release(self.paths, self.job)
        self.assertFalse((self.paths.agents / "agent-a.json").exists())

    def test_no_job_edits_the_factory_itself(self):
        self.assertEqual(self.write("builder", self.paths.plugin / "skills/factory/bin/fx/guard.py"), 2)

    def test_agents_do_not_spawn_agents(self):
        self.assertEqual(self.call("builder", "Agent", prompt="x"), 2)

    def test_reads_are_always_allowed(self):
        self.assertEqual(self.call("review-ui", "Read", file_path="/etc/hosts"), 0)


if __name__ == "__main__":
    unittest.main()
