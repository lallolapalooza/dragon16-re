# TTS Implementation Specification — Dragon/RealSpeak (Dragon 16 media)

This is the implementation-grade companion to `RE_REPORT.md`. Together they are intended to let an
engineer rebuild a functionally equivalent **text-to-speech subsystem** with no further access to
the original media. Everything here is stated at interface/format level; no proprietary source is
reproduced.

Evidence sources (in-repo): `msi_tables/*.csv`, `decomp/*.c`, `voicefiles/*`, `allprog/*`,
`datfile.py`. Confidence markers: **[verified]** = read from shipped bytes/disassembly,
**[inferred]** = consistent with evidence but not directly observed.

---

## 1. Deliverable definition

A rebuild must provide, in this order of importance:

1. **`lhs_tts*` C API** (35 exports, stdcall) — the API the product's UI actually calls.
2. **`ttscore` engine interface** behind that API (the `Core*` object).
3. **A descriptor/broker layer** resolving `broker strings` (e.g. `TTSENGINE`, `ttscore`) to
   components using `.hdr` files.
4. **A TTS pipeline** consuming a voice bank and emitting 22050 Hz 16-bit mono PCM.
5. **A SAPI4 engine module** (`rsstdv2.dll` equivalent) so third-party SAPI4 applications and the
   product's own `ttssapi.cpp` client path work.
6. **Asset compatibility**: read the shipped `.dat`/`.hdr`/`.plh` files (formats below).

Reference implementation in this repo: `dragon_tts/` (Python 3, stdlib only) — a runnable
demonstration of 1–4 plus the SAPI4 model in `sapi4.py`, verified by `tests/test_tts.py`
and `python3 -m dragon_tts.say`.

---

## 2. Component graph

```
                ┌──────────────────────── consumer (Dragon UI / SAPI4 app) ────────────────────────┐
                │  natspeak.exe: ttssapi.cpp (SAPI4 client)   opttts.cpp (legacy C API client)     │
                └───────┬──────────────────────────────────────────────┬──────────────────────────┘
                        │ COM: ITTSEnum/ITTSCentral/IAttributes        │ C: lhs_tts* (stdcall)
                        ▼                                              ▼
        rsstdv2.dll  (SAPI4 module, in-proc COM server)        lhsp01.dll (facade/dispatcher)
          CTTSInstRealSpeak / CTTSInstVauto5                        │ LoadLibraryA + GetProcAddress
          registration: HKCR CLSID + HKLM\Software\Voice\...        ▼
                        │                                    lhtts01.dll  (L&H TTS API layer, lhs_tts*)
                        │ vauto_ttsProcessText2Speech              │ brk_* calls
                        ▼                                          ▼
                    vautov5.dll  (pipeline runtime)         lhcom02W.dll (broker: *.hdr discovery)
                      component registry (*_GetInterface)         │ resolves broker string → (dll, entry)
                        │                                          ▼
                        │                                   rsstdv2_core.dll  (`ttscore` object)
                        └─────────────── voice bank ◄─────────────┘  (+ rsstdv2_dec11.dll = decoder)
                     Program\Voices\speech\{components,vautov5}  and  Program\lhsp\rsstdv2\common
```

Two independent ways in, one shared engine core. A rebuild may keep only one path initially, but
must keep the **API shapes** of both because the product selects between them at runtime.

---

## 3. `lhs_tts*` API (`lhtts01.dll`, stdcall, 32-bit) **[verified]**

`lhsp01.dll` re-exports these after dynamically loading `lhsp\lhtts01.dll`; consumers link
`lhsp01.dll`. Decorations are as observed in the PE export tables.

