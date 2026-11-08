"""New world layers with encounters, from a short description.

How the game uses them (established from retail data and scripts):

  * A layer is a set of records sharing one name: world (objects), encounter,
    path, lineregion, region and a list.  Utility_ReadLevelFile('<layer>')
    (in mission and gameflow scripts) reads the layer's list, which names the
    records to load ("[world]" "load <layer>") and the assets they need.
  * An encounter (enc_) has point sets (PSET: a name like "A" and points)
    and squads (SQD_: a name, INFO = u16 point-set names, u16 0, u32 squad
    size, then FPNT = the point-set names).  Objects join a squad through
    their own "encounter" and "squad" properties.
  * Scripts command squads by name, e.g.
    Squad_Command('Encounter', 'Squad1', 'TacticalMove', 'A', 'hiPri').

A description (a mod's encounters/<layer>.json):

  {"map": "SWN",
   "encounters": [{"name": "FoundryAmbush",
                   "points": {"A": [[x, y, z], ...]},
                   "squads": [{"name": "Squad1", "points": ["A"],
                               "members": [{"from": "RoadblockRunner",
                                            "name": "FoundryNk01",
                                            "position": [x, y, z], "yaw": 90}]}]}],
   "objects": [{"from": ..., "name": ..., "position": ..., "yaw": ...}],
   "paths": {"foundry_patrol": {"points": [[x, y, z], ...],
                                "pauses": {"0": 3.0},
                                "links": [{"path": "other", "their_point": 0}]}}}

"boundaries": {"zone": {"points": [[x, y, z], ...], "properties": {"type": "killbox"}}}
are polygons scripts watch (Event_ActorInBoundary(func, 'b', actor, 'zone',
TRUE)) and switch (Boundary_Enable('zone', TRUE)).  Without properties a
boundary is a plain trigger zone; retail types include killbox (restricted
area), radiation, water, atmosphere, ambience and faction.

Paths (path > NAME, INFO, PROP, [PSTM], PNTS, ORNT, JNCT) are what
Ai_Command(actor, 'Patrol', '<path>', '1.0', 'loPri') and an object's own
"path" property follow.  JNCT links join this path's point (default: its
last) to another path's point (default: its first), as most retail links do.

"from" names any world object (a template_* or a placed one) whose model and
properties are copied; "set" changes properties.  Copy objects whose assets
are already loaded where the layer is used, or add their models to "loads".
"""

import math
import struct

from . import aidata, world
from .hashing import name_hash

WORLD = name_hash("world")
ENCOUNTER = name_hash("encounter")
LIST = name_hash("list")


def _rotation(yaw_degrees):
    a = math.radians(yaw_degrees)
    c, s = math.cos(a), math.sin(a)
    return [[c, 0.0, -s], [0.0, 1.0, 0.0], [s, 0.0, c]]


def _member(source, spec, extra=()):
    """An inst chunk (world JSON) copied from a source inst chunk."""
    inst = {k: (list(map(list, v)) if k == "properties" else v) for k, v in source.items()}
    inst["properties"] = [list(p) for p in source["properties"]]
    inst["template"] = spec["name"]
    sets = dict(extra)
    sets.update(spec.get("set", {}))
    for key, value in sets.items():
        for pair in inst["properties"]:
            if pair[0].lower() == key.lower():
                pair[1] = str(value)
                break
        else:
            inst["properties"].append([key, str(value)])
    inst["position"] = [float(v) for v in spec["position"]]
    inst["rotation"] = _rotation(float(spec.get("yaw", 0)))
    chunks = inst.get("chunks", ["INFO", "XFRM", "PROP"])
    if "XFRM" not in chunks:
        chunks = ["INFO", "XFRM"] + [c for c in chunks if c != "INFO"]
    inst["chunks"] = chunks
    return inst


PATH = name_hash("path")
LINEREGION = name_hash("lineregion")


def _boundary_node(name, spec):
    """A boundary (lineregion > lrg_: NAME, INFO (u16 0, u16 properties,
    u16 points), PROP name/value pairs, PNTS polygon), as all 518 retail ones."""
    points = [[float(v) for v in p] for p in spec["points"]]
    if len(points) < 3:
        raise ValueError(f"boundary {name}: needs at least three points")
    pairs = [[str(k), str(v)] for k, v in (spec.get("properties") or {}).items()]
    return {"tag": "lrg_", "children": [
        {"tag": "NAME", "text": name},
        {"tag": "INFO", "hex": "000000000000", "auto": True},
        {"tag": "PROP", "pairs": pairs},
        {"tag": "PNTS", "points": points}]}


def _path_node(name, spec):
    """A path (retail chunk order: NAME INFO PROP [PSTM] PNTS ORNT JNCT)."""
    points = [[float(v) for v in p] for p in spec["points"]]
    if len(points) < 2:
        raise ValueError(f"path {name}: needs at least two points")
    pauses = sorted([int(p), float(s)] for p, s in (spec.get("pauses") or {}).items())
    for p, _ in pauses:
        if not 0 <= p < len(points):
            raise ValueError(f"path {name}: pause at point {p}, which doesn't exist")
    values = [[k, v] for k, v in (spec.get("properties") or {}).items()]
    values += [["num_respect_bridge_flags", 0], ["num_pause_times", len(pauses)], ["num_nodes_with_events", 0]]
    links = [{"path": k["path"], "their_point": int(k.get("their_point", 0)),
              "our_point": int(k.get("our_point", len(points) - 1))} for k in spec.get("links", [])]
    kids = [{"tag": "NAME", "text": name},
            {"tag": "INFO", "hex": "000000000000", "auto": True},
            {"tag": "PROP", "values": values}]
    if pauses:
        kids.append({"tag": "PSTM", "at_points": pauses})
    kids += [{"tag": "PNTS", "points": points},
             {"tag": "ORNT", "rotations": [[1.0, 0.0, 0.0, 0.0]] * len(points)},
             {"tag": "JNCT", "links": links}]
    return {"tag": "path", "children": kids}


