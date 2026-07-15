---
layout: page
title:  pivot
math: true
---

* TOC
{:toc }

## Description

Pivot reshapes a flat/long-format table into wide format suitable for
waveform plotting. This is useful when simulation results are stored as
one-row-per-measurement (e.g. parameter sweeps, Monte Carlo results).

## Usage

```bash
cicwave results.csv --pivot spec.yaml
```

Inspect the available pivot dimensions first:

```bash
cicwave results.csv --pivot spec.yaml --pivot-info
```

## Pivot spec format

A pivot spec is a YAML (or JSON) file with the following keys:

```yaml
index: Parameter         # column whose unique values become separate waves
columns: Frequency       # (optional) column used as x-axis
values: Measurement      # column containing the y-axis values
conditions:               # (optional) further split waves by these columns
  - Temp
  - Config
aliases:                  # (optional) short names for condition values
  Config:
    c0: "LV"
    c1: "HV"
```

| Key | Required | Description |
|-----|----------|-------------|
| `index` | yes | Column to split on — each unique value becomes a wave (e.g. `Parameter`) |
| `columns` | no | Column to use as the x-axis. Rows with NaN in this column are dropped. If omitted the result is a bar-style categorical plot |
| `values` | yes | Column containing the measurement values (y-axis) |
| `conditions` | no | List of additional columns to split by. Each unique combination of (`index` × conditions) becomes its own wave. Wave names are formed as `{index}_{C}{condition_value}` |
| `aliases` | no | Dictionary of short names for condition values. Keyed by condition column name, then `c0`, `c1`, ... for each unique value in sorted order |

Condition values that look like a JSON array of `{"value": ...}` objects,
or a `KEY=VAL;KEY=VAL` string, are auto-shortened for wave names. Use
`--pivot-info` to see the suggested `aliases` block for those columns.

## Example

Given a CSV with amplifier gain and phase measured across frequency at
three temperatures (`tests/docs/pivot_data.csv`), and a pivot spec:

<!--cat:
file: pivot_spec.yaml
language: yaml
-->

The `--pivot-info` flag shows the dimensions:

<!--run_output:
run: cicwave pivot_data.csv --pivot pivot_spec.yaml --pivot-info
-->

Then plot the pivoted data — see [Examples](/cicwave/examples#pivoted-data)
for the resulting plot:

```bash
cicwave pivot_data.csv --pivot pivot_spec.yaml
```

## Preprocessing and headless analysis

A pivot spec can also carry an `analysis` block, consumed by the CLI when
`--pivot` is used together with `--export`. `preprocess` runs against the
flat frame before pivoting; `steps` run against the pivoted wide frame
and print a summary (also shown in the exported plot when applicable).

```yaml
index: Parameter
columns: Sample
values: Value

analysis:
  preprocess:
    twos_complement:
      width_bits: 10
      columns: [ADC_RAW]   # omit to decode all non-time columns

  steps:
    - type: rms
      column: "v(out)"
    - type: dynamic_parameters
      y_column: "v(out)"
      fs: 1e6
      f0: 100e3            # omit or <= 0 for auto (peak-bin detection)
      osr: 8                # optional; in-band noise, dofftsd.m-style
```

### `preprocess`

| Key | Description |
|-----|-------------|
| `twos_complement.width_bits` | Bit width to decode as signed two's complement |
| `twos_complement.columns` | Columns to decode (default: all except time-like columns) |

### `steps`

| `type` | Keys | Description |
|--------|------|-------------|
| `rms` | `column` | Prints the RMS value of `column` |
| `dynamic_parameters` | `y_column`, `fs`; optional `f0`, `fmin`, `fmax`, `remove_dc`, `osr`, `exclude_harmonics`, `sigma_delta_lobe` | Computes SNR, SNDR, ENOB from an FFT of `y_column` |
| `adc_psd` | `y_column`, `fs`; optional `f0`, `max_harmonics`, `osr`, `exclude_harmonics`, `dbfs_amplitude`, `filterwidth`, `fund_filterwidth`, `remove_dc` | Same backend as the GUI's [ADC PSD dialog](/cicwave/analysis#adc-psd-dialog): SNR/SNDR/ENOB/SFDR plus a per-harmonic breakdown |
| `linear_fit` | `y_column`; optional `x_column` (defaults to the spec's `columns` x-axis) | Least-squares slope/intercept/r/r² of `y_column` vs `x_column` |
| `difference` | `a_column`, `b_column` | Element-wise `a - b` (trimmed to the shorter length); reports mean/RMS/max-abs |

Use `--pivot-info` and the [analysis dialogs](/cicwave/analysis) in the GUI to
work out sensible `fs`/`f0` values before scripting a headless export.

## Headless data export

`--pivot` combined with [`--export-data`](/cicwave/usage#exporting-without-the-gui)
writes the pivoted (and preprocessed) DataFrame itself — not just a plot
image — so a CI job can pull the reshaped numbers directly:

```bash
cicwave pivot_data.csv --pivot pivot_spec.yaml --export-data pivoted.csv
```
