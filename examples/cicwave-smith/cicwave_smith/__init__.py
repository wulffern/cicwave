"""Example cicwave plugin: read Touchstone files and draw a Smith chart.

Registers a reader for ``.s1p`` ... ``.s9p`` files, so cicwave opens
S-parameter measurements and simulations directly, and a "Smith chart"
analysis for any complex reflection coefficient such as ``S11``.
"""

import numpy as np

from . import smith, touchstone

__all__ = ["register"]


def _hz(f):
    for scale, unit in ((1e9, "GHz"), (1e6, "MHz"), (1e3, "kHz")):
        if abs(f) >= scale:
            return "%.4g %s" % (f / scale, unit)
    return "%.4g Hz" % f


def _ohm(z):
    return "%.1f %s j%.1f Ω" % (z.real, "+" if z.imag >= 0 else "−",
                                abs(z.imag))


def _gamma_of(wave):
    """The complex column behind *wave*: itself, or S11 for S11_dB/_deg."""
    df = wave.wfile.df
    key = wave.key
    for suffix in ("_dB", "_deg"):
        if key.endswith(suffix) and key[:-len(suffix)] in df.columns:
            key = key[:-len(suffix)]
    y = np.asarray(df[key])
    if not np.iscomplexobj(y):
        raise ValueError("%s is not complex; a Smith chart needs a "
                         "complex reflection coefficient such as S11" % key)
    if "frequency" in df.columns:
        freq = np.asarray(df["frequency"], dtype=float)
    else:
        freq = np.arange(len(y), dtype=float)
    return key, freq, y


def smith_chart(window, wave):
    """The analysis: runs when "Smith chart" is chosen on *wave*."""
    import pyqtgraph as pg

    key, freq, gamma = _gamma_of(wave)
    z0 = wave.wfile.df.attrs.get("touchstone", {}).get("z0", 50.0)

    tab = window.add_analysis_tab("Smith: %s" % key)
    pw = tab.pw
    pw.hideAxis("left")
    pw.hideAxis("bottom")
    pw.showGrid(x=False, y=False)
    pw.setAspectLocked(True)

    for label, pts in smith.grid_lines():
        width = 2 if label == "r=0" else 1
        pw.plot(pts.real, pts.imag, pen=pg.mkPen((128, 128, 128),
                                                 width=width))
    for r in smith.GRID_R[1:]:
        g = smith.gamma_from_z(r, 1.0)
        t = pg.TextItem("%g" % r, color=(160, 160, 160), anchor=(0.5, 0))
        t.setPos(float(g.real), 0.0)
        pw.addItem(t)
    for x in smith.GRID_X:
        for s in (1, -1):
            g = complex(smith.gamma_from_z(1j * s * x, 1.0))
            t = pg.TextItem("%+gj" % (s * x), color=(160, 160, 160),
                            anchor=(0.5 - 0.5 * g.real, 0.5 + 0.5 * s))
            t.setPos(g.real * 1.04, g.imag * 1.04)
            pw.addItem(t)

    tab.plot(gamma.real, gamma.imag, pen=pg.mkPen("c", width=2))

    best = smith.match_summary(freq, gamma, z0)
    for i, color in ((0, "g"), (len(gamma) - 1, "r"),
                     (best["index"], "y")):
        pw.plot([gamma[i].real], [gamma[i].imag], pen=None, symbol="o",
                symbolBrush=color, symbolSize=9)
        t = pg.TextItem(_hz(freq[i]), color=color, anchor=(-0.1, 1.1))
        t.setPos(gamma[i].real, gamma[i].imag)
        pw.addItem(t)
    pw.setRange(xRange=(-1.15, 1.15), yRange=(-1.15, 1.15), padding=0)

    tab.set_notes(
        "Z0 = %g Ω, %s to %s (green to red)\n"
        "Best match at %s: Z = %s, |Γ| = %.3f, return loss %.1f dB, "
        "VSWR %.2f" % (
            z0, _hz(freq[0]), _hz(freq[-1]), _hz(best["frequency"]),
            _ohm(best["z"]), abs(best["gamma"]), best["return_loss_db"],
            best["vswr"]))


def register(api):
    api.set_description("Reads Touchstone .sNp files; draws Smith charts")
    for n in range(1, 10):
        api.register_reader(".s%dp" % n, touchstone.read)
    api.register_analysis("Smith chart", smith_chart)
