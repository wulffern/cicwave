---
layout: page
title:  UART decoder plugin
math: true
---

* TOC
{:toc }

## What it does

`cicwave-uart` is a small, complete [plugin](/cicwave/plugins) that decodes
UART bytes from a waveform: a logic-analyser channel, a scope capture or a
simulated TX pin. It lives in
[`examples/cicwave-uart`](https://github.com/wulffern/cicwave/tree/main/examples/cicwave-uart)
and is meant to be copied as the starting point for your own protocol
decoder.

It adds one entry, **Decode UART...**, to a wave's right-click menu. The
decoded bytes are drawn over the waveform in a new tab:

![The Decode UART tab: the noisy TX line with each decoded character above its frame, and "Hello, cicwave!" in the readout](/cicwave/assets/uart_decoded.png)

## Trying it

```sh
cd examples/cicwave-uart
pip install -e .          # next to an installed cicwave
python make_demo.py       # writes uart_demo.csv
cicwave uart_demo.csv
```

`make_demo.py` writes "Hello, cicwave!" as 8N1 at 115200 baud, sampled at
10 MS/s, on a 3.3 V line with 0.1 V of noise.

1. **Help → Plugins** confirms that cicwave found the plugin:

   ![Help → Plugins listing uart 0.1.0 and its Decode UART analysis](/cicwave/assets/uart_plugins.png)

2. Right-click `tx` in the browser and choose **Decode UART...**. The
   plugin estimates the baud rate from the waveform and asks you to
   confirm it:

   ![The baud rate dialog, pre-filled with the estimate 115291](/cicwave/assets/uart_baud.png)

3. A tab named `UART: tx` opens. Each decoded byte is written above its
   frame, in yellow, or red on a framing or parity error. The readout
   under the plot gives the byte count, the baud rate used and the
   decoded text. Zoom in to check individual frames:

   ![The first three frames, H, e and l, zoomed in](/cicwave/assets/uart_decoded_zoom.png)

   The first frame is `H` (0x48): a start bit, the data bits
   `0 0 0 1 0 0 1 0` sent LSB first, and a stop bit.

## How it decodes

The decoder is in `cicwave_uart/uart.py`. It uses only numpy, so it can be
unit-tested without a display and used from scripts.

**Threshold.** The line is split into high and low halfway between its
5th and 95th percentiles, so a few glitches don't shift the level.

**Baud rate.** The runs between edges are multiples of one bit time. The
shortest runs are single bits, so the estimate is the mean of all runs
shorter than 1.5 × the 5th-percentile run:

$$
f_\text{baud} \approx \frac{1}{\overline{T}_\text{single bit}}
$$

On the demo capture this gives 115291 baud, 0.08 % from the true 115200.

**Frames.** From each falling edge on an idle line, the decoder
checks that the start bit is still low half a bit later (otherwise it was
a glitch), then samples every following bit in its middle:

$$
t_k = t_0 + (k + 1.5)\,T_\text{bit}, \qquad k = 0, 1, \dots
$$

The data bits are read LSB first, and the stop bit (and parity, if set)
decide whether the frame is marked OK. The next start bit is searched for
from half-way into the stop bit, so a slightly fast transmitter isn't
missed.

`decode()` also takes `data_bits`, `parity` (`"even"`/`"odd"`),
`stop_bits`, `invert` for an idle-low line, and a fixed `level`.

## How it plugs in

Only `cicwave_uart/__init__.py` knows about cicwave. `pyproject.toml`
declares the entry point:

```toml
[project.entry-points."cicwave.plugins"]
uart = "cicwave_uart:register"
```

and `register` describes the plugin and adds the analysis:

```python
def register(api):
    api.set_description("Decodes UART bytes from a waveform")
    api.register_analysis("Decode UART...", decode_uart)
```

`decode_uart(window, wave)` runs when the menu entry is chosen. It reads
`wave.x` and `wave.y`, asks for the baud rate, and draws into a tab from
`window.add_analysis_tab()`:

```python
frames = decode(x, y, baud, level=level)

tab = window.add_analysis_tab("UART: %s" % wave.key)
tab.plot(x, y, pen=pg.mkPen("c", width=1))
for f in frames:
    text = pg.TextItem(_label(f.value), color="y" if f.ok else "r",
                       anchor=(0.5, 1.0))
    text.setPos((f.start + f.stop) / 2, top)
    tab.pw.addItem(text)
tab.set_notes("%d bytes at %.0f baud\n%s" % (...))
```

## Writing your own decoder

A decoder for another protocol (SPI, I²C, Manchester, a private bus) has
the same two parts:

- a pure function from samples to frames, tested on its own. cicwave's
  test suite does this for the UART example (`UartExampleTest` in
  `tests/unittests/test_plugins.py`), encoding a message, adding noise and
  checking it decodes back;
- an analysis that asks for any settings and draws the frames, registered
  with `api.register_analysis`.

A protocol with several lines, such as SPI's clock and data, can find the
other waves in `wave.wfile.df`. To label parts of a recording as it loads
rather than on request, use an annotator instead; see
[the plugin API](/cicwave/plugins#the-api).

`screenshots.py` in the example regenerates the pictures on this page.
