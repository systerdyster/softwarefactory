import unittest

from support import PROJECT  # noqa: F401

from fx import config
from fx.errors import FactoryError


def parse(body):
    return config.parse_text(f"# P\n\n```ini\n{body}\n```\n")


class ConfigTest(unittest.TestCase):
    def test_defaults_when_keys_are_absent(self):
        cfg = parse("[factory]\n")
        self.assertEqual(cfg.target_branch, "main")
        self.assertEqual(cfg.merge_mode, "pr")
        self.assertEqual(cfg.max_rounds, 3)
        self.assertEqual(cfg.base_ref, "origin/main")
        self.assertEqual(cfg.enabled_reviewers, ["code", "security", "performance", "stability"])
        self.assertEqual((cfg.reviewer_commands, cfg.docs), ([], []))

    def test_optional_reviewers_keep_a_fixed_order_and_codex_is_off_unless_listed(self):
        cfg = parse("[reviewers]\nenabled = codex, ui")
        self.assertEqual(cfg.enabled_reviewers, ["code", "security", "performance", "stability", "ui", "codex"])

    def test_verify_defaults_to_lint_build_and_suite_where_defined(self):
        cfg = parse("[commands]\nsuite = make test\nlint = make lint\ntest = make test {target}")
        self.assertEqual(cfg.verify_commands, ["lint", "suite"])

    def test_verify_can_name_one_command_that_covers_the_rest(self):
        cfg = parse("[factory]\nverify = check\n\n[commands]\ncheck = pnpm check\nsuite = pnpm test\nlint = pnpm lint")
        self.assertEqual(cfg.verify_commands, ["check"])

    def test_verify_naming_an_undefined_command_is_refused(self):
        with self.assertRaises(FactoryError):
            parse("[factory]\nverify = suite, lint\n\n[commands]\nsuite = make test").verify_commands

    def test_docs_and_reviewer_commands(self):
        cfg = parse("[rules]\ndocs = Wiki/*.md, docs/adr/**\n\n[reviewers]\ncommands = test-components")
        self.assertEqual((cfg.docs, cfg.reviewer_commands), (["Wiki/*.md", "docs/adr/**"], ["test-components"]))

    def test_unknown_reviewer_is_refused(self):
        with self.assertRaises(FactoryError):
            parse("[reviewers]\nenabled = vibes").enabled_reviewers

    def test_unknown_merge_mode_is_refused(self):
        with self.assertRaises(FactoryError):
            parse("[factory]\nmerge_mode = yolo").merge_mode

    def test_multi_line_setup_is_one_command_per_line(self):
        cfg = parse("[workspace]\nsetup =\n    npm ci\n    cp \"$FACTORY_REPO_ROOT/.env\" .env")
        self.assertEqual(cfg.setup_commands, ["npm ci", 'cp "$FACTORY_REPO_ROOT/.env" .env'])

    def test_remote_none_makes_the_base_the_local_branch(self):
        self.assertEqual(parse("[factory]\nremote = none\ntarget_branch = develop").base_ref, "develop")

    def test_missing_block_is_an_error(self):
        with self.assertRaises(FactoryError):
            config.parse_text("# no config here\n")


if __name__ == "__main__":
    unittest.main()