| Export (stdcall, `@bytes`) | Signature (reconstructed) | Notes |
|---|---|---|
| `lhs_InitializeEx@16` | `int lhs_InitializeEx(uint flags, const char* key, int count, void** out)` | `key` is the obfuscated 139-byte blob in the product **[verified]**, semantics unknown **[inferred: licence/OEM key]**. 0 = ok, 0xC9 also tolerated by the caller. |
| `lhs_Initialize@8` | `int lhs_Initialize(uint flags, void** out)` | same, no key |
| `lhs_UnInitialize@4` | `int lhs_UnInitialize(void* h)` | |
| `lhs_ErrorGetStringA@4` / `W@4` | `const char*/wchar_t* lhs_ErrorGetString(A/W)(int code)` | LHS_E_* → text |
| `lhs_ttsOpenA@16` / `W@16` | `int lhs_ttsOpen(A/W)(void* engine, const char*/wchar_t* voice, void** inst, uint flags, uint /*2nd flag*/)` | **4th arg observed as 0**; `voice` string observed = `"RealSpeak Standard v2"`. On success writes the instance handle. |
| `lhs_ttsClose@4` | `int lhs_ttsClose(void* inst)` | |
| `lhs_ttsSetTextA@16` / `W@16` | `int lhs_ttsSetText(A/W)(void* inst, const char*/wchar_t* text, uint cb, void* unused)` | text buffer + byte length |
| `lhs_ttsSpeak@8` | `int lhs_ttsSpeak(void* inst, uint mode)` | starts async synthesis/playback |
| `lhs_ttsStop@4` | `int lhs_ttsStop(void* inst)` | |
| `lhs_ttsPause@4` / `lhs_ttsResume@4` | `int lhs_ttsPause/Resume(void* inst)` | |
| `lhs_ttsProcess@8` | `int lhs_ttsProcess(void* inst, uint) ` | pump/step |
| `lhs_ttsSetParamListA@12` / `W@12` | `int lhs_ttsSetParamList(A/W)(void* inst, TTS_PARAM* list, uint count)` | see §3.1 |
| `lhs_ttsGetParamListA@12` / `W@12` | `int lhs_ttsGetParamList(A/W)(void* inst, TTS_PARAM* list, uint count)` | |
| `lhs_ttsGetPPListA@20` / `W@20` | `int lhs_ttsGetPPList(A/W)(void* inst, ...)` | base-unit (phoneme) list |
| `lhs_ttsGetVoiceListA@20` / `W@20` | `int lhs_ttsGetVoiceList(A/W)(void* inst, ...)` | |
| `lhs_ttsSetDictionaries@12` | `int lhs_ttsSetDictionaries(void* inst, void* list, uint count)` | |
| `lhs_ttsSetInDevice@8` / `lhs_ttsSetOutDevice@8` | `int lhs_ttsSet(In/Out)Device(void* inst, void* desc)` | output uses a callback vtable (`{fn, cookie}`) **[verified]** |
| `lhs_ttsGenOutputFileA@16` / `W@16` | `int lhs_ttsGenOutputFile(A/W)(void* inst, const char*/wchar_t* path, uint fmt, uint)` | writes RIFF/WAVE |
| `lhs_ttsGenPhoneticStringA@20` / `W@20` | `int lhs_ttsGenPhoneticString(A/W)(void* inst, const char*/wchar_t* text, char*/wchar_t* out, uint cch, uint flags)` | |
| `lhs_ttsGetInstStatus@8` | `int lhs_ttsGetInstStatus(void* inst, uint* status)` | status codes checked against 2/0x10/8 ("busy") **[verified]** |
| `lhs_ttsGetLipSyncInfo@12` | `int lhs_ttsGetLipSyncInfo(void* inst, void* info, uint)` | |
| `lhs_ttsLoadRuleTableA@12` / `W@12` | `int lhs_ttsLoadRuleTable(A/W)(void* inst, const char*/wchar_t* path, uint)` | rule/parse tables via broker `ilr/*` |
| `lhs_ttsUnLoadRuleTable@8`, `lhs_ttsUnLoadAllRuleTables@4` | `int ...(void* inst[, void* table])` | |
| `lhs_ttsDetectLanguageA@24` / `W@24` | `int lhs_ttsDetectLanguage(A/W)(void* inst, const char*/wchar_t* text, uint cb, uint* lang, uint flags, void*)` | |
| `lhs_ttsForward@12` / `lhs_ttsBackward@12` | `int lhs_ttsForward/Backward(void* inst, uint n, uint units)` | unit-level navigation |

Global lifecycle: two critical sections guard module state; instances are created per `Open`
call; all entry points validate the handle first (invalid → `LHS_E_INVALIDHANDLE`).

### 3.1 Parameter entries

`SetParamListW(inst, list, count)` receives an array of fixed-stride records (**stride
0x21 dwords = 132 bytes** **[verified]**) whose first dword is a state/type field (values seen:
2, 8, 0x10 = "instance busy" guards). Observed parameter ids, passed as `{id, value}` pairs:

| id | meaning | value observed |
|---|---|---|
| `0x400` | language / voice selection | wide string, e.g. `L"American English"`, `L"British English"` |
| `0x2000` | output configuration | `0x202` (unpacked further by the engine) **[inferred: format/channel flags]** |

Error codes (`LHS_E_*`, verified by name in the binary): `INVALIDHANDLE(0xb)`,
`NULLPOINTER(0xd?)`, `INVALIDARG(0xc)`, `BUFFERTOOSMALL`, `CONVERSIONFAILED`, `BADCOMMAND`,
`MEMALLOC`, `TTS_INSTBUSY`, `TTS_NOLANGUAGE`. API-level returns also include 0 (ok),
1 (generic failure), 3, 4, 5, 6, 8, 0xe and `0x17` **[verified]**.

### 3.2 Text control escapes **[verified]**

```
\Chr=  \Com=  \Ctx=  \Dem   \Emp   \Eng:  \Eng;  \Mrk=  \Pau=  \Pit=  \Pra=  \Prn=  \Pro=
\Prt=  \RmS=  \RmW=  \RPit=  \RPrn=  \RSpd=  \Rst   \Spd=  \Vce=  \Vol=
```
A rebuild should accept and either honour (speed/pitch/volume/pause) or strip them.

---

## 4. `ttscore` engine object (`rsstdv2_core.dll`)

Exports (22) **[verified]**: `CoreInitialize, CoreUninitialize, CoreOpen, CoreClose, CoreSetText,
CoreForward, CoreBackward, CoreReset, CoreStop, CoreTranscribe, CoreGetPcmBuf, CoreGetInterface,
CoreGetParam, CoreSetParam, CoreGetPPList, CoreGetVoiceList, CoreGetLanguageList,
CoreSetDictionaries, CoreSetDictionaryCb, Core_ErrorGetString, Core_ErrorGetModuleName,
Core_ErrorGetSubModuleName`.

`CoreGetInterface(name, out)` returns the interface table; the layer above requests
`brk_InterfaceQuery(engine, "ttscore", 1, &iid, &iface)`.

**Interface slots observed in use** (offsets into the vtable) **[verified]** — any re-implementation
that wants to be drop-in for `lhtts01.dll` must match these:

| slot | observed call | role |
|---|---|---|
| `+0x1c` | `fn(this->+0x44, uVar5)` | **close/reset core** (paired with `CloseCore` logging) |
| `+0x24` | `fn(&this->+0x44, param)` | **open/init core** |
| `+0x28` | `fn(this->+0x54, cb1, cb2)` | **register callbacks** (error/notify) |
| `+0x2c` | `fn()` | **final release** of the core object |
| `+0x48` | `fn(this->+0x44, this->+0x48, value, ptr)` | **set parameter** (also used by dictionary load) |
| `+0x54` | `fn(this->+0x44, this->+0x48, buf, len)` | **set text** (buffer + length) |

