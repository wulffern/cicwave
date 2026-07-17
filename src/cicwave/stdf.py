#!/usr/bin/env python3
"""Minimal STDF (Standard Test Data Format, SEMI E10 / V4) reader.

STDF is the binary format semiconductor ATE (automated test equipment)
writes production/characterization test results in. This is a
practical subset, not full V4 spec coverage: it decodes exactly what's
needed to trend a parametric measurement across parts --

  - FAR (0,10): bootstrap record, used only to detect byte order.
  - PIR (5,10) / PRR (5,20): part identity (site number, PART_ID),
    tracked per test site so interleaved multi-site testing (the norm
    in production ATE -- several parts tested in parallel, their PIR/
    PTR/PRR records interleaved in the file) attributes each PTR to
    the correct part.
  - PTR (15,10): the parametric measurement itself.

Every other record type (MIR, MPR, FTR, wafer records, summary
records, ...) is skipped using its length header, which STDF
guarantees is always safe -- you never need to understand a record's
payload to skip past it. Notably NOT decoded: MPR (multi-result/
per-pin parametric records) and FTR (functional test records); if you
need those, this is the place to add them.

RES_SCAL/LLM_SCAL/HLM_SCAL (the scaling-exponent fields) are exposed
as a raw passthrough column rather than applied to `result` -- their
exact multiplier convention isn't reliably recalled here, and
silently mis-scaling a measurement is worse than not scaling it.
Treat `result`/`lo_limit`/`hi_limit` as being in the file's native
(unscaled) units together with `units`, and apply `res_scal` yourself
if your ATE's convention is known.

Transparently handles gzip-compressed files (very common for STDF in
production -- files can be huge), detected by magic bytes regardless
of filename.
"""

import gzip
import struct

import numpy as np
import pandas as pd

_COLUMNS = [
    'part_id', 'site_num', 'test_num', 'test_txt', 'result',
    'units', 'lo_limit', 'hi_limit', 'res_scal', 'test_flg',
]

_REC_FAR = (0, 10)
_REC_PIR = (5, 10)
_REC_PRR = (5, 20)
_REC_PTR = (15, 10)


class _Cursor:
    """Sequential, byte-order-aware reader over one record's payload.

    STDF records are allowed to be truncated: trailing optional fields
    that weren't written are simply absent, not zero-filled. Reading
    past the end of the payload therefore isn't corruption -- it means
    "field not present" -- so every accessor returns a type-appropriate
    default instead of raising once the payload is exhausted.
    """

    def __init__(self, data, big_endian):
        self.data = data
        self.pos = 0
        self.n = len(data)
        self.order = '>' if big_endian else '<'

    def _take(self, size):
        if self.pos + size > self.n:
            self.pos = self.n
            return None
        b = self.data[self.pos:self.pos + size]
        self.pos += size
        return b

    def u1(self, default=0):
        b = self._take(1)
        return default if b is None else b[0]

    def i1(self, default=0):
        b = self._take(1)
        return default if b is None else struct.unpack(self.order + 'b', b)[0]

    def u2(self, default=0):
        b = self._take(2)
        return default if b is None else struct.unpack(self.order + 'H', b)[0]

    def i2(self, default=0):
        b = self._take(2)
        return default if b is None else struct.unpack(self.order + 'h', b)[0]

    def u4(self, default=0):
        b = self._take(4)
        return default if b is None else struct.unpack(self.order + 'I', b)[0]

    def r4(self, default=float('nan')):
        b = self._take(4)
        return default if b is None else struct.unpack(self.order + 'f', b)[0]

    def cn(self, default=""):
        """Variable-length string: 1-byte length prefix + that many chars."""
        if self.pos >= self.n:
            return default
        length = self.u1()
        if length == 0:
            return ""
        b = self._take(length)
        return default if b is None else b.decode('ascii', errors='replace')


def _detect_endianness(header4):
    """FAR is always the first record. Its own header is written in the
    file's byte order, which we don't know yet -- so try both
    interpretations and keep whichever gives a sane FAR (REC_TYP=0,
    REC_SUB=10, small REC_LEN). Falls back to little-endian, by far the
    more common case in modern STDF files, if neither looks sane (e.g.
    a truncated/non-STDF file) so callers still get a best-effort parse
    rather than an exception here.
    """
    rec_typ, rec_sub = header4[2], header4[3]
    if rec_typ == 0 and rec_sub == 10:
        for big_endian, fmt in ((True, '>'), (False, '<')):
            rec_len = struct.unpack(fmt + 'H', header4[0:2])[0]
            if 0 < rec_len < 64:
                return big_endian
    return False


