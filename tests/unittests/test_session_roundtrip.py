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


@unittest.skipUnless(HAVE_PG, "pyqtgraph / PySide6 not installed")
class SessionViewStateTest(unittest.TestCase):
    """Per-wave settings and plot view state must round-trip through
    Save/Load Session, not just file paths/pivot/format."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cicwave-session-view-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_per_wave_and_view_state_round_trip(self):
        df = pd.DataFrame({
            "time": [0, 1, 2, 3, 4],
            "code": [100, 200, 4095, 300, 50],
        })
        csv = os.path.join(self.tmp, "adc.csv")
        df.to_csv(csv, index=False)

        c = CmdWavePg("time")
        win = c.win
        win.browser.openFile(csv)
        wave = win._find_wave("code")
        wave.twos_width_bits = 12
        wave.reload()
        wave.show_as_digital = True
        wave.setDigitalFormat("dec")

        p = win.tab_widget.widget(win.tab_widget.count() - 1)
        p.show_wave(wave, style="Lines")
        p._set_cursor('a', 1.5)
        p._set_cursor('b', 3.0)
        p.plot.vb.setRange(xRange=[0, 4], yRange=[-100, 4200], padding=0)

        session = win._build_session()
        wd = session["plots"][0]["waves"][0]
        self.assertEqual(wd["twos_complement_bits"], 12)
        self.assertTrue(wd["digital"])
        self.assertEqual(wd["digital_format"], "dec")
        pd_ = session["plots"][0]
        self.assertEqual(pd_["xrange"], [0.0, 4.0])
        self.assertEqual(pd_["yrange"], [-100.0, 4200.0])
        self.assertAlmostEqual(pd_["cursor_a"], 1.5)
        self.assertAlmostEqual(pd_["cursor_b"], 3.0)

        session_path = os.path.join(self.tmp, "session.cicwave.yaml")
        with open(session_path, "w") as f:
            yaml.safe_dump(session, f)

        c2 = CmdWavePg("time")
        win2 = c2.win
        win2.applySession(session_path)

        wave2 = win2._find_wave("code")
        self.assertEqual(wave2.twos_width_bits, 12)
        self.assertTrue(wave2.show_as_digital)
        self.assertEqual(wave2.digital_format, "dec")
        # 4095 (12-bit unsigned) decodes to -1 signed; confirms the
        # decode was actually re-applied, not just the flag restored.
        self.assertAlmostEqual(list(wave2.y)[2], -1.0)

        p2 = win2.tab_widget.widget(win2.tab_widget.count() - 1)
        self.assertAlmostEqual(p2.cursor_a, 1.5)
        self.assertAlmostEqual(p2.cursor_b, 3.0)
        xr, yr = p2.plot.vb.viewRange()
        self.assertAlmostEqual(xr[0], 0.0)
        self.assertAlmostEqual(xr[1], 4.0)
        self.assertAlmostEqual(yr[0], -100.0)
        self.assertAlmostEqual(yr[1], 4200.0)

    def test_cursor_on_log_x_plot_round_trips_through_data_space(self):
        import numpy as np

        freq = np.logspace(3, 8, 100)
        gain = -20 * np.log10(freq / 1e6)
        csv = os.path.join(self.tmp, "ac.csv")
        pd.DataFrame({"frequency": freq, "gain": gain}).to_csv(
            csv, index=False)

        c = CmdWavePg(None)
        win = c.win
        win.browser.openFile(csv)
        wave = win._find_wave("gain")
        p = win.tab_widget.widget(win.tab_widget.count() - 1)
        p.show_wave(wave, style="Lines")
        self.assertTrue(p._is_logx())

        # Simulate a cursor placed at 1 MHz: pyqtgraph stores this as
        # log10(1e6) in view space.
        p._set_cursor('a', 6.0)

        session = win._build_session()
        # The session file must record the real frequency, not the
        # log10 view-space value, so it stays human-readable/portable.
        self.assertAlmostEqual(session["plots"][0]["cursor_a"], 1e6,
                               delta=1.0)

        session_path = os.path.join(self.tmp, "session.cicwave.yaml")
        with open(session_path, "w") as f:
            yaml.safe_dump(session, f)

        c2 = CmdWavePg(None)
        win2 = c2.win
        win2.applySession(session_path)
        p2 = win2.tab_widget.widget(win2.tab_widget.count() - 1)
        self.assertAlmostEqual(p2.cursor_a, 6.0, places=3)


if __name__ == "__main__":
    unittest.main()
