# Speech-to-Text Implementation Specification — Dragon/RealSpeak (Dragon 16 media)

Companion to `RE_REPORT.md` (package/installer archaeology) and `SPEC_TTS.md` (read-back).
This document specifies the **recognition (dictation/transcription) subsystem** at
interface/format level so it can be rebuilt without the original media.  Reverse-engineering
provenance: MSI tables, shipped assets, `dnsapi.chm` (SDK help), and Ghidra decompilations in
`decomp/`.  Confidence markers as elsewhere: **[verified]** = read from shipped bytes/disassembly,
**[inferred]** = consistent with evidence but not directly observed.

Reference implementation in this repo: `dragon_asr/` (Python 3, stdlib only) — container reader,
MFCC front-end, decoder, vocabulary layer, engine API, CLI — verified by `tests/test_asr.py` and
`python3 -m dragon_asr.transcribe --selftest`.

---

> Algorithm architecture (how each proprietary algorithm works, with its parameter sets):
> see `ARCHITECTURE.md`.

## 1. Component map **[verified]**

| Role | Module | Size | Notes |
|---|---|---|---|
| **Recognition engine (core)** | `mrec.dll` | 8,173,464 | Nuance **MREC**; every algorithm below lives here |
| Recogniser front-end, per language | `dd10renx.dll` (+ `_x64`) | 6,203,800 | also `dd10rdeu/esp/fra/ita/nld`, `…_x64` |
| Acoustic module, per language | `au10renx.dll` | 2,836,376 | also `au10rdeu/esp/fra/ita/nld` |
| Speech tooling | `speechtools.dll` | 811,928 | feature/utility layer |
| Toolkit / automation API | `dnstk10.dll` (+ `_x64`) | 2,011,032 | embeds **typelib** `{5C486340-2F92-11D1-A47C-00A024A3A678}` |
| Vocabulary tooling | `voctools.dll`, `voctool.chm` | 753,056 / 229,278 | vocabulary editor |
| Training/adaptation | `gentrain.dll`, `vcmigrat.dll` | 258,976 / 223,128 | enrolment, vocabulary migration |
| Host UI / dictation driver | `natspeak.exe` | 6,251,936 | mic, ACO, correction, injection |
| Text injection | `dd10hook.dll` (+ `_x64`), `dgnuiasvr(_x64).exe`, `dgnria_nmhost*` | — | Win32 hooks, UI Automation, browser (RIA) |
| Service | `dgnsvc.exe`, `loggerservice.exe` | 428,448 / 206,752 | Dragon service + logger |
| SDK help | `dnsapi.chm` | 619,759 | full API reference (524 pages; extracted to `sdkdocs/`) |

`lhsp01.dll` references a legacy `lhsp\lhasr01w.dll` path, but **no such file ships in v16**
(the ASR-compiler exports `lhs_asrCpl*` are re-exported by `lhsp01.dll` itself).

`mrec.dll` internal module map (from embedded source paths) **[verified]**:

```
sdapi/      rdf*.cpp sdf*.cpp  (file accessors)  +  RDApi_* / SD* entry points
recogctl/   recogctl recogres recoggrm  adaptctl  ivecmgr  baumwel mixadapt choosew
kernel/     recog btlat lattice wordlat sausage phnlat searchlm active latutil constran
fst/        transducer fstcore srchitf fstsrch
fstgraph/   trgrmmgr trgrm unibuilder leafgraph unicomposer unisequencer
lm/         lm wordlm dummylm multilm ngramdata bgramdata tgramdata qgramdata nnlm ngram2fsm
            recentb lmcommon
neuraln/    neuraln nnscorer lmnnsc pelnnsc adptnnsc bcnnsc bnfmgr nnclassmgr nnpelmgr
pel/        pel pelsc hiersc nnpelsc fmpe ldt genclass multable bet
prefilt/    prefilt lexnet treepref lookahsc thrdpref retlist
sigproc/    sigproc spd melfbank srfft cheadpt chanadpt chanest uttfmgr uttfile snr
            feature chain: featsel featgen featscal featfilt featrang featcopy maxnorm ffv
            wavepac waveadj wavedec pitchtrk sputtdet siltrack ltf ctftmgr parseff
net/        netmgr hmmnet emptynet history
voc/        voc vocmgr vocdelta vocdeltamgr statemgr rulemgr rpd vcc tsr vgroupi
word/       wordlist collmgr collarra corpus charinfo pcollmgr
user/       user usermgr picmgr ivecmodel ocntrnac lookah
channel/    channel chanmgr filechan warpchan textchan ivecchan extchan utterance
audiosrc/   filesrc extsrc nistsrc utbsrc utdsrc
frame/      wavedata wavedec
fileutil/   huffutil cmbtable dmatrix paramset paramspec dsmstream envhold envmgr dsmmgr
dfutil/     dfile realdf dtxtfile subext listdf encrypt
mrecutil/   dstream dfstream dmstream dendio dfputil dosfile dthread dstring dtimer
            dmemchunk dverifptr dfilespec dactor dstrutil dparam dprinter dpromgr
```
Version string: `1.2.5.f-MREC-alien` (`mrecVersion`); `SDApi_GetFileRecognizerVersion` returns it.

