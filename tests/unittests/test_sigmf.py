"""Tests for SigMF recording loading (read_sigmf / WaveFile)."""

import io
import json
import os
import shutil
import tarfile
import tempfile
import unittest

import numpy as np

from cicwave.wavefiles import WaveFile, read_sigmf


class ReadSigmfTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cicwave-sigmf-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, payload, glob, captures=None):
        base = os.path.join(self.tmp, name)
        meta = {"global": dict({"core:version": "1.0.0"}, **glob),
                "captures": captures if captures is not None
                else [{"core:sample_start": 0}],
                "annotations": []}
        with open(base + ".sigmf-meta", "w", encoding="utf-8") as fh:
            json.dump(meta, fh)
        with open(base + ".sigmf-data", "wb") as fh:
            fh.write(payload)
        return base

    def test_cf32_le_gives_complex_iq_time_and_center(self):
        iq = (np.arange(8) - 1j * np.arange(8)).astype("<c8")
        base = self._write("cap", iq.tobytes(),
                           {"core:datatype": "cf32_le",
                            "core:sample_rate": 2e6},
                           [{"core:sample_start": 0,
                             "core:frequency": 2.44e9}])
        df = read_sigmf(base + ".sigmf-meta")
        self.assertEqual(list(df.columns),
                         ["time", "iq", "I", "Q", "mag", "power_dB"])
        np.testing.assert_array_equal(df["iq"].to_numpy(), iq)
        self.assertAlmostEqual(df["time"].iloc[1], 0.5e-6)
        self.assertEqual(df.attrs["cicwave_iq"],
                         {"samp_rate_hz": 2e6, "center_hz": 2.44e9})

    def test_opening_the_data_half_reads_the_same_recording(self):
        iq = np.ones(4, dtype="<c8")
        base = self._write("pair", iq.tobytes(), {"core:datatype": "cf32_le"})
        df = read_sigmf(base + ".sigmf-data")
        self.assertEqual(len(df), 4)
        self.assertIn("sample", df.columns)

    def test_ci16_be_and_real_u8(self):
        raw = np.array([1, -2, 3, -4], dtype=">i2")
        base = self._write("i16", raw.tobytes(), {"core:datatype": "ci16_be"})
        np.testing.assert_array_equal(read_sigmf(base + ".sigmf-meta")["iq"],
                                      [1 - 2j, 3 - 4j])
        base = self._write("u8", bytes([0, 128, 255]), {"core:datatype": "ru8"})
        np.testing.assert_array_equal(
            read_sigmf(base + ".sigmf-meta")["value"], [0, 128, 255])

    def test_multichannel_is_split_per_channel(self):
        # Two channels interleaved per sample: ch0, ch1, ch0, ch1.
        iq = np.array([1, 10, 2, 20], dtype="<c8")
        base = self._write("mc", iq.tobytes(),
                           {"core:datatype": "cf32_le",
                            "core:num_channels": 2})
        df = read_sigmf(base + ".sigmf-meta")
        np.testing.assert_array_equal(df["iq_ch0"], [1, 2])
        np.testing.assert_array_equal(df["iq_ch1"], [10, 20])

    def test_header_bytes_are_skipped_per_capture(self):
        a = np.array([1, 2], dtype="<f4").tobytes()
        b = np.array([3, 4], dtype="<f4").tobytes()
        payload = b"HH" + a + b"HHHH" + b
        base = self._write("hdr", payload, {"core:datatype": "rf32_le"},
                           [{"core:sample_start": 0, "core:header_bytes": 2},
                            {"core:sample_start": 2, "core:header_bytes": 4}])
        np.testing.assert_array_equal(
            read_sigmf(base + ".sigmf-meta")["value"], [1, 2, 3, 4])

    def test_archive(self):
        iq = np.arange(3, dtype="<c8")
        meta = json.dumps({"global": {"core:datatype": "cf32_le",
                                      "core:version": "1.0.0"},
                           "captures": [], "annotations": []}).encode()
        path = os.path.join(self.tmp, "rec.sigmf")
        with tarfile.open(path, "w") as tar:
            for name, data in (("rec/rec.sigmf-meta", meta),
                               ("rec/rec.sigmf-data", iq.tobytes())):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        np.testing.assert_array_equal(read_sigmf(path)["iq"], iq)

    def test_bad_datatype_and_missing_data_raise(self):
        base = self._write("bad", b"\0" * 8, {"core:datatype": "cf16_le"})
        with self.assertRaises(ValueError):
            read_sigmf(base + ".sigmf-meta")
        base = self._write("gone", b"", {"core:datatype": "cf32_le"})
        os.remove(base + ".sigmf-data")
        with self.assertRaises(ValueError) as cm:
            read_sigmf(base + ".sigmf-meta")
        self.assertIn("dataset", str(cm.exception))

    def test_zipped_dataset_is_read_without_unpacking(self):
        import zipfile
        iq = np.array([1 + 2j, 3 - 4j], dtype="<c8")
        base = self._write("zipped", iq.tobytes(), {"core:datatype": "cf32_le"})
        with zipfile.ZipFile(base + ".sigmf-data.zip", "w",
                             zipfile.ZIP_DEFLATED) as zf:
            zf.write(base + ".sigmf-data", "zipped.sigmf-data")
        os.remove(base + ".sigmf-data")
        for opened in (base + ".sigmf-meta", base + ".sigmf-data.zip"):
            np.testing.assert_array_equal(read_sigmf(opened)["iq"], iq)
        wf = WaveFile(base + ".sigmf-data.zip", xaxis="")
        self.assertIn("iq", list(wf.df.columns))

    def test_litepoint_scale_converts_counts(self):
        raw = np.array([1000, -2000], dtype="<i2")
        base = self._write("scaled", raw.tobytes(),
                           {"core:datatype": "ci16_le",
                            "litepoint:scale": 0.5})
        iq = read_sigmf(base + ".sigmf-meta")["iq"].to_numpy()
        self.assertEqual(iq.dtype, np.complex64)
        np.testing.assert_allclose(iq, [500 - 1000j])

    def test_low_if_inverted_capture_maps_fft_to_rf(self):
        # Channel at -1 MHz in the samples, spectrum mirrored: a tone
        # 0.25 MHz above the channel appears at -1.25 MHz. Conjugated, it is
        # at +1.25 MHz, and center_hz + 1.25 MHz must be the tone's RF.
        base = self._write("lowif", np.zeros(4, "<i2").tobytes(),
                           {"core:datatype": "ci16_le",
                            "core:sample_rate": 16e6,
                            "litepoint:channel_offset_hz": -1e6,
                            "litepoint:spectrum_inverted": True},
                           [{"core:sample_start": 0,
                             "core:frequency": 2440e6}])
        info = read_sigmf(base + ".sigmf-meta").attrs["cicwave_iq"]
        self.assertTrue(info["spectrum_inverted"])
        self.assertAlmostEqual(info["center_hz"] + 1.25e6, 2440.25e6)

    def test_magnitude_and_power_views(self):
        # Analyser units: |z|^2 is mW, plus the stated power offset.
        raw = np.array([3000, 4000, 0, 0], dtype="<i2")
        base = self._write("tx", raw.tobytes(),
                           {"core:datatype": "ci16_le",
                            "litepoint:scale": 1e-3,
                            "litepoint:iq_power_offset_db": 0.25})
        df = read_sigmf(base + ".sigmf-meta")
        np.testing.assert_allclose(df["mag"], [5.0, 0.0], rtol=1e-6)
        self.assertAlmostEqual(df["power_dBm"].iloc[0],
                               10 * np.log10(25.0) + 0.25, places=4)
        self.assertTrue(np.isnan(df["power_dBm"].iloc[1]))  # a gap, not -inf

        # ADC counts with a stated full scale: dBFS.
        base = self._write("adc", np.array([2048, 0], "<i2").tobytes(),
                           {"core:datatype": "ci16_le",
                            "litepoint:scale": 1.0,
                            "litepoint:units": "ADC counts (full scale 2048)"})
        self.assertAlmostEqual(
            read_sigmf(base + ".sigmf-meta")["power_dBFS"].iloc[0], 0.0,
            places=4)

        # Multi-channel: the channel goes before the unit.
        base = self._write("mc2", np.ones(4, "<c8").tobytes(),
                           {"core:datatype": "cf32_le",
                            "core:num_channels": 2})
        cols = read_sigmf(base + ".sigmf-meta").columns
        self.assertIn("mag_ch1", cols)
        self.assertIn("power_ch1_dB", cols)

    def test_symbol_rate_from_modulation_text(self):
        from cicwave.wavefiles import _sigmf_symbol_rate
        self.assertEqual(_sigmf_symbol_rate(
            {"litepoint:modulation": "16QAM, 2 Msym/s"}), 2e6)
        self.assertEqual(_sigmf_symbol_rate(
            {"litepoint:modulation": "QPSK 1.875 Msps"}), 1.875e6)
        self.assertIsNone(_sigmf_symbol_rate(
            {"litepoint:modulation": "GFSK, BT 0.5, h 0.5"}))
        self.assertIsNone(_sigmf_symbol_rate({}))

    def test_annotation_picker_numbers_each_label_and_sorts(self):
        from cicwave.wave_pg import PgWaveWindow
        attrs = {"cicwave_sigmf": {"annotations": [
            {"core:sample_start": 500, "core:sample_count": 100,
             "core:label": "payload"},
            {"core:sample_start": 0, "core:sample_count": 1000,
             "core:label": "burst", "litepoint:mean_dbm": 4.74},
            {"core:sample_start": 100, "core:sample_count": 50,
             "core:label": "header"},
            {"core:sample_start": 2000, "core:sample_count": 100,
             "core:label": "payload"},
            {"core:sample_start": 9000, "core:sample_count": 10},  # past end
        ]}}
        got = PgWaveWindow._constellation_bursts(attrs, 3000)
        self.assertEqual([lb.split(" (")[0] for lb, _ in got],
                         ["burst 1", "header 1", "payload 1", "payload 2"])
        self.assertEqual(got[0][1], (0, 1000))
        self.assertIn("4.74 dBm", got[0][0])

    def test_wavefile_dispatches_on_extension(self):
        base = self._write("wf", np.ones(5, dtype="<c8").tobytes(),
                           {"core:datatype": "cf32_le",
                            "core:sample_rate": 1e3})
        wf = WaveFile(base + ".sigmf-meta", xaxis="")
        self.assertIn("iq", list(wf.df.columns))


if __name__ == "__main__":
    unittest.main()