def member_names(doc):
    out = [m["name"] for e in doc.get("encounters", []) for s in e.get("squads", []) for m in s.get("members", [])]
    return out + [o["name"] for o in doc.get("objects", [])]


def sources(doc):
    out = [m["from"] for e in doc.get("encounters", []) for s in e.get("squads", []) for m in s.get("members", [])]
    return sorted(set(out + [o["from"] for o in doc.get("objects", [])]), key=str.lower)


LAYER_KINDS = ("world", "encounter", "path", "lineregion", "region")


def parse_list(text):
    """{section: [names]} from a load list ("[model]" ... "load name")."""
    out, section = {}, None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1]
            out.setdefault(section, [])
        elif section and s.lower().startswith("load "):
            out[section].append(s[5:].strip())
    return out


def build(layer, doc, find_source, source_loads=None):
    """{kind: record bytes} for a new layer.  find_source(name) returns the
    world-JSON inst chunk of an existing object; source_loads(name), when
    given, the asset loads ({section: [names]}) of that object's own layer,
    which are copied into the new layer's list (unless "copy_loads": false)
    so the copies' models, textures and animations are loaded wherever the
    layer is used."""
    names = member_names(doc)
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise ValueError(f"object names used twice: {', '.join(dup)}")
    insts = []
    enc_nodes = []
    for enc in doc.get("encounters", []):
        points = enc.get("points", {})
        psets = []
        for pname, pts in points.items():
            psets.append({"tag": "PSET", "children": [
                {"tag": "NAME", "text": pname},
                {"tag": "INFO", "hex": "010000000000", "auto": True},
                {"tag": "PNTS", "points": [[float(v) for v in p] for p in pts]}]})
        squads = []
        for sq in enc.get("squads", []):
            sets = list(sq.get("points", []))
            missing = [s for s in sets if s not in points]
            if missing:
                raise ValueError(f"encounter {enc['name']}, squad {sq['name']}: no point set {', '.join(missing)}")
            members = sq.get("members", [])
            size = int(sq.get("size", len(members)))
            node = {"tag": "SQD_", "children": [
                {"tag": "NAME", "text": sq["name"]},
                {"tag": "INFO", "hex": struct.pack("<HHI", len(sets), 0, size).hex()}]}
            if sets:
                node["children"].append({"tag": "FPNT", "sets": sets})
            squads.append(node)
            for m in members:
                insts.append(_member(find_source(m["from"]), m,
                                     {"encounter": enc["name"], "squad": sq["name"], "path": "none"}))
        enc_nodes.append({"tag": "enc_", "children": [
            {"tag": "NAME", "text": enc["name"]},
            {"tag": "INFO", "hex": "0000000000000000", "auto": True},
            {"tag": "PSTS", "children": psets},
            {"tag": "SQDS", "children": squads}]})
    for o in doc.get("objects", []):
        insts.append(_member(find_source(o["from"]), o))
    wdoc = {"format": world.FORMAT, "name": layer, "strings": [], "chunks": [
        {"tag": "NAME"},
        {"tag": "TNAM", "hex": (doc.get("map", "SWN").upper().encode("ascii") + b"\0").hex()},
        {"tag": "INFO", "rest": "0000000000000000"},
        {"tag": "TABL"}] + insts}
    out = {WORLD: world.from_json(wdoc)}
    sections = {"world": [layer]}
    if enc_nodes:
        edoc = {"format": aidata.FORMAT, "kind": "encounter",
                "tree": [{"tag": "ucfb", "children": enc_nodes}]}
        out[ENCOUNTER] = aidata.from_json(edoc)
        sections["encounter"] = [layer]
    if doc.get("paths"):
        pdoc = {"format": aidata.FORMAT, "kind": "path",
                "tree": [{"tag": "ucfb", "children": [_path_node(n, s) for n, s in doc["paths"].items()]}]}
        out[PATH] = aidata.from_json(pdoc)
        sections["path"] = [layer]
    if doc.get("boundaries"):
        bdoc = {"format": aidata.FORMAT, "kind": "lineregion",
                "tree": [{"tag": "ucfb", "children": [_boundary_node(n, s) for n, s in doc["boundaries"].items()]}]}
        out[LINEREGION] = aidata.from_json(bdoc)
        sections["lineregion"] = [layer]
    extra = []
    if source_loads is not None and doc.get("copy_loads", True):
        for name in sources(doc):
            for kind, names_ in source_loads(name).items():
                if kind.lower() not in LAYER_KINDS:
                    extra.append((kind, names_))
    extra += list((doc.get("loads") or {}).items())
    for kind, names_ in extra:
        have = sections.setdefault(kind, [])
        have += [n for n in names_ if n.lower() not in {h.lower() for h in have}]
    text = "".join(f"\n[{kind}]\n\n" + "".join(f"load {n}\n" for n in sections[kind]) for kind in sorted(sections))
    out[LIST] = text.encode("latin-1")
    return out
