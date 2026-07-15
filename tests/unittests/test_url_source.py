#!/usr/bin/env python3
"""Tests for loading WaveFile data from http(s) URLs."""

import http.server
import json
import threading
import unittest

from cicwave.wavefiles import WaveFile, _is_url, _check_not_link_local


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/data.csv":
            self._send(b"time,v(out)\n0,0.1\n1,0.2\n2,0.3\n", "text/csv")
        elif self.path == "/data.json":
            body = json.dumps([{"x": 1, "y": 2}, {"x": 2, "y": 4}]).encode()
            self._send(body, "application/json")
        elif self.path == "/rest-endpoint":
            # Extension-less REST-style path; only Content-Type says JSON.
            body = json.dumps([{"x": 1, "y": 2}, {"x": 2, "y": 4}]).encode()
            self._send(body, "application/json")
        elif self.path == "/unlabeled":
            # No extension AND no informative Content-Type.
            self._send(b"time,v\n0,1\n1,2\n", "application/octet-stream")
        elif self.path == "/data.pkl":
            self._send(b"not a real pickle", "application/octet-stream")
        else:
            self.send_response(404)
            self.end_headers()

    def _send(self, body, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class TestUrlSource(unittest.TestCase):
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

    def test_is_url(self):
        self.assertTrue(_is_url("http://example.com/data.csv"))
        self.assertTrue(_is_url("https://example.com/data.csv"))
        self.assertFalse(_is_url("data.csv"))
        self.assertFalse(_is_url("/tmp/data.csv"))
        self.assertFalse(_is_url("ftp://example.com/data.csv"))

    def test_csv_url_loads(self):
        wf = WaveFile(self.base + "/data.csv", xaxis="time")
        self.assertEqual(list(wf.df.columns), ["time", "v(out)"])
        self.assertEqual(len(wf.df), 3)
        self.assertAlmostEqual(wf.df["v(out)"].iloc[2], 0.3)

    def test_json_url_loads(self):
        wf = WaveFile(self.base + "/data.json", xaxis="x")
        self.assertEqual(list(wf.df.columns), ["x", "y"])
        self.assertEqual(len(wf.df), 2)

    def test_extensionless_url_dispatches_via_content_type(self):
        wf = WaveFile(self.base + "/rest-endpoint", xaxis="x")
        self.assertEqual(list(wf.df.columns), ["x", "y"])

    def test_format_override_forces_dispatch(self):
        wf = WaveFile(self.base + "/rest-endpoint", xaxis="x", fmt="json")
        self.assertEqual(list(wf.df.columns), ["x", "y"])

    def test_unresolvable_format_raises_with_hint(self):
        # Remote sources fetch eagerly on open (no cheap header-only
        # path over HTTP), so the error surfaces at construction time.
        with self.assertRaises(ValueError) as cm:
            WaveFile(self.base + "/unlabeled", xaxis="time")
        self.assertIn("--format", str(cm.exception))

    def test_pickle_over_url_is_blocked(self):
        with self.assertRaises(ValueError) as cm:
            WaveFile(self.base + "/data.pkl", xaxis="x")
        self.assertIn("deserialization", str(cm.exception))

    def test_unreachable_host_raises_clear_error(self):
        # Port 1 on loopback should refuse the connection immediately.
        with self.assertRaises(ValueError) as cm:
            WaveFile("http://127.0.0.1:1/nope.csv", xaxis="x")
        self.assertIn("failed to fetch", str(cm.exception))

    def test_remote_is_fetched_once_and_cached(self):
        wf = WaveFile(self.base + "/data.csv", xaxis="time")
        df1 = wf.df
        raw_bytes_id = id(wf._remote_bytes)
        wf.reload()
        df2 = wf.df
        self.assertIs(df1, df2)
        self.assertEqual(id(wf._remote_bytes), raw_bytes_id)


class TestLinkLocalBlocked(unittest.TestCase):
    """Cloud metadata endpoints (169.254.169.254 and friends) must never
    be reachable via a URL loaded from a shared session/pivot file."""

    def test_cloud_metadata_ip_is_blocked(self):
        with self.assertRaises(ValueError) as cm:
            _check_not_link_local("http://169.254.169.254/latest/meta-data/")
        self.assertIn("link-local", str(cm.exception))

    def test_link_local_range_is_blocked(self):
        with self.assertRaises(ValueError):
            _check_not_link_local("http://169.254.1.2/data.csv")

    def test_wavefile_refuses_link_local_url(self):
        with self.assertRaises(ValueError) as cm:
            WaveFile("http://169.254.169.254/latest/meta-data/", xaxis="x")
        self.assertIn("link-local", str(cm.exception))

    def test_loopback_and_private_ranges_not_blocked(self):
        # Only link-local is refused; RFC1918 / loopback are legitimate
        # for internal REST APIs and local test servers.
        _check_not_link_local("http://127.0.0.1:9999/data.csv")
        _check_not_link_local("http://10.0.0.5/data.csv")
        _check_not_link_local("http://192.168.1.1/data.csv")


if __name__ == "__main__":
    unittest.main()
