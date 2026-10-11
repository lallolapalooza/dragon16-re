"""The `lhs_tts*` API surface (35 exports) with the semantics observed in the original layer.

Handles are opaque integers here (the original hands out pointers).  The stdcall decorations in
the original PE exports are listed in `include/lhs_tts.h`.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import __version__, synth as synth_mod
from .pipeline import Pipeline
from .voicebank import VoiceBank

# ---------------------------------------------------------------------------
# error codes (names verified in the binary; numeric values are the observed API-level returns)
LHS_OK = 0
LHS_E_GENERAL = 1
LHS_E_OPENFAIL = 3
LHS_E_WRONGLANG = 4
LHS_E_NOVOICE = 5
LHS_E_MEMALLOC = 6
LHS_E_TTS_INSTBUSY = 8
LHS_E_INVALIDHANDLE = 0xB
LHS_E_INVALIDARG = 0xC
LHS_E_NULLPOINTER = 0xD
LHS_E_BUFFERTOOSMALL = 0xE
LHS_E_BADCOMMAND = 0x17

_ERROR_TEXT = {
    LHS_OK: "no error",
    LHS_E_GENERAL: "general error",
    LHS_E_OPENFAIL: "could not create instance",
    LHS_E_WRONGLANG: "language not supported",
    LHS_E_NOVOICE: "voice not found",
    LHS_E_MEMALLOC: "memory allocation failed",
    LHS_E_TTS_INSTBUSY: "the specified instance is busy",
    LHS_E_INVALIDHANDLE: "invalid handle",
    LHS_E_INVALIDARG: "invalid argument",
    LHS_E_NULLPOINTER: "null pointer",
    LHS_E_BUFFERTOOSMALL: "buffer too small",
    LHS_E_BADCOMMAND: "bad command",
}

# parameter ids observed in use by the consumer
PARAM_LANGUAGE = 0x400
PARAM_OUTPUTCFG = 0x2000

STATE_IDLE, STATE_SPEAKING, STATE_PAUSED = 0, 1, 2


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        for enc in ("utf-16-le", "utf-8", "latin-1"):
            try:
                return value.decode(enc).rstrip("\x00")
            except (UnicodeDecodeError, LookupError):
                continue
        return value.decode("latin-1", "replace")
    return str(value)


def _cstr(value, limit: int = 4096) -> str:
    s = _as_text(value)
    return s.split("\x00", 1)[0][:limit]


@dataclass
class TtsInstance:
    """One `lhs_ttsOpen` handle."""

    voice: str = ""
    pipeline: Optional[Pipeline] = None
    language: str = ""
    state: int = STATE_IDLE
    text: str = ""
    params: Dict[int, object] = field(default_factory=dict)
    out_device: Optional[Tuple[Callable[[bytes], None], object]] = None
    last_pcm: bytes = b""
    last_wav: bytes = b""


class TtsSystem:
    """Module-level state (the original keeps a critical section + instance list)."""

    _next_handle = 1

    def __init__(self, bank: Optional[VoiceBank] = None,
                 synthesiser: Optional[synth_mod.Synthesiser] = None,
                 search_dirs: Optional[Sequence[str]] = None):
        self.bank = bank or (VoiceBank.load(search_dirs[0]) if search_dirs else None)
        self.synthesiser = synthesiser
        self.instances: Dict[int, TtsInstance] = {}
        self.initialized = False
        self.key: bytes = b""

    # -- lifecycle -------------------------------------------------------
    def initialize_ex(self, flags: int, key, count: int) -> int:
        self.key = bytes(key) if isinstance(key, (bytes, bytearray)) else _as_text(key).encode("latin-1", "replace")
        self.initialized = True
        return LHS_OK

    def uninitialize(self) -> int:
        self.instances.clear()
        self.initialized = False
        return LHS_OK

    # -- instance --------------------------------------------------------
    def open(self, engine, voice, flags: int = 0, flags2: int = 0):
        name = _cstr(voice)
        try:
            pipeline = Pipeline.for_voice(self.bank, name if self.bank else "", self.synthesiser)
        except KeyError:
            # unknown voice label: the original still opens with the default voice where one
            # is installed; only a bank with no voices at all is a hard failure.
            if self.bank is None or not self.bank.voices:
                return LHS_E_NOVOICE, 0
            pipeline = Pipeline.for_voice(self.bank, "", self.synthesiser)
        if name and pipeline.voice and pipeline.voice.lower() not in name.lower() \
                and name.lower() not in pipeline.voice.lower() \
                and name.lower() not in pipeline.langcode.lower():
            # unknown voice label: still open with the default voice (original falls back too)
            pass
        h = TtsSystem._next_handle
        TtsSystem._next_handle += 1
        self.instances[h] = TtsInstance(voice=name, pipeline=pipeline, language=pipeline.language)
        return LHS_OK, h

    def close(self, handle: int) -> int:
        if handle not in self.instances:
            return LHS_E_INVALIDHANDLE
        del self.instances[handle]
        return LHS_OK

    def _inst(self, handle: int) -> Optional[TtsInstance]:
        return self.instances.get(handle)

    # -- parameters ------------------------------------------------------
    def set_param_list(self, handle: int, params: Sequence[Tuple[int, object]]) -> int:
        inst = self._inst(handle)
        if inst is None:
            return LHS_E_INVALIDHANDLE
        if not params:
            return LHS_E_INVALIDARG
        for pid, value in params:
            if pid == PARAM_LANGUAGE and inst.pipeline:
                inst.params[pid] = value
                inst.language = _as_text(value)
            elif pid == PARAM_OUTPUTCFG:
                inst.params[pid] = value
            else:
                inst.params[pid] = value
        return LHS_OK

    def get_param_list(self, handle: int, ids: Sequence[int]):
        inst = self._inst(handle)
        if inst is None:
            return LHS_E_INVALIDHANDLE, []
        return LHS_OK, [(pid, inst.params.get(pid)) for pid in ids]

    # -- speaking --------------------------------------------------------
    def set_text(self, handle: int, text) -> int:
        inst = self._inst(handle)
        if inst is None:
            return LHS_E_INVALIDHANDLE
        inst.text = _as_text(text).rstrip("\x00")
        return LHS_OK

    def speak(self, handle: int, mode: int = 0) -> int:
        inst = self._inst(handle)
        if inst is None:
            return LHS_E_INVALIDHANDLE
        if not inst.text:
            return LHS_E_INVALIDARG
        if inst.pipeline is None:
            return LHS_E_OPENFAIL
        inst.state = STATE_SPEAKING
        wav = inst.pipeline.synthesize(inst.text, return_wav=True)
        inst.last_wav = wav
        inst.last_pcm = wav[44:]
        if inst.out_device is not None:
            cb, cookie = inst.out_device
            cb(inst.last_pcm)
        inst.state = STATE_IDLE
        return LHS_OK

    def stop(self, handle: int) -> int:
        inst = self._inst(handle)
        if inst is None:
            return LHS_E_INVALIDHANDLE
        inst.state = STATE_IDLE
        return LHS_OK

    def pause(self, handle: int) -> int:
        inst = self._inst(handle)
        if inst is None:
            return LHS_E_INVALIDHANDLE
        inst.state = STATE_PAUSED
        return LHS_OK

    def resume(self, handle: int) -> int:
        inst = self._inst(handle)
        if inst is None:
            return LHS_E_INVALIDHANDLE
        inst.state = STATE_SPEAKING if inst.text else STATE_IDLE
        return LHS_OK

    def status(self, handle: int):
        inst = self._inst(handle)
        if inst is None:
            return LHS_E_INVALIDHANDLE, 0
        return LHS_OK, inst.state

    def gen_output_file(self, handle: int, path, fmt: int = 0) -> int:
        inst = self._inst(handle)
        if inst is None:
            return LHS_E_INVALIDHANDLE
        if not inst.last_wav:
            rc = self.speak(handle)
            if rc != LHS_OK:
                return rc
        with open(_cstr(path), "wb") as fh:
            fh.write(inst.last_wav)
        return LHS_OK

    def gen_phonetic_string(self, handle: int, text, cch: int = 4096) -> Tuple[int, str]:
        inst = self._inst(handle)
        if inst is None:
            return LHS_E_INVALIDHANDLE, ""
        from .phonemes import text_to_phones
        s = " ".join(p for p, _ in text_to_phones(_as_text(text)) if p and p != ".")
        return LHS_OK, s

    def set_out_device(self, handle: int, callback, cookie=None) -> int:
        inst = self._inst(handle)
        if inst is None:
            return LHS_E_INVALIDHANDLE
        inst.out_device = (callback, cookie)
        return LHS_OK

    # -- discovery -------------------------------------------------------
    def voice_list(self) -> List[Tuple[str, str]]:
        if self.bank is None:
            return []
        return [(v.voice or k, v.langcode) for k, v in self.bank.voices.items()]

    def pp_list(self) -> List[str]:
        from .synth import PHONES
        return sorted({p for p in PHONES if p not in ("_", ".")})


class Module:
    """Container mirroring the original flat C API (module-level functions below delegate here)."""

    def __init__(self, **kw):
        self.system = TtsSystem(**kw)

    # thin wrappers with original return conventions
    def InitializeEx(self, flags, key, count):
        return self.system.initialize_ex(flags, key, count)

    def Initialize(self, flags=0):
        return self.system.initialize_ex(flags, b"", 0)

    def UnInitialize(self):
        return self.system.uninitialize()

    def ttsOpen(self, engine, voice, flags=0, flags2=0):
        rc, handle = self.system.open(engine, voice, flags, flags2)
        return rc, handle

    def ttsClose(self, handle):
        return self.system.close(handle)

    def ttsSetText(self, handle, text):
        return self.system.set_text(handle, text)

    def ttsSpeak(self, handle, mode=0):
        return self.system.speak(handle, mode)

    def ttsStop(self, handle):
        return self.system.stop(handle)

    def ttsPause(self, handle):
        return self.system.pause(handle)

    def ttsResume(self, handle):
        return self.system.resume(handle)

    def ttsProcess(self, handle, mode=0):
        return LHS_OK if self.system._inst(handle) else LHS_E_INVALIDHANDLE

    def ttsSetParamList(self, handle, params):
        return self.system.set_param_list(handle, params)

    def ttsGetParamList(self, handle, ids):
        return self.system.get_param_list(handle, ids)

    def ttsGetVoiceList(self):
        return self.system.voice_list()

    def ttsGetPPList(self):
        return self.system.pp_list()

    def ttsSetOutDevice(self, handle, callback, cookie=None):
        return self.system.set_out_device(handle, callback, cookie)

    def ttsGenOutputFile(self, handle, path, fmt=0):
        return self.system.gen_output_file(handle, path, fmt)

    def ttsGenPhoneticString(self, text, cch=4096):
        return self.system.gen_phonetic_string(0, text, cch)

    def ttsGetInstStatus(self, handle):
        return self.system.status(handle)

    def ttsSetDictionaries(self, handle, dictionaries=()):
        if handle not in self.system.instances:
            return LHS_E_INVALIDHANDLE
        return LHS_OK

    def ttsForward(self, handle, n=1, units=0):
        return LHS_OK if handle in self.system.instances else LHS_E_INVALIDHANDLE

    def ttsBackward(self, handle, n=1, units=0):
        return LHS_OK if handle in self.system.instances else LHS_E_INVALIDHANDLE

    def ttsLoadRuleTable(self, handle, path):
        return LHS_OK if handle in self.system.instances else LHS_E_INVALIDHANDLE

    def ttsUnLoadRuleTable(self, handle, table=None):
        return LHS_OK if handle in self.system.instances else LHS_E_INVALIDHANDLE

    def ttsUnLoadAllRuleTables(self, handle):
        return LHS_OK if handle in self.system.instances else LHS_E_INVALIDHANDLE

    def ttsDetectLanguage(self, handle, text):
        inst = self.system._inst(handle)
        if inst is None:
            return LHS_E_INVALIDHANDLE, ""
        return LHS_OK, (inst.pipeline.langcode if inst.pipeline else "")

    def ttsGetLipSyncInfo(self, handle):
        return LHS_OK if handle in self.system.instances else LHS_E_INVALIDHANDLE


def ErrorGetString(code: int) -> str:
    return _ERROR_TEXT.get(int(code), f"unknown error 0x{int(code) & 0xFFFFFFFF:X}")


# ---------------------------------------------------------------------------
# Flat C-style API (names match the original exports; A/W pairs collapse to one implementation).
_DEFAULT = Module()

InitializeEx = _DEFAULT.InitializeEx
Initialize = _DEFAULT.Initialize
UnInitialize = _DEFAULT.UnInitialize
lhs_InitializeEx = _DEFAULT.InitializeEx
lhs_Initialize = _DEFAULT.Initialize
lhs_UnInitialize = _DEFAULT.UnInitialize
lhs_ErrorGetStringW = ErrorGetString
lhs_ErrorGetStringA = ErrorGetString


def configure(bank: Optional[VoiceBank] = None,
              synthesiser: Optional[synth_mod.Synthesiser] = None,
              search_dirs: Optional[Sequence[str]] = None) -> Module:
    """(Re)create the default module state (used by the CLI and tests)."""
    global _DEFAULT
    _DEFAULT = Module(bank=bank, synthesiser=synthesiser, search_dirs=search_dirs)
    _rebind()
    return _DEFAULT


def _rebind() -> None:
    g = globals()
    for name in ("InitializeEx", "Initialize", "UnInitialize", "lhs_InitializeEx",
                 "lhs_Initialize", "lhs_UnInitialize"):
        g[name] = _DEFAULT.InitializeEx if "Ex" in name else (
            _DEFAULT.Initialize if name in ("Initialize", "lhs_Initialize") else _DEFAULT.UnInitialize)


def lhs_ttsOpenA(engine, voice, out_handle=None, flags: int = 0, flags2: int = 0):
    rc, h = _DEFAULT.ttsOpen(engine, voice, flags, flags2)
    if out_handle is not None and isinstance(out_handle, list):
        out_handle[:] = [h]
    return rc if out_handle is None else (rc, h)


lhs_ttsOpenW = lhs_ttsOpenA
lhs_ttsClose = _DEFAULT.ttsClose
lhs_ttsSetTextA = _DEFAULT.ttsSetText
lhs_ttsSetTextW = _DEFAULT.ttsSetText
lhs_ttsSpeak = _DEFAULT.ttsSpeak
lhs_ttsStop = _DEFAULT.ttsStop
lhs_ttsPause = _DEFAULT.ttsPause
lhs_ttsResume = _DEFAULT.ttsResume
lhs_ttsProcess = _DEFAULT.ttsProcess
lhs_ttsSetParamListA = _DEFAULT.ttsSetParamList
lhs_ttsSetParamListW = _DEFAULT.ttsSetParamList
lhs_ttsGetParamListA = _DEFAULT.ttsGetParamList
lhs_ttsGetParamListW = _DEFAULT.ttsGetParamList
lhs_ttsGetVoiceListA = lambda *a, **k: _DEFAULT.ttsGetVoiceList()
lhs_ttsGetVoiceListW = lhs_ttsGetVoiceListA
lhs_ttsGetPPListA = lambda *a, **k: _DEFAULT.ttsGetPPList()
lhs_ttsGetPPListW = lhs_ttsGetPPListA
lhs_ttsSetDictionaries = _DEFAULT.ttsSetDictionaries
lhs_ttsSetInDevice = lambda handle, desc=None: (LHS_OK if handle in _DEFAULT.system.instances
                                                else LHS_E_INVALIDHANDLE)
lhs_ttsSetOutDevice = _DEFAULT.ttsSetOutDevice
lhs_ttsGenOutputFileA = _DEFAULT.ttsGenOutputFile
lhs_ttsGenOutputFileW = _DEFAULT.ttsGenOutputFile
lhs_ttsGenPhoneticStringA = lambda handle, text, *a, **k: _DEFAULT.ttsGenPhoneticString(text)[1]
lhs_ttsGenPhoneticStringW = lhs_ttsGenPhoneticStringA
lhs_ttsGetInstStatus = _DEFAULT.ttsGetInstStatus
lhs_ttsGetLipSyncInfo = _DEFAULT.ttsGetLipSyncInfo
lhs_ttsLoadRuleTableA = _DEFAULT.ttsLoadRuleTable
lhs_ttsLoadRuleTableW = _DEFAULT.ttsLoadRuleTable
lhs_ttsUnLoadRuleTable = _DEFAULT.ttsUnLoadRuleTable
lhs_ttsUnLoadAllRuleTables = _DEFAULT.ttsUnLoadAllRuleTables
lhs_ttsDetectLanguageA = _DEFAULT.ttsDetectLanguage
lhs_ttsDetectLanguageW = _DEFAULT.ttsDetectLanguage
lhs_ttsForward = _DEFAULT.ttsForward
lhs_ttsBackward = _DEFAULT.ttsBackward
