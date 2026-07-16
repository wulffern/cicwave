---
layout: home
---

[https://github.com/wulffern/cicwave](https://github.com/wulffern/cicwave)

cicwave is a standalone waveform viewer with a PyQtGraph backend, for
high-performance visualization of analog and mixed-signal simulation
results.

It started life as the waveform viewer inside
[cicsim](https://github.com/wulffern/cicsim), and became its own package
in May 2026 once it was clear the viewer deserved to grow on its own.

## Install

```bash
pip install cicwave
```

For development installation:

```bash
git clone https://github.com/wulffern/cicwave.git
cd cicwave
pip install -e .
```

## Quick start

```bash
cicwave data.raw
```

It also opens data straight from a URL — see [URL
sources](/cicwave/url-sources):

```bash
cicwave https://raw.githubusercontent.com/owid/co2-data/master/owid-co2-data.csv
```

<!--run_output:
run: cicwave --help
-->

## Documentation

- [Usage](/cicwave/usage) — command-line options, basic usage, environment variables
- [File formats](/cicwave/formats) — supported file formats and automatic unit detection
- [URL sources](/cicwave/url-sources) — loading data straight from a REST/CSV URL
- [Pivot](/cicwave/pivot) — reshaping long-format data into waveforms
- [Sessions](/cicwave/sessions) — saving and restoring a viewer state
- [GUI and analysis](/cicwave/analysis) — keyboard shortcuts, mouse controls, analysis tools
- [MCP server](/cicwave/mcp) — `cicwave-mcp`: plot/analyze tools for driving cicwave from an agent
- [Examples](/cicwave/examples) — example plots, generated fresh on every docs build

## Related projects

- [cicsim](https://github.com/wulffern/cicsim) — simulation orchestration and a lightweight tkinter viewer
- [cicpy](https://github.com/wulffern/cicpy) — analog IC design transpiler
- [PyQtGraph](https://pyqtgraph.readthedocs.io/) — high-performance plotting library
