#!/usr/bin/env python3
"""Tests for building a DataFrame from a JSON REST API (``source:`` block).

The fake service below is deliberately generic -- weather stations
reporting a value per frequency -- and mirrors the shape a FastAPI
service usually has: a listing endpoint wrapping records in an
envelope, and a per-item endpoint that has to be called once per row.
"""

import json
import http.server
import os
import textwrap
import threading
import unittest
import urllib.parse

import yaml

from cicwave.apisource import (
    DEFAULT_MAX_REQUESTS, fetch_dataframe, has_source, load_flat_frame,
)


#- Two series, each with three points, plus one series of a kind the
#- specs below filter out. The per-series endpoint echoes the query
#- parameters back so a test can assert templating actually happened.
_SERIES = [
    {"id": "s1", "unit": "degC", "sensor": "STATION_N04",
     "setup": "MODE=fast;RANGE=hi", "kind": "temperature"},
    {"id": "s2", "unit": "degC", "sensor": "STATION_N07",
     "setup": "MODE=slow;RANGE=lo", "kind": "temperature"},
    {"id": "s3", "unit": "pct", "sensor": "STATION_N09",
     "setup": "MODE=fast;RANGE=hi", "kind": "humidity"},
]

_POINTS = {
    "s1": [{"x": 100.0, "value": -1.0}, {"x": 200.0, "value": -2.0}],
    "s2": [{"x": 100.0, "value": -3.0}, {"x": 200.0, "value": -4.0}],
    "s3": [{"x": 100.0, "value": 50.0}],
}


class _Handler(http.server.BaseHTTPRequestHandler):
    generation = "gen-1"
    #- Flipped by a test to simulate the service rebuilding mid-pull.
    generation_flips_after = None
    request_count = 0
    seen_auth = []

    def do_GET(self):
        type(self).request_count += 1
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        type(self).seen_auth.append(self.headers.get("Authorization"))

        gen = type(self).generation
        flip = type(self).generation_flips_after
        if flip is not None and type(self).request_count > flip:
            gen = "gen-2"

        if parsed.path == "/api/series":
            self._send({"count": len(_SERIES), "rows": _SERIES}, gen)
        elif parsed.path.startswith("/api/series/"):
            sid = parsed.path.rsplit("/", 2)[-2]
            points = [dict(p) for p in _POINTS.get(sid, [])]
            for p in points:
                p["echoed_unit"] = query.get("unit", [""])[0]
            self._send({"points": points}, gen)
        elif parsed.path == "/api/flat":
            self._send(
                [{"x": 1.0, "value": 10.0, "sensor": "A"},
                 {"x": 2.0, "value": 20.0, "sensor": "A"}], gen)
        elif parsed.path == "/api/not-json":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"just some text")
        else:
            self.send_response(404)
            self.end_headers()

    def _send(self, payload, generation):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Data-Generation", generation)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _ApiTestCase(unittest.TestCase):
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
        _Handler.request_count = 0
        _Handler.generation_flips_after = None
        _Handler.seen_auth = []

    def spec(self, body, **fmt):
        """Parse an indented YAML fragment, substituting {base}."""
        return yaml.safe_load(textwrap.dedent(body).format(
            base=self.base, **fmt))


class TestSingleRequest(_ApiTestCase):
    def test_shorthand_single_url(self):
        spec = self.spec("""
            source:
              url: {base}/api/flat
            index: sensor
            columns: x
            values: value
        """)
        df = fetch_dataframe(spec)
        self.assertEqual(list(df.columns), ["x", "value", "sensor"])
        self.assertEqual(len(df), 2)

    def test_records_path_into_envelope(self):
        spec = self.spec("""
            source:
              base_url: {base}
              requests:
                - path: /api/series
                  records: rows
        """)
        df = fetch_dataframe(spec)
        self.assertEqual(len(df), 3)
        self.assertIn("sensor", df.columns)

    def test_where_filters_records(self):
        spec = self.spec("""
            source:
              base_url: {base}
              requests:
                - path: /api/series
                  records: rows
                  where: {{kind: temperature}}
        """)
        df = fetch_dataframe(spec)
        self.assertEqual(sorted(df["id"]), ["s1", "s2"])

    def test_keep_limits_columns(self):
        spec = self.spec("""
            source:
              base_url: {base}
              requests:
                - path: /api/series
                  records: rows
                  keep: [id, sensor]
        """)
        df = fetch_dataframe(spec)
        self.assertEqual(sorted(df.columns), ["id", "sensor"])

    def test_missing_records_path_names_available_keys(self):
        spec = self.spec("""
            source:
              base_url: {base}
              requests:
                - path: /api/series
                  records: nope
        """)
        with self.assertRaises(ValueError) as cm:
            fetch_dataframe(spec)
        self.assertIn("available keys", str(cm.exception))
        self.assertIn("rows", str(cm.exception))

    def test_non_json_response_is_a_clear_error(self):
        spec = self.spec("""
            source:
              url: {base}/api/not-json
        """)
        with self.assertRaises(ValueError) as cm:
            fetch_dataframe(spec)
        self.assertIn("not JSON", str(cm.exception))

    def test_unknown_spec_key_is_rejected(self):
        spec = self.spec("""
            source:
              url: {base}/api/flat
              recordz: rows
        """)
        with self.assertRaises(ValueError) as cm:
            fetch_dataframe(spec)
        self.assertIn("recordz", str(cm.exception))


