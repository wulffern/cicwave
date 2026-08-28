#!/usr/bin/env python3
"""Tests for how a pivot names its waves.

The default name mangles index and conditions together
(``A1_MFast Mode_Gwide``). A ``wave_name`` template lets a spec produce
dotted names instead, which the wave tree shows as a hierarchy.
"""

import unittest

import pandas as pd

from cicwave.pivot import apply_pivot


def _frame():
    rows = []
    for mode in ("Fast Mode", "Slow Mode"):
        for group in ("wide", "narrow"):
            for unit in ("A1", "A2"):
                for x in (1.0, 2.0):
                    rows.append({
                        "unit": unit,
                        "mode": mode,
                        "group": group,
                        "x": x,
                        "value": len(rows) * 0.5,
                    })
    return pd.DataFrame.from_records(rows)


BASE_SPEC = {
    "index": "unit",
    "columns": "x",
    "values": "value",
    "conditions": ["mode", "group"],
}


class TestDefaultNaming(unittest.TestCase):
    def test_default_names_are_unchanged(self):
        # Including the space in the condition value: only a templated
        # name collapses whitespace, so existing specs keep their names.
        out = apply_pivot(_frame(), dict(BASE_SPEC))
        self.assertIn("A1_MFast Mode_Gwide", out.columns)


class TestWaveNameTemplate(unittest.TestCase):
    def test_dotted_template_builds_hierarchical_names(self):
        spec = dict(BASE_SPEC, wave_name="{mode}.{group}.{unit}")
        out = apply_pivot(_frame(), spec)
        self.assertIn("Fast_Mode.wide.A1", out.columns)
        self.assertIn("Slow_Mode.narrow.A2", out.columns)

    def test_spaces_become_underscores(self):
        # The wave tree only splits a dotted name into a hierarchy when
        # it holds no spaces, so a spacey condition value would silently
        # flatten the tree.
        spec = dict(BASE_SPEC, wave_name="{mode}.{unit}")
        out = apply_pivot(_frame(), spec)
        self.assertTrue(all(" " not in c for c in out.columns), list(out.columns))

    def test_aliases_apply_to_template_fields(self):
        spec = dict(
            BASE_SPEC,
            wave_name="{mode}.{group}.{unit}",
            aliases={"mode": {"c0": "F", "c1": "S"}},
        )
        out = apply_pivot(_frame(), spec)
        self.assertIn("F.wide.A1", out.columns)
        self.assertIn("S.wide.A1", out.columns)

    def test_literal_text_around_fields_is_kept(self):
        spec = dict(BASE_SPEC, wave_name="sweep/{mode}/{unit}")
        out = apply_pivot(_frame(), spec)
        self.assertIn("sweep/Fast_Mode/A1", out.columns)

    def test_template_may_use_a_subset_of_fields(self):
        # Dropping `group` merges the two groups into one wave, which is
        # the existing aggfunc='mean' behaviour, not an error.
        spec = dict(BASE_SPEC, wave_name="{mode}.{unit}")
        out = apply_pivot(_frame(), spec)
        self.assertIn("Fast_Mode.A1", out.columns)

    def test_unknown_field_names_the_available_ones(self):
        spec = dict(BASE_SPEC, wave_name="{mode}.{nosuch}")
        with self.assertRaises(KeyError) as cm:
            apply_pivot(_frame(), spec)
        message = str(cm.exception)
        self.assertIn("nosuch", message)
        self.assertIn("unit", message)

    def test_template_without_fields_is_rejected(self):
        spec = dict(BASE_SPEC, wave_name="allthesame")
        with self.assertRaises(ValueError):
            apply_pivot(_frame(), spec)


class TestUnits(unittest.TestCase):
    """A spec can state the y unit instead of hiding it in a suffix."""

    def test_literal_unit_applies_to_every_wave(self):
        spec = dict(BASE_SPEC, unit="dBm")
        out = apply_pivot(_frame(), spec)
        units = out.attrs["cicwave_wave_unit"]
        self.assertEqual(set(units.values()), {"dBm"})
        self.assertEqual(len(units), len(out.columns) - 1)

    def test_unit_can_come_from_a_column(self):
        frame = _frame()
        frame["measure_unit"] = ["dBm" if m == "Fast Mode" else "dBc"
                                 for m in frame["mode"]]
        spec = dict(BASE_SPEC, conditions=["mode", "group", "measure_unit"],
                    unit="{measure_unit}")
        out = apply_pivot(frame, spec)
        self.assertEqual(set(out.attrs["cicwave_wave_unit"].values()),
                         {"dBm", "dBc"})

    def test_no_unit_key_leaves_attrs_alone(self):
        out = apply_pivot(_frame(), dict(BASE_SPEC))
        self.assertNotIn("cicwave_wave_unit",
                         getattr(out, "attrs", {}) or {})

    def test_declared_unit_reaches_the_plotted_wave(self):
        try:
            from cicwave.wave_pg import PgWave
        except Exception as e:  # pragma: no cover - optional GUI deps
            self.skipTest("pyqtgraph / PySide6 not installed (%s)" % e)
        from cicwave.wavefiles import WaveFiles

        out = apply_pivot(_frame(), dict(BASE_SPEC, unit="dBm"))
        wf = WaveFiles().openDataFrame(out, "p", "x")
        wave = PgWave(wf, "A1_MFast Mode_Gwide", "x")
        self.assertEqual(wave.yunit, "dBm")


class TestHierarchyParsing(unittest.TestCase):
    """The tree splits the names this produces."""

    def test_dotted_name_splits_into_scopes(self):
        try:
            from cicwave.wave_pg import PgWaveBrowser
        except Exception as e:  # pragma: no cover - optional GUI deps
            self.skipTest("pyqtgraph / PySide6 not installed (%s)" % e)
        self.assertEqual(
            PgWaveBrowser._parse_hierarchy("HV.27.Gain"),
            ["HV", "27", "Gain"])


if __name__ == "__main__":
    unittest.main()
