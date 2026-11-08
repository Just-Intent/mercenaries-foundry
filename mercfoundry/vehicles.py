"""Vehicle handling: the physics* properties of vehicle objects.

Every vehicle object, template or placed in the world, carries its own copy of
its handling values, so a mod rule matching on "model" reaches all of them
(templates spawned later and vehicles already placed, like the player's
Humvee at the start).  Which code reads which values (each key looked up by
its name hash in default.xbe):

  wheeled   0x136650 / 0x136AD0: engine, gearbox, wheels, suspension, brakes,
            drag; 0x1330F0 / 0x133AD0: steering rate, slide and hill boost,
            spoiler; 0x133640 / 0x1346B0: wheel friction
  tracked   0x141690 / 0x141750: forward speed and acceleration, turning,
            braking; 0x138FC0 (with the hover/flying set) as well
  flying    0x138FC0: climb, side, pitch and roll (helicopters);
            0x147ED0: top speed, acceleration, turn rates, altitude
            (airplanes)
  any body  0x1319F0 / 0x393F0: mass, friction, restitution
"""

from collections import Counter, defaultdict

from . import find

VEHICLE_TYPES = ("jeep", "truck", "trucklarge", "tank", "helicopter", "airplane")


def _handling(props):
    return {k: v for k, v in props if k.lower().startswith("physics")}


def collect(archives):
    """{model: {"type", "display", "objects", "templates", "handling", "varies"}}
    for every vehicle model.  "handling" comes from its first template
    (else its first object); "varies" lists keys whose value differs between
    copies."""
    out = {}
    values = defaultdict(lambda: defaultdict(Counter))
    for obj in find.objects(archives):
        props = obj["properties"]
        low = {k.lower(): v for k, v in props}
        kind = low.get("objecttype", "")
        if kind.lower() not in VEHICLE_TYPES:
            continue
        model = obj.get("model") or low.get("geometryfile", "")
        if not model:
            continue
        name = obj.get("name") or ""
        is_template = name.lower().startswith("template_")
        e = out.setdefault(model, {"type": kind, "display": low.get("displayname", ""), "objects": 0,
                                   "templates": [], "handling": None, "from_template": False})
        e["objects"] += 1
        if is_template and name not in e["templates"]:
            e["templates"].append(name)
        h = _handling(props)
        if h and (e["handling"] is None or (is_template and not e["from_template"])):
            e["handling"] = h
            e["from_template"] = is_template
        for k, v in h.items():
            values[model][k.lower()][v] += 1
    for model, e in out.items():
        e["varies"] = sorted(k for k, c in values[model].items() if len(c) > 1)
        e.pop("from_template")
    return dict(sorted(out.items(), key=lambda kv: (kv[1]["type"], kv[0])))


def rule(model, entry):
    """A mod object rule (objects/*.json) setting this model's handling."""
    return {"model": model, "set": dict(entry["handling"] or {})}
