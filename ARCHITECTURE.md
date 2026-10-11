# Algorithm Architecture — how the proprietary Dragon/REC components work

Documentation only: this describes the **architecture and operation** of the four proprietary
subsystems (MREC recognition engine, DFILE payload codec, language model/search stack of MREC,
and the RealSpeak/Vocalizer speech coder).  It is meant to make a rebuild *designable*, not to
provide a port.  Evidence markers: **[verified]** read from shipped bytes/disassembly,
**[inferred]** consistent with module names/parameters/file structure but not observed executing,
**[open]** not yet pinned down.

Primary sources: the module map and parameter tables inside `mrec.dll`
(`decomp/mrec_*.c`, string tables), `dd10r*/au10r*` DLLs, the shipped model assets
(`asrfiles/`, `asrbins/`, `voicefiles/`), `dnsapi.chm`, and `ss_wind32.plh`.

Everything below is *tuning and structure*, which is exactly how the original is documented
internally: MREC is a **parameter-set driven engine** — each algorithmic block reads a named
`ParamSet` from the loaded model (`ParamSet`, `ParamSetHolder`, `AcousticScoringParamSet`,
`PrefiltererAcousticScoringParamSet`, `SearchAcousticScoring_%u`, `LMScoringParamSet`,
`SausageParamSet`, `ConfidenceParamSet`, `PronGuessParamSet`, `…ParamSetArrayText`), and the
engine can even rewrite defaults at load time ("Changed default value of Parameter… in param
set '%s'").  A rebuild reproduces behaviour by implementing the same named knobs.

---

## 1. Recognition engine — runtime topology **[verified]**

```
natspeak.exe (UI, mic, correction, injection)
   └── dd10r<lang>.dll            recognizer façade, per language (dd10renx/_x64, deu, esp, fra, ita, nld)
          └── au10r<lang>.dll     acoustic module, per language (2.8 MB)
                 └── mrec.dll      the engine: sdapi + recogctl + kernel + fst + lm + neuraln + pel
                                    + prefilt + sigproc + voc + word + fstgraph + user + channel
                                    + audiosrc + frame + fileutil + dfutil + mrecutil
```
* The engine is split into a **control plane** (`recogctl/recogctl.cpp`, `recogres`, `recoggrm`,
  `adaptctl`, `ivecmgr`) and a **data plane** (`kernel/`, `fst/`, `sigproc/`, `pel/`, `neuraln/`,
  `lm/`).
* Threading/objects: `mrecutil/dthread.cpp`, `dactor.cpp`, `dmstream.cpp`; long-running state is
  ref-counted and streamed (`dstream`, `dfstream`, `dendio`, `dsmstream`).
* Audio ingest: `audiosrc/{filesrc,extsrc,nistsrc,utbsrc,utdsrc}.cpp` (file / external / NIST /
  utterance-buffer sources) → `channel/{channel,chanmgr,utterance,filechan,warpchan,textchan,
  ivecchan,extchan}.cpp` → **utterance management** (`channel/uttfile.cpp`, `sigproc/uttfmgr.cpp`).
* Offline/embedded paths: recognition of a *channel* (stream) vs *file* vs *text*, and
  `RecognizeAcoustic` / `RecognizeNonAcoustic` entry points (`recogctl/…`) — i.e. the engine can
  score acoustically or fall back to a non-acoustic (LM/lexical) pass, used for transcription and
  for out-of-vocabulary/pronunciation guessing.
* Diagnostics/self test: `SDDiag_TestReadFile`, `Diagnostic*`/`Debug*` parameters,
  `mrecver.cpp` (version, `1.2.5.f-MREC-alien`), per-module stat counters
  (`nAcousticNetUnpackEndPicsUnpacked`, `NumRecognizedFrames`, `…WordStats`,
  `…ActiveTheoryCountLimit`, `…UnpackPicTreeSearches`).  This is the engine's own telemetry model
  and is the best source of *behavioural* truth when validating a rebuild.

---

## 2. Signal-processing front end (`sigproc/`) **[verified names, inferred flow]**

**Architecture:** a pipelined feature generator with per-frame stages and a parallel silence/pitch
tracker, all driven by `ParamSet`s.

1. **Frame/window assembly** — `frame/wavedata.cpp`, `wavepac`, `waveadj`, `melfbank.cpp`,
   `srfft.cpp`, `ctftmgr.cpp`, `ltf.cpp`, `ffv.cpp`, `specwarp.cpp`.
   - `srfft` = split-radix FFT; `melfbank` = mel filterbank; `specwarp` = spectral warping;
     `ctft` = short-time transform manager. Rate-specific models ship as `*_11k_*` and `*_16k_*`,
     so window length/hop/filterbank are model parameters, not constants.
   - `WaveDataDecodeTableText` indicates a per-format decode table (µ-law/A-law/16-bit variants),
     matching `MREC_V0_16BIT` and the `frame/wavedec.cpp` decoder.
2. **Feature post-processing chain** — `featsel/sigproc` select → `featgen` → `featfilt` →
   `featscal`/`featrang` (range normalisation) → `maxnorm` → `featcopy`; `featgen` also emits
   deltas/delta-deltas; `featscal` applies the scaling that `DecimationType`/`PackedInt` scoring
   depends on. `MulTable`/`multable.cpp` supplies the fixed-point multiplication tables used by
   packed-integer scoring.
3. **Pitch and voicing** — `pitchtrk.cpp`, `siltrack.cpp`, `snr.cpp`; gated by
   `ChannelEnablePitchTracking`. Used for silence detection and (in training) alignment.
4. **Utterance detection** (`sputtdet.cpp`) — an **adaptive background-noise model** rather than a
   fixed threshold. Evidence: `UttDetBackgroundLevel`, `UttDetBackgroundThreshPercent`,
   `UttDetBackgroundFloorIncreasePerSecond`, `UttDetBackgroundFloorDecreasePerSecond`,
   `UttDetBackgroundFloorOutlierWindowMicroseconds`, `UttDetBackgroundFloorOutlierMinTriggerPercent`;
   start decision uses speech-likeness criteria (`UttDetBeginUttMinEntropy`, `…MinPurity`,
   `…MinTriggerPercent`, `…MinVoiceRatio`, `…MinVoicingFrames`, `…PurityOverrideOffset`,
   `…WindowMicroseconds`), end decision is trigger-percent based (`UttDetEndUttMinTriggerPercent`).
   The `SilenceInitial` / `SilenceAfterSpeech` / `SilenceAfterSilence` states and a
   "silence tracker" shared with channel adaptation (`…UseSilenceTracker`) complete the model.
5. **Channel adaptation** (`chanadpt.cpp`, `chanest.cpp`) — three independent estimators
   (CDF, means, variance) each with relevance, smoothing, adaptation windows (in microseconds) and
   optional "adapt fast initially"; application can be delayed to utterance end and/or applied
   synchronously (`ChannelAdaptation…` parameters), and transforms are keyed by acoustic class
   (`ChannelClassNodeIdToTransformMap`) so different phone classes adapt differently.
   `ChannelConstrainedTransAcc` links this to the constrained transition-acoustic adaptation below.

---

## 3. Acoustic scoring (`pel/` + `neuraln/`) **[verified names, inferred flow]**

**Model objects:** `PelId`, `PelIdToBasePelIdMapping`, `PelIdToPelStatClassMap`, `PelNeuronGroup`,
`PelNeuronGroupToBasePelNeuronGroupMap`, `PelNeuronGroupToOutputNeuronGroupMap`,
`PelNeuronPriorationText`, `PelNeuronMappingText`, `PelNeuronsStartIndex`, `PelScores`,
`GenoneClassTree`, `GenoneComponentIndex`, `HierarchicalScorerText`.
So the acoustic model is a **genone/PEL hierarchy**: phone-element (genone) classes, each mapped to
neuron groups, with an explicit class tree and index, and text (human-readable) side-car tables.

**Two scoring paths** — the whole design is built around choosing between them per frame:
* **Neural-net scorer** (`neuraln/neuraln.cpp`, `nnscorer`, `pelnnsc`, `lmnnsc`, `adptnnsc`,
  `bcnnsc`, `nnclassmgr`, `nnpelmgr`, `bnfmgr`) — a feed-forward scorer over PEL neuron groups:
  * `AcousticScoringNeuralNetPropagationType` (sparse/full/hashed propagation),
  * `AcousticScoringNeuralNetFrameProcessInterval` (compute every *N*-th frame and interpolate),
  * `AcousticScoringNeuralNetSigmoidActivationThreshold` (activation gating),
  * `AcousticScoringNeuralNetNormalizeScores`, `…NeuralNetPelScoreScale`,
    `…NeuralNetUseBestMappedScore` (mapping neuron outputs to PEL scores),
  * `…NeuralNetSilencePelPriorPenalty`, `…NeuralNetBlankStatePelPriorPenalty` (priors),
  * `PrefiltererBuildDisableGPUScorer` / `AcousticScoringUseHardwareAcceleration`
    (the same scorer is acceleratable — GPU/vector path).
* **Genone/PEL scorer** (`pel/pelsc`, `hiersc`, `nnpelsc`, `fmpe`, `ldt`, `genclass`,
  `bet`, `multable`) — direct genone scoring with:
  * **Hierarchical scorer**: compute coarse class scores, then refine only components above a
    cutoff — `AcousticScoringHierScorerBeamList`, `…HierScorerCutoffRatioList`,
    `…HierInactiveComponentScore`, `AcousticScoringDisableHierarchicalScorer`,
    `…EnableGenoneScoreCacheThresh` (memoisation of genone scores),
  * `AcousticScoringDecimationType`, `…DropFrameTriggerWindowInFrames`
    (frame decimation/skipping), `AcousticScoringScoreAllPels`, `…PelScoreUseBestCompScore`,
    `AcousticScoringTruncatePackedIntComponentScores` (packed-int arithmetic),
    `DebugAcousticReferencePackedIntScoring` / `DebugAcousticFloatScoring` (the two arithmetics).
  * **FMPE** discriminative front end inside the scorer: `…FmpeThreshScore`,
    `…FmpeMinimumPosteriorProbability`.
* **Duration model / penalties:** `AcousticScoringPenDif`, `…PenDifDurLogTerm`
  (per-PEL duration penalty with a log-duration term), `ComputeWeightsIdChecksum(s)` for the
  weight tables.
* **Adaptation folded into scoring:** `HierScorerChannelTransAccMatrix/Offset/LogDeterminant` and
  `HierScorerSpeakerTransAccMatrix/Offset/LogDeterminant` show the transform is applied *inside*
  the hierarchical scorer for both channel and speaker spaces.

---

## 4. Fast match / prefilter (`prefilt/`) **[verified names, inferred flow]**

Purpose: cheaply reduce the candidate word/PEL space before full scoring.
* Components: `prefilt.cpp`, `lexnet.cpp` (lexical network), `treepref.cpp` (tree prefilter),
  `lookahsc.cpp` (look-ahead scoring), `thrdpref.cpp` (threaded prefilter), `retlist.cpp`
  (returned candidate list).
* **Build-time configuration** (`PrefiltererBuild…`): context type, `MaxPronLength`,
  `AllowMultiPhonemeSequences` + `BestScoreAdditionalPhoneme{Score,StartDepth}`,
  `DurationScoreScale`, `MaxWordEndScore`, look-ahead scoring/threading type, model type
  (`PrefiltererBuildModelType`-family), GPU/hierarchical-scorer disables, and a full copy of the
  acoustic-scoring parameter set (`PrefiltererBuildAcousticScoring…`).
* **Runtime**: `PrefiltererActiveNodeCountLimit`, `…ScorerType`/`…Type`,
  adaptive thresholds (`PrefiltererAdjustThreshScoreType`), first-node skipping, additional-phoneme
  best-score accounting, and `PrefiltererAllowFirstNodeSkipping`.
* Architectural consequence: the search consumes a **candidate list per frame** with per-candidate
  scores, not a full PEL matrix — this is the main performance shape of the engine.

---

## 5. Search (`kernel/` + `fst/` + `fstgraph/`) **[verified names, inferred flow]**

Two cooperating layers:

1. **FST/transducer search ("Attila" core)** — `fst/fstcore.cpp`, `transducer.cpp`, `srchitf.cpp`,
   `fstsrch.cpp`.  Parameters (all verified strings) describe the algorithm precisely:
   * beam management: `FstCoreBeam`, `FstCoreBeamIncrement`, `FstCoreLatticeBeam`,
     `FstCoreBackoffThreshScore`, `FstCoreMaxActive`,
   * token propagation: `FstCoreHashTokens`, `FstCoreHashPropagateBestTokensFirst`,
     `FstCoreLockPelScoreCache`,
   * scoring: `FstCorePelScoreScale`, `FstCorePerWordPenalty`,
     `FstCoreEmbeddedTransitionScoreRatio`, `FstCoreUseDurationModel`,
   * output: `FstCoreGenerateLattice`, `FstCoreGenerateLeafLattice`, `FstCoreLatticeGeneration`,
     `FstCoreNBest`, `FstCoreUseFwdBwdAlgorithm` (forward-backward for posteriors),
     `FstCoreRepeatFrame`, `FstCoreCollectTracesPeriod`,
     `FstCoreLateLatticeArcFingerSuckPenalty` (pruning of late lattice arcs),
     `FstCoreAttilaLatticeOutputFileNamePrefix`, `FstCoreSearchType`.
2. **Graph construction / composition** — `fstgraph/trgrm.cpp`, `trgrmmgr.cpp` (TRG = tree/trigram
   language-model graph), `unibuilder.cpp`, `leafgraph.cpp`, `unicomposer.cpp`,
   `unisequencer.cpp`, `chunkalloc.cpp` (arena allocation).  So the LM is compiled into an FST
   graph (see §6: `lm/ngram2fsm.cpp`) that is composed with the lexicon/grammar for search.
3. **Active-node bookkeeping** (`kernel/active.cpp`, `recog.cpp`, `searchlm.cpp`, `latutil.cpp`,
   `constran.cpp`) — histories/state merging and limits: `SearchActiveNodeCountLimit`,
   `…SequenceCountLimit`, `…PreserveMultipleHistories`, `…UseHash`,
   `…HasMergedGrammarState`/`HasMergedLatticeLinkId`, `…HasPredecessorWordContext`/
   `…PredecessorPhonemeContext`, `…SpreadSeedScore`, adaptive threshold updates
   (`SearchAdaptiveThreshScoreIncrementPerFrame`, `SearchAdjustThreshScoreType`), and abort
   guards (`SearchAbortOnNumHistories`, `…NumRecognizedFrames`,
   `…AverageWordsEndingPerFrame`, `…AllocBytes`).
4. **Constrained-transition acoustic adaptation inside search** (`SearchConTransAcc*`):
   decay factor/frequency, factor list, max silence frames, ramp frames, and a per-channel
   accumulator — this is how the engine adapts acoustic transforms *during* recognition
   (`ConstrainedTransAcc`, `AdaptationConTransAcc*`).
5. **Latticing** (`kernel/lattice.cpp`, `wordlat.cpp`, `btlat.cpp`, `phnlat.cpp`,
   `crumb.cpp`, `sausage.cpp`) — word lattice with backtrace, phoneme lattice, "crumbs" (partial
   hypotheses) and **sausage** (per-word alternative sets) used for confidence/correction.

---

## 6. Language model (`lm/`) **[verified names, inferred architecture]**

* **Data containers**: `ngramdata.cpp`, `bgramdata.cpp`, `tgramdata.cpp`, `qgramdata.cpp` plus
  binary/on-disk forms `LMWNGBIN`, `LMWNGDYN`, `LMWNGIND`, `LMWNGPER`, `LMWTTBIN`, `LMWBSBIN`,
  `LMMULBIN`, and neural-net LM forms `LMNNBIN`, `LMNNSTA`, `LMNNDYN`.  This is a
  **memory-mapped / paged n-gram store with dynamic read caches**
  (`LMScoringDynamic*ReadCacheMaxBlocks`, `…BlocksPerChunk`, per-order `…MaxChunksPerRead`),
  i.e. the big `dispatch.svc` file is paged rather than fully resident.
* **Backoff / staticisation**: `LMStaticizationNoGroupingSize` — n-grams are grouped/staticised at
  load, with grouping disabled below a size, so the runtime lookup is an array/hash rather than a
  tree.
* **Scoring modes** (`lm/wordlm.cpp`, `multilm.cpp`, `dummylm.cpp`, `lmcommon.cpp`):
  `LMScoringUseCorrective`, `LMScoringUseNeuralNet`, `LMScoringUseRecent` (recent-buffer LM),
  `LMScoringMaxDepth`, `LMScoringLMDisallowedLMScore`, `LMScoringInitialSilenceToFinalSilenceLMScore`,
  `LMScale`.  So the effective LM score = weighted combination of an n-gram model, an optional
  corrective model (built from user corrections), an optional neural-net LM (`nnlm.cpp`,
  `lmnnsc.cpp`), and a recent-history model — all combined at word transitions.
* **Export to search**: `ngram2fsm.cpp` (+ `BuildMaximumNum{Bi,Tri}gramRecords`) converts the
  n-gram model into an FSM with bounded record counts — the bridge to §5's TRG graphs.
* **Vocabulary side** (`word/`, `voc/`): `wordlist`, `collmgr`/`collarra` (word collections used for
  topic/domain scoping), `corpus`, `charinfo`; `voc/voc.cpp`, `vocmgr`, `vocdelta(+mgr)`,
  `statemgr`, `rulemgr`, `rpd` (?), `vcc`, `tsr`, `vgroupi` — vocabulary, vocabulary deltas
  (user additions), pronunciation/state rules, and the vocabulary editor's data model
  (`voctools.dll`).

---

## 7. Confidence **[verified names, inferred model]**

Confidence is a **feature-weighted logistic model**, not a lookup table:
* word level: `ConfidenceWordProbAcousticScoreFactor`, `…LCntx1Factor`, `…LCntx2Factor`,
  `…RCntx1Factor`, `…RCntx2Factor`, `…AverageAcousticScore*Factor`;
* utterance level: `ConfidenceUttProb{ConstantTerm, LogWordCountFactor, AverageLMScoreFactor,
  AverageDurationFactor, AverageActiveNodeCountFactor, MaxWordsEndingFactor, TotalLMScoreFactor,
  UttAverageAMinusBScoreFactor, NBest{Min,Max,HighCount,LowCount,HighThresh,LowThresh,
  WordAverage}Factor}`;
* enabled per scope: `ConfidenceEnableWordLevel`, `…UttLevel`, `…ForAllNBest`.
Read the parameter list as the model: log-linear scoring over acoustic/LM/duration/N-best/
active-node features, with separate calibrations for word and utterance scope.

---

## 8. Adaptation and enrolment **[verified names, inferred architecture]**

* **Objectives**: `AdaptUserMeans`, `AdaptUserDevs`, `AdaptUserMixtureWeights`,
  `AdaptUserDurations`, `AdaptMethod`, `AdaptAlignment`, `AdaptHolder` — i.e. mean/variance/weight/
  duration adaptation with explicit alignment.
* **Accumulation statistics**: `AdaptationAccumulate{Left,Right}GenericBackoffPels`,
  `…DoublyGenericBackoffPels`, `AdaptationAccumulatorScale` — statistics are accumulated over
  PEL/genone classes with generic backoff for unseen contexts.
* **Transforms**: `AdaptationConTransAcc*` — compute transform matrices/translations/diagonals,
  optional global transform, cofactor-optimisation economisation, per-genone-class minimum frame
  counts, global smoothing and decay (`AdaptationConTransAccDecayFactor/Frequency`,
  `…RampFrames`, `…MaxSilenceFrames`, `…ComponentBackoffPercent`,
  `…DecomposedAccumCutoffPercent`) — an MLLR-style constrained transform estimator, applied at
  speaker and channel levels (see §3's `HierScorer…TransAcc*`).
* **Baum-Welch** (unsupervised/semi-supervised): `AdaptationBaumWelch{Scale,ThreshScore,
  MaxUttSeconds}` — re-estimation over lattices with score thresholds.
* **User model storage**: `user/user.cpp`, `usermgr.cpp`, `picmgr.cpp`, `ivecmodel.cpp`,
  `ocntrnac.cpp`, `lookah.cpp`, `fileutil/adpttype.cpp`, `dsmset.cpp`, `dsmsetdata.cpp`
  (the `.ivc`/`.ivs`/`.wt` assets and the adaptation type/set serialisation).  The product drives
  this from `natspeak.exe`'s Acoustic Optimizer (ACO) and the scheduled optimizer
  (`schedmgr.exe`).

---

## 9. Grammar / recognition-mode files **[verified]**

* `.grm` (e.g. `recogmode.grm`, 6.6 KB): tagged compiled grammar. Observed content: control
  commands `Start`, `RecognitionModeCmd`, `TextFormattingCmd`, `ModeOn`, `ModeOff`, `RecogMode`,
  `RecogModeNew`, `StartV/VI`, `ExitV/VI`, `BackTo`, `Normal` — i.e. the "recognition mode"
  command grammar (normal / numbers / spell).
* `.grx` (3.95 KB): starts with ASCII `GRXB`, then a header and a **symbol table**
  (`Start`, `RecogModeNew`, `Normal`, `#I10`, `RecogMode`, `Numbers`, `Zahlenmodus`, `Spell`,
  `Buchstabiermodus`, `Comman…`) — a grammar-exchange format carrying the language-specific
  command words (note the German strings).
* `.gri` (10 KB): application/window targeting records (`WAOL|7 America Online 7 aol7`,
  `.SpecialNLP`, dictation-box mode…) — the command-target database.

---

## 10. DFILE payload codec **[partly verified]**

**Architecture**
* Container: fixed header + page-aligned payload + trailer marker; a **subfile directory**
  (`RealDFileSubFileEntry`: name, class, three numeric fields), classes `Voc`, `SigProc`, `User`,
  `ParamSetHolder`, `EnvHolder`.
* Each subfile declares a **coding variant** (codec selector low byte, values observed 2–6, 9,
  0x12) dispatched from `RealDFile::open`; the utilities are
  `fileutil/huffutil.cpp`, `fileutil/cmbtable.cpp` and `dfutil/encrypt.cpp` — i.e. an
  **entropy-coded (Huffman) payload with a combined lookup table**, optionally passed through an
  encryption/obfuscation stage.  `DebugShowDFileSubfileChecksums` shows per-subfile checksums are
  stored, so the footer/trailer carries integrity data.
* Measured: payloads are **not uniformly coded** — `token_s.enh` (84/89 pages), `lexicon.enh`
  (22/27), `.usr` (3619/3674) are coded; `*_p2.psh`, `vocdelta.voc`, `aco_bm4_dnn.enh` are
  readable numeric tables (H 1.75–4.76).  Coded pages begin with a small header followed by
  high-entropy data (first coded page of `token_s.enh`:
  `81 40 5e 78 78 78 78 78 36 d5 cd 58 …`, i.e. a byte-pair header then dense stream).
* **[open]** the exact Huffman table layout (code-length vector vs canonical table block) and the
  combined-table semantics; they are pinned down by disassembling the
  `RealDFile::open` selector handler and the subfile-stream `Read` in `mrec.dll`
  (both are in the already-imported Ghidra project `asrproj`).
* Also used outside DFILE: the `66 B6 01 01` wrapper for `models.ini`/`server.ini`
  (`MergeIniEncrypted` install action), separate from the DFILE codec.

---

## 11. TTS engine architecture **[verified]**

**Component/technology model (LHKP, `ss_wind32.plh`)** — the engine's own capability matrix:

| TECID | Technology | Broker string | languages |
|---|---|---|---|
| 0 | (empty) | — | 0 |
| 1 | TTS3000 SDK for Windows V7 | `eng/tts3000v7` | 10 |
| 2 | RealSpeak SDK for Windows V2 | `eng/rsstdv2` | 20 |
| 3 | RealSpeak Compact SDK for Windows V2 | `eng/rscompv2` | 6 |
| 102 / 258 | ASR1600 SDK for Windows V4 | `eng/asr1600` | 14 |
| 103 / 259 | ASR1600 Feature Extractor Add-on V4 | `eng/fx1600` | 14 |

(102/103 vs 258/259 are the 32/64-bit pairs; component-type broker strings:
`VOICEFONT`, `G2P`, `CONV`, `LNG`, `SPELLING`, `GRAMMAR_COMPILER`.)  So a *voice* is assembled
from a **voice-font** component plus language/G2P/conversion components, resolved through the
broker by name — the same descriptor mechanism documented in `SPEC_TTS.md` §5.

**Voice font** (`VFNTLIST`): `VINF` (metadata), `CODR` (41,032 B codebook), `CTUN` (unit count),
`UNIT` (8.000 B/unit index), `SPCH` (coded speech, 152.825 B/frame).
**Unit-selection database** (`USELLIST`): `VCAT` (tagged property catalogue), `PHIS`, `CNT0`,
`TAB0`, `PREF/TRAF/FUZZ/CTXR` (numeric tables), `SYMB` (9.000 B/unit symbols),
`UNIT` (20.000 B/unit), `CDBK` (49,152 B codebook), `TRI ` (30.671 B/unit), `CCOS`.
So synthesis is **unit-selection + a parametric/prototype coder**: unit selection picks units from
the database (20 B/unit records) whose coded speech lives in the voice font (8 B/unit index into
152.825 B frames).

**Coder families:** `155mrf22` @270 kb/s (⇒ 220.84 frames/s, 4.53 ms frames, ~20.9 min bank) and
`vssq5f22`; `EXHD` names the configuration (`pls65_dsp179 noi65 sr22 bnd_34` — `sr22` = 22.05 kHz).
Decoder components: `rsstdv2_dec11.dll` (`gsmc_GetInterface`, broker `decoder/83swf11`) and the
`vautov5.dll` `xcoder`/`xcoder_mrksync` components (pipeline manifest, `SPEC_TTS.md` §5.4).
**[open]** the per-frame bit allocation (codebook index + gains/pitch/etc.) and `CODR`/`CDBK`
layout — pinned by disassembling `gsmc_*` in the 49 KB `rsstdv2_dec11.dll` and the `xcoder`
component in `vautov5.dll`.

---

## 12. Reading order for a rebuild

1. **Front end**: §2 (frames → features → channel adaptation → utterance detection).
2. **Acoustics**: §3 (choose a scorer: NN and/or genone with hierarchical refinement + FMPE).
3. **Fast match**: §4 (candidate generation) — optional at first; a rebuild can skip it.
4. **Search + LM**: §5–§6 (FST beam search over an LM→FSG graph; start with a bigram model).
5. **Results**: §7 (lattice → sausage → N-best + logistic confidence).
6. **Adaptation**: §8 (MAP/MLLR-style transforms, Baum-Welch, user model files).
7. **Codec/format plumbing**: §10 (DFILE) and `SPEC_TTS.md` §11 (LHKP/voice font) as needed.
8. **TTS side**: `SPEC_TTS.md` (§3–§5, §14) for the API/descriptor/pipeline, and §11 here for the
   voice-font/coder architecture.

Every algorithm above is defined by (a) module boundaries, (b) data structures, (c) the named
parameter sets — all three are listed here, which is what makes the behaviour reproducible without
the original source.
