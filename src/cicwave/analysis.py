#!/usr/bin/env python3
"""Qt-free waveform analysis API for cicwave.

All functions accept array-likes and return numpy arrays or small result
objects. The PyQtGraph GUI imports this module only — no duplicated math.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np

__all__ = [
    "decode_twos_complement",
    "rms",
    "resample_uniform",
    "time_base_irregularity",
    "UniformResampleResult",
    "psd_rfft",
    "PsdResult",
    "dynamic_parameters",
    "DynamicParametersResult",
    "HarmonicLevel",
    "AdcPsdResult",
    "adc_psd_analysis",
    "linear_fit",
    "LinearFitResult",
    "difference",
    "run_analysis_steps",
    "AnalysisRunResult",
]


def decode_twos_complement(
        codes: np.ndarray,
        width_bits: int) -> np.ndarray:
    """Map unsigned *width_bits*-wide integers to signed 2's complement."""
    if width_bits < 1 or width_bits > 63:
        raise ValueError("width_bits must be in [1, 63], got %s" % width_bits)
    c = np.asarray(codes, dtype=np.int64)
    mask = (1 << width_bits) - 1
    c = c & mask
    sign = 1 << (width_bits - 1)
    return np.where(c >= sign, c - (1 << width_bits), c).astype(np.float64)


def rms(y: np.ndarray) -> float:
    """RMS of finite samples."""
    a = np.asarray(y, dtype=np.float64)
    a = a[np.isfinite(a)]
    if len(a) == 0:
        return float("nan")
    return float(np.sqrt(np.mean(a * a)))


@dataclass(frozen=True)
class UniformResampleResult:
    x_new: np.ndarray
    y_new: np.ndarray
    fs: float


def time_base_irregularity(x: np.ndarray) -> float:
    """Relative spread of positive dt: std(dt)/median(dt). 0 = uniform."""
    x = np.asarray(x, dtype=np.float64)
    if len(x) < 3:
        return 0.0
    dx = np.diff(x)
    dx = dx[np.isfinite(dx) & (dx > 0)]
    if len(dx) < 2:
        return 0.0
    med = float(np.median(dx))
    if med <= 0:
        return float("inf")
    return float(np.std(dx) / med)


def resample_uniform(
        x: np.ndarray,
        y: np.ndarray,
        dt: Optional[float] = None,
        num_points: Optional[int] = None) -> UniformResampleResult:
    """Linearly interpolate *y* onto a uniform *x* grid.

    Default *dt* is the median positive ``diff(x)``.
    """
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if len(x) != len(y) or len(x) < 2:
        raise ValueError("x and y must have the same length >= 2")
    dx = np.diff(x)
    dx = dx[np.isfinite(dx) & (dx > 0)]
    if len(dx) == 0:
        raise ValueError("no strictly increasing x segments")
    if dt is None:
        dt = float(np.median(dx))
    if dt <= 0:
        raise ValueError("dt must be positive")
    x0, x1 = float(x[0]), float(x[-1])
    if x1 <= x0:
        raise ValueError("x must span a positive range")
    if num_points is not None:
        if num_points < 2:
            raise ValueError("num_points must be >= 2")
        x_new = np.linspace(x0, x1, num_points)
    else:
        x_new = np.arange(x0, x1 + 0.5 * dt, dt, dtype=np.float64)
        if len(x_new) < 2:
            x_new = np.array([x0, x1], dtype=np.float64)
    y_new = np.interp(x_new, x, y)
    fs = 1.0 / dt
    return UniformResampleResult(x_new=x_new, y_new=y_new, fs=fs)


@dataclass(frozen=True)
class PsdResult:
    freq_hz: np.ndarray
    psd_db: np.ndarray
    meta: Mapping[str, Any]


