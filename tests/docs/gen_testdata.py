#!/usr/bin/env python3
"""Generate synthetic sample data used by the docs examples page.

Run from tests/docs/. Produces test.csv (a comparator-like transient),
pivot_data.csv (an amplifier gain/phase sweep at three temperatures),
and spectrum_data.csv (a PSD of the transient, via cicwave's own FFT
backend) so `make docs` can export real plots from real cicwave
sessions.
"""

import numpy as np
import pandas as pd

# --- Transient data: single/multi/dual-axis examples ------------------

t = np.linspace(0, 2e-6, 2000)

vp = 0.9 + 0.4 * np.sin(2 * np.pi * 2e6 * t)
vn = 0.9 - 0.4 * np.sin(2 * np.pi * 2e6 * t)
vout = np.where(vp > vn, 1.8, 0.0)
ibias = 50e-6 + 5e-6 * np.sin(2 * np.pi * 2e6 * t)

pd.DataFrame({
    "time": t,
    "v(vp)": vp,
    "v(vn)": vn,
    "v(out)": vout,
    "i(ibias)": ibias,
}).to_csv("test.csv", index=False)

# --- Pivot data: amplifier gain/phase vs frequency at three temps -----

freq = np.array([1e3, 1e4, 1e5, 1e6, 1e7])
f0 = 3e5  # single-pole rolloff corner

rows = []
for temp, gain0 in ((-40, 43.0), (27, 42.1), (125, 41.2)):
    gain = gain0 - 10 * np.log10(1 + (freq / f0) ** 2)
    phase = -np.degrees(np.arctan(freq / f0))
    for f, g in zip(freq, gain):
        rows.append({"Parameter": "Gain", "Frequency": int(f),
                      "Measurement": round(float(g), 1), "Temp": temp})
    for f, p in zip(freq, phase):
        rows.append({"Parameter": "Phase", "Frequency": int(f),
                      "Measurement": round(float(p), 1), "Temp": temp})

pd.DataFrame(rows).to_csv("pivot_data.csv", index=False)

# --- Spectrum data: PSD of the v(vp) tone, via cicwave's own FFT ------
#- Uses the same psd_rfft() the GUI's right-click "FFT / PSD" menu item
#- calls, so the plot below is a real cicwave PSD, not a hand-drawn one.

from cicwave.analysis import psd_rfft

#- Column named exactly "frequency" so cicwave auto-selects it as the
#- x-axis with a log scale (see Wave.reload() in wavefiles.py), matching
#- how the GUI's own FFT/PSD plots look.
psd = psd_rfft(t, vp)
pd.DataFrame({
    "frequency": psd.freq_hz,
    "PSD_dB": psd.psd_db,
}).to_csv("spectrum_data.csv", index=False)

print("Wrote test.csv, pivot_data.csv, spectrum_data.csv")