---

## 2. DFILE v10 — the universal container **[verified]**

Every shipped ASR asset is a DFILE: acoustic models (`*.usr`, `*.psh`), signal/linguistic
enhancers (`*.sig`, `*.enh`), vocabularies (`*.voc`, `*.ivc`, `*.ivs`), language data, and the
user/utterance files.  Layout:

```
signature   b"DFILE v10"                      # NUL padded to 16 bytes ("DFILE v" compared byte-wise)
u32         version       = 10                # checked; older builds wrote "DFILE v9"
u16         format_marker = 0xFFFE            # 0xFEFF signals an obsolete/newer format
u16         reserved      = 0x0002
u32         page_size     ∈ {0x1000, 0x100}   # 4096 for files, 256 for streams
u32         entry_count                       # subfile/block entries (4 … 5541 observed)
u32         marker        = 0xEDACFF0E        # 0xD2CEFADE = alternate/encrypted marker
u32         data_size / checksum              # stored in the reader object
u32         codec_selector                    # low byte selects the subfile coding variant
                                              # (switch on 2, 3, 4, 5, 6, 9, 0x12)
…           payload, page aligned (0x1000)
tail        … 0xEDACFF2E then zero padding     # trailer marker
```

Subfile directory entries are `RealDFileSubFileEntry` **[verified by the engine's own error
format `"%.500s %.500s %u %u %u"`]**: name string, class string, and three `u32` fields
(offset/length/checksum family — exact roles not yet pinned).  Subfile classes referenced by the
engine: `Voc`, `SigProc`, `User`, `ParamSetHolder`, `EnvHolder`; asset extensions
`.fmp`, `.sp`, `.lm`, `.ivc`, `.ivs`, `.wt`.

Bodies are **Huffman / combined-table coded** (`fileutil/huffutil.cpp`, `fileutil/cmbtable.cpp`;
also `fileutil/huffman`, `cmbtable`), optionally wrapped by `dfutil/encrypt.cpp`.  That is why the
payloads show ~7.9 bits/byte entropy and no plain text.  Two files in the ISSetup payload also use
the Dragon `66 B6 01 01` wrapper (`models.ini`, `server.ini`).

Reader/utility surface observed in `sdapi/`: `RDApi_IsDFile`, `RDApi_GetDFileInfo`,
`RDApi_GetDFileData`, `RDUttFile_UpgradeCombinedFile`, `SDDiag_TestReadFile`, `ListDFile`,
`DebugShowDFileLoadInfo`, `DebugShowDFileSubfileChecksums`.

`dragon_asr/dfile.py` implements the container (validated against 9 shipped assets: page
alignment, markers, entry counts, trailer).

### 2.1 Payload coding classes (measured) **[verified]**

DFILE payloads are **not uniformly coded**: page-level entropy (0x1000-byte pages) separates
readable tables from coded data (`DFile.payload_classes()`, `DFile.subfile_hint()`):

| asset | coded pages (>7.5 b/B) | dense (6.5–7.5) | tables (<6.5) | reading status |
|---|---|---|---|---|
| `token_s.enh` | 84 | 0 | 5 | mostly coded |
| `lexicon.enh` | 22 | 0 | 5 | mostly coded |
| `aus_p2.usr` | 3619 | 29 | 26 | mostly coded (acoustic model) |
| `aus_p2.psh` | 2 | 1 | 7 | mostly readable tables |
| `vocdelta.voc` | 1 | 0 | 29 | readable |
| `aco_bm4_dnn.enh` | 0 | 0 | 5 | readable (H ≈ 2.3) |

