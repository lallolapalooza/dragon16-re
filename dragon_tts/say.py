"""CLI:  python3 -m dragon_tts.say "text..." out.wav [--voice serena] [--bank DIR] [--speed 1.1]

Also:  python3 -m dragon_tts.say --info --bank DIR     (voice/chunk inventory)
       python3 -m dragon_tts.say --phonemes "text"     (front-end inspection)
"""

from __future__ import annotations

import argparse
import os
import sys

from . import broker as broker_mod
from . import datfile, lhs_tts, phonemes, sapi4
from .pipeline import Pipeline
from .voicebank import VoiceBank

DEFAULT_BANK_CANDIDATES = (
    os.path.join("voicefiles", ""),
    "/home/asdf/Downloads/dragon16-re/voicefiles",
    "/home/asdf/Downloads/dragon16-re/allprog",
)


def find_bank(explicit: str = "") -> str:
    if explicit:
        return explicit
    for cand in DEFAULT_BANK_CANDIDATES:
        if os.path.isdir(cand) and any(f.startswith(("synth_", "uselect_", "pp.dat"))
                                       for f in os.listdir(cand)):
            return cand
    raise SystemExit("no voice bank found; pass --bank DIR")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dragon_tts.say", description=__doc__)
    ap.add_argument("words", nargs="*", help="text to speak; a trailing *.wav is the output path")
    ap.add_argument("--bank", default="", help="voice directory (synth_*.dat/uselect_*.dat/pp.dat)")
    ap.add_argument("--voice", default="", help="voice name/langcode, e.g. serena | enu")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--pitch", type=float, default=1.0)
    ap.add_argument("--volume", type=float, default=0.8)
    ap.add_argument("--sapi4", action="store_true", help="speak through the SAPI4 model instead")
    ap.add_argument("--info", action="store_true", help="print bank inventory and exit")
    ap.add_argument("--hdr", default="", help="directory with .hdr descriptors for broker resolution")
    ap.add_argument("--phonemes", action="store_true", help="print phone string and exit")
    ap.add_argument("--registry-script", metavar="DLLPATH", default=None,
                    help="print the SAPI4 registration .reg and exit")
    args = ap.parse_args(argv)

    if args.registry_script is not None:
        print(sapi4.registry_script(args.registry_script))
        return 0

    words = list(args.words)
    out = "out.wav"
    if len(words) > 1 and words[-1].lower().endswith((".wav", ".raw", ".pcm")):
        out = words.pop()
    text = " ".join(words)

    if args.info:
        bank = VoiceBank.load(find_bank(args.bank))
        print(bank.report())
        hdr_dirs = [args.hdr] if args.hdr else ["tests/fixtures/broker", "allprog"]
        hdr_dirs = [d for d in hdr_dirs if os.path.isdir(d)]
        b = broker_mod.Broker(roots=hdr_dirs or [os.path.dirname(find_bank(args.bank))])
        n = b.scan()
        print(f"broker: {n} descriptors; brokerstrings={sorted(b.components)}")
        print(f"TTSENGINE -> {b.broker_string_query('TTSENGINE')!r}")
        return 0

    if not text:
        ap.error("no text given")

    if args.phonemes:
        print(" ".join(p for p, _ in phonemes.text_to_phones(text) if p and p != "."))
        return 0

    bank = VoiceBank.load(find_bank(args.bank))

    if args.sapi4:
        pipeline = Pipeline.for_voice(bank, args.voice)
        pipeline.speed, pipeline.pitch, pipeline.volume = args.speed, args.pitch, args.volume

        def _render(t: str) -> None:
            with open(out, "wb") as fh:
                fh.write(pipeline.synthesize(t, return_wav=True))

        engine = sapi4.Engine(voice_bank=bank, speak=_render)
        rc, central = engine.create_and_select(args.voice)
        if central is None:
            print(f"SAPI4: no mode selected (rc={rc})", file=sys.stderr)
            return 2
        print(f"SAPI4 mode: {central.mode.name!r}")
        rc = engine.speak(text, args.voice)
        print(f"SAPI4 TextData rc={rc} wrote={out}")
        return rc

    # Legacy C API path (lhs_tts*)
    module = lhs_tts.configure(bank=bank)
    module.InitializeEx(0, b"", 1)
    rc, handle = module.ttsOpen(None, "RealSpeak Standard v2")
    if rc != lhs_tts.LHS_OK:
        print(f"lhs_ttsOpen failed: {lhs_tts.ErrorGetString(rc)}", file=sys.stderr)
        return 2
    pipeline = module.system.instances[handle].pipeline
    pipeline.speed, pipeline.pitch, pipeline.volume = args.speed, args.pitch, args.volume
    if args.voice:
        try:
            v = bank.voice(args.voice)
            pipeline.frequency = v.frequency or pipeline.frequency
            pipeline.voice = v.voice
        except KeyError:
            pass
    module.ttsSetParamList(handle, [(lhs_tts.PARAM_LANGUAGE, pipeline.language),
                                    (lhs_tts.PARAM_OUTPUTCFG, 0x202)])
    module.ttsSetText(handle, text)
    rc = module.ttsSpeak(handle)
    if rc != lhs_tts.LHS_OK:
        print(f"lhs_ttsSpeak failed: {lhs_tts.ErrorGetString(rc)}", file=sys.stderr)
        return 2
    rc = module.ttsGenOutputFile(handle, out)
    module.ttsClose(handle)
    module.UnInitialize()
    inst = module.system
    print(f"voice={pipeline.voice or 'default'} freq={pipeline.frequency} "
          f"components={len(pipeline.components)} wrote={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
