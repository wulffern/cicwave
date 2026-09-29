"""Write antenna.s1p: a resonant antenna from 2 to 3 GHz, as a VNA would.

The antenna is modelled as a series RLC (35 ohm, resonant at 2.45 GHz,
Q = 8) behind a short 50 ohm feed line, which rotates the trace on the
chart. The file uses the common ``# GHZ S MA R 50`` options.
"""

import numpy as np

f = np.linspace(2e9, 3e9, 201)
w = 2 * np.pi * f
r, f0, q = 35.0, 2.45e9, 8.0
ind = q * r / (2 * np.pi * f0)
c = 1 / ((2 * np.pi * f0) ** 2 * ind)
z = r + 1j * w * ind + 1 / (1j * w * c)
gamma = (z - 50) / (z + 50)
#- 12 mm of 50 ohm line at 0.7 c: an extra phase of -2 * beta * length.
gamma *= np.exp(-2j * w * 0.012 / (0.7 * 3e8))

with open("antenna.s1p", "w") as fh:
    fh.write("! Demo antenna for the cicwave Smith chart plugin\n")
    fh.write("# GHZ S MA R 50\n")
    for fi, g in zip(f, gamma):
        fh.write("%.6f %.6f %.3f\n" % (fi / 1e9, abs(g), np.degrees(np.angle(g))))
print("wrote antenna.s1p (%d points)" % f.size)
