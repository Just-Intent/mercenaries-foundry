"""The game's object templates: world objects named template_*.

Spawner_SpawnTemplate spawns them by name hash. They live in world layers
(template_vehicles, template_humans, template_weapons, ... and mission
layers); a template can only be spawned where its layer is loaded.
"""
from . import find

FIELDS = ("name", "layer", "objectType", "faction", "displayName", "model")


def collect(archives):
    """One row per template name (the first layer that defines it, preferring
    the shared template_* layers), sorted by name."""
    rows = {}
    for obj in find.search(archives, name="template_*"):
        name = obj["name"]
        props = {k.lower(): v for k, v in obj["properties"]}
        row = {"name": name, "layer": obj["layer"], "objectType": props.get("objecttype", ""),
               "faction": props.get("faction", ""), "displayName": props.get("displayname", ""),
               "model": obj.get("model") or props.get("geometryfile", "")}
        old = rows.get(name.lower())
        if old is None or (not old["layer"].startswith("template_") and row["layer"].startswith("template_")):
            rows[name.lower()] = row
    return sorted(rows.values(), key=lambda r: r["name"].lower())


def to_tsv(rows):
    lines = ["\t".join(FIELDS)]
    for r in rows:
        lines.append("\t".join(str(r[f]).replace("\t", " ") for f in FIELDS))
    return "\n".join(lines) + "\n"
