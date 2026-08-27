#!/usr/bin/env python3

"""Build a flat DataFrame from a JSON HTTP API, declaratively.

A single URL that returns a table already works as a plain data source
(see :mod:`cicwave.wavefiles`). This module covers the case a REST
service -- FastAPI, Flask, whatever -- usually presents instead: the
rows you want to plot are nested inside a JSON envelope, and they are
spread over several endpoints, where one request lists what exists and
a second has to be issued per item to get the actual sweep.

Putting that in a ``source:`` block of a pivot spec keeps every name
that belongs to *your* data -- host, endpoint paths, query parameters,
field names -- in your own file, and keeps cicwave itself free of any
knowledge about a particular API::

    source:
      base_url: http://api.example.com
      requests:
        - name: series               # referenced by for_each below
          path: /api/series
          params: {kind: temperature, limit: 500}
          records: rows               # dot-path to the list in the JSON
          where: {unit: degC}         # keep only matching records
        - path: /api/series/{id}/points   # {id} comes from the parent row
          for_each: series
          params: {unit: "{unit}"}
          records: points
          merge: [label]              # carry parent fields onto each row
          headers_as_columns:
            generation: X-Data-Generation

      require_consistent_headers: [X-Data-Generation]

      rename: {x: frequency_mhz, value: reading_db}
      derive:
        station: {from: sensor, regex: "([^_]+)$"}
        channel: {from: condition, kv: CHANNEL}
      filter: {channel: hi}          # applies to the derived columns too

The rows of the *last* request become the frame, which the rest of the
pivot spec (``index``/``columns``/``values``/``conditions``) then
reshapes as usual.

Everything here is data, not code: there is no expression language to
evaluate, so opening someone else's spec fetches URLs (guarded the same
way :mod:`cicwave.wavefiles` guards them) but cannot run anything.
"""

import json
import logging
import os
import re
import urllib.parse

import pandas as pd

from .wavefiles import fetch_url_bytes, _is_url

logger = logging.getLogger("cicwave")

#- A fan-out request issues one HTTP GET per parent row, so a spec with a
#- forgotten `limit` can turn into thousands of requests against someone's
#- API. Cap it, and make the cap a spec key so a genuinely large pull is a
#- deliberate choice rather than an accident.
DEFAULT_MAX_REQUESTS = 200
DEFAULT_TIMEOUT_S = 30

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_FIELD_RE = re.compile(r"\{([^{}]+)\}")

_REQUEST_KEYS = {
    "name", "path", "url", "params", "records", "where", "merge", "keep",
    "for_each", "headers_as_columns",
}
_SOURCE_KEYS = {
    "base_url", "requests", "headers", "timeout", "max_requests",
    "require_consistent_headers", "rename", "derive", "filter",
    #- single-request shorthand, promoted to a one-element `requests`
    "url", "path", "params", "records", "where", "keep",
    "headers_as_columns",
}


def has_source(spec):
    """True if *spec* carries a ``source:`` block to fetch from."""
    return isinstance(spec, dict) and bool(spec.get("source"))


def _check_keys(block, allowed, what):
    unknown = sorted(set(block) - allowed)
    if unknown:
        raise ValueError(
            "%s: unknown key(s) %s; expected one of: %s" % (
                what, ", ".join(unknown), ", ".join(sorted(allowed))))


def _expand_env(value):
    """Expand ``${VAR}`` references in a header value.

    Header values are the one place a spec plausibly needs a secret, and
    a spec file is meant to be committed and shared, so the token itself
    has to come from the environment.
    """
    def sub(m):
        name = m.group(1)
        try:
            return os.environ[name]
        except KeyError:
            raise ValueError(
                "source: header references ${%s}, which is not set in the "
                "environment" % name) from None
    return _ENV_RE.sub(sub, value)


def _resolve_headers(headers):
    return {str(k): _expand_env(str(v)) for k, v in (headers or {}).items()}


