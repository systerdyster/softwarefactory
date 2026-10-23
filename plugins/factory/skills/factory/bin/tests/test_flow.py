"""One job end to end through the CLI, with the agents' work done by hand: the same sequence
the orchestrator follows, asking `state` before every step and doing only what it names."""

import contextlib
import io
import json
import os
import unittest

from support import SandboxTest, agent_starts, agent_stops, git

import factory
from fx import jobs

TEST = "import sys\nsys.path.insert(0, 'src')\nfrom app import answer\nassert answer() == 42\n"


class FlowTest(SandboxTest):
    def setUp(self):
        super().setUp()
        os.environ["FACTORY_ROOT"] = str(self.paths.root)
        self.job = self.sb.queue_job()
        self.jd = self.paths.job(self.job)
        self.lease = self.cli("lease", "acquire")["lease"]

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

    def brief(self, *args):
        """A brief, and the agent it starts doing its work at once: attached and stopped through the hooks."""
        text = self.cli("brief", self.job, *args)
        agent_type = f"factory:factory-{args[0]}" if args[0] in ("builder", "tidy") else f"factory:factory-review-{args[-1]}"
        self.agents = getattr(self, "agents", 0) + 1
        agent_id = f"agent-{self.agents}"
        self.assertEqual(agent_starts(self.paths.root, text, agent_id, agent_type), 0)
        self.assertEqual(agent_stops(self.paths.root, agent_id, agent_type), 0)
        return text

    def expect(self, action):
        nxt = self.cli("state", self.job)["next"]
        self.assertEqual(nxt["action"], action, nxt)
        return nxt

    def log(self, section):
        old = (self.jd / "build.md").read_text() if (self.jd / "build.md").exists() else "# Build log\n"
        self.sb.write("build.md", old + f"\n{section}\nStatus: done\n", base=self.jd)

    def tidy(self, files=None, removed=""):
        """The tidy pass: its brief, its commit when it changed something, and tidy.md."""
        self.expect("spawn-tidy")
        text = self.brief("tidy")
        for rel, content in (files or {}).items():
            self.sb.write(rel, content, base=self.sb.ws)
        if files:
            self.sb.commit("Tidy", cwd=self.sb.ws)
        self.sb.write("round-1/tidy.md", f"# Tidy\n\nStatus: done\n\n## Removed\n{removed}\n", base=self.jd)
        return text

    def review(self, n, name, findings=""):
        self.assertIn(f"review-{name}.md", self.brief("reviewer", "--reviewer", name))
        self.sb.write(f"round-{n}/review-{name}.md", f"VERDICT: PASS\n\n## Findings\n{findings}\n", base=self.jd)

    def test_one_job_from_queue_to_merged(self):
        self.assertEqual(self.cli("next", "--lease", self.lease)["jobs"], [self.job])
        self.expect("start")
        self.cli("start", self.job)

        self.expect("spawn-builder")
        self.assertIn("Map each acceptance criterion", self.brief("builder"))
        self.sb.write("src/app.py", "def answer():\n    # return the answer\n    return 43\n", base=self.sb.ws)
        self.sb.write("tests/test_answer.py", TEST, base=self.sb.ws)
        self.sb.write("LINT_ERROR", "", base=self.sb.ws)
        self.sb.commit("Answer, with its test", cwd=self.sb.ws)
        self.sb.write("tests.md", "## Coverage\n- AC1 the answer is 42 — `tests/test_answer.py`\n", base=self.jd)
        self.log("## Round 1")

        self.expect("verify")
        self.cli("verify", self.job, expect=1)
        nxt = self.expect("spawn-builder")
        self.assertEqual(nxt["attempt"], 2)
        self.assertIn("lint was red", self.brief("builder"))
        (self.sb.ws / "LINT_ERROR").unlink()
        self.sb.commit("Fix lint", cwd=self.sb.ws)
        self.log("## Round 1 (attempt 2)")
        self.expect("verify")
        self.assertEqual([s["name"] for s in self.cli("verify", self.job)["steps"]], ["lint", "suite"])
        self.assertIn("src/app.py", self.tidy({"src/app.py": "def answer():\n    return 43\n"},
                                              "- `src/app.py:2` a comment that repeats the code\n"))
        self.assertEqual(self.expect("verify")["attempt"], "tidy")
        self.cli("verify", self.job)
        self.expect("schedule")
        self.assertEqual(self.cli("schedule", self.job)["reviewers"], ["code"])
        self.expect("spawn-reviewers")
        self.assertIn("tidy.md", self.brief("reviewer", "--reviewer", "code"))
        self.sb.write("round-1/review-code.md", "VERDICT: CHANGES\n\n## Findings\n"
                      "1. BLOCKING `src/app.py:2` — returns 43; AC1 asks for 42.\n"
                      "2. NOTE `src/app.py:1` — a docstring would not hurt.\n", base=self.jd)

        nxt = self.expect("spawn-builder")
        self.assertEqual(nxt["round"], 2)
        brief = self.brief("builder")
        self.assertIn("1 blocking item(s)", brief)
        self.assertIn("returns 43", (self.jd / "round-1" / "findings.md").read_text())
        self.sb.write("src/app.py", "def answer():\n    return 42\n", base=self.sb.ws)
        self.sb.commit("Fix the answer", cwd=self.sb.ws)
        self.log("## Round 2")
        self.expect("verify")
        self.cli("verify", self.job)
        self.expect("schedule")
        self.assertEqual(self.cli("schedule", self.job)["reviewers"], ["code"])
        self.assertIn("This round checks the fixes", self.brief("reviewer", "--reviewer", "code"))
        self.review(2, "code")

        s = self.cli("state", self.job)
        self.assertEqual((s["stage"], s["next"]["action"]), ("needs-human", "needs-human"))
        self.assertIn("ready for your approval", s["reason"])
        self.assertIn("max_diff 400", s["reason"])
        card = next(c for c in self.cli("board", "--print")["jobs"] if c["id"] == self.job)
        self.assertEqual(card["decision"]["kind"], "approve")
        self.assertNotIn("returns 43", str(card["decision"]["findings"]))
        self.cli("approve", self.job, "--by", "Fredrik")

        self.sb.push_target_change("README.md", "Someone else's change\n")
        self.expect("rebase")
        self.assertEqual(self.cli("rebase", self.job)["result"], "green")
        self.expect("pr")
        facts = self.cli("pr", "body", self.job)
        self.assertNotIn("Approved by", facts["body"])
        self.assertIn("Approved by Fredrik", facts["details"])
        self.assertIn("a comment that repeats the code", facts["details"])
        self.assertIn("Subject imperative", facts["commits"])
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
        self.assertEqual(self.cli("workspace", "list")["workspaces"], [])
        self.assertEqual(self.cli("board")["jobs"], 1)

    def test_an_unreadable_review_goes_back_to_its_reviewer(self):
        self.cli("next", "--lease", self.lease)
        self.cli("start", self.job)
        self.sb.write("src/app.py", "def answer():\n    return 42\n", base=self.sb.ws)
        self.sb.commit("Answer", cwd=self.sb.ws)
        self.log("## Round 1")
        self.cli("verify", self.job)
        self.tidy()
        self.cli("schedule", self.job)
        self.brief("reviewer", "--reviewer", "code")
        self.sb.write("round-1/review-code.md", "VERDICT: CHANGES\n\n## Findings\n### The total can drift (should fix)\n",
                      base=self.jd)
        s = self.cli("state", self.job)
        self.assertEqual((s["next"]["action"], s["next"]["reviewers"]), ("spawn-reviewers", ["code"]))
        self.assertIn("rejected", self.brief("reviewer", "--reviewer", "code"))

    def test_a_tidy_pass_that_turns_the_checks_red_is_discarded(self):
        self.cli("next", "--lease", self.lease)
        self.cli("start", self.job)
        self.sb.write("src/app.py", "def answer():\n    return 42\n", base=self.sb.ws)
        built = self.sb.commit("Answer", cwd=self.sb.ws)
        self.log("## Round 1")
        self.cli("verify", self.job)
        self.tidy({"BROKEN": ""}, "- `check.py` looked unused\n")
        self.cli("verify", self.job, expect=1)
        self.expect("tidy-discard")
        self.assertEqual(self.cli("tidy-discard", self.job)["head"], built)
        self.assertEqual(git(self.sb.ws, "rev-parse", "HEAD"), built)
        self.assertFalse((self.sb.ws / "BROKEN").exists())
        self.assertIn("Discarded: suite is red after the tidy pass", (self.jd / "round-1" / "tidy.md").read_text())
        self.expect("schedule")
        self.cli("schedule", self.job)
        self.assertNotIn("tidy.md", self.brief("reviewer", "--reviewer", "code"))

    def test_a_tidy_pass_that_changes_nothing_goes_straight_to_review(self):
        self.cli("next", "--lease", self.lease)
        self.cli("start", self.job)
        self.sb.write("src/app.py", "def answer():\n    return 42\n", base=self.sb.ws)
        self.sb.commit("Answer", cwd=self.sb.ws)
        self.log("## Round 1")
        self.cli("verify", self.job)
        self.tidy()
        self.expect("schedule")

    def test_an_objection_is_answered_by_the_developer_and_the_builder_continues(self):
        self.cli("next", "--lease", self.lease)
        self.cli("start", self.job)
        self.brief("builder")
        self.sb.write("build.md", "# Build log\n\n## Round 1\nThe plan can't hold.\nObjection: plan\nStatus: objection\n",
                      base=self.jd)
        self.expect("judge-objection")
        self.sb.write("round-1/ruling-1.md", "RULING: NEEDS-HUMAN\n\nQuestion: Keep two boundaries or add a third?\n\n"
                      "The category page reads `nodeId` above both.\n\n## Options\n"
                      "- (a) Add a third outer boundary — breaks \"two boundaries\" [recommended]\n"
                      "- (b) Read nodeId lazily — touches the search provider\n", base=self.jd)
        s = self.cli("state", self.job)
        self.assertTrue(s["next"]["question"])
        self.cli("approve", self.job, "--by", "Fredrik", expect=1)
        card = next(c for c in self.cli("board", "--print")["jobs"] if c["id"] == self.job)
        self.assertEqual(card["decision"]["question"], "Keep two boundaries or add a third?")
        self.assertEqual([(o["key"], o["recommended"]) for o in card["decision"]["options"]], [("a", True), ("b", False)])
        self.assertIn("objection → waits for your decision", card["story"][0])

        self.cli("answer", self.job, "--text", "(a) Add a third outer boundary", "--by", "Fredrik")
        self.assertIn("Keep two boundaries or add a third? — (a) Add a third outer boundary",
                      jobs.section((self.jd / "spec.md").read_text(), "Decided"))
        nxt = self.expect("spawn-builder")
        self.assertEqual(nxt["attempt"], 2)
        self.assertIn("The developer answered your objection", self.brief("builder"))
        card = next(c for c in self.cli("board", "--print")["jobs"] if c["id"] == self.job)
        self.assertIn("you decided: (a) Add a third outer boundary", card["story"][0])

    def test_every_brief_lists_the_projects_written_rules(self):
        self.sb.push_target_change("Wiki/Components.md", "# Components\n\nA modifier only reassigns private variables.\n")
        self.sb.write("project.md", self.paths.project.read_text().replace(
            "[reviewers]\n", "[rules]\ndocs = Wiki/*.md\n\n[reviewers]\n"), base=self.paths.factory)
        self.cli("next", "--lease", self.lease)
        self.cli("start", self.job)
        self.assertIn(str(self.sb.ws / "Wiki/Components.md"), self.brief("builder"))

    def test_every_brief_carries_the_comment_rule_the_project_sets_or_the_default(self):
        self.cli("next", "--lease", self.lease)
        self.cli("start", self.job)
        brief = self.cli("brief", self.job, "builder")
        self.assertIn("Code comments, unless a project rule above says otherwise: Write none by default", brief)
        self.assertIn("unless a project rule above says otherwise: Frame it plainly first", brief)

    def test_brief_refuses_an_agent_state_did_not_ask_for(self):
        self.cli("next", "--lease", self.lease)
        self.cli("brief", self.job, "reviewer", "--reviewer", "code", expect=1)


if __name__ == "__main__":
    unittest.main()
