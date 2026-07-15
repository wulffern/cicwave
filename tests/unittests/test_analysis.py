#!/usr/bin/env python3
"""Unit tests for cicwave.analysis (Qt-free DSP helpers)."""

import math
import unittest

import numpy as np
import pandas as pd

from cicwave import analysis


class TestDecodeTwos(unittest.TestCase):
    def test_width_8(self):
        x = np.array([0, 127, 128, 255], dtype=np.int64)
        y = analysis.decode_twos_complement(x, 8)
        np.testing.assert_array_almost_equal(y, [0, 127, -128, -1])

    def test_invalid_width(self):
        with self.assertRaises(ValueError):
            analysis.decode_twos_complement(np.array([0]), 0)

    def test_nan_passes_through_instead_of_becoming_zero(self):
        x = np.array([1.0, 2.0, np.nan, 3.0])
        y = analysis.decode_twos_complement(x, 12)
        np.testing.assert_array_almost_equal(y[[0, 1, 3]], [1.0, 2.0, 3.0])
        self.assertTrue(math.isnan(y[2]))


class TestRms(unittest.TestCase):
    def test_basic(self):
        self.assertAlmostEqual(analysis.rms(np.array([3.0, 4.0])), 5.0 / math.sqrt(2))

    def test_empty(self):
        self.assertTrue(math.isnan(analysis.rms(np.array([]))))


class TestTimeBase(unittest.TestCase):
    def test_uniform_near_zero(self):
        x = np.linspace(0, 1, 50)
        self.assertLess(analysis.time_base_irregularity(x), 1e-12)


class TestResampleUniform(unittest.TestCase):
    def test_fs_matches_dt(self):
        x = np.array([0.0, 1.0, 2.0, 3.0])
        y = x ** 2
        r = analysis.resample_uniform(x, y, dt=0.5)
        self.assertGreater(len(r.x_new), len(x))
        self.assertAlmostEqual(r.fs, 2.0)


class TestPsdRfft(unittest.TestCase):
    def test_smoke_peak_normalized(self):
        fs = 1000.0
        n = 512
        t = np.arange(n) / fs
        y = np.sin(2 * np.pi * 100.0 * t)
        r = analysis.psd_rfft(t, y, auto_resample=False)
        self.assertEqual(len(r.freq_hz), len(r.psd_db))
        self.assertTrue(np.all(np.diff(r.freq_hz) > 0))
        self.assertFalse(r.meta.get("dbfs"))

    def test_dbfs_mode(self):
        fs = 1000.0
        n = 256
        t = np.arange(n) / fs
        y = np.sin(2 * np.pi * 50.0 * t)
        r = analysis.psd_rfft(
            t, y, auto_resample=False, dbfs_amplitude=1.0)
        self.assertTrue(r.meta.get("dbfs"))
        #- Hann spreads a coherent FS tone across bins; peak < 0 dBFS.
        mx = float(np.max(r.psd_db))
        self.assertLess(mx, 0.5)
        self.assertGreater(mx, -8.0)

    def test_complex_is_two_sided(self):
        #- A positive-frequency complex exponential has energy on the +f
        #- side only; a real cosine would show both +/- lobes.
        fs = 1000.0
        n = 512
        t = np.arange(n) / fs
        f0 = 100.0
        y = np.exp(2j * np.pi * f0 * t)
        r = analysis.psd_rfft(t, y, auto_resample=False)
        self.assertTrue(r.meta.get("two_sided"))
        #- Two-sided axis spans negative frequencies and is sorted.
        self.assertLess(float(r.freq_hz[0]), 0.0)
        self.assertGreater(float(r.freq_hz[-1]), 0.0)
        self.assertTrue(np.all(np.diff(r.freq_hz) > 0))
        self.assertEqual(len(r.freq_hz), len(r.psd_db))
        #- Peak sits at +f0, not -f0.
        kpk = int(np.argmax(r.psd_db))
        self.assertGreater(float(r.freq_hz[kpk]), 0.0)
        self.assertAlmostEqual(float(r.freq_hz[kpk]), f0, delta=fs / n)

    def test_real_is_single_sided(self):
        fs = 1000.0
        n = 512
        t = np.arange(n) / fs
        y = np.cos(2 * np.pi * 100.0 * t)
        r = analysis.psd_rfft(t, y, auto_resample=False)
        self.assertFalse(r.meta.get("two_sided"))
        self.assertTrue(np.all(r.freq_hz >= 0.0))

    def test_dbfs_peak_amplitude_128_reference(self):
        fs = 1000.0
        n = 256
        t = np.arange(n) / fs
        A = 128.0
        y = A * np.sin(2 * np.pi * 50.0 * t)
        r = analysis.psd_rfft(
            t, y, auto_resample=False, dbfs_amplitude=A)
        self.assertTrue(r.meta.get("dbfs"))
        mx = float(np.max(r.psd_db))
        self.assertLess(mx, 0.5)
        self.assertGreater(mx, -8.0)


