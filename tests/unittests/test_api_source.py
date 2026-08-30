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
import shutil
import textwrap
import threading
import unittest
import urllib.parse

import yaml

from cicwave.apisource import (
    DEFAULT_MAX_REQUESTS, LazyCatalog, fetch_dataframe, has_catalog,
    has_source, load_flat_frame,
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

#- A second view of the same sweeps, carrying the probe each point came
#- from: one request returning several signals, the way a real sweep
#- endpoint returns every board it measured.
_PROBE_POINTS = {
    sid: [dict(p, probe=probe, value=p["value"] - offset)
          for probe, offset in (("P1", 0.0), ("P2", 0.5))
          for p in points]
    for sid, points in _POINTS.items()
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
        elif parsed.path.endswith("/probes"):
            sid = parsed.path.rsplit("/", 2)[-2]
            #- Envelope carries the axis and unit, the way a real sweep
            #- endpoint describes what it measured.
            self._send({"points": _PROBE_POINTS.get(sid, []),
                        "axis": "frequency", "x_label": "MHz",
                        "unit": "degC"}, gen)
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
        elif parsed.path == "/spec.yaml":
            #- The service publishing its own pivot spec. No base_url:
            #- the paths are relative to wherever this was served from.
            self._send_text(
                "source:\n"
                "  url: /api/flat\n"
                "index: sensor\n"
                "columns: x\n"
                "values: value\n", "application/yaml")
        elif parsed.path == "/spec-with-secret.yaml":
            self._send_text(
                "source:\n"
                "  url: /api/flat\n"
                "  headers:\n"
                "    Authorization: \"Bearer ${CICWAVE_TEST_SECRET}\"\n"
                "index: sensor\n"
                "columns: x\n"
                "values: value\n", "application/yaml")
        elif parsed.path == "/api/not-json":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"just some text")
        else:
            self.send_response(404)
            self.end_headers()

    def _send_text(self, text, content_type):
        body = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)

    def _send(self, payload, generation):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Data-Generation", generation)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _wave_pg_or_skip(test):
    """The GUI module, or skip: PySide6/pyqtgraph are optional."""
    try:
        import cicwave.wave_pg as wave_pg
    except Exception as e:  # pragma: no cover - optional GUI deps
        test.skipTest("pyqtgraph / PySide6 not installed (%s)" % e)
    return wave_pg


def _save_session_to(wave_pg, win, path):
    """Drive File -> Save Session, with the file dialog answered."""
    import unittest.mock as mock

    with mock.patch.object(wave_pg.QFileDialog, "getSaveFileName",
                           staticmethod(lambda *a, **k: (path, ""))):
        win._save_session()


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

    def test_scale_turns_a_magnitude_in_a_name_into_a_signed_axis(self):
        # Names often encode a magnitude whose sign is implied: a series
        # measured at -55 is called "..._n55". Without arithmetic the
        # x-axis comes out mirrored.
        spec = self.spec("""
            source:
              base_url: {base}
              requests:
                - path: /api/series
                  records: rows
              derive:
                level: {{from: sensor, regex: "N(\\\\d+)$", type: int,
                         scale: -1}}
        """)
        df = fetch_dataframe(spec)
        self.assertEqual(list(df["level"]), [-4, -7, -9])

    def test_offset_shifts_the_derived_number(self):
        spec = self.spec("""
            source:
              base_url: {base}
              requests:
                - path: /api/series
                  records: rows
              derive:
                shifted: {{from: sensor, regex: "N(\\\\d+)$", type: int,
                           offset: 100}}
        """)
        df = fetch_dataframe(spec)
        self.assertEqual(list(df["shifted"]), [104, 107, 109])

    def test_scale_works_on_catalog_records_too(self):
        spec = self.spec(TestLazyCatalog.SPEC)
        spec["source"]["catalog"]["derive"] = {
            "level": {"from": "sensor", "regex": r"N(\d+)$", "type": "int",
                      "scale": -1},
        }
        spec["source"]["catalog"]["group_name"] = "{level}"
        self.assertEqual(LazyCatalog(spec).load(), ["-4", "-7"])

    def test_misspelled_derive_key_is_rejected(self):
        # Silently ignoring an unknown key here would look like the rule
        # simply not working.
        spec = self.spec("""
            source:
              base_url: {base}
              requests:
                - path: /api/series
                  records: rows
              derive:
                x: {{from: sensor, regex: "N(\\\\d+)$", scal: -1}}
        """)
        with self.assertRaises(ValueError) as cm:
            fetch_dataframe(spec)
        self.assertIn("scal", str(cm.exception))

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

    def test_save_session_writes_a_source_entry(self):
        # Regression: File -> Save Session relativised every entry's
        # 'path', but a source spec is stored as 'source' with no
        # 'path' at all, so saving raised KeyError whenever a spec that
        # fetches its own data was open.
        wp = _wave_pg_or_skip(self)
        c = wp.CmdWavePg("x")
        c.win.openPath(self.spec_path)
        self.assertEqual(len(c.win.browser.files), 1)

        out = os.path.join(self.tmp, "s.cicwave.yaml")
        _save_session_to(wp, c.win, out)

        with open(out) as fh:
            written = yaml.safe_load(fh)
        # Relative to the session file, so the pair stays movable.
        self.assertEqual(written["files"][0]["source"], "api.yaml")

        c2 = wp.CmdWavePg("x")
        c2.win.applySession(out)
        self.assertEqual(len(c2.win.browser.files), 1)


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


