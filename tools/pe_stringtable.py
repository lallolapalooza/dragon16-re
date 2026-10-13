#!/usr/bin/env python3
"""Dump a PE image's RT_STRING (STRINGTABLE) resources.

Dragon's settings layer (`RgyReadDataInt`/`RgyReadDataCStr`) resolves setting names from
string-table IDs, so this reads the ID -> name mapping straight out of the module that owns the
API (`dd10shrd.dll`).

Usage:  python3 pe_stringtable.py <file.dll|exe> [substring-filter]
"""
import struct
import sys


def sections(d):
    e = struct.unpack_from('<I', d, 0x3C)[0]
    nsec = struct.unpack_from('<H', d, e + 6)[0]
    optsz = struct.unpack_from('<H', d, e + 20)[0]
    out = []
    for i in range(nsec):
        o = e + 24 + optsz + i * 40
        name = d[o:o + 8].rstrip(b'\0').decode('latin-1')
        vsz, rva, rsz, raw = struct.unpack_from('<IIII', d, o + 8)
        out.append((name, rva, vsz, raw, rsz))
    return out


def r2o(secs, rva):
    for _n, va, vsz, raw, rsz in secs:
        if va <= rva < va + max(vsz, rsz):
            return raw + (rva - va)
    return None


def walk(d, secs, off, base, want_type=None, out=None, depth=0):
    """Recursively walk an IMAGE_RESOURCE_DIRECTORY."""
    if out is None:
        out = []
    n_named, n_id = struct.unpack_from('<HH', d, off + 12)
    for i in range(n_named + n_id):
        e = off + 16 + i * 8
        if e + 8 > len(d):
            return out
        name_id, data_off = struct.unpack_from('<II', d, e)
        if data_off & 0x80000000:
            walk(d, secs, base + (data_off & 0x7FFFFFFF), base, want_type, out, depth + 1)
        elif depth == 2:            # language leaf -> IMAGE_RESOURCE_DATA_ENTRY
            de = base + data_off
            rva, size = struct.unpack_from('<II', d, de)
            if want_type is None or want_type == _cur_type:
                o = r2o(secs, rva)
                if o is not None:
                    out.append((_cur_type, name_id, rva, size))
    return out


def dump_strings(d, secs, filter_sub=''):
    e = struct.unpack_from('<I', d, 0x3C)[0]
    nsec = struct.unpack_from('<H', d, e + 6)[0]
    optsz = struct.unpack_from('<H', d, e + 20)[0]
    rsrc = None
    for i in range(nsec):
        o = e + 24 + optsz + i * 40
        name = d[o:o + 8].rstrip(b'\0').decode('latin-1')
        vsz, rva, rsz, raw = struct.unpack_from('<IIII', d, o + 8)
        if name == '.rsrc':
            rsrc = (rva, vsz)
    if not rsrc:
        print('no .rsrc')
        return []
    base_off = r2o(secs, rsrc[0])
    # top-level: collect type-6 (RT_STRING) leaves
    leaves = []
    n_named, n_id = struct.unpack_from('<HH', d, base_off + 12)
    for i in range(n_named + n_id):
        e2 = base_off + 16 + i * 8
        name_id, data_off = struct.unpack_from('<II', d, e2)
        if not (data_off & 0x80000000):
            continue
        if name_id != 6:            # RT_STRING
            continue
        sub = base_off + (data_off & 0x7FFFFFFF)
        n2, m2 = struct.unpack_from('<HH', d, sub + 12)
        for j in range(n2 + m2):
            e3 = sub + 16 + j * 8
            blk_id, d2 = struct.unpack_from('<II', d, e3)
            if not (d2 & 0x80000000):
                continue
            sub2 = base_off + (d2 & 0x7FFFFFFF)
            n3, m3 = struct.unpack_from('<HH', d, sub2 + 12)
            for k in range(n3 + m3):
                e4 = sub2 + 16 + k * 8
                lang, d3 = struct.unpack_from('<II', d, e4)
                if d3 & 0x80000000:
                    continue
                de = base_off + d3
                rva, size = struct.unpack_from('<II', d, de)
                o = r2o(secs, rva)
                if o is None:
                    continue
                pos = o
                end = o + size
                idx = (blk_id - 1) * 16 + 1
                strs = []
                while pos + 2 <= end:
                    ln = struct.unpack_from('<H', d, pos)[0]
                    pos += 2
                    if ln == 0:
                        strs.append('')
                    else:
                        s = d[pos:pos + ln * 2].decode('utf-16-le', 'replace')
                        pos += ln * 2
                        strs.append(s)
                for n, s in enumerate(strs):
                    sid = idx + n
                    if s and (not filter_sub or filter_sub.lower() in s.lower()):
                        leaves.append((sid, s))
    return leaves


def main():
    path = sys.argv[1]
    filt = sys.argv[2] if len(sys.argv) > 2 else ''
    with open(path, 'rb') as fh:
        d = fh.read()
    secs = sections(d)
    rows = dump_strings(d, secs, filt)
    rows.sort()
    print(f'{path}: {len(rows)} string-table entries matching {filt!r}')
    for sid, s in rows:
        print(f'  id={sid:<6} {s}')


if __name__ == '__main__':
    _cur_type = None
    main()