`this->+0x44/+0x48` is a 64-bit handle pair (`g_lhNullHandle` sentinel) passed back into the core.
Slots beyond these are unused by the TTS API layer in this build.

A `C`-level header for this ABI is in `include/lhs_tts.h`.

---

## 5. Broker and descriptors (`lhcom02W.dll`, `.hdr` files)

### 5.1 API **[verified exports]**
```
brk_ObjOpen/ObjOpenEx/ObjClose        brk_InterfaceQuery/InterfaceRelease
brk_InfoQuery/InfoRelease             brk_BrokerStringQuery/BrokerStringRelease
brk_ClassOpen/ClassOpenEx             brk_ComponentListQuery
brk_DataOpen/DataSizeQuery            brk_TagQuery      brk_AddEngineToClass
rsrc_Open  hdrpars_OpenData  hdrpars_OpenResource
edct_DctMgr*/edct_Dct*  srvspi_*
```

### 5.2 Discovery **[verified]**
* The broker enumerates components from **files**, not the registry: it imports no registry API;
  it uses `FindFirstFileA/FindNextFileA` to scan for `*.hdr` and `*.dat`, recursing into
  sub-directories, and `fopen/_wfopen` to read them.
* Search roots may be overridden by environment variables read with `_wgetenv`/`getenv`:
  `LH_MODULES`, `LH_SUBMODULES`, `LH_INPATH`, `LH_OUTPATH`; tracing by `LH_TRACELEVEL`
  (values/filters `FUNC|MODULE|SUBMODULE|INST|FILE|LINE|LEVEL|TIME|none|full`) and
  `LH_TRACEINFO`; log target `LH_LOGFILE` (default = stdout stream slot). The product ships none
  of these set, so default roots come from the caller (module directories; the broker also builds
  paths by appending `shared` to a base path it was given) **[inferred for the exact default]**.
* Allowed-component licensing checks exist (messages about "list of allowed components" and
  expiry dates) — a rebuild may treat these as no-ops.

### 5.3 `.hdr` descriptor — schema A, "LHS" **[verified]**
UTF-16LE XML, one per component DLL:
```xml
<LHS><VERSION>LH_1.0</VERSION><HEADER>
  <COMPTYPE>CORE</COMPTYPE>          <!-- CORE | DECODER | LHKP LIST | ... -->
  <VERSION>2.0</VERSION>
  <NAME>Core Module</NAME>
  <LOCATION>United States</LOCATION>
  <TTSTYPE>REALSPEAK STANDARD</TTSTYPE>
  <BROKERSTRING>ttscore</BROKERSTRING>   <!-- key for InterfaceQuery -->
  <ENGINE>eng/rsstdv2</ENGINE>           <!-- engine group id -->
  <DLL>rsstdv2_core.dll</DLL>
  <GETINTERFACE>CoreGetInterface</GETINTERFACE>  <!-- entry point in DLL -->
</HEADER></LHS>
```
Shipped examples: `rsstdv2_core.hdr` (COMPTYPE `CORE`, broker `ttscore`, engine `eng/rsstdv2`,
entry `CoreGetInterface`), `rsstdv2_dec11.hdr` (COMPTYPE `DECODER`, broker `decoder/83swf11`,
entry `gsmc_GetInterface`).

Resolution algorithm a rebuild must imitate:
```
engine_id = BrokerStringQuery("TTSENGINE", language)      # e.g. "eng/rsstdv2"
obj       = ObjOpenEx(engine_id)                           # loads the .hdr-declared DLL
iface     = InterfaceQuery(obj, broker_string, iid, &out)   # e.g. "ttscore"
```
Known broker strings: `TTSENGINE`, `ttscore`, `filter`, `decoder/83swf11`, `ilr`, `ilr/mf`,
`ilr/lr`, `ilr/data`, `pipeline/<Language>/<voice>/22/<model>/text/pcm`, `userdct/<lang>`,
`audiofetch`, `dcteg`, `domain_mngr`, `fe/fe_dctlkp`, `fe/fe_depes`, `inetspi`, `phonmap`.

### 5.4 `.hdr` descriptor — schema B, "NUANCE" (pipeline manifest) **[verified]**
ASCII XML, one per voice/model:
```xml
<NUANCE><VERSION>NUAN_1.0</VERSION><HEADER>
 <BROKERSTRING>pipeline/American English/samantha/22/full_155mrf22/text/pcm</BROKERSTRING>
 <PRIORITY>4312</PRIORITY>
 <PARAMETERS>
   <language>American English</language><langcode>ENU</langcode><langid>10</langid>
   <nativetypeofchar>utf-16</nativetypeofchar>
   <voice>Samantha</voice><gender>Female</gender><age>Adult</age>
   <fecfg>cfg3</fecfg><voiceml>no</voiceml><fedatapackaging>clc</fedatapackaging>
   <fevoice>Samantha</fevoice><frequencyhz>22050</frequencyhz>
   <voicemodel>full_155mrf22</voicemodel><reduction>full</reduction>
   <coder>155mrf22</coder><bitrate>270</bitrate><overheadframes>06</overheadframes>
   <audiooutputmimetype>audio/L16;rate=22050</audiooutputmimetype>
   <audiooutputsamplebits>16</audiooutputsamplebits>
 </PARAMETERS>
 <OBJECTS><AUDIOFETCHER>audiofetch</AUDIOFETCHER><DCTEG>dcteg</DCTEG>
   <DOMAINMNGR>domain_mngr</DOMAINMNGR><FE_DCTLKP>fe/fe_dctlkp</FE_DCTLKP>
   <FE_DEPES>fe/fe_depes</FE_DEPES><INET>inetspi</INET><PHONMAP>phonmap</PHONMAP></OBJECTS>
 <COMPONENTS>xcoder, ttt/re, pp/text, fe/fe_lid, fe/fe_udwl, fe/fe_clcml,
   fe/fe_promptoriorth, fe/fe_unixlit, fe/fe_initlingdb, fe/fe_promptorth, fe/tokentn,
   fe/fe_abbrtn, fe/fe_puncsptn, fe/fe_oneword, fe/fe_pos, fe/fe_hmogrph, fe/fe_phrasing,
   fe/fe_normout, fe/fe_prompt, fe/fe_global, fe/fe_deinitlingdb, uselect/bet1, synth/bet1,
   audioinserter, phonmap/mrk, xcoder/mrksync</COMPONENTS>
 <RESOURCES><RESOURCE content-type="application/edct-bin-dictionary;loader=broker">
   userdct/enu</RESOURCE></RESOURCES>
</HEADER></NUANCE>
```

