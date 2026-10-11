# Dragon 16 media — reverse-engineering deliverables

Target: `~/Downloads/Dragon16.zip` = **Dragon NaturallySpeaking 16** install media
(InstallShield + MSI + 11 cabinets). Goal: enough to **re-create the text-to-speech
subsystem** from scratch.

## Read in this order

| file | contents |
|---|---|
| `RE_REPORT.md` | package/installer/MSI archaeology, file inventory, TTS stack overview, licensing |
| `SPEC_TTS.md` | **TTS implementation spec**: APIs, GUIDs, descriptor schemas, asset formats, decoded tables/geometry, acceptance tests, verified status |
| `SPEC_ASR.md` | **STT implementation spec**: MREC module map, DFILE v10 container, engine/COM/automation surfaces, pipeline, acceptance tests |
| `ARCHITECTURE.md` | **how the proprietary algorithms work**: MREC front end/acoustics/prefilter/search/LM/confidence/adaptation, DFILE codec, TTS engine (LHKP) + coder — architecture & parameter sets, no porting |
| `include/lhs_tts.h` | C ABI (TTS): the 35 `lhs_tts*` exports, `Core*` prototypes, `ttscore` vtable layout, SAPI4 IDs |
| `include/lhs_asr.h` | C ABI (STT): DFILE constants, `RDApi_*`/`SD*` surface, SR CLSIDs, automation typelib |
| `dragon_tts/` | runnable TTS reference implementation (Python 3, stdlib only) |
| `dragon_asr/` | runnable STT reference implementation (container, MFCC, decoder, engine, CLI) |
| `sdkdocs/` | Dragon API reference extracted from the shipped `dnsapi.chm` (524 pages) |
| `datfile.py` | standalone `.dat` container reader (CLI form) |
| `msi_tables/*.csv` | all 68 MSI tables, decoded |
| `decomp/*.c` | Ghidra decompilations (TTS layer, SAPI4 module, broker, clients) |
| `voicefiles/` | extracted voice assets used by the tests (`synth_*`, `uselect_*`, `pp.dat`, `vauto_pipeline_*.hdr`) |
| `tests/fixtures/broker/` | `.hdr` descriptors for broker resolution tests |
| `allprog/`, `bins/`, `extract/`, `voice/`, `datapack/` | extraction scratch trees |

## Quick start

```bash
cd /home/asdf/Downloads/dragon16-re
python3 -m unittest discover -s tests -v          # 21 tests
python3 -m dragon_tts.say --info --bank voicefiles
python3 -m dragon_tts.say "hello world" out.wav --bank voicefiles --voice samantha
python3 -m dragon_tts.say "hello" out.wav --bank voicefiles --sapi4
python3 datfile.py voicefiles/uselect_serena_full.dat --depth 2
python3 -m dragon_asr.transcribe --selftest "the quick brown fox"
python3 -m dragon_asr.transcribe --inventory asrfiles
```

Produces 22050 Hz / 16-bit / mono WAV (the format the voice manifests declare).

## What is established

* **TTS stacks**: legacy L&H C API (`lhtts01.dll`, 35 `lhs_tts*` exports, broker-resolved
  `ttscore` core) and SAPI4 module (`rsstdv2.dll`, CLSID `{A162A680-…}`, registry layout,
  IIDs, client protocol from `natspeak.exe`), plus the `vautov5.dll` pipeline component registry
  and the two `.hdr` descriptor schemas.
* **Asset container** (`fb e0 ef 9d` + u32 + IFF-like chunk tree; tags/strings XOR 0xA9,
  sizes plaintext) with full chunk inventories for `PP\0\0LIST`, `VFNTLIST`, `USELLIST`.
* **Voice metadata** (`VINF`): voice, language, 22050 Hz, coder `155mrf22`, bitrate 270,
  overhead frames 6; `CTUN` unit counts; codebooks `CODR`/`CDBK`; coded speech `SPCH`; unit
  selection `UNIT`/`SYMB`/`TRI`/`CCOS`.
