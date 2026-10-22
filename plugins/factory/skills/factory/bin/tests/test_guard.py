import io
import json
import unittest

from support import SandboxTest

from fx import guard, workspace


class GuardTest(SandboxTest):
    def setUp(self):
        super().setUp()
        self.sb.create_workspace()
        self.job = self.sb.queue_job()
        workspace.switch(self.paths, self.cfg, self.job)
        self.jd = self.paths.job(self.job)

    def call(self, role, tool, **tool_input):
        event = json.dumps({"tool_name": tool, "tool_input": tool_input})
        return guard.run(role, stream=io.StringIO(event), paths=self.paths)

    def bash(self, role, command):
        return self.call(role, "Bash", command=command)

    def write(self, role, path):
        return self.call(role, "Write", file_path=str(path), content="x")

    def test_reviewers_may_only_read_git(self):
        self.assertEqual(self.bash("review-security", "git -C factory/workspace diff abc..def"), 0)
        self.assertEqual(self.bash("review-security", "git -C factory/workspace log --oneline -5"), 0)
        self.assertEqual(self.bash("review-security", "git -C factory/workspace commit -m x"), 2)
        self.assertEqual(self.bash("review-security", "git -C factory/workspace diff --output=/tmp/x"), 2)

    def test_reviewers_run_only_the_commands_project_md_lists_for_them(self):
        self.assertEqual(self.bash("review-ui", "factory-cli ws run suite"), 2)
        self.sb.write("project.md", self.paths.project.read_text().replace(
            "[reviewers]\n", "[reviewers]\ncommands = echo\n"), base=self.paths.factory)
        self.assertEqual(self.bash("review-ui", "factory-cli ws run echo src/components"), 0)
        self.assertEqual(self.bash("review-ui", "factory-cli ws run suite"), 2)

    def test_reviewers_write_only_their_own_review_file(self):
        self.assertEqual(self.write("review-code", self.jd / "round-1" / "review-code.md"), 0)
        self.assertEqual(self.write("review-code", self.jd / "round-1" / "review-security.md"), 2)
        self.assertEqual(self.write("review-code", self.sb.ws / "src" / "app.py"), 2)
        self.assertEqual(self.call("review-code", "Edit", file_path=str(self.sb.ws / "src/app.py")), 2)

    def test_shell_operators_and_other_programs_are_blocked(self):
        self.assertEqual(self.bash("builder", "cd factory/workspace && npm test"), 2)
        self.assertEqual(self.bash("builder", "git -C factory/workspace status; rm -rf /"), 2)
        self.assertEqual(self.bash("builder", "npm test"), 2)
        self.assertEqual(self.bash("builder", "git status"), 2)

    def test_no_agent_pushes_switches_or_rebases(self):
        for command in ("push origin HEAD", "switch main", "checkout main", "rebase origin/main", "reset --hard"):
            self.assertEqual(self.bash("builder", f"git -C factory/workspace {command}"), 2, command)

    def test_builder_commits_and_runs_project_commands(self):
        self.assertEqual(self.bash("builder", "git -C factory/workspace add src/app.py"), 0)
        self.assertEqual(self.bash("builder", "git -C factory/workspace commit -m 'Answer 42'"), 0)
        self.assertEqual(self.bash("builder", "factory-cli ws run test tests/x.py"), 0)
        self.assertEqual(self.bash("builder", "git -C factory/workspace commit --amend -m x"), 2)
        self.assertEqual(self.bash("builder", "git -C factory/workspace commit --no-verify -m x"), 2)

    def test_builder_owns_code_tests_and_its_two_job_files(self):
        self.assertEqual(self.write("builder", self.sb.ws / "src" / "app.py"), 0)
        self.assertEqual(self.write("builder", self.sb.ws / "tests" / "test_answer.py"), 0)
        self.assertEqual(self.write("builder", self.jd / "tests.md"), 0)
        self.assertEqual(self.write("builder", self.jd / "build.md"), 0)
        self.assertEqual(self.write("builder", self.jd / "spec.md"), 2)
        self.assertEqual(self.write("builder", self.jd / "baseline.json"), 2)

    def test_no_job_edits_the_factory_itself(self):
        self.assertEqual(self.write("builder", self.paths.plugin / "skills/factory/bin/fx/guard.py"), 2)
        self.assertEqual(self.write("builder", self.sb.ws / ".claude/settings.json"), 0)

    def test_agents_do_not_spawn_agents(self):
        self.assertEqual(self.call("builder", "Agent", prompt="x"), 2)

    def test_reads_are_always_allowed(self):
        self.assertEqual(self.call("review-ui", "Read", file_path="/etc/hosts"), 0)


if __name__ == "__main__":
    unittest.main()
