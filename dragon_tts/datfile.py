"""Reader for Nuance RealSpeak/Vocalizer `.dat` assets.

Format (see SPEC_TTS.md §6):

    file  := b'\\xfb\\xe0\\xef\\x9d' + u32le(body_len) + body
    body  := banner(8) + u32le(banner_len) + banner_payload
             then a flat sequence of chunks
    chunk := tag(4) + u32le(size) + payload(size)

Obfuscation rules (verified):
  * tag bytes                       -> XOR 0xA9
  * string payloads                 -> XOR 0xA9
  * u32 size fields / numeric fields-> plaintext
  * a tag immediately followed by another valid tag is a container *label* with no size field.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple

MAGIC = b"\xfb\xe0\xef\x9d"
KEY = 0xA9


def deobfuscate(data: bytes) -> bytes:
    """XOR every byte with 0xA9 (used for tags and string payloads)."""
    return bytes(b ^ KEY for b in data)


def looks_like_text(data: bytes, threshold: float = 0.85) -> bool:
    if not data:
        return False
    good = sum(1 for b in data if 0x20 <= b <= 0x7E or b in (0, 9, 10, 13))
    return good >= threshold * len(data)


def decode_payload(payload: bytes) -> bytes:
    """Return the human-readable form of a payload when it is text-like."""
    if looks_like_text(payload):
        return payload
    if looks_like_text(deobfuscate(payload)):
        return deobfuscate(payload)
    return payload


def _tag_at(buf: bytes, off: int) -> Optional[str]:
    if off + 4 > len(buf):
        return None
    cand = deobfuscate(buf[off:off + 4])
    if all(0x20 <= b <= 0x7E for b in cand):
        return cand.decode("latin-1")
    return None


@dataclass(frozen=True)
class Chunk:
    tag: str
    size: Optional[int]      # None for container labels
    offset: int              # offset of payload within the body
    payload: bytes

    @property
    def text(self) -> bytes:
        """Payload decoded as text when applicable."""
        return decode_payload(self.payload)

    def keys(self) -> List[Tuple[str, str]]:
        """NUL-separated key/value pairs (e.g. VINF payloads)."""
        parts = [p for p in self.text.split(b"\x00") if p]
        return [(parts[i].decode("latin-1", "replace"), parts[i + 1].decode("latin-1", "replace"))
                for i in range(0, len(parts) - 1, 2)]


def scan(buf: bytes, off: int = 0, end: Optional[int] = None) -> List[Chunk]:
    """Flat chunk scan of ``buf[off:end]``."""
    if end is None:
        end = len(buf)
    out: List[Chunk] = []
    p = off
    while p + 4 <= end:
        tag = _tag_at(buf, p)
        if tag is None:
            if buf[p] in (0x00, KEY):          # padding
                p += 1
                continue
            break
        if _tag_at(buf, p + 4) is not None:    # container label, no size field
            out.append(Chunk(tag, None, p + 4, b""))
            p += 4
            continue
        if p + 8 > end:
            break
        size = struct.unpack_from("<I", buf, p + 4)[0]
        if p + 8 + size > end or size > len(buf):
            break
        out.append(Chunk(tag, size, p + 8, buf[p + 8:p + 8 + size]))
        p += 8 + size
    return out


class DatFile:
    """A parsed `.dat` asset."""

    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as fh:
            raw = fh.read()
        if raw[:4] != MAGIC:
            raise ValueError(f"{path}: not a Nuance .dat file (magic {raw[:4].hex()})")
        self.body_len = struct.unpack_from("<I", raw, 4)[0]
        self.body = raw[8:8 + self.body_len]
        self.banner = deobfuscate(self.body[:8]).rstrip(b"\x00").decode("latin-1")
        self.banner_size = struct.unpack_from("<I", self.body, 8)[0]
        self.container = self.banner[-4:]
        self.chunks: List[Chunk] = scan(self.body, 12, len(self.body))

    # -- access helpers -------------------------------------------------
    def get(self, tag: str) -> Optional[Chunk]:
        for c in self.chunks:
            if c.tag == tag:
                return c
        return None

    def sub(self, tag: str) -> List[Chunk]:
        """Parse a payload as a nested chunk list (best effort)."""
        c = self.get(tag)
        if c is None or not c.size:
            return []
        return scan(c.payload, 0, len(c.payload))

    def labels(self) -> List[str]:
        return [c.tag for c in self.chunks if c.size is None]

    def keys(self, tag: str = "VINF") -> List[Tuple[str, str]]:
        c = self.get(tag)
        return c.keys() if c else []

    def inventory(self) -> List[Tuple[str, Optional[int]]]:
        return [(c.tag, c.size) for c in self.chunks]

    def __iter__(self) -> Iterator[Chunk]:
        return iter(self.chunks)

    def __repr__(self) -> str:
        return f"<DatFile {self.path.rsplit('/', 1)[-1]} banner={self.banner!r} chunks={len(self.chunks)}>"
