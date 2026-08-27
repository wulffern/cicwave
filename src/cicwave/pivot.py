#!/usr/bin/env python3

"""
Pivot spec loader for cicwave.

A pivot spec (YAML or JSON) defines how to reshape a flat DataFrame
into a wide format suitable for waveform plotting.

Spec format::

    index: Parameter       # split by — each unique value becomes a wave
    columns: Frequency     # (optional) x-axis; rows with None/NaN dropped
    values: Measurement    # y-axis values
    conditions:            # further split waves by these; each unique combo
      - Temp               # of (index x conditions) becomes its own wave
      - Config
    aliases:               # optional: short names for long condition values
      Config:
        c0: "LV"

A spec can also carry a ``source:`` block, in which case it fetches the
flat frame from a JSON REST API instead of taking a data file; see
:mod:`cicwave.apisource`.

Optional post-processing and metrics (consumed by the CLI when
``--pivot`` is used; see :mod:`cicwave.analysis`)::

    analysis:
      preprocess:          # applied to the flat frame before pivoting
        twos_complement:
          width_bits: 10
          columns: [ADC_RAW]   # omit to decode all non-time columns
      steps:                 # run on the pivoted wide frame (headless / export)
        - type: rms
          column: "v(out)"
        - type: dynamic_parameters
          y_column: "v(out)"
          fs: 1e6
          f0: 100e3          # omit or <= 0 for auto (peak bin, dofft-style)
          osr: 8              # optional; in-band noise like dofftsd.m

Use ``--pivot-info`` to discover the cN keys for each condition column.
"""

import json
import logging
import yaml
import pandas as pd

logger = logging.getLogger("cicwave")


def load_spec(path):
    with open(path) as f:
        if path.endswith('.json'):
            return json.load(f)
        return yaml.safe_load(f)


def _is_json_kv_array(val):
    if not isinstance(val, str):
        return False
    val = val.strip()
    if not (val.startswith('[') and val.endswith(']')):
        return False
    try:
        arr = json.loads(val)
        return (isinstance(arr, list) and len(arr) > 0
                and all(isinstance(d, dict) and 'value' in d for d in arr))
    except (json.JSONDecodeError, TypeError):
        return False


def _is_kv_semicolon(val):
    """Detect 'KEY=VAL;KEY=VAL;...' patterns."""
    if not isinstance(val, str):
        return False
    parts = [p for p in val.strip().split(';') if p]
    return len(parts) >= 2 and all('=' in p for p in parts)


def _is_structured_value(val):
    s = str(val)
    return _is_json_kv_array(s) or _is_kv_semicolon(s)


def _shorten_value(val):
    """Auto-shorten a condition value for column names."""
    try:
        if isinstance(val, float) and val == int(val):
            return str(int(val))
    except (ValueError, OverflowError):
        pass
    s = str(val)
    if _is_json_kv_array(s):
        arr = json.loads(s)
        return '_'.join(str(d.get('value', '')) for d in arr)
    if _is_kv_semicolon(s):
        parts = [p.split('=', 1)[1] for p in s.strip().split(';') if p and '=' in p]
        return '_'.join(parts)
    return s


def _condition_prefix(name):
    return name[0].upper()


def _build_alias_map(unique_vals, aliases_section):
    """Map cN keys to user-defined aliases, falling back to auto-shorten."""
    indexed = {("c%d" % i): v for i, v in enumerate(unique_vals)}
    result = {}
    for ckey, orig_val in indexed.items():
        if aliases_section and ckey in aliases_section:
            result[orig_val] = str(aliases_section[ckey])
        else:
            result[orig_val] = _shorten_value(orig_val)
    return result


def pivot_info(df, spec):
    """Return a human-readable summary of pivot dimensions and unique values."""
    index_col = spec['index']
    columns_col = spec.get('columns')
    values_col = spec['values']
    conditions = spec.get('conditions', [])

    available = set(df.columns)
    lines = []

    lines.append("available columns: %s" % ', '.join(sorted(available)))
    lines.append("")

    if index_col in available:
        lines.append("index: %s (%d unique)" % (index_col, df[index_col].nunique()))
        for v in sorted(df[index_col].dropna().unique(), key=str):
            lines.append("  %s" % v)
    else:
        lines.append("index: %s  ** NOT FOUND **" % index_col)

    if columns_col:
        lines.append("")
        if columns_col in available:
            lines.append("columns: %s (%d unique)" % (
                columns_col, df[columns_col].nunique()))
            for v in sorted(df[columns_col].unique(), key=lambda x: str(x)):
                lines.append("  %s" % v)
        else:
            lines.append("columns: %s  ** NOT FOUND (will be skipped) **" % columns_col)

    lines.append("")
    if values_col in available:
        lines.append("values: %s" % values_col)
    else:
        lines.append("values: %s  ** NOT FOUND **" % values_col)

    if conditions:
        lines.append("")
        lines.append("conditions:")
        json_cols = []
        for cond in conditions:
            unique_vals = sorted(df[cond].dropna().unique(), key=str)
            lines.append("")
            lines.append("  %s (%d unique)" % (cond, len(unique_vals)))
            has_json = any(_is_structured_value(v) for v in unique_vals)
            for i, v in enumerate(unique_vals):
                if has_json:
                    lines.append("    c%d: %s" % (i, _shorten_value(v)))
                else:
                    lines.append("    %s" % _shorten_value(v))
            if has_json:
                json_cols.append(cond)

        if json_cols:
            lines.append("")
            lines.append("Suggested aliases for your pivot spec:")
            lines.append("aliases:")
            for cond in json_cols:
                lines.append("  %s:" % cond)
                unique_vals = sorted(df[cond].dropna().unique(), key=str)
                for i, v in enumerate(unique_vals):
                    short = _shorten_value(v)
                    lines.append('    c%d: "%s"' % (i, short))

    return "\n".join(lines)


