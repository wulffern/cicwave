"""Tests for raw uint32 counter captures (.u32 + .meta.json sidecar)."""

import json
import os
import shutil
import tempfile
import unittest

import numpy as np

from cicwave.wavefiles import WaveFile, read_u32


class ReadU32Test(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cicwave-u32-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _path(self, name):
        return os.path.join(self.tmp, name)

    def _write(self, name, ticks, meta=None, meta_name=None):
        p = self._path(name)
        np.asarray(ticks, dtype="<u4").tofile(p)
        if meta is not None:
            side = self._path(meta_name or (os.path.splitext(name)[0] + ".meta.json"))
            with open(side, "w", encoding="utf-8") as fh:
                json.dump(meta, fh)
        return p

    def _chunks(self, n, per_chunk, elapsed_us, gap_s=0.0, key="n"):
        out, t = [], 1.7e9
        left = n
        while left > 0:
            k = min(per_chunk, left)
            out.append({"t_unix": t, key: k,
                        "elapsed_us": int(elapsed_us * k / per_chunk)})
            t += elapsed_us / 1e6 + gap_s
            left -= k
        return out

    # ------------------------------------------------------------------ GR06
    def test_gr06_width_converted_to_ns(self):
        tick_s = 2 / 128e6                       # 15.625 ns
        ticks = np.full(400, 450, dtype=np.uint32)
        meta = {"sensor": "GR06", "tick_s": tick_s,
                "chunks": self._chunks(400, 100, 100_000)}
        df = read_u32(self._write("noise-GR06-x.u32", ticks, meta))
        self.assertEqual(list(df.columns), ["time", "GR06_width_ns"])
        self.assertEqual(len(df), 400)
        self.assertAlmostEqual(df["GR06_width_ns"].iloc[0],
                               450 * tick_s * 1e9, places=6)

    # ------------------------------------------------------------------ GR07
    def test_gr07_ticks_become_frequency(self):
        tick_s, per = 3 / 128e6, 907
        # choose ticks so the frequency is a round number
        f_target = 910e3
        ticks = np.full(300, round(per / f_target / tick_s), dtype=np.uint32)
        meta = {"sensor": "GR07", "tick_s": tick_s, "periods_per_sample": per,
                "chunks": self._chunks(300, 100, 100_000)}
        df = read_u32(self._write("noise-GR07-x.u32", ticks, meta))
        self.assertEqual(list(df.columns), ["time", "GR07_rate_Hz"])
        self.assertAlmostEqual(df["GR07_rate_Hz"].iloc[0] / f_target, 1.0, places=3)

    def test_zero_ticks_do_not_divide_by_zero(self):
        meta = {"sensor": "GR07", "tick_s": 3 / 128e6, "periods_per_sample": 907,
                "chunks": self._chunks(4, 4, 4000)}
        df = read_u32(self._write("noise-GR07-z.u32", [0, 70, 0, 70], meta))
        self.assertTrue(np.isnan(df["GR07_rate_Hz"].iloc[0]))
        self.assertTrue(np.isfinite(df["GR07_rate_Hz"].iloc[1]))

    # ---------------------------------------------------- multi-sensor layout
    def test_sensor_component_in_filename_finds_the_shared_sidecar(self):
        meta = {"mode": "dual", "gr07_tick_s": 3 / 128e6, "gr06_tick_s": 2 / 128e6,
                "gr07_periods_per_sample": 907,
                "chunks": [{"t_unix": 1.7e9, "n7": 100, "n6": 350,
                            "elapsed_us": 100_000}]}
        self._write("corr-dual-x.gr07.u32", np.full(100, 70, dtype=np.uint32),
                    meta, meta_name="corr-dual-x.meta.json")
        self._write("corr-dual-x.gr06.u32", np.full(350, 450, dtype=np.uint32))
        self._write("corr-dual-x.index.u32", np.arange(1, 101, dtype=np.uint32))

        d7 = read_u32(self._path("corr-dual-x.gr07.u32"))
        d6 = read_u32(self._path("corr-dual-x.gr06.u32"))
        di = read_u32(self._path("corr-dual-x.index.u32"))
        self.assertEqual(list(d7.columns), ["time", "GR07_rate_Hz"])
        self.assertEqual(list(d6.columns), ["time", "GR06_width_ns"])
        self.assertEqual(list(di.columns), ["time", "gr06_pulse_index"])
        self.assertEqual(len(d6), 350)
        # the GR06 array is longer than the GR07 one and must use its own count
        self.assertGreater(d6["time"].iloc[-1], 0.0)

    # --------------------------------------------------------------- time axis
    def test_dead_time_between_chunks_is_preserved(self):
        """The gap while a chunk is shipped is real and must not be closed."""
        meta = {"sensor": "GR06", "tick_s": 2 / 128e6,
                "chunks": self._chunks(200, 100, 100_000, gap_s=5.0)}
        df = read_u32(self._write("noise-GR06-gap.u32",
                                  np.full(200, 450, dtype=np.uint32), meta))
        t = df["time"].to_numpy()
        dt = np.diff(t)
        # 99 small steps inside each chunk, one large one across the seam
        self.assertGreater(dt.max(), 4.0)
        self.assertLess(np.median(dt), 0.01)

    # ----------------------------------------------------------- no sidecar
    def test_without_a_sidecar_counts_are_reported_as_counts(self):
        """No metadata means no units - say so rather than invent a scale."""
        p = self._write("bare.u32", np.arange(16, dtype=np.uint32))
        df = read_u32(p)
        self.assertEqual(list(df.columns), ["sample", "counts"])
        self.assertEqual(df["counts"].iloc[5], 5.0)

    def test_corrupt_sidecar_is_ignored_rather_than_raising(self):
        p = self._write("bad.u32", np.arange(8, dtype=np.uint32))
        with open(self._path("bad.meta.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        df = read_u32(p)
        self.assertEqual(list(df.columns), ["sample", "counts"])

    # ------------------------------------------------------- WaveFile wiring
    def test_wavefile_dispatches_on_the_extension(self):
        meta = {"sensor": "GR06", "tick_s": 2 / 128e6,
                "chunks": self._chunks(50, 50, 50_000)}
        p = self._write("noise-GR06-wf.u32",
                        np.full(50, 450, dtype=np.uint32), meta)
        wf = WaveFile(p, "time")
        df = wf.df if hasattr(wf, "df") else None
        if df is None:                       # lazy WaveFiles load on demand
            df = wf._read_file()
        self.assertIn("GR06_width_ns", list(df.columns))


if __name__ == "__main__":
    unittest.main()