class TestFanOut(_ApiTestCase):
    """One request per row of an earlier request -- the case a plain URL
    source cannot express."""

    FANOUT = """
        source:
          base_url: {base}
          requests:
            - name: series
              path: /api/series
              records: rows
              where: {{kind: temperature}}
            - path: /api/series/{{id}}/points
              for_each: series
              params: {{unit: "{{unit}}"}}
              records: points
              merge: [sensor, setup]
              headers_as_columns:
                generation: X-Data-Generation
        index: sensor
        columns: x
        values: value
    """

    def test_fan_out_produces_child_rows_with_parent_columns(self):
        df = fetch_dataframe(self.spec(self.FANOUT))
        self.assertEqual(len(df), 4)
        self.assertEqual(sorted(df["sensor"].unique()),
                         ["STATION_N04", "STATION_N07"])
        self.assertEqual(set(df["generation"]), {"gen-1"})
        # 1 listing request + 1 per surviving series (s3 filtered out).
        self.assertEqual(_Handler.request_count, 3)

    def test_params_are_templated_from_the_parent_row(self):
        df = fetch_dataframe(self.spec(self.FANOUT))
        self.assertEqual(set(df["echoed_unit"]), {"degC"})

    def test_unknown_template_field_names_what_is_available(self):
        spec = self.spec("""
            source:
              base_url: {base}
              requests:
                - name: series
                  path: /api/series
                  records: rows
                - path: /api/series/{{nosuch}}/points
                  for_each: series
                  records: points
        """)
        with self.assertRaises(ValueError) as cm:
            fetch_dataframe(spec)
        self.assertIn("nosuch", str(cm.exception))
        self.assertIn("sensor", str(cm.exception))

    def test_for_each_must_name_an_earlier_request(self):
        spec = self.spec("""
            source:
              base_url: {base}
              requests:
                - path: /api/series/{{id}}/points
                  for_each: nowhere
                  records: points
        """)
        with self.assertRaises(ValueError) as cm:
            fetch_dataframe(spec)
        self.assertIn("for_each", str(cm.exception))

    def test_max_requests_caps_the_fan_out(self):
        spec = self.spec(self.FANOUT)
        spec["source"]["max_requests"] = 2
        with self.assertRaises(ValueError) as cm:
            fetch_dataframe(spec)
        self.assertIn("max_requests", str(cm.exception))

    def test_default_request_budget_is_bounded(self):
        self.assertLessEqual(DEFAULT_MAX_REQUESTS, 1000)

    def test_generation_change_mid_pull_is_refused(self):
        _Handler.generation_flips_after = 1
        spec = self.spec(self.FANOUT)
        spec["source"]["require_consistent_headers"] = ["X-Data-Generation"]
        with self.assertRaises(ValueError) as cm:
            fetch_dataframe(spec)
        self.assertIn("X-Data-Generation", str(cm.exception))
        self.assertIn("two versions", str(cm.exception))


