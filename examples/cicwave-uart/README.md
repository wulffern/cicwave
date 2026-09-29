# cicwave-uart: an example cicwave plugin

A small, complete [cicwave plugin](https://wulffern.github.io/cicwave/plugins)
that decodes UART bytes from a waveform. Copy it as a starting point for
your own protocol decoder.

```sh
pip install -e .          # next to an installed cicwave
python make_demo.py       # writes uart_demo.csv
cicwave uart_demo.csv
```

Right-click `tx` and choose **Decode UART...**. The baud rate is estimated
from the shortest pulse; confirm it and a new tab shows the waveform with
each decoded byte above its frame (red on a framing error) and the decoded
text under the plot. **Help → Plugins** lists the plugin and its analysis.

| File | |
|------|--|
| `pyproject.toml` | declares the `cicwave.plugins` entry point |
| `cicwave_uart/__init__.py` | `register(api)` and the analysis (the cicwave/Qt part) |
| `cicwave_uart/uart.py` | the decoder itself, plain numpy, easy to test |
| `make_demo.py` | writes a noisy 115200-baud capture to try it on |
| `screenshots.py` | regenerates the screenshots on the [docs page](https://wulffern.github.io/cicwave/uart-plugin) |

Keeping the decoder free of Qt, as here, lets you unit-test it without a
display and reuse it in scripts.
