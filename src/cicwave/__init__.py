"""
cicwave - Advanced waveform viewer with PyQtGraph backend

A standalone waveform viewer extracted from cicsim, focused on providing
high-performance visualization of simulation data with PyQtGraph and Qt6.

Supports multiple file formats:
- ngspice .raw files
- CSV, TSV, Excel 
- Parquet, HDF5, Feather
- VCD digital waveforms
- LitePoint .iqvsa IQ capture files

Features:
- GPU-accelerated rendering with PyQtGraph
- Multi-dimensional data pivoting
- Session save/restore
- Export to PDF/PNG/SVG
- Digital waveform support
- Engineering unit formatting
"""

__version__ = "1.0.0"
__author__ = "Carsten Wulff"
__email__ = "carsten@wulff.no"