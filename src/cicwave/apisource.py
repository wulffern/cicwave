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

A spec can itself be served over HTTP, so the API that owns the data can
publish how to plot it. Such a spec needs no ``base_url`` -- relative
paths resolve against where it came from -- and is refused ``${VAR}``
expansion in headers, since it is instructions from a remote party.
"""

import json
import logging
import os
import re
import urllib.parse

import pandas as pd

from .wavefiles import WAVE_X_MARKER, fetch_url_bytes, _is_url

logger = logging.getLogger("cicwave")

#- A fan-out request issues one HTTP GET per parent row, so a spec with a
#- forgotten `limit` can turn into thousands of requests against someone's
#- API. Cap it, and make the cap a spec key so a genuinely large pull is a
#- deliberate choice rather than an accident.
DEFAULT_MAX_REQUESTS = 200
DEFAULT_TIMEOUT_S = 30

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_FIELD_RE = re.compile(r"\{([^{}]+)\}")
_WAVE_FIELD_RE = _FIELD_RE

def _sanitize_name(value):
    """Make *value* usable as one segment of a wave name.

    The wave tree splits on dots and only treats a name as hierarchical
    when it has no spaces or brackets, so a value carrying any of those
    would either invent a tree level or flatten the whole thing. A field
    the source left empty becomes ``unset`` rather than an empty
    segment, which would show up as a blank row in the tree.
    """
    if value is None:
        return "unset"
    text = re.sub(r"[^\w\-+]+", "_", str(value).strip())
    return text.strip("_") or "unset"

_REQUEST_KEYS = {
    "name", "path", "url", "params", "records", "where", "merge", "keep",
    "for_each", "headers_as_columns",
}
_CATALOG_KEYS = {"requests", "group_name", "fetch", "derive"}
_FETCH_KEYS = {
    "path", "url", "params", "records", "where", "index", "columns",
    "values", "x_name", "unit", "rename", "derive", "filter",
    "headers_as_columns",
}
_SOURCE_KEYS = {
    "base_url", "requests", "headers", "timeout", "max_requests",
    "require_consistent_headers", "rename", "derive", "filter", "catalog",
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


def _resolve_headers(headers, origin=None):
    """Resolve request headers, expanding ``${VAR}`` for local specs only.

    A spec that arrived over the network is instructions from whoever
    served it. Letting one expand an environment variable would turn
    ``cicwave https://somewhere/spec.yaml`` into "send my token to a host
    of their choosing", so remote specs are refused that. A local spec is
    a file the user chose to open, and keeps the feature.
    """
    resolved = {}
    for key, value in (headers or {}).items():
        text = str(value)
        if origin and _ENV_RE.search(text):
            raise ValueError(
                "source: header '%s' expands an environment variable, which "
                "a spec fetched from %s is not allowed to do -- it would "
                "send a local secret to whichever host the spec names. Save "
                "the spec to a file if you trust it." % (key, origin))
        resolved[str(key)] = _expand_env(text)
    return resolved


def _origin_base_url(origin):
    """``scheme://host`` of the spec's own URL, or ``None``.

    Lets a service publish a spec whose paths are all relative, so the
    same spec works wherever that service is reachable.
    """
    if not origin:
        return None
    parts = urllib.parse.urlsplit(origin)
    if not (parts.scheme and parts.netloc):
        return None
    return "%s://%s" % (parts.scheme, parts.netloc)


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


_DERIVE_KEYS = {
    "from", "regex", "group", "kv", "sep", "assign", "split", "index",
    "type", "scale", "offset",
}


def _derive_extractor(rule, what):
    """Return ``f(text) -> value`` for one derive rule.

    Shared by the frame path (a whole column at a time) and the catalog
    path, which derives from raw records before any frame exists.
    """
    if not isinstance(rule, dict):
        raise ValueError("%s: must be a mapping" % what)
    _check_keys(rule, _DERIVE_KEYS, what)

    if "regex" in rule:
        pattern = re.compile(str(rule["regex"]))
        group = int(rule.get("group", 1))
        if group > pattern.groups:
            raise ValueError(
                "%s: regex has %d capture group(s), asked for group %d" % (
                    what, pattern.groups, group))

        def extract(text):
            match = pattern.search(text)
            return match.group(group) if match else None

        return extract

    if "kv" in rule:
        #- "KEY=VAL;KEY=VAL" strings, the shape a set of test conditions
        #- usually gets flattened into.
        sep = str(rule.get("sep", ";"))
        assign = str(rule.get("assign", "="))
        key = str(rule["kv"])

        def pick(text):
            for field in text.split(sep):
                if assign in field:
                    name, value = field.split(assign, 1)
                    if name.strip() == key:
                        return value
            return None

        return pick

    if "split" in rule:
        sep = str(rule["split"])
        index = int(rule.get("index", -1))

        def part(text):
            pieces = text.split(sep)
            try:
                return pieces[index]
            except IndexError:
                return None

        return part

    raise ValueError("%s: needs one of 'regex', 'kv' or 'split'" % what)


def _cast_value(value, dtype, what, scale=None, offset=None):
    if dtype in (None, "str") and scale is None and offset is None:
        return value
    if dtype not in (None, "int", "float", "str"):
        raise ValueError(
            "%s: unknown type '%s' (int, float or str)" % (what, dtype))
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    number = number * float(1 if scale is None else scale)
    number = number + float(0 if offset is None else offset)
    return int(number) if dtype == "int" else number


def _derive_series(df, name, rule):
    what = "source: derive '%s'" % name
    src = rule.get("from") if isinstance(rule, dict) else None
    if not src:
        raise ValueError("%s: needs a 'from' column" % what)
    if src not in df.columns:
        raise ValueError(
            "%s: column '%s' not in the fetched data (available: %s)" % (
                what, src, ", ".join(map(str, df.columns))))

    out = df[src].astype(str).map(_derive_extractor(rule, what))

    dtype = rule.get("type")
    scale, offset = rule.get("scale"), rule.get("offset")
    if dtype in ("int", "float") or scale is not None or offset is not None:
        out = pd.to_numeric(out, errors="coerce")
        if scale is not None:
            out = out * float(scale)
        if offset is not None:
            out = out + float(offset)
        if dtype == "int":
            out = out.astype("Int64")
    elif dtype not in (None, "str"):
        raise ValueError(
            "%s: unknown type '%s' (int, float or str)" % (what, dtype))
    return out


def _derive_records(rows, derive, what):
    """Add derived fields to raw records, in place.

    Records rather than a frame: a catalog row carries the ids that get
    templated into later request URLs, and a round trip through pandas
    would turn an integer id into ``9402112.0``.
    """
    for name, rule in (derive or {}).items():
        rule_what = "%s: derive '%s'" % (what, name)
        src = rule.get("from") if isinstance(rule, dict) else None
        if not src:
            raise ValueError("%s: needs a 'from' field" % rule_what)
        extract = _derive_extractor(rule, rule_what)
        dtype = rule.get("type")
        scale, offset = rule.get("scale"), rule.get("offset")
        for row in rows:
            if src not in row:
                raise ValueError(
                    "%s: field '%s' is not in the records (available: %s)" % (
                        rule_what, src, ", ".join(sorted(row)) or "none"))
            value = extract("" if row[src] is None else str(row[src]))
            row[name] = _cast_value(value, dtype, rule_what, scale, offset)
    return rows


def _make_fetcher(src, origin=None):
    return _Fetcher(
        base_url=src.get("base_url") or _origin_base_url(origin),
        headers=_resolve_headers(src.get("headers"), origin),
        timeout=float(src.get("timeout", DEFAULT_TIMEOUT_S)),
        max_requests=int(src.get("max_requests", DEFAULT_MAX_REQUESTS)),
        consistent_headers=src.get("require_consistent_headers"),
    )


def _run_requests(requests_, fetcher, what):
    """Run a list of requests in order; return the last one's records."""
    if not requests_:
        raise ValueError("%s: needs a 'requests' list" % what)
    if not isinstance(requests_, list):
        raise ValueError("%s: 'requests' must be a list" % what)
    results = {}
    rows = []
    for idx, req in enumerate(requests_):
        if not isinstance(req, dict):
            raise ValueError("%s: request #%d must be a mapping" % (what, idx))
        rows = _run_request(req, idx, results, fetcher)
        results[req.get("name") or "#%d" % idx] = rows
        logger.debug("%s: request %s produced %d row(s)",
                     what, req.get("name") or "#%d" % idx, len(rows))
    return rows


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

    fetcher = _make_fetcher(src, getattr(spec, "origin", None))
    rows = _run_requests(requests_, fetcher, "source")

    if not rows:
        raise ValueError(
            "source: the last request produced no records; check its "
            "'where' filter and query parameters")

    df = _post_process(pd.DataFrame.from_records(rows), src, "source")

    logger.info("source: %d row(s) from %d request(s)",
                len(df), fetcher.count)
    return df


