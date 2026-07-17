#!/usr/bin/env python3
"""Tests for the STDF (Standard Test Data Format) reader.

Builds small STDF V4 binary fixtures byte-for-byte (FAR/PIR/PRR/PTR
records packed with struct) since there's no bundled sample file, and
checks the parser against exactly what was packed.
"""

import gzip
import os
import struct
import tempfile
import unittest

from cicwave import stdf
from cicwave.wavefiles import WaveFile


def _pack_cn(s):
    b = s.encode('ascii')
    return struct.pack('B', len(b)) + b


def _rec(order, rec_typ, rec_sub, payload):
    return struct.pack(order + 'H', len(payload)) + bytes([rec_typ, rec_sub]) + payload


def _far(order, cpu_type=2):
    payload = struct.pack('BB', cpu_type, 4)  # CPU_TYPE, STDF_VER
    return _rec(order, 0, 10, payload)


def _pir(order, head, site):
    payload = struct.pack(order + 'BB', head, site)
    return _rec(order, 5, 10, payload)


def _prr(order, head, site, part_id, hard_bin=1, soft_bin=1, num_test=0):
    payload = struct.pack(order + 'BBBHHHhhI', head, site, 0, num_test,
                          hard_bin, soft_bin, 0, 0, 0)
    payload += _pack_cn(part_id)
    return _rec(order, 5, 20, payload)


def _ptr(order, test_num, head, site, result, test_txt="", units="",
         lo_limit=0.0, hi_limit=0.0, test_flg=0, res_scal=0,
         alarm_id="", opt_flag=0, include_optional=True):
    payload = struct.pack(order + 'IBBBBf', test_num, head, site,
                          test_flg, 0, result)
    payload += _pack_cn(test_txt)
    if include_optional:
        payload += _pack_cn(alarm_id)
        payload += struct.pack(order + 'Bbbb', opt_flag, res_scal, 0, 0)
        payload += struct.pack(order + 'ff', lo_limit, hi_limit)
        payload += _pack_cn(units)
    return _rec(order, 15, 10, payload)


def _mir_stub(order):
    """An unsupported record type (MIR), to verify it's safely skipped."""
    payload = b'\x00' * 20
    return _rec(order, 1, 10, payload)


