"""Signal-processing front-end: 16-bit PCM → MFCC frames.

Mirrors the role (not the exact constants) of `mrec.dll`'s `sigproc/` modules:
`melfbank.cpp` (mel filterbank), `srfft.cpp` (FFT), `sputtdet.cpp` (speech/utterance
detection), `pitchtrk.cpp`, `featgen.cpp`/`featsel.cpp` (feature assembly), `chanadpt.cpp`.

Default parameters follow common DGN-era practice for 16 kHz dictation audio; the media also
ships 11 kHz models (`*_11k_p2.sig`), so the frame/window parameters are configurable.
"""

from __future__ import annotations

import array
import math
import struct
from dataclasses import dataclass
from typing import List, Sequence, Tuple

# ---------------------------------------------------------------------------
class WavFile:
    """Minimal RIFF/WAVE reader (PCM 16-bit mono/stereo; the recorder writes L16)."""

    def __init__(self, path: str):
        with open(path, "rb") as fh:
            data = fh.read()
        if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            raise ValueError(f"{path}: not a RIFF/WAVE file")
        pos, fmt, pcm = 12, None, b""
        while pos + 8 <= len(data):
            cid = data[pos:pos + 4]
            size = struct.unpack_from("<I", data, pos + 4)[0]
            chunk = data[pos + 8:pos + 8 + size]
            if cid == b"fmt ":
                fmt = struct.unpack_from("<HHIIHH", chunk, 0)
            elif cid == b"data":
                pcm = chunk
            pos += 8 + size + (size & 1)
        if fmt is None or not pcm:
            raise ValueError(f"{path}: missing fmt/data chunk")
        self.format_tag, self.channels, self.rate, self.byte_rate, self.align, self.bits = fmt
        self.pcm = pcm
        self.samples = self._decode(pcm)

    def _decode(self, pcm: bytes) -> array.array:
        if self.bits != 16:
            raise ValueError(f"only 16-bit PCM supported (got {self.bits})")
        a = array.array("h")
        a.frombytes(pcm[:len(pcm) // 2 * 2])
        if self.channels > 1:                       # downmix to mono
            mono = array.array("h")
            for i in range(0, len(a) - self.channels + 1, self.channels):
                mono.append(sum(a[i:i + self.channels]) // self.channels)
            return mono
        return a

    @property
    def duration(self) -> float:
        return len(self.samples) / float(self.rate or 1)


# ---------------------------------------------------------------------------
@dataclass
class MfccConfig:
    sample_rate: int = 16000
    frame_ms: float = 25.0
    hop_ms: float = 10.0
    num_filters: int = 26
    num_ceps: int = 13
    low_hz: float = 130.0
    high_hz: float = 6800.0
    preemphasis: float = 0.97
    lifter: int = 22
    use_deltas: bool = True
    energy_floor: float = 1.0
    cmn: bool = True                       # cepstral mean normalisation over the utterance


def _hamming(n: int) -> List[float]:
    return [0.54 - 0.46 * math.cos(2 * math.pi * i / (n - 1)) for i in range(n)]


def _fft_inplace(re: List[float], im: List[float]) -> None:
    """Iterative radix-2 FFT (n must be a power of two)."""
    n = len(re)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            re[i], re[j] = re[j], re[i]
            im[i], im[j] = im[j], im[i]
    length = 2
    while length <= n:
        ang = -2 * math.pi / length
        wr, wi = math.cos(ang), math.sin(ang)
        for i in range(0, n, length):
            cr, ci = 1.0, 0.0
            for k in range(i, i + length // 2):
                ur, ui = re[k], im[k]
                vr = re[k + length // 2] * cr - im[k + length // 2] * ci
                vi = re[k + length // 2] * ci + im[k + length // 2] * cr
                re[k], im[k] = ur + vr, ui + vi
                re[k + length // 2], im[k + length // 2] = ur - vr, ui - vi
                cr, ci = cr * wr - ci * wi, cr * wi + ci * wr
        length <<= 1


def hz_to_mel(hz: float) -> float:
    return 2595.0 * math.log10(1.0 + hz / 700.0)


def mel_to_hz(mel: float) -> float:
    return 700.0 * (10 ** (mel / 2595.0) - 1.0)


def mel_filterbank(cfg: MfccConfig, nfft: int) -> List[List[float]]:
    lo, hi = hz_to_mel(cfg.low_hz), hz_to_mel(cfg.high_hz)
    points = [mel_to_hz(lo + (hi - lo) * i / (cfg.num_filters + 1)) for i in range(cfg.num_filters + 2)]
    bins = [int(round(p / (cfg.sample_rate / 2.0) * (nfft // 2))) for p in points]
    fb = []
    for m in range(1, cfg.num_filters + 1):
        left, center, right = bins[m - 1], bins[m], bins[m + 1]
        row = [0.0] * (nfft // 2 + 1)
        for k in range(left, center):
            if center > left:
                row[k] = (k - left) / (center - left)
        for k in range(center, right):
            if right > center:
                row[k] = (right - k) / (right - center)
        fb.append(row)
    return fb


class Mfcc:
    """Stateful MFCC extractor (frame size / filterbank precomputed once)."""

    def __init__(self, cfg: MfccConfig = None):
        self.cfg = cfg or MfccConfig()
        self.frame = 1
        while self.frame < int(self.cfg.sample_rate * self.cfg.frame_ms / 1000.0):
            self.frame <<= 1
        self.hop = int(self.cfg.sample_rate * self.cfg.hop_ms / 1000.0)
        self.window = _hamming(self.frame)
        self.fb = mel_filterbank(self.cfg, self.frame)

    def frames(self, samples: Sequence[int]) -> List[List[float]]:
        cfg = self.cfg
        frames: List[List[float]] = []
        n = len(samples)
        prev = 0.0
        pre = [0.0] * n
        for i in range(n):
            cur = float(samples[i])
            pre[i] = cur - cfg.preemphasis * prev
            prev = cur
        start = 0
        while start + self.frame <= n:
            seg = pre[start:start + self.frame]
            re = [seg[i] * self.window[i] for i in range(self.frame)]
            im = [0.0] * self.frame
            _fft_inplace(re, im)
            half = self.frame // 2 + 1
            power = [re[i] * re[i] + im[i] * im[i] for i in range(half)]
            mel = []
            for row in self.fb:
                acc = 0.0
                for k, w in enumerate(row):
                    if w:
                        acc += w * power[k]
                mel.append(math.log(acc + cfg.energy_floor))
            # DCT-II
            ceps = []
            for c in range(cfg.num_ceps):
                acc = 0.0
                for m, v in enumerate(mel):
                    acc += v * math.cos(math.pi * c * (m + 0.5) / cfg.num_filters)
                ceps.append(acc)
            if cfg.lifter:
                ceps = [ceps[i] * (1 + 0.5 * cfg.lifter * math.sin(math.pi * i / cfg.lifter))
                        for i in range(len(ceps))]
            frames.append(ceps)
            start += self.hop
        if cfg.cmn and frames:
            statics = len(frames[0])
            n = len(frames)
            mean = [sum(f[i] for f in frames) / n for i in range(statics)]
            frames = [[f[i] - mean[i] for i in range(statics)] + list(f[statics:])
                      for f in frames]
        if cfg.use_deltas:
            frames = self._add_deltas(frames)
        return frames

    @staticmethod
    def _add_deltas(frames: List[List[float]], win: int = 2) -> List[List[float]]:
        if len(frames) < 2 * win + 1:
            return [f + [0.0] * len(f) for f in frames]
        denom = 2 * sum(i * i for i in range(1, win + 1))
        out = []
        for t in range(len(frames)):
            d = [0.0] * len(frames[t])
            for n in range(1, win + 1):
                a = frames[min(len(frames) - 1, t + n)]
                b = frames[max(0, t - n)]
                for k in range(len(d)):
                    d[k] += n * (a[k] - b[k])
            out.append(list(frames[t]) + [x / denom for x in d])
        return out

    # ------------------------------------------------------------------
    def extract(self, path: str) -> Tuple[List[List[float]], int]:
        wav = WavFile(path)
        if wav.rate != self.cfg.sample_rate:
            self.cfg.sample_rate = wav.rate
            self.__init__(self.cfg)              # rebuild filterbank for the actual rate
        return self.frames(wav.samples), wav.rate

    def extract_samples(self, samples: Sequence[int]) -> List[List[float]]:
        return self.frames(samples)

    def energies(self, samples: Sequence[int]) -> List[float]:
        """Frame RMS in dB, aligned with `frames()` (same frame/hop)."""
        return frame_energies(samples, self.frame, self.hop, self.cfg.preemphasis)


# ---------------------------------------------------------------------------
def log_energy(frames: List[List[float]]) -> List[float]:
    """Cepstral c0 per frame (crude); prefer `frame_energies` for utterance detection."""
    return [f[0] for f in frames]


def frame_energies(samples: Sequence[int], frame: int, hop: int,
                   preemphasis: float = 0.0, floor_db: float = -100.0) -> List[float]:
    """Per-frame RMS in dB — the quantity `sputtdet.cpp` thresholds on."""
    out: List[float] = []
    n = len(samples)
    prev = 0.0
    pre = []
    for i in range(n):
        cur = float(samples[i])
        pre.append(cur - preemphasis * prev)
        prev = cur
    start = 0
    while start + frame <= n:
        seg = pre[start:start + frame]
        ms = sum(x * x for x in seg) / frame
        db = 10.0 * math.log10(ms + 1e-12)
        out.append(max(db, floor_db))
        start += hop
    return out


def split_words(energies: Sequence[float], rel_db: float = 12.0, min_frames: int = 5,
                min_gap: int = 2) -> List[Tuple[int, int]]:
    """Split an utterance into word-sized spans at low-energy valleys.

    Simple stand-in for the engine's utterance/word segmentation (`sputtdet.cpp` +
    `recogctl/segment.cpp`): threshold relative to the utterance peak, then cut runs separated by
    silent gaps of at least ``min_gap`` frames.
    """
    if not energies:
        return []
    peak = max(energies)
    thr = peak - rel_db
    spans: List[Tuple[int, int]] = []
    start = None
    gap = 0
    for i, v in enumerate(energies):
        if v > thr:
            if start is None:
                start = i
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= min_gap:
                end = i - gap + 1
                if end - start >= min_frames:
                    spans.append((start, end))
                start, gap = None, 0
    if start is not None:
        end = len(energies)
        if end - start >= min_frames:
            spans.append((start, end))
    if len(spans) <= 1 and len(energies) >= min_frames:
        # no clear gaps: fall back to splitting on the strongest minima
        lo, hi = detect_speech(energies, rel_db=max(20.0, rel_db), min_frames=min_frames)
        return [(lo, hi)] if hi > lo else []
    return spans


def detect_speech(energy_or_frames, rel_db: float = 30.0, min_frames: int = 10) -> Tuple[int, int]:
    """Utterance bounds from either dB frame energies (`frame_energies`) or MFCC frames.

    Returns ``(start_frame, end_frame)``.
    """
    if not energy_or_frames:
        return 0, 0
    first = energy_or_frames[0]
    if isinstance(first, (int, float)):
        e = list(energy_or_frames)
        total = len(e)
    else:
        e = log_energy(list(energy_or_frames))
        total = len(e)
    peak = max(e)
    thr = peak - rel_db
    idx = [i for i, v in enumerate(e) if v > thr]
    if not idx:
        return 0, total
    lo, hi = idx[0], idx[-1] + 1
    if hi - lo < min_frames:
        hi = min(total, lo + min_frames)
    return lo, hi
