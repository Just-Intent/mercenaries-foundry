"""AI and layout records: path, lineregion, region, encounter (and list).

All four chunk-based kinds share one readable form: a tree of containers and
leaves. Leaves we understand are decoded; each decoded leaf must re-encode to
its exact bytes or it stays hex. Established from retail data:

  path      path: NAME, INFO (u16 points, u16 ?, u16 properties), PROP
            (name\\0 + 4-byte value, repeated), PNTS (float[3] each), ORNT
            (quaternion per point), JNCT (links to other paths; hex), and
            sometimes EVNT, ROTN, PSTM, RBRG (hex)
  lineregion lrg_: NAME, INFO (u16 0, u16 properties, u16 points), PROP
            (name\\0value\\0 pairs), PNTS: a named boundary polygon, e.g.
            "allies1_killbox" (type=killbox)
  region    rgns: INFO (u32 regions, u32 sum of region INFO), regn: INFO
            (u16 properties), XFRM, SIZE (float[3]), PROP (pairs)
  encounter enc_: NAME, INFO (u16, u16, u16 point sets, u16 squads),
            PSTS > PSET (NAME, INFO (.., u16 points at byte 4), PNTS),
            SQDS > SQD_ (NAME = squad name, INFO = u16 point-set names,
            u16, u32 squad size; FPNT/PPNT = point-set names)
  list      plain text asset load lists ("[animation]", "load ...")

Count fields that matched their contents in the original are recomputed on
build, so adding points or properties keeps them right.
"""
import struct

from . import ucfb
from .hashing import name_hash

FORMAT = "mercfoundry-ai-1"
CONTAINERS = {b"ucfb", b"path", b"lrg_", b"rgns", b"regn", b"enc_", b"PSTS", b"PSET", b"SQDS", b"SQD_"}
KINDS = {name_hash(n): n for n in ("path", "lineregion", "region", "encounter")}
LIST = name_hash("list")


def _floats(b, n):
    return [list(struct.unpack_from(f"<{n}f", b, i)) for i in range(0, len(b), 4 * n)]


def _path_props(b):
    out, i = [], 0
    while i < len(b):
        j = b.index(b"\0", i)
        key = b[i:j].decode("ascii")
        raw = b[j + 1:j + 5]
        if len(raw) != 4:
            raise ValueError("short value")
        as_int = struct.unpack("<I", raw)[0]
        out.append([key, as_int] if as_int < 0x10000 else [key, struct.unpack("<f", raw)[0]])
        i = j + 5
    return out


def _pack_path_props(pairs):
    out = b""
    for key, value in pairs:
        raw = struct.pack("<I", value) if isinstance(value, int) else struct.pack("<f", value)
        out += key.encode("ascii") + b"\0" + raw
    return out


def _strings(b):
    if not b.endswith(b"\0"):
        raise ValueError("not NUL-terminated")
    return [s.decode("ascii") for s in b[:-1].split(b"\0")]


def _pack_strings(items):
    return b"".join(s.encode("ascii") + b"\0" for s in items)


def _decode_leaf(tag, body, parent):
    """(json dict) for a leaf; exact or hex."""
    t = tag.decode("latin-1")
    attempt = None
    try:
        if tag == b"NAME":
            attempt = {"tag": t, "text": _strings(body)[0]} if body.count(b"\0") == 1 else None
        elif tag == b"PNTS" and len(body) % 12 == 0:
            attempt = {"tag": t, "points": _floats(body, 3)}
        elif tag == b"ORNT" and len(body) % 16 == 0:
            attempt = {"tag": t, "rotations": _floats(body, 4)}
        elif tag == b"XFRM" and len(body) == 48:
            f = struct.unpack("<12f", body)
            attempt = {"tag": t, "rotation": [list(f[0:3]), list(f[3:6]), list(f[6:9])], "position": list(f[9:12])}
        elif tag == b"SIZE" and len(body) == 12:
            attempt = {"tag": t, "size": list(struct.unpack("<3f", body))}
        elif tag == b"PROP" and parent == b"path":
            attempt = {"tag": t, "values": _path_props(body)}
        elif tag == b"PROP" and body == b"":
            attempt = {"tag": t, "pairs": []}
        elif tag == b"PROP":
            s = _strings(body)
            if len(s) % 2 == 0:
                attempt = {"tag": t, "pairs": [s[i:i + 2] for i in range(0, len(s), 2)]}
        elif tag in (b"FPNT", b"PPNT"):
            attempt = {"tag": t, "sets": _strings(body)}
    except (ValueError, UnicodeDecodeError, struct.error):
        attempt = None
    if attempt is not None and _encode_leaf(attempt) == body:
        return attempt
    return {"tag": t, "hex": body.hex()}


def _encode_leaf(item):
    if "hex" in item:
        return bytes.fromhex(item["hex"])
    if "text" in item:
        return item["text"].encode("ascii") + b"\0"
    if "points" in item:
        return b"".join(struct.pack("<3f", *p) for p in item["points"])
    if "rotations" in item:
        return b"".join(struct.pack("<4f", *q) for q in item["rotations"])
    if "position" in item:
        return struct.pack("<12f", *[v for row in item["rotation"] for v in row], *item["position"])
    if "size" in item:
        return struct.pack("<3f", *item["size"])
    if "values" in item:
        return _pack_path_props(item["values"])
    if "pairs" in item:
        return _pack_strings([v for pair in item["pairs"] for v in pair])
    if "sets" in item:
        return _pack_strings(item["sets"])
    raise ValueError(f"can't encode {item.get('tag')}")


