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


def _expand_glob_patterns(files, patterns):
    """Merge positional ``files`` with files matched by --glob ``patterns``.

    Each pattern is expanded with ``glob.glob(..., recursive=True)`` so that
    ``**`` works. Order: positional first, then each pattern in order. Files
    are de-duplicated while preserving first-seen order. Patterns that match
    nothing are reported on stderr but not fatal.
    """
    out = []
    seen = set()
    for f in files:
        if f not in seen:
            seen.add(f)
            out.append(f)
    for pat in patterns or ():
        matches = sorted(_glob.glob(pat, recursive=True))
        if not matches:
            print("warning: --glob '%s' matched no files" % pat,
                  file=sys.stderr)
            continue
        for m in matches:
            if m not in seen:
                seen.add(m)
                out.append(m)
    return tuple(out)


def _run_wave_pg(files, x, sheet, pivot_spec=None,
                 pivot_info_flag=False, session_path=None, export_path=None):
    """Run the PyQtGraph waveform viewer."""
    x = _resolve_wave_x_from_cli_env(x)
    
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

    if pivot_spec:
        for f in files:
            wf = WaveFile(f, x or "")
            pivoted = apply_pivot(wf.df, spec)
            name = "pivot(%s)" % os.path.basename(f)
            c.openDataFrame(pivoted, name,
                            pivot_spec_path=os.path.abspath(pivot_spec),
                            original_path=os.path.abspath(f))
    else:
        for f in files:
            c.openFile(f, sheet_name=sheet)

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
@click.option("--color/--no-color", default=True, help="Enable/Disable color output")
@click.option("--debug", is_flag=True, default=False, help="Enable debug logging")
def main(files, globs, x, sheet, pivot, pivot_info, session, export, color, debug):
    """cicwave: Advanced waveform viewer with PyQtGraph backend.

    A high-performance waveform viewer focused on PyQtGraph and Qt6 for
    advanced visualization of simulation data.

    Supports: .raw, .csv, .tsv, .xlsx, .json, .parquet, .feather, .h5,
    .pkl, .vcd (digital), .iqvsa (LitePoint), and more.

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
    _run_wave_pg(files, x, sheet, pivot, pivot_info, session, export)


if __name__ == "__main__":
    main()