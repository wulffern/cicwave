---
layout: page
title:  url-sources
math: true
---

* TOC
{:toc }

## Description

`cicwave` can load data straight from an `http://` or `https://` URL —
a hosted CSV, a REST API response, anything pandas can parse. This
works anywhere a local file path works: as a positional argument, in a
[session file](/cicwave/sessions), or with [`--pivot`](/cicwave/pivot).

```bash
cicwave https://raw.githubusercontent.com/owid/co2-data/master/owid-co2-data.csv
```

See [Examples](/cicwave/examples#real-world-data-from-a-url) for two
worked examples using public climate and health datasets.

## Format detection

The format is resolved in this order:

1. `--format` (see below), if given
2. The URL's own path extension (query strings are ignored), e.g.
   `.csv` in `https://example.com/data.csv?raw=true`
3. The response's `Content-Type` header, for extension-less REST
   endpoints

If none of those resolve, `cicwave` raises an error asking for
`--format` explicitly.

### `--format`

Forces the format for a URL lacking a recognizable extension — the
common case for REST API endpoints:

```bash
cicwave https://api.example.com/v1/measurements --format json
```

`--format` is ignored for local files, which are always dispatched by
extension.

## Supported formats

| Format | Extension / `--format` |
|--------|------------------------|
| CSV | `csv` |
| TSV | `tsv` |
| JSON | `json` |
| Excel | `xlsx`, `xls`, `ods` |
| Parquet | `parquet` |
| Feather | `feather` |
| HTML (first table) | `html` |
| XML | `xml` |
| Fixed-width | `fwf` |

`--csv-sep` and `--csv-comment` apply to remote CSV/TSV the same way
they do to local files — see [Usage](/cicwave/usage#csv-options).

**Not supported for URL sources**: ngspice `.raw`, VCD, `.iqvsa`, `.u32`,
Xyce `.prn`, whitespace formats (`.dat`/`.spe`/`.cou`/`.chi`), `.npz`,
STDF (`.stdf`/`.stdf.gz`), and the statistical formats (Stata/SAS/SPSS).
These are lab-instrument or simulator output formats that aren't
realistic REST/API payloads; download the file locally first if you
need to view one.

**Blocked for URL sources**: Pickle (`.pkl`/`.pickle`) and HDF5
(`.h5`/`.hdf5`). Loading a pickle deserializes arbitrary Python objects,
which is a code-execution risk for data pulled from a URL you don't
control — `cicwave` refuses these regardless of `--format`. Download
and inspect the file locally first if you trust the source.

## Fetch behavior

- A URL source is fetched once and treated as a static snapshot for
  the rest of the session — there's no cheap way to check "has this
  changed" over HTTP the way there is for a local file's mtime, and
  re-fetching on every GUI refresh would be surprising (slow, and
  could hammer a rate-limited API). Re-open the URL, or restart
  `cicwave`, to get a fresh copy.
- Downloads are capped at 200 MB and a 30 second timeout, so a
  misbehaving or huge endpoint fails fast with a clear error instead
  of hanging or exhausting memory.
- Network and HTTP errors (DNS failure, timeout, 404, ...) produce a
  clear one-line error — `error: failed to load <url>: ...` on the
  CLI, a dialog in the GUI — instead of a raw exception traceback.
- Any hostname that resolves to a **link-local address**
  (`169.254.0.0/16` — this is where AWS/GCP/Azure/OpenStack all serve
  cloud instance-metadata, including credentials) is refused before a
  request is made. This matters because a URL can arrive indirectly,
  via a [session file](/cicwave/sessions) or `--pivot` spec someone
  else wrote — opening one shouldn't be able to make your machine read
  its own cloud credentials. Ordinary internal REST APIs on RFC1918
  private ranges (`10.0.0.0/8`, `192.168.0.0/16`, ...) are unaffected.

## Pivoting a URL source

Long-format public datasets (one row per country/date/measurement) are
a natural fit for [`--pivot`](/cicwave/pivot) — reshape them into one
wave per series without downloading and reshaping the file yourself:

```bash
cicwave https://raw.githubusercontent.com/owid/co2-data/master/owid-co2-data.csv \
    --pivot spec.yaml --pivot-info
```

```yaml
# spec.yaml
index: country     # each unique country becomes a wave
columns: year       # x-axis
values: co2         # y-axis
```

See [Examples](/cicwave/examples#real-world-data-from-a-url) for the
full worked example with plots.