`vautov5.dll` exports one entry point per component (`<name>_GetInterface`) **[verified]**:
`fe_lid, fe_udwl, fe_clcml, fe_promptoriorth, fe_unixlit, fe_initlingdb, fe_promptorth,
fe_word_seg, fe_ara_parser, tokentn, fe_abbrtn, fe_puncsptn, fe_oneword, fe_pos, fe_hmogrph,
fe_phrasing, fe_normout, fe_prompt, fe_global, fe_deinitlingdb, fe_dctlkp, fe_depes, uselect,
synth, synthstream, xcoder, xcoder_mrksync, phonmap, phonmapmrk, dcteg, domain_mngr, ttteg, ttseg,
inetspi, lespi, bespi, lookup, parser, stdpp, ules, ords, lingdb, compquery, audiofetch,
audioinserter, gsmc_vssq, gsmc_encrypt, log, logsub_logfile`, plus `CLM_GetInterface`,
`InitRsrcFunction`, L&H string helpers (`LH_wcs*`), `MD5*`, `base64_*`, `Utf8_*`, `XLIT_str*`.

Pipeline stage meanings (standard front-end/back-end split, **[verified by name/tag evidence]**):
`fe_lid` language ID → `fe_udwl` user dictionary → `tokentn` tokeniser + text normalisation →
`fe_abbrtn/fe_puncsptn/fe_oneword/fe_pos/fe_hmogrph` linguistic clean-up → `fe_phrasing`
prosodic phrase break → `phonmap` grapheme/lexicon→phoneme + markers → `uselect` unit selection
→ `synth` waveform generation (coder `155mrf22`) → `xcoder`/`xcoder_mrksync` codec + marker sync
→ `audioinserter` PCM assembly → `audio/L16;rate=22050`.

### 5.5 `ss_wind32.plh` — LHKP inventory **[verified]**
`LHKP LIST`, 4 items, 8 technologies, 7 component types, 26 languages:
`ENU American English, ARA Arabic, ARE Arabic (Egyptian), ENA Australian English, DUB Belgian
Dutch, FRB Belgian French, PTB Brazilian Portuguese, ENG British English, CAH Cantonese Chinese,
SPE Spanish, DAD Danish, DUN Dutch, FRF French, GED German, ITI Italian, JPJ Japanese,
KOK Korean, MNC Mandarin Chinese, SPM Mexican Spanish, NON Norwegian, PLP Polish, PTP Portuguese,
RUR Russian, ENS Singapore English, SWS Swedish` (+ one empty/“LNGID 0” entry).
Language dir names used on disk are the lowercase 3-letter codes: `enu eng ged frf iti jpj dun esp`.

---

## 6. Voice asset container (`.dat`) — **[verified, decoded]**

### 6.1 File wrapper
```
file := magic(4) = FB E0 EF 9D | u32le body_len | body
```
The 8-byte header is plaintext. Byte order little-endian throughout.

### 6.2 Body / chunk format
```
body   := banner(8 ASCII name) | u32le banner_size | banner_payload
          followed by a flat sequence of chunks at the same level
chunk  := tag(4) | u32le size | payload(size)
```
Rules discovered by inspecting `pp.dat`, `synth_*.dat`, `uselect_*.dat`:

* The **tag bytes are obfuscated with XOR 0xA9** (e.g. raw `e0 e7 ef e6` = `INFO`).
* A tag that is immediately followed by another valid tag is a **container label with no size
  field** (`INFO` introducing `ICOP/ICRD/VERS/...`). Its scope ends with the enclosing region.
* **`u32` size fields and numeric payloads are stored in the clear**; **string payloads are
  XOR 0xA9**; compressed/binary payloads are opaque (no XOR evident).
* Alignment padding is `0x00` (clear).
* The initial banner uses an 8-byte name whose last four characters are `LIST`
  (`PP\0\0LIST`, `HMEXLIST`, `USELLIST`, `VFNTLIST`, …).

Rule of thumb a rebuild can use: decode a payload as text if `XOR 0xA9` yields ≥85 % printable
bytes, otherwise treat as binary.

### 6.3 Chunk inventory and roles

`pp.dat` — global phone/“prompt” data (**root `PP\0\0LIST`**), 7.9 KB:
`INFO{ICOP,ICRD,VERS}`, `CFIG=1`, `BRKD` (break/pause definitions, contains L&H escape text),
`ESCD` (3.8 KB escape/control-code table), `EOSD` (3.9 KB end-of-sentence data; contains an
embedded `ERCP` marker).

`synth_<voice>_full_<model>.dat` — voice font (**root `VFNTLIST`**). Serena/155mrf22:
`INFO{…}`, `VINF` (139 B key/value), `CODR` (41,032 B codebook), `CTUN` (4 B: u32 = unit count),
`UNIT` (2,214,928 B unit index/geometry), `SPCH` (42,312,137 B coder-compressed speech units,
byte entropy ≈ 7.98 → compressed).

