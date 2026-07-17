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

The per-record field decoding (PIR/PRR/PTR) batches each record's
fixed-size field groups into a single struct.unpack_from() call rather
than one call per field, and accumulates results into flat per-column
lists instead of a dict per row -- pure-Python STDF parsers spend
nearly all their time in exactly that per-field/per-row overhead (not
in gzip decompression or in pandas itself), so this is where the
actual wins are, well before reaching for a compiled extension.
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


def _fast_cn(buf, pos, end):
    """Variable-length string at `pos`: 1-byte length + that many chars.

    `pos`/`end` are offsets into `buf` (the whole file's bytes), with
    `end` the exclusive bound of the current record. Returns (string,
    new_pos). STDF truncation drops whole trailing fields, never
    partial ones, so running past `end` just means "field (and
    everything after it) not present" -- returns "" and pins `pos` at
    `end` so later group-reads in the same record see the same
    exhausted state.
    """
    if pos >= end:
        return "", end
    length = buf[pos]
    pos += 1
    if length == 0:
        return "", pos
    field_end = pos + length
    if field_end > end:
        return "", end
    return buf[pos:field_end].decode('ascii', errors='replace'), field_end


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
    #- Tracked per test site, not globally, so interleaved multi-site
    #- PIR/PTR/PRR sequences (concurrent parts on different sites --
    #- the normal case in production testing) attribute each PTR to
    #- the part actually under test on that site.
    #-
    #- PART_ID lives on PRR, which is written *after* the PTRs for that
    #- part (a part's PRR closes it out). So each site gets a
    #- placeholder id at PIR time, every PTR row for that site is
    #- recorded (by index into the column lists) in site_to_indices,
    #- and when PRR finally supplies the real PART_ID those slots are
    #- patched in place.
    site_to_part_id = {}
    site_to_index = {}
    site_to_indices = {}

    part_ids = []
    site_nums = []
    test_nums = []
    test_txts = []
    results = []
    units_col = []
    lo_limits = []
    hi_limits = []
    res_scals = []
    test_flgs = []

    with _open_maybe_gzip(fname) as fh:
        data = fh.read()

    #- The whole file is decoded from one in-memory buffer via
    #- struct.unpack_from() at increasing offsets, rather than issuing
    #- a pair of fh.read() calls (and allocating a fresh bytes object)
    #- per record. gzip decompression and pandas' own DataFrame
    #- construction are both fast; this per-record I/O/allocation
    #- overhead is where a pure-Python STDF reader actually loses time.
    total = len(data)
    if total < 4:
        return pd.DataFrame(columns=_COLUMNS)
    big_endian = _detect_endianness(data[0:4])
    order = '>' if big_endian else '<'

    s_len = struct.Struct(order + 'H')
    s_pir = struct.Struct(order + 'BB')
    s_prr_head = struct.Struct(order + 'BBBHHHhhI')
    s_ptr_head = struct.Struct(order + 'IBBBBf')
    s_ptr_mid = struct.Struct(order + 'Bbbb')
    s_ptr_lim = struct.Struct(order + 'ff')

    pos = 0
    while pos + 4 <= total:
        rec_len = s_len.unpack_from(data, pos)[0]
        rec_typ = data[pos + 2]
        rec_sub = data[pos + 3]
        body = pos + 4
        end = body + rec_len
        if end > total:
            end = total
        n = end - body

        key = (rec_typ, rec_sub)
        if key == _REC_PTR:
            if n >= s_ptr_head.size:
                test_num, _head, site, test_flg, _parm_flg, result = \
                    s_ptr_head.unpack_from(data, body)
                p = body + s_ptr_head.size
            else:
                test_num, site, test_flg, result = 0, 0, 0, float('nan')
                p = end

            test_txt, p = _fast_cn(data, p, end)
            _alarm_id, p = _fast_cn(data, p, end)

            if p + s_ptr_mid.size <= end:
                _opt_flag, res_scal, _llm_scal, _hlm_scal = \
                    s_ptr_mid.unpack_from(data, p)
                p += s_ptr_mid.size
            else:
                res_scal = 0
                p = end

            if p + s_ptr_lim.size <= end:
                lo_limit, hi_limit = s_ptr_lim.unpack_from(data, p)
                p += s_ptr_lim.size
            else:
                lo_limit, hi_limit = 0.0, 0.0
                p = end

            units, p = _fast_cn(data, p, end)

            part_id = site_to_part_id.get(site)
            if not part_id:
                part_id = 'part_%d' % site_to_index.get(
                    site, len(part_ids) + 1)

            idx = len(part_ids)
            part_ids.append(part_id)
            site_nums.append(site)
            test_nums.append(test_num)
            test_txts.append(test_txt)
            results.append(result)
            units_col.append(units)
            lo_limits.append(lo_limit)
            hi_limits.append(hi_limit)
            res_scals.append(res_scal)
            test_flgs.append(test_flg)
            site_to_indices.setdefault(site, []).append(idx)
        elif key == _REC_PIR:
            if n >= s_pir.size:
                _head, site = s_pir.unpack_from(data, body)
            else:
                site = 0
            site_to_index[site] = site_to_index.get(site, 0) + 1
            site_to_part_id[site] = 'part_%d' % site_to_index[site]
            site_to_indices[site] = []
        elif key == _REC_PRR:
            if n >= s_prr_head.size:
                (_head, site, _part_flg, _num_test, _hard_bin,
                 _soft_bin, _x, _y, _test_t) = \
                    s_prr_head.unpack_from(data, body)
                p = body + s_prr_head.size
            else:
                site, p = 0, end
            part_id, p = _fast_cn(data, p, end)
            if part_id:
                site_to_part_id[site] = part_id
                for i in site_to_indices.get(site, ()):
                    part_ids[i] = part_id
                site_to_indices[site] = []
        #- Anything else (MIR, MPR, FTR, wafer/summary records, ...)
        #- is intentionally left unparsed; `end` above already marks
        #- exactly rec_len bytes as consumed, so the stream stays in
        #- sync regardless.

        pos = end

    df = pd.DataFrame({
        'part_id': part_ids,
        'site_num': site_nums,
        'test_num': test_nums,
        'test_txt': test_txts,
        'result': np.asarray(results, dtype=np.float64),
        'units': units_col,
        'lo_limit': lo_limits,
        'hi_limit': hi_limits,
        'res_scal': res_scals,
        'test_flg': test_flgs,
    }, columns=_COLUMNS)
    return df