* **Installer/MSI**: features, cabinets, registry, INI, custom-action flow, licensing/activation.

## Verification snapshot

`python3 -m unittest discover -s tests` → **43 tests, all passing** (21 TTS + 22 ASR).
TTS: 3 code paths emit valid non-silent 22050 Hz/16-bit mono WAV.
STT: TTS→WAV→text round trip — `hello world` 2/2, `one two three` 3/3,
`reverse engineering speech recognition` 4/4, `the quick brown fox jumps over the lazy dog` 9/9,
isolated-word accuracy ≥5/6 (template-model substitution; see `SPEC_ASR.md` §6).

## What is still open (see `SPEC_TTS.md` §12 and `SPEC_ASR.md` §8)

1. `155mrf22` coder decode (`SPCH` ↔ `CODR`) — the only blocker for sample-faithful synthesis;
   `synth.CoderSynthesiser` marks the seam. The shipped reference uses a formant synthesiser
   behind the same interfaces.
2. `clc_*_cfg3.dat` container (front-end lexicon/config).
3. Literal setting names for `TTSModeName`/`useRealSpeakV2SAPI4`/`TTSPitch|Speed|Volume`.
4. The 139-byte obfuscated blob passed to `lhs_InitializeEx`.
5. Broker default `*.hdr` search root when no `LH_*` environment variables are set.

---

## Repository contents (and what is deliberately absent)

This repo publishes **original interoperability analysis and original code only**.

Included:
```
README.md  RE_REPORT.md  SPEC_TTS.md  SPEC_ASR.md  ARCHITECTURE.md  LICENSE
include/lhs_tts.h  include/lhs_asr.h        # reconstructed ABIs
dragon_tts/  dragon_asr/                    # runnable reference implementations
tests/                                      # 46 tests
parse_msi.py  datfile.py                    # MSI + TTS asset readers
ghidra_scripts/                             # headless decompile/analysis scripts
```

Deliberately **not** included (see `.gitignore`), because redistributing them is not permitted:
the vendor install media (`Dragon16.zip`), extracted binaries and installers, voice/model assets
(`*.dat`, `*.usr`, `*.psh`, `*.voc`, `*.svc`, …), Nuance's own documentation (`dnsapi.chm`
extracted), Ghidra decompilation dumps of proprietary code, and the decoded MSI tables.

Everything excluded is reproducible from a copy of the vendor media that **you** supply:

```bash
# 1. unpack media
7zz x Dragon16.zip -oextract 'DPG_DLG_16.0_NUMC/*'
7zz e extract/DPG_DLG_16.0_NUMC/Dragon\ 16.msi -omsi_streams      # MSI streams
python3 parse_msi.py msi_streams msi_tables                        # -> decoded tables
7zz e extract/DPG_DLG_16.0_NUMC/NatSpeak.cab -oallprog             # program files
7zz e extract/DPG_DLG_16.0_NUMC/TTS.cab -ovoicefiles               # TTS core + base voice
7zz x extract/DPG_DLG_16.0_NUMC/TTSENU.cab -ovoicefiles            # US voice (Samantha)
7zz e extract/DPG_DLG_16.0_NUMC/AUS.cab -oasrfiles                 # a language pack
# 2. analyse (optional)
/opt/ghidra/support/analyzeHeadless ghidra_proj ttsproj -import allprog/mrec.dll \
    -scriptPath ghidra_scripts -postScript DumpXref.java decomp/mrec_dfile.c "DFILE"
# 3. run the reference implementations
python3 -m dragon_tts.say "hello" out.wav --bank voicefiles
python3 -m dragon_asr.transcribe --selftest "hello world"
```

Tests that need vendor assets skip automatically when the extracted trees are absent.

## Scope and legal note

The analysis documents architecture, interfaces and file formats for interoperability and
research. No vendor code, binary, model data or documentation is copied or redistributed here;
names and trademarks remain the property of their owners.
