---
layout: page
title:  mcp
math: true
---

* TOC
{:toc }

## Description

`cicwave-mcp` is an [MCP](https://modelcontextprotocol.io) server that
exposes cicwave's headless plotting and analysis as tools an agent can
call directly — no shelling out to the CLI, no hand-written session
YAML. This is aimed at the "agent drives testing" workflow: point an
agent at a simulation/measurement file and let it request a plot (and
get the image back inline) or a numeric summary as part of a test run.

It requires the `mcp` extra, and Python 3.10+ (the
[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)'s
own requirement — the rest of cicwave supports 3.8+):

```bash
pip install "cicwave[mcp]"
```

Both SDK generations work: 1.x (`FastMCP`) and 2.x, which renamed that
class to `MCPServer` and moved it out of `mcp.server.fastmcp`.

## Running it

```bash
cicwave-mcp
```

This speaks MCP over stdio, so it's normally launched by an MCP client
rather than run directly. For Claude Code:

```bash
claude mcp add cicwave -- cicwave-mcp
```

Or add it to an `.mcp.json` / client config directly:

```json
{
  "mcpServers": {
    "cicwave": {
      "command": "cicwave-mcp"
    }
  }
}
```

Rendering runs Qt in offscreen mode automatically (no display needed).

## Tools

### `plot`

Render a plot from one or more data files and return the image inline.

| Argument | Required | Description |
|----------|----------|-------------|
| `files` | yes | Local paths or `http(s)://` URLs — see [URL sources](/cicwave/url-sources) |
| `waves` | no | Column/signal names to plot. Omit to plot every numeric column of the first file except the x-axis |
| `x` | no | X-axis column. Omit to auto-detect |
| `pivot` | no | Path to a [pivot spec](/cicwave/pivot) to reshape long-format data first; `waves` then refers to post-pivot names |
| `title` | no | Plot title |
| `url_format` | no | Force the format for an extension-less URL |

### `pivot_info`

Inspect the dimensions available in a file for a pivot spec — the same
output as `cicwave --pivot spec.yaml --pivot-info`. Use this before
calling `plot`/`analyze` with a pivot spec so the agent isn't guessing
column names and unique values.

| Argument | Required | Description |
|----------|----------|-------------|
| `file` | yes | Local path or URL |
| `pivot` | yes | Path to the pivot spec |
| `url_format` | no | Force the format for an extension-less URL |

### `analyze`

Run headless numeric analysis and return a text summary — the same
backends as [pivot spec `analysis.steps`](/cicwave/pivot#preprocessing-and-headless-analysis)
and the GUI's right-click analysis menu, but with the steps passed
directly instead of embedded in a spec file.

| Argument | Required | Description |
|----------|----------|-------------|
| `file` | yes | Local path or URL |
| `steps` | yes | List of step dicts — `rms`, `dynamic_parameters`, `adc_psd`, `linear_fit`, `difference` (see [Pivot](/cicwave/pivot#steps) for each type's keys) |
| `pivot` | no | Reshape first, and apply the spec's `analysis.preprocess` block (e.g. two's-complement decode) before running `steps` |
| `x` | no | X-axis column override |
| `url_format` | no | Force the format for an extension-less URL |

## Example

A typical "agent drives testing" turn: run a simulation that writes
`output_tran/tran_typical.csv`, then ask the agent to check it —

```
plot(files=["output_tran/tran_typical.csv"], waves=["v(out)"], x="time")
```

returns the rendered plot directly in the conversation, and

```
analyze(file="output_tran/tran_typical.csv",
        steps=[{"type": "rms", "column": "v(out)"}])
```

returns `"[v(out)] RMS=0.897141"` as text — no file round-trip needed
for either.
