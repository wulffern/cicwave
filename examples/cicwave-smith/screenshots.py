"""Render the docs screenshots of this plugin into docs/assets/.

Run with QT_QPA_PLATFORM=offscreen after ``pip install -e .`` and
``python make_demo.py``: it opens cicwave's window without showing it,
reads antenna.s1p through the plugin and saves what it draws.
"""

import os

from PySide6.QtWidgets import QApplication

from cicwave import plugins, wave_pg
from cicwave.wavefiles import WaveFile

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "..", "docs", "assets")

app = QApplication([])
win = wave_pg.PgWaveWindow("frequency")
win.resize(1100, 700)


def grab(widget, name):
    widget.show()
    app.processEvents()
    widget.grab().save(os.path.join(OUT, name))
    print("wrote", name)


#- The file opened normally: S11 in dB against frequency.
win.openPath("antenna.s1p")
wf = WaveFile("antenna.s1p", xaxis="frequency")
print("columns:", list(wf.df.columns))
wave = wave_pg.PgWave(wf, "S11_dB", "frequency")
wave.reload()
plot = win.tab_widget.currentWidget()
plot.show_wave(wave)
win.show()
grab(win, "smith_s11_db.png")

#- The Smith chart analysis on the same wave.
index = [label for label, _ in plugins.analyses()].index("Smith chart")
win._on_analysis("plugin:%d" % index, wave)
tab = win.tab_widget.currentWidget()
tab.resize(760, 820)
grab(tab, "smith_chart.png")
print(tab.readout.toPlainText())