# ----------------------------------------------------------- count fields

def _child(items, tag):
    for it in items:
        if it["tag"] == tag:
            return it
    return None


def _counts(container, items):
    """{(offset, fmt): value} that INFO should hold, from the siblings."""
    pnts = _child(items, "PNTS")
    prop = _child(items, "PROP")
    npoints = len(pnts["points"]) if pnts and "points" in pnts else None
    if container == b"lrg_":
        if prop and "pairs" in prop and npoints is not None:
            return {(2, "<H"): len(prop["pairs"]), (4, "<H"): npoints}
    elif container == b"path":
        if prop and "values" in prop and npoints is not None:
            return {(0, "<H"): npoints, (4, "<H"): len(prop["values"])}
    elif container == b"regn":
        if prop and "pairs" in prop:
            return {(0, "<H"): len(prop["pairs"])}
    elif container == b"PSET":
        if npoints is not None:
            return {(4, "<H"): npoints}
    elif container == b"enc_":
        psts, sqds = _child(items, "PSTS"), _child(items, "SQDS")
        out = {(4, "<H"): len(psts["children"]) if psts and "children" in psts else 0,
               (6, "<H"): len(sqds["children"]) if sqds and "children" in sqds else 0}
        return out
    elif container == b"rgns":
        regions = [it for it in items if it["tag"] == "regn" and "children" in it]
        total = 0
        for g in regions:
            info = _child(g["children"], "INFO")
            if info is None or "hex" not in info:
                return {}
            total += struct.unpack_from("<H", bytes.fromhex(info["hex"]))[0]
        return {(0, "<I"): len(regions), (4, "<I"): total}
    return {}


def _apply_counts(info_hex, counts):
    b = bytearray(bytes.fromhex(info_hex))
    for (offset, fmt), value in counts.items():
        if offset + struct.calcsize(fmt) > len(b):
            return None
        struct.pack_into(fmt, b, offset, value)
    return bytes(b)


# ----------------------------------------------------------- tree codec

def _decode(tag, body, parent=None):
    if tag in CONTAINERS:
        try:
            parts, pad_last = ucfb.split(body)
        except ValueError:
            return {"tag": tag.decode("latin-1"), "hex": body.hex()}
        node = {"tag": tag.decode("latin-1"), "children": [_decode(t, b, tag) for t, b in parts]}
        if not pad_last:
            node["pad_last"] = False
        info = _child(node["children"], "INFO")
        if info is not None and "hex" in info:
            counts = _counts(tag, node["children"])
            if counts and _apply_counts(info["hex"], counts) == bytes.fromhex(info["hex"]):
                info["auto"] = True  # count fields are recomputed on build
        if _encode(node) != body:
            return {"tag": tag.decode("latin-1"), "hex": body.hex()}
        return node
    return _decode_leaf(tag, body, parent)


def _encode(node, tag=None):
    if "children" not in node:
        return _encode_leaf(node)
    kids = node["children"]
    tag_b = node["tag"].encode("latin-1")
    parts = []
    for k in kids:
        body = _encode(k)
        if k["tag"] == "INFO" and k.get("auto"):
            fixed = _apply_counts(k["hex"], _counts(tag_b, kids))
            if fixed is not None:
                body = fixed
        parts.append((k["tag"].encode("latin-1"), body))
    return ucfb.pack_children(parts, node.get("pad_last", True))


def to_json(record: bytes, kind: str) -> dict:
    roots, pad = ucfb.split(record)
    return {"format": FORMAT, "kind": kind, "pad_last": pad,
            "tree": [_decode(t, b) for t, b in roots]}


def from_json(doc: dict) -> bytes:
    if doc.get("format") != FORMAT:
        raise ValueError("not a mercfoundry AI/layout file")
    for item in _walk(doc["tree"]):
        if item["tag"] == "path" and "children" in item:
            pnts, ornt = _child(item["children"], "PNTS"), _child(item["children"], "ORNT")
            if pnts and ornt and "points" in pnts and "rotations" in ornt and \
                    len(pnts["points"]) != len(ornt["rotations"]):
                name = _child(item["children"], "NAME")
                raise ValueError(f"path {name.get('text') if name else '?'}: "
                                 f"{len(pnts['points'])} points but {len(ornt['rotations'])} rotations")
    parts = [(n["tag"].encode("latin-1"), _encode(n)) for n in doc["tree"]]
    return ucfb.pack_children(parts, doc.get("pad_last", True))


def _walk(nodes):
    for n in nodes:
        yield n
        if "children" in n:
            yield from _walk(n["children"])


def items(doc):
    """(kind of item, name, node) for each named path, region, encounter..."""
    for n in _walk(doc["tree"]):
        if n["tag"] in ("path", "lrg_", "regn", "enc_", "PSET", "SQD_") and "children" in n:
            name = _child(n["children"], "NAME")
            yield n["tag"], (name.get("text") if name else None), n
