# Dragon 16 (`Dragon16.zip`) — Reverse-Engineering Report

**Working directory:** `/home/asdf/Downloads/dragon16-re`
**Artifact:** `/home/asdf/Downloads/Dragon16.zip` (4,503,791,988 bytes, 43 files / 10 dirs)
**Subject:** *Dragon NaturallySpeaking 16 (Professional Group, INTL build)* — a speech-recognition
product whose "Text-to-Speech Files" feature provides speech read-back. Product name `Dragon`,
publisher `Nuance Communications Inc.`, version `16.00.200.121`.
**Purpose of this document:** give an independent implementer enough structural, interface-level
and behavioural detail to rebuild (a) the installer/package and (b) the TTS subsystem, without
re-doing the archaeology.

Tooling produced: `parse_msi.py` (MSI reader), `ghidra_scripts/*.java` (headless decompilers),
`msi_tables/*.csv` (all 68 MSI tables decoded), `decomp/*.c` (decompiled functions), plus extracted
cabinets/binaries under `extract/`, `bins/`, `voice/`, `datapack/`.

---

## 1. Package layout (media root `DPG_DLG_16.0_NUMC/`)

| File | Size | Role |
|---|---|---|
| `setup.exe` | 1,412,016 | InstallShield (Flexera) bootstrapper; `PE32 GUI`, reads `setup.ini` |
| `setup.ini` | 6,012 | Bootstrapper script (`ScriptDriven=2`, `ScriptVer=26.0.0.638`) |
| `setup.cab` | 210 | Bootstrapper payload |
| `Dragon 16.msi` | 36,495,360 | Main InstallShield MSI (all components/registry/INI) |
| `0x0407/0409/040a/040c/0410/0413.ini` | ~24 KB each | Localised bootstrapper UI strings (DE, EN, ES, FR, IT, NL) |
| `admininstall.bat` | 1,123 | Administrative (uncompressed) install driver |
| `Autorun.inf` | 27 | `OPEN=setup.exe` |
| `setup.bmp` | 351,576 | Splash |
| `AUS/ENC/ENG/ENU/IND/SEA.cab` | 229/801/460/1,390/202/257 MB | ASR language/Datapack payloads |
| `NatSpeak.cab` | 195,986,012 | **Program files** (5,848 entries; 241 EXE/DLL) |
| `TTS.cab` | 188,995 | TTS core (9 files: L&H engine + SAPI4 engine + base voice) |
| `TTSENG.cab` | 219,226,646 | UK-English voice (Serena) |
| `TTSENU.cab` | 512,041,018 | US-English voice (Samantha) |
| `Tutorial.cab` | 27,747,996 | Interactive tutorial |
| `Tools/InstallationDiagnosticUtility.exe` | 73,112 | Diagnostics |
| `ISSetupPrerequisites/{...}/` | 161 MB | WebView2 runtime x86, VC++2019 redist x86/x64 |
| `Documentation/`, `Dragon 16.pdf` | tiny | Stubs / Windows Installer package definition INI |

Notes
- The ZIP has a malformed central-directory offset: Info-ZIP `unzip` fails with
  "208820013 extra bytes"; **Python `zipfile` and 7-Zip 23.01 read it fine** (Zip64).
- Deployment is DVD/ISO style (`VolumeLabel=DNS16_DVD1`), so the outer ZIP is just a wrapper.

### 1.1 Bootstrapper behaviour (`setup.ini` + `admininstall.bat`)

- MSI command line (from `[Startup] CmdLine`):
  `/Liampurecow+ "%TEMP%\dgnsetup.log" PRODUCTUPDATEFLAG=-1 WEBREGISTRATION=0 VLAFLAG=0 /qb+`
  (`/L…+` = msiexec logging flags; `…/qb+` = basic UI with modal progress).
- `PackageName=Dragon 16.msi`, `LauncherName=setup.exe`, `WaitInstallation=Y`, `UI=1000`,
  `OnUpgrade=2`, `DoMaintenance=N`, `EnableLangDlg=N`.
- Silent/admin: `admininstall.bat <dir>` → `setup.exe /a /s /v"/qb TARGETDIR=<dir> /Liampurecow+ <log>"`.
- Prerequisites chained before the MSI: `MicrosoftEdgeWebView2.prq`,
  `Microsoft Visual C++ 2019 Redistributable (x86).prq`, `(x64).prq`;
  `DotNetOptionalInstallIfSilent=N`.
- Supported OS table is legacy (Win95…WinXP/2003) plus `[WINSERVER2019] BuildMin=17763`,
  `LaunchCondition` block enforces x86/x64 OS floors and .NET 4.5.2.
- `Dragon 16.msi` is cipher-text signed by `Dragon 16.isc` (bootstrapper cert key `CertKey`).

---

## 2. MSI database

Identity (from `Property`): `ProductName=Dragon`, `ProductVersion=16.00.200`,
`ProductCode={332B63C5-EFDD-4BFD-AB35-63E0C84FF2A3}`,
`UpgradeCode={DDDD0D1D-2E1D-4F0C-AE80-77FF29AB7535}`,
PackageCode `{DFAEACE6-464D-496A-850F-AAD70CC8B81F}`, `Manufacturer=Nuance Communications Inc.`,
`ALLUSERS=1`, `CURRENT_EDITION=ProfessionalGroup`, `SUPPORTED_LANGUAGES=enx`,
`SERIALNUMBER=A209A-K13-JTRD-W2XV-AR`, `ACTIVATION_VERSION=16.0`, `ProductID=none`.
Upgrade rows handle DNS 12–15.x (and forward-version blocks) via `Upgrade` table and
`ISFOUNDNEWERPRODUCTVERSION/UPGRADEFOUND/MAJORUPGRADEFOUND/NEWPRODUCTFOUND` properties.

### 2.1 Table-stream encoding (verified empirically — needed to re-read the MSI)

All 68 tables (`msi_tables/*.csv`) were decoded with these two rules:

