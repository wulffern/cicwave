#!/usr/bin/env python3
"""Render the api-sources example plot from a recorded GitHub response.

`make docs` must not depend on a third-party endpoint being up, the same
reason the URL examples plot from committed CSV snapshots. But an API
source is only itself when it fetches, so instead of swapping the data
out, this swaps the *service* out: github_api_snapshot.json is replayed
over HTTP on localhost and github_api_spec.yaml is pointed at it. The
spec that runs is the committed one, requests and all -- only its
base_url differs from what the docs tell you to run.

Refresh the recording with `python fetch_url_snapshots.py`.

Usage: python render_github_api.py <output.svg>
"""

import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import urllib.parse

import yaml

SNAPSHOT = "github_api_snapshot.json"
SPEC = "github_api_spec.yaml"
SESSION = "session_github_api.cicwave.yaml"


def _key(path):
    """Path plus normalised query -- must match the recorder's key."""
    parts = urllib.parse.urlsplit(path)
    query = urllib.parse.urlencode(
        sorted(urllib.parse.parse_qsl(parts.query)))
    return parts.path + ("?" + query if query else "")


def _handler_for(responses):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            payload = responses.get(_key(self.path))
            if payload is None:
                self.send_error(
                    404, "not in %s: %s" % (SNAPSHOT, _key(self.path)))
                return
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return Handler


def main(out_svg):
    with open(SNAPSHOT) as fh:
        responses = json.load(fh)["responses"]
    with open(SPEC) as fh:
        spec = yaml.safe_load(fh)

    server = http.server.HTTPServer(
        ("127.0.0.1", 0), _handler_for(responses))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    spec["source"]["base_url"] = (
        "http://127.0.0.1:%d" % server.server_address[1])

    with open(SESSION) as fh:
        session = yaml.safe_load(fh)

    #- The session names the spec, so both have to be redirected: the
    #- copy of the spec that points at the replay server, and a copy of
    #- the session that points at that copy.
    written = []
    try:
        spec_path = _write_temp(spec, written)
        session["files"][0]["source"] = spec_path
        session_path = _write_temp(session, written)
        result = subprocess.run(
            ["cicwave", "--session", session_path, "--export", out_svg])
    finally:
        for path in written:
            os.unlink(path)
        server.shutdown()
        server.server_close()
    return result.returncode


def _write_temp(obj, written):
    """Dump *obj* beside the originals, so relative paths still resolve."""
    tmp = tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", delete=False, dir=".")
    yaml.safe_dump(obj, tmp)
    tmp.close()
    written.append(tmp.name)
    return os.path.basename(tmp.name)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