class TestDeriveAndRename(_ApiTestCase):
    def test_rename_derive_regex_kv_and_split(self):
        spec = self.spec("""
            source:
              base_url: {base}
              requests:
                - path: /api/series
                  records: rows
              rename:
                sensor: station
              derive:
                station_number: {{from: station, regex: "(\\\\d+)$", type: int}}
                mode: {{from: setup, kv: MODE}}
                tail: {{from: station, split: "_", index: -1}}
        """)
        df = fetch_dataframe(spec)
        self.assertIn("station", df.columns)
        self.assertNotIn("sensor", df.columns)
        self.assertEqual(list(df["station_number"]), [4, 7, 9])
        self.assertEqual(list(df["mode"]), ["fast", "slow", "fast"])
        self.assertEqual(list(df["tail"]), ["N04", "N07", "N09"])

    def test_rename_of_absent_column_is_an_error(self):
        spec = self.spec("""
            source:
              base_url: {base}
              requests:
                - path: /api/series
                  records: rows
              rename: {{nosuch: x}}
        """)
        with self.assertRaises(ValueError) as cm:
            fetch_dataframe(spec)
        self.assertIn("nosuch", str(cm.exception))

    def test_filter_selects_on_a_derived_column(self):
        # The point of `filter`: `where` runs against raw records, so it
        # cannot see a column that only exists after `derive`.
        spec = self.spec("""
            source:
              base_url: {base}
              requests:
                - path: /api/series
                  records: rows
              derive:
                mode: {{from: setup, kv: MODE}}
              filter: {{mode: slow}}
        """)
        df = fetch_dataframe(spec)
        self.assertEqual(list(df["id"]), ["s2"])

    def test_filter_accepts_a_list_of_values(self):
        spec = self.spec("""
            source:
              base_url: {base}
              requests:
                - path: /api/series
                  records: rows
              filter: {{id: [s1, s3]}}
        """)
        df = fetch_dataframe(spec)
        self.assertEqual(list(df["id"]), ["s1", "s3"])

    def test_filter_matching_nothing_is_an_error(self):
        spec = self.spec("""
            source:
              base_url: {base}
              requests:
                - path: /api/series
                  records: rows
              filter: {{kind: nosuchkind}}
        """)
        with self.assertRaises(ValueError) as cm:
            fetch_dataframe(spec)
        self.assertIn("matched none", str(cm.exception))

    def test_filter_on_unknown_column_is_an_error(self):
        spec = self.spec("""
            source:
              base_url: {base}
              requests:
                - path: /api/series
                  records: rows
              filter: {{nosuchcolumn: 1}}
        """)
        with self.assertRaises(ValueError) as cm:
            fetch_dataframe(spec)
        self.assertIn("nosuchcolumn", str(cm.exception))

    def test_derive_needs_a_known_operation(self):
        spec = self.spec("""
            source:
              base_url: {base}
              requests:
                - path: /api/series
                  records: rows
              derive: {{oops: {{from: sensor}}}}
        """)
        with self.assertRaises(ValueError) as cm:
            fetch_dataframe(spec)
        self.assertIn("regex", str(cm.exception))


class TestHeadersAndSecrets(_ApiTestCase):
    def test_header_value_comes_from_the_environment(self):
        os.environ["CICWAVE_TEST_TOKEN"] = "sekret"
        try:
            spec = self.spec("""
                source:
                  url: {base}/api/flat
                  headers: {{Authorization: "Bearer ${{CICWAVE_TEST_TOKEN}}"}}
            """)
            fetch_dataframe(spec)
        finally:
            del os.environ["CICWAVE_TEST_TOKEN"]
        self.assertEqual(_Handler.seen_auth[-1], "Bearer sekret")

    def test_unset_environment_variable_is_reported(self):
        spec = self.spec("""
            source:
              url: {base}/api/flat
              headers: {{Authorization: "Bearer ${{CICWAVE_NOT_SET_ANYWHERE}}"}}
        """)
        with self.assertRaises(ValueError) as cm:
            fetch_dataframe(spec)
        self.assertIn("CICWAVE_NOT_SET_ANYWHERE", str(cm.exception))

    def test_link_local_host_is_refused(self):
        spec = {"source": {"url": "http://169.254.169.254/latest/meta-data/"}}
        with self.assertRaises(ValueError) as cm:
            fetch_dataframe(spec)
        self.assertIn("link-local", str(cm.exception))


class _SpecOnDiskTestCase(_ApiTestCase):
    """Writes a real spec file pointing at the fake service."""

    SPEC = """
        source:
          base_url: {base}
          requests:
            - name: series
              path: /api/series
              records: rows
              where: {{kind: temperature}}
            - path: /api/series/{{id}}/points
              for_each: series
              records: points
              merge: [sensor]
          derive:
            station: {{from: sensor, split: "_", index: -1}}
        index: station
        columns: x
        values: value
    """

    def setUp(self):
        super().setUp()
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("CICSIM_USE_OPENGL", "0")
        import tempfile

        self.tmp = tempfile.mkdtemp(prefix="cicwave-api-cli-")
        self.spec_path = os.path.join(self.tmp, "api.yaml")
        with open(self.spec_path, "w") as fh:
            yaml.safe_dump(self.spec(self.SPEC), fh)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _invoke(self, args):
        from click.testing import CliRunner
        from cicwave.cli import main

        runner = (CliRunner(mix_stderr=True)
                  if "mix_stderr" in CliRunner.__init__.__code__.co_varnames
                  else CliRunner())
        return runner.invoke(main, args)


