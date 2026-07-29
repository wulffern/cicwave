######################################################################
##        Copyright (c) 2026 Carsten Wulff Software, Norway
## ###################################################################
## Created       : wulff at 2026-5-8
## ###################################################################
##  The MIT License (MIT)
##
##  Permission is hereby granted, free of charge, to any person obtaining a copy
##  of this software and associated documentation files (the "Software"), to deal
##  in the Software without restriction, including without limitation the rights
##  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
##  copies of the Software, and to permit persons to whom the Software is
##  furnished to do so, subject to the following conditions:
##
##  The above copyright notice and this permission notice shall be included in all
##  copies or substantial portions of the Software.
##
##  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
##  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
##  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
##  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
##  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
##  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
##  SOFTWARE.
##
######################################################################


import csv as _csv_mod
import io as _io
import ipaddress
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import numpy as np
import pandas as pd
from matplotlib.ticker import EngFormatter

from .ngraw import toDataFrame as _ngraw_toDataFrame
from .stdf import toDataFrame as _stdf_toDataFrame


# ---------------------------------------------------------------------------
# URL-based data sources (REST endpoints, hosted CSV/JSON, ...).
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r'^https?://', re.IGNORECASE)


def _is_url(s):
    return bool(_URL_RE.match(str(s)))


#- Safety cap so an unbounded / malicious endpoint can't exhaust memory.
_URL_MAX_BYTES = 200 * 1024 * 1024  # 200 MB
_URL_TIMEOUT_S = 30

#- Content-Type -> extension, used when a URL has no recognizable suffix
#- (typical for REST API endpoints) and no explicit ``--format`` was given.
_CONTENT_TYPE_EXT = {
    'text/csv': '.csv',
    'application/csv': '.csv',
    'text/tab-separated-values': '.tsv',
    'application/json': '.json',
    'text/json': '.json',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
    'application/vnd.ms-excel': '.xls',
    'text/html': '.html',
    'application/xml': '.xml',
    'text/xml': '.xml',
    'application/parquet': '.parquet',
    'application/vnd.apache.parquet': '.parquet',
}


def _check_not_link_local(url):
    """Refuse a URL that resolves to a link-local address.

    169.254.0.0/16 (and IPv6 fe80::/10) covers the cloud-metadata
    endpoints (AWS/GCP/Azure/OpenStack all use 169.254.169.254) that a
    malicious or careless session/pivot file could point at to read
    instance credentials via a `.cicwave.yaml` someone opens without
    knowing it references a URL. Ordinary REST APIs and internal
    services never live on a link-local address, so this has no
    legitimate false-positive case; it does not touch RFC1918 private
    ranges, which are a real use case for internal APIs.
    """
    host = urllib.parse.urlsplit(url).hostname
    if not host:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ValueError("failed to resolve %s: %s" % (host, e)) from e
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.is_link_local:
            raise ValueError(
                "refusing to fetch %s: %s resolves to link-local address "
                "%s (cloud metadata endpoints live here)" % (url, host, ip))


