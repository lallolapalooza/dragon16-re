"""Voice bank model: reads a `Voices\\speech` tree (or any directory holding voice assets).

Recognised files
----------------
pp.dat                              prompt/phone data           (root 'PP\\x00\\x00LIST')
synth_<voice>_<model>.dat           voice font                  (root 'VFNTLIST')
uselect_<voice>_full.dat            unit-selection database     (root 'USELLIST')
vauto_pipeline_*.hdr                pipeline manifests          (schema B)
clc_*.dat                           front-end lexicon/config    (other container)
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import datfile, hdr


@dataclass
class VoiceInfo:
    language: str = ""
    langcode: str = ""
    voice: str = ""
    frequency: int = 22050
    coder: str = ""
    bitrate: str = ""
    model: str = ""
    overhead_frames: str = ""
    vinf: Dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.voice.title() if self.voice else self.langcode.upper()


@dataclass
class VoiceBank:
    directory: str
    voices: Dict[str, VoiceInfo] = field(default_factory=dict)
    manifests: List[hdr.PipelineManifest] = field(default_factory=list)
    files: Dict[str, str] = field(default_factory=dict)
    pp: Optional[datfile.DatFile] = None
    chunk_inventory: Dict[str, List] = field(default_factory=dict)

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, directory: str, patterns: bool = True) -> "VoiceBank":
        bank = cls(directory=directory)
        for path in sorted(glob.glob(os.path.join(directory, "**", "*"), recursive=True)):
            if not os.path.isfile(path):
                continue
            bank.files[os.path.basename(path)] = path
        for name, path in bank.files.items():
            low = name.lower()
            try:
                if low.endswith(".hdr"):
                    obj = hdr.load(path)
                    if isinstance(obj, hdr.PipelineManifest):
                        bank.manifests.append(obj)
                        v = bank.voices.setdefault((obj.voice or name).lower(),
                                                   VoiceInfo(voice=obj.voice))
                        # manifest metadata fills gaps left by the packed assets
                        v.language = v.language or obj.language
                        v.langcode = v.langcode or obj.langcode
                        v.frequency = obj.frequency or v.frequency
                        v.coder = v.coder or obj.coder
                        v.bitrate = v.bitrate or obj.bitrate
                        v.model = obj.model or v.model
                        v.vinf.update(dict(obj.parameters))
                elif low == "pp.dat":
                    bank.pp = datfile.DatFile(path)
                elif low.startswith("synth_") and low.endswith(".dat"):
                    df = datfile.DatFile(path)
                    bank.chunk_inventory[name] = df.inventory()
                    kv = dict(df.keys("VINF"))
                    if "Voice" not in kv:
                        continue          # packed asset without voice metadata
                    key = kv["Voice"].lower()
                    v = bank.voices.setdefault(key, VoiceInfo(voice=kv["Voice"]))
                    v.vinf.update(kv)
                    v.language = v.language or kv.get("Voice", "")
                    v.langcode = (v.langcode or kv.get("Lng", "")).lower()
                    v.frequency = int(kv.get("Freq", v.frequency))
                    v.coder = v.coder or kv.get("Coder", "")
                    v.bitrate = v.bitrate or kv.get("BitRate", "")
                    v.model = v.model or kv.get("Reduction", "") + "_" + v.coder
                    v.overhead_frames = v.overhead_frames or kv.get("OverheadFrames", "")
                elif low.startswith("uselect_") and low.endswith(".dat"):
                    df = datfile.DatFile(path)
                    bank.chunk_inventory[name] = df.inventory()
                    kv = dict(df.keys("VINF"))
                    if "Voice" not in kv:
                        continue          # packed asset without voice metadata
                    key = kv["Voice"].lower()
                    v = bank.voices.setdefault(key, VoiceInfo(voice=kv["Voice"]))
                    v.vinf.update({f"uselect.{k}": val for k, val in kv.items()})
                    v.langcode = (v.langcode or kv.get("Lng", "")).lower()
            except Exception:
                # unknown / different container (e.g. clc_*_cfg3.dat) — record and continue
                continue
        return bank

    # ------------------------------------------------------------------
    @property
    def frequency(self) -> int:
        for v in self.voices.values():
            if v.frequency:
                return v.frequency
        return 22050

    def voice(self, name: str = "") -> VoiceInfo:
        if not self.voices:
            raise KeyError(f"no voices found in {self.directory}")
        if not name:
            return next(iter(self.voices.values()))
        key = name.lower()
        for k, v in self.voices.items():
            if k == key or v.voice.lower() == key or v.langcode.lower() == key:
                return v
        raise KeyError(f"voice {name!r} not found; have {sorted(self.voices)}")

    def manifest_for(self, voice: str = "") -> Optional[hdr.PipelineManifest]:
        if not self.manifests:
            return None
        if not voice:
            return self.manifests[0]
        key = voice.lower()
        for m in self.manifests:
            if m.voice.lower() == key or m.langcode.lower() == key or m.model.lower() == key:
                return m
        return self.manifests[0]

    def report(self) -> str:
        lines = [f"bank: {self.directory}"]
        for name, v in self.voices.items():
            lines.append(f"  voice={v.voice or '?'} lang={v.langcode or '?'} "
                         f"freq={v.frequency} coder={v.coder or '?'} bitrate={v.bitrate or '?'} "
                         f"model={v.model or '?'}")
        for name, inv in self.chunk_inventory.items():
            lines.append(f"  {name}: " + ", ".join(f"{t}{'' if s is None else ':' + str(s)}"
                                                   for t, s in inv))
        return "\n".join(lines)
