---
layout: page
title:  usage
math: true
---

* TOC
{:toc }

## Command

<!--run_output:
run: cicwave --help
-->

See the full option list on the [home page](/cicwave/).

## Description

`cicwave` opens an interactive waveform viewer for one or more data files,
local or remote. Files are dispatched by extension; anything unrecognised
falls through to the ngspice `.raw` reader, so non-standard suffixes on
ngspice output (`.raw0`, `.bin`, ...) usually still work.

## Basic usage

Open a single file:

```bash
cicwave data.raw
```

Open multiple files at once:

```bash
cicwave sim1.csv sim2.csv results.xlsx
```

Open many files with a glob pattern. `--glob` is repeatable and supports
`**` for recursion — useful on shells like PowerShell that don't
auto-expand wildcards for external commands:

```bash
cicwave --glob "results/*.csv" --glob "**/*.raw"
```

Positional arguments containing glob metacharacters (`* ? [`) are expanded
the same way, so `cicwave path/*.raw` also works if a file named literally
`path/*.raw` doesn't exist.

Pick the X-axis column explicitly:

```bash
cicwave data.csv --x time
```

If `--x` is omitted, `cicwave` falls back to the `CICWAVE_X` environment
variable, then a saved default, then auto-detection.

## URL sources

Any `http://`/`https://` URL works wherever a local path does:

```bash
cicwave https://raw.githubusercontent.com/owid/co2-data/master/owid-co2-data.csv
```

For a REST endpoint with no file extension, force the format with
`--format`:

```bash
cicwave https://api.example.com/v1/measurements --format json
```

See [URL sources](/cicwave/url-sources) for the full reference
(supported formats, fetch behavior, security notes).

## CSV options

Force a delimiter (disables auto-sniffing for the run):

```bash
cicwave --csv-sep ';' european_data.csv
cicwave --csv-sep tab measurements.csv
```

Strip comment lines from text files (`.csv`, `.tsv`, `.dat`, `.spe`,
`.cou`, `.chi`):

```bash
cicwave --csv-comment '#' results.csv          # strip '#' banners
cicwave --csv-comment '*' spice_log.csv        # SPICE-style comments
cicwave --csv-comment '//' c_style_dump.csv    # multi-char marker
cicwave --csv-comment '' eldo.cou              # disable default for .cou
```

By default, comment stripping is on (`#`) for `.dat`/`.spe`/`.cou`/`.chi`
files and off for `.csv`/`.tsv`.

## Two's complement decoding

Decode integer columns as signed two's complement after load — useful for
raw ADC codes:

```bash
cicwave --twos-complement 12 adc_codes.csv
cicwave --twos-complement 10 --twos-complement-cols ADC_A,ADC_B data.csv
```

`--twos-complement-cols` defaults to every non-time/frequency column.

## Excel sheets

```bash
cicwave data.xlsx --sheet "Sheet2"
```

## Exporting without the GUI

Export a plot image straight to a file — useful in scripts and CI:

```bash
cicwave --session mysession.cicwave.yaml --export plot.pdf
```

Export the plotted **data** instead of (or alongside) the image — one
column per (X, Y) pair, format from the extension (`.csv`, `.tsv`,
`.parquet`, `.feather`, `.h5`):

```bash
cicwave --session mysession.cicwave.yaml --export-data plot.csv
cicwave --session mysession.cicwave.yaml --export plot.pdf --export-data plot.csv
```

This is the same code path as the GUI's **File → Export Data...**, so a
CI job gets the actual processed numbers (pivoted, two's-complement
decoded, ...), not just a picture of them.

See [Sessions](/cicwave/sessions) for the session file format.

## Loading large file sets

Files are opened lazily: only the column header is read on open, and the
full data is parsed (with `pyarrow` when available) the first time a wave
is actually plotted. This makes it practical to drop hundreds of large
CSV/TSV/Excel files into the viewer at once. Selecting "Plot all visible
waves" or "Plot for all files" then triggers the full parse only for the
files you actually plot.

When `PyOpenGL` is installed (it is by default), the pyqtgraph backend
uses GPU-accelerated rendering, which keeps zoom/pan responsive even with
hundreds of curves on screen. Display-time downsampling (lossless,
viewport-aware) is enabled automatically.

## Environment variables

| Variable | Description |
|----------|--------------|
| `CICWAVE_X` | Default X-axis column name, used when `--x` is not given |
