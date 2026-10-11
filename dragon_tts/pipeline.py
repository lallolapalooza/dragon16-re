"""Pipeline component graph, named after the manifest `COMPONENTS` entries.

Every stage of `vauto_pipeline_*.hdr` has a class here so that a manifest can be validated and
instantiated: unknown components degrade to pass-through, and the audio chain
(`uselect` -> `synth` -> `xcoder` -> `audioinserter`) produces `audio/L16;rate=<fs>` PCM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import phonemes, synth as synth_mod
from .hdr import PipelineManifest
from .voicebank import VoiceBank, VoiceInfo


class Component:
    name = "component"

    def __init__(self, pipeline: "Pipeline"):
        self.pipeline = pipeline

    def process(self, data, context: Dict):
        return data


class PassThrough(Component):
    name = "pass-through"


# -- front-end stages -------------------------------------------------------
class FeLid(Component):
    """Language identification (single-language build: echoes the configured language)."""
    name = "fe/fe_lid"

    def process(self, text: str, ctx: Dict):
        ctx.setdefault("language", self.pipeline.language)
        ctx.setdefault("langcode", self.pipeline.langcode)
        return text


class FeUdwl(Component):
    """User dictionary (identity unless a dictionary is supplied)."""
    name = "fe/fe_udwl"

    def process(self, text: str, ctx: Dict):
        for src, dst in self.pipeline.user_dictionary.items():
            text = text.replace(src, dst)
        return text


class TokenTN(Component):
    """Tokeniser + text normalisation (numbers, abbreviations, symbols)."""
    name = "fe/tokentn"

    def process(self, text: str, ctx: Dict):
        text = phonemes.normalise(text)
        ctx["normalised"] = text
        return text


class FePhrasing(Component):
    """Produce phrase/terminator structure."""
    name = "fe/fe_phrasing"

    def process(self, text: str, ctx: Dict):
        words = text.split()
        phrases = phonemes.split_phrases(text)
        ctx["phrases"] = phrases
        ctx["words"] = len(words)
        return text


class Phonmap(Component):
    """Grapheme/lexicon -> phoneme sequence with stress and boundary markers."""
    name = "phonmap"

    def process(self, text: str, ctx: Dict):
        phones = phonemes.text_to_phones(text)
        ctx["phones"] = phones
        return phones


# -- back-end stages --------------------------------------------------------
class Uselect(Component):
    """Unit selection; the substitute synthesiser needs only the phone list and prosody."""
    name = "uselect"

    def process(self, phones, ctx: Dict):
        ctx["selected_units"] = [p for p, _ in phones if p and p != "."]
        return phones


class Synth(Component):
    """Waveform generation (pluggable, see dragon_tts.synth)."""
    name = "synth"

    def process(self, phones, ctx: Dict):
        req = synth_mod.RenderRequest(
            phones=phones,
            fs=self.pipeline.frequency,
            speed=ctx.get("speed", 1.0),
            pitch=ctx.get("pitch", 1.0),
            volume=ctx.get("volume", self.pipeline.volume),
        )
        pcm = self.pipeline.synthesiser.render(req)
        ctx["pcm"] = pcm
        return pcm


class Xcoder(Component):
    """Coder stage. The reference build is PCM-native, so this is an accounting hook."""
    name = "xcoder"

    def process(self, pcm: bytes, ctx: Dict):
        ctx["coded"] = False
        return pcm


class AudioInserter(Component):
    """Adds the leading/trailing silence and guarantees whole frames."""
    name = "audioinserter"

    def process(self, pcm: bytes, ctx: Dict):
        fs = self.pipeline.frequency
        pad = b"\x00\x00" * int(0.03 * fs)
        return pad + pcm + pad


# -- pipeline ---------------------------------------------------------------
FRONT_END_ORDER = ["fe/fe_lid", "fe/fe_udwl", "pp/text", "fe/fe_clcml",
                   "fe/fe_promptoriorth", "fe/fe_unixlit", "fe/fe_initlingdb",
                   "fe/fe_promptorth", "fe/tokentn", "fe/fe_abbrtn", "fe/fe_puncsptn",
                   "fe/fe_oneword", "fe/fe_pos", "fe/fe_hmogrph", "fe/fe_phrasing",
                   "fe/fe_normout"]
BACK_END_ORDER = ["phonmap", "phonmap/mrk", "uselect/bet1", "uselect", "synth",
                  "xcoder", "xcoder/mrksync", "audioinserter"]


@dataclass
class Pipeline:
    """Instantiates and runs the component graph for one voice."""

    frequency: int = 22050
    language: str = "American English"
    langcode: str = "ENU"
    voice: str = ""
    manifest: Optional[PipelineManifest] = None
    synthesiser: synth_mod.Synthesiser = field(default_factory=synth_mod.FormantSynthesiser)
    user_dictionary: Dict[str, str] = field(default_factory=dict)
    volume: float = 0.8
    speed: float = 1.0
    pitch: float = 1.0
    components: Dict[str, Component] = field(default_factory=dict)
    missing: List[str] = field(default_factory=list)

    @classmethod
    def for_voice(cls, bank: Optional[VoiceBank] = None, voice: str = "",
                  synthesiser: Optional[synth_mod.Synthesiser] = None) -> "Pipeline":
        info = VoiceInfo()
        manifest = None
        if bank is not None:
            info = bank.voice(voice)
            manifest = bank.manifest_for(voice or info.voice)
        p = cls(
            frequency=info.frequency or 22050,
            language=info.language or "American English",
            langcode=(info.langcode or "enu").upper(),
            voice=info.voice,
            manifest=manifest,
            synthesiser=synthesiser or synth_mod.FormantSynthesiser(),
        )
        p.build()
        return p

    def build(self) -> None:
        """Instantiate the component graph; validate against the manifest when present."""
        n = 0

        def add(key: str, factory):
            nonlocal n
            self.components[key] = factory(self)
            n += 1

        add("fe/fe_lid", FeLid)
        add("fe/fe_udwl", FeUdwl)
        add("fe/tokentn", TokenTN)
        add("fe/fe_phrasing", FePhrasing)
        add("phonmap", Phonmap)
        add("uselect", Uselect)
        add("synth", Synth)
        add("xcoder", Xcoder)
        add("audioinserter", AudioInserter)

        if self.manifest:
            for name in self.manifest.components:
                if name in self.components:
                    continue
                if name in ("ttt/re", "pp/text") or name.startswith("fe/") or name in (
                        "phonmap/mrk", "xcoder/mrksync"):
                    self.components[name] = PassThrough(self)
                else:
                    self.components[name] = PassThrough(self)
                    self.missing.append(name)

    # -- execution -------------------------------------------------------
    def synth_phones(self, phones: Sequence[Tuple[str, str]], context: Optional[Dict] = None) -> bytes:
        context = dict(context or {})
        data = phones
        for stage in ("uselect", "synth", "xcoder", "audioinserter"):
            data = self.components[stage].process(data, context)
        return data

    def synthesize(self, text: str, return_wav: bool = True) -> bytes:
        context: Dict = {"speed": self.speed, "pitch": self.pitch, "volume": self.volume}
        data = text
        for key in ("fe/fe_lid", "fe/fe_udwl", "fe/tokentn", "fe/fe_phrasing", "phonmap"):
            data = self.components[key].process(data, context)
        pcm = self.synth_phones(data, context)
        return synth_mod.pcm_to_wav(pcm, self.frequency) if return_wav else pcm