`uselect_<voice>_full.dat` — unit selection database (**root `USELLIST`**). Serena:
`INFO`, `VINF` (71 B), `VCAT` (328), `VBOP` (108, zero-filled), `PREF` (224), `TRAF` (52),
`FUZZ` (492), `CTXR` (232), `PCAR` (428), `DCAR` (7,940), `PHIS` (100), `CTUN` (4),
`CNT0` (520), `TAB0` (1,040), `SYMB` (2,491,794), `UNIT` (5,537,320), `CDBK` (49,152),
`TRI ` (8,491,800), `CCOS` (197,920).
Samantha has the same tag set with differing sizes. `PREF/TRAF/FUZZ/CTXR/PCAR/DCAR` begin with
4 obfuscated tag bytes + u32 size + u32 count patterns (nested structures) **[verified
structurally]**.

`VINF` is NUL-separated key/value pairs **[verified]**:
```
serena : Version 1.00 | Lng eng | Voice serena | Freq 22050 | Reduction full |
         Coder 155mrf22 | BitRate 270 | OverheadFrames 6 | AvgSpeed 149 | ReductionVersion 1.00
uselect: Version 1.00 | Lng eng | Voice serena | Reduction full | ReductionVersion 1.00
```

`CTUN` = `u32` unit count (value `0x00043982` = 276,354 in both Serena files) **[verified]**.
`UNIT`/`SPCH`/`SYMB`/`TRI`/`CCOS`/`CDBK`/`CODR` internal geometry is **not decoded** — this is
the boundary of the DNN/concatenative model. A rebuild either ports the coder (`155mrf22`,
see §8) or substitutes its own synthesiser; the container, metadata and interfaces are fully
specified so either choice is a drop-in.

`clc_<lang>_cfg3.dat` / `clc_<lang>_<voice>_cfg3.dat` — front-end configuration and compiled
lexicon. **Different container** (starts `E7 32 E6 1F 00 00 01 00 …`, no `FB E0 EF 9D`), holds a
tagged binary with a nested directory; readable embedded strings include `NVAR`, `pipeline`,
`depes_enu_samantha`, section names. Role: front-end dictionaries/lexicon for `fe_*`/`phonmap`;
a rebuild may replace it with its own lexicon.

---

## 7. SAPI4 module (`rsstdv2.dll`)

### 7.1 Registration **[verified]** — written by `DllRegisterServer` (embedded registration script)
```
HKEY_CLASSES_ROOT\CLSID\{A162A680-7689-4B2E-8A9B-6806F141EFC5} = "ScanSoft RealSpeak v2 Standard"
    \Programmable
    \InprocServer32 = <path to rsstdv2.dll>, ThreadingModel = "Apartment"
HKEY_LOCAL_MACHINE\Software\Voice\TextToSpeech\Engine
    "ScanSoft RealSpeak v2 Standard" = "{A162A680-7689-4B2E-8A9B-6806F141EFC5}"
```
The product self-registers it at runtime (`natspeak.exe` loads each DLL and calls
`DllRegisterServer`, falling back to spawning `regsvr32.exe` when the bitness differs).

### 7.2 Interfaces and IIDs used by the client **[verified by disassembly of natspeak.exe]**
| IID | Role |
|---|---|
| `{6B837B20-4A47-101B-931A-00AA0047BA4F}` | mode enumerator (methods at slots 3=`Next`, 7=`Select`) |
| `{1287A280-4A47-101B-931A-00AA0047BA4F}` | central TTS object (slots 0=`QI`, `Register`≈+0x30, `TextData`≈+0x1c) |
| `{C0FA8F40-4A46-101B-931A-00AA0047BA4F}` | notify sink registered via `Register` |
| `{1D59DED1-E367-11D1-BED7-006008317CE8}` | attributes (pitch/speed/volume, `StringSet` id `0x101b`) |
| `{E4963D40-C743-11CD-80E5-00AA003E4B50}` | text-format GUID passed to `TextData` |

Client protocol (what a compatible server must implement) **[verified]**:
1. `CoCreateInstance(CLSID {A162A680-…}, IID {6B837B20-…}, CLSCTX_INPROC_SERVER)`.
2. `Next(1, &entry, &fetched)`; entry = `{GUID modeId; wchar_t name[262];}`;
   `S_FALSE` ⇒ no modes.
3. Compare `entry.name` with the expected mode name (see §7.4); `Select(modeId, &obj, 0)`.
4. `obj->Register(sink, IID {C0FA8F40-…}, …, &key)`; sink receives
   `SpeakingStarted`/`SpeakingDone`.
5. QueryInterface `ITTSCentral` `{1287A280-…}` and `IAttributes` `{1D59DED1-…}`;
   for engines whose vendor is `ScanSoft, Inc.`/`Dragon NaturallySpeaking` call
   `IAttributes::StringSet(0x101b, "MM/DD/YYYY")`.
6. `TextData(flags=0, attrs=0, text, byteLen, ctx, formatGUID)`; `UnRegister(key)` on teardown.
7. Volume safety: `waveOutGetVolume` before, clamp to system max while speaking, restore after.

### 7.3 Modes and names **[verified strings/GUIDs]**
Engine CLSID above; mode label string in the module: `RealSpeak(SAPI4) Text to Speech Mode`;
notify class `RealSpeak_SAPI4_NotifyClass`. Classes: `CTTSInstBase`, `CTTSInstRealSpeak`
(legacy path), `CTTSInstVauto5` (pipeline path, calls `vauto_ttsProcessText2Speech` in
`vautov5.dll`), `CEngineEnum`, `RSWrapper`. Module version 16.00.200.121.
Default mode names per language: `American English Jennifer`, `British English Jane`,
`French Sophie`, `Italian Bianca`, `German Vera`, `Spanish Isabel`, `Japanese Tomoko`,
`Dutch Laura`; UK default for the legacy variant: `RealSpeak (Jane) UK English (v2) Female #2`
(engine-list label `L&H Speech RealSpeak`).

