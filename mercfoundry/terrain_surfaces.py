"""Terrain surface types and per-texture effects, from a level's config file.

Each level's ConfigFile (levels.ini; e.g. \\SW\\SWN.ini for LV_SWN, whose
terrain is swn) sets per-texture values by slot: 0-15 are the terrain's ground
layers (LTEX), 16-31 its decal textures (16 + DTEX slot).

  terrainPhysicsType <slot> <surface>  read by the terrain loader (0x1F0688)
      into a 32-entry table (0x6430C0): dirt 1, road 2, snow 3, grass 4,
      rock 5, anything else 0.  The game asks for the surface under a point
      (0x1F28E0: the dominant texture of the cell there); impact effects use
      it when a shot or blast hits bare ground (0xB4D80 / 0xB5460).
  TerrainDust <slot> <effect>   name hashes (0xAD2A5), table 0x323E08
  Dirt <slot> <effect>          name hashes (0xAD25A), table 0x323E88
  layereffect <slot> <effect>   name hashes (0xAD2EA), table 0x323F08
      (e.g. "layereffect 8 blowingpaper").
"""

import re

SURFACES = ("dirt", "road", "snow", "grass", "rock")
KEYS = {"surface": "terrainPhysicsType", "dust": "TerrainDust", "debris": "Dirt", "effect": "layereffect"}
SLOTS = 32

_LINE = re.compile(r"^(\s*)(\S+)(\s+)(\d+)(\s+)(\S+)(.*)$")


def level_config(levels_ini: str, terrain: str):
    """The ConfigFile path (e.g. 'SW\\SWN.ini') of the level whose Name is
    the terrain's name, or None."""
    section, fields, found = None, {}, []
    for line in levels_ini.splitlines() + ["[end]"]:
        s = line.strip()
        if s.startswith("["):
            if fields.get("name", "").lower() == terrain.lower() and "configfile" in fields:
                found.append(fields["configfile"])
            section, fields = s, {}
        elif section and s and not s.startswith(";"):
            parts = s.split(None, 1)
            if len(parts) == 2:
                fields[parts[0].lower()] = parts[1].strip()
    return found[0].strip("\\").replace("/", "\\") if found else None


def read(ini: str) -> dict:
    """{field: {slot: value}} for the per-texture lines of a level config."""
    out = {f: {} for f in KEYS}
    by_key = {k.lower(): f for f, k in KEYS.items()}
    for line in ini.splitlines():
        m = _LINE.match(line)
        if m and m.group(2).lower() in by_key:
            out[by_key[m.group(2).lower()]][int(m.group(4))] = m.group(6)
    return out


def export(ini: str, layer_names, decal_names) -> dict:
    """The JSON a mod edits: one entry per slot that has a texture or a value."""
    values = read(ini)
    entries = []
    for slot in range(SLOTS):
        names = layer_names if slot < 16 else decal_names
        k = slot if slot < 16 else slot - 16
        texture = names[k] if k < len(names) else ""
        e = {"slot": slot, "texture": texture, "kind": "layer" if slot < 16 else "decal"}
        for f in KEYS:
            e[f] = values[f].get(slot)
        if texture or any(e[f] is not None for f in KEYS):
            entries.append(e)
    return {"textures": entries}


def apply(ini: str, doc: dict) -> tuple:
    """The config text with the doc's values set.  Lines for unchanged values
    are left as they are; a null value removes the line.  Returns
    (text, [changes], [warnings])."""
    current = read(ini)
    want = {}
    warnings = []
    for e in doc.get("textures", []):
        slot = int(e["slot"])
        if not 0 <= slot < SLOTS:
            raise ValueError(f"slot {slot}: slots are 0-15 (ground layers) and 16-31 (decal textures)")
        for f in KEYS:
            if f in e:
                v = e[f]
                if v is not None and (not isinstance(v, str) or not v or any(c.isspace() for c in v)):
                    raise ValueError(f"slot {slot}: {f} must be a single word or null")
                if f == "surface" and v is not None and v.lower() not in SURFACES:
                    warnings.append(f"slot {slot}: surface {v!r} isn't one the game knows "
                                    f"({', '.join(SURFACES)}); it will act as none")
                if current[f].get(slot) != v:
                    want[(f, slot)] = v
    if not want:
        return ini, [], warnings
    newline = "\r\n" if "\r\n" in ini else "\n"
    by_key = {k.lower(): f for f, k in KEYS.items()}
    out, last, done = [], {}, set()
    for line in ini.splitlines():
        m = _LINE.match(line)
        if m and m.group(2).lower() in by_key:
            f, slot = by_key[m.group(2).lower()], int(m.group(4))
            if (f, slot) in want:
                done.add((f, slot))
                v = want[(f, slot)]
                if v is None:
                    continue
                line = m.group(1) + m.group(2) + m.group(3) + m.group(4) + m.group(5) + v + m.group(7)
            last[f] = len(out)
        out.append(line)
    # values with no line yet go after the last line of the same key (or at the end)
    added = {}
    for (f, slot), v in sorted(want.items()):
        if (f, slot) not in done and v is not None:
            added.setdefault(f, []).append(f"{KEYS[f]} \t{slot} \t\t{v}")
    for f in sorted(added, key=lambda f: -last.get(f, len(out))):
        at = last[f] + 1 if f in last else len(out)
        out[at:at] = added[f]
    changes = [f"{KEYS[f]} {slot} = {v if v is not None else '(removed)'}" for (f, slot), v in sorted(want.items())]
    return newline.join(out) + (newline if ini.endswith(("\n", "\r")) else ""), changes, warnings
