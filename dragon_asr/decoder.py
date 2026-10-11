"""Search/decoding layer: template DTW plus lattice/result modelling.

Names mirror `mrec.dll`'s `kernel/` (lattice, wordlat, sausage, recog, searchlm) and
`recogctl/` (recogctl, recogres) roles so that a port of the real decoder can slot in.

The shipped engine is a large-vocabulary DNN/HMM recogniser driven by transducer (FST) search;
`MFCC + DTW over word templates` is the reference substitute that keeps the API, the result
model and the confidence/lattice surface identical while remaining runnable without the
proprietary model weights.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
def cmn(frames: Sequence[Sequence[float]]) -> List[List[float]]:
    """Cepstral mean normalisation over a frame sequence (statics only).

    Applied to both templates and search windows so that channel/utterance differences do not
    dominate the distance (the engine does the equivalent in `sigproc/chanadpt.cpp`).
    """
    if not frames:
        return []
    n = len(frames)
    dim = len(frames[0])
    statics = max(1, dim // 2) if dim > 13 else dim
    mean = [sum(f[i] for f in frames) / n for i in range(statics)]
    return [[f[i] - mean[i] if i < statics else f[i] for i in range(dim)] for f in frames]


def _dist(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((x - y) * (x - y) for x, y in zip(a, b)))


def dtw(ref: Sequence[Sequence[float]], hyp: Sequence[Sequence[float]],
        band: Optional[float] = None) -> Tuple[float, List[Tuple[int, int]]]:
    """Sakoe-Chiba banded DTW.

    Returns ``(total cost, alignment path)`` where the cost is the sum of frame distances over the
    optimal path (length-proportional, so segmentations can be compared in a DP).
    Use :func:`dtw_norm` for a length-normalised distance.
    """
    n, m = len(ref), len(hyp)
    if not n or not m:
        return float("inf"), []
    if band is None:
        band = max(n, m)
    inf = float("inf")
    prev = [inf] * (m + 1)
    prev[0] = 0.0
    paths: List[List[Optional[Tuple[int, int]]]] = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cur = [inf] * (m + 1)
        lo = max(1, int(i - band))
        hi = min(m, int(i + band))
        for j in range(lo, hi + 1):
            cost = _dist(ref[i - 1], hyp[j - 1])
            best = prev[j]
            bp = (i - 1, j)
            if cur[j - 1] < best:
                best, bp = cur[j - 1], (i, j - 1)
            if prev[j - 1] < best:
                best, bp = prev[j - 1], (i - 1, j - 1)
            cur[j] = cost + best
            paths[i][j] = bp
        prev = cur
    total = prev[m]
    if total == inf:
        return inf, []
    path, i, j = [], n, m
    while i > 0 and j > 0:
        path.append((i - 1, j - 1))
        p = paths[i][j]
        if p is None:
            break
        i, j = p
    return total, list(reversed(path))


def dtw_norm(ref: Sequence[Sequence[float]], hyp: Sequence[Sequence[float]],
             band: Optional[float] = None) -> float:
    """Length-normalised DTW distance (cost per reference frame)."""
    cost, path = dtw(ref, hyp, band)
    if cost == float("inf"):
        return cost
    return cost / max(1, len(ref))


# ---------------------------------------------------------------------------
@dataclass
class Template:
    """One dictionary entry: word + pronunciation + acoustic template."""

    word: str
    phones: List[str]
    frames: List[List[float]]


@dataclass
class Phrase:
    """One recognised phrase (mirrors `kernel/wordlat` + `recogctl/recogres` entries)."""

    text: str
    score: float
    start_frame: int
    end_frame: int
    words: List[Tuple[str, int, int, float]] = field(default_factory=list)

    conf_scale: float = 60.0

    @property
    def confidence(self) -> float:
        """Exponential confidence over the normalised acoustic cost (0 cost -> 1.0)."""
        return max(0.0, min(1.0, math.exp(-abs(self.score) / max(1e-6, self.conf_scale))))

    def as_dict(self) -> Dict:
        return {"text": self.text, "score": self.score, "confidence": self.confidence,
                "start_frame": self.start_frame, "end_frame": self.end_frame,
                "words": [{"word": w, "start": a, "end": b, "score": s}
                          for w, a, b, s in self.words]}


@dataclass
class Result:
    """Recognition result (mirrors `SDRecogResult` / `IDgnResult`)."""

    phrases: List[Phrase] = field(default_factory=list)
    duration_frames: int = 0

    @property
    def text(self) -> str:
        return " ".join(p.text for p in self.phrases)

    def as_dict(self) -> Dict:
        return {"text": self.text, "duration_frames": self.duration_frames,
                "phrases": [p.as_dict() for p in self.phrases]}


# ---------------------------------------------------------------------------
class Decoder:
    """Segmental DTW decoder over a template vocabulary.

    ``grammar`` optionally restricts the word sequence (mirrors `recogmode.grm/.grx` grammars and
    the LM's role): pass a list of allowed phrases, or a weight map ``{word: weight}``.
    """

    def __init__(self, templates: Iterable[Template], grammar: Optional[Iterable[str]] = None,
                 word_penalty: float = 0.0, conf_scale: float = 60.0):
        self.conf_scale = conf_scale
        self._norm_cache: Dict[int, List[List[float]]] = {}
        self.templates: List[Template] = [t for t in templates if t.frames]
        self.word_penalty = word_penalty
        self.grammar: Optional[List[str]] = None
        if grammar:
            self.grammar = sorted({w.lower() for phr in grammar for w in phr.split()})
            self.templates = [t for t in self.templates if t.word.lower() in self.grammar]

    # ------------------------------------------------------------------
    def decode(self, frames: List[List[float]], max_words: int = 24) -> Result:
        if not frames or not self.templates:
            return Result([], len(frames))
        # segmental search: at each frame choose the best (template, length) continuation
        n = len(frames)
        inf = float("inf")
        cost = [inf] * (n + 1)
        back: List[Optional[Tuple[int, Template, float]]] = [None] * (n + 1)
        cost[0] = 0.0
        factors = tuple(0.5 + 0.1 * i for i in range(16))     # 0.5 … 2.0
        for start in range(n):
            if cost[start] == inf:
                continue
            for tmpl in self.templates:
                tlen = len(tmpl.frames)
                if tlen == 0:
                    continue
                for f in factors:
                    end = min(n, start + max(1, int(tlen * f)))
                    seg = frames[start:end]
                    if len(seg) < 3:
                        continue
                    # the band must at least absorb the length difference, or DTW has no path
                    band = max(4, abs(tlen - len(seg)) + int(0.3 * max(tlen, len(seg))))
                    d, _ = dtw(tmpl.frames, seg, band=band)
                    if d == inf:
                        continue
                    c = cost[start] + d + self.word_penalty
                    if c < cost[end]:
                        cost[end] = c
                        back[end] = (start, tmpl, d)
        # walk back from the best reachable end
        end = min(range(n + 1), key=lambda i: cost[i] if i > 0 else inf) if cost[n] == inf else n
        if cost[end] == inf:
            return Result([], n)
        words: List[Tuple[str, int, int, float]] = []
        idx = end
        while idx > 0 and back[idx] is not None:
            start, tmpl, d = back[idx]
            words.append((tmpl.word, start, idx, d))
            idx = start
        words.reverse()
        if not words:
            return Result([], n)
        covered = max(1, words[-1][2] - words[0][1])
        score = cost[end] / covered          # per-frame acoustic cost of the chosen path
        phrase = Phrase(text=" ".join(w[0] for w in words), score=score,
                        start_frame=words[0][1], end_frame=words[-1][2], words=words,
                        conf_scale=self.conf_scale)
        return Result([phrase], n)

    def nbest(self, frames: List[List[float]], k: int = 3) -> List[Phrase]:
        """Coarse N-best: same decode, then per-word alternatives scored locally."""
        best = self.decode(frames)
        out = list(best.phrases)
        if not out:
            return out
        base = out[0]
        for alt in self.templates:
            if len(out) >= k:
                break
            if alt.word in base.text.split():
                continue
            seg = frames[base.start_frame:base.end_frame]
            if len(seg) < 3:
                continue
            d = dtw_norm(alt.frames, seg,
                         band=max(4, abs(len(alt.frames) - len(seg)) + int(0.3 * max(len(alt.frames), len(seg)))))
            cand = Phrase(text=alt.word, score=base.score + d / max(1, len(seg)),
                          start_frame=base.start_frame, end_frame=base.end_frame,
                          words=[(alt.word, base.start_frame, base.end_frame, d)])
            out.append(cand)
        out.sort(key=lambda p: p.score)
        return out[:k]


    def _norm_template(self, tmpl: Template) -> List[List[float]]:
        key = id(tmpl)
        if key not in self._norm_cache:
            self._norm_cache[key] = cmn(tmpl.frames)
        return self._norm_cache[key]

    def decode_spotting(self, frames: List[List[float]], max_matches: int = 24,
                        scales: Tuple[float, ...] = (0.8, 0.9, 1.0, 1.1, 1.25)) -> Result:
        """Keyword-spotting decode: CMN-normalised DTW over sliding windows + NMS.

        Robust when the vocabulary is a set of isolated-word templates: every template is matched
        against windows at several durations, matches below the acceptance threshold are kept and
        overlapping ones are suppressed, then results are ordered by time.
        """
        n = len(frames)
        if not n or not self.templates:
            return Result([], n)
        norm_frames = cmn(frames)
        import statistics
        per_template: Dict[str, List[Tuple[float, int, int]]] = {}
        for tmpl in self.templates:
            ref = self._norm_template(tmpl)
            L = len(ref)
            if L < 3:
                continue
            hits: List[Tuple[float, int, int]] = []
            for sc in scales:
                w = max(3, int(L * sc))
                if w > n:
                    continue
                step = max(1, w // 5)
                band = max(4, abs(L - w) + int(0.35 * max(L, w)))
                for st in range(0, n - w + 1, step):
                    seg = norm_frames[st:st + w]
                    d = dtw_norm(ref, seg, band=band)
                    if d != float("inf"):
                        hits.append((d, st, st + w))
            if hits:
                per_template[tmpl.word] = hits

        # Acceptance is per template: a template is considered present when its best window stands
        # out from its own cost distribution (median).  This rejects vocabulary words that are not
        # in the audio while keeping every word that is (the engine's equivalent gate lives in
        # `prefilt/` + the LM score thresholds).
        cands: List[Tuple[float, int, int, Template]] = []
        by_word: Dict[str, Template] = {}
        for tmpl in self.templates:
            by_word[tmpl.word] = tmpl
        for word, hits in per_template.items():
            costs = sorted(h[0] for h in hits)
            best_t = costs[0]
            median = statistics.median(costs)
            # Presence test: the template's best window must stand out from its own cost
            # distribution (rejects vocabulary words absent from the audio).
            if best_t > median * 0.85:
                continue
            gate = best_t * 1.10          # never below the best match for a present template
            for d, a, b in hits:
                if d <= gate:
                    cands.append((d, a, b, by_word[word]))
        if not cands:
            return Result([], n)
        cands.sort(key=lambda c: c[0])
        taken: List[Tuple[int, int, Template, float]] = []
        for d, a, b, t in cands:
            if len(taken) >= max_matches:
                break
            centre = (a + b) / 2.0
            span = max(1, b - a)
            dup = False
            for x, y, tt, _dd in taken:
                if b <= x or a >= y:                      # no overlap: never a duplicate
                    continue
                dup = True
                break
            if dup:
                continue
            # same-template proximity suppression (adjacent windows of one word)
            for x, y, tt, _dd in taken:
                if tt.word == t.word and abs((x + y) / 2.0 - centre) < 0.55 * span:
                    dup = True
                    break
            if dup:
                continue
            taken.append((a, b, t, d))
        if not taken:
            return Result([], n)
        taken.sort(key=lambda x: x[0])
        words = [(t.word, a, b, d) for a, b, t, d in taken]
        covered = max(1, words[-1][2] - words[0][1])
        score = sum((b - a) * d for _, a, b, d in words) / covered
        phrase = Phrase(text=" ".join(w[0] for w in words), score=score,
                        start_frame=words[0][1], end_frame=words[-1][2], words=words,
                        conf_scale=self.conf_scale)
        return Result([phrase], n)

    def classify(self, segment: Sequence[Sequence[float]]) -> Tuple[Optional[Template], float]:
        """Best template for one isolated word segment (cost per reference frame)."""
        best, best_d = None, float("inf")
        for tmpl in self.templates:
            d = dtw_norm(tmpl.frames, segment,
                         band=max(4, abs(len(tmpl.frames) - len(segment))
                                  + int(0.3 * max(len(tmpl.frames), len(segment)))))
            if d < best_d:
                best, best_d = tmpl, d
        return best, best_d

    def decode_isolated(self, frames: List[List[float]],
                        spans: Sequence[Tuple[int, int]]) -> Result:
        """Isolated-word decoding: classify each detected word span independently."""
        words: List[Tuple[str, int, int, float]] = []
        for lo, hi in spans:
            seg = frames[lo:hi]
            if len(seg) < 3:
                continue
            tmpl, d = self.classify(seg)
            if tmpl is None:
                continue
            words.append((tmpl.word, lo, hi, d))
        if not words:
            return Result([], len(frames))
        total_frames = sum(b - a for _, a, b, _ in words) or 1
        score = sum((b - a) * d for _, a, b, d in words) / total_frames
        phrase = Phrase(text=" ".join(w[0] for w in words), score=score,
                        start_frame=words[0][1], end_frame=words[-1][2], words=words,
                        conf_scale=self.conf_scale)
        return Result([phrase], len(frames))


class Lattice:
    """Placeholder for `kernel/lattice.cpp` / `btlat.cpp` (word lattice over hypotheses)."""

    def __init__(self, phrases: Sequence[Phrase]):
        self.phrases = list(phrases)

    def best(self) -> Optional[Phrase]:
        return min(self.phrases, key=lambda p: p.score) if self.phrases else None

    def sausages(self):
        """`kernel/sausage.cpp` stand-in: per-word alternative lists."""
        return [[(w[0], w[3]) for w in p.words] for p in self.phrases]