class TestCliEndToEnd(_SpecOnDiskTestCase):
    """The whole path: spec file in, plotted data out, no data file."""

    def test_pivot_info_needs_no_data_file(self):
        result = self._invoke([self.spec_path, "--pivot-info"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("index: station", result.output)
        self.assertIn("N04", result.output)

    def test_export_data_from_the_api(self):
        import pandas as pd

        out = os.path.join(self.tmp, "out.csv")
        result = self._invoke([self.spec_path, "--export-data", out])
        self.assertEqual(result.exit_code, 0, result.output)
        df = pd.read_csv(out)
        self.assertTrue(
            any(c.startswith("N04") for c in df.columns), list(df.columns))
        self.assertTrue(
            any(c.startswith("N07") for c in df.columns), list(df.columns))


class TestSessionRoundTrip(_SpecOnDiskTestCase):
    """A session built from an API source has no data file to point at,
    so the file entry names the spec instead."""

    def test_session_saves_and_reloads_a_source_entry(self):
        try:
            from cicwave.wave_pg import CmdWavePg
        except Exception as e:  # pragma: no cover - optional GUI deps
            self.skipTest("pyqtgraph / PySide6 not installed (%s)" % e)

        session = {
            "files": [{"source": self.spec_path}],
            "plots": [{"waves": [{"file": 0, "name": "N04",
                                  "style": "Lines"}]}],
        }
        session_path = os.path.join(self.tmp, "s.cicwave.yaml")
        with open(session_path, "w") as fh:
            yaml.safe_dump(session, fh)

        c = CmdWavePg("x")
        c.win.applySession(session_path)
        self.assertEqual(len(c.win.browser.files), 1)

        rebuilt = c.win._build_session()
        self.assertEqual(rebuilt["files"][0].get("source"), self.spec_path)
        self.assertNotIn("path", rebuilt["files"][0])


class TestGuiOpen(_SpecOnDiskTestCase):
    """Opening a spec in the GUI (File -> Open, or drag-and-drop) has to
    fetch it, not hand the YAML to the ngspice raw reader."""

    def _window(self):
        try:
            from cicwave.wave_pg import CmdWavePg
        except Exception as e:  # pragma: no cover - optional GUI deps
            self.skipTest("pyqtgraph / PySide6 not installed (%s)" % e)
        return CmdWavePg("x").win

    def test_open_path_fetches_a_source_spec(self):
        win = self._window()
        win.openPath(self.spec_path)
        self.assertEqual(len(win.browser.files), 1)
        wf = next(iter(win.browser.files.values()))
        self.assertTrue(any(str(c).startswith("N04") for c in wf.df.columns),
                        list(wf.df.columns))

    def test_open_path_still_opens_a_plain_data_file(self):
        csv = os.path.join(self.tmp, "d.csv")
        with open(csv, "w") as fh:
            fh.write("time,v\n0,1\n1,2\n")
        win = self._window()
        win.openPath(csv)
        self.assertEqual(len(win.browser.files), 1)

    def test_unreadable_file_says_what_went_wrong(self):
        # Regression: a YAML (or any unknown extension) fell through to
        # the ngspice reader, which returns None, and the failure only
        # surfaced later as "'NoneType' object has no attribute 'columns'".
        from cicwave.wavefiles import WaveFile

        plain = os.path.join(self.tmp, "notaspec.yaml")
        with open(plain, "w") as fh:
            fh.write("index: a\ncolumns: b\nvalues: c\n")
        with self.assertRaises(ValueError) as cm:
            WaveFile(plain, xaxis="").df
        message = str(cm.exception)
        self.assertIn("could not read", message)
        self.assertIn("notaspec.yaml", message)
        self.assertNotIn("NoneType", message)


class TestLoadFlatFrame(_ApiTestCase):
    def test_has_source_detects_the_block(self):
        self.assertTrue(has_source({"source": {"url": "http://x/y"}}))
        self.assertFalse(has_source({"index": "a"}))
        self.assertFalse(has_source(None))

    def test_load_flat_frame_prefers_the_source(self):
        spec = self.spec("""
            source:
              url: {base}/api/flat
        """)
        df = load_flat_frame(spec, file=None)
        self.assertEqual(len(df), 2)

    def test_load_flat_frame_without_source_or_file_explains_itself(self):
        with self.assertRaises(ValueError) as cm:
            load_flat_frame({"index": "a"}, file=None)
        self.assertIn("source:", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
