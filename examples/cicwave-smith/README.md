# cicwave-smith: an example cicwave plugin

A [cicwave plugin](https://wulffern.github.io/cicwave/plugins) that opens
Touchstone S-parameter files (`.s1p` ... `.s9p`) and draws a Smith chart
of any reflection coefficient. It shows both a **reader** and an
**analysis**; see the [UART decoder](../cicwave-uart) for an analysis-only
plugin.

```sh
pip install -e .          # next to an installed cicwave
python make_demo.py       # writes antenna.s1p
cicwave antenna.s1p
```

Plot `S11_dB` to see the match against frequency, then right-click `S11`
(or `S11_dB`) and choose **Smith chart**.

| File | |
|------|--|
| `pyproject.toml` | declares the `cicwave.plugins` entry point |
| `cicwave_smith/__init__.py` | `register(api)` and the chart drawing (the cicwave/Qt part) |
| `cicwave_smith/touchstone.py` | the Touchstone v1 reader, plain numpy/pandas |
| `cicwave_smith/smith.py` | chart grid and impedance maths, plain numpy |
| `make_demo.py` | writes a resonant antenna's S11, 2–3 GHz |
| `screenshots.py` | regenerates the screenshots on the [docs page](https://wulffern.github.io/cicwave/smith-plugin) |
