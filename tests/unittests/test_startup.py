"""Guards against slow imports creeping back into viewer startup."""

import subprocess
import sys
import unittest


class TestStartupImports(unittest.TestCase):

    def test_viewer_import_does_not_load_matplotlib(self):
        # matplotlib is only needed for PDF export and cursor readouts;
        # importing it up front added ~0.1-0.2 s to every launch.
        code = ("import sys, cicwave.cli, cicwave.wave_pg; "
                "print('matplotlib' in sys.modules)")
        out = subprocess.run([sys.executable, "-c", code],
                             capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.strip(), "False")


if __name__ == "__main__":
    unittest.main()
