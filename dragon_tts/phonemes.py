"""Front-end: text normalisation and grapheme-to-phoneme.

This is a stand-in for the `fe_*` / `tokentn` / `phonmap` component chain.  It produces an
ARPABET-like phone list with stress marks, which the synthesiser consumes.  The interface is
what matters: `Pipeline` calls exactly these stages, in the order named in the manifest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# small hand-written lexicon for high-frequency words (grapheme -> phones)
LEXICON: Dict[str, str] = {
    "a": "AH0", "an": "AE1 N", "the": "DH0 AH0", "to": "T UW1", "of": "AH1 V",
    "and": "AH0 N D", "in": "IH0 N", "is": "IH1 Z", "it": "IH1 T", "you": "Y UW1",
    "that": "DH AE1 T", "he": "HH IY1", "was": "W AH1 Z", "for": "F AO1 R",
    "on": "AA1 N", "are": "AA1 R", "as": "AE1 Z", "with": "W IH1 DH",
    "his": "HH IH1 Z", "they": "DH EY1", "I": "AY1", "at": "AE1 T", "be": "B IY1",
    "this": "DH IH1 S", "have": "HH AE1 V", "from": "F R AH1 M", "or": "AO1 R",
    "one": "W AH1 N", "had": "HH AE1 D", "by": "B AY1", "word": "W ER1 D",
    "but": "B AH1 T", "not": "N AA1 T", "what": "W AH1 T", "all": "AO1 L",
    "were": "W ER1", "we": "W IY1", "when": "W EH1 N", "your": "Y AO1 R",
    "can": "K AE1 N", "said": "S EH1 D", "there": "DH EH1 R", "use": "Y UW1 Z",
    "each": "IY1 CH", "which": "W IH1 CH", "she": "SH IY1", "do": "D UW1",
    "how": "HH AW1", "their": "DH EH1 R", "if": "IH1 F", "will": "W IH1 L",
    "up": "AH1 P", "other": "AH1 DH ER0", "about": "AH0 B AW1 T", "out": "AW1 T",
    "many": "M EH1 N IY0", "then": "DH EH1 N", "them": "DH EH1 M", "these": "DH IY1 Z",
    "so": "S OW1", "some": "S AH1 M", "her": "HH ER1", "would": "W UH1 D",
    "make": "M EY1 K", "like": "L AY1 K", "him": "HH IH1 M", "into": "IH1 N T UW0",
    "time": "T AY1 M", "has": "HH AE1 Z", "look": "L UH1 K", "two": "T UW1",
    "more": "M AO1 R", "write": "R AY1 T", "go": "G OW1", "see": "S IY1",
    "number": "N AH1 M B ER0", "no": "N OW1", "way": "W EY1", "could": "K UH1 D",
    "people": "P IY1 P AH0 L", "my": "M AY1", "than": "DH AE1 N", "first": "F ER1 S T",
    "water": "W AO1 T ER0", "been": "B IH1 N", "call": "K AO1 L", "who": "HH UW1",
    "oil": "OY1 L", "now": "N AW1", "find": "F AY1 N D", "long": "L AO1 NG",
    "down": "D AW1 N", "day": "D EY1", "did": "D IH1 D", "get": "G EH1 T",
    "come": "K AH1 M", "made": "M EY1 D", "may": "M EY1", "part": "P AA1 R T",
    "dragon": "D R AE1 G AH0 N", "speech": "S P IY1 CH", "text": "T EH1 K S T",
    "hello": "HH AH0 L OW1", "world": "W ER1 L D", "computer": "K AH0 M P Y UW1 T ER0",
    "testing": "T EH1 S T IH0 NG", "voice": "V OY1 S", "system": "S IH1 S T AH0 M",
    "samantha": "S AH0 M AE1 N TH AH0", "serena": "S ER0 IY1 N AH0",
}

# grapheme rules, applied longest-first
RULES: List[Tuple[str, str]] = [
    ("tion", "SH AH0 N"), ("sion", "ZH AH0 N"), ("ough", "AH1 F"), ("augh", "AO1"),
    ("ch", "CH"), ("sh", "SH"), ("th", "TH"), ("ph", "F"), ("wh", "W"), ("ck", "K"),
    ("ng", "NG"), ("qu", "K W"), ("ee", "IY1"), ("ea", "IY1"), ("oo", "UW1"),
    ("ou", "AW1"), ("ow", "OW1"), ("ai", "EY1"), ("ay", "EY1"), ("oi", "OY1"),
    ("oy", "OY1"), ("ar", "AA1 R"), ("er", "ER0"), ("ir", "ER0"), ("ur", "ER0"),
    ("or", "AO1 R"),
]

VOWELS = set("AEIOUY")
SINGLE = {
    "a": "AE1", "b": "B", "c": "K", "d": "D", "e": "EH1", "f": "F", "g": "G",
    "h": "HH", "i": "IH1", "j": "JH", "k": "K", "l": "L", "m": "M", "n": "N",
    "o": "AA1", "p": "P", "q": "K", "r": "R", "s": "S", "t": "T", "u": "AH1",
    "v": "V", "w": "W", "x": "K S", "y": "Y", "z": "Z",
}

DIGITS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
    "6": "six", "7": "seven", "8": "eight", "9": "nine", "10": "ten",
    "11": "eleven", "12": "twelve", "13": "thirteen", "14": "fourteen",
    "15": "fifteen", "16": "sixteen", "17": "seventeen", "18": "eighteen",
    "19": "nineteen", "20": "twenty", "30": "thirty", "40": "forty",
    "50": "fifty", "60": "sixty", "70": "seventy", "80": "eighty", "90": "ninety",
}

ABBREV = {"mr.": "mister", "mrs.": "missus", "dr.": "doctor", "st.": "street",
          "vs.": "versus", "etc.": "et cetera", "e.g.": "for example",
          "i.e.": "that is"}

TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


# ---------------------------------------------------------------------------
def number_to_words(n: int) -> str:
    if n < 0:
        return "minus " + number_to_words(-n)
    if n < 20:
        return DIGITS[str(n)]
    if n < 100:
        return TENS[n // 10] + ((" " + DIGITS[str(n % 10)]) if n % 10 else "")
    if n < 1000:
        return DIGITS[str(n // 100)] + " hundred" + ((" " + number_to_words(n % 100)) if n % 100 else "")
    for base, name in ((1_000_000_000, "billion"), (1_000_000, "million"), (1_000, "thousand")):
        if n >= base:
            return number_to_words(n // base) + " " + name + (
                (" " + number_to_words(n % base)) if n % base else "")
    return str(n)


def normalise(text: str) -> str:
    """tokentn / fe_normout stand-in: expand numbers, abbreviations, currency, symbols."""
    t = text
    for k, v in ABBREV.items():
        t = re.sub(r"\b" + re.escape(k), v + " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\$(\d[\d,]*)(?:\.(\d\d))?", lambda m: number_to_words(int(m.group(1).replace(",", "")))
               + (" dollars" if not m.group(2) else " dollars and " + number_to_words(int(m.group(2)))
                  + " cents"), t)
    t = re.sub(r"\b(\d[\d,]*)\b", lambda m: number_to_words(int(m.group(1).replace(",", ""))), t)
    t = t.replace("&", " and ").replace("%", " percent").replace("+", " plus ").replace("=", " equals ")
    t = re.sub(r"[^\w\s'.,;:!?()-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def split_phrases(text: str) -> List[Tuple[str, str]]:
    """fe_phrasing stand-in -> [(phrase_text, terminator)] where terminator in {'', '.', ',', '?', '!'}"""
    out: List[Tuple[str, str]] = []
    for part in re.findall(r"[^.,;:!?]+[.,;:!?]*", text):
        part = part.strip()
        if not part:
            continue
        term = part[-1] if part[-1] in ".,;:!?" else ""
        out.append((part[:-1].strip() if term else part, term))
    return out


def word_to_phones(word: str) -> List[str]:
    """phonmap stand-in (rule based)."""
    w = re.sub(r"[^a-z']", "", word.lower())
    if not w:
        return []
    if w in LEXICON:
        return LEXICON[w].split()
    if w.endswith("ing") and len(w) > 5:
        return word_to_phones(w[:-3])[:-1] + ["IH0", "NG"]
    if w.endswith("ed") and len(w) > 4:
        return word_to_phones(w[:-2]) + ["D"]
    if w.endswith("s") and len(w) > 3 and not w.endswith("ss"):
        base = word_to_phones(w[:-1])
        last = base[-1] if base else ""
        return base + (["IH0", "Z"] if last and last[-1] in "SZCJH" else ["Z"])
    phones: List[str] = []
    i = 0
    while i < len(w):
        for g, p in RULES:
            if w.startswith(g, i):
                phones.extend(p.split())
                i += len(g)
                break
        else:
            ch = w[i]
            if ch in SINGLE:
                phones.extend(SINGLE[ch].split())
            i += 1
    if not phones:
        phones = ["AH1"]
    if not any(p[-1].isdigit() for p in phones):
        phones[0] = phones[0] + "1"
    return phones


def text_to_phones(text: str) -> List[Tuple[str, str]]:
    """Full front-end: returns [(phone, terminator)] with a stress digit on vowels."""
    out: List[Tuple[str, str]] = []
    for phrase, term in split_phrases(normalise(text)):
        for word in phrase.split():
            for p in word_to_phones(word):
                out.append((p, ""))
            out.append(("_", ""))       # word separator
        if term:
            out.append((".", term))
    return out