def _fetch_url(url, timeout=_URL_TIMEOUT_S, max_bytes=_URL_MAX_BYTES):
    """Fetch *url* into memory. Returns ``(bytes, content_type_or_None)``.

    Raises :class:`ValueError` with a readable message on network/HTTP
    errors instead of leaking urllib's exception types up to the UI.
    """
    _check_not_link_local(url)
    req = urllib.request.Request(url, headers={"User-Agent": "cicwave"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get_content_type()
            data = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        raise ValueError(
            "failed to fetch %s: HTTP %s %s" % (url, e.code, e.reason)) from e
    except urllib.error.URLError as e:
        raise ValueError("failed to fetch %s: %s" % (url, e.reason)) from e
    if len(data) > max_bytes:
        raise ValueError(
            "%s exceeds the %d MB download limit" %
            (url, max_bytes // (1024 * 1024)))
    return data, content_type


#- Sentinel used by ``WaveFile.csv_comment_override`` to distinguish
#- "user has not configured anything" (use per-format default) from
#- "user explicitly disabled comment stripping" (override = None).
_UNSET = object()


def _normalize_csv_comment(s):
    """Normalize a user-supplied comment-line marker.

    Empty string ``""`` is the explicit "disable" sentinel and returns
    ``None`` (no comment stripping). Any other non-blank string is
    accepted as-is (single character is fast-path through pandas;
    multi-character is handled via a pre-filter at read time).
    Whitespace-only input is rejected so the CLI surfaces a clear error.
    """
    if s is None:
        return None
    if not isinstance(s, str):
        raise ValueError("comment marker must be a string")
    if s == "":
        return None
    if not s.strip():
        raise ValueError(
            "comment marker must contain non-whitespace characters; "
            "got %r" % s)
    return s


def _normalize_csv_sep(sep):
    """Normalize a user-supplied CSV separator string.

    Accepts a single character, the literal escape ``\\t``, or the aliases
    ``tab`` / ``TAB``. Returns the resolved single-character separator.
    Raises :class:`ValueError` on anything else so the CLI can surface a
    clear error message rather than silently falling back to comma.
    """
    if sep is None:
        return None
    if not isinstance(sep, str) or sep == "":
        raise ValueError("CSV separator must be a non-empty string")
    low = sep.lower()
    if low in ("tab", "\\t"):
        return "\t"
    if len(sep) == 1:
        return sep
    raise ValueError(
        "CSV separator must be a single character "
        "(or 'tab' / '\\t'); got %r" % sep)

# ---------------------------------------------------------------------------
# Unit auto-detection from column names.
# ---------------------------------------------------------------------------

# SI-prefix scale factors (lower-case) plus the case-sensitive ones we
# tolerate. ``u`` is accepted as an alias for ``µ`` (micro).
_SI_PREFIXES = {
    'y': 1e-24, 'z': 1e-21, 'a': 1e-18, 'f': 1e-15, 'p': 1e-12,
    'n': 1e-9, 'u': 1e-6, 'µ': 1e-6, 'm': 1e-3,
    'k': 1e3, 'K': 1e3,
    'M': 1e6, 'G': 1e9, 'T': 1e12, 'P': 1e15, 'E': 1e18,
}

# Plain-unit symbols (no prefix scaling) that we recognise as-is.
_PLAIN_UNITS = {
    's', 'Hz', 'V', 'A', 'W', 'F', 'H', 'Ω', 'ohm', 'rad', 'deg',
    '°C', 'C', 'K', 'J', 'N', 'Pa', 'T', 'lx', 'cd',
}

# Log-domain units. Always treated as the literal string and never
# rescaled (you don't say "kdBm").
_LOG_UNITS = {
    'dB', 'dBm', 'dBV', 'dBuV', 'dBµV', 'dBc', 'dBFS', 'dBi', 'dBA',
}

# Base units that accept SI prefixes for auto-rescaling.
_PREFIXABLE_BASES = {'Hz', 'V', 'A', 's', 'W', 'F', 'H', 'Ω', 'ohm'}

# Match a unit token at the end of a column name, possibly enclosed in
# brackets/parens/braces or separated by underscore / space / slash.
# Examples that match:
#   "Frequency_MHz"        -> "MHz"
#   "Amplitude [dBm]"      -> "dBm"
#   "Power (W)"            -> "W"
#   "phase / deg"          -> "deg"
# Examples that DON'T match (good — these are node names, not units):
#   "v(out)", "i(M1.d)", "v-out"
_UNIT_SUFFIX_RE = re.compile(
    r"""
    [\s_/]*
    (?:
        \[ \s* (?P<b>[^\[\]]+?) \s* \]      # [unit]
      | \( \s* (?P<p>[^()]+?)   \s* \)      # (unit)
      | \{ \s* (?P<c>[^{}]+?)   \s* \}      # {unit}
      |       (?P<n>[A-Za-zµΩ°]+)           # bare suffix
    )
    \s*$
    """,
    re.VERBOSE,
)


def _classify_unit_token(tok):
    """Return ``(scale, unit_str)`` if ``tok`` is a recognised unit token,
    otherwise ``None``. ``scale`` is the multiplicative factor to apply to
    the data so the data, after scaling, is in the returned base unit."""
    if not tok:
        return None
    t = tok.strip()
    if not t:
        return None
    # Log-domain units: literal, no scaling.
    if t in _LOG_UNITS:
        return (1.0, t)
    # Plain units: literal, no scaling.
    if t in _PLAIN_UNITS:
        return (1.0, t)
    # SI prefix + base unit -> rescale to base.
    if len(t) >= 2:
        prefix, rest = t[0], t[1:]
        if prefix in _SI_PREFIXES and rest in _PREFIXABLE_BASES:
            return (_SI_PREFIXES[prefix], rest)
    return None


def parse_unit_from_name(name):
    """Detect the unit encoded in a column name.

    Returns a tuple ``(scale, unit, stripped_label)`` or ``None`` if no
    unit could be identified.

    - ``scale`` is the multiplier to apply to the data so it ends up in
      the returned base unit (e.g. ``Frequency_MHz`` -> 1e6 + ``Hz``).
    - ``unit`` is the unit string suitable for ``EngFormatter`` /
      axis labels.
    - ``stripped_label`` is ``name`` with the trailing unit token removed,
      for use as a clean axis label.
    """
    if not name:
        return None
    s = str(name)
    m = _UNIT_SUFFIX_RE.search(s)
    if not m:
        return None
    tok = m.group('b') or m.group('p') or m.group('c') or m.group('n')
    cls = _classify_unit_token(tok)
    if cls is None:
        return None
    scale, unit = cls
    stripped = s[:m.start()].rstrip(" _/-:")
    return (scale, unit, stripped or s)


#- Model for wavefiles

class Wave():

    def __init__(self,wfile,key,xaxis):
        self.xaxis = xaxis
        self.wfile = wfile
        self.x = None
        self.y = None
        self.xlabel = "Samples"
        self.key = key
        self.ylabel = key + f" ({wfile.name})"
        self.logx = False
        self.logy = False
        self.xunit = ""
        self.yunit, self._yscale = self._infer_yunit_and_scale(key)
        self.tag = wfile.getTag(self.key)
        self.line = None
        self.reload()

    @staticmethod
    def _infer_yunit(key):
        unit, _scale = Wave._infer_yunit_and_scale(key)
        return unit

    @staticmethod
    def _infer_yunit_and_scale(key):
        parsed = parse_unit_from_name(key)
        if parsed is not None:
            scale, unit, _ = parsed
            return unit, scale
        kl = (key or "").lower()
        if kl.startswith("v(") or kl.startswith("v-"):
            return "V", 1.0
        if kl.startswith("i(") or kl.startswith("i-"):
            return "A", 1.0
        return "", 1.0

    def deleteLine(self):
        if(self.line):
            self.line.remove()
            self.line = None

    def plot(self,ax):
        x = np.real(self.x) if self.x is not None else None
        y = np.real(self.y) if self.y is not None else self.y
        if(x is not None):
            if(not self.logx and not self.logy):
                self.line, = ax.plot(x,y,label=self.ylabel)
            elif(self.logx and not self.logy):
                self.line, = ax.semilogx(x,y,label=self.ylabel)
            elif(not self.logx and self.logy):
                self.line, = ax.semilogy(x,y,label=self.ylabel)
            elif(self.logx and self.logy):
                self.line, = ax.loglog(x,y,label=self.ylabel)
        else:
            self.line, = ax.plot(y,label=self.ylabel)

        if self.xunit:
            ax.xaxis.set_major_formatter(EngFormatter(unit=self.xunit))
        if self.yunit:
            ax.yaxis.set_major_formatter(EngFormatter(unit=self.yunit))

    def _set_x_from_column(self, col, label, unit, logx=False):
        arr = self.wfile.df[col].to_numpy()
        parsed = parse_unit_from_name(col)
        if parsed is not None:
            scale, base_unit, clean = parsed
            if scale != 1.0:
                arr = arr * scale
            self.x = arr
            self.xlabel = clean or label
            self.xunit = base_unit
        else:
            self.x = arr
            self.xlabel = label
            self.xunit = unit
        self.logx = logx

    def reload(self):
        self.wfile.reload()
        keys = self.wfile.df.columns

        if "time" in keys:
            self._set_x_from_column("time", "Time", "s")
        elif "frequency" in keys:
            self._set_x_from_column("frequency", "Frequency", "Hz",
                                    logx=True)
        elif "v(v-sweep)" in keys:
            self._set_x_from_column("v(v-sweep)", "Voltage", "V")
        elif "i(i-sweep)" in keys:
            self._set_x_from_column("i(i-sweep)", "Current", "A")
        elif "temp-sweep" in keys:
            self._set_x_from_column("temp-sweep", "Temperature", "°C")
        elif self.xaxis and self.xaxis in keys:
            self._set_x_from_column(self.xaxis, " ", "")
        else:
            for col in keys:
                if col == self.key:
                    continue
                if parse_unit_from_name(col) is not None:
                    self._set_x_from_column(col, col, "")
                    break

        if self.key in keys:
            y = self.wfile.df[self.key].to_numpy()
            if self._yscale != 1.0:
                y = y * self._yscale
            self.y = y

        if self.line:
            if self.x is not None:
                self.line.set_xdata(self.x)
            self.line.set_ydata(self.y)


class WaveFile():

    # Extensions where reading the column list is much cheaper than reading
    # the full file. For everything else we fall back to a full load on open
    # (matching pre-lazy behavior).
    _HEADER_ONLY_EXTS = {
        '.csv', '.tsv', '.txt',
        '.xlsx', '.xls', '.ods',
        '.parquet', '.feather',
        '.dat', '.spe', '.cou', '.chi',
    }

    #- Extensions that map to the whitespace-separated reader. Kept as a
    #- class attribute so tests / external callers can introspect.
    _WHITESPACE_EXTS = ('.dat', '.spe', '.cou', '.chi')

    #- Blocked for URL sources: unpickling / HDF5 loading from an
    #- arbitrary remote endpoint is a deserialization / native-library
    #- risk we don't want to take on a user-supplied URL.
    _REMOTE_BLOCKED_EXTS = {'.pkl', '.pickle', '.h5', '.hdf5'}

    #- Formats that need their format-specific local-file parser
    #- (custom text parsing, sidecar files, etc.) and aren't worth
    #- porting to an in-memory buffer yet — not realistic REST payloads
    #- anyway (lab-instrument formats, not climate/health datasets).
    _REMOTE_UNSUPPORTED_EXTS = {
        '.raw', '.vcd', '.iqvsa', '.prn', '.npz', '.stdf',
        '.dat', '.spe', '.cou', '.chi',
        '.stata', '.dta', '.sas7bdat', '.sav',
    }

    def __init__(self, fname, xaxis, sheet_name=0, df=None, fmt=None):
        self.xaxis = xaxis
        self.fname = fname
        self.sheet_name = sheet_name
        self.name = os.path.basename(fname)
        if isinstance(sheet_name, str):
            self.name += " [%s]" % sheet_name
        self.waves = dict()
        self._df = df
        self._columns = None
        self._virtual = df is not None
        self.fmt = fmt
        self._remote = (not self._virtual) and _is_url(fname)
        self._remote_bytes = None
        self._remote_content_type = None
        if self._virtual and df is not None:
            #- In-memory frames (pivot / session) skip global ``--twos-complement``
            #- so we do not re-decode already-processed floats. Use pivot
            #- ``analysis.preprocess`` or per-wave GUI decode instead.
            self._df = df.copy()
            self._columns = list(self._df.columns)
        self.modified = None
        self.reload()

    @property
    def df(self):
        if self._virtual:
            return self._df
        if self._df is None:
            self._df = self._read_file()
            self._columns = list(self._df.columns)
        return self._df

    @df.setter
    def df(self, value):
        self._df = value
        if value is not None:
            self._columns = list(value.columns)

    def reload(self):
        """Refresh metadata. Header-only on first open for cheap formats; the
        full DataFrame is loaded lazily on first ``df`` access. If the file
        on disk has changed since we last loaded it, drop any cached data so
        the next access re-reads."""
        if self._virtual:
            return

        if self._remote:
            #- Remote sources are fetched once and treated as a static
            #- snapshot: there's no cheap mtime check over HTTP, and
            #- re-fetching on every GUI refresh would be surprising
            #- (slow, and could hammer rate-limited APIs). Re-open the
            #- URL (or restart cicwave) to get a fresh copy.
            if self._df is None and self._remote_bytes is None:
                self._read_header_or_full()
            return

        if self.modified is None:
            self._read_header_or_full()
            self.modified = os.path.getmtime(self.fname)
            return

        newmodified = os.path.getmtime(self.fname)
        if newmodified > self.modified:
            had_full = self._df is not None
            self._df = None
            self._columns = None
            self._read_header_or_full()
            if had_full:
                # Trigger full reload right away to match pre-lazy semantics
                # for callers that expected fresh data after reload().
                _ = self.df
            self.modified = newmodified

    def _read_header_or_full(self):
        """Populate ``self._columns`` cheaply if possible; otherwise fall back
        to a full read (which also fills ``self._df``)."""
        if self._remote:
            self._df = self._read_file()
            self._columns = list(self._df.columns)
            return
        ext = os.path.splitext(self.fname)[1].lower()
        if ext in self._HEADER_ONLY_EXTS:
            try:
                self._columns = list(self._read_header(ext))
                return
            except Exception:
                self._columns = None
        # Formats without a cheap header path (or header read failed):
        # do a full load now, matching pre-lazy behavior.
        self._df = self._read_file()
        self._columns = list(self._df.columns)

    def _read_header(self, ext):
        if ext in ('.csv',):
            return self._read_csv_header(self._sniff_csv_sep(default=','))
        if ext in ('.tsv', '.txt'):
            return self._read_csv_header('\t')
        if ext in self._WHITESPACE_EXTS:
            return self._read_whitespace_header()
        if ext in ('.xlsx', '.xls', '.ods'):
            df = pd.read_excel(self.fname, sheet_name=self.sheet_name, nrows=0)
            return [c.strip() for c in df.columns]
        if ext == '.parquet':
            import pyarrow.parquet as pq
            return list(pq.read_schema(self.fname).names)
        if ext == '.feather':
            import pyarrow.feather as pf
            return list(pf.read_table(self.fname, columns=[]).schema.names)
        raise ValueError("no header reader for %s" % ext)

    def _read_csv_header(self, sep):
        comment = self._effective_comment(default=None)
        if comment and len(comment) > 1:
            #- Multi-char comment: pre-filter and parse from memory.
            buf = self._prefilter_text(comment)
            df = pd.read_csv(buf, sep=sep, nrows=0)
            return [c.strip() for c in df.columns]
        c_kw = {'comment': comment} if comment else {}
        try:
            df = pd.read_csv(self.fname, sep=sep, nrows=0,
                             engine='c', memory_map=True, **c_kw)
        except Exception:
            try:
                df = pd.read_csv(self.fname, sep=sep, nrows=0, **c_kw)
            except Exception:
                df = pd.read_csv(self.fname, sep=None, engine='python',
                                 nrows=0, **c_kw)
        return [c.strip() for c in df.columns]

    PANDAS_READERS = {
        '.prn':     lambda self: self._read_prn(),
        '.vcd':     lambda self: read_vcd(self.fname),
        '.iqvsa':   lambda self: read_iqvsa(self.fname),
        '.csv':     lambda self: self._read_csv(self._sniff_csv_sep(default=',')),
        '.tsv':     lambda self: self._read_csv('\t'),
        '.txt':     lambda self: self._read_csv('\t'),
        '.dat':     lambda self: self._read_whitespace(),
        '.spe':     lambda self: self._read_whitespace(),
        '.cou':     lambda self: self._read_whitespace(),
        '.chi':     lambda self: self._read_whitespace(),
        '.xlsx':    lambda self: self._read_excel(),
        '.xls':     lambda self: self._read_excel(),
        '.ods':     lambda self: self._read_excel(),
        '.pkl':     lambda self: pd.read_pickle(self.fname),
        '.pickle':  lambda self: pd.read_pickle(self.fname),
        '.json':    lambda self: pd.read_json(self.fname),
        '.parquet': lambda self: pd.read_parquet(self.fname),
        '.feather': lambda self: pd.read_feather(self.fname),
        '.npz':     lambda self: read_npz(self.fname),
        '.h5':      lambda self: pd.read_hdf(self.fname),
        '.hdf5':    lambda self: pd.read_hdf(self.fname),
        '.html':    lambda self: pd.read_html(self.fname)[0],
        '.xml':     lambda self: pd.read_xml(self.fname),
        '.fwf':     lambda self: pd.read_fwf(self.fname),
        '.stata':   lambda self: pd.read_stata(self.fname),
        '.dta':     lambda self: pd.read_stata(self.fname),
        '.sas7bdat': lambda self: pd.read_sas(self.fname),
        '.sav':     lambda self: pd.read_spss(self.fname),
    }

    def _read_file(self):
        if self._remote:
            return self._apply_twos_decode_df(self._read_remote_file())
        lower = self.fname.lower()
        if lower.endswith('.stdf') or lower.endswith('.stdf.gz'):
            return self._apply_twos_decode_df(_stdf_toDataFrame(self.fname))
        ext = os.path.splitext(self.fname)[1].lower()
        reader = self.PANDAS_READERS.get(ext)
        if reader:
            df = reader(self)
        else:
            df = _ngraw_toDataFrame(self.fname)
        return self._apply_twos_decode_df(df)

    def _remote_ext(self):
        """Resolve the format to use for a URL source.

        Priority: explicit ``--format`` override, then the URL path's own
        extension (query string ignored), then the response's
        Content-Type header. Returns ``''`` if none of those resolve.
        """
        if self.fmt:
            f = self.fmt.lower().lstrip('.')
            return '.' + f
        path = urllib.parse.urlsplit(self.fname).path
        ext = os.path.splitext(path)[1].lower()
        if ext:
            return ext
        return _CONTENT_TYPE_EXT.get(self._remote_content_type or '', '')

    def _read_remote_file(self):
        if self._remote_bytes is None:
            self._remote_bytes, self._remote_content_type = _fetch_url(self.fname)

        ext = self._remote_ext()
        if ext in self._REMOTE_BLOCKED_EXTS:
            raise ValueError(
                "refusing to load %s over a URL (arbitrary deserialization "
                "risk) - download it locally first if you trust the source"
                % ext)
        if ext in self._REMOTE_UNSUPPORTED_EXTS:
            raise ValueError(
                "%s is not supported for URL sources yet - download the "
                "file locally first" % ext)
        if not ext:
            raise ValueError(
                "could not determine a file format for %s; pass --format "
                "(e.g. --format csv or --format json)" % self.fname)

        if ext == '.csv':
            return self._parse_remote_csv(self._sniff_csv_sep(default=','))
        if ext in ('.tsv', '.txt'):
            return self._parse_remote_csv('\t')
        if ext == '.json':
            return pd.read_json(_io.BytesIO(self._remote_bytes))
        if ext in ('.xlsx', '.xls', '.ods'):
            df = pd.read_excel(_io.BytesIO(self._remote_bytes),
                               sheet_name=self.sheet_name)
            df.columns = [c.strip() for c in df.columns]
            return df
        if ext == '.parquet':
            return pd.read_parquet(_io.BytesIO(self._remote_bytes))
        if ext == '.feather':
            return pd.read_feather(_io.BytesIO(self._remote_bytes))
        if ext == '.html':
            return pd.read_html(_io.BytesIO(self._remote_bytes))[0]
        if ext == '.xml':
            return pd.read_xml(_io.BytesIO(self._remote_bytes))
        if ext == '.fwf':
            return pd.read_fwf(_io.BytesIO(self._remote_bytes))
        raise ValueError("unsupported format %r for URL sources" % ext)

    def _parse_remote_csv(self, sep):
        comment = self._effective_comment(default=None)
        text = self._remote_bytes.decode('utf-8', errors='replace')
        if comment and len(comment) > 1:
            text = '\n'.join(
                ln for ln in text.splitlines()
                if not ln.lstrip().startswith(comment))
            df = pd.read_csv(_io.StringIO(text), sep=sep)
            df.columns = [c.strip() for c in df.columns]
            return df
        c_kw = {'comment': comment} if comment else {}
        try:
            df = pd.read_csv(_io.StringIO(text), sep=sep, engine='c', **c_kw)
        except Exception:
            df = pd.read_csv(_io.StringIO(text), sep=None,
                             engine='python', **c_kw)
        df.columns = [c.strip() for c in df.columns]
        return df

    # Candidate delimiters for CSV auto-detection. Order is the tie-break
    # preference (',' wins over ';' when counts are equal).
    _CSV_DELIMITERS = (',', ';', '\t', '|')

    #- Class-level override set by the CLI's ``--csv-sep`` flag. When
    #- non-None, ``_sniff_csv_sep`` returns this verbatim instead of
    #- inspecting the file. Use ``set_csv_sep_override`` to set it so the
    #- usual aliases (``tab``, ``\t``, ``\\t``) are normalized.
    csv_sep_override = None

    #- Class-level override set by the CLI's ``--csv-comment`` flag.
    #- Sentinel ``_UNSET`` means "use per-format default"; ``None`` means
    #- "explicitly disable comment stripping"; any string is the marker.
    csv_comment_override = _UNSET

    #- Optional global 2's complement decode after load (``--twos-complement``).
    twos_width = None
    twos_columns = None  # None = all columns except common time names

    @classmethod
    def set_twos_complement(cls, width_bits, columns=None):
        """Decode unsigned integer columns as *width_bits* 2's complement.

        Pass ``width_bits=None`` to disable. *columns* is an iterable of
        names, or ``None`` to apply to every column except ``time`` /
        ``Time [s]`` / ``frequency`` / ``temp-sweep`` / ``v(v-sweep)`` /
        ``i(i-sweep)``.
        """
        if width_bits is None:
            cls.twos_width = None
            cls.twos_columns = None
            return
        cls.twos_width = int(width_bits)
        cls.twos_columns = tuple(columns) if columns else None

    @classmethod
    def _twos_skip_columns(cls):
        from .analysis import DEFAULT_TWOS_SKIP_COLUMNS
        return DEFAULT_TWOS_SKIP_COLUMNS

    def _apply_twos_decode_df(self, df):
        if self.twos_width is None:
            return df
        from .analysis import decode_twos_complement
        skip = self._twos_skip_columns()
        cols = self.twos_columns
        if cols is None:
            cols = [c for c in df.columns if c not in skip]
        for c in cols:
            if c not in df.columns:
                continue
            try:
                df[c] = decode_twos_complement(
                    df[c].to_numpy(), self.twos_width)
            except Exception:
                pass
        return df

    @classmethod
    def set_csv_sep_override(cls, sep):
        """Set a global CSV delimiter override (used by ``--csv-sep``).

        Pass ``None`` to clear. Accepts a single character or one of the
        aliases ``tab`` / ``\\t``. Raises :class:`ValueError` on bad input.
        """
        if sep is None:
            cls.csv_sep_override = None
            return
        cls.csv_sep_override = _normalize_csv_sep(sep)

    @classmethod
    def set_csv_comment_override(cls, marker):
        """Set a global comment-marker override (used by ``--csv-comment``).

        Pass the sentinel ``_UNSET`` (or call with ``marker=_UNSET``) to
        clear and fall back to per-format defaults. Pass ``""`` to
        explicitly disable comment stripping. Pass any non-blank string
        to use as the marker. Raises :class:`ValueError` on bad input.
        """
        if marker is _UNSET:
            cls.csv_comment_override = _UNSET
            return
        cls.csv_comment_override = _normalize_csv_comment(marker)

    def _effective_comment(self, default=None):
        """Resolve the comment marker for this file.

        If a global override has been set via
        :meth:`set_csv_comment_override`, it wins (including the explicit
        ``None`` "disabled" value). Otherwise return ``default``, which
        callers pick to match the file format (``'#'`` for whitespace
        formats, ``None`` for plain CSV/TSV).
        """
        if self.csv_comment_override is _UNSET:
            return default
        return self.csv_comment_override

    def _sniff_csv_sep(self, default=','):
        """Resolve the CSV delimiter for ``self.fname``.

        If a global override has been set via :meth:`set_csv_sep_override`
        (typically from the ``--csv-sep`` CLI flag), it wins unconditionally.
        Otherwise, sniff the first few KB of the file: try
        :class:`csv.Sniffer` first, then fall back to counting candidate
        delimiters on the first non-empty line. Returns ``default`` if
        nothing plausible is found.
        """
        if self.csv_sep_override:
            return self.csv_sep_override
        if self._remote:
            if self._remote_bytes is None:
                self._remote_bytes, self._remote_content_type = \
                    _fetch_url(self.fname)
            sample = self._remote_bytes[:8192].decode(
                'utf-8', errors='replace')
        else:
            try:
                with open(self.fname, 'r', encoding='utf-8',
                          errors='replace', newline='') as f:
                    sample = f.read(8192)
            except OSError:
                return default
        if not sample:
            return default

        try:
            dialect = _csv_mod.Sniffer().sniff(
                sample, delimiters=''.join(self._CSV_DELIMITERS))
            if dialect.delimiter in self._CSV_DELIMITERS:
                return dialect.delimiter
        except _csv_mod.Error:
            pass

        first = next((ln for ln in sample.splitlines() if ln.strip()), '')
        counts = {d: first.count(d) for d in self._CSV_DELIMITERS}
        best = max(counts, key=counts.get)
        return best if counts[best] > 0 else default

    def _read_csv(self, sep, default_comment=None):
        # Resolve comment marker: CLI override, else per-format default.
        comment = self._effective_comment(default=default_comment)

        if comment and len(comment) > 1:
            #- Multi-char comment (e.g. ``//``): pandas only supports
            #- single-char ``comment=``, so pre-filter the file once and
            #- feed an in-memory buffer. Skips the pyarrow fast path,
            #- which is fine since multi-char comments are rare.
            buf = self._prefilter_text(comment)
            df = pd.read_csv(buf, sep=sep)
            df.columns = [c.strip() for c in df.columns]
            return df

        # Prefer pyarrow (much faster on large numeric CSVs); fall back to
        # the C engine with memory_map, then the slow Python engine.
        # pyarrow doesn't support ``comment=``, so skip it when set.
        c_kw = {'comment': comment} if comment else {}
        if not comment:
            try:
                df = pd.read_csv(self.fname, sep=sep, engine='pyarrow')
                df.columns = [c.strip() for c in df.columns]
                return df
            except Exception:
                pass
        try:
            df = pd.read_csv(self.fname, sep=sep, engine='c',
                             low_memory=False, memory_map=True, **c_kw)
        except Exception:
            df = pd.read_csv(self.fname, sep=None, engine='python', **c_kw)
        df.columns = [c.strip() for c in df.columns]
        return df

    def _prefilter_text(self, comment):
        """Read ``self.fname`` and drop lines starting with ``comment``.

        Used for multi-character comment markers (e.g. ``//``) that
        :func:`pandas.read_csv` cannot handle natively. Returns a
        :class:`io.StringIO` ready to feed back into ``read_csv``.
        Leading whitespace before the marker is preserved verbatim;
        only lines whose first non-whitespace characters match the
        marker are skipped (matches SDN.Report's behavior).
        """
        kept = []
        with open(self.fname, 'r', encoding='utf-8',
                  errors='replace', newline='') as f:
            for line in f:
                if line.lstrip().startswith(comment):
                    continue
                kept.append(line)
        return _io.StringIO(''.join(kept))

    def _read_whitespace(self):
        """Read a whitespace-separated text file (Eldo .cou/.chi, .dat, .spe).

        Uses ``sep=r'\\s+'`` with the Python engine (pyarrow/C don't
        support regex separators). Default comment marker is ``'#'``,
        which covers ngspice ``.dat`` print files and most SPICE-family
        log dumps; override with ``--csv-comment`` if needed.
        """
        comment = self._effective_comment(default='#')
        if comment and len(comment) > 1:
            buf = self._prefilter_text(comment)
            df = pd.read_csv(buf, sep=r'\s+', engine='python')
        else:
            c_kw = {'comment': comment} if comment else {}
            df = pd.read_csv(self.fname, sep=r'\s+',
                             engine='python', **c_kw)
        df.columns = [str(c).strip() for c in df.columns]
        return df

    def _read_whitespace_header(self):
        """Lazy-header counterpart to :meth:`_read_whitespace`."""
        comment = self._effective_comment(default='#')
        if comment and len(comment) > 1:
            buf = self._prefilter_text(comment)
            df = pd.read_csv(buf, sep=r'\s+',
                             engine='python', nrows=0)
        else:
            c_kw = {'comment': comment} if comment else {}
            df = pd.read_csv(self.fname, sep=r'\s+',
                             engine='python', nrows=0, **c_kw)
        return [str(c).strip() for c in df.columns]

    def _read_excel(self):
        df = pd.read_excel(self.fname, sheet_name=self.sheet_name)
        df.columns = [c.strip() for c in df.columns]
        return df

    def _read_prn(self):
        """Parse Xyce .prn (print) waveform file into a DataFrame."""
        sweep_var = "time"
        var_names = []
        times = []
        data_rows = []

        with open(self.fname, 'r') as f:
            in_header = False
            in_nodes = False
            pending_time = None

            for line in f:
                line_s = line.strip()

                if line_s.startswith('#H'):
                    in_header = True
                    in_nodes = False
                    continue

                if line_s.startswith('#N'):
                    in_header = False
                    in_nodes = True
                    continue

                if line_s.startswith('#C'):
                    in_nodes = False
                    in_header = False
                    parts = line_s.split()
                    pending_time = float(parts[1])
                    continue

                if line_s.startswith('#'):
                    in_header = False
                    in_nodes = False
                    pending_time = None
                    continue

                if in_header:
                    m = re.search(r"SWEEPVAR='([^']+)'", line_s)
                    if m:
                        sweep_var = m.group(1).lower()
                    continue

                if in_nodes:
                    var_names.extend(re.findall(r"'([^']+)'", line_s))
                    continue

                if pending_time is not None and line_s:
                    vals = [float(v.split(':')[0]) for v in line_s.split()]
                    if vals:
                        times.append(pending_time)
                        data_rows.append(vals)
                    pending_time = None

        n_vars = len(var_names)
        cols = [sweep_var] + [v.lower() for v in var_names]
        rows = [[t] + d[:n_vars] for t, d in zip(times, data_rows)]
        return pd.DataFrame(rows, columns=cols)

    @staticmethod
    def excel_sheet_names(fname):
        xl = pd.ExcelFile(fname)
        return xl.sheet_names

    def getWaveNames(self):
        if self._columns is not None:
            return self._columns
        return list(self.df.columns)

    def getWave(self,yname):

        if(yname not in self.waves):
            wave = Wave(self,yname,self.xaxis)
            self.waves[yname] = wave

        wave = self.waves[yname]
        wave.reload()

        return wave

    def getTag(self,yname):
        return self.fname + "/" + yname


class WaveFiles(dict):

    def __init__(self):
        self.current = None
        pass

    def open(self,fname,xaxis,sheet_name=0,fmt=None):
        key = fname if sheet_name == 0 else "%s::%s" % (fname, sheet_name)
        self[key] = WaveFile(fname,xaxis,sheet_name,fmt=fmt)
        self.current = key
        return self[key]

    def openDataFrame(self, df, name, xaxis):
        key = "::virtual::" + name
        self[key] = WaveFile(name, xaxis, df=df)
        self.current = key
        return self[key]

    def select(self,fname):
        if(fname in self):
            self.current = fname

    def remove(self, key):
        """Remove a loaded file by dict key. Updates current if needed."""
        if key not in self:
            return
        del self[key]
        if self.current == key:
            self.current = next(iter(self.keys()), None)

    def clear_all(self):
        """Remove every loaded file."""
        dict.clear(self)
        self.current = None

    def getSelected(self):
        if(self.current is not None):
            return self[self.current]


# ---------------------------------------------------------------------------
# NumPy .npz archives
# ---------------------------------------------------------------------------

def _npz_apply_frequency_aliases(df):
    """Add ``frequency`` (Hz) when only rnsr-style keys are present.

    Swept-capture artifacts often store ``freqs_hz`` / ``freqs_mhz`` while
    :class:`Wave` auto-selects a column named ``frequency``."""
    if df is None or df.empty:
        return
    if "frequency" in df.columns:
        return
    if "freqs_hz" in df.columns:
        df["frequency"] = df["freqs_hz"]
    elif "freqs_mhz" in df.columns:
        df["frequency"] = np.asarray(df["freqs_mhz"], dtype=np.float64) * 1e6


def _npz_arrays_to_dataframe(arrays):
    """Merge archive arrays into one rectangular DataFrame.

    * 0-D arrays broadcast to the table length.
    * 1-D arrays align on the dominant row count (most common among
      non-scalar arrays). Shorter / longer series are skipped.
    * 2-D arrays ``(n, k)`` split into ``name__0`` … ``name__{k-1}``.
    * ``object`` dtypes are skipped (pickle-free ``.npz`` rarely uses them).
    """
    from collections import Counter

    scalars = {}
    parsed = {}
    for name, arr in sorted(arrays.items()):
        a = np.asarray(arr)
        if a.dtype == object:
            continue
        if a.ndim == 0:
            scalars[name] = a
        elif a.ndim == 1:
            parsed[name] = ("1d", a)
        elif a.ndim == 2:
            parsed[name] = ("2d", a)
        #- Higher dims: skip (too ambiguous for a flat table).
    lengths = [v[1].shape[0] for v in parsed.values()]
    if lengths:
        n = Counter(lengths).most_common(1)[0][0]
    elif scalars:
        n = 1
    else:
        return pd.DataFrame()

    cols = {}
    for name, (kind, a) in parsed.items():
        if kind == "1d":
            if a.shape[0] == n:
                cols[name] = np.asarray(a)
            elif a.shape[0] == 1 and n > 1:
                cols[name] = np.full(n, a[0], dtype=a.dtype)
            else:
                continue
        else:
            if a.shape[0] != n:
                continue
            k = a.shape[1]
            if k == 1:
                cols[name] = a[:, 0]
            else:
                for j in range(k):
                    cols["%s__%d" % (name, j)] = a[:, j]
    for name, a in scalars.items():
        cols[name] = np.full(n, a.item(), dtype=a.dtype)
    return pd.DataFrame(cols)


def _npz_read_sidecar(fname):
    """Return the sibling ``<base>.json`` sidecar dict, or ``None``.

    SDR IQ captures (``*_iq.npz``) often store only the complex ``iq`` array
    and keep acquisition metadata (``samp_rate_hz``, ``center_mhz``, …) in a
    JSON sidecar next to the archive.
    """
    import json

    side = os.path.splitext(fname)[0] + ".json"
    if not os.path.exists(side):
        return None
    try:
        with open(side, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def _npz_sidecar_sample_rate(meta):
    """Positive ``samp_rate_hz`` from a sidecar dict, else ``None``."""
    if not meta:
        return None
    try:
        fs = float(meta.get("samp_rate_hz"))
    except (TypeError, ValueError):
        return None
    return fs if np.isfinite(fs) and fs > 0 else None


def _npz_sidecar_center_hz(meta):
    """Center frequency (Hz) from a sidecar dict, else ``None``.

    Accepts ``center_hz`` directly or ``center_mhz`` (converted)."""
    if not meta:
        return None
    val = meta.get("center_hz")
    scale = 1.0
    if val is None:
        val = meta.get("center_mhz")
        scale = 1e6
    try:
        c = float(val) * scale
    except (TypeError, ValueError):
        return None
    return c if np.isfinite(c) else None


def _npz_apply_time_axis(df, fname):
    """Add a ``time`` column + IQ metadata for SDR captures via the sidecar.

    Only applies when the frame has no existing time/frequency x-axis and a
    sibling JSON gives a usable ``samp_rate_hz``; this lets the viewer plot
    IQ vs seconds and the FFT report real Hz instead of cycles/sample. The
    sample rate and (optional) RF center are also stashed on ``df.attrs``
    under ``cicwave_iq`` so the FFT can label a two-sided spectrum at the
    real carrier frequency.
    """
    if df is None or df.empty:
        return
    if any(c in df.columns for c in ("time", "frequency")):
        return
    meta = _npz_read_sidecar(fname)
    fs = _npz_sidecar_sample_rate(meta)
    if fs is None:
        return
    n = len(df)
    df.insert(0, "time", np.arange(n, dtype=np.float64) / fs)
    info = {"samp_rate_hz": fs}
    center_hz = _npz_sidecar_center_hz(meta)
    if center_hz is not None:
        info["center_hz"] = center_hz
    df.attrs["cicwave_iq"] = info


def read_npz(fname):
    """Load a NumPy ``.npz`` archive into a DataFrame.

    Intended for bench artifacts (e.g. swept captures with ``freqs_hz`` /
    ``amps_dbm`` and optional scalar metadata, or ``*_iq.npz`` complex SDR
    captures with a JSON sidecar) and other tabular saves. Uses
    ``allow_pickle=False`` for safety.
    """
    with np.load(fname, allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}
    df = _npz_arrays_to_dataframe(arrays)
    _npz_apply_frequency_aliases(df)
    _npz_apply_time_axis(df, fname)
    return df


# ---------------------------------------------------------------------------
# VCD parser
# ---------------------------------------------------------------------------

#- VCD timescale unit -> seconds. The standard set per IEEE 1364.
_VCD_TIMESCALE_UNITS = {
    's': 1.0, 'ms': 1e-3, 'us': 1e-6, 'µs': 1e-6, 'ns': 1e-9,
    'ps': 1e-12, 'fs': 1e-15,
}


def _vcd_parse_timescale(text):
    """Parse a ``$timescale`` body like ``"1 ps"`` / ``"10ns"`` -> seconds.

    Returns the multiplier that converts a raw ``#<n>`` tick into seconds.
    Falls back to 1.0 if the body is missing or unrecognised.
    """
    s = (text or "").strip().lower().replace('\n', ' ')
    if not s:
        return 1.0
    m = re.match(r"\s*([0-9.eE+\-]+)\s*([a-zµ]+)\s*$", s)
    if m is None:
        #- Sometimes the number and unit are on separate tokens with extra
        #- whitespace; do a looser split.
        toks = s.split()
        if len(toks) >= 2:
            num_s, unit_s = toks[0], toks[1]
        else:
            return 1.0
    else:
        num_s, unit_s = m.group(1), m.group(2)
    try:
        num = float(num_s)
    except ValueError:
        return 1.0
    return num * _VCD_TIMESCALE_UNITS.get(unit_s, 1.0)


def read_vcd(fname, max_signals=None):
    """Parse a VCD (Value Change Dump) file into a pandas DataFrame.

    The result has a ``time`` column (in seconds, after applying
    ``$timescale``) plus one column per signal. Single-bit signals are
    stored as object arrays of ``"0" / "1" / "x" / "z"``; vector
    signals are stored as Python ints (or ``None`` for x/z); real
    signals are stored as floats.

    The DataFrame attaches metadata in ``df.attrs['cicwave_vcd']``:
        ``{'kinds': {col: 'bit'|'vector'|'real'},
           'widths': {col: int},
           'scope':  {col: scoped_name}}``

    Parameters
    ----------
    fname : str
        Path to the VCD file.
    max_signals : int or None
        If set, only the first N declared signals are kept (used to
        bound memory on huge dumps). ``None`` keeps everything.

    Notes
    -----
    Only the subset of VCD actually emitted by Icarus / Verilator /
    ngspice is supported. We follow the structure but don't try to
    enforce strict compliance.
    """
    timescale = 1.0
    #- id_code -> {'name': scoped_name, 'kind': 'bit'/'vector'/'real',
    #-             'width': int}
    sig_info = {}
    scope_stack = []

    def _parse_decls(fh):
        nonlocal timescale
        cur = []
        in_timescale = False
        for line in fh:
            ls = line.strip()
            if not ls:
                continue
            if ls.startswith('$timescale'):
                in_timescale = True
                cur = [ls[len('$timescale'):]]
                if '$end' in ls:
                    timescale = _vcd_parse_timescale(
                        ' '.join(cur).replace('$end', ''))
                    in_timescale = False
                continue
            if in_timescale:
                if '$end' in ls:
                    cur.append(ls.replace('$end', ''))
                    timescale = _vcd_parse_timescale(' '.join(cur))
                    in_timescale = False
                else:
                    cur.append(ls)
                continue
            if ls.startswith('$scope'):
                toks = ls.split()
                #- $scope <type> <name> $end
                if len(toks) >= 3:
                    scope_stack.append(toks[2])
                continue
            if ls.startswith('$upscope'):
                if scope_stack:
                    scope_stack.pop()
                continue
            if ls.startswith('$var'):
                #- $var <type> <width> <id> <name> [<bit-select>] $end
                toks = ls.split()
                if len(toks) < 6:
                    continue
                vtype = toks[1]
                try:
                    width = int(toks[2])
                except ValueError:
                    continue
                idc = toks[3]
                name = toks[4]
                if width == 1 and vtype != 'real':
                    kind = 'bit'
                elif vtype == 'real':
                    kind = 'real'
                else:
                    kind = 'vector'
                #- Multiple $var lines can share the same id (alias). Keep
                #- the first; remember every alias name in ``aliases``.
                full = '.'.join(scope_stack + [name]) if scope_stack else name
                if idc in sig_info:
                    sig_info[idc].setdefault('aliases', []).append(full)
                else:
                    sig_info[idc] = {
                        'name': full, 'kind': kind, 'width': width,
                        'aliases': [],
                    }
                continue
            if ls.startswith('$enddefinitions'):
                return

    with open(fname, 'r', errors='replace') as fh:
        _parse_decls(fh)

        if max_signals is not None and len(sig_info) > max_signals:
            #- Keep only the first ``max_signals`` declared ids by
            #- insertion order.
            keep = set(list(sig_info.keys())[:max_signals])
            sig_info = {k: v for k, v in sig_info.items() if k in keep}

        ids = list(sig_info.keys())
        col_names = [sig_info[i]['name'] for i in ids]
        kind_by_id = {i: sig_info[i]['kind'] for i in ids}
        width_by_id = {i: sig_info[i]['width'] for i in ids}

        #- Pre-allocate per-signal change lists: list of (time_idx, value).
        changes = {i: [] for i in ids}
        times = []
        cur_t = 0
        in_dump = False  # inside $dumpvars / $dumpall block

        def _ensure_t0():
            #- Some VCDs (e.g. our compact unit-test fixtures) emit
            #- ``$dumpvars`` initial values before any ``#0`` time tick.
            #- Inject t=0 so those initial values are anchored properly.
            if not times:
                times.append(0)

        for line in fh:
            ls = line.strip()
            if not ls:
                continue
            c0 = ls[0]
            if c0 == '#':
                #- Time tick.
                try:
                    cur_t = int(ls[1:])
                except ValueError:
                    continue
                if not times or times[-1] != cur_t:
                    times.append(cur_t)
                continue
            if c0 == '$':
                if ls.startswith('$dumpvars') or ls.startswith('$dumpall'):
                    in_dump = True
                    _ensure_t0()
                elif ls.startswith('$end'):
                    in_dump = False
                #- Other directives ($comment etc.) ignored.
                continue
            if c0 in ('0', '1', 'x', 'X', 'z', 'Z', 'h', 'H', 'l', 'L'):
                #- Single-bit change: value char + id (no space).
                idc = ls[1:].strip()
                if idc in changes:
                    changes[idc].append(
                        (len(times) - 1 if times else 0, c0.lower()))
                continue
            if c0 == 'b' or c0 == 'B':
                #- Vector: b<bits> <id>
                try:
                    bits, idc = ls.split(None, 1)
                except ValueError:
                    continue
                idc = idc.strip()
                bits = bits[1:]  # drop leading 'b'
                if idc in changes:
                    if any(c in 'xXzZ' for c in bits):
                        val = None  # unknown -> store as None
                        raw = bits.lower()
                    else:
                        try:
                            val = int(bits, 2)
                            raw = val
                        except ValueError:
                            val = None
                            raw = bits.lower()
                    changes[idc].append(
                        (len(times) - 1 if times else 0, raw))
                continue
            if c0 == 'r' or c0 == 'R':
                #- Real: r<float> <id>
                try:
                    fval, idc = ls.split(None, 1)
                except ValueError:
                    continue
                idc = idc.strip()
                try:
                    rv = float(fval[1:])
                except ValueError:
                    continue
                if idc in changes:
                    changes[idc].append(
                        (len(times) - 1 if times else 0, rv))
                continue

    if not times:
        #- Pathological: no time markers. Return empty frame with the
        #- declared column list so the UI still shows the signal names.
        df = pd.DataFrame({c: [] for c in ['time'] + col_names})
        df.attrs['cicwave_vcd'] = {
            'kinds': {sig_info[i]['name']: sig_info[i]['kind'] for i in ids},
            'widths': {sig_info[i]['name']: sig_info[i]['width'] for i in ids},
            'scope': {sig_info[i]['name']: sig_info[i]['name'] for i in ids},
            'timescale_s': timescale,
        }
        return df

    #- Build forward-filled per-signal columns.
    n = len(times)
    data = {'time': np.asarray(times, dtype=np.float64) * timescale}
    for idc in ids:
        kind = kind_by_id[idc]
        if kind == 'real':
            col = np.full(n, np.nan, dtype=np.float64)
            cur = np.nan
            j = 0
            ch = changes[idc]
            for t_idx in range(n):
                while j < len(ch) and ch[j][0] <= t_idx:
                    cur = ch[j][1]
                    j += 1
                col[t_idx] = cur
        elif kind == 'bit':
            col = np.empty(n, dtype=object)
            cur = 'x'
            j = 0
            ch = changes[idc]
            for t_idx in range(n):
                while j < len(ch) and ch[j][0] <= t_idx:
                    cur = ch[j][1]
                    j += 1
                col[t_idx] = cur
        else:  # vector
            col = np.empty(n, dtype=object)
            cur = None
            j = 0
            ch = changes[idc]
            for t_idx in range(n):
                while j < len(ch) and ch[j][0] <= t_idx:
                    cur = ch[j][1]
                    j += 1
                col[t_idx] = cur
        name = sig_info[idc]['name']
        data[name] = col
        for alias in sig_info[idc].get('aliases', []):
            #- Aliases share data but get their own column for selection.
            data[alias] = col

    df = pd.DataFrame(data)
    kinds = {sig_info[i]['name']: sig_info[i]['kind'] for i in ids}
    widths = {sig_info[i]['name']: sig_info[i]['width'] for i in ids}
    for i in ids:
        for alias in sig_info[i].get('aliases', []):
            kinds[alias] = sig_info[i]['kind']
            widths[alias] = sig_info[i]['width']
    df.attrs['cicwave_vcd'] = {
        'kinds': kinds,
        'widths': widths,
        'timescale_s': timescale,
    }
    return df


# ---------------------------------------------------------------------------
# LitePoint .iqvsa parser
# ---------------------------------------------------------------------------

#- LitePoint VSA capture: ASCII XML header terminated by ``</LPHeader>``
#- followed by interleaved little-endian float32 I,Q samples (volts at the
#- instrument port). Header carries sample rate, sample count, center
#- frequency and the full SCPI module configuration.
_IQVSA_HEADER_END = b'</LPHeader>'
_IQVSA_HEADER_SCAN_LIMIT = 1 << 20  # 1 MiB is plenty; real headers are < 4 KiB


def _iqvsa_parse_module_config(text):
    """Parse the SCPI-style ``<ModuleConfiguration>`` block into a dict.
    Lines look like ``FREQuency:CENTer 2.412000e+09;`` — keep the key as
    written and try to coerce the value to float, else keep the string."""
    out = {}
    for raw in (text or "").splitlines():
        line = raw.strip().rstrip(';').strip()
        if not line or ';' in line and line.endswith(';'):
            line = line.rstrip(';').strip()
        if not line or line.endswith(';') or ' ' not in line:
            continue
        key, _, val = line.partition(' ')
        val = val.strip()
        try:
            out[key] = float(val)
        except ValueError:
            out[key] = val
    return out


def read_iqvsa(fname):
    """Parse a LitePoint ``.iqvsa`` IQ capture into a pandas DataFrame.

    Columns: ``time`` (s), ``I (V)``, ``Q (V)``, ``mag (V)``. Header
    metadata is stashed in ``df.attrs['cicwave_iqvsa']`` (sample rate,
    center frequency, sample count, raw XML header, parsed SCPI config).

    Only the v0.3 ``DataFormat=IQ`` / float32 layout we have a sample of
    is supported; other variants raise ``ValueError``.
    """
    import xml.etree.ElementTree as ET

    with open(fname, 'rb') as fh:
        head = fh.read(_IQVSA_HEADER_SCAN_LIMIT)
        idx = head.find(_IQVSA_HEADER_END)
        if idx < 0:
            raise ValueError(
                "iqvsa: no </LPHeader> found in first %d bytes of %s"
                % (_IQVSA_HEADER_SCAN_LIMIT, fname))
        header_end = idx + len(_IQVSA_HEADER_END)
        header_xml = head[:header_end].decode('ascii', errors='replace')
        fh.seek(header_end)
        payload = fh.read()

    try:
        root = ET.fromstring(header_xml)
    except ET.ParseError as e:
        raise ValueError("iqvsa: malformed XML header in %s: %s" % (fname, e))

    def _txt(path, default=None):
        node = root.find(path)
        return node.text.strip() if node is not None and node.text else default

    fmt = _txt('General/DataFormat', 'IQ')
    if fmt.upper() != 'IQ':
        raise ValueError("iqvsa: unsupported DataFormat=%r (only 'IQ')" % fmt)

    try:
        fs = float(_txt('DataInfo/SamplingRate'))
        n_declared = int(_txt('DataInfo/SampleCount'))
    except (TypeError, ValueError) as e:
        raise ValueError("iqvsa: missing/bad SamplingRate or SampleCount in %s"
                         % fname) from e

    #- Format inference: payload should be n_declared * 2 * itemsize bytes.
    #- We only know float32 for sure; sniff but require an exact match.
    bytes_per_pair = len(payload) // n_declared if n_declared else 0
    if bytes_per_pair == 8 and len(payload) == n_declared * 8:
        iq = np.frombuffer(payload, dtype='<f4').reshape(-1, 2)
    elif bytes_per_pair == 4 and len(payload) == n_declared * 4:
        #- int16 IQ — not seen in the wild yet, but plausible. Treat as
        #- raw counts; downstream can scale.
        iq = np.frombuffer(payload, dtype='<i2').reshape(-1, 2).astype(
            np.float32)
    else:
        raise ValueError(
            "iqvsa: payload size %d does not match SampleCount=%d for "
            "float32 (%d) or int16 (%d) IQ in %s"
            % (len(payload), n_declared, n_declared * 8, n_declared * 4,
               fname))

    I = iq[:, 0]
    Q = iq[:, 1]
    n = I.shape[0]
    t = np.arange(n, dtype=np.float64) / fs
    mag = np.hypot(I, Q)

    df = pd.DataFrame({
        'time': t,
        'I (V)': I,
        'Q (V)': Q,
        'mag (V)': mag,
    })

    cfg_text = _txt('ModuleConfiguration', '')
    cfg = _iqvsa_parse_module_config(cfg_text)
    df.attrs['cicwave_iqvsa'] = {
        'sampling_rate_hz': fs,
        'sample_count': n,
        'center_freq_hz': cfg.get('FREQuency:CENTer'),
        'capture_time_s': cfg.get('CAPTure:TIME'),
        'idn': _txt('General/IDN'),
        'datetime': _txt('General/DateTime'),
        'header_xml': header_xml,
        'config': cfg,
    }
    return df