def psd_rfft(
        x: np.ndarray,
        y: np.ndarray,
        *,
        auto_resample: bool = True,
        irregularity_threshold: float = 0.2,
        dbfs_amplitude: Optional[float] = None) -> PsdResult:
    """Single-sided PSD (Hanning window) vs frequency.

    If *dbfs_amplitude* is ``None``, output is **dB relative to peak**
    (matches legacy cicwave: ``10*log10(psd / max(psd))``), DC bin dropped.

    If *dbfs_amplitude* is set (*A_fs*, **peak** amplitude of a full-scale
    sine in the same units as *y*), each **positive-frequency** bin carries
    power ``P_k`` from Parseval (interior bins: ``2|Y[k]|²/N``, Nyquist and
    DC handled separately).  The plot is **10·log10(P_k / E_ref)** with
    ``E_ref = (A_fs²/2) · Σ w[n]²``, i.e. the expected **total** energy of
    a full-scale sine after the same Hanning window (sin² → ½).  A coherent
    full-scale tone then peaks **at or a few dB below 0 dBFS** because the
    window spreads energy across bins — not a single bin at exactly 0 dBFS.
    """
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    meta: dict[str, Any] = {}
    if len(y) < 2:
        raise ValueError("need at least 2 samples")
    if len(x) != len(y):
        raise ValueError("x and y length mismatch")

    irr = time_base_irregularity(x)
    meta["irregularity"] = irr
    if auto_resample and irr > irregularity_threshold:
        res = resample_uniform(x, y)
        x, y = res.x_new, res.y_new
        meta["resampled"] = True
        meta["fs"] = res.fs
        dt = 1.0 / res.fs
    else:
        meta["resampled"] = False
        dt = float(np.mean(np.diff(x)))
        if dt <= 0 or not np.isfinite(dt):
            raise ValueError("non-positive or invalid mean(dt)")
        meta["fs"] = 1.0 / dt

    n = len(y)
    w = np.hanning(n)
    yw = y * w
    Y = np.fft.rfft(yw)
    freqs_all = np.fft.rfftfreq(n, d=dt)

    if dbfs_amplitude is not None and np.isfinite(dbfs_amplitude):
        if dbfs_amplitude <= 0:
            raise ValueError("dbfs_amplitude must be positive")
        A_fs = float(dbfs_amplitude)
        #- Parseval-consistent bin power for a **real** windowed sequence:
        #- ``sum((yw)²) = (1/N) · (|Y0|² + 2·Σ|Yk|² + |Y_{N/2}|²)`` (N even).
        p_bin = np.abs(Y) ** 2
        if n % 2 == 0:
            p_bin[1:-1] *= 2.0
        else:
            p_bin[1:] *= 2.0
        p_bin /= float(n)
        ref_energy = 0.5 * (A_fs ** 2) * float(np.sum(w * w))
        ref_energy = max(ref_energy, 1e-300)
        p_bin = np.maximum(p_bin, 1e-300)
        psd_db = 10.0 * np.log10(p_bin[1:] / ref_energy)
        freqs = freqs_all[1:len(p_bin)]
        meta["dbfs"] = True
        meta["dbfs_ref_energy"] = ref_energy
    else:
        psd = np.abs(Y) ** 2
        psd = np.maximum(psd[1:], 1e-300)
        psd_db = 10.0 * np.log10(psd / np.max(psd))
        freqs = freqs_all[1:]
        meta["dbfs"] = False

    return PsdResult(freq_hz=freqs, psd_db=psd_db, meta=meta)


@dataclass(frozen=True)
class DynamicParametersResult:
    snr_db: float
    sndr_db: float
    enob: float
    #- Fundamental actually used (Hz); ``nan`` if unknown / failed.
    f0_hz: float = float("nan")
    #- Centre bin of the fundamental (0-based *rfft* index).
    signal_bin: int = -1


def _signal_bins_mask(
        k_sig: int,
        n_bins: int,
        *,
        filterwidth: int,
        sigma_delta_lobe: bool) -> np.ndarray:
    """Boolean mask of *rfft* bins that belong to the fundamental tone.

    * ``sigma_delta_lobe=True`` matches ``dofftsd.m`` Hanning bins
      ``fbin + [-1, 0, 1, 2]`` (1-based ``fbin`` → 0-based *k_sig*).
    * Otherwise use a symmetric band ``± filterwidth`` around *k_sig*
      (``filterwidth=1`` → three bins, like ``oct_dofft.m`` ``[-1,0,1]``;
      default *filterwidth* is 3 for wide main/harmonic lobes).
    """
    if sigma_delta_lobe:
        offs = np.array([-1, 0, 1, 2], dtype=np.int32)
    else:
        w = int(max(0, filterwidth))
        offs = np.arange(-w, w + 1, dtype=np.int32)
    ks = k_sig + offs
    ks = ks[(ks >= 0) & (ks < n_bins)]
    mask = np.zeros(n_bins, dtype=bool)
    mask[np.unique(ks)] = True
    return mask


