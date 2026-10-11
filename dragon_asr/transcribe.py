"""CLI: transcribe a WAV to text.

    python3 -m dragon_asr.transcribe in.wav --words "hello,world,testing"
    python3 -m dragon_asr.transcribe in.wav --vocab words.txt --json
    python3 -m dragon_asr.transcribe --selftest "hello world"     # synth with dragon_tts, then recognise
    python3 -m dragon_asr.transcribe --inventory DATAPACK_DIR     # ASR asset inventory

Templates (acoustic models for the vocabulary) are built by synthesising each word with
`dragon_tts` and cached next to the WAVs, so the loop is self-contained on Linux: the shipped
assets supply the container/format/metadata layer, and the reference acoustic model is generated.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional

from . import dfile as df
from . import vocab as vocab_mod
from .decoder import Result, Template
from .engine import EngineControl, RDApi_GetDFileInfo, RDApi_IsDFile, Recognizer, SDApi_GetFileRecognizerVersion
from .features import Mfcc, MfccConfig, WavFile

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_templates.json")


# ---------------------------------------------------------------------------
def synth_word_frames(word: str, rate: int = 16000) -> List[List[float]]:
    """Render a word with the TTS reference implementation and return its MFCC frames."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from dragon_tts import phonemes, synth as tts_synth
    s = tts_synth.FormantSynthesiser()
    pcm = s.render(tts_synth.RenderRequest(phones=phonemes.text_to_phones(word), fs=rate))
    import array
    a = array.array("h")
    a.frombytes(pcm)
    return Mfcc(MfccConfig(sample_rate=rate)).frames(a)


def build_templates(words: List[str], rate: int = 16000) -> List[Template]:
    out = []
    for w in words:
        frames = synth_word_frames(w, rate)
        if frames:
            out.append(Template(word=w, phones=vocab_mod.g2p(w), frames=frames))
    return out


def load_cache() -> Dict[str, List[List[float]]]:
    if os.path.exists(CACHE):
        try:
            with open(CACHE) as fh:
                return json.load(fh)
        except Exception:
            return {}
    return {}


def save_cache(cache: Dict[str, List[List[float]]]) -> None:
    with open(CACHE, "w") as fh:
        json.dump(cache, fh)


# ---------------------------------------------------------------------------
def cmd_inventory(path: str) -> int:
    rows = []
    for root, _dirs, files in os.walk(path):
        for fn in files:
            p = os.path.join(root, fn)
            if fn.lower().endswith((".usr", ".psh", ".sig", ".enh", ".voc", ".svc", ".ivc", ".ivs", ".wt")):
                try:
                    rows.append(df.DFile(p))
                except ValueError:
                    continue
    rows.sort(key=lambda d: -d.size)
    print(f"DFILE assets under {path}: {len(rows)}")
    for d in rows:
        print("  " + df.probe(d.path))
    if not rows:
        print("  (none found)")
    return 0


def cmd_selftest(text: str, json_out: bool) -> int:
    """Synthesise `text` with dragon_tts, then recognise it: end-to-end WAV→text."""
    words = [w for w in text.split() if w.isalpha()]
    if not words:
        print("selftest needs words", file=sys.stderr)
        return 2
    v = vocab_mod.Vocabulary()
    for w in set(words):
        v.add(w)
    rec = Recognizer(vocabulary=v)
    cache = load_cache()
    templates = []
    for w in sorted(set(words)):
        frames = cache.get(w) or synth_word_frames(w)
        cache.setdefault(w, frames)
        templates.append(Template(word=w, phones=v.pronunciation(w) or vocab_mod.g2p(w), frames=frames))
    save_cache(cache)
    rec.templates = templates
    from .decoder import Decoder
    rec.decoder = Decoder(templates)

    # render the whole sentence with the TTS engine
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from dragon_tts import lhs_tts, synth as tts_synth
    pcm = tts_synth.FormantSynthesiser().render(
        tts_synth.RenderRequest(phones=__import__("dragon_tts.phonemes", fromlist=["x"]).text_to_phones(text),
                                fs=16000))
    import array
    a = array.array("h")
    a.frombytes(pcm)
    result = rec.transcribe_samples(a, 16000)
    hit = sum(1 for w in words if w.lower() in result.text.lower().split())
    if json_out:
        print(json.dumps({"expected": text, "hypothesis": result.text, "words_expected": len(words),
                          "words_found": hit, "result": result.as_dict()}, indent=2))
    else:
        print(f"expected : {text}")
        print(f"hypothesis: {result.text}")
        print(f"words found: {hit}/{len(words)}  engine={SDApi_GetFileRecognizerVersion()}")
    return 0 if hit else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="dragon_asr.transcribe", description=__doc__)
    ap.add_argument("wav", nargs="?", help="input WAV (16-bit PCM)")
    ap.add_argument("--words", default="", help="comma-separated vocabulary to build templates for")
    ap.add_argument("--vocab", default="", help="file with one word per line")
    ap.add_argument("--datapack", default="", help="language Datapack dir (for vocrejct/.voc words)")
    ap.add_argument("--json", action="store_true", help="emit JSON result")
    ap.add_argument("--inventory", metavar="DIR", default="", help="list DFILE assets and exit")
    ap.add_argument("--selftest", metavar="TEXT", default="", help="synthesise then recognise TEXT")
    ap.add_argument("--rate", type=int, default=16000)
    args = ap.parse_args(argv)

    if args.inventory:
        return cmd_inventory(args.inventory)
    if args.selftest:
        return cmd_selftest(args.selftest, args.json)
    if not args.wav:
        ap.error("no input WAV (or use --inventory/--selftest)")

    v = vocab_mod.Vocabulary()
    words: List[str] = []
    if args.words:
        words = [w.strip() for w in args.words.split(",") if w.strip()]
        for w in words:
            v.add(w)
    if args.vocab:
        v.load_words(args.vocab)
    if args.datapack:
        v = vocab_mod.load_language_pack(args.datapack)
        words = v.words()
    if not words:
        ap.error("no vocabulary: pass --words, --vocab or --datapack")

    cache = load_cache()
    templates = []
    for w in words:
        frames = cache.get(w)
        if not frames:
            frames = synth_word_frames(w, args.rate)
            cache[w] = frames
        templates.append(Template(word=w, phones=v.pronunciation(w) or vocab_mod.g2p(w), frames=frames))
    save_cache(cache)

    rec = Recognizer(templates, vocabulary=v, mfcc=Mfcc(MfccConfig(sample_rate=args.rate)))
    control = EngineControl(rec)
    result = control.recognize_file(args.wav)
    if args.json:
        print(json.dumps({"file": args.wav, "engine": SDApi_GetFileRecognizerVersion(),
                          "vocabulary": len(words), "result": result.as_dict()}, indent=2))
    else:
        print(result.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
