"""`.hdr` descriptor parsing: LHS component registry and NUANCE pipeline manifests."""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional


def _read_text(path: str) -> str:
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace")


@dataclass
class LHSEntry:
    """Schema A: one component declaration (`rsstdv2_core.hdr`, `rsstdv2_dec11.hdr`, ...)."""

    path: str
    version_file: str = ""
    comptype: str = ""
    version: str = ""
    name: str = ""
    location: str = ""
    ttstype: str = ""
    brokerstring: str = ""
    engine: str = ""
    dll: str = ""
    getinterface: str = ""

    @property
    def directory(self) -> str:
        return os.path.dirname(os.path.abspath(self.path))

    @property
    def dll_path(self) -> str:
        return os.path.join(self.directory, self.dll) if self.dll else ""


def parse_lhs(path: str) -> LHSEntry:
    root = ET.fromstring(_read_text(path))
    e = LHSEntry(path=path)
    e.version_file = (root.findtext("VERSION") or "").strip()
    h = root.find("HEADER")
    if h is None:
        raise ValueError(f"{path}: missing <HEADER>")
    for tag, attr in (("COMPTYPE", "comptype"), ("VERSION", "version"), ("NAME", "name"),
                      ("LOCATION", "location"), ("TTSTYPE", "ttstype"),
                      ("BROKERSTRING", "brokerstring"), ("ENGINE", "engine"),
                      ("DLL", "dll"), ("GETINTERFACE", "getinterface")):
        setattr(e, attr, (h.findtext(tag) or "").strip())
    return e


@dataclass
class PipelineManifest:
    """Schema B: a voice/model pipeline manifest (`vauto_pipeline_*.hdr`)."""

    path: str
    version: str = ""
    brokerstring: str = ""
    priority: int = 0
    parameters: Dict[str, str] = field(default_factory=dict)
    objects: Dict[str, str] = field(default_factory=dict)
    components: List[str] = field(default_factory=list)
    resources: List[Dict[str, str]] = field(default_factory=list)

    # convenience accessors used by the pipeline
    @property
    def language(self) -> str:
        return self.parameters.get("language", "")

    @property
    def langcode(self) -> str:
        return self.parameters.get("langcode", "")

    @property
    def voice(self) -> str:
        return self.parameters.get("voice", "")

    @property
    def frequency(self) -> int:
        return int(self.parameters.get("frequencyhz", "22050"))

    @property
    def sample_bits(self) -> int:
        return int(self.parameters.get("audiooutputsamplebits", "16"))

    @property
    def coder(self) -> str:
        return self.parameters.get("coder", "")

    @property
    def bitrate(self) -> str:
        return self.parameters.get("bitrate", "")

    @property
    def model(self) -> str:
        return self.parameters.get("voicemodel", "")


def parse_pipeline(path: str) -> PipelineManifest:
    root = ET.fromstring(_read_text(path))
    m = PipelineManifest(path=path)
    m.version = (root.findtext("VERSION") or "").strip()
    h = root.find("HEADER")
    if h is None:
        raise ValueError(f"{path}: missing <HEADER>")
    m.brokerstring = (h.findtext("BROKERSTRING") or "").strip()
    m.priority = int((h.findtext("PRIORITY") or "0").strip() or 0)
    for p in h.findall("./PARAMETERS/*"):
        m.parameters[p.tag.strip()] = (p.text or "").strip()
    for o in h.findall("./OBJECTS/*"):
        m.objects[o.tag.strip()] = (o.text or "").strip()
    for c in h.findall("./COMPONENTS/COMPONENT"):
        m.components.append((c.text or "").strip())
    for r in h.findall("./RESOURCES/RESOURCE"):
        d = dict(r.attrib)
        d["value"] = (r.text or "").strip()
        m.resources.append(d)
    return m


_ROOT_RE = re.compile(r"<\s*([A-Za-z_][\w.-]*)")


def load(path: str):
    """Parse either descriptor schema based on its root element."""
    text = _read_text(path)
    body = re.sub(r"<\?.*?\?>", "", text, flags=re.S)      # drop <?xml ...?> prologues
    m = _ROOT_RE.search(body)
    root_tag = m.group(1) if m else ""
    if root_tag.upper() == "NUANCE":
        return parse_pipeline(path)
    return parse_lhs(path)
