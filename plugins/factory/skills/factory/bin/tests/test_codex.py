import unittest
from unittest import mock

from support import SandboxTest

from fx import codex
from fx.errors import Fallback


class CodexTest(SandboxTest):
    def test_missing_cli_is_a_fallback_not_a_failure(self):
        with mock.patch("fx.codex.shutil.which", return_value=None):
            with self.assertRaises(Fallback):
                codex.available()

    def test_logged_out_cli_is_a_fallback(self):
        logged_out = mock.Mock(returncode=1, stdout="", stderr="not logged in")
        with mock.patch("fx.codex.shutil.which", return_value="/bin/codex"), \
                mock.patch("fx.codex._run", return_value=logged_out):
            with self.assertRaises(Fallback):
                codex.available()

    def test_changed_flags_are_a_fallback(self):
        responses = [mock.Mock(returncode=0, stdout=""), mock.Mock(returncode=0, stdout="--sandbox --cd")]
        with mock.patch("fx.codex.shutil.which", return_value="/bin/codex"), \
                mock.patch("fx.codex._run", side_effect=responses):
            with self.assertRaises(Fallback):
                codex.available()


if __name__ == "__main__":
    unittest.main()
