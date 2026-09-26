"""
cicwave - Advanced waveform viewer with PyQtGraph backend

A standalone waveform viewer extracted from cicsim, focused on providing
high-performance visualization of simulation data with PyQtGraph and Qt6.

Supports multiple file formats:
- ngspice .raw files
- CSV, TSV, Excel 
- Parquet, HDF5, Feather
- NumPy .npz archives (tabular / bench traces)
- VCD digital waveforms
- LitePoint .iqvsa IQ capture files
- SigMF IQ recordings (.sigmf-meta/.sigmf-data, .sigmf archives)

Features:
- GPU-accelerated rendering with PyQtGraph
- Multi-dimensional data pivoting
- Session save/restore
- Export to PDF/PNG/SVG
- Digital waveform support
- Engineering unit formatting
"""

try:
    from importlib.metadata import PackageNotFoundError, version as _version
    __version__ = _version("cicwave")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.0.0"
__author__ = "Carsten Wulff"
__email__ = "carsten@wulff.no"