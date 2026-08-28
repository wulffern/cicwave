#!/usr/bin/env python3
######################################################################
##        Copyright (c) 2020 Carsten Wulff Software, Norway
## ###################################################################
## Created       : wulff at 2020-10-23
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

import os
import re
import sys
import glob as _glob
import urllib.parse
import click
from .command import setup_logging

#- Kept in sync with ``wavefiles._is_url``. Duplicated (rather than
#- imported) so plain ``cicwave --help`` doesn't pay for importing
#- numpy/pandas/matplotlib via wavefiles.py at module load time.
_URL_RE = re.compile(r'^https?://', re.IGNORECASE)


def _resolve_wave_x_from_cli_env(cli_x):
    """Apply --x, else CICWAVE_X environment variable."""
    if cli_x:
        return cli_x
    v = os.environ.get("CICWAVE_X")
    if v and str(v).strip():
        return str(v).strip()
    return None


def _parse_twos_cols(s):
    if not s or not str(s).strip():
        return None
    return tuple(x.strip() for x in str(s).split(",") if x.strip())


#- Glob metacharacters. A positional arg containing any of these (and not
#- matching a literal file on disk) is treated as a pattern and expanded.
#- This makes ``cicwave path/*.npz`` work on shells that don't auto-expand
#- wildcards for external commands (notably PowerShell and cmd.exe).
_GLOB_META = ("*", "?", "[")


def _looks_like_glob(s):
    return any(ch in s for ch in _GLOB_META)


def _expand_glob_patterns(files, patterns):
    """Merge positional ``files`` with files matched by --glob ``patterns``.

    Positional args are kept verbatim when they name an existing file;
    otherwise, if they contain glob metacharacters (``*?[``) they are
    expanded with ``glob.glob(..., recursive=True)`` too, so wildcard paths
    work even on shells that don't expand them for external commands (e.g.
    PowerShell). ``--glob`` patterns are always expanded. Order: positional
    first (in order), then each ``--glob`` pattern. Files are de-duplicated
    while preserving first-seen order. Patterns that match nothing are
    reported on stderr but are not fatal.
    """
    out = []
    seen = set()

    def _add(path):
        if path not in seen:
            seen.add(path)
            out.append(path)

    for f in files:
        if _URL_RE.match(f) or os.path.exists(f) or not _looks_like_glob(f):
            #- URL, existing file, or a plain path (let downstream report a
            #- genuine missing-file error rather than silently dropping it).
            #- URLs are checked first since a query string (``?``) or an
            #- API path segment would otherwise look like a glob pattern.
            _add(f)
            continue
        matches = sorted(_glob.glob(f, recursive=True))
        if not matches:
            print("warning: '%s' matched no files" % f, file=sys.stderr)
            continue
        for m in matches:
            _add(m)
    for pat in patterns or ():
        matches = sorted(_glob.glob(pat, recursive=True))
        if not matches:
            print("warning: --glob '%s' matched no files" % pat,
                  file=sys.stderr)
            continue
        for m in matches:
            _add(m)
    return tuple(out)


def _spec_path_of(path):
    """Lower-cased path part of *path*, with any URL query string cut off."""
    if _URL_RE.match(path):
        return urllib.parse.urlsplit(path).path.lower()
    return path.lower()


def _spec_ref(path):
    """How a spec is recorded for a session: absolute path, or URL as-is.

    ``os.path.abspath`` on an ``http://`` URL yields a local path that
    names nothing, so a session written from a URL spec could not be
    read back. Mirrors what ``PgWaveWindow.openSourceSpec`` does.
    """
    return path if _URL_RE.match(path) else os.path.abspath(path)


