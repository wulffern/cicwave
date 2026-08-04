"""Headless ``--export`` must actually write a file.

``exportAndExit`` only wrote something if a tab already held plotted waves.
Opening a file plots nothing - the user picks waves in the GUI - so a headless
``cicwave --x time data.csv --export out.png`` walked the (empty) tab list,
found nothing, and quit having written no file and said nothing about it. The
autoplot helper that fixes this already existed but was reached only for pivot
specs, though it was never pivot-specific.
"""

import os
import shutil
import tempfile
import unittest


def _headless():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("CICSIM_USE_OPENGL", "0")


class ExportHeadlessTest(unittest.TestCase):

    def setUp(self):
        _headless()
        self.tmp = tempfile.mkdtemp(prefix="cicwave-export-")
        self.csv = os.path.join(self.tmp, "d.csv")
        with open(self.csv, "w", encoding="utf-8") as fh:
            fh.write("time,v_V,i_A\n")
            for k in range(64):
                fh.write(f"{k*1e-6},{k*0.01},{k*1e-3}\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args):
        from cicwave.cli import main
        try:
            main(list(args), standalone_mode=False)
        except SystemExit:
            pass

    def test_export_image_writes_a_file(self):
        out = os.path.join(self.tmp, "plot.png")
        self._run("--x", "time", self.csv, "--export", out)
        self.assertTrue(os.path.exists(out), "--export wrote no file")
        self.assertGreater(os.path.getsize(out), 1000, "exported image is empty")

    def test_export_data_writes_a_file(self):
        out = os.path.join(self.tmp, "data.csv")
        self._run("--x", "time", self.csv, "--export-data", out)
        self.assertTrue(os.path.exists(out), "--export-data wrote no file")
        with open(out, "r", encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("time", body.splitlines()[0].lower())
        self.assertGreater(len(body.splitlines()), 2)

    def test_both_exports_together(self):
        img = os.path.join(self.tmp, "both.png")
        dat = os.path.join(self.tmp, "both.csv")
        self._run("--x", "time", self.csv, "--export", img, "--export-data", dat)
        self.assertTrue(os.path.exists(img))
        self.assertTrue(os.path.exists(dat))

    def test_autoplot_alias_still_resolves(self):
        """The old pivot-named entry point is kept; callers may still use it."""
        from cicwave.wave_pg import PgWaveWindow
        self.assertTrue(hasattr(PgWaveWindow, "autoplot_pivot_for_export"))
        self.assertTrue(hasattr(PgWaveWindow, "autoplot_for_export"))


if __name__ == "__main__":
    unittest.main()