def apply_pivot(df, spec):
    """Reshape *df* according to *spec*, return a wide DataFrame.

    ``index`` values become wave names (one column per unique value).
    ``columns`` (if present) becomes the x-axis — rows with None/NaN
    in that column are dropped.  ``conditions`` further split waves so
    each unique (index × conditions) combo is its own wave.
    """
    index_col = spec['index']
    columns_col = spec.get('columns')
    values_col = spec['values']
    conditions = spec.get('conditions', [])
    aliases = spec.get('aliases', {})

    available = set(df.columns)
    missing = []
    if index_col not in available:
        missing.append("index '%s'" % index_col)
    if values_col not in available:
        missing.append("values '%s'" % values_col)
    for cond in conditions:
        if cond not in available:
            missing.append("condition '%s'" % cond)
    if missing:
        raise KeyError(
            "Pivot spec references missing columns: %s\n"
            "Available columns: %s" % (
                ', '.join(missing), ', '.join(sorted(available))))

    has_xaxis = columns_col and columns_col in available

    alias_maps = {}
    for cond in conditions:
        unique_vals = sorted(df[cond].dropna().unique(), key=str)
        alias_maps[cond] = _build_alias_map(unique_vals, aliases.get(cond))

    keep = [index_col, values_col] + conditions
    if has_xaxis:
        keep.insert(1, columns_col)
    sub = df[keep].copy()

    if has_xaxis:
        sub = sub.dropna(subset=[columns_col])

    wave_col = sub[index_col].astype(str)
    for cond in conditions:
        amap = alias_maps.get(cond, {})
        labels = sub[cond].map(
            lambda v: amap.get(v, _shorten_value(v)))
        wave_col = wave_col + '_' + _condition_prefix(cond) + labels.astype(str)
    sub['_wave'] = wave_col

    #- pivot_table below uses aggfunc='mean', which silently averages
    #- together any rows that land in the same (wave, x) cell. That's
    #- correct when it's intentional (e.g. Monte-Carlo repeats), but
    #- usually means the spec is missing a `conditions` column that
    #- actually varies. Warn so an under-specified spec doesn't look
    #- like clean data.
    dedup_cols = ['_wave', columns_col] if has_xaxis else ['_wave']
    n_dupes = int(sub.duplicated(subset=dedup_cols).sum())
    if n_dupes:
        logger.warning(
            "pivot: %d row(s) share the same wave%s and were averaged "
            "together (aggfunc='mean'); add a 'conditions' column if "
            "that wasn't intended",
            n_dupes, " and x-value" if has_xaxis else "")

    #- observed=True is explicit (rather than relying on the pandas
    #- default, which varies by version) so a categorical index/columns
    #- column -- e.g. STDF's part_id/test_txt -- reshapes to only the
    #- combinations actually present in `sub`, not the full cross
    #- product of every category the dtype remembers (older pandas
    #- defaults to including all of them, which silently balloons the
    #- result with all-NaN rows/columns for a filtered-down `sub`).
    if has_xaxis:
        result = sub.pivot_table(
            index=columns_col,
            columns='_wave',
            values=values_col,
            aggfunc='mean',
            observed=True,
        )
        result.columns.name = None
        result = result.reset_index()
        # Try to make x-axis numeric
        try:
            result[columns_col] = pd.to_numeric(result[columns_col])
            result = result.sort_values(columns_col)
        except (ValueError, TypeError):
            pass
    else:
        result = sub.pivot_table(
            index='_wave',
            columns=index_col,
            values=values_col,
            aggfunc='mean',
            observed=True,
        )
        result.columns.name = None
        result = result.reset_index().rename(columns={'_wave': 'condition'})

    return result
