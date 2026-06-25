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

    def test_complex_iq_column_preserved(self):
        p = self._path("iq.npz")
        iq = (np.arange(8) + 1j * np.arange(8)).astype(np.complex64)
        np.savez_compressed(p, iq=iq)
        df = read_npz(p)
        self.assertIn("iq", df.columns)
        self.assertTrue(np.iscomplexobj(df["iq"].to_numpy()))

    def test_iq_sidecar_adds_time_axis(self):
        import json
        p = self._path("sdr_iq.npz")
        n = 16
        iq = np.ones(n, dtype=np.complex64)
        np.savez_compressed(p, iq=iq)
        with open(self._path("sdr_iq.json"), "w", encoding="utf-8") as fh:
            json.dump({"samp_rate_hz": 16e6, "center_mhz": 1000.0}, fh)
        df = read_npz(p)
        self.assertIn("time", df.columns)
        t = df["time"].to_numpy()
        self.assertAlmostEqual(t[1] - t[0], 1.0 / 16e6)

    def test_iq_without_sidecar_has_no_time_axis(self):
        p = self._path("plain_iq.npz")
        np.savez_compressed(p, iq=np.ones(8, dtype=np.complex64))
        df = read_npz(p)
        self.assertNotIn("time", df.columns)

    def test_iq_sidecar_records_center_and_rate_in_attrs(self):
        import json
        p = self._path("sdr2_iq.npz")
        np.savez_compressed(p, iq=np.ones(8, dtype=np.complex64))
        with open(self._path("sdr2_iq.json"), "w", encoding="utf-8") as fh:
            json.dump({"samp_rate_hz": 16e6, "center_mhz": 1000.0}, fh)
        df = read_npz(p)
        info = df.attrs.get("cicwave_iq")
        self.assertIsNotNone(info)
        self.assertAlmostEqual(info["samp_rate_hz"], 16e6)
        self.assertAlmostEqual(info["center_hz"], 1000e6)


if __name__ == "__main__":
    unittest.main()
