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

How the game uses an entry (retail code, RsAnimTable.cpp and the actor's
animation channels; see docs/formats/dsk.md): a handle is looked up in the
actor's current set, then "default", then the fallback table. Each actor
has three channels (full body, upper body, lower body: fb_, ub_, lb_). A
clip starts on a channel only if its priority is higher than the playing
clip's, or equal when the playing clip has flag 0x4; a playing clip without
0x1 or 0x8 gives way to anything once it has finished. The game keeps the
priority in a byte (0-255).
"""
import struct

from . import ucfb
from .hashing import name_hash

ANIMTABLE = name_hash("animtable")

# Entry flag bits: (word, what the game does with it). "code" = read from
# the retail code; "seen" = only from which clips carry it.
FLAGS = {
    0x1: ("loops", "code: the clip repeats (copied to the channel's loop bit); a clip without 0x1 or 0x8 gives "
                   "way to any other once it ends"),
    0x2: ("moves the character", "code: the character is carried by the clip (paired takedowns, carrying a "
                                 "passenger)"),
    0x4: ("replaceable at equal priority", "code: a clip of the same priority may cut in (walk, run, idle)"),
    0x8: ("holds", "code: like a loop it never gives way by ending (subdued, being taken down, verification)"),
    0x10: ("no aiming", "code: the upper body isn't turned to aim while it plays (reload, grenades, equip, "
                        "fidgets)"),
    0x20: ("seat change", "code: the character is getting in, out or dying in a seat; some actions wait "
                          "(get in, get out, die in a vehicle)"),
    0x40: ("firing", "code: the upper body is firing (checked by the shooting code)"),
    0x80: ("fidget", "code: cut short when the character moves (fidgets, moods)"),
    0x200: ("stop", "code: cut short when the character moves (walk and run stops)"),
    0x400: ("full-body action", "code: no aiming; on the upper body it stops the full-body clip (jumps, "
                                "deaths, ragdoll, melee, airstrike reaction)"),
    0x800: ("moves the character (physical)", "code: with 0x2, the character is moved by the clip "
                                              "(staggers, dive rolls, cover rolls, airstrike reaction)"),
    0x1000: ("idle", "seen: standing idles only; no reader found"),
    0x2000: ("player", "seen: the player's tables' upper-body walk, stop, jump and aim; no reader found"),
    0x4000: ("?", "seen: fidgets, runs, melee, airstrike reaction; no reader found"),
    0x8000: ("stagger", "code: the full-body stagger (speed reset when it ends)"),
    0x10000: ("melee strike", "code: the game asks whether one is playing (melee bash)"),
}


def flag_words(value) -> list:
    """The known meanings of an entry's flags, lowest bit first; unknown bits
    as their hex value."""
    value = int(str(value), 0)
    out = []
    for b in range(32):
        bit = 1 << b
        if value & bit:
            out.append(FLAGS[bit][0] if bit in FLAGS else f"0x{bit:X}")
    return out


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
                            if int(e["flags"], 16):
                                e["meaning"] = flag_words(e["flags"])      # for reading; not read back
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
            prio = int(e.get("priority", 40))
            if not 0 <= prio <= 255:
                raise ValueError(f"{e['handle']}: priority {prio} is out of range (the game keeps it in a byte, 0-255)")
            items.append((b"PRIO", struct.pack("<I", prio)))
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