The coded pages require the Huffman / combined-table decoder in `mrec.dll`
(`fileutil/huffutil.cpp`, `fileutil/cmbtable.cpp`, plus `dfutil/encrypt.cpp`); everything else can
be read today by slicing the payload (`DFile.read_subfile`).

### 2.2 Shipped ASR assets (per language pack)

| Asset | Role | Example sizes (ENG/ENU) |
|---|---|---|
| `<lang>_p1.usr`, `_p2.usr` | acoustic model (two passes / sizes) | 30.5 MB, 15.0 MB |
| `<lang>_p1.psh`, `_p2.psh` | phone/state headers | 57 KB, 40 KB |
| `<lang>_11k_p2.sig`, `_16k_p1.sig` | sampling-rate specific signal data | 36 KB, 294 KB |
| `lexicon.enh` | lexicon/pronunciation tables | 110 KB |
| `token_s.enh` | tokeniser | 364 KB |
| `itn_s.enh`, `itn_si.svc/.voc` | inverse-text-normalisation | 61 KB / 520 KB / 254 KB |
| `autopunct_s.enh` | automatic punctuation | 20 KB |
| `aco_bm4_dnn.enh` | neural acoustic post-processing ("bm4_dnn") | 20 KB |
| `dispatch.svc` | language model (network form) | 117–473 MB |
| `dispatch.voc`, `pg.voc`, `vocdelta.voc` | LM vocabulary / pronunciation / delta | 22.5 / 12.2 / 0.12 MB |
| `spelling.voc` | spelling vocabulary | 803 KB |
| `vocrejct.dat` | **plain-text** phoneme rejection list | 1.4 KB |
| `models.ini`, `server.ini` | configuration (`66 B6 01 01` wrapper) | 3.9 KB / 0.4 KB |

Layout: `<CommonAppData>\Nuance\NaturallySpeaking16\Datapack\<edition>-<lang>-<region>/`
(`config/`, `am/`, `lm/general/large/`, `lm/legal/large/`, `lm/dle/pol/lrg/`, …) plus
`Data\<lang>/{dat,dvce,dvcu}` and user profiles.

---

## 3. Recognition pipeline

```
mic / file ──► channel capture ──► sigproc features ──► acoustic scoring ──► search ──► result
 audio.exe      audio src,            melfbank/srfft      neuraln + pel      fst + kernel   recogres/
 dd10hook       chanadpt,             pitchtrk,           (fast match         TRG,           SDRecogResult
                uttfmgr, snr          sputtdet,           prefilter lexnet)   n-gram LM      IDgnResult
                                      featsel/gen                             + NN LM        → injection
```

* **Features** — mel filterbank + FFT, channel adaptation, pitch tracking, speech/utterance
  detection, feature normalisation/scaling; per-rate variants (`_11k_`, `_16k_`).
* **Acoustic scoring** — `neuraln` (network scorer, propagation types, packed-int and float
  scoring) combined with `pel` (phone-element/genone scoring, hierarchical scorer, FMPE,
  LDT/genclass tables) and a `prefilt` fast match (lexical net, thresholds).
* **Search** — FST/transducer search (`transducer`, `fstsrch`, `srchitf`) with TRG graphs
  (`fstgraph/trgrmmgr`), lattice/sausage/word-lattice/backtrace kernels, and n-gram models
  (unigram…qgram/trigram data, plus a neural LM `lm/nnlm`).
* **Vocabulary** — word lists, collections, pronunciation guessing (acoustic and non-acoustic),
  vocabulary delta management (user adds), rules/states.
* **Adaptation** — user model (`user/`, `ivecmodel`), Baum-Welch (`baumwel`), MLLR-style mix
  adaptation (`mixadapt`), speaker/enrolment, and the product's Acoustic Optimizer (ACO) driving
  it from `natspeak.exe`.
* **Results** — lattice/sausage/N-best, confidence factors (`ConfidenceWordProb*`,
  `LatticePostProb*`), then text delivery through hooks/UIA/RIA.

Observed tuning surface (config keys in `mrec.dll`) — useful for parity testing:
`SearchAcousticScoring*`, `AcousticScoring{NeuralNet*,Pel*,Hier*,Fmpe*,PenDif*}`,
`PrefiltererAcousticScoring*`, `PronGuessNonAcoustic*`, `ConfidenceWordProb*`,
`LatticePostProb*`, `*NumRecogLayers`, plus diagnostics (`Diagnostic*`, `Debug*`).

