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