class TestAdcPsdAnalysis(unittest.TestCase):
    def test_sine_has_sfdr_and_harmonics_list(self):
        #- Coherent capture so the Hann main lobe sits inside the default
        #- ``filterwidth=3`` (±3) window cleanly.
        fs = 2000.0
        n = 4096
        f0 = fs * 137 / n  # exact bin centre
        t = np.arange(n) / fs
        y = np.sin(2 * np.pi * f0 * t)
        r = analysis.adc_psd_analysis(t, y, f0=f0, max_harmonics=6)
        self.assertEqual(r.freq_hz.shape, r.psd_db.shape)
        self.assertEqual(r.psd_unit, "dBc")
        self.assertTrue(np.isfinite(r.sfdr_db))
        self.assertGreater(r.sfdr_db, 60.0)
        self.assertGreater(len(r.marker_freqs_hz), 1)
        self.assertTrue(any(h.order == 2 for h in r.harmonics))

    def test_dbfs_and_fund_filterwidth(self):
        fs = 2000.0
        n = 4096
        f0 = fs * 100 / n
        t = np.arange(n) / fs
        A = 0.5  # half-scale tone
        y = A * np.sin(2 * np.pi * f0 * t)
        r = analysis.adc_psd_analysis(
            t, y, f0=f0, dbfs_amplitude=1.0, fund_filterwidth=5)
        self.assertEqual(r.psd_unit, "dBFS")
        #- Half-scale sine: -6 dBFS ± Hann lobe loss.
        self.assertLess(r.signal_dbfs, -5.0)
        self.assertGreater(r.signal_dbfs, -8.0)

    def test_sfdr_includes_harmonics(self):
        #- A strong 3rd harmonic must dominate SFDR even though it is
        #- listed in ``harmonics`` — IEEE definition.
        fs = 2000.0
        n = 4096
        f0 = fs * 100 / n
        t = np.arange(n) / fs
        h3_dbc = -25.0  # 3rd harmonic at -25 dBc
        a3 = 10.0 ** (h3_dbc / 20.0)
        y = np.sin(2 * np.pi * f0 * t) + a3 * np.sin(2 * np.pi * 3 * f0 * t)
        r = analysis.adc_psd_analysis(t, y, f0=f0, max_harmonics=6)
        self.assertTrue(np.isfinite(r.sfdr_db))
        #- Hann scallop / lobe-vs-peak asymmetry costs ~1-3 dB.
        self.assertAlmostEqual(r.sfdr_db, -h3_dbc, delta=3.0)


class TestDynamicParameters(unittest.TestCase):
    def test_sine_high_snr(self):
        fs = 2000.0
        n = 4096
        t = np.arange(n) / fs
        f0 = 137.0
        y = np.sin(2 * np.pi * f0 * t)
        res = analysis.dynamic_parameters(y, fs, f0)
        self.assertTrue(np.isfinite(res.snr_db))
        self.assertTrue(np.isfinite(res.sndr_db))
        self.assertGreater(res.snr_db, 40.0)
        self.assertGreater(res.sndr_db, 40.0)
        self.assertAlmostEqual(res.f0_hz, f0, delta=fs / n * 2)

    def test_auto_fundamental_matches_explicit(self):
        fs = 1000.0
        n = 2048
        t = np.arange(n) / fs
        f0 = 111.0
        y = np.sin(2 * np.pi * f0 * t)
        ra = analysis.dynamic_parameters(y, fs, None)
        rb = analysis.dynamic_parameters(y, fs, f0)
        self.assertTrue(np.isfinite(ra.f0_hz))
        self.assertEqual(ra.signal_bin, rb.signal_bin)
        self.assertAlmostEqual(ra.snr_db, rb.snr_db, delta=0.1)

    def test_osr_sigma_delta_matches_snr_sndr_without_harmonics(self):
        fs = 1000.0
        n = 1024
        t = np.arange(n) / fs
        y = np.sin(2 * np.pi * 50.0 * t)
        r = analysis.dynamic_parameters(
            y, fs, 50.0, osr=4.0, exclude_harmonics=False)
        self.assertTrue(np.isfinite(r.sndr_db))
        self.assertAlmostEqual(r.snr_db, r.sndr_db, places=5)


class TestLinearFit(unittest.TestCase):
    def test_line(self):
        x = np.linspace(0, 10, 20)
        y = 2.5 * x - 1.0
        r = analysis.linear_fit(x, y)
        self.assertAlmostEqual(r.slope, 2.5)
        self.assertAlmostEqual(r.intercept, -1.0)
        self.assertAlmostEqual(r.r_squared, 1.0)


class TestDifference(unittest.TestCase):
    def test_trim(self):
        d = analysis.difference(np.array([5, 4, 3]), np.array([1, 1]))
        np.testing.assert_array_almost_equal(d, [4, 3])


class TestRunAnalysisSteps(unittest.TestCase):
    def test_rms_and_dynamic(self):
        fs = 1000.0
        n = 1024
        t = np.arange(n) / fs
        f0 = 100.0
        y = np.sin(2 * np.pi * f0 * t)
        df = pd.DataFrame({"time": t, "sig": y})
        steps = [
            {"type": "rms", "column": "sig"},
            {"type": "dynamic_parameters", "y_column": "sig",
             "fs": fs, "f0": f0},
        ]
        out = analysis.run_analysis_steps(df, steps, x_column="time")
        self.assertIn("RMS=", out.summary_text)
        self.assertIn("SNR=", out.summary_text)
        self.assertIn("f0=", out.summary_text)

        out_auto = analysis.run_analysis_steps(
            df,
            [{"type": "dynamic_parameters", "y_column": "sig", "fs": fs}],
            x_column="time",
        )
        self.assertIn("f0=", out_auto.summary_text)


class TestPreprocessDataframe(unittest.TestCase):
    def test_twos_columns(self):
        df = pd.DataFrame({"time": [0, 1], "code": [255, 128]})
        spec = {"twos_complement": {"width_bits": 8, "columns": ["code"]}}
        out = analysis.preprocess_dataframe(df, spec)
        self.assertAlmostEqual(out["code"].iloc[0], -1.0)
        self.assertAlmostEqual(out["code"].iloc[1], -128.0)


if __name__ == "__main__":
    unittest.main()
