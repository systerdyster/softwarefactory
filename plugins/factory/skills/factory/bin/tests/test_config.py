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
        self.assertEqual(list(cfg.mages), ["code", "security", "performance", "stability"])
        self.assertEqual(cfg.mages["security"], {"kind": "default", "name": "security"})
        self.assertEqual((cfg.reviewer_commands, cfg.docs), ([], []))

    def test_without_mages_the_optional_reviewers_keep_a_fixed_order_and_codex_is_off_unless_listed(self):
        cfg = parse("[reviewers]\nenabled = codex, ui")
        self.assertEqual(list(cfg.mages), ["code", "security", "performance", "stability", "ui", "codex"])
        self.assertEqual(cfg.mages["codex"], {"kind": "codex"})

    def test_mages_name_their_rulebook_and_the_project_picks_them(self):
        cfg = parse("[mages]\ncode = skill:code-review\na11y = default:ux\ncodex = codex")
        self.assertEqual(cfg.mages, {"code": {"kind": "skill", "name": "code-review"},
                                     "a11y": {"kind": "default", "name": "ux"},
                                     "codex": {"kind": "codex"}})

    def test_mages_need_the_main_one_and_a_rulebook_each(self):
        for body in ("[mages]\nsecurity = default:security", "[mages]\ncode = code-review",
                     "[mages]\ncode = skill:", "[mages]\nCode Review = skill:code-review",
                     "[mages]\ncode = default:code\nsecond = codex", "[mages]\ncode = default:code\ncodex = default:code"):
            with self.assertRaises(FactoryError, msg=body):
                parse(body).mages

    def test_verify_defaults_to_lint_build_and_suite_where_defined(self):
        cfg = parse("[commands]\nsuite = make test\nlint = make lint\ntest = make test {target}")
        self.assertEqual(cfg.verify_commands, ["lint", "suite"])

    def test_verify_can_name_one_command_that_covers_the_rest(self):
        cfg = parse("[factory]\nverify = check\n\n[commands]\ncheck = pnpm check\nsuite = pnpm test\nlint = pnpm lint")
        self.assertEqual(cfg.verify_commands, ["check"])

    def test_verify_naming_an_undefined_command_is_refused(self):
        with self.assertRaises(FactoryError):
            parse("[factory]\nverify = suite, lint\n\n[commands]\nsuite = make test").verify_commands

    def test_parallel_is_any_number_of_jobs_from_one_and_defaults_to_two(self):
        self.assertEqual(parse("[factory]\n").parallel, 2)
        self.assertEqual(parse("[factory]\nparallel = 4").parallel, 4)
        with self.assertRaises(FactoryError):
            parse("[factory]\nparallel = 0").parallel

    def test_docs_and_reviewer_commands(self):
        cfg = parse("[rules]\ndocs = Wiki/*.md, docs/adr/**\n\n[reviewers]\ncommands = test-components")
        self.assertEqual((cfg.docs, cfg.reviewer_commands), (["Wiki/*.md", "docs/adr/**"], ["test-components"]))

    def test_the_wiki_settings_are_read_and_empty_means_no_wiki_agent(self):
        cfg = parse("[rules]\nwiki = Wiki/*.md, docs/**/*.md\nwiki_external = Project.wiki\nwiki_guide = Wiki/Rules.md")
        self.assertEqual((cfg.wiki_globs, cfg.wiki_external, cfg.wiki_guide),
                         (["Wiki/*.md", "docs/**/*.md"], ["Project.wiki"], "Wiki/Rules.md"))
        self.assertEqual((parse("[rules]\n").wiki_globs, parse("[rules]\n").wiki_guide), ([], None))

    def test_the_test_note_follows_the_pr_language_unless_it_has_its_own(self):
        self.assertEqual(parse("[handoff]\nlanguage = sv").test_note_language, "sv")
        self.assertEqual(parse("[handoff]\nlanguage = en\ntest_note_language = sv").test_note_language, "sv")

    def test_publish_choice_depends_on_the_pr_tool_and_merge_mode(self):
        from fx import story
        for body, expected in (("[factory]\npr_tool = az", "both"), ("[factory]\npr_tool = gh\nmerge_mode = auto-merge", "auto"),
                               ("[factory]\npr_tool = other", "approve"),
                               ("[factory]\npr_tool = az\nmerge_mode = local-merge", "merge")):
            self.assertEqual(story.publish_choice(parse(body)), expected, body)

    def test_unknown_reviewer_is_refused(self):
        with self.assertRaises(FactoryError):
            parse("[reviewers]\nenabled = vibes").mages

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
