"""Readable forms of record types, keyed by the record's type hash.

decode(kind, record) -> (suffix, bytes) or None
encode(kind, decoded, base) -> record bytes, built on the original record
"""
import json

from . import registry, script, world
from .hashing import name_hash


def _json(doc) -> bytes:
    return (json.dumps(doc, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def _script_decode(record):
    return script.decode(record).source.encode("latin-1")


def _script_encode(decoded, base):
    old = script.decode(base)
    if b"\r\n" not in old.text:  # editors on Windows may add CRs; keep LF files LF
        decoded = decoded.replace(b"\r\n", b"\n")
    return script.encode(script.with_source(old, decoded.decode("latin-1")))


CODECS = {
    name_hash("script"): (".lua", _script_decode, _script_encode),
    name_hash("world"): (".world.json",
                         lambda rec: _json(world.to_json(rec)),
                         lambda dec, base: world.from_json(json.loads(dec))),
    name_hash("registry"): (".text.json",
                            lambda rec: _json(registry.to_json(rec)),
                            lambda dec, base: registry.from_json(json.loads(dec), base)),
}


def decode(kind: int, record: bytes):
    codec = CODECS.get(kind)
    if not codec:
        return None
    suffix, dec, _ = codec
    return suffix, dec(record)


def encode(kind: int, decoded: bytes, base: bytes) -> bytes:
    """Return the record for an edited decoded file; the base if unchanged."""
    suffix, dec, enc = CODECS[kind]
    if dec(base) == decoded:
        return base
    return enc(decoded, base)
