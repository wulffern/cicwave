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

from mcp.server.fastmcp import FastMCP, Image

mcp = FastMCP(
    "cicwave",
    instructions=(
        "Tools for plotting and analyzing waveform/measurement data "
        "with cicwave. 'plot' renders one or more signals from a file "
        "(local path or http(s) URL) and returns the image inline. "
        "'pivot_info' inspects the dimensions available for a pivot "
        "spec before you write one. 'analyze' runs headless numeric "
        "analysis (RMS, SNR/SNDR/ENOB, ADC PSD/SFDR, linear fit, "
        "difference) and returns a text summary."
    ),
)


def _open_plot_widget(files, x=None, pivot=None, url_format=None):
    """Load *files* (optionally pivoted) into a fresh plot window.

    Returns ``(CmdWavePg, PgWavePlot)``. Each call builds its own
    disposable window; nothing here is shared across tool calls.
    """
    from .wave_pg import CmdWavePg
    from .wavefiles import WaveFile

    c = CmdWavePg(x)
    win = c.win

    if pivot:
        from .pivot import apply_pivot, load_spec
        spec = load_spec(pivot)
        if not win.browser.xaxis and spec.get('columns'):
            win.browser.xaxis = spec['columns']
        xaxis = win.browser.xaxis or spec.get('columns', '')
        for f in files:
            wf = WaveFile(f, xaxis, fmt=url_format)
            pivoted = apply_pivot(wf.df, spec)
            name = "pivot(%s)" % os.path.basename(f)
            win.openDataFrame(pivoted, name)
    else:
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
        from .pivot import apply_pivot, load_spec
        spec = load_spec(pivot)
        xaxis = x or spec.get('columns', '')
        wf = WaveFile(file, xaxis, fmt=url_format)
        raw = wf.df.copy()
        a_block = spec.get("analysis") or {}
        raw = preprocess_dataframe(raw, a_block.get("preprocess", {}))
        return apply_pivot(raw, spec), (x or spec.get('columns'))
    wf = WaveFile(file, x or "", fmt=url_format)
    return wf.df, x


@mcp.tool()
def plot(
    files: list[str],
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
        waves: Column/signal names to plot. Omit to plot every
            numeric column of the first file except the x-axis.
        x: X-axis column name. Omit to auto-detect (time/frequency/
            first unit-suffixed column).
        pivot: Path to a pivot spec YAML/JSON to reshape long-format
            data (one row per measurement) into one wave per series
            before plotting. `waves` then refers to post-pivot names
            (e.g. a country/parameter value) -- use `pivot_info` first
            to see what those will be.
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
def pivot_info(file: str, pivot: str, url_format: str | None = None) -> str:
    """Show the dimensions available in *file* for a pivot spec.

    Prints available columns and, for each of the spec's index/
    columns/values/conditions, its unique values -- use this to write
    or debug a pivot spec before calling `plot`/`analyze` with it.

    Args:
        file: Local path or http(s) URL to inspect.
        pivot: Path to the pivot spec YAML/JSON.
        url_format: Force the format for an extension-less URL.
    """
    from . import pivot as pivot_mod
    from .wavefiles import WaveFile

    spec = pivot_mod.load_spec(pivot)
    wf = WaveFile(file, "", fmt=url_format)
    return pivot_mod.pivot_info(wf.df, spec)


@mcp.tool()
def analyze(
    file: str,
    steps: list[dict],
    pivot: str | None = None,
    x: str | None = None,
    url_format: str | None = None,
) -> str:
    """Run headless numeric analysis and return a text summary.

    Args:
        file: Local path or http(s) URL to analyze.
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
        pivot: Optional pivot spec path to reshape the data first (and
            apply its analysis.preprocess block, e.g. two's-complement
            decode) before running steps.
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
