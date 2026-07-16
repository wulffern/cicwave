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

```bash
cicwave --help
```

```bash
Usage: cicwave [OPTIONS] [FILES]...

  cicwave: Advanced waveform viewer with PyQtGraph backend.

  A high-performance waveform viewer focused on PyQtGraph and Qt6 for advanced
  visualization of simulation data.

  Supports: .raw, .csv, .tsv, .xlsx, .json, .parquet, .feather, .npz, .h5,
  .pkl, .vcd (digital), .iqvsa (LitePoint), and more.

  URL sources:
    cicwave https://example.com/data.csv    Any http(s) URL works directly
    --format json                           Force format for extension-less
                                             REST endpoints

  Pivot:
    --pivot spec.yaml     Reshape data using pivot spec before viewing
    --pivot-info          Print unique values per pivot dimension and exit

  Session:
    --session plot.cicwave.yaml         Load saved session
    --export plot.pdf                   Export image and exit (no GUI)
    --export-data out.csv               Export plotted data and exit
    --session s.yaml --export out.pdf   Restore session and export

  Globs:
    --glob 'data/*.csv'             Repeatable, supports ** for recursion
    --glob '**/*.raw'               Useful on PowerShell which doesn't
                                     auto-expand patterns

Options:
  --glob TEXT                     Glob pattern (repeatable). Supports ** for
                                  recursion. Useful on shells like PowerShell
                                  that don't auto-expand.
  --x TEXT                        X-axis column; else CICWAVE_X; else saved
                                  default; else auto
  --sheet TEXT                    Sheet name for Excel files (default: first
                                  sheet)
  --format [csv|tsv|txt|json|xlsx|xls|ods|parquet|feather|html|xml|fwf]
                                  Force the data format for a URL source (e.g.
                                  a REST API endpoint with no file extension).
                                  Ignored for local files, which are always
                                  dispatched by extension.
  --pivot TEXT                    Pivot spec file (YAML/JSON)
  --pivot-info                    Print pivot dimensions and exit
  --session TEXT                  Load session file (.cicwave.yaml)
  --export TEXT                   Export plot to file (PDF/PNG/SVG) and exit
  --export-data TEXT              Export the plotted wave data (not the image)
                                  to file (CSV/TSV/Parquet/Feather/HDF5) and
                                  exit. Combine with --export to write both.
                                  Headless, like --export.
  --csv-sep SEP                   Override CSV delimiter for all .csv files in
                                  this run (e.g. ';', '|', 'tab'). Disables
                                  auto-sniffing.
  --csv-comment STR               Strip lines starting with STR from text data
                                  files (e.g. '#', '*', '//'). Pass '' to
                                  disable. Default: '#' for
                                  .dat/.spe/.cou/.chi; off for .csv/.tsv.
  --twos-complement W             Decode integer columns as W-bit 2's
                                  complement after load.
  --twos-complement-cols LIST     Comma-separated column names for --twos-
                                  complement (default: all except
                                  time/frequency sweep columns).
  --color / --no-color            Enable/Disable color output
  --debug                         Enable debug logging
  --help                          Show this message and exit.

```


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
