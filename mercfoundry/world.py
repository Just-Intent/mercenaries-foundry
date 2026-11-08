"""World layer records (type "world"): ucfb -> wrld.

wrld holds NAME (the layer name, whose hash is the record key), TABL (a string
table) and repeated inst chunks. Each inst has a PROP chunk of u32 string-table
hashes: model, template, then (property, value) pairs. Observed in retail
assets.dsk by Mercenaries Recompiled (inspect_retail_templates.py).

Each inst also has INFO (u16; usually the property-pair count + 1) and XFRM:
float[9] rotation (rows) + float[3] world position in metres.

to_json/from_json give an editable form that rebuilds the record exactly when
nothing is changed: instances show "position" and "rotation". Unknown chunks
are kept as hex.
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
        elif tag == b"INFO" and len(body) == 16:
            # [object count, sum of object INFO values, 0, 0]; recomputed on
            # build. Kept raw if a layer ever disagrees.
            out["chunks"].append({"tag": "INFO", "rest": body[8:].hex()})
        else:
            out["chunks"].append(_raw(tag, body))
    if not _layer_info_matches(items):
        out["chunks"] = [_raw(b"INFO", dict(items)[b"INFO"]) if c == {"tag": "INFO", "rest": c.get("rest")} else c
                         for c in out["chunks"]]
    return out


def _layer_info_matches(items):
    infos = [b for t, b in items if t == b"INFO" and len(b) == 16]
    if not infos:
        return True
    count, total = struct.unpack_from("<II", infos[0])
    insts = [dict(ucfb.iter_chunks(b)) for t, b in items if t == b"inst"]
    got = sum(struct.unpack("<H", i[b"INFO"])[0] for i in insts if len(i.get(b"INFO", b"")) == 2)
    return count == len(insts) and total == got


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
    layout = []
    for t, b in parts:
        if t == b"PROP":
            layout.append("PROP")
        elif t == b"INFO" and len(b) == 2 and "info_offset" not in inst:
            # Usually the property-pair count + 1; kept as an offset so edits
            # that add properties stay consistent.
            inst["info_offset"] = struct.unpack("<H", b)[0] - len(inst["properties"])
            layout.append("INFO")
        elif t == b"XFRM" and len(b) == 48 and "position" not in inst:
            f = struct.unpack("<12f", b)
            inst["rotation"] = [list(f[0:3]), list(f[3:6]), list(f[6:9])]
            inst["position"] = list(f[9:12])
            layout.append("XFRM")
        else:
            layout.append(_raw(t, b))
    if layout != ["PROP"]:
        inst["chunks"] = layout
    if not pad_last:
        inst["pad_last"] = False
    return inst


_LAYER_INFO = b"<layer info>"


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
        elif tag == "INFO" and "hex" not in item:
            layer_rest = bytes.fromhex(item.get("rest", "00" * 8))
            built.append((b"INFO", _LAYER_INFO))
        elif tag == "inst" and "hex" not in item:
            words = [word(item["model"]), word(item["template"])]
            for key, value in item["properties"]:
                words += [word(key), word(value)]
            prop = struct.pack(f"<{len(words)}I", *words)
            def part(c):
                if c == "PROP":
                    return b"PROP", prop
                if c == "INFO":
                    return b"INFO", struct.pack("<H", len(item["properties"]) + item.get("info_offset", 1))
                if c == "XFRM":
                    rows = [v for row in item["rotation"] for v in row]
                    return b"XFRM", struct.pack("<12f", *rows, *item["position"])
                return _unraw(c)
            parts = [part(c) for c in item.get("chunks", ["PROP"])]
            built.append((b"inst", ucfb.pack_children(parts, item.get("pad_last", True))))
        else:
            built.append(_unraw(item))
    table = write_table(strings)
    built = [(t, table if b is None else b) for t, b in built]
    if any(t == b"INFO" and b is _LAYER_INFO for t, b in built):
        insts = [dict(ucfb.iter_chunks(b)) for t, b in built if t == b"inst"]
        total = sum(struct.unpack("<H", i[b"INFO"])[0] for i in insts if len(i.get(b"INFO", b"")) == 2)
        info = struct.pack("<II", len(insts), total) + layer_rest
        built = [(t, info if b is _LAYER_INFO else b) for t, b in built]
    pad = tuple(doc.get("record_pad", (True, True)))
    return ucfb.wrap(b"wrld", ucfb.pack_children(built, doc.get("pad_last", True)), pad)


def iter_objects(record: bytes):
    """Yield every object in a world layer quickly, without building JSON.

    Each item: dict(layer, index, model, name, position, rotation, properties)
    where properties is a list of (key, value) pairs. Objects without a
    transform have position None."""
    body = ucfb.unwrap(record, b"wrld")
    layer, table = "", None
    insts = []
    for tag, chunk_body in ucfb.iter_chunks(body):
        if tag == b"NAME":
            layer = chunk_body.split(b"\0", 1)[0].decode("ascii", "replace")
        elif tag == b"TABL":
            table = {name_hash(s): s for s in read_table(chunk_body)}
        elif tag == b"inst":
            insts.append(chunk_body)
    table = table or {}

    def word(v):
        return "" if v == 0 else table.get(v, f"#{v:08X}")

    for index, body in enumerate(insts):
        parts = dict(ucfb.iter_chunks(body))
        prop = parts.get(b"PROP", b"")
        words = struct.unpack(f"<{len(prop) // 4}I", prop[:len(prop) // 4 * 4])
        if len(words) < 2:
            continue
        position = rotation = None
        x = parts.get(b"XFRM")
        if x and len(x) == 48:
            f = struct.unpack("<12f", x)
            rotation, position = f[0:9], f[9:12]
        yield {
            "layer": layer,
            "index": index,
            "model": word(words[0]),
            "name": word(words[1]),
            "position": position,
            "rotation": rotation,
            "properties": [(word(words[k]), word(words[k + 1])) for k in range(2, len(words) - 1, 2)],
        }
