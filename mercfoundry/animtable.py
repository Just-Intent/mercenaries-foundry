"""Animation tables ("animtable" records): which clip a character plays for
each handle the game asks for.

    ucfb
      atbl
        NAME   table name
        DFLT   optional fallback table, consulted for handles this one lacks
        SIZE   u32 variant count, u32 set count
        vanm   one per variant clip: ANAM clip name
        aset   one per set (weapon or seat: default, rifle, smg, driver, ...)
          NAME   set name
          SIZE   u32 entry count
          entr   HNDL handle (asked for by the game's code, e.g.
                      lb_upright_run_forward), then ANAM clip name or VARG
                      (u32 count, u32 start: a random pick from the variant
                      list), PRIO u32 priority, FLGS u32 flags

Strings are NUL-terminated. Established from retail data: all 95 tables
rebuild byte-for-byte from the JSON form, with counts recomputed.
"""
import struct

from . import ucfb
from .hashing import name_hash

ANIMTABLE = name_hash("animtable")


def _str(b):
    return b.split(b"\0", 1)[0].decode("latin-1")


def _cstr(s):
    return s.encode("latin-1") + b"\0"


def to_json(record: bytes) -> dict:
    (tag, top), = list(ucfb.iter_chunks(record))[:1]
    (tag2, atbl), = list(ucfb.iter_chunks(top))[:1]
    if tag != b"ucfb" or tag2 != b"atbl":
        raise ValueError("not an animation table")
    doc = {"kind": "animtable", "name": "", "variants": [], "sets": []}
    for t, b in ucfb.iter_chunks(atbl):
        if t == b"NAME":
            doc["name"] = _str(b)
        elif t == b"DFLT":
            doc["fallback"] = _str(b)
        elif t == b"SIZE":
            pass  # recomputed
        elif t == b"vanm":
            doc["variants"].append(_str(dict(ucfb.iter_chunks(b))[b"ANAM"]))
        elif t == b"aset":
            s = {"name": "", "entries": []}
            for t2, b2 in ucfb.iter_chunks(b):
                if t2 == b"NAME":
                    s["name"] = _str(b2)
                elif t2 == b"entr":
                    e = {}
                    for t3, b3 in ucfb.iter_chunks(b2):
                        if t3 == b"HNDL":
                            e["handle"] = _str(b3)
                        elif t3 == b"ANAM":
                            e["anim"] = _str(b3)
                        elif t3 == b"VARG":
                            count, start = struct.unpack("<2I", b3)
                            e["variants"] = {"start": start, "count": count}
                        elif t3 == b"PRIO":
                            e["priority"], = struct.unpack("<I", b3)
                        elif t3 == b"FLGS":
                            e["flags"] = f"0x{struct.unpack('<I', b3)[0]:X}"
                        else:
                            raise ValueError(f"unknown entry chunk {t3!r}")
                    s["entries"].append(e)
                elif t2 != b"SIZE":
                    raise ValueError(f"unknown set chunk {t2!r}")
            doc["sets"].append(s)
        else:
            raise ValueError(f"unknown table chunk {t!r}")
    return doc


def from_json(doc: dict) -> bytes:
    if doc.get("kind", "animtable") != "animtable":
        raise ValueError(f"file holds {doc.get('kind')!r} data, not an animation table")
    parts = [(b"NAME", _cstr(doc["name"]))]
    if doc.get("fallback"):
        parts.append((b"DFLT", _cstr(doc["fallback"])))
    variants, sets = doc.get("variants", []), doc.get("sets", [])
    parts.append((b"SIZE", struct.pack("<2I", len(variants), len(sets))))
    for v in variants:
        parts.append((b"vanm", ucfb.pack_chunk(b"ANAM", _cstr(v))))
    for s in sets:
        body = [(b"NAME", _cstr(s["name"])), (b"SIZE", struct.pack("<I", len(s["entries"])))]
        for e in s["entries"]:
            items = [(b"HNDL", _cstr(e["handle"]))]
            if "anim" in e:
                items.append((b"ANAM", _cstr(e["anim"])))
            if "variants" in e:
                v = e["variants"]
                if v["start"] + v["count"] > len(variants):
                    raise ValueError(f"{e['handle']}: variants {v} run past the table's {len(variants)} variants")
                items.append((b"VARG", struct.pack("<2I", v["count"], v["start"])))
            if "anim" not in e and "variants" not in e:
                raise ValueError(f"{e['handle']}: needs an anim or variants")
            items.append((b"PRIO", struct.pack("<I", int(e.get("priority", 40)))))
            items.append((b"FLGS", struct.pack("<I", int(str(e.get("flags", "0")), 0))))
            body.append((b"entr", ucfb.pack_children(items)))
        parts.append((b"aset", ucfb.pack_children(body)))
    atbl = ucfb.pack_chunk(b"atbl", ucfb.pack_children(parts))
    return ucfb.pack_chunk(b"ucfb", atbl)


def clips(doc: dict) -> set:
    """Every clip name a table refers to."""
    out = set(doc.get("variants", []))
    for s in doc.get("sets", []):
        out.update(e["anim"] for e in s["entries"] if "anim" in e)
    return out
