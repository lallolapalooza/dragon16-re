"""dragon_tts — reference re-implementation of the Dragon/RealSpeak TTS subsystem.

Modules
-------
datfile   Nuance `.dat` asset container reader (voice banks, prompt/phone data)
hdr       `.hdr` descriptor parsing (LHS component registry + NUANCE pipeline manifest)
broker    `brk_*` broker semantics: component discovery and broker-string resolution
voicebank voice directory model (VINF metadata, chunk inventory, manifests)
phonemes  text normalisation + grapheme-to-phoneme (front-end stand-in)
synth     replaceable waveform synthesiser (formant source-filter model)
pipeline  component graph named after the manifest `COMPONENTS` entries
lhs_tts   the 35-function `lhs_tts*` API surface with original semantics
sapi4     SAPI4 module model: CLSIDs/IIDs, mode enumeration, registration script
say       CLI:  python3 -m dragon_tts.say "text" out.wav --voice samantha

See SPEC_TTS.md for the interface/format specification this implements.
"""

__all__ = [
    "datfile", "hdr", "broker", "voicebank", "phonemes", "synth", "pipeline",
    "lhs_tts", "sapi4",
]
__version__ = "0.1.0"
