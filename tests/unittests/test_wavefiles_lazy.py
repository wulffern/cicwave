#!/usr/bin/env python3
"""Tests for lazy CSV / column-only loading in WaveFile."""

import os
import tempfile
import unittest

import pandas as pd

from cicwave.wavefiles import WaveFile


class TestWaveFileLazy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.csv = os.path.join(self.tmp.name, "wf.csv")
        with open(self.csv, "w") as f:
            f.write("time,v(out),v(in)\n")
            for i in range(5):
                f.write("%d,%g,%g\n" % (i, i * 0.1, i * 0.2))

    def tearDown(self):
        self.tmp.cleanup()

    def test_open_does_not_load_full_dataframe(self):
        wf = WaveFile(self.csv, xaxis="time")
        self.assertIsNone(wf._df, "df should be None after open (lazy)")
        self.assertEqual(
            list(wf.getWaveNames()), ["time", "v(out)", "v(in)"])
        self.assertIsNone(wf._df, "getWaveNames must not trigger full read")

    def test_df_access_loads_full_dataframe(self):
        wf = WaveFile(self.csv, xaxis="time")
        df = wf.df
        self.assertIsInstance(df, pd.DataFrame)
        self.assertEqual(len(df), 5)
        self.assertEqual(list(df.columns), ["time", "v(out)", "v(in)"])
        self.assertAlmostEqual(df["v(out)"].iloc[4], 0.4)

    def test_setter_keeps_columns_in_sync(self):
        wf = WaveFile(self.csv, xaxis="time")
        new = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        wf.df = new
        self.assertEqual(list(wf.getWaveNames()), ["a", "b"])

    def test_virtual_dataframe_is_not_re_read(self):
        df = pd.DataFrame({"time": [0, 1], "v(x)": [0.5, 0.6]})
        wf = WaveFile("virtual.csv", xaxis="time", df=df)
        #- Virtual files keep a snapshot copy so pivot/GUI cannot mutate caller data.
        self.assertIsNot(wf.df, df)
        pd.testing.assert_frame_equal(wf.df, df)
        self.assertEqual(list(wf.getWaveNames()), ["time", "v(x)"])


class TestWaveFileCsvSeparatorSniffing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, content):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_semicolon_separated_csv(self):
        path = self._write(
            "wf_semi.csv",
            "time;v(out);v(in)\n"
            + "".join("%d;%g;%g\n" % (i, i * 0.1, i * 0.2) for i in range(5)),
        )
        wf = WaveFile(path, xaxis="time")
        self.assertEqual(
            list(wf.getWaveNames()), ["time", "v(out)", "v(in)"])
        df = wf.df
        self.assertEqual(len(df), 5)
        self.assertAlmostEqual(df["v(out)"].iloc[4], 0.4)

    def test_comma_separated_still_works(self):
        path = self._write(
            "wf_comma.csv",
            "time,v(out)\n0,0\n1,0.5\n2,1.0\n",
        )
        wf = WaveFile(path, xaxis="time")
        self.assertEqual(list(wf.getWaveNames()), ["time", "v(out)"])
        self.assertAlmostEqual(wf.df["v(out)"].iloc[2], 1.0)

    def test_pipe_separated_csv(self):
        path = self._write(
            "wf_pipe.csv",
            "time|a|b\n0|1|2\n1|3|4\n",
        )
        wf = WaveFile(path, xaxis="time")
        self.assertEqual(list(wf.getWaveNames()), ["time", "a", "b"])
        self.assertEqual(wf.df["b"].iloc[1], 4)


