"""SAPI4 module model: CLSIDs/IIDs, mode enumeration, client protocol, registry script.

Facts (verified from `rsstdv2.dll` and `natspeak.exe`):

  engine CLSID   {A162A680-7689-4B2E-8A9B-6806F141EFC5}   "ScanSoft RealSpeak v2 Standard"
  enumerator IID {6B837B20-4A47-101B-931A-00AA0047BA4F}   Next(1,&entry,&fetched) / Select(id,&obj,0)
  central IID    {1287A280-4A47-101B-931A-00AA0047BA4F}   Register(sink,...) / TextData(flags,attrs,..)
  sink IID       {C0FA8F40-4A46-101B-931A-00AA0047BA4F}
  attributes IID {1D59DED1-E367-11D1-BED7-006008317CE8}   StringSet(0x101b, date) / DWORDGet
  text format    {E4963D40-C743-11CD-80E5-00AA003E4B50}

Registry layout (as written by the original DllRegisterServer):
  HKCR\\CLSID\\{A162A680-...} = "ScanSoft RealSpeak v2 Standard"
      \\Programmable
      \\InprocServer32 = <path>, ThreadingModel = Apartment
  HKLM\\Software\\Voice\\TextToSpeech\\Engine
      "ScanSoft RealSpeak v2 Standard" = "{A162A680-...}"
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

CLSID_ENGINE = "{A162A680-7689-4B2E-8A9B-6806F141EFC5}"
IID_ENUM = "{6B837B20-4A47-101B-931A-00AA0047BA4F}"
IID_CENTRAL = "{1287A280-4A47-101B-931A-00AA0047BA4F}"
IID_NOTIFY_SINK = "{C0FA8F40-4A46-101B-931A-00AA0047BA4F}"
IID_ATTRIBUTES = "{1D59DED1-E367-11D1-BED7-006008317CE8}"
GUID_TEXT_FORMAT = "{E4963D40-C743-11CD-80E5-00AA003E4B50}"

ENGINE_NAME = "ScanSoft RealSpeak v2 Standard"
MODE_LABEL = "RealSpeak(SAPI4) Text to Speech Mode"
NOTIFY_CLASS = "RealSpeak_SAPI4_NotifyClass"
S_FALSE = 1
TTSERR_INVALIDKEY = 0x80040206

MODE_NAMES = {
    "enu": "American English Jennifer",
    "eng": "British English Jane",
    "frf": "French Sophie",
    "iti": "Italian Bianca",
    "ged": "German Vera",
    "spe": "Spanish Isabel",
    "jpj": "Japanese Tomoko",
    "dun": "Dutch Laura",
}
UK_LEGACY_MODE = "RealSpeak (Jane) UK English (v2) Female #2"
LEGACY_ENGINE = "L&H Speech RealSpeak"


@dataclass
class Mode:
    """One enumerator entry: GUID + name (the original uses a 16-byte id + 262 wchar name)."""

    guid: str
    name: str
    langcode: str = ""

    def entry(self) -> Tuple[str, str]:
        return (self.guid, self.name)


@dataclass
class NotifySink:
    """Receives SpeakingStarted/SpeakingDone equivalent callbacks."""

    on_started: Optional[Callable[[], None]] = None
    on_done: Optional[Callable[[], None]] = None

    def speaking_started(self) -> None:
        if self.on_started:
            self.on_started()

    def speaking_done(self) -> None:
        if self.on_done:
            self.on_done()


class Central:
    """`ITTSCentral` equivalent."""

    def __init__(self, mode: Mode, synth_engine=None):
        self.mode = mode
        self.synth_engine = synth_engine
        self.attributes: Dict[int, object] = {}
        self._keys: Dict[int, NotifySink] = {}
        self._next_key = 1
        self.volume_saved: Optional[int] = None

    # -- ITTSCentral ----------------------------------------------------
    def Register(self, sink: NotifySink, iid: str = IID_NOTIFY_SINK, *args) -> Tuple[int, int]:
        key = self._next_key
        self._next_key += 1
        self._keys[key] = sink
        return 0, key

    def UnRegister(self, key: int) -> int:
        if key not in self._keys:
            return TTSERR_INVALIDKEY
        del self._keys[key]
        return 0

    def TextData(self, flags: int, attributes: int, text: str, cb: int,
                 stream_desc=None, format_guid: str = GUID_TEXT_FORMAT) -> int:
        for sink in list(self._keys.values()):
            sink.speaking_started()
        try:
            if self.synth_engine is not None:
                self.synth_engine(text)
        finally:
            for sink in list(self._keys.values()):
                sink.speaking_done()
        return 0

    def AudioReset(self) -> int:
        return 0

    # -- IAttributes ----------------------------------------------------
    def StringSet(self, attr_id: int, value: str) -> int:
        self.attributes[attr_id] = value
        return 0

    def StringGet(self, attr_id: int):
        return self.attributes.get(attr_id)

    def DWORDGet(self, attr_id: int):
        return self.attributes.get(attr_id)

    def DWORDSet(self, attr_id: int, value: int) -> int:
        self.attributes[attr_id] = int(value)
        return 0

    def PitchGet(self):
        return self.attributes.get(0x1001)

    def PitchSet(self, value) -> int:
        self.attributes[0x1001] = value
        return 0

    def SpeedGet(self):
        return self.attributes.get(0x1002)

    def SpeedSet(self, value) -> int:
        self.attributes[0x1002] = value
        return 0

    def VolumeGet(self):
        return self.attributes.get(0x1003)

    def VolumeSet(self, value) -> int:
        self.attributes[0x1003] = value
        return 0


class EngineEnum:
    """`ITTSEnum` equivalent: mode enumeration and selection."""

    def __init__(self, modes: List[Mode]):
        self._modes = list(modes)
        self._pos = 0

    def Next(self, celt: int = 1):
        out = []
        for _ in range(celt):
            if self._pos >= len(self._modes):
                break
            out.append(self._modes[self._pos])
            self._pos += 1
        return (S_FALSE if not out else 0), out

    def Reset(self) -> int:
        self._pos = 0
        return 0

    def Select(self, mode: Mode, synth_engine=None) -> Central:
        return Central(mode, synth_engine)


class Engine:
    """The module as the product's client sees it (CLSID_ENGINE)."""

    def __init__(self, voice_bank=None, speak: Optional[Callable[[str], None]] = None):
        self.bank = voice_bank
        self.speak_callback = speak
        self.central: Optional[Central] = None

    # -- discovery -------------------------------------------------------
    def modes(self) -> List[Mode]:
        out: List[Mode] = []
        if self.bank is not None:
            for key, v in self.bank.voices.items():
                code = (v.langcode or key).lower()
                out.append(Mode(guid=f"{{00000000-0000-0000-0000-{code.encode().hex():>012}}}",
                                name=MODE_NAMES.get(code, v.voice or key), langcode=code))
        if not out:
            out = [Mode(guid="{00000000-0000-0000-0000-656e75000000}",
                        name=MODE_NAMES["enu"], langcode="enu")]
        return out

    def create_enumerator(self) -> EngineEnum:
        return EngineEnum(self.modes())

    # -- client protocol --------------------------------------------------
    def create_and_select(self, wanted_name: str = "") -> Tuple[int, Optional[Central]]:
        """Replicates ttssapi.cpp: enumerate, choose by name, fall back to US English."""
        enum = self.create_enumerator()
        rc, entries = enum.Next(len(self.modes()) or 1)
        if not entries:
            return S_FALSE, None
        chosen = None
        for m in entries:
            if wanted_name and m.name.lower() == wanted_name.lower():
                chosen = m
                break
        if chosen is None:
            for m in entries:
                if m.langcode == "enu":
                    chosen = m
                    break
        if chosen is None:
            return S_FALSE, None
        self.central = enum.Select(chosen, synth_engine=self.speak_callback)
        return 0, self.central

    def speak(self, text: str, wanted_name: str = "") -> int:
        rc, central = self.create_and_select(wanted_name)
        if central is None:
            return rc
        sink = NotifySink()
        _, key = central.Register(sink)
        central.StringSet(0x101B, time.strftime("%m/%d/%Y"))
        rc = central.TextData(0, 0, text, len(text.encode("utf-16-le")), None, GUID_TEXT_FORMAT)
        central.UnRegister(key)
        return rc


