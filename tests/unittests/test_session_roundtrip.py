#!/usr/bin/env python3
"""Regression tests for session save/load round-tripping.

Covers two bugs found in review: applySession's pivot branch didn't
pass pivot_spec_path/original_path to openDataFrame (so re-saving a
loaded pivoted session silently dropped the pivot), and a URL's
--format override wasn't persisted in the session schema at all (so
reloading a session with an extension-less REST endpoint would fail
where the original load succeeded).
"""

import http.server
import json
import os
import tempfile
import threading
import unittest

import pandas as pd
import yaml

try:  # pragma: no cover - optional GUI deps
    from cicwave.wave_pg import CmdWavePg
    HAVE_PG = True
except Exception:
    HAVE_PG = False


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/rest-endpoint":
            body = json.dumps(
                [{"time": 0, "v": 1}, {"time": 1, "v": 2}]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


@unittest.skipUnless(HAVE_PG, "pyqtgraph / PySide6 not installed")
class SessionPivotProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cicwave-session-")
        df = pd.DataFrame({
            "Parameter": ["Gain", "Gain", "Gain", "Gain"],
            "Freq": [1, 2, 1, 2],
            "Value": [10, 20, 11, 21],
            "Temp": [27, 27, 85, 85],
        })
        self.data_csv = os.path.join(self.tmp, "data.csv")
        df.to_csv(self.data_csv, index=False)
        self.spec_yaml = os.path.join(self.tmp, "spec.yaml")
        with open(self.spec_yaml, "w") as f:
            yaml.safe_dump({
                "index": "Parameter", "columns": "Freq",
                "values": "Value", "conditions": ["Temp"],
            }, f)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pivot_provenance_survives_a_save_reload_save_cycle(self):
        from cicwave.pivot import load_spec, apply_pivot
        from cicwave.wavefiles import WaveFile

        c = CmdWavePg("Freq")
        win = c.win
        wf = WaveFile(self.data_csv, "Freq")
        spec = load_spec(self.spec_yaml)
        pivoted = apply_pivot(wf.df, spec)
        win.browser.openDataFrame(
            pivoted, "pivot(data.csv)",
            pivot_spec_path=os.path.abspath(self.spec_yaml),
            original_path=os.path.abspath(self.data_csv))

        session = win._build_session()
        entry = session["files"][0]
        self.assertEqual(entry["path"], os.path.abspath(self.data_csv))
        self.assertEqual(entry["pivot"], os.path.abspath(self.spec_yaml))

        session_path = os.path.join(self.tmp, "session.cicwave.yaml")
        with open(session_path, "w") as f:
            yaml.safe_dump(session, f)

        # Reload into a fresh window (mirrors applySession's real path
        # resolution, not the shortcut used to build the fixture above).
        c2 = CmdWavePg("Freq")
        win2 = c2.win
        win2.applySession(session_path)
        self.assertEqual(len(win2.browser.files), 1)

        # The bug: without pivot_spec_path/original_path threaded through
        # applySession's openDataFrame call, re-saving here would drop
        # the 'pivot' key and point 'path' at the virtual "pivot(...)"
        # name instead of the real source file.
        session2 = win2._build_session()
        entry2 = session2["files"][0]
        self.assertEqual(entry2["path"], os.path.abspath(self.data_csv))
        self.assertEqual(entry2["pivot"], os.path.abspath(self.spec_yaml))


@unittest.skipUnless(HAVE_PG, "pyqtgraph / PySide6 not installed")
class SessionFormatPersistenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cicwave-session-fmt-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_format_override_survives_session_save_and_reload(self):
        url = self.base + "/rest-endpoint"

        c = CmdWavePg("time")
        win = c.win
        win.browser.openFile(url, fmt="json")

        session = win._build_session()
        entry = session["files"][0]
        self.assertEqual(entry["path"], url)
        self.assertEqual(entry["format"], "json")

        session_path = os.path.join(self.tmp, "session.cicwave.yaml")
        with open(session_path, "w") as f:
            yaml.safe_dump(session, f)

        # Without persisting 'format', this reload would hit "could not
        # determine a file format" since the endpoint has no extension
        # and a non-informative Content-Type.
        c2 = CmdWavePg("time")
        win2 = c2.win
        win2.applySession(session_path)
        self.assertEqual(len(win2.browser.files), 1)
        wf2 = list(win2.browser.files.values())[0]
        self.assertEqual(list(wf2.df.columns), ["time", "v"])


if __name__ == "__main__":
    unittest.main()
