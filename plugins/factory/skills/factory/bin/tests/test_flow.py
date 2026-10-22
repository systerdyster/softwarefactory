"""One job end to end through the CLI, with the agents' work done by hand: the same sequence
the orchestrator follows, asking `state` before every step and doing only what it names."""

import contextlib
import io
import json
import os
import unittest

from support import SandboxTest, git

import factory

TEST = "import sys\nsys.path.insert(0, 'src')\nfrom app import answer\nassert answer() == 42\n"


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

    def expect(self, action):
        nxt = self.cli("state", self.job)["next"]
        self.assertEqual(nxt["action"], action, nxt)
        return nxt

    def log(self, section):
        old = (self.jd / "build.md").read_text() if (self.jd / "build.md").exists() else "# Build log\n"
        self.sb.write("build.md", old + f"\n{section}\nStatus: done\n", base=self.jd)

    def review(self, n, name, findings=""):
        self.assertIn(f"review-{name}.md", self.cli("brief", self.job, "reviewer", "--reviewer", name))
        self.sb.write(f"round-{n}/review-{name}.md", f"VERDICT: PASS\n\n## Findings\n{findings}\n", base=self.jd)

    def test_one_job_from_queue_to_merged(self):
        self.assertEqual(self.cli("next")["job"], self.job)
        self.expect("start")
        self.cli("start", self.job)

        self.expect("spawn-builder")
        self.assertIn("write the plan's tests first", self.cli("brief", self.job, "builder"))
        self.sb.write("tests/test_answer.py", TEST, base=self.sb.ws)
        self.sb.commit("Add failing tests for the answer", cwd=self.sb.ws)
        self.sb.write("src/app.py", "def answer():\n    return 43\n", base=self.sb.ws)
        self.sb.commit("Answer", cwd=self.sb.ws)
        self.sb.write("tests.md", "| File |\n|---|\n| `tests/test_answer.py` |\n", base=self.jd)
        self.log("## Round 1")

        self.expect("red-check")
        self.assertTrue(self.cli("red-check", self.job)["ok"])
        self.expect("verify")
        self.cli("verify", self.job)
        self.expect("schedule")
        self.assertEqual(self.cli("schedule", self.job)["reviewers"], ["code"])
        self.expect("spawn-reviewers")
        self.review(1, "code", "- 🔴 BLOCKING `src/app.py:2` — returns 43; AC1 asks for 42.\n"
                               "- 🟡 NOTE `src/app.py:1` — a docstring would not hurt.")

        nxt = self.expect("spawn-builder")
        self.assertEqual(nxt["round"], 2)
        brief = self.cli("brief", self.job, "builder")
        self.assertIn("1 blocking item(s)", brief)
        self.assertIn("returns 43", (self.jd / "round-1" / "findings.md").read_text())
        self.sb.write("src/app.py", "def answer():\n    return 42\n", base=self.sb.ws)
        self.sb.commit("Fix the answer", cwd=self.sb.ws)
        self.log("## Round 2")
        self.expect("verify")
        self.cli("verify", self.job)
        self.expect("schedule")
        self.assertEqual(self.cli("schedule", self.job)["reviewers"], ["code"])
        self.assertIn("This round checks the fixes", self.cli("brief", self.job, "reviewer", "--reviewer", "code"))
        self.review(2, "code")

        s = self.cli("state", self.job)
        self.assertEqual((s["stage"], s["next"]["action"]), ("needs-human", "needs-human"))
        self.assertIn("ready for your approval", s["reason"])
        self.assertIn("max_diff 400", s["reason"])
        self.cli("approve", self.job, "--by", "Fredrik")

        self.sb.push_target_change("README.md", "Someone else's change\n")
        self.expect("rebase")
        self.assertEqual(self.cli("rebase", self.job)["result"], "green")
        self.expect("pr")
        self.assertIn("Approved by Fredrik", self.cli("pr", "body", self.job)["body"])
        self.assertEqual(self.cli("pr", "open", self.job)["state"], "MANUAL")
        s = self.cli("state", self.job)
        self.assertEqual((s["stage"], s["waiting"]), ("pr-open", True))

        branch_head = git(self.sb.root, "rev-parse", "factory/001-answer")
        git(self.sb.root, "push", "origin", f"{branch_head}:refs/heads/main")
        self.assertEqual(self.cli("pr", "status", self.job)["state"], "MERGED")
        self.expect("finish")
        self.cli("finish", self.job)
        self.assertEqual(self.cli("state", self.job)["stage"], "done")
        self.assertEqual(git(self.sb.root, "branch", "--list", "factory/*"), "")
        self.assertIsNone(self.cli("workspace", "check")["branch"])
        self.assertEqual(self.cli("board")["jobs"], 1)

    def test_tests_that_pass_before_the_change_send_the_builder_back(self):
        self.cli("next")
        self.cli("start", self.job)
        self.sb.write("tests/test_answer.py", "assert True\n", base=self.sb.ws)
        self.sb.commit("Tests", cwd=self.sb.ws)
        self.sb.write("tests.md", "| File |\n|---|\n| `tests/test_answer.py` |\n", base=self.jd)
        self.log("## Round 1")
        self.expect("red-check")
        self.assertFalse(self.cli("red-check", self.job)["ok"])
        nxt = self.expect("spawn-builder")
        self.assertEqual(nxt["attempt"], 2)
        self.assertIn("prove nothing", self.cli("brief", self.job, "builder"))

    def test_every_brief_lists_the_projects_written_rules(self):
        self.sb.push_target_change("Wiki/Components.md", "# Components\n\nA modifier only reassigns private variables.\n")
        self.sb.write("project.md", self.paths.project.read_text().replace(
            "[reviewers]\n", "[rules]\ndocs = Wiki/*.md\n\n[reviewers]\n"), base=self.paths.factory)
        self.cli("next")
        self.cli("start", self.job)
        self.assertIn(str(self.sb.ws / "Wiki/Components.md"), self.cli("brief", self.job, "builder"))

    def test_brief_refuses_an_agent_state_did_not_ask_for(self):
        self.cli("next")
        self.cli("brief", self.job, "reviewer", "--reviewer", "code", expect=1)


if __name__ == "__main__":
    unittest.main()