def dynamic_parameters(
        y: np.ndarray,
        fs: float,
        f0: Optional[float] = None,
        *,
        fmin: float = -1.0,
        fmax: float = -1.0,
        remove_dc: bool = True,
        filterwidth: int = 3,
        max_harmonics: int = 6,
        osr: float = 1.0,
        exclude_harmonics: Optional[bool] = None,
        sigma_delta_lobe: Optional[bool] = None,
        auto_f0_guard_lo: int = 2,
        auto_f0_guard_hi: int = 10) -> DynamicParametersResult:
    """SNR, SNDR (dB), and ENOB from a captured waveform.

    Uses a Hanning window and *rfft* bin powers.

    * *f0* — fundamental frequency (Hz). Use ``None``, ``NaN``, or ``<= 0``
      to **auto-detect** the tone as the strongest bin in the spectrum
      (with guards near DC and Nyquist), matching the spirit of
      ``oct_dofft.m`` / ``dofftsd.m``.
    * *osr* — oversampling ratio. For ``osr > 1``, the **noise** is summed
      only over the in-band bins ``1 … floor(N_fft / osr)`` (same indexing
      idea as ``noiseBins = 1:length(y)/OSR`` in ``dofftsd.m``). When
      ``osr > 1``, harmonics are **not** removed from the noise floor by
      default (SNDR equals in-band SNR), as in the sigma-delta script.
    * *sigma_delta_lobe* — if ``True``, fundamental bins are
      ``k_sig + [-1,0,1,2]`` (``dofftsd.m``). If ``None``, this is enabled
      automatically when ``osr > 1``; otherwise a symmetric ``±filterwidth``
      band is used (``oct_dofft.m``-style three bins when ``filterwidth=1``).
    * *exclude_harmonics* — if ``True``, SNR excludes harmonic lobes from
      the noise sum; SNDR keeps them as noise. If ``None``, defaults to
      ``False`` when ``osr > 1``, else ``True``.

    *fmin* / *fmax* < 0 mean full band along that edge; they still interact
    with the OSR in-band cap for the noise integration range.
    """
    nan3 = DynamicParametersResult(
        float("nan"), float("nan"), float("nan"), float("nan"), -1)

    y = np.asarray(y, dtype=np.float64)
    y = y[np.isfinite(y)]
    n = len(y)
    if n < 16 or fs <= 0:
        return nan3

    auto_f0 = f0 is None or (isinstance(f0, float) and not np.isfinite(f0))
    if not auto_f0 and f0 is not None and float(f0) <= 0:
        auto_f0 = True

    if exclude_harmonics is None:
        exclude_harmonics = not (osr > 1.0)
    if sigma_delta_lobe is None:
        sigma_delta_lobe = osr > 1.0

    if remove_dc:
        y = y - np.mean(y)

    w = np.hanning(n)
    yw = y * w
    Y = np.fft.rfft(yw)
    P = np.abs(Y) ** 2
    P = np.maximum(P, 1e-300)
    n_bins = len(P)

    def bin_for_freq(f: float) -> int:
        return int(np.floor(f / fs * n))

    if auto_f0:
        k_lo = int(np.clip(auto_f0_guard_lo, 1, n_bins - 2))
        k_hi = int(np.clip(n_bins - auto_f0_guard_hi, k_lo + 1, n_bins))
        if k_hi <= k_lo:
            return nan3
        rel = int(np.argmax(P[k_lo:k_hi]))
        k_sig = k_lo + rel
        f0_hz = float(k_sig * fs / n)
    else:
        assert f0 is not None
        f0_hz = float(f0)
        k_sig = int(np.clip(bin_for_freq(f0_hz), 0, n_bins - 1))

    sig_mask = _signal_bins_mask(
        k_sig, n_bins, filterwidth=filterwidth,
        sigma_delta_lobe=bool(sigma_delta_lobe))

    fmini = 1 if fmin < 0 else max(1, bin_for_freq(fmin))
    fmaxi = n_bins if fmax < 0 else bin_for_freq(fmax)
    fmini = max(0, min(fmini, n_bins - 1))
    fmaxi = max(fmini + 1, min(fmaxi, n_bins))

    osr = float(osr)
    if not np.isfinite(osr) or osr < 1.0:
        osr = 1.0
    #- In-band limit (``dofftsd.m``: ``1 : length(y)/OSR`` on one-sided spectrum).
    k_noise_end_excl = min(fmaxi, max(fmini + 1, int(np.floor(n_bins / osr)) + 1))

    harmonic_centers: List[int] = []
    if exclude_harmonics:
        for h in range(2, max_harmonics + 1):
            kh = bin_for_freq(f0_hz * h)
            if 0 <= kh < n_bins:
                harmonic_centers.append(kh)

    def in_lobe(k: int, center: int, width: int) -> bool:
        return abs(k - center) <= width

    w_h = int(max(0, filterwidth))
    psig = float(np.sum(P[sig_mask]))

    if psig <= 1e-300:
        return nan3

    pnoise_snr = 0.0
    pnoise_sndr = 0.0
    for k in range(fmini, k_noise_end_excl):
        if sig_mask[k]:
            continue
        in_harm = exclude_harmonics and any(
            in_lobe(k, hb, w_h) for hb in harmonic_centers)
        if not in_harm:
            pnoise_snr += P[k]
        pnoise_sndr += P[k]

    if pnoise_snr <= 1e-300 or pnoise_sndr <= 1e-300:
        return nan3

    snr_db = 10.0 * np.log10(psig / pnoise_snr)
    sndr_db = 10.0 * np.log10(psig / pnoise_sndr)
    enob = (sndr_db - 1.76) / 6.02
    return DynamicParametersResult(
        snr_db=float(snr_db), sndr_db=float(sndr_db), enob=float(enob),
        f0_hz=f0_hz, signal_bin=int(k_sig))


