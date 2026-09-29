"""UART (8N1-style) decoding of a sampled waveform; no cicwave or Qt needed."""

from dataclasses import dataclass

import numpy as np


@dataclass
class Frame:
    start: float     # time of the start bit's falling edge
    stop: float      # time at the end of the stop bit
    value: int       # the data bits, LSB first
    ok: bool         # False on a framing or parity error


def threshold(y):
    """Midway between the low and high levels (robust to a few glitches)."""
    lo, hi = np.percentile(y, [5, 95])
    return (lo + hi) / 2.0


def estimate_baud(x, y, level=None):
    """Baud rate from the shortest run between edges, or None if too few.

    The shortest runs are single bits; a low percentile of the run lengths
    ignores the odd glitch that a plain minimum would pick up.
    """
    level = threshold(y) if level is None else level
    bits = np.asarray(y) > level
    edges = np.flatnonzero(np.diff(bits.astype(np.int8)))
    if edges.size < 3:
        return None
    t = np.asarray(x, dtype=float)[edges]
    runs = np.diff(t)
    runs = runs[runs > 0]
    if runs.size == 0:
        return None
    shortest = np.percentile(runs, 5)
    #- Average the runs that are about one bit long for a better estimate.
    single = runs[runs < 1.5 * shortest]
    return 1.0 / float(np.mean(single))


def decode(x, y, baud, data_bits=8, parity=None, stop_bits=1,
           invert=False, level=None):
    """Decode the UART frames in samples *y* at times *x*.

    *parity* is None, ``"even"`` or ``"odd"``. The line idles high (low
    if *invert*). Each bit is sampled at its middle.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    level = threshold(y) if level is None else level
    line = (y > level) != invert          # True = idle / mark
    bit = 1.0 / baud
    nbits = 1 + data_bits + (parity is not None) + stop_bits

    def sample(t):
        i = min(np.searchsorted(x, t), len(x) - 1)
        return bool(line[i])

    frames = []
    falls = np.flatnonzero(line[:-1] & ~line[1:]) + 1
    busy_until = -np.inf
    for i in falls:
        t0 = x[i]
        if t0 < busy_until:
            continue                      # an edge inside the last frame
        if t0 + nbits * bit > x[-1]:
            break                         # frame runs past the recording
        if sample(t0 + 0.5 * bit):
            continue                      # glitch, not a start bit
        bits = [sample(t0 + (k + 1.5) * bit) for k in range(nbits - 1)]
        data = bits[:data_bits]
        value = sum(1 << k for k, b in enumerate(data) if b)
        ok = all(bits[data_bits + (parity is not None):])
        if parity is not None:
            ones = sum(data) + bits[data_bits]
            ok &= (ones % 2 == 0) if parity == "even" else (ones % 2 == 1)
        frames.append(Frame(t0, t0 + nbits * bit, value, ok))
        #- Resynchronise half-way into the stop bit, not at its end, so a
        #- slightly fast transmitter's next start bit is not missed.
        busy_until = t0 + (nbits - 0.5) * bit
    return frames


def encode(data, baud, fs, idle_bits=2, data_bits=8):
    """A clean 8N1 waveform carrying *data* (bytes) -- for demos and tests.

    Returns ``(t, v)`` sampled at *fs*, levels 0 and 1.
    """
    levels = [1] * idle_bits
    for byte in data:
        levels += [0] + [(byte >> k) & 1 for k in range(data_bits)] + [1]
    levels += [1] * idle_bits
    spb = fs / baud
    n = int(len(levels) * spb)
    v = np.array([levels[int(i / spb)] for i in range(n)], dtype=float)
    return np.arange(n) / fs, v
