#!/usr/bin/env python3
"""Tests for the cicwave CLI option plumbing.

Focused on the flags with non-trivial validation that runs before the
GUI is imported (``--csv-sep`` and ``--csv-comment``).
"""

import unittest
from unittest import mock

from click.testing import CliRunner

import cicwave.cli as cli_mod
from cicwave.cli import main
from cicwave.wavefiles import WaveFile, _UNSET


def _runner():
    """CliRunner that merges stderr into output across Click versions."""
    if "mix_stderr" in CliRunner.__init__.__code__.co_varnames:
        return CliRunner(mix_stderr=True)
    return CliRunner()


class TestCsvSepCliFlag(unittest.TestCase):
    def tearDown(self):
        WaveFile.set_csv_sep_override(None)

    def test_invalid_csv_sep_exits_with_error(self):
        # No files -> empty file list. Bad --csv-sep validates early and
        # calls sys.exit(2) before the GUI backend would be imported.
        result = _runner().invoke(main, ["--csv-sep", "bogus"])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("--csv-sep", result.output)

    def test_empty_csv_sep_exits_with_error(self):
        result = _runner().invoke(main, ["--csv-sep", ""])
        self.assertEqual(result.exit_code, 2)


class TestCsvCommentCliFlag(unittest.TestCase):
    def tearDown(self):
        WaveFile.set_csv_comment_override(_UNSET)
        WaveFile.set_csv_sep_override(None)

    def test_whitespace_only_csv_comment_exits_with_error(self):
        result = _runner().invoke(main, ["--csv-comment", "   "])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("--csv-comment", result.output)

    def test_valid_csv_comment_is_forwarded_to_run(self):
        # Stub out the actual viewer so the GUI never starts; verify
        # Click parses --csv-comment and passes it through.
        with mock.patch.object(cli_mod, "_run_wave_pg") as run:
            result = _runner().invoke(main, ["--csv-comment", "#"])
            self.assertEqual(
                result.exit_code, 0,
                "expected clean exit, got %r\n%s"
                % (result.exit_code, result.output))
            run.assert_called_once()
            self.assertEqual(run.call_args.kwargs.get("csv_comment"), "#")

    def test_explicit_disable_csv_comment_is_forwarded_as_empty(self):
        with mock.patch.object(cli_mod, "_run_wave_pg") as run:
            result = _runner().invoke(main, ["--csv-comment", ""])
            self.assertEqual(result.exit_code, 0)
            run.assert_called_once()
            self.assertEqual(run.call_args.kwargs.get("csv_comment"), "")


if __name__ == "__main__":
    unittest.main()
