"""Tests for NumPy .npz loading (WaveFile / read_npz)."""

import os
import tempfile
import unittest

import numpy as np

from cicwave.wavefiles import WaveFile, read_npz


class ReadNpzTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cicwave-npz-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _path(self, name):
        return os.path.join(self.tmp, name)

    def test_rnsr_swept_shape_and_frequency_alias(self):
        f = np.linspace(2.4e9, 2.5e9, 51)
        a = np.full_like(f, -85.0)
        a[25] = -30.0
        p = self._path("swept.npz")
        np.savez_compressed(p, freqs_hz=f, amps_dbm=a, center_mhz=np.asarray(2450.0))
        df = read_npz(p)
        self.assertEqual(len(df), 51)
        self.assertIn("freqs_hz", df.columns)
        self.assertIn("amps_dbm", df.columns)
        self.assertIn("center_mhz", df.columns)
        self.assertIn("frequency", df.columns)
        np.testing.assert_array_equal(df["frequency"].to_numpy(), f)
        np.testing.assert_array_equal(df["amps_dbm"].to_numpy(), a)
        self.assertTrue(np.all(df["center_mhz"].to_numpy() == 2450.0))

    def test_freqs_mhz_only(self):
        m = np.linspace(2400.0, 2500.0, 11)
        y = np.arange(11, dtype=float)
        p = self._path("mhz.npz")
        np.savez_compressed(p, freqs_mhz=m, amps_dbm=y)
        df = read_npz(p)
        np.testing.assert_array_almost_equal(
            df["frequency"].to_numpy(), m * 1e6)

    def test_wavefile_opens_npz(self):
        p = self._path("t.npz")
        np.savez_compressed(p, x=np.arange(5), y=np.ones(5))
        wf = WaveFile(p, xaxis="")
        cols = list(wf.df.columns)
        self.assertIn("x", cols)
        self.assertIn("y", cols)

    def test_two_d_expands_columns(self):
        p = self._path("2d.npz")
        m = np.arange(12, dtype=float).reshape(4, 3)
        np.savez_compressed(p, time=np.arange(4), mat=m)
        df = read_npz(p)
        self.assertEqual(len(df), 4)
        self.assertIn("mat__0", df.columns)
        self.assertIn("mat__1", df.columns)
        self.assertIn("mat__2", df.columns)


if __name__ == "__main__":
    unittest.main()
