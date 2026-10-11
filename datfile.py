#!/usr/bin/env python3
"""Reader for Nuance RealSpeak/Vocalizer '.dat' assets (TTS.cab, Voices\\speech\\components).

Format (little-endian; verified on pp.dat, synth_*.dat, uselect_*.dat,
clc_*_cfg3.dat is a different/plain format):

    file   := magic(4) = FB E0 EF 9D   + u32 body_len + body
    body   := rootName(8)              + u32 root_payload_len + root_payload
    chunk  := tag(4)  + u32 size + payload(size)

  * `tag` bytes and *text* payloads are obfuscated with XOR 0xA9.
  * integer fields (the u32 size, numeric payloads) are stored in the clear.
  * a tag whose following 4 bytes are also a valid tag is a container label with **no size
    field** (e.g. `INFO` introducing `ICOP/ICRD/VERS/...`); its scope ends with the parent.
  * rootName examples: 'PP\\x00\\x00LIST', 'HMEXLIST', 'USELLIST' (the last 4 chars are 'LIST').
  * payload lengths are 32-bit; alignment padding is 0x00 in the clear.

Widely seen tags: INFO, ICOP (copyright), ICRD (date), VERS (version), CFIG (config u16),
BRKD (break data), EXHD (engine header text), EXDT (data), VINF (voice info header),
Lng/Voice/Reduction/... (VINF sub-keys), LHDT, TLHD, SPKR, UNTS, ...

CLI:
  python3 datfile.py <file> [--depth N] [--strings] [--limit N]
  python3 datfile.py <file> --dump TAG out.bin
"""
import re
import struct
import sys

MAGIC = b'\xfb\xe0\xef\x9d'
KEY = 0xA9
TEXT_TAGS = {'ICOP', 'ICRD', 'VERS', 'EXHD', 'VINF', 'NAME', 'INFO', 'Lng', 'Voice',
             'Reduction', 'DevDir', 'ID', 'DESC', 'LANG', 'DCT', 'COMT'}

def deob(b):
    return bytes(x ^ KEY for x in b)

def tag_at(buf, off):
    if off + 4 > len(buf):
        return None
    cand = deob(buf[off:off + 4])
    if all(0x20 <= b <= 0x7e for b in cand):
        return cand.decode('latin-1')
    return None

def looks_text(b):
    if not b:
        return False
    printable = sum(1 for x in b if 0x20 <= x <= 0x7e or x in (0, 9, 10, 13))
    return printable >= 0.85 * len(b)

def scan(buf, off, end, skip_payload=False):
    """Flat structure scan -> list of (tag, size|None, payload_raw)."""
    out = []
    p = off
    while p + 4 <= end:
        tag = tag_at(buf, p)
        if tag is None:
            if buf[p] in (0x00, KEY):
                p += 1
                continue
            break
        if tag_at(buf, p + 4) is not None:
            out.append((tag, None, b''))
            p += 4
            continue
        if p + 8 > end:
            break
        size = struct.unpack_from('<I', buf, p + 4)[0]
        if p + 8 + size > end or size > len(buf):
            break
        out.append((tag, size, buf[p + 8:p + 8 + size] if not skip_payload else b''))
        p = p + 8 + size
    return out

def text_of(tag, raw):
    if looks_text(raw):
        return deob(raw)
    return raw

def main():
    args = sys.argv[1:]
    path = args[0]
    if '--dump' in args:
        want = args[args.index('--dump') + 1]
        outp = args[args.index('--dump') + 2]
        raw = open(path, 'rb').read()
        buf = raw[8:]
        root_size = struct.unpack_from('<I', buf, 8)[0]
        for tag, size, payload in scan(buf, 12, 12 + root_size):
            if tag == want and size is not None:
                open(outp, 'wb').write(text_of(tag, payload))
                print(f'dumped {tag} ({size} bytes) -> {outp}')
                return
        print('tag not found (root level)')
        return
    maxdepth = 3
    if '--depth' in args:
        maxdepth = int(args[args.index('--depth') + 1])
    strings = '--strings' in args
    limit = int(args[args.index('--limit') + 1]) if '--limit' in args else 400

    raw = open(path, 'rb').read()
    if raw[:4] != MAGIC:
        print(f'{path}: no FB E0 EF 9D magic (first16={raw[:16].hex()})')
        return
    blen = struct.unpack_from('<I', raw, 4)[0]
    buf = raw[8:]
    root_size = struct.unpack_from('<I', buf, 8)[0]
    print(f'{path}: body_len={blen} actual={len(buf)}   root={deob(buf[:8]).rstrip(chr(0).encode()).decode("latin-1")!r} root_size={root_size}')

    lines = []

    def emit(off, end, depth):
        for tag, size, payload in scan(buf, off, end):
            if size is None:
                lines.append('  ' * depth + f'{tag}/')
                continue
            note = ''
            if strings and size:
                t = text_of(tag, payload)
                if looks_text(t):
                    s = re.findall(rb'[ -~]{4,}', t[:256])
                    if s:
                        note = '  ' + ' | '.join(x.decode('latin-1') for x in s[:3])[:110]
            lines.append('  ' * depth + f'{tag} size={size}{note}')
            if depth + 1 <= maxdepth and size >= 32 and not strings:
                sub = scan(buf, off + 0, end)
                # nested binary containers: try parsing payload as chunks
                p2 = off + 8
                sub2 = scan(buf, p2, p2 + size)
                del sub
                if sub2 and sum((s or 4) + 8 for _, s, _ in sub2) >= 0.5 * size:
                    emit(p2, p2 + size, depth + 1)

    emit(12, len(buf), 0)
    print('\n'.join(lines[:limit]))
    if len(lines) > limit:
        print(f'... ({len(lines) - limit} more)')

if __name__ == '__main__':
    main()
