import io
import json
import unittest

from support import SandboxTest

from fx import guard, jobs, runs, workspace


class GuardTest(SandboxTest):
    def setUp(self):
        super().setUp()
        self.job = self.sb.queue_job()
        self.other = self.sb.queue_job("002-other", "Other")
        for job in (self.job, self.other):
            workspace.open_(self.paths, self.cfg, job)
        self.jd = self.paths.job(self.job)
        self.ws = f"factory/workspaces/{self.job}"

    def running(self, agent_id, role="builder", job=None, round_number=1):
        run = runs.reserve(self.paths, self.cfg, job or self.job, role, round_number, 1, None)
        return runs.attach(self.paths, run["run"], agent_id)

    def call(self, role, tool, agent_id=None, **tool_input):
        event = {"tool_name": tool, "tool_input": tool_input}
        if agent_id:
            event["agent_id"] = agent_id
        return guard.run(role, stream=io.StringIO(json.dumps(event)), paths=self.paths)

    def bash(self, role, command, agent_id="agent-a"):
        return self.call(role, "Bash", agent_id, command=command)

    def write(self, role, path, agent_id="agent-a"):
        return self.call(role, "Write", agent_id, file_path=str(path), content="x")

    def test_reviewers_may_only_read_git(self):
        self.assertEqual(self.bash("review-security", f"git -C {self.ws} diff abc..def", None), 0)
        self.assertEqual(self.bash("review-security", f"git -C {self.ws} log --oneline -5", None), 0)
        self.assertEqual(self.bash("review-security", f"git -C {self.ws} commit -m x"), 2)
        self.assertEqual(self.bash("review-security", f"git -C {self.ws} diff --output=/tmp/x"), 2)

    def test_reviewers_run_only_the_commands_project_md_lists_for_them(self):
        self.running("agent-a", role="review-ui")
        self.assertEqual(self.bash("review-ui", f"factory-cli ws run {self.job} suite"), 2)
        self.sb.write("project.md", self.paths.project.read_text().replace(
            "[reviewers]\n", "[reviewers]\ncommands = echo\n"), base=self.paths.factory)
        self.assertEqual(self.bash("review-ui", f"factory-cli ws run {self.job} echo src/components"), 0)
        self.assertEqual(self.bash("review-ui", f"factory-cli ws run {self.job} suite"), 2)

    def test_reviewers_write_only_their_own_review_file(self):
        self.running("agent-a", role="review-code")
        self.assertEqual(self.write("review-code", self.jd / "round-1" / "review-code.md"), 0)
        self.assertEqual(self.write("review-code", self.jd / "round-1" / "review-security.md"), 2)
        self.assertEqual(self.write("review-code", self.sb.ws / "src" / "app.py"), 2)

    def test_git_must_name_a_job_worktree(self):
        self.assertEqual(self.bash("builder", "git -C factory/workspace status"), 2)
        self.assertEqual(self.bash("builder", "git status"), 2)
        self.assertEqual(self.bash("builder", "git -C factory/workspaces status"), 2)
        self.assertEqual(self.bash("builder", f"git -C {self.ws}/src status"), 2)

    def test_shell_operators_and_other_programs_are_blocked(self):
        self.running("agent-a")
        self.assertEqual(self.bash("builder", f"cd {self.ws} && npm test"), 2)
        self.assertEqual(self.bash("builder", f"git -C {self.ws} status; rm -rf /"), 2)
        self.assertEqual(self.bash("builder", "npm test"), 2)

    def test_no_agent_pushes_switches_or_rebases(self):
        self.running("agent-a")
        for command in ("push origin HEAD", "switch main", "checkout main", "rebase origin/main", "reset --hard"):
            self.assertEqual(self.bash("builder", f"git -C {self.ws} {command}"), 2, command)

    def test_a_running_builder_commits_writes_and_runs_commands_in_its_job(self):
        self.running("agent-a")
        self.assertEqual(self.bash("builder", f"git -C {self.ws} add src/app.py"), 0)
        self.assertEqual(self.bash("builder", f"git -C {self.ws} commit -m 'Answer 42'"), 0)
        self.assertEqual(self.bash("builder", f"factory-cli ws run {self.job} test tests/x.py"), 0)
        self.assertEqual(self.write("builder", self.sb.ws / "src" / "app.py"), 0)
        self.assertEqual(self.write("builder", self.jd / "tests.md"), 0)
        self.assertEqual(self.write("builder", self.jd / "build.md"), 0)
        self.assertEqual(self.write("builder", self.jd / "spec.md"), 2)
        self.assertEqual(self.write("builder", self.jd / "baseline.json"), 2)
        self.assertEqual(self.bash("builder", f"git -C {self.ws} commit --amend -m x"), 2)

    def test_the_tidy_pass_works_in_the_workspace_and_writes_only_tidy_md_in_the_job_folder(self):
        self.running("agent-a", role="tidy")
        self.assertEqual(self.bash("tidy", f"git -C {self.ws} rm src/app.py"), 0)
        self.assertEqual(self.bash("tidy", f"git -C {self.ws} commit -m 'Remove orphan'"), 0)
        self.assertEqual(self.bash("tidy", f"factory-cli ws run {self.job} test tests/x.py"), 0)
        self.assertEqual(self.write("tidy", self.sb.ws / "src" / "app.py"), 0)
        self.assertEqual(self.write("tidy", self.jd / "round-1" / "tidy.md"), 0)
        for path in ("tests.md", "build.md", "round-2/tidy.md", "round-1/review-code.md"):
            self.assertEqual(self.write("tidy", self.jd / path), 2, path)

    def test_a_commit_with_ai_attribution_is_refused(self):
        self.running("agent-a")
        self.assertEqual(self.bash("builder", f"git -C {self.ws} commit -m 'Add x' -m 'Co-Authored-By: Claude <c@a>'"), 2)
        self.assertEqual(self.bash("builder", f"git -C {self.ws} commit '--message=Generated with Claude Code'"), 2)
        message = self.sb.write("msg.txt", "Add x\n\nCo-authored-by: Claude Opus <noreply@anthropic.com>\n", base=self.sb.ws)
        self.assertEqual(self.bash("builder", f"git -C {self.ws} commit -F {message}"), 2)
        self.assertEqual(self.bash("builder", f"git -C {self.ws} commit -m 'Add x' -m 'Why it was needed'"), 0)

    def test_the_first_write_to_another_job_is_refused(self):
        self.running("agent-a")
        self.running("agent-b", job=self.other)
        self.assertEqual(self.write("builder", self.sb.ws_of(self.other) / "src" / "app.py", "agent-a"), 2)
        self.assertEqual(self.bash("builder", f"git -C factory/workspaces/{self.other} add x", "agent-a"), 2)
        self.assertEqual(self.bash("builder", f"factory-cli ws run {self.other} suite", "agent-a"), 2)
        self.assertEqual(self.write("builder", self.paths.job(self.other) / "build.md", "agent-a"), 2)
        self.assertEqual(self.write("builder", self.sb.ws_of(self.other) / "src" / "app.py", "agent-b"), 0)

    def test_reads_need_no_run(self):
        self.assertEqual(self.bash("builder", f"git -C factory/workspaces/{self.other} log -1", None), 0)
        self.assertEqual(self.call("review-ui", "Read", file_path="/etc/hosts"), 0)

    def test_without_an_agent_id_nothing_is_written(self):
        self.running("agent-a")
        self.assertEqual(self.write("builder", self.sb.ws / "a.py", None), 2)

    def test_an_agent_without_a_run_writes_nothing_and_the_binding_failure_is_recorded(self):
        self.assertEqual(self.write("builder", self.sb.ws / "a.py", "agent-unknown"), 2)
        self.assertIn("agent-unknown", runs.binding_failure(self.paths))

    def test_an_aborted_or_finished_run_keeps_no_write_access(self):
        run = self.running("agent-a")
        runs.end(self.paths, run["run"], status="aborted", why="cancelled")
        self.assertEqual(self.write("builder", self.sb.ws / "a.py"), 2)
        done = self.running("agent-c", job=self.other)
        runs.end(self.paths, agent_id="agent-c")
        self.assertEqual(self.write("builder", self.sb.ws_of(self.other) / "a.py", "agent-c"), 2)
        self.assertEqual(runs.for_agent(self.paths, "agent-c")["run"], done["run"])

    def test_a_silent_run_expires_and_loses_write_access(self):
        run = self.running("agent-a")
        jobs.write_json(self.paths.factory / "runs" / f"{run['run']}.json", {**run, "seen_at": "2020-01-01T00:00:00+00:00"})
        self.assertEqual(self.write("builder", self.sb.ws / "a.py"), 2)

    def test_a_run_of_another_role_does_not_count(self):
        self.running("agent-a", role="review-code")
        self.assertEqual(self.write("builder", self.sb.ws / "a.py"), 2)

    def test_no_job_edits_the_factory_itself(self):
        self.running("agent-a")
        self.assertEqual(self.write("builder", self.paths.plugin / "skills/factory/bin/fx/guard.py"), 2)

    def test_agents_do_not_spawn_agents(self):
        self.assertEqual(self.call("builder", "Agent", prompt="x"), 2)


if __name__ == "__main__":
    unittest.main()