### 7.4 Client-side mode selection keys **[verified behaviour, names unresolved]**
Preferences are read through the product's settings layer under symbolic names
`TTSModeName`, `UKTTSModeName`, `useRealSpeakV2SAPI4`, `TTSPitch`, `TTSSpeed`, `TTSVolume`,
`TTSAllowAllLanguages` (symbols present in the binary; the literal storage names are string-table
IDs). A rebuild can pick its own names as long as defaults reproduce the observed behaviour:
* `useRealSpeakV2SAPI4 == 0` **and** configured mode name equals the language default ⇒ use the
  **legacy `lhs_tts*` path** with voice `"RealSpeak Standard v2"`.
* otherwise use the **SAPI4 path**, selecting by name, falling back to the last US-English
  (`LCID 0x809`, dialect 1) mode, else failing with `0x80040206`.

---

## 8. Audio, voices, data you must supply or substitute

| item | value |
|---|---|
| Sample format | `audio/L16;rate=22050`, 16-bit, mono, little-endian |
| Voices shipped | `Samantha` (ENU, US English), `Serena` (ENG, UK English) |
| Voice models | `full_155mrf22` (coder `155mrf22`, bitrate 270, overheadframes 6) and `full_vssq5f22` |
| Coder | `155mrf22` (proprietary; decoder = `rsstdv2_dec11.dll`, entry `gsmc_GetInterface`, broker `decoder/83swf11`) |
| Codebook | `CODR` chunk (41 KB) in the voice font, plus `CDBK` (48 KB) in the uselect DB |
| Front-end config | `clc_<lang>_cfg3.dat` (fecfg `cfg3`, fedatapackaging `clc`) |

To be **faithful** you must port the `155mrf22` coder (decode `SPCH` frames using `CODR`, driven
by `UNIT` geometry and `CTUN` count) — that is the one genuinely hard part and is **not** solved
here. The seam is explicit: implement `xcoder`/`synth` components of §5.4 such that
`uselect → synth → xcoder → audioinserter` yields PCM; the container reader in
`dragon_tts/datfile.py` already exposes every chunk.

To be **functionally equivalent** (recommended first step, and what the reference implementation
does) provide your own `synth` implementation behind the same component names, driven by `VINF`
(frequency/coder/bitrate) and the manifest parameters, and keep everything else identical.

---

## 9. Error / return-code conventions

* `lhs_*`: 0 = success; negative `0x8xxxxxxx` = error object encoding
  `(module<<16)|(submodule<<8)|code`; API maps to small codes (1/3/4/5/6/8/0xb/0xc/0xd/0xe/0x17).
* Core/broker: `HRESULT`-style — `int < 0` is failure; `0x80000000` threshold is used throughout
  the decompiled call sites.
* Product-level TTS failure surfaced to the user: `0x80040206`
  (“Couldn’t find expected TTS mode”/“could not initialize TTS”).

---

## 10. Configuration surface

Environment (broker/trace) **[verified names]**: `LH_MODULES`, `LH_SUBMODULES`, `LH_INPATH`,
`LH_OUTPATH`, `LH_TRACELEVEL`, `LH_TRACEINFO`, `LH_LOGFILE`.

Registry (installer-written, relevant to TTS) **[verified]**: SAPI4 engine key (§7.1),
`SOFTWARE\Voice\SpeechRecognition\Engine` (SR engine), `SOFTWARE\ScanSoft\NaturallySpeaking16\*`,
`Software\Nuance\NaturallySpeaking16\*`; the product also stores `TTS Enabled` and playback
keys in `nssystem.ini`/`nsdefaults.ini` under `[Settings]` (e.g. `enx TTS Enabled=1`).

Files/dirs the subsystem expects (installed tree):
```
Program\                          natspeak.exe, rsstdv2.dll, vautov5.dll, rsstdv2_core*, lhsp01.dll, lhcom02W.dll…
Program\lhsp\                     lhtts01.dll, lhdct01w.dll, ss_wind32.plh
Program\lhsp\rsstdv2\common\      rsstdv2_core.dll/.hdr, rsstdv2_dec11.dll/.hdr
Program\lhsp\rsstdv2\<lang>\      per-language descriptors (empty in this SKU)
Program\voices\speech\components\ pp.dat, synth_med_fxd_bet2f22.dat
Program\Voices\speech\components\ clc_*.dat, synth_*.dat, uselect_*.dat
Program\Voices\speech\vautov5\    vauto_pipeline_*.hdr
```

---

## 11. Acceptance tests for a rebuild

1. `lhs_InitializeEx/UnInitialize`, `lhs_ttsOpenW(h, L"RealSpeak Standard v2", &i, 0)` succeeds and
   returns a handle; invalid handles return `LHS_E_INVALIDHANDLE`.
2. `lhs_ttsSetTextW` + `lhs_ttsSpeak` produce non-silent PCM at 22050 Hz/16-bit/mono; `Stop`
   within one utterance stops output within a bounded time; `Pause`/`Resume` behave.
3. `lhs_ttsGenOutputFileW` writes a valid RIFF/WAVE whose `fmt ` says 22050/16/1 and whose
   `data` chunk is non-silent and duration ∝ text length.
