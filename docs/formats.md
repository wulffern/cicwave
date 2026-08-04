---
layout: page
title:  formats
math: true
---

* TOC
{:toc }

## Supported file formats

`cicwave` dispatches on file extension. Any extension not listed below
falls through to the ngspice raw reader, so non-standard suffixes (e.g.
`.raw0`, `.bin`) on ngspice output usually still work.

### Simulation / measurement formats

| Format | Extension | Description |
|--------|-----------|-------------|
| ngspice raw | `.raw` (and unknown extensions) | Binary simulation results, parsed by `ngraw.py` |
| Xyce print | `.prn` | Sandia Xyce print/probe waveform output |
| Whitespace text | `.dat`, `.spe`, `.cou`, `.chi` | Eldo `.cou`/`.chi`, ngspice `.dat`, generic space/tab columns; `#` comments stripped by default |
| VCD | `.vcd` | Value Change Dump — digital simulation waveforms |
| LitePoint IQ | `.iqvsa` | LitePoint IQxstream / IQfact IQ capture data |
| STDF | `.stdf`, `.stdf.gz` | Semiconductor ATE test results (SEMI E10/V4) — parametric (PTR) results per part/site, gzip auto-detected |
| Raw counter | `.u32` | Bare little-endian `uint32` counter records with a `.meta.json` sidecar giving the tick length and periods per sample; converted to Hz or ns on load, with the dead time between capture chunks preserved |

### Tabular text formats

| Format | Extension | Description |
|--------|-----------|-------------|
| CSV | `.csv` | Delimiter auto-detected from `, ; \t \|` (override with `--csv-sep`; pyarrow when available) |
| TSV | `.tsv`, `.txt` | Tab separated values |
| Fixed-width | `.fwf` | Fixed-width columnar text (`pandas.read_fwf`) |
| HTML | `.html` | First `<table>` in the document |
| XML | `.xml` | `pandas.read_xml` |
| JSON | `.json` | `pandas.read_json` records |

### Spreadsheet formats

| Format | Extension | Description |
|--------|-----------|-------------|
| Excel | `.xlsx`, `.xls` | Microsoft Excel workbooks (sheet selectable with `--sheet`) |
| OpenDocument | `.ods` | LibreOffice / OpenOffice spreadsheets (requires `odfpy`) |

### Big-data / binary formats

| Format | Extension | Description |
|--------|-----------|-------------|
| Parquet | `.parquet` | Columnar storage (requires `pip install pyarrow`) |
| Feather | `.feather` | Arrow IPC file format (requires `pip install pyarrow`) |
| HDF5 | `.h5`, `.hdf5` | Hierarchical data (requires `pip install tables`) |
| Pickle | `.pkl`, `.pickle` | Serialized pandas DataFrame |

### Statistical packages

| Format | Extension | Description |
|--------|-----------|-------------|
| Stata | `.dta`, `.stata` | Stata data files (`pandas.read_stata`) |
| SAS | `.sas7bdat` | SAS transport/data files (`pandas.read_sas`) |
| SPSS | `.sav` | SPSS data files (requires `pyreadstat`) |

## Automatic unit detection

When a column name carries a unit suffix, `cicwave` picks it up so axis
labels and engineering-notation tick formatting work without any manual
configuration. Recognised separators are `_`, ` `, `/`, `[]`, `()`, or
`{}`:

| Column name | Detected unit | Data scaling | Axis label |
|-------------|---------------|--------------|------------|
| `Frequency_MHz` | `Hz` | × 1e6 | "Frequency" |
| `Amplitude [dBm]` | `dBm` | × 1.0 | "Amplitude" |
| `delay_ps` | `s` | × 1e-12 | "delay" |
| `I_uA` | `A` | × 1e-6 | "I" |
| `phase / deg` | `deg` | × 1.0 | "phase" |

SI-prefixed base units (`Hz, V, A, s, W, F, H, Ω/ohm`) with prefixes
`y/z/a/f/p/n/u/µ/m/k/K/M/G/T/P/E` are rescaled to the base unit so ticks
display nice prefixes (e.g. "5.726 GHz") regardless of the unit the data
was stored in. Log-domain units (`dB, dBm, dBV, dBuV, dBc, dBFS, dBi,
dBA`) are kept as the literal string and never rescaled. SPICE-style
names like `v(out)` and `i(M1.d)` are left untouched.

String-based columns are supported as categorical axes (labels rotated 90
degrees).