def _is_source_spec_file(path):
    """True for a YAML/JSON pivot spec that fetches its own data.

    Lets a self-contained spec be passed positionally (``cicwave
    api.yaml``, or a URL a service publishes one at) instead of as
    ``--pivot api.yaml`` with no file. Only a ``source:`` block
    qualifies -- a spec that still needs a data file would be ambiguous
    with the file itself.

    A URL has to end in ``.yaml``/``.yml`` to be treated this way, since
    an extension-less endpoint is just as likely to be serving data;
    ``--pivot <url>`` says "this is a spec" without the guesswork.
    """
    #- Extension check first, so the pandas/Qt import chain behind
    #- apisource is paid for only when a YAML argument is actually given,
    #- and so a plain data URL is never fetched twice. Test it against
    #- the URL's path, not the whole URL: a generated spec is served at
    #- something like `/spec.yaml?dut=X`, and the rest of cicwave already
    #- ignores the query string when resolving a format.
    if not _spec_path_of(path).endswith((".yaml", ".yml")):
        return False
    from .apisource import is_source_spec_file
    return is_source_spec_file(path)


def _promote_positional_spec(files, pivot_spec):
    """Move a positional source spec into the ``--pivot`` slot."""
    specs = [f for f in files if _is_source_spec_file(f)]
    if not specs:
        return files, pivot_spec
    if pivot_spec:
        print("error: %s already fetches its own data; passing --pivot %s "
              "as well is ambiguous" % (specs[0], pivot_spec), file=sys.stderr)
        sys.exit(2)
    if len(files) > 1:
        print("error: %s fetches its own data and must be the only argument"
              % specs[0], file=sys.stderr)
        sys.exit(2)
    return (), specs[0]


#- How many catalog groups --pivot-info lists before summarising. A
#- catalog can hold thousands; a screenful is enough to see the shape.
_CATALOG_PREVIEW = 40


def _run_catalog_spec(pivot_spec, spec, x, pivot_info_flag,
                      export_path, export_data_path, session_path):
    """Open a catalog spec: names now, data when a wave is clicked."""
    from .apisource import LazyCatalog

    try:
        catalog = LazyCatalog(spec)
        groups = catalog.load()
    except Exception as e:
        print("error: failed to load %s: %s" % (pivot_spec, e),
              file=sys.stderr)
        sys.exit(1)

    if pivot_info_flag:
        print("--- %s ---" % pivot_spec)
        print("%d group(s), each fetched when first plotted:" % len(groups))
        for name in groups[:_CATALOG_PREVIEW]:
            print("  %s" % name)
        if len(groups) > _CATALOG_PREVIEW:
            print("  ... and %d more" % (len(groups) - _CATALOG_PREVIEW))
        return

    if export_path or export_data_path:
        #- Nothing has been fetched yet and fetching everything is the
        #- cost this spec exists to avoid, so there is nothing to export.
        print("error: %s is a catalog spec: its %d group(s) are fetched "
              "individually when you plot them, so a headless export has "
              "nothing to write. Narrow it into a spec with a plain "
              "'source:' block to export." % (pivot_spec, len(groups)),
              file=sys.stderr)
        sys.exit(2)

    try:
        from .wave_pg import CmdWavePg
    except ImportError as e:
        print("Error: PyQtGraph backend requires PySide6 and pyqtgraph")
        print("Install with: pip install PySide6 pyqtgraph")
        print(f"  ({e})")
        sys.exit(1)

    c = CmdWavePg(x)
    if session_path:
        c.win.applySession(session_path)
    c.win.browser.openLazyFrame(
        os.path.basename(pivot_spec), groups, catalog.load_group,
        pivot_spec_path=_spec_ref(pivot_spec))
    c.run()


