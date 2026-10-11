"""Front-end and model table reader for the TTS `.dat` assets.

Decodes the parts of `pp.dat` / `synth_*.dat` / `uselect_*.dat` that are not proprietary
coded audio:

* text chunks (`ICOP`, `ICRD`, `VERS`, `VINF`, `EXHD`, `BRKD`) — XOR 0xA9 coded strings;
* **escape/command schema** (`ESCD`, `EOSD` in `pp.dat`) — plainly readable names of the
  text-escape grammar the front end implements (`enhancedescseq`, `basicesc`,
  `basicescwitharg`, `esctoi`, `esccomment`, `typeofdocVDN`, `typeofinputVDN`, `posVDN`, …);
* **voice property catalogue** (`VCAT` in `uselect_*.dat`) — 4-character tags (`ALPH`, `VOIC`,
  `COVO`, `SAFE`, …) each followed by a length and a per-entry vector;
* numeric index tables (`PHIS`, `CNT0`, `TAB0`);
* **model geometry**: `CTUN` unit count and the exact per-unit record sizes of `UNIT`/`SYMB`/`TRI`.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import datfile

TEXT_CHUNKS = ("ICOP", "ICRD", "VERS", "VINF", "EXHD", "BRKD", "NAME", "DESC")
SCHEMA_CHUNKS = ("ESCD", "EOSD")
PROPERTY_CHUNKS = ("VCAT", "PREF", "TRAF", "FUZZ", "CTXR")
NUMERIC_CHUNKS = ("PHIS", "CNT0", "TAB0", "PCAR", "DCAR", "CCOS", "CDBK", "CODR")
MODEL_CHUNKS = ("UNIT", "SPCH", "SYMB", "TRI ")


@dataclass
class ChunkInfo:
    tag: str
    size: int
    encoding: str          # 'text' | 'binary' | 'schema'
    record_size: Optional[float] = None     # bytes per unit when it divides evenly


@dataclass
class VoiceGeometry:
    units: int = 0
    per_unit: Dict[str, float] = field(default_factory=dict)

    def describe(self) -> str:
        parts = ", ".join(f"{k.strip()}={v:.3f}B" for k, v in sorted(self.per_unit.items()))
        return f"units={self.units} ({parts})"


def _readable(bytes_: bytes, min_len: int = 3) -> List[str]:
    return [m.group().decode("latin-1")
            for m in re.finditer(rb"[\x20-\x7e]{%d,}" % min_len, bytes_)]


class VoiceTables:
    """Everything readable from one voice asset (or the shared `pp.dat`)."""

    def __init__(self, path: str):
        self.path = path
        self.dat = datfile.DatFile(path)
        self.chunks: List[ChunkInfo] = []
        self.strings: Dict[str, List[str]] = {}
        self.schema_names: List[str] = []
        self.properties: List[Tuple[str, int]] = []
        self.geometry = VoiceGeometry()
        self._classify()

    # ------------------------------------------------------------------
    def _classify(self) -> None:
        for c in self.dat.chunks:
            if c.size is None:
                continue
            if c.tag in TEXT_CHUNKS:
                enc = "text"
                # text-carrying chunks are XOR 0xA9 coded in full
                self.strings[c.tag] = _readable(datfile.deobfuscate(c.payload))
            elif c.tag in SCHEMA_CHUNKS:
                enc = "schema"
                names = _readable(c.payload)
                self.schema_names.extend(n for n in names if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,}", n))
            elif c.tag == "VCAT":
                enc = "text"
                self.properties = self._parse_vcat(c.payload)
            elif c.tag in MODEL_CHUNKS or c.tag in NUMERIC_CHUNKS:
                enc = "binary"
            else:
                enc = "binary"
            self.chunks.append(ChunkInfo(c.tag, c.size, enc))

        ctun = self.dat.get("CTUN")
        if ctun is not None and len(ctun.payload) >= 4:
            self.geometry.units = struct.unpack_from("<I", ctun.payload, 0)[0]
            if self.geometry.units:
                for c in self.dat.chunks:
                    if c.size and c.tag.strip() in ("UNIT", "SYMB", "TRI"):
                        per = c.size / self.geometry.units
                        if abs(per - round(per, 3)) < 1e-9:
                            self.geometry.per_unit[c.tag.strip()] = per

    @staticmethod
    def _parse_vcat(payload: bytes) -> List[Tuple[str, int]]:
        """`VCAT` = sequence of `TAG(4)` + obfuscated u32 length + vector(length bytes)."""
        out: List[Tuple[str, int]] = []
        p = 0
        while p + 8 <= len(payload):
            tag = datfile.deobfuscate(payload[p:p + 4])          # tag is XOR 0xA9 coded
            if not all(0x20 <= b <= 0x7e for b in tag):
                break
            size = struct.unpack_from("<I", payload, p + 4)[0]   # length is plaintext
            if size < 0 or p + 8 + size > len(payload):
                break
            out.append((tag.decode("latin-1"), size))
            p += 8 + size
        return out

    # ------------------------------------------------------------------
    def get(self, tag: str) -> Optional[bytes]:
        c = self.dat.get(tag)
        return c.payload if c else None

    def table_u32(self, tag: str, count: int = 16) -> List[int]:
        p = self.get(tag)
        if not p:
            return []
        return [struct.unpack_from("<I", p, i)[0] for i in range(0, min(len(p), 4 * count), 4)]

    def report(self) -> str:
        lines = [f"{self.path.rsplit('/',1)[-1]}: {len(self.chunks)} chunks"]
        for ci in self.chunks:
            extra = f" rec={ci.record_size:.3f}B" if ci.record_size else ""
            lines.append(f"  {ci.tag:6} {ci.size:>9} {ci.encoding}{extra}")
        if self.geometry.units:
            lines.append(f"  geometry: {self.geometry.describe()}")
        if self.schema_names:
            lines.append(f"  escape schema names: {sorted(set(self.schema_names))[:12]}")
        if self.properties:
            lines.append(f"  properties: {self.properties[:8]}")
        return "\n".join(lines)


def coder_geometry(synth_path: str) -> Dict[str, float]:
    """Derive the coder's frame geometry from `VINF` + `CTUN` + `SPCH`.

    `bitrate` (kb/s) and `frequencyhz` come from VINF/§5.4; `SPCH` bytes / unit count gives the
    coded frame size, and ``frames_per_second = bitrate/8/bytes_per_unit``.

    Measured (Serena `full_155mrf22`): 152.825 B/unit, 270 kb/s -> 220.84 frames/s -> one coded
    frame every ~4.53 ms (~100 samples at 22050 Hz), 276,866 units = 1,253.7 s (~20.9 min) of
    coded speech in the bank.  Interpretation "one coded frame per unit" is [inferred] from the
    constant bytes/unit; the measured facts are the constant records and the derived frame rate.
    """
    t = VoiceTables(synth_path)
    vinf = {}
    for pair in t.dat.keys("VINF"):
        vinf[pair[0]] = pair[1]
    units = t.geometry.units or 0
    spch = t.dat.get("SPCH")
    out: Dict[str, float] = {
        "units": units,
        "spch_bytes": spch.size if spch else 0,
        "frequency_hz": float(vinf.get("Freq", 22050) or 22050),
        "bitrate_kbps": float(vinf.get("BitRate", 0) or 0),
    }
    if units and spch:
        out["bytes_per_unit"] = spch.size / units
        if out["bitrate_kbps"] and out["bytes_per_unit"]:
            out["frames_per_second"] = out["bitrate_kbps"] * 1000.0 / 8.0 / out["bytes_per_unit"]
        if out.get("frames_per_second"):
            out["frame_ms"] = 1000.0 / out["frames_per_second"]
            out["bank_seconds"] = units / out["frames_per_second"]
    return out


def decode_escape_schema(pp_path: str) -> List[str]:
    """The front-end escape/command schema names declared in `pp.dat` (`ESCD`/`EOSD`)."""
    t = VoiceTables(pp_path)
    return sorted(set(t.schema_names))