class TestLazyCatalog(_ApiTestCase):
    """A catalog names every series cheaply; data arrives per group."""

    SPEC = """
        source:
          base_url: {base}
          catalog:
            requests:
              - path: /api/series
                records: rows
                where: {{kind: temperature}}
            group_name: "{{kind}}.{{sensor}}"
            fetch:
              path: /api/series/{{id}}/probes
              records: points
              index: probe
              columns: x
              values: value
    """

    def test_catalog_lists_groups_without_fetching_them(self):
        catalog = LazyCatalog(self.spec(self.SPEC))
        groups = catalog.load()
        self.assertEqual(groups,
                         ["temperature.STATION_N04", "temperature.STATION_N07"])
        # One listing request, and nothing else.
        self.assertEqual(_Handler.request_count, 1)

    def test_group_name_values_are_sanitised(self):
        # A dot or space inside a value would invent a tree level or
        # flatten the hierarchy, so they collapse to underscores.
        spec = self.spec(self.SPEC)
        spec["source"]["catalog"]["group_name"] = "{setup}"
        catalog = LazyCatalog(spec)
        groups = catalog.load()
        self.assertTrue(all("." not in g and " " not in g for g in groups),
                        groups)

    def test_loading_one_group_fetches_only_that_group(self):
        catalog = LazyCatalog(self.spec(self.SPEC))
        groups = catalog.load()
        frame = catalog.load_group(groups[0])
        self.assertEqual(_Handler.request_count, 2)
        self.assertIn("temperature.STATION_N04.P1", frame.columns)
        self.assertIn("temperature.STATION_N04.P2", frame.columns)
        # Two x values, both probes side by side.
        self.assertEqual(len(frame), 2)

    def test_group_frame_declares_its_own_x_column(self):
        catalog = LazyCatalog(self.spec(self.SPEC))
        groups = catalog.load()
        frame = catalog.load_group(groups[0])
        wave_x = frame.attrs["cicwave_wave_x"]
        wave = "temperature.STATION_N04.P1"
        self.assertIn(wave, wave_x)
        self.assertIn(wave_x[wave], frame.columns)
        self.assertNotEqual(wave_x[wave], wave)

    def test_derive_splits_a_packed_field_for_the_group_name(self):
        # The listing carries several settings inside one string; the
        # tree should have a level per setting, not one opaque leaf.
        spec = self.spec(TestLazyCatalog.SPEC)
        spec["source"]["catalog"]["derive"] = {
            "mode": {"from": "setup", "kv": "MODE"},
            "range": {"from": "setup", "kv": "RANGE"},
        }
        spec["source"]["catalog"]["group_name"] = "{mode}.{range}.{sensor}"
        groups = LazyCatalog(spec).load()
        self.assertEqual(groups, ["fast.hi.STATION_N04",
                                  "slow.lo.STATION_N07"])

    def test_derived_catalog_field_can_be_templated_into_the_fetch(self):
        spec = self.spec(TestLazyCatalog.SPEC)
        spec["source"]["catalog"]["derive"] = {
            "series_id": {"from": "id", "regex": r"s(\d+)", "type": "int"},
        }
        spec["source"]["catalog"]["group_name"] = "{series_id}"
        catalog = LazyCatalog(spec)
        groups = catalog.load()
        self.assertEqual(groups, ["1", "2"])
        # An int id must not become "1.0" on its way into a URL.
        spec["source"]["catalog"]["fetch"]["path"] = \
            "/api/series/s{series_id}/probes"
        frame = catalog.load_group("1")
        self.assertIn("1.P1", frame.columns)

    def test_derive_from_a_missing_field_is_reported(self):
        spec = self.spec(TestLazyCatalog.SPEC)
        spec["source"]["catalog"]["derive"] = {
            "x": {"from": "nosuchfield", "kv": "MODE"},
        }
        with self.assertRaises(ValueError) as cm:
            LazyCatalog(spec).load()
        self.assertIn("nosuchfield", str(cm.exception))

    def test_x_name_and_unit_come_from_the_response_envelope(self):
        spec = self.spec(TestLazyCatalog.SPEC)
        spec["source"]["catalog"]["fetch"]["x_name"] = "{axis}_{x_label}"
        spec["source"]["catalog"]["fetch"]["unit"] = "{unit}"
        catalog = LazyCatalog(spec)
        groups = catalog.load()
        frame = catalog.load_group(groups[0])

        wave = "temperature.STATION_N04.P1"
        self.assertTrue(
            frame.attrs["cicwave_wave_x"][wave].endswith("frequency_MHz"),
            frame.attrs["cicwave_wave_x"][wave])
        self.assertEqual(frame.attrs["cicwave_wave_unit"][wave], "degC")

    def test_unit_from_an_unknown_envelope_field_is_reported(self):
        spec = self.spec(TestLazyCatalog.SPEC)
        spec["source"]["catalog"]["fetch"]["unit"] = "{nosuchfield}"
        catalog = LazyCatalog(spec)
        groups = catalog.load()
        with self.assertRaises(ValueError) as cm:
            catalog.load_group(groups[0])
        self.assertIn("nosuchfield", str(cm.exception))

    def test_empty_field_becomes_a_visible_segment(self):
        # An empty value would otherwise render as a blank tree row.
        from cicwave.apisource import _sanitize_name

        self.assertEqual(_sanitize_name(""), "unset")
        self.assertEqual(_sanitize_name(None), "unset")
        self.assertEqual(_sanitize_name("  "), "unset")
        # Slashes, spaces and dots would each invent or flatten a level.
        self.assertEqual(_sanitize_name("a/b mode"), "a_b_mode")
        self.assertEqual(_sanitize_name("v1.2"), "v1_2")

    def test_unknown_group_is_an_error(self):
        catalog = LazyCatalog(self.spec(self.SPEC))
        catalog.load()
        with self.assertRaises(ValueError) as cm:
            catalog.load_group("nosuch.group")
        self.assertIn("nosuch.group", str(cm.exception))

    def test_group_name_field_must_exist_in_the_catalog(self):
        spec = self.spec(self.SPEC)
        spec["source"]["catalog"]["group_name"] = "{nosuchfield}"
        with self.assertRaises(ValueError) as cm:
            LazyCatalog(spec).load()
        self.assertIn("nosuchfield", str(cm.exception))

    def test_fetch_block_must_say_what_the_axes_are(self):
        spec = self.spec(self.SPEC)
        del spec["source"]["catalog"]["fetch"]["columns"]
        with self.assertRaises(ValueError) as cm:
            LazyCatalog(spec)
        self.assertIn("columns", str(cm.exception))

    def test_has_catalog_detects_the_block(self):
        self.assertTrue(has_catalog(self.spec(self.SPEC)))
        self.assertFalse(has_catalog({"source": {"url": "http://x/y"}}))