def _post_process(df, block, what):
    """Apply a block's ``rename`` / ``derive`` / ``filter`` to *df*."""
    rename = block.get("rename") or {}
    missing = [c for c in rename if c not in df.columns]
    if missing:
        raise ValueError(
            "%s: rename refers to column(s) %s that were not fetched "
            "(available: %s)" % (
                what, ", ".join(missing), ", ".join(map(str, df.columns))))
    df = df.rename(columns=rename)

    for name, rule in (block.get("derive") or {}).items():
        df[name] = _derive_series(df, name, rule)

    #- Runs after rename/derive, so it can select on a column that only
    #- exists once the raw records have been reshaped -- which a
    #- request's `where` cannot. `where` prunes requests, `filter` prunes
    #- rows; a narrow spec usually wants both.
    row_filter = block.get("filter") or {}
    if row_filter:
        unknown = [c for c in row_filter if c not in df.columns]
        if unknown:
            raise ValueError(
                "%s: filter refers to column(s) %s that do not exist "
                "(available: %s)" % (
                    what, ", ".join(unknown), ", ".join(map(str, df.columns))))
        before = len(df)
        keep = df.apply(lambda r: _matches(r, row_filter), axis=1)
        df = df[keep].reset_index(drop=True)
        if df.empty:
            raise ValueError(
                "%s: filter %r matched none of the %d fetched row(s)" % (
                    what, row_filter, before))
        logger.debug("%s: filter kept %d of %d row(s)", what, len(df), before)
    return df