def _adc_uniform_series_and_fs(
        x: np.ndarray,
        y: np.ndarray,
        *,
        auto_resample: bool,
        irregularity_threshold: float) -> Tuple[np.ndarray, np.ndarray, float]:
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if len(y) < 2:
        raise ValueError("need at least 2 samples")
    irr = time_base_irregularity(x)
    if auto_resample and irr > irregularity_threshold:
        res = resample_uniform(x, y)
        x, y = res.x_new, res.y_new
        fs = float(res.fs)
    else:
        dt = float(np.mean(np.diff(x)))
        if dt <= 0 or not np.isfinite(dt):
            raise ValueError("non-positive or invalid mean(dt)")
        fs = 1.0 / dt
    return x, y, fs


@dataclass(frozen=True)
class HarmonicLevel:
    """Integrated harmonic lobe power relative to the fundamental."""

    order: int
    #- 10·log10(P_harm / P_fund); negative when below the tone.
    power_dbc: float
    #- 10·log10(P_peak_bin / P_fund) within the lobe (single-bin floor).
    peak_dbc: float


@dataclass(frozen=True)
class AdcPsdResult:
    """Windowed one-sided spectrum with ADC-style metrics."""

    freq_hz: np.ndarray
    #- Spectrum in dB (DC bin omitted).
    #- dBc by default — relative to the fundamental lobe power.
    #- dBFS when ``dbfs_amplitude`` was supplied — relative to a full-scale sine.
    psd_db: np.ndarray
    psd_unit: str           # ``"dBc"`` or ``"dBFS"``
    fs: float
    n_fft: int
    fundamental_hz: float
    signal_bin: int
    dynamic: DynamicParametersResult
    #- IEEE SFDR: fundamental vs largest spur (harmonics included). dBc.
    sfdr_db: float
    #- Fundamental level vs full-scale, ``nan`` when ``dbfs_amplitude`` is unset.
    signal_dbfs: float
    harmonics: Tuple[HarmonicLevel, ...]
    #- Frequencies for vertical markers (fundamental + harmonics < Nyquist).
    marker_freqs_hz: Tuple[float, ...]


