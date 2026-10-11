#!/usr/bin/env python3
"""MSI table reader: streams are column-major (all values of col1, then col2...).

Type decoding (empirical):
  bit 0x0800 -> string column: 2-byte string-pool id (0 = NULL)
  otherwise  -> integer column of width (type & 0xff) bytes: 1/2/4
  object/binary columns (width 0, no 0x0800) hold a string id of the stream name.
"""
import struct, sys, os, csv

src, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)

def rd(name):
    with open(os.path.join(src, name), 'rb') as f:
        return f.read()

sp, sd = rd('!_StringPool'), rd('!_StringData')
strings = ['']  # id 0 = NULL
off = 0
for i in range((len(sp) - 4) // 4):
    ln, _refs = struct.unpack_from('<HH', sp, 4 + i * 4)
    strings.append(sd[off:off + ln].decode('cp1252', errors='replace'))
    off += ln

def getstr(sid):
    return None if sid == 0 else strings[sid]

def col_size(typ):
    if typ & 0x0800:
        return 2
    w = typ & 0xff
    return w if w else 2  # object column (w=0) stores a stream-name id

def read_col(data, pos, typ, n):
    vals = []
    if typ & 0x0800 or (typ & 0xff) == 0:
        for _ in range(n):
            vals.append(getstr(struct.unpack_from('<H', data, pos)[0])); pos += 2
    else:
        w = typ & 0xff
        fmt = {1: '<B', 2: '<H', 4: '<I'}[w]
        flag = 0x8000 if w == 2 else (0x80000000 if w == 4 else 0x80)
        for _ in range(n):
            v = struct.unpack_from(fmt, data, pos)[0]; pos += w
            vals.append(v - flag if v >= flag else v)
    return vals, pos

# parse _Columns (itself column-major: Table s, Number i2, Name s, Type i2)
cd = rd('!_Columns')
coldefs = {}  # table -> [(num, name, type)]
# discover N for _Columns: widths 2,2,2,2 -> 8 bytes/row
N = len(cd) // 8
tbl_c = [getstr(struct.unpack_from('<H', cd, i)[0]) for i in range(0, N * 2, 2)]
num_c = [struct.unpack_from('<H', cd, N * 2 + i)[0] & 0x7fff for i in range(0, N * 2, 2)]
name_c = [getstr(struct.unpack_from('<H', cd, N * 4 + i)[0]) for i in range(0, N * 2, 2)]
type_c = [struct.unpack_from('<H', cd, N * 6 + i)[0] for i in range(0, N * 2, 2)]
for t, num, nm, ty in zip(tbl_c, num_c, name_c, type_c):
    coldefs.setdefault(t, []).append((num, nm, ty))

tables = set(coldefs)

for tname in sorted(tables):
    cols = sorted(coldefs[tname])
    stream = '!' + tname
    if not os.path.exists(os.path.join(src, stream)):
        continue
    data = rd(stream)
    sizes = [col_size(ty) for _, _, ty in cols]
    total = sum(sizes)
    if len(data) % total:
        print(f'{tname}: SIZE MISMATCH len={len(data)} row={total}'); continue
    n = len(data) // total
    pos = 0
    columns = []
    for (num, nm, ty), sz in zip(cols, sizes):
        vals, pos = read_col(data, pos, ty, n)
        columns.append((nm, vals))
    with open(os.path.join(out, tname + '.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow([nm for nm, _ in columns])
        for r in range(n):
            w.writerow([v for _, vals in columns for v in [vals[r]]])
    print(f'{tname}: {n} rows')
