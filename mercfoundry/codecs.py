"""Readable forms of record types, keyed by the record's type hash.

decode(kind, record, textures=False) -> (suffix, bytes) or None
encode(kind, decoded, base) -> record bytes, built on the original record

Textures are opt-in because decoding thousands of them takes minutes.
"""
import json

from . import png, registry, script, texture, world
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


def _ai(kind):
    from . import aidata
    return (f".{kind}.json",
            lambda rec: _json(aidata.to_json(rec, kind)),
            lambda dec, base: aidata.from_json(json.loads(dec)))


CODECS = {
    name_hash("script"): (".lua", _script_decode, _script_encode),
    name_hash("world"): (".world.json",
                         lambda rec: _json(world.to_json(rec)),
                         lambda dec, base: world.from_json(json.loads(dec))),
    name_hash("registry"): (".text.json",
                            lambda rec: _json(registry.to_json(rec)),
                            lambda dec, base: registry.from_json(json.loads(dec), base)),
    name_hash("path"): _ai("path"),
    name_hash("lineregion"): _ai("lineregion"),
    name_hash("region"): _ai("region"),
    name_hash("encounter"): _ai("encounter"),
    name_hash("list"): (".list.txt", lambda rec: rec, lambda dec, base: dec),
    name_hash("animtable"): (".animtable.json",
                             lambda rec: _json(_animtable().to_json(rec)),
                             lambda dec, base: _animtable().from_json(json.loads(dec))),
}


def _animtable():
    from . import animtable
    return animtable


def _texture_encode(decoded, base):
    width, height, rgba = png.decode(decoded)
    return texture.encode(texture.replace_image(texture.decode(base), width, height, rgba))


TEXTURES = {
    texture.TEXTURE: (".png", lambda rec: texture.decode(rec).to_png(), _texture_encode),
}


def decode(kind: int, record: bytes, textures: bool = False):
    codec = CODECS.get(kind) or (TEXTURES.get(kind) if textures else None)
    if not codec:
        return None
    suffix, dec, _ = codec
    return suffix, dec(record)


def encode(kind: int, decoded: bytes, base: bytes, check_unchanged: bool = True) -> bytes:
    """Return the record for an edited decoded file; the base if unchanged."""
    suffix, dec, enc = CODECS.get(kind) or TEXTURES[kind]
    if check_unchanged and dec(base) == decoded:
        return base
    return enc(decoded, base)
