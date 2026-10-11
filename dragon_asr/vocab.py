"""Vocabulary / lexicon layer (mirrors `voc/` and `word/` in `mrec.dll`).

* `vocrejct.dat` — plain-text word-reject list shipped in every language pack (**readable**).
* `*.voc` — DFILE containers (`vocdelta.voc`, `pg.voc`, `dispatch.voc`, `spelling.voc`,
  `itn_si.voc`); bodies are Huffman/combined-table coded, so this module exposes the container
  and the word/pronunciation model, and can additionally load a plain word list so a rebuild has
  a working vocabulary without decoding the proprietary tables.
* pronunciations come from the shipped `dragon_tts` grapheme-to-phoneme rules (same phone set
  family the audio tokens use).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from .dfile import DFile

# Dragon phone inventory (ARPABET-like; matches the TTS front-end phone set used by the
# reference implementation and the `*.psh` phone-state files shipped per language).
PHONES = ["AA", "AE", "AH", "AO", "AW", "AY", "B", "CH", "D", "DH", "EH", "ER", "EY", "F", "G",
          "HH", "IH", "IY", "JH", "K", "L", "M", "N", "NG", "OW", "OY", "P", "R", "S", "SH",
          "T", "TH", "UH", "UW", "V", "W", "Y", "Z", "ZH", "SIL"]

VOWELS = {"AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY", "IH", "IY", "OW", "OY", "UH", "UW"}


@dataclass
class VocabEntry:
    word: str
    pronunciation: List[str]
    flags: int = 0
    source: str = ""


@dataclass
class Vocabulary:
    """Word list + pronunciations, optionally filtered by a rejection list."""

    entries: Dict[str, VocabEntry] = field(default_factory=dict)
    rejected: set = field(default_factory=set)
    rejected_phones: set = field(default_factory=set)

    # ------------------------------------------------------------------
    def add(self, word: str, pronunciation: Optional[Iterable[str]] = None,
            source: str = "", flags: int = 0) -> None:
        w = word.strip()
        if not w or w.lower() in self.rejected:
            return
        key = w.lower()
        if key in self.entries:
            return
        prons = list(pronunciation) if pronunciation else g2p(w)
        self.entries[key] = VocabEntry(word=w, pronunciation=prons, flags=flags, source=source)

    def load_words(self, path: str, source: str = "") -> int:
        """Load a plain word list (one word/phrase per line, `#` starts a comment)."""
        n = 0
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                for w in re.split(r"[^A-Za-z0-9'\-]+", line):
                    if w:
                        self.add(w, source=source or os.path.basename(path))
                        n += 1
        return n

    def load_reject(self, path: str) -> int:
        """Parse `vocrejct.dat`-style lists.

        The shipped files are *phoneme* rejection lists, one entry per line as
        ``SYMBOL\\phoneme\\?`` (older builds use whitespace separation); word lists written the
        same way are also accepted.  Returns the number of entries consumed.
        """
        n = 0
        with open(path, "r", encoding="latin-1", errors="replace") as fh:
            for line in fh:
                if line.startswith("##"):
                    continue
                line = line.rstrip("\r\n")
                if not line.strip():
                    continue
                head = re.split(r"[\\\s]", line.strip(), 1)[0]
                if not head or not re.fullmatch(r"[A-Za-z0-9'\-]{1,24}", head):
                    continue
                kind = ""
                if "\\" in line:
                    parts = line.split("\\")
                    kind = parts[1] if len(parts) > 1 else ""
                if kind.lower().startswith("phoneme") or (head.isupper() or len(head) <= 3):
                    self.rejected_phones.add(head)
                else:
                    self.rejected.add(head.lower())
                    self.entries.pop(head.lower(), None)
                n += 1
        return n

    def load_dfile_voc(self, path: str) -> int:
        """Register a DFILE `.voc` asset; words are recovered only if unencoded strings exist."""
        try:
            d = DFile(path)
        except ValueError:
            return 0
        n = 0
        for s in d.strings(min_len=3):
            if re.fullmatch(r"[A-Za-z][A-Za-z'\-]{1,23}", s):
                if s.lower() not in self.entries and s.lower() not in self.rejected:
                    self.add(s, source=os.path.basename(path))
                    n += 1
        return n

    # ------------------------------------------------------------------
    def words(self) -> List[str]:
        return [e.word for e in self.entries.values()]

    def pronunciation(self, word: str) -> Optional[List[str]]:
        e = self.entries.get(word.lower())
        return list(e.pronunciation) if e else None

    def __len__(self) -> int:
        return len(self.entries)


# ---------------------------------------------------------------------------
def g2p(word: str) -> List[str]:
    """Grapheme→phoneme via the shared English rules (see `dragon_tts.phonemes`)."""
    try:
        from dragon_tts.phonemes import word_to_phones
    except Exception:                       # standalone use
        return ["SIL"]
    phones = []
    for p in word_to_phones(word):
        base = p[:-1] if p and p[-1].isdigit() else p
        phones.append(base if base in PHONES else base)
    return phones or ["SIL"]


def phone_set_from_psh(path: str) -> List[str]:
    """Best-effort phone inventory probe from a `*.psh` (phone-state header) DFILE.

    The body is coded, so this returns the canonical inventory unless unencoded phone symbols
    are present (which happens for a few of the shipped files).
    """
    try:
        d = DFile(path)
    except ValueError:
        return list(PHONES)
    found = {s.upper() for s in d.strings(min_len=2)
             if re.fullmatch(r"[A-Z]{2,3}", s) and s.upper() in set(PHONES)}
    return sorted(found) if len(found) >= 8 else list(PHONES)


def load_language_pack(datapack_dir: str) -> Vocabulary:
    """Load every recognisable vocabulary asset from a `Datapack/<lang>/...` tree."""
    vocab = Vocabulary()
    for root, _dirs, files in os.walk(datapack_dir):
        for fn in files:
            p = os.path.join(root, fn)
            low = fn.lower()
            try:
                if low == "vocrejct.dat":
                    vocab.load_reject(p)
                elif low.endswith(".voc"):
                    vocab.load_dfile_voc(p)
            except Exception:
                continue
    return vocab