class TestLazyWaveFile(_ApiTestCase):
    """The WaveFile side: names up front, samples on demand."""

    def _lazy_file(self):
        from cicwave.wavefiles import WaveFiles

        catalog = LazyCatalog(self.spec(TestLazyCatalog.SPEC))
        groups = catalog.load()
        files = WaveFiles()
        return files.openLazyFrame("cat", "", groups, catalog.load_group)

    def test_pending_groups_appear_as_wave_names(self):
        wf = self._lazy_file()
        self.assertIn("temperature.STATION_N04", wf.getWaveNames())
        self.assertTrue(wf.isPendingGroup("temperature.STATION_N04"))

    def test_loading_a_group_replaces_it_with_its_waves(self):
        wf = self._lazy_file()
        added = wf.loadGroup("temperature.STATION_N04")
        self.assertEqual(added, ["temperature.STATION_N04.P1",
                                 "temperature.STATION_N04.P2"])
        self.assertFalse(wf.isPendingGroup("temperature.STATION_N04"))
        self.assertIn("temperature.STATION_N04.P1", wf.getWaveNames())

    def test_x_column_is_not_offered_as_a_wave(self):
        wf = self._lazy_file()
        added = wf.loadGroup("temperature.STATION_N04")
        self.assertTrue(all("::x::" not in name for name in added), added)

    def test_second_group_widens_the_frame_without_disturbing_the_first(self):
        wf = self._lazy_file()
        wf.loadGroup("temperature.STATION_N04")
        wf.loadGroup("temperature.STATION_N07")
        names = wf.getWaveNames()
        self.assertIn("temperature.STATION_N04.P1", names)
        self.assertIn("temperature.STATION_N07.P1", names)
        # Rows are stacked, not joined: each group keeps its own.
        self.assertEqual(len(wf.df), 4)

    def test_group_with_no_samples_stays_in_the_tree(self):
        # `humidity` is filtered out of the catalog, so build one whose
        # fetch legitimately returns nothing.
        from cicwave.wavefiles import WaveFiles

        spec = self.spec(TestLazyCatalog.SPEC)
        spec["source"]["catalog"]["fetch"]["where"] = {"probe": "nosuch"}
        catalog = LazyCatalog(spec)
        groups = catalog.load()
        wf = WaveFiles().openLazyFrame("cat", "", groups, catalog.load_group)

        self.assertEqual(wf.loadGroup(groups[0]), [])
        self.assertIn(groups[0], wf.getWaveNames())
        before = _Handler.request_count
        self.assertEqual(wf.loadGroup(groups[0]), [])
        self.assertEqual(_Handler.request_count, before)

    def test_loading_the_same_group_twice_does_not_refetch(self):
        wf = self._lazy_file()
        wf.loadGroup("temperature.STATION_N04")
        before = _Handler.request_count
        self.assertEqual(wf.loadGroup("temperature.STATION_N04"), [])
        self.assertEqual(_Handler.request_count, before)


