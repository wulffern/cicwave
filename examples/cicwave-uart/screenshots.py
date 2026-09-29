"""Render the docs screenshots of this plugin into docs/assets/.

Run with QT_QPA_PLATFORM=offscreen after ``pip install -e .`` and
``python make_demo.py``: it opens cicwave's window without showing it,
runs the plugin on uart_demo.csv and saves what it draws.
"""

import os
from unittest import mock

import numpy as np
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog

from cicwave import wave_pg
from cicwave.wavefiles import WaveFile

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "..", "docs", "assets")

app = QApplication([])
win = wave_pg.PgWaveWindow("time")


def grab(widget, name):
    widget.show()
    app.processEvents()
    widget.grab().save(os.path.join(OUT, name))
    print("wrote", name)


#- Help > Plugins
with mock.patch.object(QDialog, "exec", lambda d: grab(d, "uart_plugins.png")):
    win._show_plugins()

#- The baud-rate question, as the analysis asks it.
dlg = QInputDialog(win)
dlg.setWindowTitle("Decode UART")
dlg.setInputMode(QInputDialog.DoubleInput)
dlg.setDoubleDecimals(0)
dlg.setDoubleRange(1.0, 1e9)
dlg.setLabelText("Baud rate (estimated 115291):")
dlg.setDoubleValue(115291)
grab(dlg, "uart_baud.png")
dlg.close()

#- Run the analysis, accepting the estimated baud rate.
wave = wave_pg.PgWave(WaveFile("uart_demo.csv", xaxis="time"), "tx", "time")
wave.reload()
with mock.patch.object(QInputDialog, "getDouble",
                       lambda *a: (a[3], True)):
    win._on_analysis("plugin:0", wave)
tab = win.tab_widget.currentWidget()
tab.resize(1000, 500)
win.show()
grab(tab, "uart_decoded.png")
tab.pw.setXRange(0.0, 0.3e-3, padding=0)
tab.pw.setYRange(float(np.min(wave.y)), 3.9)
grab(tab, "uart_decoded_zoom.png")
