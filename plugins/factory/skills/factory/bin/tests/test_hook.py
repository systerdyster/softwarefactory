import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from support import SandboxTest

import hook
from fx import actions, paths as paths_mod
from fx.errors import FactoryError


def event(agent_type=None, **tool):
    data = {"tool_name": "Bash", "tool_input": tool or {"command": "git -C factory/workspaces/001-answer commit -m x"}}
    if agent_type is not None:
        data["agent_type"] = agent_type
    return io.StringIO(json.dumps(data))


class RoleTest(unittest.TestCase):
    def test_factory_agents_map_to_their_roles(self):
        self.assertEqual(hook.role_for("factory:factory-builder"), "builder")
        self.assertEqual(hook.role_for("factory:factory-codex-fallback"), "review-codex")
        self.assertEqual(hook.role_for("factory:factory-review-security"), "review-security")

    def test_anything_else_is_not_the_factory(self):
        self.assertIsNone(hook.role_for(None))
        self.assertIsNone(hook.role_for(""))
        self.assertIsNone(hook.role_for("Explore"))
        self.assertIsNone(hook.role_for("other-plugin:factory-builder"))

    def test_an_unknown_factory_agent_fails_closed(self):
        self.assertEqual(hook.role_for("factory:factory-renamed"), "unknown")
        self.assertEqual(hook.role_for("factory:factory-test-writer"), "unknown")
        self.assertEqual(hook.main(stream=event("factory:factory-renamed")), 2)


class HookTest(SandboxTest):
    def setUp(self):
        super().setUp()
        self.sb.open_job(self.sb.queue_job())
        actions.log_spawn(self.paths, "001-answer", "review-security", 1, None, None)

    def run_hook(self, agent_type, **tool):
        with mock.patch.dict(os.environ, {"FACTORY_ROOT": str(self.sb.root)}):
            return hook.main(stream=event(agent_type, **tool))

    def test_main_thread_and_other_agents_pass_untouched(self):
        self.assertEqual(self.run_hook(None, command="rm -rf /tmp/x; echo hi"), 0)
        self.assertEqual(self.run_hook("Explore", command="git push"), 0)

    def test_unreadable_input_never_blocks_other_sessions(self):
        self.assertEqual(hook.main(stream=io.StringIO("not json")), 0)

    def test_a_factory_reviewer_is_held_to_its_role(self):
        self.assertEqual(self.run_hook("factory:factory-review-security"), 2)
        self.assertEqual(self.run_hook("factory:factory-review-security",
                                       command="git -C factory/workspaces/001-answer diff HEAD~1"), 0)


class RootTest(unittest.TestCase):
    def setUp(self):
        self.env = {k: v for k, v in os.environ.items() if k not in ("FACTORY_ROOT", "CLAUDE_PROJECT_DIR")}

    def test_project_dir_wins_over_the_working_directory(self):
        with mock.patch.dict(os.environ, {**self.env, "CLAUDE_PROJECT_DIR": "/tmp/project"}, clear=True):
            self.assertEqual(paths_mod._derive_root(), Path("/tmp/project").resolve())

    def test_the_workspace_maps_back_to_the_main_checkout(self):
        with mock.patch.dict(os.environ, {**self.env, "CLAUDE_PROJECT_DIR": "/tmp/project/factory/workspaces/001-a"},
                             clear=True):
            self.assertEqual(paths_mod._derive_root(), Path("/tmp/project").resolve())

    def test_outside_a_repository_it_refuses(self):
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch.object(paths_mod, "_git_toplevel", return_value=None):
            with self.assertRaises(FactoryError):
                paths_mod._derive_root()

    def test_otherwise_the_git_repository_of_the_working_directory(self):
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch.object(paths_mod, "_git_toplevel", return_value=Path("/tmp/repo/factory/workspaces/001-a")):
            self.assertEqual(paths_mod._derive_root(), Path("/tmp/repo").resolve())


if __name__ == "__main__":
    unittest.main()