class TestStdfParser(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cicwave-stdf-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, chunks):
        path = os.path.join(self.tmp, name)
        with open(path, 'wb') as f:
            for c in chunks:
                f.write(c)
        return path

    def test_single_part_single_test(self):
        order = '<'
        chunks = [
            _far(order),
            _pir(order, 1, 1),
            _ptr(order, 1000, 1, 1, 1.23, test_txt="Vout", units="V",
                lo_limit=1.0, hi_limit=1.5),
            _prr(order, 1, 1, "U1"),
        ]
        path = self._write("one.stdf", chunks)
        df = stdf.toDataFrame(path)
        self.assertEqual(len(df), 1)
        row = df.iloc[0]
        self.assertEqual(row['part_id'], "U1")
        self.assertEqual(row['test_num'], 1000)
        self.assertEqual(row['test_txt'], "Vout")
        self.assertAlmostEqual(row['result'], 1.23, places=5)
        self.assertEqual(row['units'], "V")
        self.assertAlmostEqual(row['lo_limit'], 1.0, places=5)
        self.assertAlmostEqual(row['hi_limit'], 1.5, places=5)

    def test_part_id_uses_prr_even_when_prr_follows_ptr(self):
        # PRR (which carries PART_ID) comes AFTER the PTRs for that part
        # in real STDF files -- the parser must not need PART_ID before
        # attributing the PTR.
        order = '<'
        chunks = [
            _far(order),
            _pir(order, 1, 1),
            _ptr(order, 1, 1, 1, 5.0, test_txt="A"),
            _ptr(order, 2, 1, 1, 6.0, test_txt="B"),
            _prr(order, 1, 1, "DUT42"),
        ]
        path = self._write("late_id.stdf", chunks)
        df = stdf.toDataFrame(path)
        self.assertEqual(list(df['part_id']), ["DUT42", "DUT42"])

    def test_interleaved_multi_site_attributes_correctly(self):
        # Two sites tested in parallel: PIR(site1), PIR(site2), then
        # their PTRs interleaved, then PRR(site1), PRR(site2). Each
        # PTR carries its own SITE_NUM and must be matched to the
        # right part despite the interleaving.
        order = '<'
        chunks = [
            _far(order),
            _pir(order, 1, 1),
            _pir(order, 1, 2),
            _ptr(order, 100, 1, 1, 1.0, test_txt="T1"),
            _ptr(order, 100, 1, 2, 2.0, test_txt="T1"),
            _ptr(order, 101, 1, 1, 1.1, test_txt="T2"),
            _prr(order, 1, 1, "SITE1_PART"),
            _ptr(order, 101, 1, 2, 2.1, test_txt="T2"),
            _prr(order, 1, 2, "SITE2_PART"),
        ]
        path = self._write("multisite.stdf", chunks)
        df = stdf.toDataFrame(path)
        self.assertEqual(len(df), 4)
        site1 = df[df['site_num'] == 1]
        site2 = df[df['site_num'] == 2]
        self.assertTrue((site1['part_id'] == "SITE1_PART").all())
        self.assertTrue((site2['part_id'] == "SITE2_PART").all())
        self.assertAlmostEqual(
            float(site1[site1['test_num'] == 100]['result'].iloc[0]), 1.0,
            places=5)
        self.assertAlmostEqual(
            float(site2[site2['test_num'] == 100]['result'].iloc[0]), 2.0,
            places=5)

    def test_multiple_parts_second_lot(self):
        order = '<'
        chunks = [
            _far(order),
            _pir(order, 1, 1),
            _ptr(order, 1, 1, 1, 1.0, test_txt="V"),
            _prr(order, 1, 1, "P1"),
            _pir(order, 1, 1),
            _ptr(order, 1, 1, 1, 2.0, test_txt="V"),
            _prr(order, 1, 1, "P2"),
        ]
        path = self._write("two_parts.stdf", chunks)
        df = stdf.toDataFrame(path)
        self.assertEqual(list(df['part_id']), ["P1", "P2"])
        self.assertEqual(list(df['result'].round(3)), [1.0, 2.0])

    def test_missing_part_id_falls_back_to_generated_id(self):
        order = '<'
        chunks = [
            _far(order),
            _pir(order, 1, 1),
            _ptr(order, 1, 1, 1, 1.0, test_txt="V"),
            # No PRR at all -- part_id must still be something usable.
        ]
        path = self._write("no_prr.stdf", chunks)
        df = stdf.toDataFrame(path)
        self.assertEqual(len(df), 1)
        self.assertTrue(df.iloc[0]['part_id'])

    def test_truncated_optional_fields_use_defaults(self):
        # A PTR with only the fixed fields (no ALARM_ID/limits/UNITS) --
        # legal per spec; must not raise, and trailing fields default.
        order = '<'
        chunks = [
            _far(order),
            _pir(order, 1, 1),
            _ptr(order, 1, 1, 1, 3.3, test_txt="short",
                include_optional=False),
            _prr(order, 1, 1, "U1"),
        ]
        path = self._write("truncated.stdf", chunks)
        df = stdf.toDataFrame(path)
        self.assertEqual(len(df), 1)
        row = df.iloc[0]
        self.assertAlmostEqual(row['result'], 3.3, places=5)
        self.assertEqual(row['units'], "")

    def test_unknown_record_type_is_skipped_without_breaking_sync(self):
        order = '<'
        chunks = [
            _far(order),
            _mir_stub(order),
            _pir(order, 1, 1),
            _ptr(order, 1, 1, 1, 9.0, test_txt="V"),
            _prr(order, 1, 1, "U1"),
        ]
        path = self._write("with_mir.stdf", chunks)
        df = stdf.toDataFrame(path)
        self.assertEqual(len(df), 1)
        self.assertAlmostEqual(df.iloc[0]['result'], 9.0, places=5)

    def test_big_endian_file(self):
        order = '>'
        chunks = [
            _far(order, cpu_type=1),
            _pir(order, 1, 1),
            _ptr(order, 1, 1, 1, 7.5, test_txt="BE"),
            _prr(order, 1, 1, "U1"),
        ]
        path = self._write("be.stdf", chunks)
        df = stdf.toDataFrame(path)
        self.assertEqual(len(df), 1)
        self.assertAlmostEqual(df.iloc[0]['result'], 7.5, places=5)

    def test_gzip_compressed_file(self):
        order = '<'
        chunks = [
            _far(order),
            _pir(order, 1, 1),
            _ptr(order, 1, 1, 1, 4.2, test_txt="Z"),
            _prr(order, 1, 1, "U1"),
        ]
        path = os.path.join(self.tmp, "z.stdf.gz")
        with gzip.open(path, 'wb') as f:
            for c in chunks:
                f.write(c)
        df = stdf.toDataFrame(path)
        self.assertEqual(len(df), 1)
        self.assertAlmostEqual(df.iloc[0]['result'], 4.2, places=5)

    def test_empty_file_returns_empty_dataframe_with_columns(self):
        path = self._write("empty.stdf", [])
        df = stdf.toDataFrame(path)
        self.assertEqual(len(df), 0)
        self.assertIn('part_id', df.columns)
        self.assertIn('result', df.columns)

    def test_wavefile_dispatches_stdf_extension(self):
        order = '<'
        chunks = [
            _far(order),
            _pir(order, 1, 1),
            _ptr(order, 1000, 1, 1, 1.5, test_txt="Vout", units="V"),
            _prr(order, 1, 1, "U1"),
        ]
        path = self._write("via_wavefile.stdf", chunks)
        wf = WaveFile(path, xaxis="")
        self.assertEqual(
            sorted(wf.getWaveNames()),
            sorted(['part_id', 'site_num', 'test_num', 'test_txt',
                    'result', 'units', 'lo_limit', 'hi_limit',
                    'res_scal', 'test_flg']))
        self.assertEqual(len(wf.df), 1)

    def test_wavefile_dispatches_stdf_gz_extension(self):
        order = '<'
        chunks = [
            _far(order),
            _pir(order, 1, 1),
            _ptr(order, 1, 1, 1, 1.0, test_txt="V"),
            _prr(order, 1, 1, "U1"),
        ]
        path = os.path.join(self.tmp, "via_wavefile.stdf.gz")
        with gzip.open(path, 'wb') as f:
            for c in chunks:
                f.write(c)
        wf = WaveFile(path, xaxis="")
        self.assertEqual(len(wf.df), 1)


if __name__ == "__main__":
    unittest.main()
