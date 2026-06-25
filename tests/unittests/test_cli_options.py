#!/usr/bin/env python3
"""Tests for the cicwave CLI option plumbing.

Focused on the flags with non-trivial validation that runs before the
GUI is imported (``--csv-sep`` and ``--csv-comment``).
"""

import os
import tempfile
import unittest
from unittest import mock

from click.testing import CliRunner

import cicwave.cli as cli_mod
from cicwave.cli import main, _expand_glob_patterns
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


class TestPositionalGlobExpansion(unittest.TestCase):
    """Positional wildcard paths must expand even when the shell doesn't.

    PowerShell / cmd.exe pass ``dir/*.npz`` verbatim to external commands;
    the CLI expands such args itself so ``cicwave dir/*.npz`` just works.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cicwave-glob-")
        for name in ("a.npz", "b.npz"):
            open(os.path.join(self.tmp, name), "wb").close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_wildcard_positional_expands(self):
        pat = os.path.join(self.tmp, "*.npz")
        out = _expand_glob_patterns((pat,), ())
        self.assertEqual(
            sorted(os.path.basename(p) for p in out), ["a.npz", "b.npz"])

    def test_literal_existing_path_kept(self):
        lit = os.path.join(self.tmp, "a.npz")
        out = _expand_glob_patterns((lit,), ())
        self.assertEqual(out, (lit,))

    def test_plain_missing_path_preserved(self):
        # No glob chars: keep verbatim so downstream reports a real error
        # rather than silently dropping a typo'd path.
        missing = os.path.join(self.tmp, "nope.npz")
        out = _expand_glob_patterns((missing,), ())
        self.assertEqual(out, (missing,))

    def test_nonmatching_wildcard_dropped(self):
        pat = os.path.join(self.tmp, "*.csv")
        out = _expand_glob_patterns((pat,), ())
        self.assertEqual(out, ())


if __name__ == "__main__":
    unittest.main()