def _template(value, row, where):
    """Substitute ``{field}`` references in *value* from the parent *row*."""
    if not isinstance(value, str):
        return value

    def sub(m):
        key = m.group(1)
        if key not in row:
            raise ValueError(
                "%s refers to {%s}, which is not a field of the record it "
                "iterates over (available: %s)" % (
                    where, key, ", ".join(sorted(row)) or "none"))
        return str(row[key])

    return _FIELD_RE.sub(sub, value)


def _build_url(base_url, req, row, what):
    target = req.get("url") or req.get("path")
    if not target:
        raise ValueError("%s: needs a 'path' (or a full 'url')" % what)
    target = _template(str(target), row, what)

    if _is_url(target):
        url = target
    else:
        if not base_url:
            raise ValueError(
                "%s: path '%s' is relative but the source has no 'base_url'"
                % (what, target))
        url = urllib.parse.urljoin(
            base_url.rstrip("/") + "/", target.lstrip("/"))

    params = req.get("params") or {}
    if params:
        if not isinstance(params, dict):
            raise ValueError("%s: 'params' must be a mapping" % what)
        resolved = {k: _template(v, row, what) for k, v in params.items()}
        query = urllib.parse.urlencode(resolved, doseq=True)
        url = url + ("&" if urllib.parse.urlsplit(url).query else "?") + query
    return url


def _dig(payload, path, url):
    """Follow a dot-path into a decoded JSON payload."""
    cur = payload
    for part in str(path).split("."):
        if not isinstance(cur, dict):
            raise ValueError(
                "%s: records path '%s' ran into a %s where an object was "
                "expected" % (url, path, type(cur).__name__))
        if part not in cur:
            raise ValueError(
                "%s: records path '%s' not found in the response "
                "(available keys: %s)" % (
                    url, path, ", ".join(sorted(cur)) or "none"))
        cur = cur[part]
    return cur


def _records_of(payload, records_path, url):
    data = _dig(payload, records_path, url) if records_path else payload
    if not isinstance(data, list):
        raise ValueError(
            "%s: expected a list of records%s, got %s; set 'records' to the "
            "path of the list in the response" % (
                url,
                " at '%s'" % records_path if records_path else "",
                type(data).__name__))
    rows = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError(
                "%s: records must be JSON objects, got %s" % (
                    url, type(item).__name__))
        rows.append(dict(item))
    return rows


def _matches(row, where):
    """Exact-match filter; a list value matches any of its entries."""
    for key, want in where.items():
        if key not in row:
            return False
        got = row[key]
        options = want if isinstance(want, list) else [want]
        #- Compare as strings too: YAML gives 6 where JSON gave "6".
        if not any(got == o or str(got) == str(o) for o in options):
            return False
    return True


class _Fetcher:
    """GETs JSON, once per distinct URL, under a request budget."""

    def __init__(self, base_url, headers, timeout, max_requests,
                 consistent_headers):
        self.base_url = base_url
        self.headers = headers
        self.timeout = timeout
        self.max_requests = max_requests
        self.consistent_headers = list(consistent_headers or [])
        self._cache = {}
        self._seen_header_values = {}
        self.count = 0

    def get(self, url):
        if url in self._cache:
            return self._cache[url]
        if self.count >= self.max_requests:
            raise ValueError(
                "source: stopped after %d requests (max_requests); narrow "
                "the spec with 'where'/'params', or raise max_requests if "
                "the fan-out is intended" % self.max_requests)
        self.count += 1
        data, resp_headers = fetch_url_bytes(
            url, headers=self.headers, timeout=self.timeout)
        try:
            payload = json.loads(data)
        except ValueError as e:
            raise ValueError(
                "%s: response is not JSON (%s)" % (url, e)) from e
        self._check_consistency(url, resp_headers)
        result = (payload, resp_headers)
        self._cache[url] = result
        return result

    def _check_consistency(self, url, resp_headers):
        """Fail if a header that should pin the dataset changed mid-pull.

        A multi-request pull is only one dataset if every response came
        from the same build of it. Without this, a service that
        regenerates between two requests hands back a frame silently
        stitched from two generations.
        """
        for name in self.consistent_headers:
            value = resp_headers.get(name)
            if value is None:
                continue
            first = self._seen_header_values.setdefault(name, (value, url))
            if first[0] != value:
                raise ValueError(
                    "source: %s changed from %r (%s) to %r (%s) while "
                    "fetching; the data would mix two versions" % (
                        name, first[0], first[1], value, url))


