#!/usr/bin/env python3
"""Tests for the cicwave-mcp server tools (plot / pivot_info / analyze).

Skipped entirely when the optional `mcp` extra isn't installed, same
pattern as the HAVE_PG-gated PySide6 tests.
"""

import os
import tempfile
import unittest

import numpy as np
import pandas as pd

try:  # pragma: no cover - optional dependency
    import mcp as _mcp_pkg  # noqa: F401
    from cicwave.mcp_server import analyze, mcp, pivot_info, plot
    HAVE_MCP = True
except Exception:
    HAVE_MCP = False


@unittest.skipUnless(HAVE_MCP, "mcp package not installed (pip install cicwave[mcp])")
class McpToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cicwave-mcp-")

        t = np.linspace(0, 2e-6, 200)
        vp = 0.9 + 0.4 * np.sin(2 * np.pi * 2e6 * t)
        vn = 0.9 - 0.4 * np.sin(2 * np.pi * 2e6 * t)
        self.csv = os.path.join(self.tmp, "test.csv")
        pd.DataFrame({"time": t, "v(vp)": vp, "v(vn)": vn}).to_csv(
            self.csv, index=False)

        self.pivot_csv = os.path.join(self.tmp, "pivot_data.csv")
        pd.DataFrame({
            "Parameter": ["Gain"] * 4,
            "Freq": [1, 2, 1, 2],
            "Value": [10.0, 20.0, 11.0, 21.0],
            "Temp": [27, 27, 85, 85],
        }).to_csv(self.pivot_csv, index=False)
        self.pivot_spec = os.path.join(self.tmp, "spec.yaml")
        with open(self.pivot_spec, "w") as f:
            f.write(
                "index: Parameter\ncolumns: Freq\nvalues: Value\n"
                "conditions: [Temp]\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_plot_returns_png_image(self):
        img = plot(files=[self.csv], waves=["v(vp)"], x="time")
        self.assertEqual(img._mime_type, "image/png")
        self.assertGreater(len(img.data), 1000)
        self.assertEqual(img.data[:8], b"\x89PNG\r\n\x1a\n")

    def test_plot_multiple_waves_and_title(self):
        img = plot(files=[self.csv], waves=["v(vp)", "v(vn)"], x="time",
                  title="two waves")
        self.assertGreater(len(img.data), 1000)

    def test_plot_defaults_to_all_columns_when_waves_omitted(self):
        img = plot(files=[self.csv], x="time")
        self.assertGreater(len(img.data), 1000)

    def test_plot_unknown_wave_raises(self):
        with self.assertRaises(ValueError):
            plot(files=[self.csv], waves=["nope"], x="time")

    def test_plot_with_pivot(self):
        img = plot(files=[self.pivot_csv], pivot=self.pivot_spec,
                  waves=["Gain_T27"])
        self.assertGreater(len(img.data), 1000)

    def test_pivot_info_lists_dimensions(self):
        info = pivot_info(file=self.pivot_csv, pivot=self.pivot_spec)
        self.assertIn("Parameter", info)
        self.assertIn("Gain", info)
        self.assertIn("Temp", info)

    def test_analyze_rms(self):
        df = pd.DataFrame({"y": [3.0, 4.0]})
        csv = os.path.join(self.tmp, "rms.csv")
        df.to_csv(csv, index=False)
        summary = analyze(file=csv, steps=[{"type": "rms", "column": "y"}])
        self.assertIn("RMS=", summary)
        # RMS of [3, 4] is 5/sqrt(2).
        self.assertIn("3.5355", summary)

    def test_analyze_linear_fit_with_pivot(self):
        summary = analyze(
            file=self.pivot_csv, pivot=self.pivot_spec,
            steps=[{"type": "linear_fit", "y_column": "Gain_T27"}])
        self.assertIn("slope=", summary)

    def test_analyze_applies_pivot_preprocess_twos_complement(self):
        df = pd.DataFrame({
            "Parameter": ["Code"] * 2,
            "Sample": [0, 1],
            "Value": [4095, 100],  # 4095 -> -1 as 12-bit signed
        })
        csv = os.path.join(self.tmp, "codes.csv")
        df.to_csv(csv, index=False)
        spec_path = os.path.join(self.tmp, "codes_spec.yaml")
        with open(spec_path, "w") as f:
            f.write(
                "index: Parameter\ncolumns: Sample\nvalues: Value\n"
                "analysis:\n  preprocess:\n    twos_complement:\n"
                "      width_bits: 12\n      columns: [Value]\n")
        summary = analyze(
            file=csv, pivot=spec_path,
            steps=[{"type": "rms", "column": "Code"}])
        self.assertIn("RMS=", summary)
        val = float(summary.split("RMS=")[1])
        # Decoded values are [-1, 100] (4095 -> -1 as 12-bit signed);
        # RMS = sqrt((1 + 10000) / 2) ~= 70.71. Raw/undecoded would be
        # sqrt((4095^2 + 100^2) / 2) ~= 2896, an easy way to tell decode
        # actually ran rather than being silently skipped.
        self.assertAlmostEqual(val, 70.7142, places=3)


@unittest.skipUnless(HAVE_MCP, "mcp package not installed (pip install cicwave[mcp])")
class McpProtocolTest(unittest.IsolatedAsyncioTestCase):
    """Round-trip a call through the real MCP tool-call machinery (schema
    validation, not just calling the Python function directly)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cicwave-mcp-proto-")
        t = np.linspace(0, 1, 50)
        self.csv = os.path.join(self.tmp, "d.csv")
        pd.DataFrame({"time": t, "v": t * 2}).to_csv(self.csv, index=False)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def test_tools_are_registered(self):
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        self.assertEqual(names, {"plot", "pivot_info", "analyze"})

    async def test_call_tool_plot_returns_image_content(self):
        result = await mcp.call_tool(
            "plot", {"files": [self.csv], "waves": ["v"], "x": "time"})
        content = result[0] if isinstance(result, tuple) else result
        self.assertTrue(len(content) >= 1)
        self.assertEqual(content[0].type, "image")

    async def test_call_tool_analyze_returns_text(self):
        result = await mcp.call_tool(
            "analyze", {"file": self.csv,
                        "steps": [{"type": "rms", "column": "v"}]})
        content = result[0] if isinstance(result, tuple) else result
        self.assertEqual(content[0].type, "text")
        self.assertIn("RMS=", content[0].text)


if __name__ == "__main__":
    unittest.main()
