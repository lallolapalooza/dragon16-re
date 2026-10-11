"""dragon_asr — reference re-implementation of Dragon's speech-to-text subsystem.

Modules
-------
dfile      DFILE v10 container reader (every ASR asset: *.usr *.psh *.sig *.enh *.voc *.svc)
features   signal processing: WAV reader, MFCC/mel filterbank/FFT, utterance detection
vocab      vocabulary + lexicon (word lists, vocrejct.dat, DFILE .voc, G2P pronunciations)
decoder    search/decoding: template DTW, lattice/sausage/N-best, result model
engine     engine/API layer: RDApi_*/SD* surface + Dragon automation model (DgnEngineControl)
transcribe CLI: WAV -> text, self-test (synthesise with dragon_tts then recognise)

The shipped recognition stack is `mrec.dll` (Nuance MREC: sdapi/, recogctl/, kernel/, fst/, lm/,
neuraln/, pel/, sigproc/, voc/) loaded by `dd10renx.dll` + `au10renx.dll` per language, driven
from `natspeak.exe`.  See ../SPEC_ASR.md.
"""

__all__ = ["dfile", "features", "vocab", "decoder", "engine", "transcribe"]
__version__ = "0.1.0"