def adc_psd_analysis(
        x: np.ndarray,
        y: np.ndarray,
        *,
        fs: Optional[float] = None,
        f0: Optional[float] = None,
        remove_dc: bool = True,
        #- Hann main lobe is ~3 bins for a coherent tone, but real-world
        #- ADC captures are non-coherent and the fundamental smears further.
        #- Default ``±3`` (7 bins) covers the main lobe + first side lobes
        #- so SNDR/SFDR don't lose energy to "leakage spurs".
        filterwidth: int = 3,
        #- Half-width of the **fundamental** lobe in bins. Falls back to
        #- ``filterwidth`` when ``None``. Use this when the fundamental is
        #- noticeably wider than the harmonics (e.g. jittered captures).
        fund_filterwidth: Optional[int] = None,
        max_harmonics: int = 5,
        osr: float = 1.0,
        exclude_harmonics: bool = True,
        #- Peak full-scale sine amplitude (same units as *y*). When set,
        #- the spectrum is plotted in **dBFS** and ``signal_dbfs`` reports
        #- where the captured fundamental sits relative to FS.
        dbfs_amplitude: Optional[float] = None,
        auto_resample: bool = True,
        irregularity_threshold: float = 0.2) -> AdcPsdResult:
    """Hanning-windowed *rfft* spectrum with ADC-style metrics.

    The fundamental is auto-detected as the strongest bin (with the same
    DC/Nyquist guards as :func:`dynamic_parameters`) unless *f0* is a
    positive finite frequency.

    Spectrum units:

    * **dBc** by default — every bin is normalised to the integrated
      fundamental lobe power. The fundamental peak then sits near 0 dBc.
    * **dBFS** when *dbfs_amplitude* (peak FS amplitude *A_fs*) is given.
      Reference energy is ``E_ref = (A_fs²/2)·Σ w[n]²`` so a coherent
      full-scale sine peaks just below 0 dBFS (Hann lobe spread).

    * **SFDR** — ``10·log10(P_fund_lobe / P_largest_spur)`` (always dBc
      regardless of plot units). Harmonics **count as spurs** (IEEE def.).
    * **Harmonics** — for each *h* ≥ 2, integrated power in ``±filterwidth``
      bins around the bin of *h·f₀*, reported as dBc vs the fundamental.
    * **signal_dbfs** — fundamental level vs full-scale (``nan`` when
      *dbfs_amplitude* is unset).

    SNDR/SNR/ENOB reuse :func:`dynamic_parameters` on the same *y* and *fs*.
    """
    x, y, fs_inf = _adc_uniform_series_and_fs(
        x, y, auto_resample=auto_resample,
        irregularity_threshold=irregularity_threshold)
    fs_used = float(fs) if (fs is not None and np.isfinite(fs) and fs > 0
                            ) else fs_inf

    y = np.asarray(y, dtype=np.float64)
    if remove_dc:
        y = y - np.mean(y)

    n = len(y)
    w = np.hanning(n)
    yw = y * w
    Y = np.fft.rfft(yw)
    #- Parseval-consistent one-sided bin power: Σ p_bin = Σ (yw)².
    P = np.abs(Y) ** 2
    if n % 2 == 0:
        P[1:-1] *= 2.0
    else:
        P[1:] *= 2.0
    P = P / float(n)
    P = np.maximum(P, 1e-300)
    n_bins = len(P)
    freqs_all = np.fft.rfftfreq(n, d=1.0 / fs_used)

    def bin_for_freq(fhz: float) -> int:
        return int(np.floor(fhz / fs_used * n))

    auto_f0 = f0 is None or (isinstance(f0, float) and not np.isfinite(f0))
    if not auto_f0 and f0 is not None and float(f0) <= 0:
        auto_f0 = True

    guard_lo, guard_hi = 2, 10
    if auto_f0:
        k_lo = int(np.clip(guard_lo, 1, n_bins - 2))
        k_hi = int(np.clip(n_bins - guard_hi, k_lo + 1, n_bins))
        if k_hi <= k_lo:
            raise ValueError("auto fundamental: not enough FFT bins")
        rel = int(np.argmax(P[k_lo:k_hi]))
        k_sig = k_lo + rel
        f0_hz = float(k_sig * fs_used / n)
    else:
        assert f0 is not None
        f0_hz = float(f0)
        k_sig = int(np.clip(bin_for_freq(f0_hz), 0, n_bins - 1))

    fund_w = int(filterwidth if fund_filterwidth is None
                 else fund_filterwidth)
    sigma_delta_lobe = osr > 1.0
    sig_mask = _signal_bins_mask(
        k_sig, n_bins, filterwidth=fund_w,
        sigma_delta_lobe=sigma_delta_lobe)
    ps_fund = float(np.sum(P[sig_mask]))
    if ps_fund <= 1e-300:
        raise ValueError("fundamental lobe has no power")

    if dbfs_amplitude is not None and np.isfinite(dbfs_amplitude):
        if float(dbfs_amplitude) <= 0:
            raise ValueError("dbfs_amplitude must be positive")
        A_fs = float(dbfs_amplitude)
        ref_energy = 0.5 * (A_fs ** 2) * float(np.sum(w * w))
        ref_energy = max(ref_energy, 1e-300)
        psd_db = 10.0 * np.log10(P[1:n_bins] / ref_energy)
        psd_unit = "dBFS"
        signal_dbfs = float(10.0 * np.log10(ps_fund / ref_energy))
    else:
        psd_db = 10.0 * np.log10(P[1:n_bins] / ps_fund)
        psd_unit = "dBc"
        signal_dbfs = float("nan")
    freq_hz = freqs_all[1:n_bins]

    w_h = int(max(0, filterwidth))

    harmonics_list: List[HarmonicLevel] = []
    h = 2
    while h <= max_harmonics:
        f_h = f0_hz * h
        if f_h >= 0.5 * fs_used * (1.0 - 1e-9):
            break
        kh = bin_for_freq(f_h)
        if kh < 1:
            h += 1
            continue
        if kh >= n_bins:
            break
        hmask = np.zeros(n_bins, dtype=bool)
        for dk in range(-w_h, w_h + 1):
            kk = kh + dk
            if 0 <= kk < n_bins:
                hmask[kk] = True
        p_h = float(np.sum(P[hmask]))
        peak_in_lobe = float(np.max(P[hmask])) if np.any(hmask) else 1e-300
        harmonics_list.append(HarmonicLevel(
            order=h,
            power_dbc=float(10.0 * np.log10(p_h / ps_fund)),
            peak_dbc=float(10.0 * np.log10(peak_in_lobe / ps_fund)),
        ))
        h += 1

    #- IEEE SFDR: largest spur **including harmonics**. Only the
    #- fundamental lobe (and DC) are excluded so a strong harmonic
    #- properly dominates the SFDR result.
    spur_mask = np.ones(n_bins, dtype=bool)
    spur_mask[0] = False
    spur_mask &= ~sig_mask
    if np.any(spur_mask):
        p_spur = float(np.max(P[spur_mask]))
        sfdr_db = float(10.0 * np.log10(ps_fund / p_spur))
    else:
        sfdr_db = float("nan")

    marker_freqs: List[float] = [f0_hz]
    h = 2
    while h <= max_harmonics:
        f_h = f0_hz * h
        if f_h >= 0.5 * fs_used * (1.0 - 1e-9):
            break
        marker_freqs.append(f_h)
        h += 1

    dyn = dynamic_parameters(
        y, fs_used, f0_hz, remove_dc=False, filterwidth=filterwidth,
        max_harmonics=max_harmonics, osr=osr,
        exclude_harmonics=exclude_harmonics,
        sigma_delta_lobe=sigma_delta_lobe if osr > 1.0 else None)

    return AdcPsdResult(
        freq_hz=freq_hz,
        psd_db=psd_db,
        psd_unit=psd_unit,
        fs=fs_used,
        n_fft=n,
        fundamental_hz=f0_hz,
        signal_bin=int(k_sig),
        dynamic=dyn,
        sfdr_db=sfdr_db,
        signal_dbfs=signal_dbfs,
        harmonics=tuple(harmonics_list),
        marker_freqs_hz=tuple(marker_freqs),
    )


