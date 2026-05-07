# cicwave

Waveform viewer with PyQtGraph backend for high-performance visualization of simulation data.

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
  - CSV, TSV, Excel spreadsheets
  - Parquet, HDF5, Feather for big data
  - VCD digital waveforms 
  - LitePoint `.iqvsa` IQ capture files
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

| Format | Extension | Description |
|--------|-----------|-------------|
| ngspice | `.raw` | Binary simulation results |
| CSV/TSV | `.csv`, `.tsv` | Comma/tab separated values |
| Excel | `.xlsx` | Spreadsheet format |
| Parquet | `.parquet` | Columnar storage (requires `pip install pyarrow`) |
| HDF5 | `.h5` | Hierarchical data (requires `pip install tables`) |
| VCD | `.vcd` | Digital simulation waveforms |
| LitePoint | `.iqvsa` | IQ capture data |

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
- **Cursors**: Measure time/voltage differences
- **Math expressions**: Create derived signals  
- **Digital analysis**: View bus values and timing
- **Export data**: Save currently visible waveforms

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