def _run_wave_pg(files, x, sheet, pivot_spec=None,
                 pivot_info_flag=False, session_path=None, export_path=None,
                 export_data_path=None, csv_sep=None, csv_comment=None,
                 twos_bits=None, twos_cols=None, fmt=None):
    """Run the PyQtGraph waveform viewer."""
    x = _resolve_wave_x_from_cli_env(x)

    if csv_sep is not None or csv_comment is not None or twos_bits is not None:
        from .wavefiles import WaveFile
        if twos_bits is not None:
            WaveFile.set_twos_complement(twos_bits, _parse_twos_cols(twos_cols))
        if csv_sep is not None:
            try:
                WaveFile.set_csv_sep_override(csv_sep)
            except ValueError as e:
                print("error: --csv-sep: %s" % e, file=sys.stderr)
                sys.exit(2)
        if csv_comment is not None:
            #- ``""`` reaches us when the user explicitly disabled with
            #- ``--csv-comment ''``; that is normalized to ``None`` inside
            #- ``set_csv_comment_override`` and means "no stripping".
            try:
                WaveFile.set_csv_comment_override(csv_comment)
            except ValueError as e:
                print("error: --csv-comment: %s" % e, file=sys.stderr)
                sys.exit(2)

    spec = None
    sources = ()
    if pivot_spec:
        from .apisource import has_catalog, has_source, load_flat_frame
        from .pivot import load_spec, pivot_info, apply_pivot
        spec = load_spec(pivot_spec)

        if has_catalog(spec):
            _run_catalog_spec(pivot_spec, spec, x, pivot_info_flag,
                              export_path, export_data_path, session_path)
            return

        from_source = has_source(spec)

        #- A spec that fetches its own data has nothing to read from
        #- disk; anything positional alongside it is a mistake worth
        #- naming rather than silently ignoring.
        if from_source:
            if files:
                print("error: %s has a 'source:' block, so it fetches its "
                      "own data; drop the file argument(s): %s"
                      % (pivot_spec, ", ".join(files)), file=sys.stderr)
                sys.exit(2)
            sources = (pivot_spec,)
        else:
            sources = files

        if pivot_info_flag:
            for f in sources:
                try:
                    df = load_flat_frame(spec, None if from_source else f,
                                         xaxis=x or "", fmt=fmt)
                    print("--- %s ---" % f)
                    print(pivot_info(df, spec))
                    print()
                except Exception as e:
                    print("error: failed to load %s: %s" % (f, e),
                          file=sys.stderr)
                    sys.exit(1)
            return

        if not x and spec.get('columns'):
            x = spec['columns']

    # Import the PyQtGraph backend
    try:
        from .wave_pg import CmdWavePg
    except ImportError as e:
        print("Error: PyQtGraph backend requires PySide6 and pyqtgraph")
        print("Install with: pip install PySide6 pyqtgraph")
        print(f"  ({e})")
        sys.exit(1)
    
    c = CmdWavePg(x)

    if session_path:
        c.win.applySession(session_path)

    summary_text = ""
    if pivot_spec:
        from .analysis import preprocess_dataframe, run_analysis_steps
        for f in sources:
            try:
                raw = load_flat_frame(
                    spec, None if from_source else f,
                    xaxis=x or "", fmt=fmt).copy()
                a_block = (spec.get("analysis") or {})
                raw = preprocess_dataframe(raw, a_block.get("preprocess", {}))
                pivoted = apply_pivot(raw, spec)
                if a_block.get("steps"):
                    ar = run_analysis_steps(
                        pivoted, a_block["steps"],
                        x_column=spec.get("columns"))
                    summary_text = ar.summary_text
                    if export_path and summary_text.strip():
                        print(summary_text)
                name = "pivot(%s)" % os.path.basename(f)
                c.openDataFrame(
                    pivoted, name,
                    pivot_spec_path=_spec_ref(pivot_spec),
                    original_path=(
                        f if _URL_RE.match(f) else os.path.abspath(f)),
                    source_spec=from_source)
            except Exception as e:
                print("error: failed to load %s: %s" % (f, e), file=sys.stderr)
                sys.exit(1)
    else:
        for f in files:
            try:
                c.openFile(f, sheet_name=sheet, fmt=fmt)
            except Exception as e:
                print("error: failed to load %s: %s" % (f, e), file=sys.stderr)
                sys.exit(1)

    if (export_path or export_data_path) and pivot_spec:
        c.win._pending_export_metrics = summary_text or ""
        c.win.autoplot_pivot_for_export()
    if export_path or export_data_path:
        c.exportAndExit(export_path, export_data_path)
    else:
        c.run()


@click.command()
@click.argument("files", nargs=-1)
@click.option("--glob", "globs", multiple=True,
              help="Glob pattern (repeatable). Supports ** for recursion. "
                   "Useful on shells like PowerShell that don't auto-expand.")
@click.option("--x", default=None,
              help="X-axis column; else CICWAVE_X; else saved default; else auto")