class TestWaveFileCsvSepOverride(unittest.TestCase):
    """Cover the ``--csv-sep`` plumbing on WaveFile.set_csv_sep_override."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        # Always clear the global override so test order doesn't matter.
        WaveFile.set_csv_sep_override(None)
        self.tmp.cleanup()

    def _write(self, name, content):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_override_forces_separator(self):
        # File looks like it could be sniffed as ',' but we force ';'.
        path = self._write(
            "ambiguous.csv",
            "a,b;c\n1,2;3\n4,5;6\n",
        )
        WaveFile.set_csv_sep_override(";")
        wf = WaveFile(path, xaxis="a,b")
        self.assertEqual(list(wf.getWaveNames()), ["a,b", "c"])

    def test_override_alias_tab(self):
        path = self._write(
            "tabbed.csv",
            "time\tv\n0\t0.0\n1\t0.5\n",
        )
        WaveFile.set_csv_sep_override("tab")
        wf = WaveFile(path, xaxis="time")
        self.assertEqual(list(wf.getWaveNames()), ["time", "v"])
        self.assertAlmostEqual(wf.df["v"].iloc[1], 0.5)

    def test_override_alias_backslash_t(self):
        path = self._write(
            "tabbed2.csv",
            "x\ty\n1\t2\n",
        )
        WaveFile.set_csv_sep_override("\\t")
        wf = WaveFile(path, xaxis="x")
        self.assertEqual(list(wf.getWaveNames()), ["x", "y"])

    def test_override_clear_restores_sniffing(self):
        path = self._write(
            "semi.csv",
            "time;v\n0;0.0\n1;1.0\n",
        )
        WaveFile.set_csv_sep_override(";")
        WaveFile.set_csv_sep_override(None)
        wf = WaveFile(path, xaxis="time")
        # Sniffer should still pick ';'.
        self.assertEqual(list(wf.getWaveNames()), ["time", "v"])

    def test_override_invalid_raises(self):
        with self.assertRaises(ValueError):
            WaveFile.set_csv_sep_override("--")
        with self.assertRaises(ValueError):
            WaveFile.set_csv_sep_override("")


class TestWaveFileWhitespace(unittest.TestCase):
    """Cover the .dat/.spe/.cou/.chi whitespace-separated reader."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        # Always restore defaults so test order doesn't matter.
        from cicwave.wavefiles import _UNSET
        WaveFile.set_csv_comment_override(_UNSET)
        WaveFile.set_csv_sep_override(None)
        self.tmp.cleanup()

    def _write(self, name, content):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_dat_with_hash_header_comment(self):
        # ngspice .dat-style: '#'-prefixed banner line above the header.
        path = self._write(
            "ng.dat",
            "# generated by ngspice 42\n"
            "time v(out) v(in)\n"
            "0    0.0    0.0\n"
            "1    0.1    0.2\n"
            "2    0.2    0.4\n",
        )
        wf = WaveFile(path, xaxis="time")
        self.assertEqual(
            list(wf.getWaveNames()), ["time", "v(out)", "v(in)"])
        df = wf.df
        self.assertEqual(len(df), 3)
        self.assertAlmostEqual(df["v(out)"].iloc[2], 0.2)

    def test_cou_with_irregular_whitespace(self):
        # Eldo .cou-style: multiple spaces / mixed spacing between cols.
        path = self._write(
            "wf.cou",
            "TIME      V(A)        V(B)\n"
            "0.0       1.0         2.0\n"
            "1.0e-9    1.5         2.5\n"
            "2.0e-9    2.0         3.0\n",
        )
        wf = WaveFile(path, xaxis="TIME")
        self.assertEqual(list(wf.getWaveNames()), ["TIME", "V(A)", "V(B)"])
        df = wf.df
        self.assertEqual(len(df), 3)
        self.assertAlmostEqual(df["V(B)"].iloc[1], 2.5)

    def test_lazy_header_read_does_not_load_full_dataframe(self):
        path = self._write(
            "lazy.dat",
            "# header comment\n"
            "x y z\n"
            + "".join("%d %d %d\n" % (i, i + 1, i + 2) for i in range(20)),
        )
        wf = WaveFile(path, xaxis="x")
        self.assertIsNone(
            wf._df, "df should be None after open (lazy)")
        self.assertEqual(list(wf.getWaveNames()), ["x", "y", "z"])
        self.assertIsNone(
            wf._df, "getWaveNames must not trigger full read")
        # Now force a full read and verify shape.
        self.assertEqual(len(wf.df), 20)


class TestWaveFileCsvComment(unittest.TestCase):
    """Cover --csv-comment override on plain CSV files."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        from cicwave.wavefiles import _UNSET
        WaveFile.set_csv_comment_override(_UNSET)
        WaveFile.set_csv_sep_override(None)
        self.tmp.cleanup()

    def _write(self, name, content):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_single_char_comment_strips_header_banner(self):
        path = self._write(
            "withhash.csv",
            "# generated by ngspice 42\n"
            "# version 1.2\n"
            "time,v(out)\n"
            "0,0.0\n"
            "1,0.5\n"
            "2,1.0\n",
        )
        WaveFile.set_csv_comment_override("#")
        wf = WaveFile(path, xaxis="time")
        self.assertEqual(list(wf.getWaveNames()), ["time", "v(out)"])
        self.assertAlmostEqual(wf.df["v(out)"].iloc[2], 1.0)

    def test_multichar_comment_strips_via_prefilter(self):
        # '//' is a multi-character marker, requires the pre-filter path.
        path = self._write(
            "cstyle.csv",
            "// header banner\n"
            "// more notes\n"
            "time,v\n"
            "0,0.0\n"
            "1,0.5\n",
        )
        WaveFile.set_csv_comment_override("//")
        wf = WaveFile(path, xaxis="time")
        self.assertEqual(list(wf.getWaveNames()), ["time", "v"])
        self.assertAlmostEqual(wf.df["v"].iloc[1], 0.5)

    def test_explicit_disable_overrides_whitespace_default(self):
        # Whitespace formats default to comment='#'. ``--csv-comment ''``
        # should disable that, causing the '#' line to be parsed as data
        # (and thus typically blow up or yield a weird header).
        path = self._write(
            "explicit.dat",
            "# this should NOT be stripped\n"
            "x y\n"
            "0 1\n"
            "1 2\n",
        )
        WaveFile.set_csv_comment_override("")
        wf = WaveFile(path, xaxis="x")
        names = wf.getWaveNames()
        # Without stripping, the '# this should NOT be stripped' line is
        # parsed as the header; it has many whitespace-split tokens
        # (including the bare '#') and definitely doesn't look like the
        # 2-column 'x y' header we'd see if stripping were active.
        self.assertGreater(
            len(names), 2,
            "comment line should NOT be stripped when override is ''")
        self.assertIn("#", names[0])

    def test_invalid_marker_raises(self):
        with self.assertRaises(ValueError):
            WaveFile.set_csv_comment_override("   ")
        with self.assertRaises(ValueError):
            WaveFile.set_csv_comment_override("\t  \n")


if __name__ == "__main__":
    unittest.main()