4. `lhs_ttsGetVoiceListW` enumerates the installed voices read from `VINF` (Samantha/Serena).
5. Parameter `0x400` switches language/voice; `0x2000`/`0x202` is accepted.
6. Broker: given a tree containing the shipped `.hdr` files, `TTSENGINE` resolves to
   `eng/rsstdv2`, `ttscore` resolves to `rsstdv2_core.dll`+`CoreGetInterface`, and
   `decoder/83swf11` to `rsstdv2_dec11.dll`+`gsmc_GetInterface`; `vauto_pipeline_*.hdr`
   manifests are parsed and their `COMPONENTS` list instantiates.
7. `.dat` reader: `pp.dat`, `synth_*.dat`, `uselect_*.dat` parse with the tag inventories of §6.3
   and `VINF` decodes to the exact key/value sets quoted there.
8. SAPI4: creating the engine CLSID yields a mode enumerator; `Next` returns the configured mode
   name; `Select` returns an object accepting `Register`/`TextData`/`UnRegister`; the registry
   script matches §7.1 byte-for-byte in its keys/values.
9. Legacy-vs-SAPI4 selection: with `useRealSpeakV2SAPI4=0` and default mode name the legacy path is
   used; with a custom mode name the SAPI4 path is used (observable via which init function runs).

Reference implementation status: 1–7 and 9 are exercised by `dragon_tts` + `tests/test_tts.py`
(using a substitute synthesiser for the `synth` component); 8 is provided as the protocol model +
registration data in `dragon_tts/sapi4.py` (COM binary itself needs Windows).

---

## 12. Open items carried forward

1. Coder `155mrf22` decode (SPCH ↔ CODR ↔ UNIT geometry) — the only missing piece for
   sample-faithful synthesis.
2. `clc_*_cfg3.dat` container decode (front-end lexicon/config).
3. Literal setting names for `g_wRGY_*` (string-table IDs; §7.4 uses the symbolic names).
4. The 139-byte obfuscated key blob passed to `lhs_InitializeEx` (purpose undetermined).
5. Broker default search root when no `LH_*` env vars are set (caller-supplied base dir).

---

## 13. Reference implementation — verified state

`dragon_tts/` implements the interfaces above and is exercised by `tests/test_tts.py`
(21 tests, all passing) plus the CLI.  Verified outputs:

```
python3 -m dragon_tts.say --info --bank voicefiles
  bank: voicefiles
    voice=serena   lang=eng freq=22050 coder=155mrf22 bitrate=270 model=full_155mrf22
    voice=samantha lang=enu freq=22050 coder=155mrf22 bitrate=270 model=full_155mrf22
    synth_serena_full_155mrf22.dat: INFO, ICOP:64, ICRD:11, VERS:6, VINF:139, CODR:41032,
                                    CTUN:4, UNIT:2214928, SPCH:42312137
    uselect_serena_full.dat: ... SYMB:2491794, UNIT:5537320, CDBK:49152, TRI :8491800, CCOS:197920
  broker: 3 descriptors; brokerstrings=['decoder/83swf11',
           'pipeline/American English/samantha/22/full_155mrf22/text/pcm', 'ttscore']
  TTSENGINE -> 'eng/rsstdv2'
```

```
python3 -m dragon_tts.say "Hello. This is the Dragon sixteen text to speech reference
                           implementation speaking." out_enu.wav --bank voicefiles --voice samantha
python3 -m dragon_tts.say "British English voice, testing the reconstructed pipeline." \
                           out_eng.wav --bank voicefiles --voice serena
python3 -m dragon_tts.say "hello world" out_sapi.wav --bank voicefiles --sapi4
```

| file | format | duration | peak | rms | zcr |
|---|---|---|---|---|---|
| `out_enu.wav` | PCM mono 22050 Hz 16-bit | 8.05 s | 26213 | 1279 | 364 /s |
| `out_eng.wav` | PCM mono 22050 Hz 16-bit | 5.50 s | 26213 | 1259 | 370 /s |
| `out_sapi.wav` | PCM mono 22050 Hz 16-bit | 1.01 s | 26213 | 2556 | 394 /s |

Duration scales with text length; the zero-crossing rate is in the speech range.  The pipeline
instantiates **29 components** (9 implemented + 20 pass-through) from the shipped manifest, i.e.
the manifest is the contract that drives construction.

Component ↔ file map:

| interface / behaviour | implementation |
|---|---|
| asset container reader | `dragon_tts/datfile.py` |
| `.hdr` schemas A/B | `dragon_tts/hdr.py` |
| broker discovery + broker strings | `dragon_tts/broker.py` |
| voice metadata + chunk inventory | `dragon_tts/voicebank.py` |
| front-end (`fe_*`, `tokentn`, `phonmap`) | `dragon_tts/phonemes.py` |
| `synth` / `xcoder` / `audioinserter` seam | `dragon_tts/synth.py` |
| manifest-driven component graph | `dragon_tts/pipeline.py` |
| 35-export `lhs_tts*` API | `dragon_tts/lhs_tts.py` |
| SAPI4 module model + `.reg` script | `dragon_tts/sapi4.py` |
| C ABI (exports, params, `ttscore` vtable) | `include/lhs_tts.h` |

What a follow-up must still do for **sample-faithful** audio: port the `155mrf22` coder (decode
`SPCH` with `CODR`/`CDBK` under `UNIT` geometry) into `synth.CoderSynthesiser`, and decode
`clc_*_cfg3.dat` if the original lexicon is wanted.  Everything else — formats, interfaces,
discovery, registration, mode selection, prosody plumbing — is specified and running.

---

## 14. Voice/front-end tables decoded (TTS deep-dive)

### 14.1 Which chunk payloads are obfuscated **[verified]**

Measured over every chunk of `pp.dat`, `synth_med_fxd_bet2f22.dat`,
`synth_serena_full_155mrf22.dat`, `uselect_serena_full.dat`:

