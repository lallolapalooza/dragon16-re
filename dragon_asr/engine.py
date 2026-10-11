"""Engine/API layer.

Two surfaces, mirroring what the product actually exposes:

1. **SD / RDApi internal surface** (`mrec.dll`) — recogniser lifecycle and result access.
   Names observed in the module: `RDApi_IsDFile`, `RDApi_GetDFileInfo`, `RDApi_GetDFileData`,
   `SDRecog_RecognizerGetNumRecogLayers`, `SDRecog_RecognizeNonAcoustic`,
   `SDRecog_GuessPronsAcoustic`, `SDRecog_GuessPronsNonAcoustic`,
   `SDRecogResult_NewListFromRecognizer`, `SDLattice_NewFromRecogResult`,
   `SDSausage_NewFromSausageSplitNonAcoustic`, `SDState_*`, `SDPrefilterResult_*`,
   `SDFile*` accessors (`sdapi/rdf*.cpp`, `sdf*.cpp`), `SDApi_GetFileRecognizerVersion`,
   `SDDiag_TestReadFile`.

2. **Dragon automation surface** (`dnstk10.dll` typelib `{5C486340-2F92-11D1-A47C-00A024A3A678}`,
   documented in `dnsapi.chm`): `DgnEngineControl`, `DgnSpeechEngines`/`DgnSpeechEngine`,
   `DgnResult`, `DgnStrings`, `DgnLongLong`, dictation/correction objects, and the event
   interfaces (`DgnEngineControlEvents`, `DgnMicBtnEvents`, `DgnVCmdEvents`) — see
   `SPEC_ASR.md` §6 for the documented member lists.

The reference implementation keeps the same shape and drives the substitute acoustic model.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import vocab as vocab_mod
from .decoder import Decoder, Lattice, Phrase, Result, Template
from .dfile import DFile
from .features import Mfcc, MfccConfig, WavFile, detect_speech, split_words

# ---------------------------------------------------------------------------
# engine / file identity helpers (mirror RDApi_*)
def RDApi_IsDFile(path: str) -> bool:
    try:
        DFile(path)
        return True
    except Exception:
        return False


def RDApi_GetDFileInfo(path: str) -> Dict:
    d = DFile(path)
    return {"path": path, "version": d.header.version, "page_size": d.header.page_size,
            "entries": d.header.entry_count, "pages": d.pages, "size": d.size,
            "trailer_ok": d.trailer_ok}


def RDApi_GetDFileData(path: str, offset: int = 0, length: int = -1) -> bytes:
    with open(path, "rb") as fh:
        fh.seek(offset)
        return fh.read() if length < 0 else fh.read(length)


def SDApi_GetFileRecognizerVersion() -> str:
    """Version string observed in `mrec.dll`: the MREC build identifier."""
    return "1.2.5.f-MREC-alien"


# ---------------------------------------------------------------------------
@dataclass
class MicState:
    """`DgnMicBtnEvents.MicState` / `IDgnVoiceCapture` status."""

    on: bool = False
    vu: int = 0
    level: int = 0


class EngineControl:
    """`DgnEngineControl` equivalent: connects writer targets and drives dictation."""

    def __init__(self, recognizer: "Recognizer"):
        self.recognizer = recognizer
        self.microphone = False
        self.recognition_mode = "dictation"
        self.speaker = "default"
        self.topic = "<default>"
        self.writer: Optional[Callable[[str], None]] = None
        self._events: Dict[str, List[Callable]] = {}

    # -- event plumbing (DgnEngineControlEvents / DgnMicBtnEvents) ------
    def on(self, name: str, cb: Callable) -> None:
        self._events.setdefault(name, []).append(cb)

    def fire(self, name: str, *args) -> None:
        for cb in self._events.get(name, []):
            cb(*args)

    # -- documented properties -----------------------------------------
    @property
    def Microphone(self) -> bool:
        return self.microphone

    @Microphone.setter
    def Microphone(self, value: bool) -> None:
        self.microphone = bool(value)
        self.fire("MicState", int(self.microphone))

    @property
    def RecognitionMode(self) -> str:
        return self.recognition_mode

    @RecognitionMode.setter
    def RecognitionMode(self, value: str) -> None:
        self.recognition_mode = str(value)
        self.fire("RecognitionModeChanged")

    @property
    def Speaker(self) -> str:
        return self.speaker

    @property
    def EngineFeatures(self) -> List[str]:
        """`DgnEngineFeatureConstants`: flags advertised by the installed engine."""
        return ["FeatureDictation", "FeatureCommands", "FeatureTranscription",
                "FeatureCorrection", "FeatureVocabulary", "FeatureAdaptation"]

    # -- dictation ------------------------------------------------------
    def recognize_file(self, path: str) -> Result:
        """Transcribe a recorded utterance (what the UI's transcription engine does)."""
        self.fire("RecognitionStarting")
        self.fire("UtteranceBegin")
        result = self.recognizer.transcribe_file(path)
        self.fire("UtteranceEnd")
        if self.writer is not None and result.text:
            self.writer(result.text)
        return result


# ---------------------------------------------------------------------------
class Recognizer:
    """The ASR engine instance (SD API equivalent)."""

    def __init__(self, templates: Iterable[Template] = (), vocabulary: Optional[vocab_mod.Vocabulary] = None,
                 grammar: Optional[Iterable[str]] = None, mfcc: Optional[Mfcc] = None):
        self.mfcc = mfcc or Mfcc(MfccConfig(sample_rate=16000))
        self.vocabulary = vocabulary or vocab_mod.Vocabulary()
        self.templates: List[Template] = list(templates)
        self.decoder = Decoder(self.templates, grammar)
        self.config: Dict[str, object] = {}
        self.built = False
        self.search_mode = "spotting"      # 'spotting' | 'continuous' (segmental DP)

    # -- configuration --------------------------------------------------
    def set_config(self, **kw) -> None:
        """AcousticScoring*/Search* style knobs (kept for API parity)."""
        self.config.update(kw)

    @property
    def num_recog_layers(self) -> int:
        """`SDRecog_RecognizerGetNumRecogLayers` (acoustic + LM layers)."""
        return 2

    # -- vocabulary ------------------------------------------------------
    def build_templates(self, synth: Callable[[str], List[List[float]]],
                        words: Optional[Iterable[str]] = None) -> int:
        """Create acoustic templates for vocabulary words using a synthesiser callback.

        ``synth(word) -> MFCC frames``; the CLI wires this to `dragon_tts` so the demo is
        self-contained (synthesise → recognise round trip).
        """
        src = list(words) if words else self.vocabulary.words()
        added = 0
        for w in src:
            entry = self.vocabulary.entries.get(w.lower())
            prons = entry.pronunciation if entry else vocab_mod.g2p(w)
            frames = synth(w)
            if frames:
                self.templates.append(Template(word=w, phones=list(prons), frames=frames))
                added += 1
        self.decoder = Decoder(self.templates, None)
        self.built = True
        return added

    # -- decoding --------------------------------------------------------
    def transcribe_samples(self, samples: Sequence[int], rate: int = 16000) -> Result:
        if rate != self.mfcc.cfg.sample_rate:
            self.mfcc = Mfcc(MfccConfig(sample_rate=rate))
        frames = self.mfcc.frames(samples)
        energies = self.mfcc.energies(samples)
        if self.search_mode in ("spotting", "isolated") and self.templates:
            result = self.decoder.decode_spotting(frames)
            result.duration_frames = len(frames)
            return result
        lo, hi = detect_speech(energies)
        result = self.decoder.decode(frames[lo:hi])
        for p in result.phrases:                     # re-base to absolute frames
            p.start_frame += lo
            p.end_frame += lo
            p.words = [(w, a + lo, b + lo, s) for w, a, b, s in p.words]
        result.duration_frames = len(frames)
        return result

    def transcribe_file(self, path: str) -> Result:
        wav = WavFile(path)
        return self.transcribe_samples(wav.samples, wav.rate)

    def lattice(self, path: str, k: int = 3) -> Lattice:
        wav = WavFile(path)
        if wav.rate != self.mfcc.cfg.sample_rate:
            self.mfcc = Mfcc(MfccConfig(sample_rate=wav.rate))
        frames = self.mfcc.frames(wav.samples)
        lo, hi = detect_speech(self.mfcc.energies(wav.samples))
        return Lattice(self.decoder.nbest(frames[lo:hi], k))

    # -- pronunciation guessing (`SDRecog_GuessProns*`) ------------------
    def guess_pronunciations(self, word: str, acoustic: bool = False) -> List[List[str]]:
        return [vocab_mod.g2p(word)]

    # -- file identification (`SDDiag_TestReadFile`) ---------------------
    def test_read_file(self, path: str) -> Dict:
        return RDApi_GetDFileInfo(path)
