"""Reader for `clc_<lang>_cfg3.dat` — the RealSpeak/Vocalizer front-end configuration container.

Structure (decoded from the shipped `clc_*_cfg3.dat` files; these are **not** DFILE and **not** the
LHS `.dat` container — they have their own magic family):

    u32  magic        0x1FE632E7 (voice file) / 0x1FE7C8B5, 0x1FE6AD1F (record magics)
    u16  version      observed 0x0000/0x0001 pairs
    u16  flags        observed 0x0001
    u32  file_size    equals the on-disk size (e.g. 0x55B5 = 21941)
    u32  flags2       0x00010004
    u32  offset_a     0x00144000 (model/table region selector)
    u32  0
    u32  magic2       0x1FE7C8B5
    u32  0x10181018
    …   offsets/counts, then records

Records carry 4-character ASCII tags (`NVAR`, `pipeline`, `depes_<lang>_<voice>`, `phonmap_<lang>_<voice>`,
`SCANSOFT`, `global`, `silence`, `"tag:question"`, `"tag:statement"`) and **length-prefixed strings**
(`u32 length` + bytes), which makes the linguistic tables directly readable:

* letter-to-sound / lexicon rules, e.g. ``(th)en 'DEn``, ``batt(le) 'b@.r6l%)``, ``c(a)t 'k@t``
* alphabet tables (`ABCDEFGHIJKLMNOPQRSTUVWXYZabc…`) and character-class maps
* per-language voice identification (`enu`, `samantha`, `1.00`)

The reader exposes header fields, named records, all length-prefixed strings and the rule tables;
the numeric tables remain opaque (no public documentation of their packing).
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

MAGICS = (0x1FE632E7, 0x1FE7C8B5, 0x1FE6AD1F, 0x1FE2AF1F)
RULE_RE = re.compile(rb"[(][A-Za-z']{0,6}[)]?[A-Za-z]{0,6}[ ]{0,2}'[A-Za-z0-9@^&%+._\-]{1,14}")
TAG_RE = re.compile(rb'"[a-z]+:[a-z]+"')
NAME_RE = re.compile(rb"[A-Za-z][A-Za-z0-9_]{3,40}")


@dataclass
class ClcHeader:
    magic: int = 0
    version: int = 0
    flags: int = 0
    file_size: int = 0
    flags2: int = 0
    offset_a: int = 0
    magic2: int = 0
    words: List[int] = field(default_factory=list)

    def describe(self) -> str:
        return (f"magic={self.magic:#010x} version={self.version} flags={self.flags} "
                f"size={self.file_size} flags2={self.flags2:#010x} offset_a={self.offset_a:#010x} "
                f"magic2={self.magic2:#010x}")


class ClcFile:
    """Parsed `clc_*_cfg3.dat` front-end configuration."""

    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as fh:
            self.raw = fh.read()
        self.size = len(self.raw)
        if self.size < 32:
            raise ValueError(f"{path}: too small to be a clc config")
        words = [struct.unpack_from("<I", self.raw, i)[0] for i in range(0, min(64, self.size), 4)]
        self.header = ClcHeader(
            magic=words[0],
            version=struct.unpack_from("<H", self.raw, 4)[0],
            flags=struct.unpack_from("<H", self.raw, 6)[0],
            file_size=words[2],
            flags2=words[3],
            offset_a=words[4],
            magic2=words[6] if len(words) > 6 else 0,
            words=words,
        )
        if self.header.magic not in MAGICS:
            raise ValueError(f"{path}: unexpected magic {self.header.magic:#010x}")
        self.records = self._records()
        self.strings = self._length_prefixed_strings()
        self.rules = self._rule_lines()
        self.tags = self._tag_strings()

    # ------------------------------------------------------------------
    def _records(self) -> List[Tuple[str, int, int]]:
        """4-char tag followed by a plausible u32 length (payload may be data or strings)."""
        out = []
        for m in re.finditer(rb"[A-Za-z][A-Za-z0-9_]{3}", self.raw):
            off = m.start()
            if off + 8 > self.size:
                continue
            ln = struct.unpack_from("<I", self.raw, off + 4)[0]
            if 0 < ln <= self.size - (off + 8):
                out.append((m.group().decode("latin-1"), off, ln))
        return out

    def _length_prefixed_strings(self, min_len: int = 4) -> List[str]:
        """Printable ASCII runs — tags, language strings, symbol tables, rule text."""
        out, seen = [], set()
        for m in re.finditer(rb"[\x20-\x7e]{%d,}" % min_len, self.raw):
            s = m.group().decode("latin-1")
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out

    def _rule_lines(self, limit: int = 0) -> List[str]:
        out, seen = [], set()
        for m in RULE_RE.finditer(self.raw[:limit] if limit else self.raw):
            s = m.group().decode("latin-1")
            if s.count("'") == 1 and len(s) >= 5 and s not in seen:
                seen.add(s)
                out.append(s)
        return out

    def _tag_strings(self) -> List[str]:
        return sorted({m.group().decode("latin-1") for m in TAG_RE.finditer(self.raw)})

    # ------------------------------------------------------------------
    def named_records(self, names=("NVAR", "pipeline", "global", "silence")) -> Dict[str, List[int]]:
        out: Dict[str, List[int]] = {}
        for tag, off, ln in self.records:
            if tag in names:
                out.setdefault(tag, []).append(off)
        return out

    def voice_identity(self) -> Dict[str, str]:
        """Language/voice strings present near the header (e.g. `{'enu','samantha','1.00'}`)."""
        idents = {}
        for key in ("enu", "eng", "deu", "esp", "fra", "ita", "nld", "jpn", "ged", "frf", "dun", "iti"):
            if re.search(rb"\x00" + key.encode() + rb"\x00", self.raw):
                idents["langcode"] = key
                break
        for v in ("samantha", "serena"):
            if re.search(rb"\x00" + v.encode() + rb"\x00", self.raw):
                idents["voice"] = v
        m = re.search(rb"\x00(\d+\.\d\d)\x00", self.raw)
        if m:
            idents["version"] = m.group(1).decode()
        return idents

    def report(self) -> str:
        v = self.voice_identity()
        return (f"{self.path.rsplit('/', 1)[-1]}: {self.header.describe()} "
                f"| records={len(self.records)} strings={len(self.strings)} "
                f"rules={len(self.rules)} tags={self.tags} identity={v}")


def read_rules(path: str) -> List[str]:
    """Convenience: the letter-to-sound / lexicon rule lines from a clc file."""
    return ClcFile(path).rules
