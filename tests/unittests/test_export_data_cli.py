#!/usr/bin/env python3
"""End-to-end tests for --export-data (headless CLI data export).

Complements test_export_data.py (which tests _export_data_to in
isolation) by exercising the real CmdWavePg.exportAndExit path used by
`cicwave --session ... --export-data out.csv` and
`cicwave data.csv --pivot spec.yaml --export-data out.csv`.
"""

import os
import tempfile
import unittest

import pandas as pd
import yaml

try:  # pragma: no cover - optional GUI deps
    from cicwave.wave_pg import CmdWavePg
    HAVE_PG = True
except Exception:
    HAVE_PG = False


@unittest.skipUnless(HAVE_PG, "pyqtgraph / PySide6 not installed")
class ExportDataCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cicwave-export-cli-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _path(self, name):
        return os.path.join(self.tmp, name)

    def test_session_overlay_uses_each_waves_file_index(self):
        """The same signal in two files must plot from both files."""
        paths = []
        for name, level in (("a.csv", 1.0), ("b.csv", 2.0)):
            df = pd.DataFrame({"time": [0.0, 1.0], "v(out)": [level, level]})
            paths.append(self._path(name))
            df.to_csv(paths[-1], index=False)
        session = {
            "files": [{"path": p} for p in paths],
            "plots": [{"name": "Overlay", "waves": [
                {"file": 1, "name": "v(out)"},
                {"file": 0, "name": "v(out)"},
            ]}],
        }
        spath = self._path("s.cicwave.yaml")
        with open(spath, "w") as fh:
            yaml.safe_dump(session, fh)
        c = CmdWavePg("time")
        c.win.applySession(spath)
        out = self._path("out.csv")
        c.exportAndExit(None, out)
        data = pd.read_csv(out)
        levels = sorted(data[col].iloc[0] for col in data.columns
                        if col.startswith("v(out)"))
        self.assertEqual(levels, [1.0, 2.0])

    def _histogram_session(self, plot_extra=None):
        rows = [20.0, 20.5, 21.0, 21.5, 22.0]
        csv = self._path("mc.csv")
        pd.DataFrame({"n": range(5), "ibn": rows}).to_csv(csv, index=False)
        plot = {"name": "Dist", "analysis": "histogram",
                "waves": [{"file": 0, "name": "ibn"}]}
        plot.update(plot_extra or {})
        spath = self._path("h.cicwave.yaml")
        with open(spath, "w") as fh:
            yaml.safe_dump({"files": [{"path": csv}], "plots": [plot]}, fh)
        return spath

    def test_session_histogram_exports_an_image(self):
        c = CmdWavePg("n")
        c.win.applySession(self._histogram_session({"unit": "uA"}))
        out = self._path("hist.png")
        c.exportAndExit(out)
        self.assertGreater(os.path.getsize(out), 1000)

    def test_session_histogram_survives_save_and_reload(self):
        c = CmdWavePg("n")
        c.win.applySession(self._histogram_session(
            {"unit": "uA", "title": "MC"}))
        saved = c.win._build_session()["plots"]
        self.assertEqual(saved, [{
            "name": "Dist", "analysis": "histogram",
            "waves": [{"file": 0, "name": "ibn"}],
            "unit": "uA", "title": "MC"}])

    def test_histogram_values_get_one_prefix(self):
        from cicwave.wave_pg import _hist_value
        self.assertEqual(_hist_value(0.8346, "uA"), "0.8346 uA")
        self.assertEqual(_hist_value(0.8346), "0.8346")
        self.assertIn("nA", _hist_value(8.3e-7, "A"))
        self.assertIn("m", _hist_value(0.83, "m"))

    def test_export_data_writes_plotted_session_data(self):
        df = pd.DataFrame({
            "time": [0.0, 1.0, 2.0],
            "v(out)": [0.1, 0.2, 0.3],
        })
        csv = self._path("data.csv")
        df.to_csv(csv, index=False)

        session = {
            "files": [{"path": csv}],
            "plots": [{
                "name": "p",
                "waves": [{"file": 0, "name": "v(out)", "style": "Lines"}],
            }],
        }
        session_path = self._path("s.cicwave.yaml")
        with open(session_path, "w") as f:
            yaml.safe_dump(session, f)

        c = CmdWavePg("time")
        c.win.applySession(session_path)
        out = self._path("exported.csv")
        c.exportAndExit(None, out)

        result = pd.read_csv(out)
        self.assertIn("v(out) (V)", result.columns)
        self.assertEqual(list(result["v(out) (V)"]), [0.1, 0.2, 0.3])

    def test_export_data_and_image_together(self):
        df = pd.DataFrame({"time": [0.0, 1.0], "v(out)": [1.0, 2.0]})
        csv = self._path("data.csv")
        df.to_csv(csv, index=False)
        session = {
            "files": [{"path": csv}],
            "plots": [{
                "waves": [{"file": 0, "name": "v(out)", "style": "Lines"}],
            }],
        }
        session_path = self._path("s.cicwave.yaml")
        with open(session_path, "w") as f:
            yaml.safe_dump(session, f)

        c = CmdWavePg("time")
        c.win.applySession(session_path)
        img = self._path("plot.svg")
        data = self._path("plot.csv")
        c.exportAndExit(img, data)

        self.assertTrue(os.path.exists(img))
        self.assertTrue(os.path.exists(data))
        self.assertGreater(os.path.getsize(img), 0)
        self.assertGreater(os.path.getsize(data), 0)


if __name__ == "__main__":
    unittest.main()
