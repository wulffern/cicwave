---
layout: page
title:  plugins
math: true
---

* TOC
{:toc }

## Description

`cicwave` stays a general waveform viewer. Support for a private file
format, an in-house instrument or a protocol that isn't public goes in a
separate package, a **plugin**, which cicwave picks up once it is
installed. Without the plugin installed, cicwave is unchanged.

A plugin can:

- **read new file formats**, by filename suffix;
- **annotate recordings** as they load, for example labelling the sections
  of each packet so the constellation dialog can show them one at a time.
  The recording files themselves are not changed;
- **add analyses** to a wave's right-click menu;
- **add constellation presets**, named settings for the constellation
  dialog.

## Writing a plugin

A plugin is an ordinary Python package that declares an entry point in the
`cicwave.plugins` group:

```toml
# pyproject.toml
[project]
name = "my-cicwave-plugin"
dependencies = ["cicwave"]

[project.entry-points."cicwave.plugins"]
mine = "my_cicwave_plugin:register"
```

The entry point names a function that receives the plugin API:

```python
# my_cicwave_plugin/__init__.py
import numpy as np
import pandas as pd
from cicwave import plugins


def read_mybin(path):
    data = np.fromfile(path, dtype="<f4")
    return pd.DataFrame({"sample": np.arange(data.size), "value": data})


def label_packets(df, path):
    """Split every 'burst' annotation into a preamble and a payload."""
    anns = plugins.annotations(df)
    for burst in [a for a in anns if a.get("core:label") == "burst"]:
        start, count = burst["core:sample_start"], burst["core:sample_count"]
        anns.append({"core:sample_start": start, "core:sample_count": 1000,
                     "core:label": "preamble"})
        anns.append({"core:sample_start": start + 1000,
                     "core:sample_count": count - 1000,
                     "core:label": "payload"})
    anns.sort(key=lambda a: a["core:sample_start"])


def peak_to_average(window, wave):
    y = np.abs(np.asarray(wave.y))
    print("%s: peak/average %.2f dB" % (
        wave.key, 20 * np.log10(y.max() / y.mean())))


def register(api):
    api.register_reader(".mybin", read_mybin)
    api.register_annotator(label_packets)
    api.register_analysis("Peak / average", peak_to_average)
    api.register_constellation_preset(
        "QPSK payload", {"symbol_rate": 1e6, "annotation": "payload"})
```

Install it next to cicwave (`pip install -e .` while developing) and it
shows up the next time cicwave starts.

## The API

`register(api)` is called once, the first time cicwave needs its plugins
(when a file is read or a menu is built), so plugins cost nothing at
startup. `api.API_VERSION` (currently `1`) changes when the API changes
incompatibly, so a plugin can check it and refuse to load.

| Call | What it does |
|------|--------------|
| `api.register_reader(suffix, fn)` | `fn(path)` returns a `pandas.DataFrame`. The longest matching suffix wins (so `.foo.bin` beats `.bin`), and plugin readers are tried before the built-in ones. |
| `api.register_annotator(fn)` | `fn(df, path)` runs after every file is read, whichever reader read it, and may change `df` in place. Use `cicwave.plugins.annotations(df)` to get the list of annotations. |
| `api.register_analysis(label, fn)` | Adds `label` to the wave context menu; `fn(window, wave)` runs when it is chosen. |
| `api.register_constellation_preset(name, settings)` | `settings` may set `symbol_rate`, `offset`, `freq_offset`, `phase`, `window_start`, `window_stop`, `conjugate`, `normalize`, `trajectory`, and `annotation`, which selects the first annotated range with that label. |

### Showing results

An analysis's `fn(window, wave)` gets the viewer window and the wave that was
right-clicked (`wave.key`, `wave.y`, and `wave.wfile` with its `fname` and
`df`). To draw its result, open a tab with `window.add_analysis_tab(title)`.
It returns a plot with:

| Member | |
|--------|--|
| `plot(x, y, **kwargs)` | adds a curve; `kwargs` go to pyqtgraph (`pen=None, symbol='o'` for a scatter) |
| `setLabel(axis, text, units)` | axis labels (`'bottom'`, `'left'`) |
| `set_notes(text)` | text at the top of the readout under the plot, kept when cursors move |
| `pw` | the underlying `pyqtgraph.PlotWidget`, e.g. `pw.setAspectLocked(True)` |

Annotations follow the SigMF convention: dicts with `core:sample_start`,
`core:sample_count` and `core:label`. For a SigMF recording the list starts
with the file's own annotations. The constellation dialog offers every
annotated range as a choice of samples, each label numbered on its own
(`payload 1`, `payload 2`, …).

## Failures

A plugin that fails to load, or an annotator that raises, is logged as a
warning under `cicwave.plugins` and skipped; the file still opens. Set
`CICWAVE_PLUGINS=0` to start cicwave with plugin discovery turned off,
which helps when checking whether a problem comes from a plugin.

## Keeping private code private

A plugin lives in its own repository, which can be private. Develop it with
cicwave checked out alongside and both installed in editable mode
(`pip install -e ../cicwave -e .`), and pin a cicwave release in the
plugin's `dependencies` for deployment. If a plugin needs something cicwave
doesn't offer yet, add a generic hook to `cicwave/plugins.py` instead of
patching cicwave for one plugin.
