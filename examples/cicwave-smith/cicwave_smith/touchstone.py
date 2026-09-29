"""Touchstone (.sNp) reader; no cicwave or Qt needed.

Reads version 1 files: ``!`` comments, one ``#`` option line
(``# <freq unit> <parameter> <format> R <z0>``, default ``# GHZ S MA R 50``)
and records of a frequency followed by N*N complex values, which may wrap
over several lines. The noise block that can follow a 2-port's data is
skipped.
"""

import os
import re

import numpy as np
import pandas as pd

_FREQ_UNITS = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}


def ports_from_name(path):
    """N from a ``.sNp`` suffix, or None."""
    m = re.search(r"\.s(\d+)p$", path.lower())
    return int(m.group(1)) if m else None


def _options(line):
    unit, param, fmt, z0 = "GHZ", "S", "MA", 50.0
    tokens = line[1:].split()
    i = 0
    while i < len(tokens):
        t = tokens[i].upper()
        if t in _FREQ_UNITS:
            unit = t
        elif t in ("S", "Y", "Z", "H", "G"):
            param = t
        elif t in ("DB", "MA", "RI"):
            fmt = t
        elif t == "R" and i + 1 < len(tokens):
            z0 = float(tokens[i + 1])
            i += 1
        i += 1
    return _FREQ_UNITS[unit], param, fmt, z0


def _to_complex(a, b, fmt):
    if fmt == "RI":
        return a + 1j * b
    mag = 10 ** (a / 20.0) if fmt == "DB" else a
    return mag * np.exp(1j * np.deg2rad(b))


def read(path, ports=None):
    """Read *path* into a DataFrame.

    Columns: ``frequency`` (Hz); for every parameter, e.g. ``S21``, the
    complex value plus ``S21_dB`` (20 log10 of the magnitude) and
    ``S21_deg`` (phase). ``df.attrs['touchstone']`` holds ``z0``,
    ``parameter`` and ``ports``.
    """
    ports = ports or ports_from_name(path)
    if ports is None:
        raise ValueError("%s: can't tell the port count from the name "
                         "(expected .s1p, .s2p, ...)" % path)
    scale, param, fmt, z0 = 1e9, "S", "MA", 50.0
    per_record = 1 + 2 * ports * ports
    records, current = [], []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.split("!", 1)[0].strip()
            if not line:
                continue
            if line.startswith("#"):
                scale, param, fmt, z0 = _options(line)
                continue
            if line.startswith("["):
                raise ValueError("%s: Touchstone 2 keywords are not "
                                 "supported" % path)
            values = [float(t) for t in line.split()]
            #- A 2-port's noise block follows its data with the frequency
            #- starting over: a record that would begin at or below the
            #- last frequency ends the data.
            if not current and records and values[0] <= records[-1][0]:
                break
            current.extend(values)
            if len(current) >= per_record:
                records.append(current[:per_record])
                current = []
    if not records:
        raise ValueError("%s: no data" % path)
    data = np.array(records)

    values = _to_complex(data[:, 1::2], data[:, 2::2], fmt)
    #- Records are row-major (S11 S12 ... S21 ...), except the 2-port,
    #- which is ordered S11 S21 S12 S22.
    names = ["%s%d%d" % (param, r + 1, c + 1)
             for r in range(ports) for c in range(ports)]
    if ports == 2:
        names = ["%s11" % param, "%s21" % param, "%s12" % param,
                 "%s22" % param]

    cols = {"frequency": data[:, 0] * scale}
    for k, name in enumerate(names):
        v = values[:, k]
        cols[name] = v
        with np.errstate(divide="ignore"):
            cols[name + "_dB"] = 20 * np.log10(np.abs(v))
        cols[name + "_deg"] = np.rad2deg(np.angle(v))
    df = pd.DataFrame(cols)
    df.attrs["touchstone"] = {"z0": z0, "parameter": param, "ports": ports,
                              "file": os.path.basename(path)}
    return df