@dataclass(frozen=True)
class LinearFitResult:
    slope: float
    intercept: float
    r: float
    r_squared: float


def linear_fit(x: np.ndarray, y: np.ndarray) -> LinearFitResult:
    """Least-squares line *y = intercept + slope*x* and Pearson *r*."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if len(x) < 2:
        return LinearFitResult(
            float("nan"), float("nan"), float("nan"), float("nan"))
    slope, intercept = np.polyfit(x, y, 1)
    r_mat = np.corrcoef(x, y)
    r = float(r_mat[0, 1]) if r_mat.shape == (2, 2) else float("nan")
    if not np.isfinite(r):
        r = 0.0
    return LinearFitResult(
        slope=float(slope), intercept=float(intercept),
        r=r, r_squared=float(r * r))


def difference(
        y_a: np.ndarray,
        y_b: np.ndarray,
        *,
        align: str = "min_length") -> np.ndarray:
    """Element-wise *y_a* − *y_b* after trimming to common length."""
    if align != "min_length":
        raise ValueError("only align='min_length' is supported")
    a = np.asarray(y_a, dtype=np.float64)
    b = np.asarray(y_b, dtype=np.float64)
    n = min(len(a), len(b))
    if n == 0:
        return np.array([], dtype=np.float64)
    return a[:n] - b[:n]


@dataclass
class AnalysisRunResult:
    """Structured + text output from :func:`run_analysis_steps`."""

    lines: List[str]
    summary_text: str

    @property
    def text(self) -> str:
        return self.summary_text


def run_analysis_steps(
        df,
        steps: Sequence[Mapping[str, Any]],
        *,
        x_column: Optional[str] = None) -> AnalysisRunResult:
    """Execute *steps* on a pandas DataFrame; return printable summary.

    Each step is a dict with ``type`` and type-specific keys.

    Supported ``type`` values:

    - ``dynamic_parameters``: ``y_column``, ``fs``; ``f0`` optional
      (omit, ``null``, or ``<= 0`` → auto peak bin). Optional: ``osr``
      (``dofftsd.m`` in-band noise), ``exclude_harmonics``, ``sigma_delta_lobe``,
      ``fmin``, ``fmax``, ``remove_dc``.
    - ``rms``: ``column``.
    """
    import pandas as pd

    lines: List[str] = []
    for i, step in enumerate(steps):
        st = step.get("type")
        if st == "dynamic_parameters":
            yc = step["y_column"]
            fs = float(step["fs"])
            f0_raw = step.get("f0")
            if f0_raw is None:
                f0_opt = None
            else:
                f0_opt = float(f0_raw)
                if f0_opt <= 0 or not np.isfinite(f0_opt):
                    f0_opt = None
            fmin = float(step.get("fmin", -1))
            fmax = float(step.get("fmax", -1))
            rem = bool(step.get("remove_dc", True))
            osr = float(step.get("osr", 1.0))
            excl = step.get("exclude_harmonics", None)
            if excl is not None:
                excl = bool(excl)
            sd_lobe = step.get("sigma_delta_lobe", None)
            if sd_lobe is not None:
                sd_lobe = bool(sd_lobe)
            y = df[yc].to_numpy(dtype=np.float64)
            if x_column and x_column in df.columns:
                _ = df[x_column].to_numpy()
            res = dynamic_parameters(
                y, fs, f0_opt, fmin=fmin, fmax=fmax, remove_dc=rem,
                osr=osr, exclude_harmonics=excl, sigma_delta_lobe=sd_lobe)
            f0_note = (
                "  f0=%.6g Hz" % res.f0_hz
                if np.isfinite(res.f0_hz) else "")
            lines.append(
                "[%s]%s  SNR=%.2f dB  SNDR=%.2f dB  ENOB=%.2f bits"
                % (yc, f0_note, res.snr_db, res.sndr_db, res.enob))
        elif st == "rms":
            col = step["column"]
            val = rms(df[col].to_numpy())
            lines.append("[%s] RMS=%.6g" % (col, val))
        else:
            lines.append("(unknown step type %r at index %d)" % (st, i))

    summary = "\n".join(lines)
    return AnalysisRunResult(lines=lines, summary_text=summary)


def preprocess_dataframe(df, preprocess: Optional[Mapping[str, Any]]):
    """Apply *preprocess* block from a pivot spec (mutates a copy)."""
    import pandas as pd

    out = df.copy()
    if not preprocess:
        return out
    tc = preprocess.get("twos_complement")
    if tc:
        w = int(tc["width_bits"])
        cols = tc.get("columns")
        if cols is None:
            cols = [c for c in out.columns
                    if str(c).lower() not in ("time", "time [s]")]
        for c in cols:
            if c in out.columns:
                out[c] = decode_twos_complement(
                    out[c].to_numpy(), w)
    return out