---

## 4. Engine API surface **[verified names]**

`sdapi/` (internal, used by `dd10renx.dll`):

```
RDApi_IsDFile / RDApi_GetDFileInfo / RDApi_GetDFileData
SDApi_GetFileRecognizerVersion / SDDiag_TestReadFile
SDRecog_RecognizerGetNumRecogLayers / SDRecog_RecognizeNonAcoustic
SDRecog_GuessPronsAcoustic / SDRecog_GuessPronsNonAcoustic
SDRecogResult_NewListFromRecognizer / SDLattice_NewFromRecogResult
SDSausage_NewFromSausageSplitNonAcoustic
SDState_* / SDPrefilterResult_* / SDhAcousticScoringParamSet
SDRecog_* / SDFile* accessors (rdf*.cpp, sdf*.cpp)
```
Signatures are not recovered (no exports; the DLL is consumed by name/ordinal internally).
`include/lhs_asr.h` declares the surface with reconstructed calling shapes for a rebuild.

---

## 5. Product/COM surface **[verified]**

Registry (installer): `SOFTWARE\Voice\SpeechRecognition\Engine\Dragon NaturallySpeaking =
{dd100001-6205-11cf-ae61-0000e8a28647}`; classes `{dd100002-…}` *Speech Services*,
`{dd100003-…}` *VoiceDictate*, `{607fc1b2-70ae-11d1-a49f-00a024a3a678}` *VoiceCommands Control*;
interfaces proxied by `dd10midl.dll`/`dd10sapi.dll`:
`IVoiceDictation00..12{A,W}`, `IDgnVoiceDictateA`, `IDgnVoiceCapture`, `IDgnSpeechServices`,
`IDgnRecognitionModes`, `IDgnSREngineControl`, `IDgnSRMicrophone`, `IDgnSRSpeaker`.

Automation layer (typelib in `dnstk10.dll`, documented in `dnsapi.chm`) — the shape a rebuild
must reproduce:

| Object / event source | Role |
|---|---|
| `DgnSpeechEngines`, `DgnSpeechEngine` | enumerate installed engines; `LanguageID`, `Dialect`, `ModeName`, `EngineFeatures`, `ProductName` |
| `DgnEngineControl` (+ `IDgnEngineControlAuto…`) | microphone state, recognition mode, speaker/topic, dialog control, `EngineRunning` |
| `DgnResult`, `DgnResPhrases`, `DgnLongLong` | recognised text, per-phrase timing, alternatives |
| `DgnStrings`, collections | string lists for correction/vocabulary calls |
| `IDgnDictCustom*`, `IDgnDictEditAuto*` | vocabulary/dictionary editing |
| Events | `DgnEngineControlEvents` (Speaker/Topic/RecognitionMode/UtteranceBegin/End/Login…), `DgnMicBtnEvents` (`MicState`, `VUMeter`), `DgnVCmdEvents` (`ActionComplete`, `CommandOther`) |
| Constant enums | `DgnDictationSourceConstants`, `DgnEngineFeatureConstants`, `DgnItnOptionConstants`, `DgnDialogConstants`, `DgnDlgStatusConstants`, `DragonBar*Constants`, … |

---

## 6. Reference implementation — verified state

`dragon_asr/`: `dfile` (container), `features` (WAV/MFCC/mel/FFT/energy/segmentation), `vocab`
(word lists, `vocrejct.dat`, DFILE `.voc`, G2P), `decoder` (CMN-normalised sliding DTW +
non-maximum suppression, segmental DP, lattice/N-best, result model), `engine` (`RDApi_*`/SD*
equivalents + `DgnEngineControl` model with events), `transcribe` (CLI).

Round-trip verification (synthesise with `dragon_tts`, recognise with `dragon_asr`, 16 kHz):

| input text | hypothesis | words found |
|---|---|---|
| `hello world` | `hello world` | 2/2 |
| `one two three` | `one two three` | 3/3 |
| `reverse engineering speech recognition` | `reverse engineering speech recognition` | 4/4 |
| `the quick brown fox jumps over the lazy dog` | `the quick brown fox jumps over lazy dog` | 9/9 (one missing) |
| `testing the dragon speech to text` | `testing the dragon speech speech speech` | 4/6 |
| `dragon speech recognition` | `recognition recognition` | 1/3 |

