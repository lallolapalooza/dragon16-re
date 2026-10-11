"""Waveform synthesis (the `synth` / `xcoder` / `audioinserter` seam).

Default implementation: a source-filter (Klatt-style) formant synthesiser producing
22050 Hz, 16-bit, mono PCM — the format declared by the voice manifests
(`audio/L16;rate=22050`, `audiooutputsamplebits=16`).

The proprietary coder (`155mrf22`) and its data (`CODR`/`CDBK`/`SPCH`/`UNIT`) are NOT decoded;
`CoderSynthesiser` documents that seam: a faithful implementation replaces `render()` and may use
`voicebank` chunk access to drive the real decoder.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# phone descriptions: (F1, F2, F3, bandwidth-scale, voiced, class)
# values in Hz for an adult female-ish voice; class in {vowel, nasal, liquid, fricative,
# plosive, affricate, silence, separator}
PHONES: Dict[str, Tuple[float, float, float, float, str, str]] = {
    "IY": (300, 2300, 3000, 1.0, "vowel", "v"), "IH": (400, 1900, 2600, 1.0, "vowel", "v"),
    "EH": (550, 1800, 2500, 1.0, "vowel", "v"), "AE": (700, 1700, 2450, 1.0, "vowel", "v"),
    "AA": (750, 1200, 2500, 1.0, "vowel", "v"), "AO": (500, 900, 2400, 1.0, "vowel", "v"),
    "UH": (450, 1100, 2350, 1.0, "vowel", "v"), "UW": (320, 800, 2300, 1.0, "vowel", "v"),
    "AH": (600, 1200, 2400, 1.0, "vowel", "v"), "ER": (480, 1350, 1600, 1.0, "vowel", "v"),
    "AY": (700, 1200, 2450, 1.0, "vowel", "v"), "EY": (450, 1900, 2500, 1.0, "vowel", "v"),
    "OW": (450, 900, 2400, 1.0, "vowel", "v"), "OY": (550, 900, 2400, 1.0, "vowel", "v"),
    "AW": (700, 1200, 2400, 1.0, "vowel", "v"),
    "M": (250, 1100, 2200, 1.2, "voiced", "n"), "N": (250, 1600, 2500, 1.2, "voiced", "n"),
    "NG": (250, 2000, 2600, 1.2, "voiced", "n"),
    "L": (350, 1100, 2600, 1.0, "voiced", "l"), "R": (450, 1300, 1600, 1.0, "voiced", "l"),
    "W": (300, 700, 2200, 1.0, "voiced", "l"), "Y": (300, 2200, 3000, 1.0, "voiced", "l"),
    "B": (300, 1000, 2400, 1.0, "voiced", "p"), "D": (400, 1700, 2600, 1.0, "voiced", "p"),
    "G": (300, 2000, 2600, 1.0, "voiced", "p"), "P": (400, 1000, 2400, 1.0, "unvoiced", "p"),
    "T": (400, 1700, 2600, 1.0, "unvoiced", "p"), "K": (400, 2000, 2600, 1.0, "unvoiced", "p"),
    "F": (400, 1400, 2400, 1.5, "unvoiced", "f"), "TH": (400, 1600, 2500, 1.5, "unvoiced", "f"),
    "S": (500, 4000, 5500, 2.0, "unvoiced", "f"), "SH": (500, 2500, 3500, 2.0, "unvoiced", "f"),
    "Z": (400, 3500, 5000, 1.8, "voiced", "f"), "V": (400, 1400, 2400, 1.5, "voiced", "f"),
    "DH": (400, 1600, 2500, 1.5, "voiced", "f"), "HH": (500, 1500, 2500, 2.0, "unvoiced", "f"),
    "CH": (400, 2200, 3000, 2.0, "unvoiced", "a"), "JH": (400, 2200, 3000, 2.0, "voiced", "a"),
    "_": (500, 1500, 2500, 1.0, "silence", "s"), ".": (500, 1500, 2500, 1.0, "silence", "s"),
}

BASE_DURATION = {          # seconds, pre-stress
    "v": 0.090, "n": 0.070, "l": 0.065, "f": 0.085, "p": 0.060, "a": 0.090, "s": 0.130,
}
TERMINATOR_PAUSE = {"": 0.0, ",": 0.18, ";": 0.22, ":": 0.22, ".": 0.35, "?": 0.35, "!": 0.35}

TWO_PI = 2.0 * math.pi


def _resonator(freq: float, bw: float, fs: int):
    """Klatt resonator coefficients (y = a*x + b*y1 + c*y2)."""
    f = max(50.0, min(freq, fs * 0.45))
    c = -math.exp(-TWO_PI * bw / fs)
    b = 2.0 * math.exp(-math.pi * bw / fs) * math.cos(TWO_PI * f / fs)
    a = 1.0 - b - c
    return a, b, c


@dataclass
class RenderRequest:
    phones: Sequence[Tuple[str, str]]      # [(phone, terminator)]
    fs: int = 22050
    speed: float = 1.0                     # >1 faster
    pitch: float = 1.0                     # >1 higher
    volume: float = 0.8                    # 0..1
    base_f0: float = 185.0                 # Hz (female-ish default)


class Synthesiser:
    """Interface a replacement synthesizer must implement."""

    name = "abstract"

    def render(self, req: RenderRequest) -> bytes:
        raise NotImplementedError


class FormantSynthesiser(Synthesiser):
    """Source-filter formant synthesiser (default stand-in for coder `155mrf22`)."""

    name = "formant"

    def render(self, req: RenderRequest) -> bytes:
        fs = req.fs
        out: List[float] = []
        rng = _Lcg(0x1234ABCD)
        f0 = req.base_f0 * req.pitch
        total_voiced = max(1, sum(1 for p, _ in req.phones if p and p != "."))
        idx = 0
        for phone, term in req.phones:
            base = phone[:-1] if phone and phone[-1].isdigit() else phone
            stress = int(phone[-1]) if phone and phone[-1].isdigit() else 0
            spec = PHONES.get(base)
            if spec is None:
                continue
            f1, f2, f3, bwscale, voicing, cls = spec
            dur = BASE_DURATION.get(cls, 0.08)
            if cls == "v":
                dur *= 1.0 + 0.45 * stress
            dur /= max(0.35, req.speed)
            n = int(dur * fs)
            # declination: f0 falls across the sentence, rises slightly on stressed vowels
            decl = 1.0 - 0.25 * (idx / total_voiced)
            local_f0 = f0 * decl * (1.15 if (cls == "v" and stress == 1) else 1.0)
            if cls == "s":
                out.extend([0.0] * n)
                out.extend([0.0] * int(TERMINATOR_PAUSE.get(term, 0.0) * fs))
                idx += 1
                continue
            bw = 70.0 * bwscale
            a1, b1, c1 = _resonator(f1, bw, fs)
            a2, b2, c2 = _resonator(f2, bw * 1.1, fs)
            a3, b3, c3 = _resonator(f3, bw * 1.3, fs)
            y1 = y2 = 0.0
            z1 = z2 = z3 = z4 = z5 = z6 = 0.0
            phase = 0.0
            noise_lp = 0.0
            amp = 1.0 if voicing == "voiced" else 0.0
            for i in range(n):
                t = i / max(1, n - 1)
                env = min(1.0, 8 * t, 8 * (1 - t)) if cls != "p" else min(1.0, 4 * t, 6 * (1 - t))
                if voicing == "voiced":
                    # glottal pulse train with jitter
                    jitter = 1.0 + 0.004 * (rng.rand() - 0.5)
                    phase += local_f0 * jitter / fs
                    if phase >= 1.0:
                        phase -= 1.0
                        y1 = 1.0
                    else:
                        y1 = 0.0
                    src = y1 - 0.97 * y2
                    y2 = y1
                else:
                    src = (rng.rand() * 2.0 - 1.0) * 0.6
                if cls == "p" and t > 0.55:      # stop closure, then release burst handled below
                    src *= 0.15
                if voicing == "voiced" and cls == "f":
                    src = src * 0.4 + (rng.rand() * 2 - 1) * 0.25
                o1 = a1 * src + b1 * z1 + c1 * z2
                z2, z1 = z1, o1
                o2 = a2 * o1 + b2 * z3 + c2 * z4
                z4, z3 = z3, o2
                o3 = a3 * o2 + b3 * z5 + c3 * z6
                z6, z5 = z5, o3
                out.append(o3 * env * amp * 3000.0)
            idx += 1
            if term:
                out.extend([0.0] * int(TERMINATOR_PAUSE.get(term, 0.0) * fs))
                rng_seed = rng.rand()  # keep stream moving
                del rng_seed
        return _to_pcm16(out, req.volume)

    def to_phones_then_render(self, text: str, **kw) -> bytes:
        from .phonemes import text_to_phones
        return self.render(RenderRequest(phones=text_to_phones(text), **kw))


class CoderSynthesiser(Synthesiser):
    """Documented seam for the original coder.

    A faithful rebuild decodes `SPCH` frames with the `CODR` codebook (`155mrf22`, driven by
    `UNIT` geometry / `CTUN` count) as `rsstdv2_dec11.dll!gsmc_GetInterface` does.  Hook that
    here; everything else in the pipeline is already byte-compatible.
    """

    name = "155mrf22"

    def __init__(self, voicebank=None):
        self.voicebank = voicebank

    def render(self, req: RenderRequest) -> bytes:
        raise NotImplementedError(
            "coder 155mrf22 not implemented: substitute FormantSynthesiser or port the decoder "
            "(see SPEC_TTS.md §8)")


# ---------------------------------------------------------------------------
class _Lcg:
    """Tiny deterministic PRNG (no dependency on the host RNG, reproducible tests)."""

    def __init__(self, seed: int = 1):
        self.state = seed & 0xFFFFFFFF

    def rand(self) -> float:
        self.state = (1103515245 * self.state + 12345) & 0x7FFFFFFF
        return self.state / 0x7FFFFFFF


def _to_pcm16(samples: Sequence[float], volume: float = 0.8) -> bytes:
    peak = max((abs(s) for s in samples), default=1.0) or 1.0
    scale = (32767.0 * max(0.0, min(1.0, volume))) / peak
    buf = bytearray()
    for s in samples:
        v = int(max(-32768, min(32767, s * scale)))
        buf += struct.pack("<h", v)
    return bytes(buf)


def pcm_to_wav(pcm: bytes, fs: int = 22050, channels: int = 1, bits: int = 16) -> bytes:
    """Minimal RIFF/WAVE writer (matches `lhs_ttsGenOutputFile`)."""
    byte_rate = fs * channels * bits // 8
    block_align = channels * bits // 8
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + \
        struct.pack("<IHHIIHH", 16, 1, channels, fs, byte_rate, block_align, bits) + \
        b"data" + struct.pack("<I", len(pcm))
    return header + pcm