# ---------------------------------------------------------------------------
def registry_script(dll_path: str = r"C:\Program Files\Nuance\NaturallySpeaking16\Program\rsstdv2.dll") -> str:
    """The registration the original module performs (Windows Registry Editor 5.00 format)."""
    return (
        "Windows Registry Editor Version 5.00\r\n\r\n"
        f"[HKEY_CLASSES_ROOT\\CLSID\\{CLSID_ENGINE}]\r\n"
        f"@=\"{ENGINE_NAME}\"\r\n\r\n"
        f"[HKEY_CLASSES_ROOT\\CLSID\\{CLSID_ENGINE}\\Programmable]\r\n\r\n"
        f"[HKEY_CLASSES_ROOT\\CLSID\\{CLSID_ENGINE}\\InprocServer32]\r\n"
        f"@=\"{dll_path}\"\r\n"
        "\"ThreadingModel\"=\"Apartment\"\r\n\r\n"
        "[HKEY_LOCAL_MACHINE\\Software\\Voice\\TextToSpeech\\Engine]\r\n"
        f"\"{ENGINE_NAME}\"=\"{CLSID_ENGINE}\"\r\n"
    )


def unregister_script() -> str:
    return (
        "Windows Registry Editor Version 5.00\r\n\r\n"
        f"[-HKEY_CLASSES_ROOT\\CLSID\\{CLSID_ENGINE}]\r\n\r\n"
        "[HKEY_LOCAL_MACHINE\\Software\\Voice\\TextToSpeech\\Engine]\r\n"
        f"\"{ENGINE_NAME}\"=-\r\n"
    )
