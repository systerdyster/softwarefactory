"""One job end to end through the CLI, with the agents' work done by hand: the same sequence
the orchestrator follows, asking `state` before every step and doing only what it names."""

import contextlib
import io
import json
import os
import unittest

from support import SandboxTest, git

import factory


class FlowTest(SandboxTest):
    def setUp(self):
        super().setUp()
        os.environ["FACTORY_ROOT"] = str(self.paths.root)
        self.sb.create_workspace()
        self.job = self.sb.queue_job()
        self.jd = self.paths.job(self.job)

    def tearDown(self):
        os.environ.pop("FACTORY_ROOT", None)
        super().tearDown()

    def cli(self, *args, expect=0):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = factory.main(list(args))
        self.assertEqual(code, expect, out.getvalue())
        text = out.getvalue()
        return text if args[0] == "brief" else json.loads(text)

    def next_action(self):
        return self.cli("state", self.job)["next"]

    def expect(self, action):
        nxt = self.next_action()
        self.assertEqual(nxt["action"], action, nxt)
        return nxt

    def review_all(self, n, verdicts):
        nxt = self.expect("spawn-reviewers")
        for name in nxt["reviewers"]:
            self.assertIn(f"review-{name}.md", self.cli("brief", self.job, "reviewer", "--reviewer", name))
            verdict = verdicts.get(name, "PASS")
            self.sb.write(f"round-{n}/review-{name}.md", f"VERDICT: {verdict}\n\n- MAJOR src/app.py:2 — off by one\n"
                          if verdict == "CHANGES" else f"VERDICT: PASS\n", base=self.jd)
        return nxt["reviewers"]

    def builder_round(self, n, value):
        brief = self.cli("brief", self.job, "builder")
        self.assertIn(f"## Round {n}", brief)
        self.sb.write("src/app.py", f"def answer():\n    return {value}\n", base=self.sb.ws)
        self.sb.commit(f"Round {n}", cwd=self.sb.ws)
        log = (self.jd / "build.md").read_text() if (self.jd / "build.md").exists() else "# Build log\n"
        self.sb.write("build.md", log + f"\n## Round {n}\nSet answer to {value}.\nStatus: done\n", base=self.jd)
        self.expect("verify")
        self.cli("verify", self.job)
        self.expect("schedule")
        return self.cli("schedule", self.job)

    def test_one_job_from_queue_to_merged(self):
        taken = self.cli("next")
        self.assertEqual(taken["job"], self.job)
        self.expect("start")
        self.cli("start", self.job)

        self.expect("spawn-test-writer")
        self.assertIn("Mode: write", self.cli("brief", self.job, "test-writer"))
        self.sb.write("tests/test_answer.py", "from src.app import answer\nassert answer() == 42\n", base=self.sb.ws)
        self.sb.commit("Add failing tests for the answer", cwd=self.sb.ws)
        self.sb.write("tests.md", "# Tests\n\n| File | Hash |\n|---|---|\n| `tests/test_answer.py` | |\n\n"
                      "## Exemptions\n\nNone.\n\nStatus: red-confirmed\n", base=self.jd)
        self.expect("tests-record")
        self.cli("tests", "record", self.job)

        self.expect("spawn-builder")
        first = self.builder_round(1, 43)
        self.assertEqual(first["reviewers"], ["security", "code", "performance", "stability"])
        self.review_all(1, {"code": "CHANGES"})

        nxt = self.expect("spawn-builder")
        self.assertEqual(nxt["round"], 2)
        self.assertIn("review-code.md", self.cli("brief", self.job, "builder"))
        self.sb.write("src/app.py", "def answer():\n    return 42\n", base=self.sb.ws)
        self.sb.commit("Fix the answer", cwd=self.sb.ws)
        self.sb.write("build.md", (self.jd / "build.md").read_text() + "\n## Round 2\nFixed.\nStatus: done\n", base=self.jd)
        self.expect("verify")
        self.cli("verify", self.job)
        self.expect("schedule")
        second = self.cli("schedule", self.job)
        self.assertEqual(second["reviewers"], ["security", "code"])
        self.review_all(2, {})

        self.expect("spawn-approver")
        brief = self.cli("brief", self.job, "approver")
        head = self.cli("state", self.job)["head"]
        self.assertIn(f"Head: {head}", brief)
        self.sb.write("decision.md", f"APPROVE\nHead: {head}\n\n## Least sure\n\nNothing named.\n", base=self.jd)

        self.sb.push_target_change("README.md", "Someone else's change\n")
        self.expect("rebase")
        self.assertEqual(self.cli("rebase", self.job)["result"], "green")
        self.expect("pr")
        self.assertEqual(self.cli("pr", "open", self.job)["state"], "MANUAL")
        s = self.cli("state", self.job)
        self.assertEqual((s["stage"], s["waiting"]), ("pr-open", True))

        branch_head = git(self.sb.root, "rev-parse", "factory/001-answer")
        git(self.sb.root, "push", "origin", f"{branch_head}:refs/heads/main")
        self.assertEqual(self.cli("pr", "status", self.job)["state"], "MERGED")
        self.expect("finish")
        self.cli("finish", self.job)
        final = self.cli("state", self.job)
        self.assertEqual(final["stage"], "done")
        self.assertEqual(git(self.sb.root, "branch", "--list", "factory/*"), "")
        self.assertIsNone(self.cli("workspace", "check")["branch"])
        self.assertEqual(self.cli("board")["jobs"], 1)

    def test_a_builder_that_edits_a_test_is_caught_and_reverted(self):
        self.cli("next")
        self.cli("start", self.job)
        self.sb.write("tests/test_answer.py", "assert 42 == 42\n", base=self.sb.ws)
        self.sb.commit("Tests", cwd=self.sb.ws)
        self.sb.write("tests.md", "| File | Hash |\n|---|---|\n| `tests/test_answer.py` | |\n\nStatus: red-confirmed\n",
                      base=self.jd)
        self.cli("tests", "record", self.job)
        self.sb.write("tests/test_answer.py", "assert True\n", base=self.sb.ws)
        self.sb.commit("Make it pass the easy way", cwd=self.sb.ws)
        self.sb.write("build.md", "## Round 1\nStatus: done\n", base=self.jd)
        self.cli("tests", "check", self.job, expect=1)
        self.expect("tests-restore")
        self.cli("tests", "restore", self.job)
        self.assertEqual((self.sb.ws / "tests/test_answer.py").read_text(), "assert 42 == 42\n")
        self.expect("verify")

    def test_brief_refuses_an_agent_state_did_not_ask_for(self):
        self.cli("next")
        self.cli("brief", self.job, "approver", expect=1)


if __name__ == "__main__":
    unittest.main()