1. **Column-major storage.** A table stream contains *all rows of column 1*, then *all rows of
   column 2*, … (not interleaved rows). Verified on `Property` (215 rows: first 215 u16 = property
   names in PK order, next 215 = values), `CheckBox`, `Media`.
2. **Integer encoding.** 2-byte integer columns encode non-null as `value | 0x8000`; 4-byte integer
   columns use `value | 0x80000000`; subtract the flag when the high bit is set. String columns are
   2-byte indexes into `_StringPool`/`_StringData` (0 = NULL). Object/binary columns store the
   stream-name string id.
   `_Columns` itself is 4×2-byte columns (`Table`, `Number`, `Name`, `Type`) where `Type & 0x0800`
   marks strings and `Type & 0xff` is the byte width (string width = max length, default 0).

Cross-validation: `Media.LastSequence` values 18/84/117/196/214/6062/6080/6089/6096/6103/7087
match the per-cabinet file counts exactly; `DiskId` decodes to 1…11.

### 2.2 Media ↔ cabinet ↔ feature map

`Media` table (11 cabinets, all `VolumeLabel=DNS16_DVD1`):

| DiskId | LastSequence | Cabinet | Content |
|---|---|---|---|
| 1 | 18 | AUS.cab | Australian English ASR model |
| 2 | 84 | ENC.cab | Canadian English (+aus/esp/ff/ind/kid/south/uk sub-models) |
| 3 | 117 | ENG.cab | UK English ASR model |
| 4 | 196 | ENU.cab | US English ASR model |
| 5 | 214 | IND.cab | Indian English model |
| 6 | 6062 | NatSpeak.cab | program tree (5,848 files) |
| 7 | 6080 | SEA.cab | SE-Asian English model |
| 8 | 6089 | TTS.cab | TTS core binaries + base voice |
| 9 | 6096 | TTSENG.cab | Serena (UK) voice |
| 10 | 6103 | TTSENU.cab | Samantha (US) voice |
| 11 | 7087 | Tutorial.cab | itutorial assets |

Features (27, `Feature.csv`): root `DNS` → `Speech` (install dir `COMMONAPPDATA_NATSPEAK`),
`NatSpeak` (program files, `INSTALLDIR`), `Tutorial` (`ITUTORIALDIR`), and TTS chain:
`TTS` (dir `PROGRAMDIR`) → `TTSENX` (`PROG_VOICES`) → `TTSENG`/`TTSENU` (`ENU_TTS_DIR`),
plus hidden per-language TTS features (`TTSDEU/TTSESP/TTSFRA/TTSITA/TTSJPN/TTSNLD`,
Display `0x8000` = hidden). Language speech features: `ENX`→`AUS/ENC/ENG/ENU/IND/SEA`,
`DEU`, `ESP`, `FRA`, `ITA`, `JPN`, `NLD` (all under `COMMON_NS_DATAPACK`).
`ControlEvent` shows TTS is enabled by region: `ChooseRegion.Next` → `AddLocal TTS,1`;
`TTSENG` for regions 3–7; `TTSENU` for regions 1,2,7.

### 2.3 Install tree (resolved `Directory` table)

```
<ProgramFiles>\Nuance\NaturallySpeaking16\
  Program\                     natspeak.exe + ~240 DLLs/EXEs, dd10*.dll, dnstk10.dll, …
  Program\lhsp\                lhtts01.dll, lhdct01w.dll, ss_wind32.plh
  Program\lhsp\rsstdv2\        rsstdv2.dll (SAPI4 module)
  Program\lhsp\rsstdv2\common\ rsstdv2_core.dll/.hdr, rsstdv2_dec11.dll/.hdr
  Program\lhsp\rsstdv2\{enu,eng,ged,frf,iti,jpj,dun,esp}\   (created, empty in this SKU)
  Program\voices\speech\components\  pp.dat, synth_med_fxd_bet2f22.dat
  Program\Voices\speech\components\  clc_*.dat, synth_*.dat, uselect_*.dat
  Program\Voices\speech\vautov5\     vauto_pipeline_*.hdr
  Help\, itutorial\, SDK\, SupportTools\, x64\, upgrdmod28\
<CommonAppData>\Nuance\NaturallySpeaking16\  Data\, Datapack\, Users\, nssystem.ini, nsdefaults.ini
<ProgramFiles>\Common Files\Nuance\           dgnsvc.exe (service host)
<CommonAppData>\FLEXnet\Connect\              licensing
```

### 2.4 Installer flow (custom actions, `InstallExecuteSequence`, 193 rows / 140 actions)

Representative chain (InstallShield `ISSetup.dll` actions are `f1…f101`):

1. `ISSetupFilesExtract`, `CancelButton_Disable`, `CollectSystemData`,
   `DLLWrapStartup`, `ISSetAllUsers`, `FindRelatedProducts` (200).
2. Version/upgrade gates: `PreventOldPackageInstallingOverNew`, `PreventSecondWEBInstallation`,
   `PreventDowngrades*`, `ISPreventDowngrade`, `MigrateFeatureStates`.
3. Licensing: `CheckNatSpeakInMemory`, `CheckInstallDir`, `SetActivationSerialNumber`
   (`ACTIVATION_SERIALNUMBER`→`SERIALNUMBER`), `CheckSerialNumber` (`f4`) and error
   `SerialNumberIsNotCorrect` ("The Serial Number [ACTIVATION_SERIALNUMBER] is not valid…"),
   `SetPIDKEY` (650), `SetDNSHistorySerialNumber`, `StopFLEXnet` (1300).
4. Service: `StopDragonService`/`DeactivateDragonService`, later
   `CreateAdditionalFolders`, `RegFormfiller /regserver`, `ActivateDragonService`,
   `SetPeriodicTasks`, `RegNetBinaries`, `CreateRegEntries`(+`_x64`), `RefreshStartMenu`.
5. Model/INI generation: `GenerateModelsIni`, `RegenerateModelsIni`, `GenerateNatspeakIni`
   — all run **`natspeak.exe -rebuildmodels`**; `MergeIniEncrypted` (f36),
   `RestoreSavedNSDefaultsIniSettings`, `SetDefaultsIniFile`, `WriteDefaultLangAndDialect`.