def _run_request(req, idx, results, fetcher):
    what = "source: request '%s'" % (req.get("name") or "#%d" % idx)
    _check_keys(req, _REQUEST_KEYS, what)

    parent_name = req.get("for_each")
    if parent_name:
        if parent_name not in results:
            raise ValueError(
                "%s: for_each '%s' does not name an earlier request "
                "(available: %s)" % (
                    what, parent_name, ", ".join(results) or "none"))
        parents = results[parent_name]
    else:
        parents = [{}]

    where = req.get("where") or {}
    merge = req.get("merge") or []
    keep = req.get("keep")
    header_cols = req.get("headers_as_columns") or {}

    out = []
    for parent in parents:
        url = _build_url(fetcher.base_url, req, parent, what)
        payload, resp_headers = fetcher.get(url)
        rows = [r for r in _records_of(payload, req.get("records"), url)
                if _matches(r, where)]
        for row in rows:
            for col in merge:
                if col not in parent:
                    raise ValueError(
                        "%s: merge column '%s' is not a field of '%s' "
                        "(available: %s)" % (
                            what, col, parent_name,
                            ", ".join(sorted(parent)) or "none"))
                row[col] = parent[col]
            for col, header_name in header_cols.items():
                row[col] = resp_headers.get(header_name)
            if keep is not None:
                for col in list(row):
                    if col not in keep:
                        del row[col]
        out.extend(rows)
    return out


def _derive_series(df, name, rule):
    if not isinstance(rule, dict):
        raise ValueError("source: derive '%s' must be a mapping" % name)
    what = "source: derive '%s'" % name
    src = rule.get("from")
    if not src:
        raise ValueError("%s: needs a 'from' column" % what)
    if src not in df.columns:
        raise ValueError(
            "%s: column '%s' not in the fetched data (available: %s)" % (
                what, src, ", ".join(map(str, df.columns))))
    col = df[src].astype(str)

    if "regex" in rule:
        group = int(rule.get("group", 1))
        extracted = col.str.extract(str(rule["regex"]), expand=True)
        if group > extracted.shape[1]:
            raise ValueError(
                "%s: regex has %d capture group(s), asked for group %d" % (
                    what, extracted.shape[1], group))
        out = extracted[extracted.columns[group - 1]]
    elif "kv" in rule:
        #- "KEY=VAL;KEY=VAL" condition strings, the shape a test setup
        #- usually gets flattened into.
        sep = str(rule.get("sep", ";"))
        assign = str(rule.get("assign", "="))
        key = str(rule["kv"])

        def pick(text):
            for field in text.split(sep):
                if assign in field:
                    k, v = field.split(assign, 1)
                    if k.strip() == key:
                        return v
            return None

        out = col.map(pick)
    elif "split" in rule:
        sep = str(rule["split"])
        index = int(rule.get("index", -1))

        def part(text):
            pieces = text.split(sep)
            try:
                return pieces[index]
            except IndexError:
                return None

        out = col.map(part)
    else:
        raise ValueError(
            "%s: needs one of 'regex', 'kv' or 'split'" % what)

    dtype = rule.get("type")
    if dtype in ("int", "float"):
        out = pd.to_numeric(out, errors="coerce")
        if dtype == "int":
            out = out.astype("Int64")
    elif dtype not in (None, "str"):
        raise ValueError(
            "%s: unknown type '%s' (int, float or str)" % (what, dtype))
    return out