def _open_maybe_gzip(fname):
    with open(fname, 'rb') as fh:
        magic = fh.read(2)
    if magic == b'\x1f\x8b':
        return gzip.open(fname, 'rb')
    return open(fname, 'rb')


def toDataFrame(fname):
    """Parse an STDF file into a flat DataFrame of parametric results.

    One row per (part, test) PTR record. See the module docstring for
    exactly which record types are decoded.
    """
    rows = []
    #- Tracked per test site, not globally, so interleaved multi-site
    #- PIR/PTR/PRR sequences (concurrent parts on different sites --
    #- the normal case in production testing) attribute each PTR to
    #- the part actually under test on that site.
    #-
    #- PART_ID lives on PRR, which is written *after* the PTRs for that
    #- part (a part's PRR closes it out). So each site gets a
    #- placeholder id at PIR time, every PTR row for that site keeps a
    #- reference to its dict in site_to_rows, and when PRR finally
    #- supplies the real PART_ID those row dicts are patched in place.
    site_to_part_id = {}
    site_to_index = {}
    site_to_rows = {}

    with _open_maybe_gzip(fname) as fh:
        header = fh.read(4)
        if len(header) < 4:
            return pd.DataFrame(columns=_COLUMNS)
        big_endian = _detect_endianness(header)
        order = '>' if big_endian else '<'

        while len(header) == 4:
            rec_len = struct.unpack(order + 'H', header[0:2])[0]
            rec_typ, rec_sub = header[2], header[3]
            payload = fh.read(rec_len)

            key = (rec_typ, rec_sub)
            if key == _REC_PIR:
                c = _Cursor(payload, big_endian)
                c.u1()  # HEAD_NUM
                site = c.u1()
                site_to_index[site] = site_to_index.get(site, 0) + 1
                site_to_part_id[site] = 'part_%d' % site_to_index[site]
                site_to_rows[site] = []
            elif key == _REC_PRR:
                c = _Cursor(payload, big_endian)
                c.u1()  # HEAD_NUM
                site = c.u1()
                c.u1()  # PART_FLG
                c.u2()  # NUM_TEST
                c.u2()  # HARD_BIN
                c.u2()  # SOFT_BIN
                c.i2()  # X_COORD
                c.i2()  # Y_COORD
                c.u4()  # TEST_T
                part_id = c.cn()
                if part_id:
                    site_to_part_id[site] = part_id
                    for row in site_to_rows.get(site, ()):
                        row['part_id'] = part_id
                    site_to_rows[site] = []
            elif key == _REC_PTR:
                c = _Cursor(payload, big_endian)
                test_num = c.u4()
                c.u1()  # HEAD_NUM
                site = c.u1()
                test_flg = c.u1()
                c.u1()  # PARM_FLG
                result = c.r4()
                test_txt = c.cn()
                c.cn()  # ALARM_ID
                c.u1()  # OPT_FLAG
                res_scal = c.i1()
                c.i1()  # LLM_SCAL
                c.i1()  # HLM_SCAL
                lo_limit = c.r4()
                hi_limit = c.r4()
                units = c.cn()

                part_id = site_to_part_id.get(site)
                if not part_id:
                    part_id = 'part_%d' % site_to_index.get(site, len(rows) + 1)
                row = {
                    'part_id': part_id,
                    'site_num': site,
                    'test_num': test_num,
                    'test_txt': test_txt,
                    'result': result,
                    'units': units,
                    'lo_limit': lo_limit,
                    'hi_limit': hi_limit,
                    'res_scal': res_scal,
                    'test_flg': test_flg,
                }
                rows.append(row)
                site_to_rows.setdefault(site, []).append(row)
            #- Anything else (MIR, MPR, FTR, wafer/summary records, ...)
            #- is intentionally left unparsed; `payload` above already
            #- consumed exactly rec_len bytes, so the stream stays in
            #- sync regardless.

            header = fh.read(4)

    df = pd.DataFrame(rows, columns=_COLUMNS)
    if len(df):
        df['result'] = df['result'].astype(np.float64)
    return df