@click.option("--sheet", default=None, help="Sheet name for Excel files (default: first sheet)")
@click.option("--format", "fmt", default=None,
              type=click.Choice(
                  ["csv", "tsv", "txt", "json", "xlsx", "xls", "ods",
                   "parquet", "feather", "html", "xml", "fwf"],
                  case_sensitive=False),
              help="Force the data format for a URL source (e.g. a REST "
                   "API endpoint with no file extension). Ignored for "
                   "local files, which are always dispatched by extension.")
@click.option("--pivot", default=None, help="Pivot spec file (YAML/JSON)")
@click.option("--pivot-info", is_flag=True, default=False, help="Print pivot dimensions and exit")
@click.option("--session", default=None, help="Load session file (.cicwave.yaml)")
@click.option("--export", default=None, help="Export plot to file (PDF/PNG/SVG) and exit")
@click.option("--export-data", "export_data", default=None,
              help="Export the plotted wave data (not the image) to file "
                   "(CSV/TSV/Parquet/Feather/HDF5) and exit. Combine with "
                   "--export to write both. Headless, like --export.")
@click.option("--csv-sep", "csv_sep", default=None, metavar="SEP",
              help="Override CSV delimiter for all .csv files in this run "
                   "(e.g. ';', '|', 'tab'). Disables auto-sniffing.")
@click.option("--csv-comment", "csv_comment", default=None, metavar="STR",
              help="Strip lines starting with STR from text data files "
                   "(e.g. '#', '*', '//'). Pass '' to disable. "
                   "Default: '#' for .dat/.spe/.cou/.chi; off for .csv/.tsv.")
@click.option("--twos-complement", "twos_bits", default=None, type=int,
              metavar="W",
              help="Decode integer columns as W-bit 2's complement after load.")
@click.option("--twos-complement-cols", "twos_cols", default=None,
              metavar="LIST",
              help="Comma-separated column names for --twos-complement "
                   "(default: all except time/frequency sweep columns).")
@click.option("--color/--no-color", default=True, help="Enable/Disable color output")
@click.option("--debug", is_flag=True, default=False, help="Enable debug logging")
def main(files, globs, x, sheet, fmt, pivot, pivot_info, session, export,
         export_data, csv_sep, csv_comment, twos_bits, twos_cols, color,
         debug):
    """cicwave: Advanced waveform viewer with PyQtGraph backend.

    A high-performance waveform viewer focused on PyQtGraph and Qt6 for
    advanced visualization of simulation data.

    Supports: .raw, .csv, .tsv, .xlsx, .json, .parquet, .feather, .npz,
    .h5, .pkl, .vcd (digital), .iqvsa (LitePoint), .stdf (ATE), and more.

    \b
    URL sources:
      cicwave https://example.com/data.csv    Any http(s) URL works directly
      --format json                           Force format for extension-less
                                               REST endpoints

    \b
    Pivot:
      --pivot spec.yaml     Reshape data using pivot spec before viewing
      --pivot-info          Print unique values per pivot dimension and exit

    \b
    API sources:
      cicwave api.yaml      A pivot spec with a 'source:' block fetches its
                             own data from a JSON REST API (no file needed)
      --pivot api.yaml      Same, spelled out

    \b
    Session:
      --session plot.cicwave.yaml         Load saved session
      --export plot.pdf                   Export image and exit (no GUI)
      --export-data out.csv               Export plotted data and exit
      --session s.yaml --export out.pdf   Restore session and export

    \b
    Globs:
      --glob 'data/*.csv'             Repeatable, supports ** for recursion
      --glob '**/*.raw'               Useful on PowerShell which doesn't
                                       auto-expand patterns
    """
    # Set up logging
    import logging
    level = logging.DEBUG if debug else logging.INFO
    setup_logging(color=color, level=level)

    files = _expand_glob_patterns(files, globs)
    files, pivot = _promote_positional_spec(files, pivot)
    _run_wave_pg(files, x, sheet, pivot, pivot_info, session, export,
                 export_data_path=export_data, csv_sep=csv_sep,
                 csv_comment=csv_comment, twos_bits=twos_bits,
                 twos_cols=twos_cols, fmt=fmt)


if __name__ == "__main__":
    main()