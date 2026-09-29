"""Write uart_demo.csv: "Hello, cicwave!" at 115200 baud, with noise."""

import numpy as np
import pandas as pd

from cicwave_uart.uart import encode

t, v = encode(b"Hello, cicwave!", baud=115200, fs=10e6)
v = 3.3 * v + np.random.default_rng(1).normal(0, 0.1, v.size)
pd.DataFrame({"time": t, "tx": v}).to_csv("uart_demo.csv", index=False)
print("wrote uart_demo.csv (%d samples)" % v.size)
