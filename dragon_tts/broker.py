"""Broker (`brk_*`) semantics: component discovery and broker-string resolution.

The original broker (`lhcom02W.dll`) discovers components by walking directories for `*.hdr`
and `*.dat` files.  Search roots come from environment variables (LH_MODULES, LH_SUBMODULES,
LH_INPATH, LH_OUTPATH) with the caller supplying a base directory otherwise.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from . import hdr

ENV_ROOTS = ("LH_MODULES", "LH_SUBMODULES", "LH_INPATH", "LH_OUTPATH")
ENV_TRACE = ("LH_TRACELEVEL", "LH_TRACEINFO", "LH_LOGFILE")


@dataclass
class ComponentRef:
    brokerstring: str
    engine: str
    dll: str
    entry: str
    comptype: str
    name: str
    descriptor: str


class Broker:
    """Resolves broker strings to (DLL, entry point) and loads `.hdr` manifests."""

    def __init__(self, roots: Optional[Iterable[str]] = None, env: Optional[Dict[str, str]] = None):
        self.env = dict(os.environ if env is None else env)
        self.roots: List[str] = list(roots or [])
        for var in ENV_ROOTS:
            val = self.env.get(var)
            if val:
                self.roots.extend(p for p in val.split(os.pathsep) if p)
        self.components: Dict[str, ComponentRef] = {}     # brokerstring -> ref
        self.engines: Dict[str, List[ComponentRef]] = {}  # engine -> components
        self.manifests: List[hdr.PipelineManifest] = []
        self.descriptors: List[hdr.LHSEntry] = []
        self._objects: Dict[str, object] = {}             # loaded "module" objects

    # -- discovery ------------------------------------------------------
    def scan(self, patterns: Iterable[str] = ("*.hdr",)) -> int:
        seen = 0
        for root in self.roots:
            if not os.path.isdir(root):
                continue
            for dirpath, _dirs, files in os.walk(root):
                for fn in files:
                    if any(fnmatch.fnmatch(fn.lower(), p) for p in patterns):
                        path = os.path.join(dirpath, fn)
                        try:
                            obj = hdr.load(path)
                        except Exception:
                            continue
                        seen += 1
                        if isinstance(obj, hdr.PipelineManifest):
                            self.manifests.append(obj)
                            continue
                        self.descriptors.append(obj)
                        if obj.brokerstring:
                            ref = ComponentRef(obj.brokerstring, obj.engine, obj.dll,
                                               obj.getinterface, obj.comptype, obj.name, path)
                            self.components.setdefault(obj.brokerstring, ref)
                            self.engines.setdefault(obj.engine, []).append(ref)
        return seen

    # -- brk_* surface --------------------------------------------------
    def obj_open(self, engine: str) -> str:
        """`brk_ObjOpenEx(engine)` — validate an engine group exists."""
        if engine not in self.engines:
            raise KeyError(f"NO ENGINE FOUND WITH SPECIFIED BROKER STRING: {engine!r}")
        obj = f"obj:{engine}"
        self._objects.setdefault(obj, engine)
        return obj

    def obj_close(self, obj: str) -> None:
        self._objects.pop(obj, None)

    def broker_string_query(self, key: str, language: str = "") -> str:
        """`brk_BrokerStringQuery` — resolve a well-known key, optionally language-qualified."""
        key = key.strip()
        if key == "TTSENGINE":
            # pick an engine that declares the CORE ttscore interface
            for engine, comps in self.engines.items():
                if any(c.brokerstring == "ttscore" for c in comps):
                    return engine
            return next(iter(self.engines), "")
        if key in self.components:
            return self.components[key].brokerstring
        # pipeline/<Language>/<voice>/... manifests
        for m in self.manifests:
            if m.brokerstring == key:
                return m.brokerstring
        if language:
            for m in self.manifests:
                if language.lower() in m.brokerstring.lower():
                    return m.brokerstring
        # fall back to a stem match
        for m in self.manifests:
            if key.lower() in m.brokerstring.lower():
                return m.brokerstring
        return ""

    def interface_query(self, obj: str, brokerstring: str, iid: int = 1):
        """`brk_InterfaceQuery(obj, name, ...)` — return the component reference."""
        if obj not in self._objects:
            raise KeyError(f"invalid object handle {obj!r}")
        ref = self.components.get(brokerstring)
        if ref is None:
            raise KeyError(f"interface {brokerstring!r} not registered")
        return ref

    def interface_release(self, ref) -> None:  # symmetry with the original API
        return None

    def info_query(self, obj: str, brokerstring: str):
        ref = self.components.get(brokerstring)
        if ref is None:
            raise KeyError(brokerstring)
        return ref.dll_path

    def component_list(self, engine: str) -> List[ComponentRef]:
        return list(self.engines.get(engine, []))

    def find_pipeline(self, langcode: str = "", voice: str = "") -> Optional[hdr.PipelineManifest]:
        for m in self.manifests:
            if langcode and m.langcode.lower() != langcode.lower():
                continue
            if voice and m.voice.lower() != voice.lower():
                continue
            return m
        return None

    def __repr__(self) -> str:
        return (f"<Broker roots={len(self.roots)} descriptors={len(self.descriptors)} "
                f"manifests={len(self.manifests)} brokerstrings={sorted(self.components)}>")
