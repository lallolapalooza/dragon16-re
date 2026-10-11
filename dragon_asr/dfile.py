"""DFILE v10 reader — Nuance MREC's universal container.

Used by every ASR asset in the media: acoustic models (`*.usr`, `*.psh`), signal-processing
and linguistic enhancers (`*.enh`), vocabularies (`*.voc`, incl. `dispatch.voc`, `pg.voc`,
`vocdelta.voc`), and utterance/user files (`*.ivc`, `*.ivs`, `*.wt`).

Verified structure (from the shipped files, cross-checked with `mrec.dll` strings):

    signature  b"DFILE v10\\x00\\x00\\x00"            # 16 bytes
    u32        version        = 10
    u32        flags          = 0xFFFE0002
    u32        page_size      = 0x1000 (4096)
    u32        entry_count    (varies: 4 … 5541)
    u32        marker         = 0xEDACFF0E
    u32…       per-file header values
    …          page-aligned (0x1000) payload; the last page ends with 0xEDCAFF2E then zero padding

Payloads are Huffman/combined-table coded (`mrec.dll: fileutil/huffutil.cpp`, `cmbtable.cpp`),
which is why plain-text strings are absent and byte entropy is ~7.9.

Subfile classes referenced by the engine (directory entries carry these types):
`Voc`, `SigProc`, `User`, `ParamSetHolder`, `EnvHolder`, plus extensions `.fmp`, `.sp`, `.lm`,
`.ivc`, `.ivs`, `.wt` (see `mrec.dll` string table: `dfutil/dfile.cpp`, `realdf.cpp`).

The subfile *directory* layout is provisional here (header + page map); `dragon_asr` exposes the
raw payload and page table so a caller can plug in the exact directory parse once confirmed
from `RealDFile`/`RealDFileSubFileEntry`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional

SIGNATURE = b"DFILE v10\x00\x00\x00"
HEADER_MARKER = 0xEDACFF0E
TRAILER_MARKER = 0xEDACFF2E
DEFAULT_PAGE = 0x1000

# subfile class names seen in mrec.dll
SUBFILE_CLASSES = ("Voc", "SigProc", "User", "ParamSetHolder", "EnvHolder")
SUBFILE_EXTENSIONS = (".fmp", ".sp", ".lm", ".ivc", ".ivs", ".wt")


@dataclass
class DFileHeader:
    version: int = 0
    flags: int = 0
    page_size: int = DEFAULT_PAGE
    entry_count: int = 0
    marker: int = 0
    fields: List[int] = field(default_factory=list)   # u32 words 16..0x40

    def as_dict(self) -> Dict[str, int]:
        return {"version": self.version, "flags": self.flags, "page_size": self.page_size,
                "entry_count": self.entry_count, "marker": self.marker}


class DFile:
    """A parsed DFILE container (structure + raw access)."""

    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as fh:
            self.raw = fh.read()
        if self.raw[:9] != b"DFILE v10":
            raise ValueError(f"{path}: not a DFILE (sig={self.raw[:16]!r})")
        self.header = DFileHeader(
            version=struct.unpack_from("<I", self.raw, 16)[0],
            flags=struct.unpack_from("<I", self.raw, 20)[0],
            page_size=struct.unpack_from("<I", self.raw, 24)[0],
            entry_count=struct.unpack_from("<I", self.raw, 28)[0],
            marker=struct.unpack_from("<I", self.raw, 32)[0],
            fields=[struct.unpack_from("<I", self.raw, 16 + 4 * i)[0] for i in range(10)],
        )
        self.size = len(self.raw)
        self.pages = self.size // (self.header.page_size or DEFAULT_PAGE)
        self.remainder = self.size % (self.header.page_size or DEFAULT_PAGE)
        tail = self.raw[-64:]
        self.trailer_ok = struct.pack("<I", TRAILER_MARKER) in self.raw[-512:]

    # ------------------------------------------------------------------
    @property
    def payload(self) -> bytes:
        """Everything after the 16-byte signature + header block (page aligned)."""
        return self.raw[0x40:]

    def page(self, index: int, count: int = 1) -> bytes:
        off = index * self.header.page_size
        return self.raw[off:off + count * self.header.page_size]

    def find_marker(self, marker: int = HEADER_MARKER) -> List[int]:
        needle = struct.pack("<I", marker)
        out, start = [], 0
        while True:
            i = self.raw.find(needle, start)
            if i < 0:
                return out
            if i % 4 == 0:
                out.append(i)
            start = i + 1

    def strings(self, min_len: int = 5) -> List[str]:
        """ASCII runs (Huffman-coded payloads are opaque; this only finds unencoded leftovers)."""
        import re
        return [m.group().decode("latin-1")
                for m in re.finditer(rb"[\x20-\x7e]{%d,}" % min_len, self.raw)]

    def __repr__(self) -> str:
        h = self.header
        return (f"<DFile {self.path.rsplit('/',1)[-1]} v{h.version} page={h.page_size:#x} "
                f"entries={h.entry_count} pages={self.pages} size={self.size}>")


    def page_entropy(self, pages: Optional[int] = None) -> List[float]:
        """Shannon entropy (bits/byte) of each 0x1000 page — locates table vs coded subfiles."""
        import collections
        import math
        out: List[float] = []
        ps = self.header.page_size or DEFAULT_PAGE
        n = pages if pages is not None else self.pages
        for i in range(n):
            chunk = self.raw[i * ps:(i + 1) * ps]
            if not chunk:
                break
            c = collections.Counter(chunk)
            ln = len(chunk)
            out.append(-sum(v / ln * math.log2(v / ln) for v in c.values()))
        return out

    def payload_classes(self) -> Dict[str, int]:
        """Classify pages: 'coded' (>7.5 b/B), 'dense' (6.5-7.5), 'tables' (<6.5)."""
        counts = {"coded": 0, "dense": 0, "tables": 0}
        for h in self.page_entropy():
            if h > 7.5:
                counts["coded"] += 1
            elif h > 6.5:
                counts["dense"] += 1
            else:
                counts["tables"] += 1
        return counts

    def read_subfile(self, offset: int, size: int) -> bytes:
        """Raw slice of the payload (once the directory parse pins offsets, this is the accessor)."""
        return self.raw[offset:offset + size]

    def subfile_hint(self) -> str:
        """One-line summary of where coded vs plaintext data lives (for the decoder work)."""
        c = self.payload_classes()
        return (f"pages coded={c['coded']} dense={c['dense']} tables={c['tables']} "
                f"(coded pages need fileutil/huffutil+cmbtable; tables are readable as-is)")


def probe(path: str) -> str:
    """One-line description used by the CLI/inventory tools."""
    try:
        d = DFile(path)
    except ValueError:
        return f"{path}: not DFILE"
    return (f"{path.rsplit('/',1)[-1]:26} v{d.header.version} page={d.header.page_size:#x} "
            f"entries={d.header.entry_count:6d} pages={d.pages:6d} size={d.size}")
