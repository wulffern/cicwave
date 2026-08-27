#!/usr/bin/env python3
"""MCP server exposing cicwave's headless plotting/analysis as tools.

Lets an agent request a plot (returned as an inline image), inspect
pivot dimensions, or run headless analysis (RMS/SNR/SNDR/ENOB/SFDR,
linear fit, difference) against local files or http(s) URLs -- the
same capabilities available via `cicwave --pivot ... --export`, without
shelling out to the CLI or hand-writing a session YAML file.

Run directly:

    cicwave-mcp

Or point an MCP client's config at the `cicwave-mcp` console script
(installed with the base `cicwave` package -- PySide6/pyqtgraph are
already mandatory dependencies, so no extra install is needed beyond
`pip install "mcp[cli]"` for the server framework itself).

Rendering runs Qt in offscreen mode (no display needed); this is set
before importing anything Qt-related, so it must stay the first real
statement in this module.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile

#- The MCP Python SDK renamed FastMCP to MCPServer in 2.0 and moved it out of
#- mcp.server.fastmcp. The constructor kwargs, the .tool() decorator, .run()
#- and Image are otherwise source-compatible, so accept either generation
#- rather than pinning users to one.
try:
    from mcp.server.mcpserver import Image, MCPServer as _Server  # SDK >= 2.0
except ImportError:  # pragma: no cover - depends on the installed SDK
    from mcp.server.fastmcp import FastMCP as _Server, Image  # SDK 1.x

import inspect

from . import __version__

#- SDK 2.0 reports a version in the initialize handshake and defaults it to an
#- empty string, so clients would otherwise see cicwave with no version. SDK 1.x
#- has no such parameter, hence the signature check rather than a bare kwarg.
_server_kwargs = {}
if "version" in inspect.signature(_Server.__init__).parameters:
    _server_kwargs["version"] = __version__

mcp = _Server(
    "cicwave",
    instructions=(
        "Tools for plotting and analyzing waveform/measurement data "
        "with cicwave. 'plot' renders one or more signals from a file "
        "(local path or http(s) URL) and returns the image inline. "
        "A pivot spec with a 'source:' block fetches its own data from "
        "a JSON REST API, in which case no file is needed. "
        "'pivot_info' inspects the dimensions available for a pivot "
        "spec before you write one. 'analyze' runs headless numeric "
        "analysis (RMS, SNR/SNDR/ENOB, ADC PSD/SFDR, linear fit, "
        "difference) and returns a text summary."
    ),
    **_server_kwargs,
)


def _spec_sources(files, pivot):
    """Resolve what to load: the given *files*, or the spec's own source.

    A pivot spec carrying a ``source:`` block fetches its own data, so
    ``files`` is then not just optional but meaningless -- the labels
    below are the spec itself.
    """
    from .apisource import has_source
    from .pivot import load_spec

    spec = load_spec(pivot) if pivot else None
    if spec is not None and has_source(spec):
        if files:
            raise ValueError(
                "%s has a 'source:' block and fetches its own data; omit "
                "the file argument" % pivot)
        return spec, [pivot], True
    if not files:
        raise ValueError(
            "no file given, and the pivot spec has no 'source:' block to "
            "fetch from")
    return spec, list(files), False


def _open_plot_widget(files, x=None, pivot=None, url_format=None):
    """Load *files* (optionally pivoted) into a fresh plot window.

    Returns ``(CmdWavePg, PgWavePlot)``. Each call builds its own
    disposable window; nothing here is shared across tool calls.
    """
    from .wave_pg import CmdWavePg

    c = CmdWavePg(x)
    win = c.win

    if pivot:
        from .apisource import load_flat_frame
        from .pivot import apply_pivot
        spec, targets, from_source = _spec_sources(files, pivot)
        if not win.browser.xaxis and spec.get('columns'):
            win.browser.xaxis = spec['columns']
        xaxis = win.browser.xaxis or spec.get('columns', '')
        for f in targets:
            raw = load_flat_frame(spec, None if from_source else f,
                                  xaxis=xaxis, fmt=url_format)
            pivoted = apply_pivot(raw, spec)
            name = "pivot(%s)" % os.path.basename(f)
            win.openDataFrame(pivoted, name)
    else:
        if not files:
            raise ValueError("no files given")
        for f in files:
            win.openFile(f, fmt=url_format)

    plot = win.tab_widget.widget(0)
    return c, plot


def _load_single_dataframe(file, x=None, pivot=None, url_format=None):
    """Load *file* (optionally pivoted) into a plain DataFrame.

    Used by ``analyze``, which operates on the data directly rather
    than through a plot widget.
    """
    from .wavefiles import WaveFile

    if pivot:
        from .analysis import preprocess_dataframe
        from .apisource import load_flat_frame
        from .pivot import apply_pivot
        spec, targets, from_source = _spec_sources(
            [file] if file else [], pivot)
        xaxis = x or spec.get('columns', '')
        raw = load_flat_frame(spec, None if from_source else targets[0],
                              xaxis=xaxis, fmt=url_format).copy()
        a_block = spec.get("analysis") or {}
        raw = preprocess_dataframe(raw, a_block.get("preprocess", {}))
        return apply_pivot(raw, spec), (x or spec.get('columns'))
    if not file:
        raise ValueError("no file given")
    wf = WaveFile(file, x or "", fmt=url_format)
    return wf.df, x


@mcp.tool()
def plot(
    files: list[str] | None = None,
    waves: list[str] | None = None,
    x: str | None = None,
    pivot: str | None = None,
    title: str | None = None,
    url_format: str | None = None,
) -> Image:
    """Render a plot from one or more data files and return the image.

    Args:
        files: Local paths or http(s) URLs to plot. Any format
            cicwave supports (CSV, ngspice .raw, VCD, JSON, ...).
            Omit when `pivot` names a spec with a `source:` block,
            which fetches its own data from a REST API.
        waves: Column/signal names to plot. Omit to plot every
            numeric column of the first file except the x-axis.
        x: X-axis column name. Omit to auto-detect (time/frequency/
            first unit-suffixed column).
        pivot: Path to a pivot spec YAML/JSON to reshape long-format
            data (one row per measurement) into one wave per series
            before plotting. `waves` then refers to post-pivot names
            (e.g. a country/parameter value) -- use `pivot_info` first
            to see what those will be. A spec with a `source:` block
            also fetches the data itself, so pass no `files`.
        title: Optional plot title.
        url_format: Force the format for a URL with no recognizable
            extension (csv/tsv/txt/json/xlsx/...). Ignored for local
            files and URLs with a normal extension.
    """
    c, p = _open_plot_widget(files, x=x, pivot=pivot, url_format=url_format)

    names = waves
    if not names:
        wf = next(iter(c.win.browser.files.values()), None)
        if wf is None:
            raise ValueError("no data loaded from %r" % (files,))
        names = [n for n in wf.getWaveNames() if n != x]

    plotted = 0
    for name in names:
        wave = c.win._find_wave(name)
        if wave is None:
            raise ValueError("no such wave/column: %r" % name)
        if p.show_wave(wave, style='Lines'):
            plotted += 1
    if plotted == 0:
        raise ValueError("nothing plotted (empty or unmatched waves)")

    if title:
        from .theme import _get_theme
        p.custom_title = title
        p.plot.setTitle(title, color=_get_theme()['title_color'],
                        size='11pt')

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        out_path = tf.name
    try:
        p._export_matplotlib(out_path)
        with open(out_path, "rb") as fh:
            data = fh.read()
    finally:
        os.unlink(out_path)
    return Image(data=data, format="png")


@mcp.tool()
def pivot_info(pivot: str, file: str | None = None,
               url_format: str | None = None) -> str:
    """Show the dimensions available for a pivot spec.

    Prints available columns and, for each of the spec's index/
    columns/values/conditions, its unique values -- use this to write
    or debug a pivot spec before calling `plot`/`analyze` with it.

    Args:
        pivot: Path to the pivot spec YAML/JSON.
        file: Local path or http(s) URL to inspect. Omit when the spec
            has a `source:` block, which fetches its own data.
        url_format: Force the format for an extension-less URL.
    """
    from . import pivot as pivot_mod
    from .apisource import load_flat_frame

    spec, targets, from_source = _spec_sources([file] if file else [], pivot)
    df = load_flat_frame(spec, None if from_source else targets[0],
                         fmt=url_format)
    return pivot_mod.pivot_info(df, spec)


@mcp.tool()
def analyze(
    steps: list[dict],
    file: str | None = None,
    pivot: str | None = None,
    x: str | None = None,
    url_format: str | None = None,
) -> str:
    """Run headless numeric analysis and return a text summary.

    Args:
        steps: List of analysis steps, each a dict with a "type" key.
            Supported types (same as a pivot spec's analysis.steps --
            see https://wulffern.github.io/cicwave/pivot):
              - rms: {type: rms, column}
              - dynamic_parameters: {type: dynamic_parameters, y_column,
                fs, f0?, fmin?, fmax?, remove_dc?, osr?,
                exclude_harmonics?, sigma_delta_lobe?}
              - adc_psd: {type: adc_psd, y_column, fs, f0?,
                max_harmonics?, osr?, exclude_harmonics?,
                dbfs_amplitude?, filterwidth?, fund_filterwidth?,
                remove_dc?}
              - linear_fit: {type: linear_fit, y_column, x_column?}
              - difference: {type: difference, a_column, b_column}
        file: Local path or http(s) URL to analyze. Omit when `pivot`
            names a spec with a `source:` block.
        pivot: Optional pivot spec path to reshape the data first (and
            apply its analysis.preprocess block, e.g. two's-complement
            decode) before running steps. A spec with a `source:` block
            fetches its own data from a REST API.
        x: X-axis column override.
        url_format: Force the format for an extension-less URL.
    """
    from .analysis import run_analysis_steps

    df, x_column = _load_single_dataframe(
        file, x=x, pivot=pivot, url_format=url_format)
    result = run_analysis_steps(df, steps, x_column=x_column)
    return result.summary_text or "(no output)"


def main():
    mcp.run()


if __name__ == "__main__":
    main()
