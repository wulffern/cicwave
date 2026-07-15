"""Regression test for PgWavePlot._export_matplotlib's log-x handling.

pyqtgraph's ViewBox reports its range in log10 space when the plot is
in log-x mode (it pre-transforms data rather than using a "true" log
axis). _export_matplotlib mirrors that range onto a matplotlib Axes via
set_xlim(), which expects real data values -- feeding it the raw log10
range silently produced a near-empty exported plot for any log-x wave
(e.g. an FFT/PSD or ngspice AC-analysis frequency sweep).
"""

import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

try:  # pragma: no cover - optional GUI deps
    from cicwave.wave_pg import PgWavePlot
    HAVE_PG = True
except Exception:
    HAVE_PG = False


def _make_wave(key, x, y, *, logx=False, xlabel="Frequency", xunit="Hz",
               yunit="dB"):
    return SimpleNamespace(
        key=key, ylabel=key, xlabel=xlabel, xunit=xunit, yunit=yunit,
        logx=logx, style="Lines", x=np.asarray(x), y=np.asarray(y),
    )


class _LogView:
    """Stand-in for a pyqtgraph ViewBox in log-x mode: range is log10."""

    def __init__(self, log_xrange, yrange=(0.0, 1.0)):
        self._xr = log_xrange
        self._yr = yrange

    def viewRange(self):
        return [list(self._xr), list(self._yr)]


@unittest.skipUnless(HAVE_PG, "pyqtgraph / PySide6 not installed")
class ExportMatplotlibLogXTest(unittest.TestCase):

    def _build(self, xrange_data):
        freq = np.logspace(5, 8, 50)  # 100 kHz .. 100 MHz
        psd = -np.linspace(0, 100, 50)
        wave = _make_wave("psd", freq, psd, logx=True)

        p = PgWavePlot.__new__(PgWavePlot)
        p.wave_data = {"psd": (wave, wave.yunit)}
        p._digital_waves = {}
        p._right_vb = None
        p._unit_vb = {}
        p.custom_xlabel = None
        p.custom_ylabel = None
        p.custom_title = None
        p._annotations = []
        p._metrics_banner = ""
        p._logx = True

        #- log10(1e5)=5, log10(1e8)=8: the exact shape of the bug -
        #- these got applied verbatim to matplotlib's linear-valued
        #- set_xlim instead of being un-logged first.
        p.plot = SimpleNamespace(vb=_LogView(xrange_data))

        def _stats():
            return []
        p.getStats = _stats
        return p

    def test_log_x_view_range_is_unlogged_before_set_xlim(self):
        p = self._build((5.0, 8.0))
        captured = {}

        real_savefig = None
        import matplotlib.figure

        def fake_savefig(self_fig, *a, **kw):
            ax = self_fig.axes[0]
            captured["xlim"] = ax.get_xlim()

        with mock.patch.object(
                matplotlib.figure.Figure, "savefig", fake_savefig):
            with tempfile.NamedTemporaryFile(suffix=".svg") as tf:
                p._export_matplotlib(tf.name)

        self.assertIn("xlim", captured)
        x0, x1 = captured["xlim"]
        # Must be real Hz values (~1e5 .. 1e8), not the raw log10 range
        # (~5 .. 8) that caused the exported plot to render blank.
        self.assertGreater(x0, 1e4)
        self.assertLess(x1, 1e9)


if __name__ == "__main__":
    unittest.main()
