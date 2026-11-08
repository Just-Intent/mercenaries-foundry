"""Check that our tools rebuild real game files exactly.

For each archive: the directory and data rebuild byte-for-byte, then every
record we can decode goes through decode -> encode and must come back
identical. Compressed scripts can't match byte-for-byte (our compressor
differs from the original), so for those we check that the text survives a
compress/decompress cycle.
"""
import collections
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

from . import dsk, registry, script, world
from .hashing import name_hash

SCRIPT, WORLD, REGISTRY = name_hash("script"), name_hash("world"), name_hash("registry")
ERRORS = (ValueError, KeyError, UnicodeError, struct.error, IndexError)


@dataclass
class Report:
    archive: str
    records: int = 0
    layout_ok: bool = False
    checked: collections.Counter = field(default_factory=collections.Counter)
    failures: list = field(default_factory=list)  # (index, type, message)

    @property
    def ok(self):
        return self.layout_ok and not self.failures


def _rebuild_layout(arc: dsk.Archive) -> bool:
    """Regenerate the header and directory and compare with the file.

    Record data and the tail are copied verbatim by pack(), so these bytes
    are the only part of the layout we actually rebuild.
    """
    head = dsk.HEADER.pack(arc.count, arc.extra) + b"".join(
        dsk.ENTRY.pack(r.size, r.key, r.kind) for r in arc.records)
    with arc.path.open("rb") as f:
        return f.read(len(head)) == head


def _check_record(kind, payload):
    """Return (label, error or None), or None if we don't decode this type."""
    from . import aidata
    if kind in aidata.KINDS:
        name = aidata.KINDS[kind]
        doc = json.loads(json.dumps(aidata.to_json(payload, name)))
        return name, None if aidata.from_json(doc) == payload else "rebuilt bytes differ"
    if kind == WORLD:
        doc = json.loads(json.dumps(world.to_json(payload)))
        return "world", None if world.from_json(doc) == payload else "rebuilt bytes differ"
    if kind == REGISTRY:
        if registry.Registry(payload).to_bytes() != payload:
            return "text", "rebuilt bytes differ"
        doc = json.loads(json.dumps(registry.to_json(payload)))
        if not doc["strings"]:
            return "text", "no text values decoded"
        return "text", None if registry.from_json(doc, payload) == payload else "rebuilt from text differs"
    if kind == SCRIPT:
        s = script.decode(payload)
        if not s.compressed:
            return "script", None if script.encode(s) == payload else "rebuilt bytes differ"
        if script.decompress(script.compress(s.text), len(s.text)) != s.text:
            return "script", "recompressed text differs"
        # Same fields apart from BODY: rebuild with the original body.
        again = script.decode(script.encode(s))
        return "script", None if (again.name, again.info, again.text) == (s.name, s.info, s.text) else "fields differ"
    return None


def verify_archive(path, max_failures=50) -> Report:
    arc = dsk.Archive(path)
    rep = Report(arc.path.name, arc.count)
    rep.layout_ok = _rebuild_layout(arc)
    with arc.path.open("rb") as f:
        for r in arc.records:
            f.seek(r.offset)
            payload = f.read(r.size)
            try:
                result = _check_record(r.kind, payload)
            except ERRORS as err:
                from . import aidata
                labels = {SCRIPT: "script", WORLD: "world", REGISTRY: "text", **aidata.KINDS}
                result = (labels[r.kind], f"{type(err).__name__}: {err}")
            if result is None:
                continue
            label, error = result
            rep.checked[label] += 1
            if error and len(rep.failures) < max_failures:
                rep.failures.append((r.index, label, error))
            elif error:
                rep.checked["unreported failures"] += 1
    return rep


def find_archives(target) -> list:
    p = Path(target)
    if p.is_dir():
        return sorted(p.rglob("*.dsk"))
    return [p]
