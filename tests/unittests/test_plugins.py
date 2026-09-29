"""Tests for the plugin API (cicwave.plugins)."""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd

from cicwave import plugins
from cicwave.wavefiles import WaveFile


class _EntryPoint:
    """Stands in for an importlib.metadata entry point."""

    def __init__(self, name, register):
        self.name = name
        self._register = register

    def load(self):
        return self._register


class PluginApiTest(unittest.TestCase):

    def setUp(self):
        plugins._reset()
        self.tmp = tempfile.mkdtemp(prefix="cicwave-plugins-")

    def tearDown(self):
        plugins._reset()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _load(self, *register_fns):
        return plugins.load_plugins([
            _EntryPoint("p%d" % i, fn) for i, fn in enumerate(register_fns)])

    def test_reader_matches_longest_suffix_and_feeds_wavefile(self):
        def register(api):
            api.register_reader(".bin", lambda p: pd.DataFrame({"a": [0]}))
            api.register_reader("foo.bin", lambda p: pd.DataFrame(
                {"time": [0.0, 1.0], "v": [1.0, 2.0]}))
        self._load(register)
        path = os.path.join(self.tmp, "x.foo.bin")
        open(path, "wb").close()
        wf = WaveFile(path, xaxis="")
        self.assertEqual(list(wf.df.columns), ["time", "v"])

    def test_annotator_adds_sections_to_a_sigmf_recording(self):
        base = os.path.join(self.tmp, "rec")
        with open(base + ".sigmf-meta", "w", encoding="utf-8") as fh:
            json.dump({"global": {"core:datatype": "cf32_le",
                                  "core:version": "1.0.0"},
                       "captures": [], "annotations": [
                           {"core:sample_start": 0, "core:sample_count": 8,
                            "core:label": "burst"}]}, fh)
        np.ones(8, "<c8").tofile(base + ".sigmf-data")

        def annotate(df, path):
            anns = plugins.annotations(df)
            for a in [a for a in anns if a["core:label"] == "burst"]:
                anns.append({"core:sample_start": a["core:sample_start"] + 4,
                             "core:sample_count": 4, "core:label": "payload"})
        self._load(lambda api: api.register_annotator(annotate))
        anns = WaveFile(base + ".sigmf-meta", xaxis="").df.attrs[
            "cicwave_annotations"]
        self.assertEqual([a["core:label"] for a in anns],
                         ["burst", "payload"])

    def test_failing_plugin_is_skipped_and_logged(self):
        def broken(api):
            raise RuntimeError("boom")
        with self.assertLogs("cicwave.plugins", level="WARNING") as logs:
            names = self._load(broken,
                               lambda api: api.register_analysis("A", None))
        self.assertEqual(names, ["p1"])
        self.assertIn("boom", logs.output[0])
        self.assertEqual([lb for lb, _ in plugins.analyses()], ["A"])

    def test_failing_annotator_does_not_break_loading(self):
        def bad(df, path):
            raise ValueError("nope")
        self._load(lambda api: api.register_annotator(bad))
        path = os.path.join(self.tmp, "t.csv")
        pd.DataFrame({"time": [0, 1], "v": [2, 3]}).to_csv(path, index=False)
        with self.assertLogs("cicwave.plugins", level="WARNING"):
            df = WaveFile(path, xaxis="").df
        self.assertEqual(list(df["v"]), [2, 3])

    def test_constellation_preset_fields_are_checked(self):
        def register(api):
            api.register_constellation_preset(
                "QPSK 1M", {"symbol_rate": 1e6, "annotation": "payload"})
        self._load(register)
        self.assertEqual(plugins.constellation_presets()["QPSK 1M"],
                         {"symbol_rate": 1e6, "annotation": "payload"})
        api = plugins.PluginAPI("t")
        with self.assertRaises(ValueError):
            api.register_constellation_preset("bad", {"symbolrate": 1})

    def test_plugin_info_lists_registrations_and_failures(self):
        def register(api):
            api.set_description("Decodes things")
            api.register_reader(".mybin", lambda p: None)
            api.register_analysis("Peak / average", lambda w, wave: None)
            api.register_constellation_preset("QPSK", {"symbol_rate": 1e6})

        def broken(api):
            raise RuntimeError("boom")
        with self.assertLogs("cicwave.plugins", level="WARNING"):
            self._load(register, broken)
        info = {i["name"]: i for i in plugins.plugin_info()}
        self.assertEqual(info["p0"]["description"], "Decodes things")
        self.assertEqual(info["p0"]["readers"], [".mybin"])
        self.assertEqual(info["p0"]["analyses"], ["Peak / average"])
        self.assertEqual(info["p0"]["presets"], ["QPSK"])
        self.assertIsNone(info["p0"]["error"])
        self.assertEqual(info["p1"]["error"], "boom")
        text = plugins.describe_plugins()
        self.assertIn("Decodes things", text)
        self.assertIn("FAILED TO LOAD: boom", text)

    def test_describe_with_no_plugins(self):
        self._load()
        with mock.patch.dict(os.environ, {"CICWAVE_PLUGINS": ""}):
            self.assertIn("No plugins installed", plugins.describe_plugins())
        with mock.patch.dict(os.environ, {"CICWAVE_PLUGINS": "0"}):
            self.assertIn("discovery is off", plugins.describe_plugins())

    def test_no_plugins_installed_is_a_no_op(self):
        self.assertEqual(self._load(), [])
        self.assertIsNone(plugins.find_reader("x.csv"))
        self.assertEqual(plugins.analyses(), [])


_EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "examples",
                        "cicwave-uart")


class UartExampleTest(unittest.TestCase):
    """The example plugin in examples/cicwave-uart keeps working."""

    def setUp(self):
        import sys
        sys.path.insert(0, _EXAMPLE)
        self.addCleanup(sys.path.remove, _EXAMPLE)
        from cicwave_uart import uart
        self.uart = uart

    def test_decodes_a_noisy_capture_at_the_estimated_baud(self):
        msg = b"Hello, cicwave!\x00\xff"
        t, v = self.uart.encode(msg, baud=115200, fs=10e6)
        v = 3.3 * v + np.random.default_rng(1).normal(0, 0.1, v.size)
        baud = self.uart.estimate_baud(t, v)
        self.assertAlmostEqual(baud / 115200, 1.0, delta=0.02)
        frames = self.uart.decode(t, v, baud)
        self.assertEqual(bytes(f.value for f in frames), msg)
        self.assertTrue(all(f.ok for f in frames))

    def test_registers_its_analysis(self):
        plugins._reset()
        self.addCleanup(plugins._reset)
        import cicwave_uart
        plugins.load_plugins([_EntryPoint("uart", cicwave_uart.register)])
        (info,) = plugins.plugin_info()
        self.assertEqual(info["analyses"], ["Decode UART..."])
        self.assertIn("UART", info["description"])


_SMITH = os.path.join(os.path.dirname(__file__), "..", "..", "examples",
                      "cicwave-smith")


class SmithExampleTest(unittest.TestCase):
    """The example plugin in examples/cicwave-smith keeps working."""

    def setUp(self):
        import sys
        sys.path.insert(0, _SMITH)
        self.addCleanup(sys.path.remove, _SMITH)
        from cicwave_smith import smith, touchstone
        self.smith, self.ts = smith, touchstone
        self.tmp = tempfile.mkdtemp(prefix="cicwave-smith-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _write(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_reads_every_format_to_the_same_values(self):
        g = 0.5 * np.exp(1j * np.deg2rad(-60))
        files = {
            "ma.s1p": "# GHZ S MA R 50\n1.0 0.5 -60\n",
            "db.s1p": "# GHZ S DB R 50\n1.0 %.12f -60\n"
                      % (20 * np.log10(0.5)),
            "ri.s1p": "! comment\n# MHz S RI R 75\n1000 %.12f %.12f\n"
                      % (g.real, g.imag),
        }
        for name, text in files.items():
            df = self.ts.read(self._write(name, text))
            self.assertAlmostEqual(df["frequency"][0], 1e9)
            self.assertAlmostEqual(df["S11"][0], g, places=9, msg=name)
        self.assertEqual(df.attrs["touchstone"]["z0"], 75.0)

    def test_two_port_order_and_noise_block(self):
        path = self._write("amp.s2p", (
            "# HZ S RI R 50\n"
            "1e9 1 0  2 0  3 0  4 0\n"
            "2e9 5 0  6 0\n 7 0  8 0\n"      # a record wrapped over lines
            "! noise parameters\n"
            "1e9 1.5 0.3 20 0.4\n"
            "1.5e9 1.6 0.3 25 0.4\n"
            "2e9 1.7 0.3 30 0.4\n"))
        df = self.ts.read(path)
        self.assertEqual(len(df), 2)
        self.assertEqual(list(np.real(df["S21"])), [2, 6])  # 2nd is S21
        self.assertEqual(list(np.real(df["S12"])), [3, 7])

    def test_reader_feeds_wavefile_through_the_plugin(self):
        import cicwave_smith
        plugins._reset()
        self.addCleanup(plugins._reset)
        plugins.load_plugins([_EntryPoint("smith", cicwave_smith.register)])
        path = self._write("a.s1p", "# GHZ S MA R 50\n1 0.1 0\n2 0.2 90\n")
        df = WaveFile(path, xaxis="").df
        self.assertEqual(list(df.columns),
                         ["frequency", "S11", "S11_dB", "S11_deg"])
        (info,) = plugins.plugin_info()
        self.assertIn(".s2p", info["readers"])
        self.assertEqual(info["analyses"], ["Smith chart"])

    def test_chart_geometry_and_match_figures(self):
        for _label, pts in self.smith.grid_lines():
            self.assertTrue(np.all(np.abs(pts) <= 1 + 1e-9))
        z = np.array([50, 25 + 10j, 100 - 40j])
        np.testing.assert_allclose(
            self.smith.z_from_gamma(self.smith.gamma_from_z(z)), z)
        g = self.smith.gamma_from_z(np.array([100, 75, 30]))
        best = self.smith.match_summary(np.array([1e9, 2e9, 3e9]), g)
        self.assertEqual(best["frequency"], 2e9)          # 75 ohm: |g| = 0.2
        self.assertAlmostEqual(best["vswr"], 1.5)
        self.assertAlmostEqual(best["return_loss_db"], 13.979, places=3)


if __name__ == "__main__":
    unittest.main()