| chunks | encoding | evidence |
|---|---|---|
| `ICOP`, `ICRD`, `VERS`, `VINF`, `EXHD`, `BRKD`, `VCAT` | **XOR 0xA9 over the whole payload** | printable-after-XOR ≈ 1.00 / 0.69 / 0.86, printable-raw ≈ 0.00–0.39 |
| `ESCD`, `EOSD` (in `pp.dat`) | mixed: plaintext names + numeric tables | raw-printable 0.76 / 0.79 |
| `CODR`, `UNIT`, `SPCH`, `SYMB`, `TRI `, `CDBK`, `CCOS`, `PCDR`, `DCAR`, `PCAR` | **plaintext binary** | XOR does not improve readability (0.08–0.39) |

Container rules therefore are: 8-byte file header clear → banner name XOR 0xA9 → chunk tags XOR
0xA9 → chunk sizes **clear** (u32 LE) → payload XOR 0xA9 only for text-bearing chunks.

### 14.2 Front-end escape/command schema (`pp.dat` `ESCD`/`EOSD`) **[verified]**

`ESCD` (3,824 B) and `EOSD` (3,896 B) declare the text-escape grammar the front end implements.
Readable names: `basicesc`, `basicescwitharg`, `enhancedescseq`, `esctoi`, `esccomment`,
`typeofdocVDN`, `typeofinputVDN`, `posVDN`, `esc` (plus embedded `ERCP` markers, `MP`/`MQ` tags).
These are the rule families behind the `\Spd=` / `\Pit=` / `\Vol=` / `\Pau=` escapes listed in
§3.2 — i.e. the normalisation/escape layer a rebuild must reproduce.
`BRKD` (33 B) holds break/pause definitions (its text tag name is `TNULLBROKERSTRING`).

### 14.3 Voice property catalogue and index tables **[verified]**

* `VCAT` (uselect) — sequence of `TAG(4, XOR 0xA9)` + `u32 length (clear)` + vector, tags seen:
  `ALPH`, `VOIC`, `COVO`, `SAFE`, … (alphabet/voice/coding/safety property classes).
* `PHIS` (100 B), `CNT0` (520 B), `TAB0` (1,040 B) — plaintext numeric index/priority tables
  (`TAB0` begins `0, 1, 2, 0x2c86, …`).
* `PREF`/`TRAF`/`FUZZ`/`CTXR`/`PCAR`/`DCAR` — numeric tables (< 10 KB each).

### 14.4 Model geometry — exact per-unit record sizes **[verified]**

`CTUN` (4 B) is the unit count; the per-unit tables divide it exactly:

| asset | chunk | bytes/unit |
|---|---|---|
| `synth_serena_full_155mrf22.dat` | `CTUN` = 276,866 units | — |
| | `UNIT` | **8.000** (2,214,928 B) |
| | `SPCH` | 152.825 (42,312,137 B, variable-rate) |
| | `CODR` | codebook, 41,032 B (not per-unit) |
| `uselect_serena_full.dat` | `UNIT` | **20.000** (5,537,320 B) |
| | `SYMB` | **9.000** (2,491,794 B) |
| | `TRI ` | 30.671 (8,491,800 B) |
| | `CDBK` | codebook, 49,152 B |

So unit selection can be indexed directly: one 20-byte record per unit in the uselect database and
one 8-byte record per unit in the synthesis database, with `SPCH` holding the variable-rate coded
speech for the units.  `EXHD` in the base voice (`pls65_dsp179 noi65 sr22 bnd_34 `) names the
engine/coder configuration: `sr22` = 22.05 kHz, `bet2f22` = the "bet2 f22" prototype coder.

### 14.5 Reader API

`dragon_tts/pmtables.py` exposes this layer:

```python
from dragon_tts import pmtables
t = pmtables.VoiceTables("voicefiles/uselect_serena_full.dat")
t.chunks                    # [(tag, size, 'text'|'binary'|'schema')]
t.strings["VINF"]           # decoded VINF fields
t.properties                # [('ALPH', 51), ('VOIC', …), …]
t.geometry.describe()       # "units=276866 (SYMB=9.000B, TRI=30.671B, UNIT=20.000B)"
pmtables.decode_escape_schema("voicefiles/pp.dat")   # ['basicesc', 'enhancedescseq', …]
```

Coder status unchanged: `SPCH` is coded at ~153 B/unit (codebooks `CODR`/`CDBK`); porting the
`155mrf22` (`bet2f22`) decoder remains the only step between the shipped data and sample-faithful
synthesis — the container, geometry, tables and interfaces above are now fully specified.

### 14.6 Coder frame geometry — derived **[verified arithmetic]**

`SPCH` is a constant-rate stream: `bytes_per_unit = SPCH / CTUN` = **152.825 B** (Serena,
`full_155mrf22`), and VINF gives `BitRate = 270` kb/s at `Freq = 22050`.  Therefore

```
frames_per_second = bitrate/8 / bytes_per_frame = 270000/8 / 152.825 = 220.84
frame_period      = 1/220.84 s = 4.53 ms      (~100 samples at 22050 Hz)
bank_duration     = CTUN / frames_per_second = 276866 / 220.84 = 1253.7 s  (~20.9 min)
```

So the voice bank holds 276,866 coded frames of ~4.53 ms each; `synth UNIT` stores exactly 8.000 B
per frame of side information and `uselect UNIT` 20.000 B per frame.  `pmtables.coder_geometry()`
returns these numbers.  What remains for bit-exact synthesis is the frame decoder itself
(`SPCH` frame -> PCM) using the `CODR`/`CDBK` codebooks, i.e. the `155mrf22`/`bet2f22` decoder in
`rsstdv2_dec11.dll` (`gsmc_GetInterface`) and the `vautov5` `xcoder` components.