class TestLazyPlotting(_ApiTestCase):
    """Per-wave x-axes, so groups swept against different axes coexist."""

    def _wave(self, wave_name):
        try:
            from cicwave.wave_pg import PgWave
        except Exception as e:  # pragma: no cover - optional GUI deps
            self.skipTest("pyqtgraph / PySide6 not installed (%s)" % e)
        from cicwave.wavefiles import WaveFiles

        catalog = LazyCatalog(self.spec(TestLazyCatalog.SPEC))
        groups = catalog.load()
        files = WaveFiles()
        wf = files.openLazyFrame("cat", "", groups, catalog.load_group)
        for group in groups:
            wf.loadGroup(group)
        return PgWave(wf, wave_name, "")

    def test_wave_reads_its_own_x_and_skips_other_groups_rows(self):
        wave = self._wave("temperature.STATION_N04.P1")
        self.assertEqual(len(wave.x), 2)
        self.assertEqual(len(wave.y), 2)
        self.assertEqual(list(wave.x), [100.0, 200.0])
        self.assertEqual(list(wave.y), [-1.0, -2.0])

    def test_second_group_gets_its_own_values(self):
        wave = self._wave("temperature.STATION_N07.P1")
        self.assertEqual(list(wave.y), [-3.0, -4.0])

    def test_unfetched_group_plots_nothing_rather_than_fetching(self):
        try:
            from cicwave.wave_pg import PgWave
        except Exception as e:  # pragma: no cover - optional GUI deps
            self.skipTest("pyqtgraph / PySide6 not installed (%s)" % e)
        from cicwave.wavefiles import WaveFiles

        catalog = LazyCatalog(self.spec(TestLazyCatalog.SPEC))
        groups = catalog.load()
        wf = WaveFiles().openLazyFrame("cat", "", groups, catalog.load_group)
        before = _Handler.request_count
        wave = PgWave(wf, groups[0], "")
        # Bulk paths (plot-all, headless export autoplot) walk every name;
        # they must not turn into one request per catalog entry.
        self.assertIsNone(wave.y)
        self.assertEqual(_Handler.request_count, before)


