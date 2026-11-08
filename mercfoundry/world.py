"""World layer records (type "world"): ucfb -> wrld.

wrld holds NAME (the layer name, whose hash is the record key), TABL (a string
table) and repeated inst chunks. Each inst has a PROP chunk of u32 string-table
hashes: model, template, then (property, value) pairs. Observed in retail
assets.dsk by Mercenaries Recompiled (inspect_retail_templates.py).

to_json/from_json give an editable form that rebuilds the record exactly when
nothing is changed. Unknown chunks are kept as hex.
"""
import struct

from . import ucfb
from .hashing import name_hash

FORMAT = "mercfoundry-world-1"


def read_table(data: bytes) -> list:
    if len(data) < 4:
        raise ValueError("string table is truncated")
    count = struct.unpack_from("<I", data)[0]
    if count > (len(data) - 4) // 9:
        raise ValueError("string table count is too large")
    pos, out = 4, []
    for _ in range(count):
        if pos + 8 > len(data):
            raise ValueError("string table entry is truncated")
        key, size = struct.unpack_from("<II", data, pos)
        pos += 8
        if pos + size >= len(data) or data[pos + size] != 0:
            raise ValueError("string table entry is not NUL-terminated")
        text = data[pos:pos + size].decode("ascii")
        pos += size + 1
        if name_hash(text) != key:
            raise ValueError(f"string table hash mismatch for {text!r}")
        out.append(text)
    if pos != len(data):
        raise ValueError("string table has trailing data")
    return out


def write_table(strings) -> bytes:
    out = bytearray(struct.pack("<I", len(strings)))
    for text in strings:
        raw = text.encode("ascii")
        out += struct.pack("<II", name_hash(text), len(raw)) + raw + b"\0"
    return bytes(out)


def _raw(tag, body):
    return {"tag": tag.decode("latin-1"), "hex": body.hex()}


def _unraw(item):
    return item["tag"].encode("latin-1"), bytes.fromhex(item["hex"])


def to_json(record: bytes) -> dict:
    body, record_pad = ucfb.unwrap(record, b"wrld", with_pad=True)
    items, pad_last = ucfb.split(body)
    tables = [body for tag, body in items if tag == b"TABL"]
    if len(tables) != 1:
        raise ValueError("world record needs exactly one TABL chunk")
    strings = read_table(tables[0])
    lookup = {}
    for s in strings:
        lookup.setdefault(name_hash(s), s)

    def word(v):
        if v == 0:
            return ""
        return lookup.get(v, f"#{v:08X}")

    out = {"format": FORMAT, "name": None, "strings": strings, "chunks": []}
    if not pad_last:
        out["pad_last"] = False
    if record_pad != (True, True):
        out["record_pad"] = list(record_pad)
    for tag, body in items:
        if tag == b"NAME" and body.endswith(b"\0") and b"\0" not in body[:-1]:
            out["name"] = body[:-1].decode("ascii")
            out["chunks"].append({"tag": "NAME"})
        elif tag == b"TABL":
            out["chunks"].append({"tag": "TABL"})
        elif tag == b"inst":
            out["chunks"].append(_inst_to_json(body, word))
        else:
            out["chunks"].append(_raw(tag, body))
    return out


def _inst_to_json(body, word):
    parts, pad_last = ucfb.split(body)
    props = [b for t, b in parts if t == b"PROP"]
    if len(props) != 1 or len(props[0]) < 8 or len(props[0]) % 8:
        return _raw(b"inst", body)
    words = struct.unpack(f"<{len(props[0]) // 4}I", props[0])
    inst = {
        "tag": "inst",
        "model": word(words[0]),
        "template": word(words[1]),
        "properties": [[word(words[k]), word(words[k + 1])] for k in range(2, len(words), 2)],
    }
    layout = ["PROP" if t == b"PROP" else _raw(t, b) for t, b in parts]
    if layout != ["PROP"]:
        inst["chunks"] = layout
    if not pad_last:
        inst["pad_last"] = False
    return inst


def from_json(doc: dict) -> bytes:
    if doc.get("format") != FORMAT:
        raise ValueError("not a mercfoundry world file")
    strings = list(doc.get("strings", []))
    known = {name_hash(s) for s in strings}

    def word(text):
        if text == "":
            return 0
        if text.startswith("#") and len(text) == 9:
            return int(text[1:], 16)
        h = name_hash(text)
        if h not in known:
            strings.append(text)
            known.add(h)
        return h

    built = []
    for item in doc["chunks"]:
        tag = item["tag"]
        if tag == "NAME" and "hex" not in item:
            built.append((b"NAME", doc["name"].encode("ascii") + b"\0"))
        elif tag == "TABL" and "hex" not in item:
            built.append((b"TABL", None))  # filled once all strings are known
        elif tag == "inst" and "hex" not in item:
            words = [word(item["model"]), word(item["template"])]
            for key, value in item["properties"]:
                words += [word(key), word(value)]
            prop = struct.pack(f"<{len(words)}I", *words)
            parts = [(b"PROP", prop) if c == "PROP" else _unraw(c) for c in item.get("chunks", ["PROP"])]
            built.append((b"inst", ucfb.pack_children(parts, item.get("pad_last", True))))
        else:
            built.append(_unraw(item))
    table = write_table(strings)
    built = [(t, table if b is None else b) for t, b in built]
    pad = tuple(doc.get("record_pad", (True, True)))
    return ucfb.wrap(b"wrld", ucfb.pack_children(built, doc.get("pad_last", True)), pad)