6. TTS: `SetTTSFeature` (f75); prerequisites gated by `ALLOW_TTS`, `ALLOW_TTS_ENG`, `ALLOW_TTS_ENU`.
7. Cleanup/rollback: `RemoveExtraFiles`, `DefragSVCFiles`, `ISSetupFilesCleanup`, log copy
   (`BackUpSetupLog`).

Configuration written by the installer: 2,572 `Registry` rows and 149 `IniFile` rows
(`nssystem.ini`, `nsdefaults.ini`, COM/CLSID/Interface registration for the `dd10xxxx` family,
`SOFTWARE\ScanSoft\NaturallySpeaking16\Activation` (`ProdCode`, `ProductName`, `Version`,
`SerialNumber`), `SOFTWARE\ScanSoft\Shared\Products\<serial>\<ver>` (`CPID`, `SerialNumber`),
`Software\Nuance\DNSHistory\<timestamp>` (`Edition`, `Version`, `SerialNumber`),
`SOFTWARE\Voice\SpeechRecognition\Engine\Dragon NaturallySpeaking = {dd100001-…}`,
`SOFTWARE\Dragon Systems\Dragon log` (logger service settings),
`DisableCicero` AppCompat shim for `natspeak.exe`, native-messaging hosts for FF/Chrome/Edge.
TTS-relevant INI: `nssystem.ini [Settings] enx TTS Enabled=1`; `nsdefaults.ini` playback hot keys.
Installed service: **DragonLoggerService** (`loggerservice.exe`), auto-start, depends on
`LanmanServer`+`eventlog`.
Shortcuts (`Shortcut.csv`): Dragon (desktop+start menu), Dragon Tools folder entries for
Upgrade User Profiles, Gather files for support, Show Dragon Log / Setup Log,
MyCommands Protection Utility, MyCommands Samples, AutoTranscribe Folder Agent,
Acoustic & Language Model Optimizer Scheduler, Convert XML To DAT, Dragon service (dgnsvc), NSAdmin.
Executables shipped (34): `natspeak.exe` (6.25 MB, main UI), `dgnee.exe` (15.8 MB),
`dragonbar.exe`, `datacollector.exe`, `audio.exe`, `assistant.exe`, `dragon_support_packager.exe`,
`dgnsvc.exe`, `loggerservice.exe`, `itutorial.exe`, `nsbrowse.exe`, `nsadmin(w).exe`, `tagent.exe`,
`schedmgr.exe`, `protectcmds.exe`, `mergemycmds.exe`, `mycmdsxml2dat.exe`, `formfiller.exe`,
`natlink.exe`, `savewave.exe`, `modmerge.exe`, `upgrade.exe`, `pspdispatchers.exe`,
`dgnuiasvr(_x64).exe`, `dgnria_nmhost{,_FF,_MSEdge}.exe`, `ToggleFullTextControlWPF.exe`,
`twovermsg.exe`, `InstallationDiagnosticUtility.exe`.

---

## 3. Runtime architecture

`natspeak.exe` (MFC/ATL C++, x86) is the ASR host and the TTS client. It exports ~38 C++ symbols
and carries PDB-derived source names (`voicebar\voicebar.cpp`, `voicebar\ttssapi.cpp`,
`voicebar\opttts.cpp`, `voicebar\transcrb.cpp`, `audio\mixer.cpp`) that are useful road signs.

COM surface (installed by MSI): Dragon's own out-of-proc/dual interfaces are marshalled by
`dd10midl.dll` / `dd10sapi.dll` (the latter exported as `Dragon NaturallySpeaking SAPI MIDL DLL`,
a NDR proxy/stub exporting only `DllCanUnloadNow/GetClassObject/Register/UnregisterServer`).
Registered CLSIDs include `{dd100001-6205-11cf-ae61-0000e8a28647}` (*Voicebar SR Enumerator*),
`{dd100002-…}` (*Speech Services*), `{dd100003-…}` (*VoiceDictate*),
`{607fc1b2-70ae-11d1-a49f-00a024a3a678}` (*Dragon VoiceCommands Control*), and the
`IVoiceDictation00..12{A,W}` + `IDgnVoiceDictateA`/`IDgnVoiceCapture` interface proxies.
.NET: `nasservices.dll` + `Nuance.NAS.Objects*.dll` + `System.Data.SQLite` (registered classes
`nasservices.SpeechAutoTextWrapper {2906ED08-5CD0-42F5-8FA4-716F45A04837}`, `nmsSvcmgr`, …).
COM self-registration at runtime: `natspeak.exe` (`voicebar.cpp`) enumerates DLLs, and for each
whose version resource `ProductName` matches it either `LoadLibrary`+`DllRegisterServer` or spawns
`System32\regsvr32.exe /s /c "<dll>"` (used when 32/64-bit mismatch is detected).
`voicebar.cpp` also registers `dgnuiax.dll` / `dgnuiax_x64.dll` and scans the directory named by
`nssystem.ini [Settings] Code x64 Directory` for `*.dll`.

---

## 4. Text-to-speech subsystem (primary target)

Dragon ships **two TTS stacks**, selected at runtime, plus an SAPI4 registration so external
SAPI4 applications can use the engine.

```
                        ┌────────────────────────── natspeak.exe (voicebar) ─────────────────────────┐
                        │  FUN_00537e20  TTS initialize   (opttts.cpp)                             │
                        │  FUN_00534b10  SAPI4 engine/mode select + ITTSCentral  (ttssapi.cpp)     │
                        │  FUN_00535890  speak (TextData)            (ttssapi.cpp)                 │
                        │  FUN_005de200  runtime DLL self-registration       (voicebar.cpp)        │
                        └───────────────┬───────────────────────────────┬──────────────────────────┘
                                        │ path A: SAPI4 COM             │ path B: L&H C API
                                        ▼                               ▼
                     rsstdv2.dll  (SAPI4 module, ATL)          lhsp01.dll (facade; LoadLibraryA)
                       └─ vautov5.dll  (pipeline runtime)          └─ lhsp\lhtts01.dll  (lhs_tts*)
                                                                       └─ lhtts01 → lhcom02W broker
                                                                             └─ rsstdv2_core.dll (ttscore)
                                                                             └─ rsstdv2_dec11.dll (decoder/83swf11)
```

### 4.1 Path B — L&H/RealSpeak "native" C API (loaded through `lhsp01.dll`)

`natspeak.exe` imports from `lhsp01.dll`:
`lhs_InitializeEx`, `lhs_UnInitialize`, `lhs_ttsOpenW`, `lhs_ttsClose`,
`lhs_ttsSetParamListW`, `lhs_ttsSetOutDevice`, `lhs_ttsSetTextW`, `lhs_ttsSpeak`, `lhs_ttsStop`,
`lhs_ErrorGetStringW`. `lhsp01.dll` is a **dispatcher/facade**: it `LoadLibraryA`s
`lhsp\lhtts01.dll`, `lhsp\lhasr01w.dll`, `lhsp\lhdct01w.dll` and forwards ~200 `lhs_*` exports
(TTS = `lhs_tts*`, ASR compile = `lhs_asrCpl*`, dictionaries = `lhs_dct*`).

Call sequence (from decompilation of `natspeak.exe` TTS init, image base `0x400000`):
`lhs_InitializeEx(0, key, 1, &handle)` → `lhs_ttsOpenW(handle, L"RealSpeak Standard v2", &inst, 0)`
→ `lhs_ttsSetParamListW(inst, {0x400, L"<language>"}, 1)` (param id `0x400` = language name;
language string comes from resource `g_wLSTR_REALSPEAK2_CURRENTLANGUAGE`, remapped
"American English"→"British English" for UK dialect) → `lhs_ttsSetOutDevice(inst, {callback,…})`
→ `lhs_ttsSetParamListW(inst, {0x2000, 0x202}, 1)` → then per utterance
`lhs_ttsSetTextW` + `lhs_ttsSpeak` (or `lhs_ttsStop`). Note the 2nd argument to
`lhs_InitializeEx` is a 139-byte printable-ASCII blob, byte-identical inside `natspeak.exe`
and `rsstdv2.dll` (`BoY25BncjL7eJ>QRXmaEb>Sh=HodG7Vm…`). No XOR/rotation/substitution scheme
tried reproduced plaintext; treat as an **obfuscated key/config blob (undecoded)** — candidates
are an OEM/licence key (the broker has "Could not read the OEM key from file %s") or an encoded
module list.

#### `lhtts01.dll` — L&H TTS API layer (`lhsp\lhtts01.dll`, "LH_MODULE_MMTTSAPIDLL")
35 exports, all `lhs_tts*`/`lhs_ErrorGetStringW` (see `decomp/lhtts01_all.c`):

```
lhs_ttsOpenA/W, Close, SetTextA/W, Speak, Stop, Pause, Resume, Process,
SetParamListA/W, GetParamListA/W, GetPPListA/W, GetVoiceListA/W, SetDictionaries,
SetInDevice, SetOutDevice, GenOutputFileA/W, GenPhoneticStringA/W, GetInstStatus,
GetLipSyncInfo, LoadRuleTableA/W, UnLoadRuleTable, UnLoadAllRuleTables,
DetectLanguageA/W, Forward, Backward
```
Behaviour (decompiled):
- One-time module init guarded by two critical sections; all APIs validate handle (the handle is a
  `CTtsInst*`; magic/`this` layout at `+0x1c`,`+0x20`); error codes are L&H `LHS_E_*`
  (`LHS_E_INVALIDHANDLE`, `LHS_E_NULLPOINTER`, `LHS_E_INVALIDARG`, `LHS_E_BUFFERTOOSMALL`,
  `LHS_E_BADCOMMAND`, `LHS_E_MEMALLOC`, `LHS_E_CONVERSIONFAILED`, `LHS_E_TTS_INSTBUSY`,
  `LHS_E_TTS_NOLANGUAGE`). Return codes are mapped to API-level codes (0/1/3/4/5/6/8/0x17).
- Instance creation (`CTtsInst` ctor, `FUN_1001c9c…`): opens the **broker**
  (`brk_ObjOpen`) and resolves `brk_BrokerStringQuery(broker, "TTSENGINE", <language>)` → engine
  id (the build contains `eng/rsstdv2` and legacy `eng/tts3000v7`), then `brk_ObjOpenEx(engine)`
  and `brk_InterfaceQuery(engine, "ttscore", …)` to obtain the `Core*` function table.
- Rule/parse-table loading (`FUN_1001b374`): queries broker info `ilr/mf` (other ids present:
  `ilr`, `ilr/lr`, `ilr/data`), `LoadLibraryA`s the returned DLL path, then
  `brk_InterfaceQuery(engine, "filter", …)` and calls its 5th slot to parse a
  text/HTML/XML buffer. This is the text-normalisation front end.
- Text control escapes supported by the engine: `\Chr=`, `\Com=`, `\Ctx=`, `\Dem`, `\Emp`,
  `\Eng:`, `\Eng;`, `\Mrk=`, `\Pau=`, `\Pit=`, `\Pra=`, `\Prn=`, `\Pro=`, `\Prt=`, `\RmS=`,
  `\RmW=`, `\RPit=`, `\RPrn=`, `\RSpd=`, `\Rst`, `\Spd=`, `\Vce=`, `\Vol=`.
- Output: PCM via WaveOut (`waveOutGetVolume`/`waveOutSetVolume`), or file writer (RIFF/WAVE);
  lip-sync info and phonetic-string generation are exposed.

#### `rsstdv2_core.dll` — RealSpeak Standard v2 core (`Core*` exports, 22)
```
CoreInitialize, CoreUninitialize, CoreOpen, CoreClose, CoreSetText, CoreForward, CoreBackward,
CoreReset, CoreStop, CoreTranscribe, CoreGetPcmBuf, CoreGetInterface, CoreGetParam, CoreSetParam,
CoreGetPPList, CoreGetVoiceList, CoreGetLanguageList, CoreSetDictionaries, CoreSetDictionaryCb,
Core_ErrorGetString, Core_ErrorGetModuleName, Core_ErrorGetSubModuleName
```
`rsstdv2_dec11.dll` exports exactly `gsmc_GetInterface` (decoder). Both are described by XML
headers (see §4.4). The core loads before the module interface (`Core Initialize failed`,
`CoreOpen failed`, then `GO TO CORE …` paths pushing PCM buffers).

#### Broker (`lhcom02W.dll`, `brk_*` API, "ScanSoft Speech SDKs")
Exports: `brk_ObjOpen/ObjOpenEx/ObjClose`, `brk_InterfaceQuery/InterfaceRelease/InfoQuery/
InfoRelease`, `brk_BrokerStringQuery/BrokerStringRelease`, `brk_ClassOpen(Ex)`,
`brk_ComponentListQuery`, `brk_DataOpen/DataSizeQuery`, `brk_TagQuery`, `brk_AddEngineToClass`,
plus resource (`rsrc_Open`, `hdrpars_OpenData/OpenResource`), dictionary (`edct_*`) and SPI
(`srvspi_*`) helpers. Resolution is descriptor-driven: it scans `*.hdr` descriptors
(`hdrpars_*`), registers engines/components, honours `LH_MODULES`, `LH_SUBMODULES`,
`LH_TRACELEVEL`, `LH_TRACEINFO`, `LH_INPATH`, `LH_OUTPATH`, `LH_LOGFILE`, and enforces
allowed-component/licence checks (messages: *"Engine %s is in the list of allowed components, but
the date has expired"*, *"…is not in the list of allowed components"*, *"NO ENGINE FOUND WITH
SPECIFIED BROKER STRING"*).

### 4.2 Path A — SAPI4 COM module (`Program\lhsp\rsstdv2.dll`)

`rsstdv2.dll` is an ATL in-proc COM server (`DllGetClassObject` etc.) implementing the SAPI4
engine model. It imports `lhsp01.dll` and `vautov5.dll`.

**Registration performed by `DllRegisterServer`** (script embedded in `.rdata`, invoked by
`natspeak.exe` at runtime, §3):

```
HKCR\CLSID\{A162A680-7689-4B2E-8A9B-6806F141EFC5} = "ScanSoft RealSpeak v2 Standard"
    \Programmable
    \InprocServer32 = <module path>, ThreadingModel = Apartment
HKLM\Software\Voice\TextToSpeech\Engine
    "ScanSoft RealSpeak v2 Standard" = "{A162A680-7689-4B2E-8A9B-6806F141EFC5}"
```
(The second key is the classic SAPI4 engine list location.)

Internal C++ classes (RTTI names): `CTTSInstBase`, `CTTSInstRealSpeak@RSSTDV2`,
`CTTSInstVauto5@VAUTO5`, `CEngineEnum` (coclass), `RSWrapper` + tear-off,
`RealSpeak_SAPI4_NotifyClass`; mode name `RealSpeak(SAPI4) Text to Speech Mode`; version
`16.00.200.121`; a hard-coded build path
`C:\Program Files\Nuance\Vocalizer for Automotive v5\common` survives in the binary.
`CTTSInstVauto5` calls `vauto_ttsProcessText2Speech` exported by `vautov5.dll`.

**Client side (`voicebar\ttssapi.cpp` in `natspeak.exe`)** — decompiled flow:
1. `CoCreateInstance(CLSID {A162A680-7689-4B2E-8A9B-6806F141EFC5}, IID {6B837B20-4A47-101B-931A-00AA0047BA4F}
   /* mode enumerator */, CLSCTX_INPROC_SERVER)` → `pITTSEnum`.
2. `pITTSEnum->vfn[3](1, &modeEntry, &fetched)` — `Next`; `S_FALSE(1)` ⇒
   *"SAPI installed, but no SAPI TTS engine modes found."* Each entry is
   `{GUID modeId; WCHAR name[262];}`.
3. Mode choice: compare against the *expected* mode name (registry `TTSModeName` /
   `UKTTSModeName`, else per-language default from the table below); compare the `GuidMode` field
   with the preferred GUID; if not found, log *"TTS mode '%s' wasn't found. Try to set another
   aviable TTS mode '%s'"* and fall back to the last mode whose language is US-English
   (LCID `0x809`) dialect 1; if still nothing → "Couldn't find expected TTS mode '%s'…"
   and throw `0x80040206`.
4. `pITTSEnum->vfn[7](guid, &pITTS…, 0)` — `Select` → engine object stored at `obj+0xc`.
5. `Register(notifySink, IID {C0FA8F40-4A46-101B-931A-00AA0047BA4F}, …, &key)` where
   `notifySink` is a `ClsTTSNotifySink` instance (`SpeakingStarted`/`SpeakingDone` callbacks,
   *"Got SpeakingStarted notification %d"*); returned key stored at `obj+0x48`.
6. QueryInterface for `{1287A280-4A47-101B-931A-00AA0047BA4F}` (`m_pITTSCentral`),
   `{1D59DED1-E367-11D1-BED7-006008317CE8}` (`IAttributes`, pitch/speed/volume). If the engine
   vendor strings are `Dragon NaturallySpeaking` / `ScanSoft, Inc.`, it additionally calls
   `StringSet(0x101b, <today's date>)` on `IAttributes` (a date/licence attribute).
7. Pitch/speed/volume initialised from `RgyReadDataInt` settings (`TTSPitch`, `TTSSpeed`,
   `TTSVolume`); range conversion *"Converted %d in range %d - %d to %d in range %d - %d"*;
   WaveOut master volume is captured and clamped while speaking, then restored
   (*"TTS Volume previously changed.  Leaving it alone."*).
8. Speak (`FUN_00535890`): reject empty text (*"TTS received empty string"*), then
   `m_pITTSCentral->TextData(0 /*flags*/, 0 /*attrs*/, text, byteLen, ctx, formatGUID
   {E4963D40-C743-11CD-80E5-00AA003E4B50})`; errors are reported through `ComCallFailed`.
   `StopSpeaking`/`UnInitialize` use `UnRegister(key)` (*"Passing key %d to
   ITTSCentral::UnRegister"*) and release audio objects.

**Per-language SAPI4 mode names** (`FUN_00539540`, primary language id switch):
`1` English → *"British English Jane"* unless dialect ∈ {0,1,6} → *"American English Jennifer"*;
`2` French Sophie; `3` Italian Bianca; `4` German Vera; `5` Spanish Isabel; `6` Japanese Tomoko;
`7` Dutch Laura; default *"American English Jennifer"*. If `useRealSpeakV2SAPI4 ≠ 0` and the run is
UK English the default name becomes *"RealSpeak (Jane) UK English (v2) Female #2"*.
Engine-list strings near `opttts.cpp`: `pITTSEnum`, `Next`, `ModeGet`, `Reset`, `L&H Speech
RealSpeak`, format prints `/l%d /p%d /s%d` and `/l%d /s%d`.

**Legacy path selection** (`FUN_00537e20`): read `useRealSpeakV2SAPI4`.
- flag `0` **and** the stored mode name equals the language default → *legacy L&H path* (§4.1),
  i.e. `lhs_InitializeEx`/`lhs_ttsOpenW(L"RealSpeak Standard v2")`.
- otherwise → SAPI4 path, executed asynchronously by posting `WM_USER+0x874` to the main window
  (`Main Window got a TTS message…` / `Returning from main window TTS message processing…`) or
  directly via `FUN_00533ff0`+`FUN_00534b10`. `TTSWrap` wraps every call as a posted message with
  `critLongTimeout` waits (`TTSWrap::Speak timeout`, `TTSWrap::Initialize timeout`, …).
`natspeak.exe` also drives TTS through its command engine (`CmdTTSPlayString`, *"Reg hotkey for
TTS…"*) and the Options → Playback/TTS page.

### 4.3 `vautov5.dll` — Vocalizer-5-family pipeline runtime

Exports are a flat component registry (each component = `*_GetInterface`), matching the pipeline
manifest in §4.4: `fe_lid, fe_udwl, fe_clcml, fe_promptoriorth, fe_unixlit, fe_initlingdb,
fe_promptorth, fe_word_seg, fe_ara_parser, tokentn, fe_abbrtn, fe_puncsptn, fe_oneword, fe_pos,
fe_hmogrph, fe_phrasing, fe_normout, fe_prompt, fe_global, fe_deinitlingdb, fe_dctlkp, fe_depes,
uselect, synth, synthstream, xcoder, xcoder_mrksync, phonmap, phonmapmrk, phonmap/sym maps,
dcteg, domain_mngr, tteg, ttseg, inetspi, lespi, bespi, lexicon lookup, parser, stdpp, ules, ords,
lingdb, compquery, audiofetch, audioinserter, gsmc_vssq, gsmc_encrypt, log/logsub_logfile`,
plus `CLM_GetInterface`, `InitRsrcFunction` and L&H string helpers (`LH_wcs*`, `LH_atoi`, …),
`MD5*`, `base64_*`, `Utf8_*`, `XLIT_str*` conversions.
`rsstdv2.dll` drives text→speech via `vauto_ttsProcessText2Speech`.

### 4.4 Voice data and descriptor formats

**`rsstdv2_core.hdr` / `rsstdv2_dec11.hdr`** (UTF-16 XML, `<LHS>` schema) — one per DLL, they are
the broker's component registry:

```xml
<LHS><VERSION>LH_1.0</VERSION><HEADER>
  <COMPTYPE>CORE|DECODER</COMPTYPE><VERSION>2.0</VERSION><NAME>Core Module|Decoder libraby</NAME>
  <LOCATION>United States</LOCATION><TTSTYPE>REALSPEAK STANDARD|REALSPEAK</TTSTYPE>
  <BROKERSTRING>ttscore|decoder/83swf11</BROKERSTRING><ENGINE>eng/rsstdv2</ENGINE>
  <DLL>rsstdv2_core.dll|rsstdv2_dec11.dll</DLL>
  <GETINTERFACE>CoreGetInterface|gsmc_GetInterface</GETINTERFACE>
</HEADER></LHS>
```
`<BROKERSTRING>` is what `brk_InterfaceQuery` looks up; `<ENGINE>` groups components;
`<GETINTERFACE>` is the DLL entry point returning the interface table.

**`ss_wind32.plh`** — `LHKP LIST` inventory (4 items) with 26 languages
(`{ENU American English},{ARA Arabic},{ARE Arabic (Egyptian)},{ENA Australian English},
{DUB Belgian Dutch},{FRB Belgian French},{PTB Brazilian Portuguese},{ENG British English},
{CAH Cantonese Chinese},{SPE Spanish},{DAD Danish},{DUN Dutch},{FRF French},{GED German},
{ITI Italian},{JPJ Japanese},{KOK Korean},{MNC Mandarin Chinese},{SPM Mexican Spanish},
{NON Norwegian},{PLP Polish},{PTP Portuguese},{RUR Russian},{ENS Singapore English},{SWS Swedish}`),
8 technologies and 7 component types — the broker's language/technology capability matrix.

**`vauto_pipeline_<lang>_<voice>_22_<model>.hdr`** (ASCII XML, `<NUANCE>` schema) — the actual TTS
pipeline manifest for one voice/vocoder combination. Example (US Samantha):

```xml
<NUANCE><VERSION>NUAN_1.0</VERSION><HEADER>
 <BROKERSTRING>pipeline/American English/samantha/22/full_155mrf22/text/pcm</BROKERSTRING>
 <PRIORITY>4312</PRIORITY>
 <PARAMETERS><language>American English</language><langcode>ENU</langcode><langid>10</langid>
   <langversion>5.2.3.11091</langversion><nativetypeofchar>utf-16</nativetypeofchar>
   <voice>Samantha</voice><voiceversion>5.2.3.10264</voiceversion><gender>Female</gender>
   <age>Adult</age><fecfg>cfg3</fecfg><voiceml>no</voiceml><fedatapackaging>clc</fedatapackaging>
   <fevoice>Samantha</fevoice><frequencyhz>22050</frequencyhz>
   <voicemodel>full_155mrf22</voicemodel><reduction>full</reduction><coder>155mrf22</coder>
   <bitrate>270</bitrate><overheadframes>06</overheadframes>
   <audiooutputmimetype>audio/L16;rate=22050</audiooutputmimetype>
   <audiooutputsamplebits>16</audiooutputsamplebits></PARAMETERS>
 <OBJECTS><AUDIOFETCHER>audiofetch</AUDIOFETCHER><DCTEG>dcteg</DCTEG>
   <DOMAINMNGR>domain_mngr</DOMAINMNGR><FE_DCTLKP>fe/fe_dctlkp</FE_DCTLKP>
   <FE_DEPES>fe/fe_depes</FE_DEPES><INET>inetspi</INET><PHONMAP>phonmap</PHONMAP></OBJECTS>
 <COMPONENTS>xcoder, ttt/re, pp/text, fe/fe_lid, fe/fe_udwl, fe/fe_clcml,
   fe/fe_promptoriorth, fe/fe_unixlit, fe/fe_initlingdb, fe/fe_promptorth, fe/tokentn,
   fe/fe_abbrtn, fe/fe_puncsptn, fe/fe_oneword, fe/fe_pos, fe/fe_hmogrph, fe/fe_phrasing,
   fe/fe_normout, fe/fe_prompt, fe/fe_global, fe/fe_deinitlingdb, uselect/bet1, synth/bet1,
   audioinserter, phonmap/mrk, xcoder/mrksync</COMPONENTS>
 <RESOURCES><RESOURCE content-type="application/edct-bin-dictionary;loader=broker">
   userdct/enu</RESOURCE></RESOURCES></HEADER></NUANCE>
```
Two manifests ship per voice (English example): model `full_155mrf22` (bitrate 270, 22050 Hz,
16-bit, `audio/L16`) and `full_vssq5f22`.

Data files per voice (`Program\Voices\speech\components\`):
`clc_<lang>_cfg3.dat` (front-end config), `clc_<lang>_<voice>_cfg3.dat` (voice override),
`synth_<voice>_full_<model>.dat` (synthesis units — the large blobs: Samantha 106.9 MB +
434.9 MB; Serena 44.6 MB + 181.4 MB), `uselect_<voice>_full.dat` (unit selection)
`pp.dat` + `synth_med_fxd_bet2f22.dat` (shared base voice, in the `voices/speech/components`
spelling). File magic/encoding of the `.dat` files is proprietary and was not decoded.

### 4.5 What a re-implementation needs (TTS)

1. **Broker/descriptor layer**: scan `*.hdr`, parse the two XML schemas above, build the
   broker-string → (DLL, GETINTERFACE) map; implement `brk_*` semantics incl. the
   `TTSENGINE`/`ttscore`/`decoder/83swf11`/`ilr/*` names.
2. **Legacy C API**: `lhs_tts*` (35 functions, §4.1) with handle-based instances, parameter list
   entries `{id, value}` (observed ids `0x400` = language string, `0x2000`/`0x202` = output),
   output-device callback, PCM/WAVE output, `LHS_E_*` error space.
3. **SAPI4 module**: COM server registering CLSID `{A162A680-7689-4B2E-8A9B-6806F141EFC5}` under
   both `HKCR\CLSID` and `HKLM\Software\Voice\TextToSpeech\Engine`, exposing a mode enumerator
   (`Next`/`Select`) over `{GUID, name}` entries and the SAPI4 interfaces used by the client
   (`ITTSCentral` `Register`/`TextData`/`UnRegister`, `IAttributes` `StringSet`/`DWORDGet`/…,
   `ITTSNotifySink`), plus `RealSpeak(SAPI4) Text to Speech Mode` naming.
4. **Pipeline runtime**: component registry (`*_GetInterface`) and the text→PCM graph
   (front-end normalisation → letter-to-sound/unit selection → synthesis → audio inserter),
   parameter set from the manifest, output `audio/L16;rate=22050`, 16-bit.
5. **Client integration**: mode-name map per language, `useRealSpeakV2SAPI4` preference switch,
   pitch/speed/volume settings, message-based async calls with timeouts, notification sink,
   WaveOut volume save/clamp/restore, hotkey + `PlayString` command.
6. **Voice data**: either ship equivalent synthesizer data or substitute a pluggable synthesizer —
   the interface contract is the manifest + component names, not the `.dat` internals.

---

## 5. ASR side (context for the rest of the product)

Datapack (`<CommonAppData>\Nuance\NaturallySpeaking16\Datapack\professional-<lang>-<region>\`):
acoustic model `.usr` (~15–30 MB), `.psh` (phone/state headers), `.sig`, `.enh` (ITN/lexicon/
tokeniser enhancers: `lexicon.enh`, `token_s.enh`, `itn_s.enh`, `autopunct_s.enh`,
`aco_bm4_dnn.enh`), language model `lm/general/large/dispatch.svc` (~150–275 MB) + `dispatch.voc`
(~12–23 MB) and per-domain `lm/<domain>/...` variants, plus `config/models.ini` and
`config/server.ini`. `models.ini`/`server.ini` in the shipped cab are **binary/encrypted**
(magic `66 B6 01 01`, entropy ≈ 7.96); they are generated on the target by
`natspeak.exe -rebuildmodels` (custom actions `GenerateModelsIni`/`RegenerateModelsIni`).
The `Data\<lang>\{dat,dvce,dvcu}\...` trees hold user/voice data templates;
`common-*` files populate `Users` profiles.

---

## 6. Licensing / activation surface

- Serial number: `ACTIVATION_SERIALNUMBER` → `SERIALNUMBER` → `ProductID`, validated by
  `CheckSerialNumber` (ISSetup `f4`), failure message embedded; history/activation recorded under
  `SOFTWARE\ScanSoft\NaturallySpeaking16\Activation` and
  `SOFTWARE\ScanSoft\Shared\Products\<serial>\<version>` (`CPID`, `SerialNumber`),
  `SOFTWARE\Nuance\DNSHistory\<timestamp>`; bootstrapper runs with `WEBREGISTRATION=0 VLAFLAG=0`.
- FLEXnet/FlexNet Connect: `CommonAppData\FLEXnet` + `Connect` plus `StopFLEXnet` CA and
  `KillFlexnetApp.vbs` shipped in setup files.
- Bootstrapper chain checks prior installs (DNS 12–15.x) via `RegLocator`/`AppSearch` signatures
  `sigDNS15*` and blocks downgrades/co-installs with Dragon Speech Server / Medical EHR.

---

## 7. Tooling & reproduction

```
parse_msi.py <msi_streams_dir> <out_dir>      # decodes all MSI tables to CSV (see §2.1 encoding)
ghidra_scripts/DumpExports.java               # decompile every export (arg="exports" for exports only)
ghidra_scripts/DumpSome.java <out> <namePat>  # decompile functions by name substring
ghidra_scripts/DumpXref.java <out> <str> [wide]  # decompile functions referencing a string
ghidra_scripts/DumpCallers.java <out> <func>  # decompile callers of a function
```
Usage:
```
./7zz x Dragon16.zip -oextract 'DPG_DLG_16.0_NUMC/*'
./7zz e extract/DPG_DLG_16.0_NUMC/NatSpeak.cab -obins -y natspeak.exe ...
/opt/ghidra/support/analyzeHeadless ghidra_proj ttsproj -import bins/x.dll \
    -scriptPath ghidra_scripts -postScript DumpExports.java out.c exports
```
Artifacts: `decomp/lhtts01_all.c` (5,341 lines, all TTS exports),
`decomp/lhtts01_internals.c` (broker/loader/instance internals),
`decomp/rsstdv2_core_all.c`, `decomp/rsstdv2_sapi4.c` (registration script),
`decomp/dd10sapi_all.c`, `decomp/natspeak_realspeak.c` (`FUN_00537e20` init),
`decomp/ns_sapi_nomodes.c` + `ns_sapi_speak.c` (SAPI4 select/speak),
`decomp/ns_modename*.c` (mode-name map), `decomp/ns_register*.c` (runtime COM registration),
`decomp/ns_langmap.c`, `msi_tables/*.csv`, `voice/*.hdr`, `bins/*`.

---

## 8. Open items / caveats

1. **Obfuscated 139-byte blob** shared by `natspeak.exe` and `rsstdv2.dll`, passed to
   `lhs_InitializeEx` — not decoded (no simple XOR/rotation/substitution). Likely an OEM/licence
   key or encoded module list.
2. **`models.ini`/`server.ini` encryption** (magic `66 B6 01 01`) — writer/reader not located in
   the extracted binaries; presumably inside `natspeak.exe`'s `-rebuildmodels` path or a
   non-extracted module.
3. **Broker header search paths** (`*.hdr` discovery root) not fully traced; `LH_MODULES`/
   `LH_SUBPATH`-style environment/registry inputs are referenced but their source was not pinned
   down. The per-language `lhsp\rsstdv2\<lang>` directories are created but **empty** in this SKU
   (the legacy per-voice `rsstdv2_<lang>_<voice>_f11.hdr` files that `natspeak` looks for are not
   shipped), so the legacy RealSpeak-2 path depends on runtime-generated/absent headers.
4. **`g_wRGY_*` setting names** (`TTSModeName`, `UKTTSModeName`, `useRealSpeakV2SAPI4`,
   `TTSPitch/Speed/Volume`) are stored as string-table IDs; the literal value names were not
   extracted. Only `nssystem.ini [Settings] enx TTS Enabled` was observed in the INI payload.
5. InstallShield internal actions (`ISSetup.dll f1…f101`) are proprietary and were not
   decompiled; behaviour was inferred from action names, targets and messages.
6. `.dat` synthesis payload internals (unit-selection/PSOLA or DNN models) are proprietary; only
   their role, naming and manifest metadata are documented.

---

## 9. Quick reference

- Product/version: `Dragon` 16.00.200.121, ProductCode `{332B63C5-EFDD-4BFD-AB35-63E0C84FF2A3}`,
  UpgradeCode `{DDDD0D1D-2E1D-4F0C-AE80-77FF29AB7535}`.
- SAPI4 TTS CLSID: `{A162A680-7689-4B2E-8A9B-6806F141EFC5}` ("ScanSoft RealSpeak v2 Standard").
- Mode-enumerator IID used by the client: `{6B837B20-4A47-101B-931A-00AA0047BA4F}`;
  `ITTSCentral` `{1287A280-4A47-101B-931A-00AA0047BA4F}`;
  `ITTSNotifySink` `{C0FA8F40-4A46-101B-931A-00AA0047BA4F}`;
  `IAttributes` `{1D59DED1-E367-11D1-BED7-006008317CE8}`;
  text format GUID passed to `TextData`: `{E4963D40-C743-11CD-80E5-00AA003E4B50}`.
- Broker strings: `TTSENGINE` → `eng/rsstdv2`; interfaces `ttscore`, `filter`, decoder
  `decoder/83swf11`; info `ilr`, `ilr/mf`, `ilr/lr`, `ilr/data`;
  pipelines `pipeline/<Language>/<voice>/22/<model>/text/pcm`.
- Voices: ENU **Samantha**, ENG **Serena**; 22050 Hz, 16-bit, `audio/L16;rate=22050`;
  models `full_155mrf22` (270 kb/s) and `full_vssq5f22`.
- Legacy mode names: American English Jennifer, British English Jane, French Sophie,
  Italian Bianca, German Vera, Spanish Isabel, Japanese Tomoko, Dutch Laura.
