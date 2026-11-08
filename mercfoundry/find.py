"""Find objects in the world layers.

Patterns: with * or ? they match the whole text (wildcards); without, they
match any part of it. Matching ignores case.
"""
import fnmatch
import math

from . import dsk, world
from .hashing import name_hash

WORLD = name_hash("world")


def _matches(pattern, text):
    if pattern is None:
        return True
    pattern, text = pattern.lower(), text.lower()
    if any(c in pattern for c in "*?["):
        return fnmatch.fnmatchcase(text, pattern)
    return pattern in text


def objects(archives):
    for arc in archives:
        with arc.path.open("rb") as f:
            for r in arc.records:
                if r.kind != WORLD:
                    continue
                f.seek(r.offset)
                for obj in world.iter_objects(f.read(r.size)):
                    obj["archive"] = arc.path.name
                    yield obj


def yaw(rotation):
    """Heading in degrees, in the same sense as the mod rule "turn"."""
    if not rotation:
        return None
    return math.degrees(math.atan2(-rotation[2], rotation[0]))


def locate(archives, name, layer=None):
    """Positions of objects with exactly this name: [(layer, position)]."""
    out = []
    for obj in objects(archives):
        if obj["name"].lower() == name.lower() and obj["position"] and \
                (layer is None or obj["layer"].lower() == layer.lower()):
            out.append((obj["layer"], obj["position"]))
    return out


def search(archives, name=None, model=None, layer=None, near=None, radius=None,
           properties=(), limit=None):
    """Matching objects, each with a "distance" when `near` is given. Sorted
    by distance, else by layer and order in the layer."""
    hits = []
    for obj in objects(archives):
        if layer is not None and not _matches(layer, obj["layer"]):
            continue
        if not _matches(name, obj["name"]) or not _matches(model, obj["model"]):
            continue
        ok = True
        for key, value in properties:
            vals = [v for k, v in obj["properties"] if k.lower() == key.lower()]
            if not vals or (value is not None and not any(_matches(value, v) for v in vals)):
                ok = False
                break
        if not ok:
            continue
        if near is not None:
            if obj["position"] is None:
                continue
            obj["distance"] = math.dist(obj["position"], near)
            if radius is not None and obj["distance"] > radius:
                continue
        hits.append(obj)
    if near is not None:
        hits.sort(key=lambda o: o["distance"])
    return hits[:limit] if limit else hits


def rule_for(obj):
    """A mod rule that matches exactly this object (move it by editing)."""
    rule = {"layer": obj["layer"]}
    if obj["name"]:
        rule["name"] = obj["name"]
    else:
        rule["model"] = obj["model"]
        rule["near"] = [round(v, 3) for v in obj["position"]]
        rule["radius"] = 0.5
    rule["move"] = [0, 0, 0]
    return rule
