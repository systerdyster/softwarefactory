import json
import unittest

from support import SandboxTest, git

from fx import setup


class InitFilesTest(SandboxTest):
    def setUp(self):
        super().setUp()
        self.commit_gitignore("setup.log\n")

    def commit_gitignore(self, text):
        self.sb.write(".gitignore", text)
        git(self.sb.root, "commit", "-m", "Change ignores", "--", ".gitignore")

    def exclude(self):
        return (self.sb.root / ".git" / "info" / "exclude").read_text(encoding="utf-8")

    def test_ignores_factory_locally_without_touching_gitignore(self):
        setup.init_files(self.paths)
        self.assertIn("/factory/", self.exclude())
        self.assertEqual((self.sb.root / ".gitignore").read_text(encoding="utf-8"), "setup.log\n")
        self.assertEqual(git(self.sb.root, "status", "--porcelain"), "")

    def test_leaves_exclude_alone_when_factory_is_already_ignored(self):
        self.commit_gitignore("setup.log\n/factory/\n")
        before = self.exclude()
        setup.init_files(self.paths)
        self.assertEqual(self.exclude(), before)


class AllowlistTest(SandboxTest):
    def local(self):
        return json.loads((self.sb.root / ".claude" / "settings.local.json").read_text(encoding="utf-8"))

    def test_writes_rules_to_local_settings_only(self):
        result = setup.allowlist(self.paths, self.cfg, write=True)
        self.assertEqual(result["file"], ".claude/settings.local.json")
        self.assertEqual(self.local()["permissions"]["allow"], setup.proposed_rules())
        self.assertFalse((self.sb.root / ".claude" / "settings.json").exists())

    def test_counts_rules_in_shared_settings_as_present(self):
        shared = {"permissions": {"allow": ["Bash(factory-cli:*)"]}}
        self.sb.write(".claude/settings.json", json.dumps(shared))
        result = setup.allowlist(self.paths, self.cfg, write=True)
        self.assertNotIn("Bash(factory-cli:*)", result["add"])
        self.assertNotIn("Bash(factory-cli:*)", self.local()["permissions"]["allow"])

    def test_keeps_existing_local_settings(self):
        self.sb.write(".claude/settings.local.json", json.dumps({"permissions": {"allow": ["Bash(ls:*)"]}, "x": 1}))
        setup.allowlist(self.paths, self.cfg, write=True)
        local = self.local()
        self.assertEqual(local["x"], 1)
        self.assertEqual(local["permissions"]["allow"].count("Bash(ls:*)"), 1)


if __name__ == "__main__":
    unittest.main()
