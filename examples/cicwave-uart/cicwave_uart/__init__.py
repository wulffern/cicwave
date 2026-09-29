"""Example cicwave plugin: decode UART bytes from a waveform.

Right-click a wave holding a UART line (a logic-analyser channel, a scope
capture, a simulated TX pin) and choose "Decode UART...". The plugin
guesses the baud rate from the shortest pulse, asks you to confirm it,
and opens a tab with the waveform and each decoded byte written over its
frame.
"""

import numpy as np

from .uart import decode, estimate_baud, threshold

__all__ = ["register", "decode", "estimate_baud"]


def _label(value):
    ch = chr(value)
    return repr(ch)[1:-1] if ch.isprintable() else "0x%02X" % value


def decode_uart(window, wave):
    """The analysis: runs when "Decode UART..." is chosen on *wave*."""
    from PySide6.QtWidgets import QInputDialog
    import pyqtgraph as pg

    x = np.asarray(wave.x, dtype=float)
    y = np.real(np.asarray(wave.y))
    level = threshold(y)
    guess = estimate_baud(x, y, level) or 9600.0
    baud, ok = QInputDialog.getDouble(
        window, "Decode UART", "Baud rate (estimated %.0f):" % guess,
        round(guess), 1.0, 1e9, 0)
    if not ok:
        return
    frames = decode(x, y, baud, level=level)

    tab = window.add_analysis_tab("UART: %s" % wave.key)
    tab.plot(x, y, pen=pg.mkPen("c", width=1))
    tab.setLabel("bottom", "Time", units="s")
    tab.setLabel("left", wave.key)
    top = float(np.max(y))
    for f in frames:
        text = pg.TextItem(_label(f.value), color="y" if f.ok else "r",
                           anchor=(0.5, 1.0))
        text.setPos((f.start + f.stop) / 2, top)
        tab.pw.addItem(text)

    good = [f for f in frames if f.ok]
    errors = len(frames) - len(good)
    tab.set_notes("%d bytes at %.0f baud%s\n%s" % (
        len(frames), baud,
        ", %d framing error(s)" % errors if errors else "",
        "".join(_label(f.value) for f in frames)))


def register(api):
    api.set_description("Decodes UART bytes from a waveform")
    api.register_analysis("Decode UART...", decode_uart)
