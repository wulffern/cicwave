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
import sys
import glob as _glob
import click
from .command import setup_logging


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
        if os.path.exists(f) or not _looks_like_glob(f):
            #- Existing file, or a plain path (let downstream report a
            #- genuine missing-file error rather than silently dropping it).
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


def _run_wave_pg(files, x, sheet, pivot_spec=None,
                 pivot_info_flag=False, session_path=None, export_path=None,
                 csv_sep=None, csv_comment=None, twos_bits=None,
                 twos_cols=None):
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
    if pivot_spec:
        from .pivot import load_spec, pivot_info, apply_pivot
        from .wavefiles import WaveFile
        spec = load_spec(pivot_spec)

        if pivot_info_flag:
            for f in files:
                wf = WaveFile(f, x or "")
                print("--- %s ---" % f)
                print(pivot_info(wf.df, spec))
                print()
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
        for f in files:
            wf = WaveFile(f, x or "")
            raw = wf.df.copy()
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
            c.openDataFrame(pivoted, name,
                            pivot_spec_path=os.path.abspath(pivot_spec),
                            original_path=os.path.abspath(f))
    else:
        for f in files:
            c.openFile(f, sheet_name=sheet)

    if export_path and pivot_spec:
        c.win._pending_export_metrics = summary_text or ""
        c.win.autoplot_pivot_for_export()
    if export_path:
        c.exportAndExit(export_path)
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
@click.option("--pivot", default=None, help="Pivot spec file (YAML/JSON)")
@click.option("--pivot-info", is_flag=True, default=False, help="Print pivot dimensions and exit")
@click.option("--session", default=None, help="Load session file (.cicwave.yaml)")
@click.option("--export", default=None, help="Export plot to file (PDF/PNG/SVG) and exit")
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
def main(files, globs, x, sheet, pivot, pivot_info, session, export, csv_sep,
         csv_comment, twos_bits, twos_cols, color, debug):
    """cicwave: Advanced waveform viewer with PyQtGraph backend.

    A high-performance waveform viewer focused on PyQtGraph and Qt6 for
    advanced visualization of simulation data.

    Supports: .raw, .csv, .tsv, .xlsx, .json, .parquet, .feather, .npz,
    .h5, .pkl, .vcd (digital), .iqvsa (LitePoint), and more.

    \b
    Pivot:
      --pivot spec.yaml     Reshape data using pivot spec before viewing
      --pivot-info          Print unique values per pivot dimension and exit

    \b
    Session:
      --session plot.cicwave.yaml         Load saved session
      --export plot.pdf                   Export to file and exit (no GUI)
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
    _run_wave_pg(files, x, sheet, pivot, pivot_info, session, export,
                 csv_sep=csv_sep, csv_comment=csv_comment,
                 twos_bits=twos_bits, twos_cols=twos_cols)


if __name__ == "__main__":
    main()