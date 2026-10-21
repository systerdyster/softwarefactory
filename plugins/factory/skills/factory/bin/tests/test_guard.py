import io
import json
import unittest

from support import SandboxTest

from fx import guard, testfiles, workspace


class GuardTest(SandboxTest):
    def setUp(self):
        super().setUp()
        self.sb.create_workspace()
        self.job = self.sb.queue_job()
        workspace.switch(self.paths, self.cfg, self.job)
        self.sb.write("tests/test_answer.py", "assert 1\n", base=self.sb.ws)
        self.sb.commit("Tests", cwd=self.sb.ws)
        self.sb.write("tests.md", "| File | Hash |\n|---|---|\n| `tests/test_answer.py` | |\n\nStatus: red-confirmed\n",
                      base=self.paths.job(self.job))
        testfiles.record(self.paths, self.job)
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
        self.assertEqual(self.bash("review-security", "python3 .claude/skills/factory/bin/factory.py ws run suite"), 2)

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
        self.assertEqual(self.bash("builder", "python3 .claude/skills/factory/bin/factory.py ws run test tests/x.py"), 0)
        self.assertEqual(self.bash("builder", "git -C factory/workspace commit --amend -m x"), 2)
        self.assertEqual(self.bash("builder", "git -C factory/workspace commit --no-verify -m x"), 2)

    def test_builder_never_touches_test_writer_files(self):
        self.assertEqual(self.write("builder", self.sb.ws / "src" / "app.py"), 0)
        self.assertEqual(self.write("builder", self.sb.ws / "tests" / "test_answer.py"), 2)
        self.assertEqual(self.bash("builder", "git -C factory/workspace rm tests/test_answer.py"), 2)
        self.assertEqual(self.bash("builder", "git -C factory/workspace restore --source=HEAD~1 tests/test_answer.py"), 2)
        self.assertEqual(self.write("builder", self.jd / "tests.md"), 2)
        self.assertEqual(self.write("builder", self.jd / "build.md"), 0)
        self.assertEqual(self.write("builder", self.jd / "spec.md"), 2)

    def test_no_job_edits_the_factory_itself(self):
        self.assertEqual(self.write("builder", self.sb.ws / ".claude/skills/factory/bin/fx/guard.py"), 2)
        self.assertEqual(self.write("test-writer", self.sb.ws / ".claude/agents/factory-builder.md"), 2)
        self.assertEqual(self.write("builder", self.sb.ws / ".claude/settings.json"), 0)

    def test_test_writer_owns_tests_and_its_job_files(self):
        self.assertEqual(self.write("test-writer", self.sb.ws / "tests" / "test_answer.py"), 0)
        self.assertEqual(self.write("test-writer", self.jd / "tests.md"), 0)
        self.assertEqual(self.write("test-writer", self.jd / "round-2" / "objection-reply-1.md"), 0)
        self.assertEqual(self.write("test-writer", self.jd / "build.md"), 2)

    def test_approver_writes_only_the_decision(self):
        self.assertEqual(self.write("approver", self.jd / "decision.md"), 0)
        self.assertEqual(self.write("approver", self.sb.ws / "src" / "app.py"), 2)

    def test_agents_do_not_spawn_agents(self):
        self.assertEqual(self.call("builder", "Agent", prompt="x"), 2)

    def test_reads_are_always_allowed(self):
        self.assertEqual(self.call("review-ui", "Read", file_path="/etc/hosts"), 0)


if __name__ == "__main__":
    unittest.main()