def fetch_dataframe(spec):
    """Fetch and assemble the flat frame described by ``spec['source']``."""
    src = spec.get("source") or {}
    if not isinstance(src, dict):
        raise ValueError("source: must be a mapping")
    _check_keys(src, _SOURCE_KEYS, "source")

    requests_ = src.get("requests")
    if not requests_:
        if not (src.get("url") or src.get("path")):
            raise ValueError(
                "source: needs a 'requests' list, or a single 'url'/'path'")
        requests_ = [{k: src[k] for k in _REQUEST_KEYS if k in src}]
    if not isinstance(requests_, list):
        raise ValueError("source: 'requests' must be a list")

    fetcher = _Fetcher(
        base_url=src.get("base_url"),
        headers=_resolve_headers(src.get("headers")),
        timeout=float(src.get("timeout", DEFAULT_TIMEOUT_S)),
        max_requests=int(src.get("max_requests", DEFAULT_MAX_REQUESTS)),
        consistent_headers=src.get("require_consistent_headers"),
    )

    results = {}
    rows = []
    for idx, req in enumerate(requests_):
        if not isinstance(req, dict):
            raise ValueError("source: request #%d must be a mapping" % idx)
        rows = _run_request(req, idx, results, fetcher)
        results[req.get("name") or "#%d" % idx] = rows
        logger.debug("source: request %s produced %d row(s)",
                     req.get("name") or "#%d" % idx, len(rows))

    if not rows:
        raise ValueError(
            "source: the last request produced no records; check its "
            "'where' filter and query parameters")

    df = pd.DataFrame.from_records(rows)

    rename = src.get("rename") or {}
    missing = [c for c in rename if c not in df.columns]
    if missing:
        raise ValueError(
            "source: rename refers to column(s) %s that were not fetched "
            "(available: %s)" % (
                ", ".join(missing), ", ".join(map(str, df.columns))))
    df = df.rename(columns=rename)

    for name, rule in (src.get("derive") or {}).items():
        df[name] = _derive_series(df, name, rule)

    #- Runs after rename/derive, so it can select on a column that only
    #- exists once the raw records have been reshaped -- which a
    #- request's `where` cannot. `where` prunes requests, `filter` prunes
    #- rows; a narrow spec usually wants both.
    row_filter = src.get("filter") or {}
    if row_filter:
        unknown = [c for c in row_filter if c not in df.columns]
        if unknown:
            raise ValueError(
                "source: filter refers to column(s) %s that do not exist "
                "(available: %s)" % (
                    ", ".join(unknown), ", ".join(map(str, df.columns))))
        before = len(df)
        keep = df.apply(lambda r: _matches(r, row_filter), axis=1)
        df = df[keep].reset_index(drop=True)
        if df.empty:
            raise ValueError(
                "source: filter %r matched none of the %d fetched row(s)" % (
                    row_filter, before))
        logger.debug("source: filter kept %d of %d row(s)", len(df), before)

    logger.info("source: %d row(s) from %d request(s)",
                len(df), fetcher.count)
    return df


def is_source_spec_file(path):
    """True if *path* is a YAML/JSON pivot spec that fetches its own data.

    Used wherever a path could be either a data file or a spec: the CLI's
    positional argument, and the GUI's open dialog / drag-and-drop.
    """
    if not os.path.isfile(path):
        return False
    try:
        from .pivot import load_spec

        return has_source(load_spec(path))
    except Exception:
        return False


def load_flat_frame(spec, file=None, xaxis="", fmt=None):
    """The flat frame a pivot *spec* applies to.

    Fetched from the spec's ``source:`` block when it has one, otherwise
    read from *file* the ordinary way. Callers (CLI, GUI session,
    MCP server) go through here so a spec with a source works everywhere
    a spec with a data file does.
    """
    if has_source(spec):
        return fetch_dataframe(spec)
    if not file:
        raise ValueError(
            "no data file given, and the pivot spec has no 'source:' block "
            "to fetch from")
    from .wavefiles import WaveFile

    return WaveFile(file, xaxis, fmt=fmt).df