class TestCatalogInGui(_ApiTestCase):
    """Clicking an unfetched group is what triggers the request."""

    def setUp(self):
        super().setUp()
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("CICSIM_USE_OPENGL", "0")
        import tempfile

        self.tmp = tempfile.mkdtemp(prefix="cicwave-catalog-")
        self.spec_path = os.path.join(self.tmp, "catalog.yaml")
        with open(self.spec_path, "w") as fh:
            yaml.safe_dump(self.spec(TestLazyCatalog.SPEC), fh)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _window(self):
        try:
            from cicwave.wave_pg import CmdWavePg
        except Exception as e:  # pragma: no cover - optional GUI deps
            self.skipTest("pyqtgraph / PySide6 not installed (%s)" % e)
        return CmdWavePg(None).win

    @staticmethod
    def _leaves(tree):
        found = []

        def walk(item, path):
            here = path + [item.text(0)]
            if item.childCount() == 0:
                found.append(".".join(here))
            for i in range(item.childCount()):
                walk(item.child(i), here)

        for i in range(tree.topLevelItemCount()):
            walk(tree.topLevelItem(i), [])
        return found

    def test_opening_a_catalog_shows_the_tree_without_fetching(self):
        win = self._window()
        win.openPath(self.spec_path)
        self.assertEqual(
            sorted(self._leaves(win.browser.wave_tree)),
            ["temperature.STATION_N04", "temperature.STATION_N07"])
        self.assertEqual(_Handler.request_count, 1)

    def test_clicking_a_group_fetches_and_plots_it(self):
        win = self._window()
        win.openPath(self.spec_path)

        item = win.browser.wave_tree.topLevelItem(0).child(0)
        win.browser._wave_clicked(item, 0)

        self.assertEqual(_Handler.request_count, 2)
        plot = win.tab_widget.widget(0)
        plotted = sorted(w.key for w, _unit in plot.wave_data.values())
        self.assertEqual(plotted, ["temperature.STATION_N04.P1",
                                   "temperature.STATION_N04.P2"])
        # The tree now shows what the fetch produced.
        self.assertIn("temperature.STATION_N04.P1",
                      self._leaves(win.browser.wave_tree))

    def test_fetching_keeps_the_tree_open(self):
        # Regression: the refill after a fetch rebuilt the tree from
        # scratch, collapsing every scope the user had opened.
        win = self._window()
        win.openPath(self.spec_path)
        tree = win.browser.wave_tree
        top = tree.topLevelItem(0)
        top.setExpanded(True)

        win.browser._wave_clicked(top.child(0), 0)

        top = tree.topLevelItem(0)
        self.assertTrue(top.isExpanded(), "top-level scope collapsed")
        group = top.child(0)
        self.assertTrue(group.isExpanded(),
                        "the group just fetched should show its waves")
        self.assertEqual(group.childCount(), 2)

    def test_the_other_group_is_still_unfetched(self):
        win = self._window()
        win.openPath(self.spec_path)
        win.browser._wave_clicked(
            win.browser.wave_tree.topLevelItem(0).child(0), 0)
        wf = next(iter(win.browser.files.values()))
        self.assertTrue(wf.isPendingGroup("temperature.STATION_N07"))

    def test_a_catalog_session_round_trips(self):
        # Regression: applySession sent every 'source' entry through
        # fetch_dataframe, which a catalog spec has no plain request
        # for, so reloading died with "source: needs a 'requests' list".
        wave_pg = _wave_pg_or_skip(self)
        win = self._window()
        win.openPath(self.spec_path)
        win.browser._wave_clicked(
            win.browser.wave_tree.topLevelItem(0).child(0), 0)

        out = os.path.join(self.tmp, "s.cicwave.yaml")
        _save_session_to(wave_pg, win, out)

        win2 = self._window()
        win2.applySession(out)
        self.assertEqual(len(win2.browser.files), 1)
        wf = next(iter(win2.browser.files.values()))
        # The group that was plotted is fetched; the rest still are not.
        self.assertTrue(wf.isPendingGroup("temperature.STATION_N07"))
        plotted = sorted(
            w.key for w, _unit
            in win2.tab_widget.widget(0).wave_data.values())
        self.assertEqual(plotted, ["temperature.STATION_N04.P1",
                                   "temperature.STATION_N04.P2"])

    def test_a_catalog_session_does_not_fetch_every_group(self):
        # The whole point of a catalog: opening one must cost the
        # listing plus the groups actually plotted, not the catalog.
        wave_pg = _wave_pg_or_skip(self)
        win = self._window()
        win.openPath(self.spec_path)
        out = os.path.join(self.tmp, "s.cicwave.yaml")
        _save_session_to(wave_pg, win, out)

        _Handler.request_count = 0
        self._window().applySession(out)
        self.assertEqual(_Handler.request_count, 1)


