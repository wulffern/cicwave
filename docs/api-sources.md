---
layout: page
title:  api-sources
math: true
---

* TOC
{:toc }

## Description

A [pivot spec](/cicwave/pivot) can carry a `source:` block describing
how to build its table from a JSON HTTP API — FastAPI, Flask, whatever
your lab or CI service runs — instead of from a file on disk. The spec
then needs no data file at all:

```bash
cicwave measurements.yaml
```

In the GUI, a spec opens like any data file — **File → Open** it or drag
it onto the window, and cicwave fetches it.

This is for the shape a REST service usually has, which a plain
[URL source](/cicwave/url-sources) cannot express:

- the rows you want are **nested inside an envelope**
  (`{"count": 40, "rows": [...]}`), not the top-level JSON, and
- they are **spread over several endpoints** — one request lists what
  exists, and a second has to be issued once per item to get the actual
  sweep.

If your endpoint already returns a flat table (CSV, or a JSON array of
objects), you don't need any of this — just pass the URL.

Everything a `source:` block contains is data, not code: there is no
expression language to evaluate, so opening someone else's spec fetches
URLs but cannot run anything.

## A single request

The short form, for one endpoint whose records sit under a key:

```yaml
source:
  url: http://api.example.com/v1/readings
  records: rows           # dot-path to the list inside the response

index: station
columns: timestamp
values: temperature
```

## Several requests

The general form. Each entry in `requests` is one GET; a later request
can be issued once per row of an earlier one with `for_each`, and
`{field}` in its `path` or `params` is substituted from that row:

```yaml
source:
  base_url: http://api.example.com
  requests:
    - name: series               # so a later request can refer to it
      path: /v1/series
      params: {kind: temperature, limit: 500}
      records: rows
      where: {unit: degC}        # keep only matching records

    - path: /v1/series/{id}/points    # {id} comes from a `series` row
      for_each: series
      params: {unit: "{unit}"}
      records: points
      merge: [station, setup]    # carry these parent fields onto each row
      headers_as_columns:
        generation: X-Data-Generation

  require_consistent_headers: [X-Data-Generation]
```

The rows of the **last** request become the table. Identical URLs are
fetched once, however many parent rows ask for them.

## Reference

### `source`

| Key | Description |
|-----|-------------|
| `requests` | List of requests to issue, in order. The last one produces the table |
| `base_url` | Prefix for each request's relative `path` |
| `headers` | Extra request headers. `${VAR}` is expanded from the environment — see [Secrets](#secrets) |
| `timeout` | Per-request timeout in seconds (default 30) |
| `max_requests` | Cap on total requests (default 200) — see [Request budget](#request-budget) |
| `require_consistent_headers` | Response headers that must not change mid-pull — see [Provenance](#provenance) |
| `rename` | `{old: new}` column renames, applied to the assembled table |
| `derive` | New columns computed from fetched ones — see [Derived columns](#derived-columns) |
| `filter` | Keep only rows matching every `{column: value}`, applied after `rename`/`derive` — see [Narrowing](#narrowing) |

A single-request source can skip the `requests` list and put that
request's keys (`url`/`path`, `params`, `records`, `where`, `keep`,
`headers_as_columns`) directly in `source`.

### `requests`

| Key | Description |
|-----|-------------|
| `path` | Endpoint path, joined onto `base_url` |
| `url` | Full URL, as an alternative to `path` |
| `params` | Query parameters, as a mapping |
| `records` | Dot-path to the list of records in the response (e.g. `rows`, `data.items`). Omit if the response *is* a list |
| `name` | Label, so a later request can `for_each` this one |
| `for_each` | Issue this request once per row of the named earlier request |
| `where` | Keep only records matching every `{field: value}`. A list value matches any of its entries |
| `merge` | Parent fields to copy onto each record (only with `for_each`) |
| `keep` | Keep only these columns from this request's records |
| `headers_as_columns` | `{column: Header-Name}` — record a response header as a column |

`{field}` references in `path` and `params` are filled from the parent
row of a `for_each`.

### Derived columns

Fetched fields rarely arrive in the shape you want to plot against. The
`derive` block builds new columns from existing ones, using one of three
operations:

```yaml
derive:
  station:        {from: sensor, split: "_", index: -1}   # STATION_N04 -> N04
  station_number: {from: sensor, regex: "(\\d+)$", type: int}
  mode:           {from: setup, kv: MODE}                 # MODE=fast;RANGE=hi -> fast
```

| Key | Description |
|-----|-------------|
| `from` | Source column (required) |
| `regex` | Regular expression; the captured group becomes the value |
| `group` | Which capture group to take (default 1) |
| `kv` | Pull one key out of a `KEY=VAL;KEY=VAL` string |
| `sep`, `assign` | Separators for `kv` (default `;` and `=`) |
| `split` | Split on this string |
| `index` | Which piece to take after `split` (default `-1`, the last) |
| `type` | `int`, `float` or `str` — cast the result |

A numeric derived column is what lets a categorical field (a board, a
station, a device) serve as the x-axis.

## Narrowing

There are two places to cut the data down, and they do different jobs:

- a request's **`where`** matches raw records, before the fan-out — so
  it decides how many requests get issued;
- the source's **`filter`** matches the assembled table, after `rename`
  and `derive` — so it can select on a column that only exists once the
  records have been reshaped.

```yaml
requests:
  - name: series
    path: /v1/series
    where: {kind: temperature}    # do not fetch points for other kinds

derive:
  mode: {from: setup, kv: MODE}
filter: {mode: fast}              # a column `where` could not have seen
```

Both match exactly, and a list value matches any of its entries. A
`filter` that matches nothing is an error rather than an empty plot.

## Provenance

A multi-request pull is only one dataset if every response came from the
same build of it. `require_consistent_headers` names the headers that
pin that down, and the fetch fails if one changes partway through:

```yaml
require_consistent_headers: [X-Data-Generation]
```

Without it, a service that regenerates between two requests hands back a
table silently stitched from two versions. Pair it with
`headers_as_columns` to carry the value into the data, so a plot can
name the generation it came from.

## Request budget

A `for_each` issues one GET per parent row, so a spec with a forgotten
`limit` can turn into thousands of requests against someone's service.
`max_requests` (default 200) stops the run with a message naming the
cap rather than quietly hammering the API. Narrow the parent request
with `where` and `params`, or raise the cap deliberately.

## Secrets

Header values expand `${VAR}` from the environment, which is the only
supported way to send a credential:

```yaml
source:
  base_url: https://api.example.com
  headers:
    Authorization: "Bearer ${MY_API_TOKEN}"
```

A spec file is meant to be committed and shared, so the token itself
must not live in it. An unset variable is an error naming the variable,
not a request sent with the literal string `${MY_API_TOKEN}`.

## Safety

`source:` fetches inherit the guards described under [URL
sources](/cicwave/url-sources#fetch-behavior): a 200 MB cap per
response, a request timeout, clear one-line errors instead of
tracebacks, and a refusal to touch any host that resolves to a
link-local address (where cloud instance-metadata credentials live).
Only GET is ever issued.

## Sessions

Save a session built from an API source and the file entry names the
spec rather than a data path, since there is no file to point at:

```yaml
files:
  - source: measurements.yaml
```

Loading that session re-fetches from the API.

## MCP

The [MCP tools](/cicwave/mcp) take the same specs. Pass `pivot` and
omit the file:

```json
{"pivot": "measurements.yaml", "waves": ["N04", "N07"]}
```