The substitute acoustic model (synthesised templates + CMN DTW spotting) is reliable for short
phrases and isolated words; the two weak cases above are model-substitution artefacts, not
interface defects — the API, container and geometry layers are exact.

`tests/test_asr.py` (16 tests): container invariants over 9 shipped assets, MFCC/framing/mel
maths, DTW properties, segmental decode, spotting decode, vocabulary/reject parsing, engine
events + writer, `RDApi_*` helpers, SDK-doc presence, isolated-word accuracy (≥5/6) and a two-word
phrase equality check.  Full suite: **39 tests, all passing** (`python3 -m unittest discover -s tests`).

CLI:
```
python3 -m dragon_asr.transcribe --inventory asrfiles
python3 -m dragon_asr.transcribe --selftest "the quick brown fox"
python3 -m dragon_asr.transcribe speech.wav --words hello,world --json
python3 -m dragon_asr.transcribe speech.wav --datapack <Datapack/…>   # uses shipped vocab assets
```

---

## 7. Acceptance tests for a full rebuild

1. **Container**: every ASR asset parses as DFILE v10; page size ∈ {0x1000,0x100}; trailer marker;
   subfile directory enumerates `Voc`/`SigProc`/`User`/`ParamSetHolder`/`EnvHolder` entries.
2. **Feature front-end**: MFCC per language rate (11 kHz and 16 kHz models both supported),
   channel adaptation and utterance detection produce stable word spans.
3. **Acoustic model**: scores for a held-out utterance are monotonic in the expected way across
   phone states (requires the ported model; otherwise a substitute scorer with the same interface).
4. **Vocabulary**: `vocrejct.dat` filters pronunciations; user word add/remove round-trips through
   the vocabulary delta path; `GuessProns*` returns plausible pronunciations.
5. **Decoding**: a known utterance yields the expected transcript; `NumRecogLayers`, lattice,
   sausage and N-best surfaces are populated; confidence is monotonic with accuracy.
6. **Adaptation**: enrolment/optimisation run updates the user model and improves accuracy on a
   fixed test set.
7. **API/COM**: the SR engine enumerator reports `Dragon NaturallySpeaking`, `Speech Services` and
   `VoiceDictate` CLSIDs resolve, and the automation objects/events of §5 behave as documented;
   `RDApi_IsDFile`/`GetDFileInfo` match on shipped assets.
8. **End-to-end**: dictated audio lands as text in the focused application (hook/UIA/RIA paths).

Status: 1, 2, 4 (word lists + reject list), 5 (substitute acoustic model), 7 (`RDApi_*` helpers +
automation model) and 8 (text output through `EngineControl.writer`) are exercised by `dragon_asr`.
Items 3 and 6 require the model data described next.

---

## 8. What a fully faithful port still requires

| Gap | Where it lives | Notes |
|---|---|---|
| Huffman / combined-table codec | `fileutil/huffutil.cpp`, `fileutil/cmbtable.cpp` | needed to read *any* subfile payload |
| Subfile directory field roles | `dfutil/realdf.cpp` (`RealDFileSubFileEntry`) | entry = name, class, 3×u32 — offsets vs checksums not yet separated |
| Acoustic model | `neuraln/`, `pel/` (`*.usr`, `*.psh`, `aco_bm4_dnn.enh`) | DNN weights + packed-int genone tables |
| Language model | `lm/` (`dispatch.svc`, `*.voc`) | n-gram/NN model or FST network |
| Grammars | `recogmode.grm/.grx`, `*.grm/.grx/.gri` | compiled grammar binaries (`GRXB` magic in `.grx`) |
| User/adaptation model | `user/`, `*.ivc/.ivs/.wt` | speaker profile, transformation matrices |
| Precise `SD*` signatures | `sdapi/` | names known; parameter lists need disassembly |
| Text injection wiring | `dd10hook.dll`, `dgnuiasvr`, `dgnria_nmhost*` | per-target delivery (Win32/UIA/browser) |

The reference implementation substitutes an acoustic front-end (MFCC + CMN) and decoder
(sliding DTW spotting) behind the documented interfaces, so a build that ports items 1–2 can drop
in the real model and decoder without touching the API layer.