class TestSpecServedOverHttp(_ApiTestCase):
    """A service can publish the spec itself, so there is no local file
    to keep in step with the API."""

    def test_spec_url_is_fetched_and_used(self):
        from cicwave.pivot import load_spec

        spec = load_spec(self.base + "/spec.yaml")
        self.assertEqual(spec.origin, self.base + "/spec.yaml")
        df = fetch_dataframe(spec)
        self.assertEqual(len(df), 2)

    def test_relative_paths_resolve_against_the_spec_url(self):
        # The served spec has no base_url; it should still reach the
        # service that handed it out.
        from cicwave.pivot import load_spec

        spec = load_spec(self.base + "/spec.yaml")
        self.assertIsNone((spec.get("source") or {}).get("base_url"))
        self.assertEqual(list(fetch_dataframe(spec)["sensor"]), ["A", "A"])

    def test_local_spec_has_no_origin(self):
        import tempfile
        from cicwave.pivot import load_spec

        with tempfile.NamedTemporaryFile("w", suffix=".yaml",
                                         delete=False) as fh:
            fh.write("source:\n  url: http://127.0.0.1:1/x\n")
            path = fh.name
        try:
            self.assertIsNone(load_spec(path).origin)
        finally:
            os.unlink(path)

    def test_spec_url_with_a_query_string_is_recognised(self):
        # A generated spec is served at something like
        # /spec.yaml?dut=X. Testing endswith(".yaml") against the whole
        # URL missed those, and they fell through to the data loader,
        # which reports "unsupported format '.yaml'".
        from cicwave.cli import _is_source_spec_file, _promote_positional_spec

        url = self.base + "/spec.yaml?param=X&other=1"
        self.assertTrue(_is_source_spec_file(url))
        self.assertEqual(_promote_positional_spec((url,), None), ((), url))

    def test_query_string_spec_loads_and_fetches(self):
        from cicwave.pivot import load_spec

        spec = load_spec(self.base + "/spec.yaml?param=X")
        self.assertEqual(len(fetch_dataframe(spec)), 2)

    def test_a_data_url_is_still_not_taken_for_a_spec(self):
        from cicwave.cli import _is_source_spec_file

        self.assertFalse(_is_source_spec_file(self.base + "/api/flat"))
        self.assertFalse(
            _is_source_spec_file(self.base + "/data.csv?format=csv"))

    def test_a_spec_url_survives_a_session_round_trip(self):
        # A spec fetched over HTTP has no local path. Absolutising the
        # URL turned it into a local path naming nothing, so a session
        # written from a spec URL could not be read back.
        import tempfile

        wave_pg = _wave_pg_or_skip(self)
        url = self.base + "/spec.yaml?dut=A0"
        tmp = tempfile.mkdtemp(prefix="cicwave-api-url-session-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        c = wave_pg.CmdWavePg("x")
        c.win.openPath(url)
        self.assertEqual(len(c.win.browser.files), 1)

        out = os.path.join(tmp, "s.cicwave.yaml")
        _save_session_to(wave_pg, c.win, out)
        with open(out) as fh:
            written = yaml.safe_load(fh)
        # A URL is already portable: kept whole, query string and all.
        self.assertEqual(written["files"][0]["source"], url)

        c2 = wave_pg.CmdWavePg("x")
        c2.win.applySession(out)
        self.assertEqual(len(c2.win.browser.files), 1)
        # And re-saving still names the URL, not a mangled local path.
        self.assertEqual(
            c2.win._build_session()["files"][0]["source"], url)

    def test_the_cli_records_a_spec_url_as_a_url(self):
        # cli.py absolutised the spec for the session it would later
        # save; for a URL that yields a path that names nothing.
        from cicwave.cli import _spec_ref

        url = self.base + "/spec.yaml?dut=A0"
        self.assertEqual(_spec_ref(url), url)
        self.assertEqual(_spec_ref("api.yaml"), os.path.abspath("api.yaml"))

    def test_non_mapping_response_is_rejected(self):
        from cicwave.pivot import load_spec

        # A data endpoint (a JSON list) is not a spec.
        with self.assertRaises(ValueError) as cm:
            load_spec(self.base + "/api/flat")
        self.assertIn("did not return a pivot spec", str(cm.exception))


class TestRemoteSpecCannotReadSecrets(_ApiTestCase):
    """A spec is instructions, not data. One fetched over the network
    must not be able to put a local secret into a request header."""

    def test_remote_spec_may_not_expand_environment_variables(self):
        from cicwave.pivot import load_spec

        os.environ["CICWAVE_TEST_SECRET"] = "sekret"
        try:
            spec = load_spec(self.base + "/spec-with-secret.yaml")
            with self.assertRaises(ValueError) as cm:
                fetch_dataframe(spec)
        finally:
            del os.environ["CICWAVE_TEST_SECRET"]
        message = str(cm.exception)
        self.assertIn("not allowed", message)
        self.assertIn("Authorization", message)
        # The point of the guard: the value never leaves the process.
        self.assertNotIn("sekret", message)
        self.assertNotIn("sekret", str(_Handler.seen_auth))

    def test_the_same_spec_from_disk_still_expands_it(self):
        import tempfile
        from cicwave.pivot import load_spec

        os.environ["CICWAVE_TEST_SECRET"] = "sekret"
        with tempfile.NamedTemporaryFile("w", suffix=".yaml",
                                         delete=False) as fh:
            fh.write("source:\n  url: %s/api/flat\n"
                     "  headers: {Authorization: \"Bearer "
                     "${CICWAVE_TEST_SECRET}\"}\n" % self.base)
            path = fh.name
        try:
            fetch_dataframe(load_spec(path))
        finally:
            del os.environ["CICWAVE_TEST_SECRET"]
            os.unlink(path)
        self.assertEqual(_Handler.seen_auth[-1], "Bearer sekret")


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
