# cicwave

[![docs](https://github.com/wulffern/cicwave/actions/workflows/docs.yml/badge.svg)](https://github.com/wulffern/cicwave/actions/workflows/docs.yml)

Waveform viewer with PyQtGraph backend for high-performance visualization of simulation data.

Full documentation: <https://wulffern.github.io/cicwave/>

cicwave is a standalone package extracted from
[cicsim](https://github.com/wulffern/cicsim), focused on providing the 
best possible waveform viewing experience with PyQtGraph and Qt6.

## Background

I made my first waveform viewer (<https://github.com/wulffern/NextGenLab.Chart>)
back in 2001 during a summer internship. NextGenLab.Chart evolved into
<https://github.com/wulffern/SystemDotNet.Report> during my Ph.D. In I always
had in my plan to port SdnReport to Mac and Linux, but I never really did (no
coding time). 

In December 2023 I needed a waveform viewer for cicsim (read ngspice raw files),
so I wrote one by hand. It was pretty basic. 

Enter March 2026, and the advent of agentic coding. It was finally possible to
revive SdnReport in the form of a Qt port to cicwave. Initially it lived inside
cicsim, however, in May 2026 it became clear that cicwave deserved it's own
repo. As such, you're here. 

## Features

- **High-performance rendering** with PyQtGraph and GPU acceleration
- **Multiple file format support**: 
  - ngspice `.raw` files
  - Xyce `.prn` print waveform files
  - Whitespace-separated text (`.dat`, `.spe`, `.cou`, `.chi`)
  - CSV, TSV, plain `.txt`, Excel, OpenDocument spreadsheets
  - Parquet, HDF5, Feather for big data
  - JSON, HTML, XML, fixed-width tables
  - Pickle (`.pkl`) for cached DataFrames
  - VCD digital waveforms 
  - LitePoint `.iqvsa` IQ capture files
  - Statistical formats: Stata (`.dta`), SAS (`.sas7bdat`), SPSS (`.sav`)
- **Multi-dimensional data pivoting** with YAML specifications
- **Digital waveform support** with separate analog/digital panes  
- **Session save/restore** with `.cicwave.yaml` files
- **Export capabilities** to PDF, PNG, SVG formats
- **Engineering unit formatting** and auto-detection
- **GPU-accelerated plotting** for smooth interaction with large datasets

## Installation

```bash
pip install cicwave
```

For development installation:

```bash
git clone https://github.com/wulffern/cicwave.git
cd cicwave
pip install -e .
```

## Usage

### Basic Usage

Open waveform files directly:

```bash
# Single file
cicwave data.raw

# Multiple files  
cicwave sim1.csv sim2.csv results.xlsx

# Glob patterns (useful on PowerShell)
cicwave --glob "results/*.csv" --glob "**/*.raw"

# Force a CSV delimiter (disables auto-sniffing for this run)
cicwave --csv-sep ';' european_data.csv
cicwave --csv-sep tab measurements.csv

# Strip comment lines from text files (CSV/TSV/.dat/.spe/.cou/.chi)
cicwave --csv-comment '#' results.csv          # strip '#' banners
cicwave --csv-comment '*' spice_log.csv        # SPICE-style comments
cicwave --csv-comment '//' c_style_dump.csv    # multi-char marker
cicwave --csv-comment '' eldo.cou              # disable default for .cou
```

### Advanced Features

**Multi-dimensional data reshaping:**
```bash
cicwave --pivot analysis.yaml dataset.csv
```

**Session management:**
```bash
# Save your current plot configuration in the GUI (File → Save Session)
cicwave --session my_analysis.cicwave.yaml

# Export plots without opening GUI
cicwave --session config.yaml --export results.pdf
```

**Data exploration:**
```bash
# Preview pivot dimensions before plotting
cicwave --pivot spec.yaml --pivot-info data.csv
```

## File Format Support

cicwave dispatches on file extension. Any extension not listed below falls
through to the ngspice raw reader, so non-standard suffixes (e.g. `.raw0`,
`.bin`) on ngspice output usually still work.

### Simulation / measurement formats

| Format | Extension | Description |
|--------|-----------|-------------|
| ngspice raw | `.raw` (and unknown extensions) | Binary simulation results, parsed by `ngraw.py` |
| Xyce print | `.prn` | Sandia Xyce print/probe waveform output |
| Whitespace text | `.dat`, `.spe`, `.cou`, `.chi` | Eldo `.cou`/`.chi`, ngspice `.dat`, generic space/tab columns; `#` comments stripped by default |
| VCD | `.vcd` | Value Change Dump — digital simulation waveforms |
| LitePoint IQ | `.iqvsa` | LitePoint IQxstream / IQfact IQ capture data |

### Tabular text formats

| Format | Extension | Description |
|--------|-----------|-------------|
| CSV | `.csv` | Delimiter auto-detected from `, ; \t |` (override with `--csv-sep`; pyarrow when available) |
| TSV | `.tsv`, `.txt` | Tab separated values |
| Fixed-width | `.fwf` | Fixed-width columnar text (`pandas.read_fwf`) |
| HTML | `.html` | First `<table>` in the document |
| XML | `.xml` | `pandas.read_xml` |
| JSON | `.json` | `pandas.read_json` records |

### Spreadsheet formats

| Format | Extension | Description |
|--------|-----------|-------------|
| Excel | `.xlsx`, `.xls` | Microsoft Excel workbooks (sheet selectable) |
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

## Environment Variables

- `CICWAVE_X` - Default X-axis column name

## GUI Features

### Waveform Interaction
- **Mouse wheel**: Zoom in/out
- **Middle click + drag**: Pan
- **Right click**: Context menu with analysis tools
- **Ctrl+A**: Auto-fit all waveforms
- **Ctrl+Mouse wheel**: Zoom X-axis only

### Analysis Tools

Right-click a trace in the wave tree for the analysis menu:

- **Cursors**: A/B markers measure time / voltage differences
- **FFT / PSD**: Hanning-windowed spectrum (peak-normalised dB)
- **ADC PSD (SNDR, SFDR, harmonics)…**: ADC characterisation
- **SNR / SNDR / ENOB…**: numeric metrics with the same backend
- **Histogram**: distribution + Gaussian fit
- **Differentiate (dy/dx)**: numerical derivative
- **Linear fit…**, **Difference (this − other)…**, **X vs Y…**
- **2's complement decode** submenu: 8 / 10 / 12 / 16-bit signed
- **Math expressions**: create derived signals
- **Digital analysis**: bus values and timing
- **Export data**: save currently visible waveforms

#### ADC PSD dialog

Tailored for ADC bench data and behavioural simulations, inspired by
`oct_dofft.m` / `dofftsd.m`:

| Field | Meaning |
|-------|---------|
| `Sample rate F_s` | Empty → infer from the time axis |
| `Fundamental F₀` | Empty / 0 → auto (strongest non-DC bin, with DC and Nyquist guard bands) |
| `Oversampling OSR` | `>1` integrates noise only over the in-band slice (`1 … N_fft / OSR`), σΔ-style |
| `Max harmonic order` | Default 5 (H2…H5 typical for ADC reports) |
| `Harmonic lobe ±bins` | Half-width for the harmonic masks. Hann main lobe is 3 bins; ±3 (7 bins) is a safe default for non-coherent captures |
| `Fundamental lobe ±bins` | Separate width for the fundamental — use a wider window when jitter or drift smears the tone more than the harmonics |
| `Full-scale amplitude A_FS` | Peak FS sine amplitude (same units as `y`). Empty → spectrum is **dBc**; set → **dBFS** plot + `Signal level = … dBFS` line |
| `Exclude harmonics from SNR…` | Splits SNR vs SNDR (default on); off matches σΔ "in-band noise only" |
| `Logarithmic frequency axis` | Default on; dialog and markers stay aligned in either mode |

**Reported metrics** (per call, also shown as a banner on the main wave tab):

- `SNR`, `SNDR`, `ENOB` (`dynamic_parameters` backend)
- `SFDR` — IEEE definition: ratio of fundamental lobe to the **largest
  spur, harmonics included** (always in dBc)
- `Hn` table — for each harmonic up to *Max*: integrated `lobe power`
  (dBc) and `peak` bin (dBc)
- Fundamental + harmonics drawn as vertical markers, with diamond
  glyphs at the spectrum peak nearest each tone

All dialog fields are **persisted between invocations** via QSettings
(`cicwave/cicwave/{snr_dialog,adc_psd_dialog}/…`), so running ADC PSD on
the next signal reuses the same `F_s`, `A_FS`, lobe widths and so on.

### Session Management
- **File → Save Session**: Save current plot configuration
- **File → Load Session**: Restore previous analysis
- Sessions store: visible signals, zoom levels, cursor positions, analysis setup

## Pivot Specifications

For multi-dimensional datasets, use YAML pivot specs to reshape data before visualization:

```yaml
# analysis.yaml
index: time          # X-axis (rows)
columns: [corner, temperature]  # Create separate traces for each combination  
values: ["v(out)", "v(in)"]     # Y-axis signals to plot
conditions:          # Filter data
  frequency: 1e9
  process: tt
```

```bash
cicwave --pivot analysis.yaml monte_carlo_results.csv
```

## Migrating from cicsim

If you previously used `cicsim wave` or the standalone `cicwave` from cicsim:

1. **Install standalone cicwave**: `pip install cicwave`
2. **Update workflows**: Replace `cicsim wave --backend pg` with just `cicwave`
3. **Session files**: Existing `.cicwave.yaml` files are fully compatible
4. **Tkinter users**: `cicsim wave --backend tk` still works in cicsim for lightweight usage

## Development

### Running Tests

```bash
# Run all unit tests
python -m unittest discover -s tests/unittests/ -p 'test_*.py' -v

# Test specific functionality
python -m unittest tests.unittests.test_wavefiles_lazy -v
```

### Project Structure

```
cicwave/
├── src/cicwave/
│   ├── cli.py          # Command-line interface
│   ├── wave_pg.py      # Main PyQtGraph viewer
│   ├── wavefiles.py    # File I/O and data loading
│   ├── ngraw.py        # ngspice binary parser  
│   ├── pivot.py        # Data reshaping
│   ├── theme.py        # Color themes
│   └── command.py      # Logging utilities
├── tests/unittests/    # Unit test suite
└── scripts/            # Windows shortcut generator
```

## License

MIT License - see LICENSE file for details.

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality  
4. Ensure all tests pass
5. Submit a pull request

## Related Projects

- **[cicsim](https://github.com/wulffern/cicsim)** - Simulation orchestration and lightweight tkinter viewer
- **[cicpy](https://github.com/wulffern/cicpy)** - Analog IC design transpiler 
- **[PyQtGraph](https://pyqtgraph.readthedocs.io/)** - High-performance plotting library