def has_catalog(spec):
    """True if *spec* enumerates series to fetch on demand."""
    return bool(isinstance(spec, dict)
                and (spec.get("source") or {}).get("catalog"))


class LazyCatalog:
    """A catalog of series that are fetched one group at a time.

    Enumerating what a service holds is cheap -- one listing request --
    while downloading all of it is not: a characterisation database can
    list thousands of sweeps, which is minutes of requests and mostly
    data nobody asked to see. So the catalog names the series, the wave
    tree is built from those names, and :meth:`load_group` runs the
    per-group request the first time something under it is plotted.

    One fetch usually yields several waves (a sweep returns every board
    it measured), so a group is a branch of the tree rather than a leaf.
    """

    def __init__(self, spec):
        src = spec.get("source") or {}
        _check_keys(src, _SOURCE_KEYS, "source")
        catalog = src.get("catalog") or {}
        _check_keys(catalog, _CATALOG_KEYS, "source: catalog")

        self._src = src
        self._catalog = catalog
        self._fetch = catalog.get("fetch") or {}
        _check_keys(self._fetch, _FETCH_KEYS, "source: catalog: fetch")
        if not self._fetch:
            raise ValueError(
                "source: catalog needs a 'fetch' block saying how to load "
                "one group's data")
        for key in ("index", "columns", "values"):
            if not self._fetch.get(key):
                raise ValueError(
                    "source: catalog: fetch needs '%s' (the field in each "
                    "fetched record that gives the %s)" % (key, {
                        "index": "wave name",
                        "columns": "x-axis",
                        "values": "y-axis",
                    }[key]))

        self._group_template = catalog.get("group_name")
        if not self._group_template:
            raise ValueError(
                "source: catalog needs 'group_name', a template naming each "
                "series (dots nest in the wave tree)")

        self._fetcher = _make_fetcher(src, getattr(spec, "origin", None))
        self._rows_by_group = {}
        self.groups = []

    def load(self):
        """Run the catalog requests and return the group names."""
        rows = _run_requests(self._catalog.get("requests"), self._fetcher,
                             "source: catalog")
        #- Lets group_name split on something the listing only carries
        #- inside a packed field, e.g. one setting out of a
        #- "KEY=VAL;KEY=VAL" condition string.
        rows = _derive_records(rows, self._catalog.get("derive"),
                               "source: catalog")
        fields = _WAVE_FIELD_RE.findall(self._group_template)
        collisions = 0
        for row in rows:
            missing = [f for f in fields if f not in row]
            if missing:
                raise ValueError(
                    "source: catalog: group_name refers to %s, which the "
                    "catalog records do not have (available: %s)" % (
                        ", ".join(missing), ", ".join(sorted(row))))
            name = _WAVE_FIELD_RE.sub(
                lambda m: _sanitize_name(row[m.group(1)]),
                self._group_template)
            #- Two catalog rows naming the same group would each fetch
            #- into it; keep the first so a click maps to exactly one
            #- request, and say how many entries that hid -- a template
            #- missing a distinguishing field otherwise looks like a
            #- smaller database rather than a naming bug.
            if name not in self._rows_by_group:
                self._rows_by_group[name] = row
                self.groups.append(name)
            else:
                collisions += 1
        if collisions:
            logger.warning(
                "source: catalog: %d of %d entries share a group_name with "
                "an earlier one and are not reachable; add a field that "
                "tells them apart to '%s'",
                collisions, len(rows), self._group_template)
        logger.info("source: catalog lists %d group(s) from %d request(s)",
                    len(self.groups), self._fetcher.count)
        return self.groups

    def _from_envelope(self, payload, template, what, sanitize=True):
        """Fill ``{field}`` from the response's own scalar fields."""
        scalars = {k: v for k, v in (payload or {}).items()
                   if isinstance(payload, dict)
                   and not isinstance(v, (list, dict))}

        def sub(match):
            key = match.group(1)
            if key not in scalars:
                raise ValueError(
                    "source: catalog: fetch: %s refers to {%s}, which is not "
                    "a scalar field of the response (available: %s)" % (
                        what, key, ", ".join(sorted(scalars)) or "none"))
            value = scalars[key]
            return _sanitize_name(value) if sanitize else str(
                "" if value is None else value)

        return _WAVE_FIELD_RE.sub(sub, template)

    def _x_display(self, payload, default):
        """Label for this group's x column, from the response envelope."""
        template = self._fetch.get("x_name")
        if not template:
            return _sanitize_name(default)
        return self._from_envelope(payload, template, "x_name")

    def _unit(self, payload):
        """The y unit for this group, if the spec says where to find it."""
        template = self._fetch.get("unit")
        if not template:
            return None
        #- Not sanitised: a unit is a label, not a name, so "°C" and
        #- "%" have to survive intact.
        return self._from_envelope(payload, str(template), "unit",
                                   sanitize=False).strip() or None

    def load_group(self, name):
        """Fetch one group and return it as a wide frame."""
        row = self._rows_by_group.get(name)
        if row is None:
            raise ValueError("source: no catalog entry named '%s'" % name)

        what = "source: catalog: fetch"
        url = _build_url(self._fetcher.base_url, self._fetch, row, what)
        payload, resp_headers = self._fetcher.get(url)
        records = [r for r in _records_of(payload, self._fetch.get("records"),
                                          url)
                   if _matches(r, self._fetch.get("where") or {})]
        for record in records:
            for col, header in (
                    self._fetch.get("headers_as_columns") or {}).items():
                record[col] = resp_headers.get(header)
        if not records:
            return pd.DataFrame()

        df = pd.DataFrame.from_records(records)
        df = _post_process(df, self._fetch, what)

        index_col = self._fetch["index"]
        x_col = self._fetch["columns"]
        value_col = self._fetch["values"]
        for col in (index_col, x_col, value_col):
            if col not in df.columns:
                raise ValueError(
                    "%s: '%s' is not a field of the fetched records "
                    "(available: %s)" % (
                        what, col, ", ".join(map(str, df.columns))))

        #- The group's own x column, named after the group so two groups
        #- swept against different axes can share one frame. What follows
        #- the marker is the axis label; a spec usually builds it from
        #- the response envelope (``x_name: "{axis}_{x_label}"``) so a
        #- unit suffix there drives cicwave's usual axis scaling.
        x_name = "%s%s%s" % (name, WAVE_X_MARKER,
                             self._x_display(payload, x_col))
        wide = df.pivot_table(index=x_col, columns=index_col,
                              values=value_col, aggfunc="mean",
                              observed=True)
        wide.columns = ["%s.%s" % (name, _sanitize_name(c))
                        for c in wide.columns]
        wide = wide.reset_index().rename(columns={x_col: x_name})
        try:
            wide[x_name] = pd.to_numeric(wide[x_name])
            wide = wide.sort_values(x_name)
        except (ValueError, TypeError):
            pass
        waves = [c for c in wide.columns if c != x_name]
        attrs = {"cicwave_wave_x": {c: x_name for c in waves}}
        unit = self._unit(payload)
        if unit:
            attrs["cicwave_wave_unit"] = {c: unit for c in waves}
        wide.attrs = attrs
        return wide


def is_source_spec_file(path):
    """True if *path* is a YAML/JSON pivot spec that fetches its own data.

    Used wherever a path could be either a data file or a spec: the CLI's
    positional argument, and the GUI's open dialog / drag-and-drop.
    """
    if not (os.path.isfile(path) or _is_url(path)):
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
