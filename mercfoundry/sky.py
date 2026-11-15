"""Sky and weather: a map's atmosphere presets and base sky settings.

They live in the map's loose level config files (DATAxbox), not in the
archives. levels.ini names each level's ConfigFile (SW\\SW.ini); next to
it, an Xbox-only file (SW\\SW_XBOX.ini) holds most of the named presets:

    [atmosphere]
    Name        summer1
    SunlightRGB 158 167 175
    FogRGBA     53 56 48 32
    SkyTexture  sky_chemicalplant
    RainChance  0.3
    ...

A preset comes on inside a lineregion boundary named after it with property
type=atmosphere (weather zones), or when a script forces it
(Mission_EnableAtmosphere(name, true), as the prison and the C-17 do). The
config file's top block holds the base sky (SunAngle, FogRGBA, SkyTexture,
Rain, ...).

A mod changes them with sky/<map>.json:

    {"base": {"FogRGBA": "120 120 120 32"},
     "presets": {"summer1": {"RainChance": "1.0"},
                 "mystorm": {"copy": "summer1", "RainChance": "1", "FogRGBA": "40 40 48 96"}}}

Values are the ini's own text (numbers separated by spaces). A preset that
isn't the map's is new: "copy" names the one it starts from, and it goes
at the end of the file that holds the presets.
"""
import re

from . import terrain_surfaces

KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]*$")
NAME = re.compile(r"[A-Za-z0-9_\-]+$")

# The keys a preset has (the editor's fields, in the files' own order), and
# the base sky keys in the config file's top block.
PRESET_KEYS = ("Fadetime", "SunlightRGB", "AmbientRGB", "HorizonRGB", "SkyRGB", "ZenithRGB", "SunRGB", "SunSize",
               "SunFlareSize", "SunRaySize", "SunDiscSize", "SnowChance", "RainChance", "LeafParticlesPerSecond",
               "AshParticlesPerSecond", "FogRGBA", "SkyTexture", "SkyRotationsPerMinute", "GlowRadius", "GlowDarken",
               "GlowAdd", "ProcessRGBA", "TerrCloudsTexture", "TerrCloudsScale", "TerrCloudsSpeed", "TerrCloudsRGBA",
               "FlatSkyRGB", "NormMapSpecRGB", "NormMapBackSpecRGB")
BASE_KEYS = ("SunAngle", "NormalSkyRGB", "NormalSunRGB", "NormalSunSize", "DarkSkyRGB", "DarkSunRGB", "DarkSunSize",
             "FogRGBA", "SkyTexture", "Rain", "FogFunction", "Atmospherefade")


def level_files(levels_ini, map_name, exists):
    """[config, xbox config] for a map ("SW/SW.ini", "SW/SW_XBOX.ini"), the
    second only if it's there. exists(rel) says whether a DATAxbox-relative
    path is (matching case as the Xbox does)."""
    config = terrain_surfaces.level_config(levels_ini, map_name)
    if not config:
        return []
    config = config.replace("\\", "/")
    out = [config]
    stem, dot, ext = config.rpartition(".")
    for cand in (f"{stem}_XBOX.{ext}", f"{stem}_xbox.{ext}"):
        hit = exists(cand)
        if hit:
            out.append(hit)
            break
    return out


def find(data_dir, rel):
    """A DATAxbox-relative path as it is on disk (matching case as the Xbox
    does), or None."""
    p = data_dir
    for part in rel.split("/"):
        hit = next((c for c in p.iterdir() if c.name.lower() == part.lower()), None) if p.is_dir() else None
        if hit is None:
            return None
        p = hit
    return p.relative_to(data_dir).as_posix()


def level_texts(data_dir, map_name, files=None):
    """{"DATAxbox/SW/SW.ini": text, "DATAxbox/SW/SW_XBOX.ini": text}: a
    map's level config files (files: loose files a build has already
    changed, by that path), or {} if the map isn't a level."""
    from pathlib import Path
    data_dir = Path(data_dir)
    levels = data_dir / "levels.ini"
    if not levels.exists():
        return {}
    rels = level_files(levels.read_text(encoding="latin-1"), map_name, lambda rel: find(data_dir, rel))
    out = {}
    for rel in rels:
        rel = find(data_dir, rel)
        if not rel:
            continue
        path = "DATAxbox/" + rel
        out[path] = files[path].decode("latin-1") if files and path in files else             (data_dir / rel).read_bytes().decode("latin-1")
    return out


def _lines(text):
    return text.splitlines()


def parse(text):
    """The presets in a level config's text: [{"name", "start", "end",
    "keys": [(key, value), ...]}] (start/end: line indexes of the section,
    end exclusive)."""
    lines = _lines(text)
    out, cur = [], None
    for i, line in enumerate(lines + ["[end]"]):
        s = line.strip()
        if s.startswith("["):
            if cur:
                cur["end"] = i
                out.append(cur)
            cur = {"name": None, "start": i, "keys": []} if s.lower() == "[atmosphere]" else None
            continue
        if cur is None or not s or s.startswith(";") or s.startswith("//"):
            continue
        parts = s.split(None, 1)
        key, value = parts[0], (parts[1].strip() if len(parts) > 1 else "")
        if key.lower() == "name":
            cur["name"] = value
        else:
            cur["keys"].append((key, value))
    return [p for p in out if p["name"]]


def base(text):
    """The base sky keys in a config's top block (before its first section): {key: value}."""
    out = {}
    for line in _lines(text):
        s = line.strip()
        if s.startswith("["):
            break
        parts = s.split(None, 1)
        if len(parts) == 2 and parts[0].lower() in {k.lower() for k in BASE_KEYS}:
            out[parts[0]] = parts[1].strip()
    return out


def _check(where, key, value):
    if not KEY.match(key):
        raise ValueError(f"{where}: {key!r} isn't a setting name")
    if not isinstance(value, (str, int, float)) or "\n" in str(value) or "[" in str(value):
        raise ValueError(f"{where}: {key} is one line of numbers or a name, not {value!r}")


def _set_keys(lines, start, end, changes, where):
    """Set keys in lines[start:end] (replacing the first line of each, adding
    the rest at the end of the block). Returns the new lines."""
    block = lines[start:end]
    # trailing blank lines stay after the block
    tail = 0
    while tail < len(block) and not block[len(block) - 1 - tail].strip():
        tail += 1
    body, blank = block[:len(block) - tail], block[len(block) - tail:]
    for key, value in changes.items():
        # a list: a setting that repeats (ProcessRGBA: five colour grades), one line each
        values = [str(v).strip() for v in value] if isinstance(value, list) else [str(value).strip()]
        for v in values:
            _check(where, key, v)
        at = [j for j, line in enumerate(body)
              if line.strip().split(None, 1)[:1] and line.strip().split(None, 1)[0].lower() == key.lower()]
        name = body[at[0]].strip().split(None, 1)[0] if at else key
        new = [f"{name}\t\t{v}" for v in values]
        if at:
            body = body[:at[0]] + new + [l for j, l in enumerate(body[at[0]:], at[0]) if j not in at]
        else:
            body += new
    return lines[:start] + body + blank + lines[end:]


def apply_base(text, changes):
    """The config text with base sky keys set (in its top block)."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = _lines(text)
    first = next((i for i, l in enumerate(lines) if l.strip().startswith("[")), len(lines))
    for key in changes:
        if key.lower() not in {k.lower() for k in BASE_KEYS}:
            raise ValueError(f"base: {key!r} isn't a base sky setting ({', '.join(BASE_KEYS)})")
    lines = _set_keys(lines, 0, first, changes, "base")
    return nl.join(lines) + nl


def apply_presets(text, changes, where="presets"):
    """The text with the named presets' keys set ({name: {key: value}}), for
    presets this text has (case-insensitive names). Returns (text, the names done)."""
    nl = "\r\n" if "\r\n" in text else "\n"
    done = set()
    for name, keys in changes.items():
        found = [p for p in parse(nl.join(_lines(text))) if p["name"].lower() == name.lower()]
        if not found:
            continue
        p = found[0]
        lines = _set_keys(_lines(text), p["start"] + 1, p["end"],
                          {k: v for k, v in keys.items() if k != "copy"}, f"{where} {name}")
        text = nl.join(lines) + nl
        done.add(name)
    return text, done


def add_preset(text, name, keys, where="presets"):
    """The text with a new preset at its end."""
    if not NAME.match(name):
        raise ValueError(f"{where}: a preset name is letters, digits, _ and -, not {name!r}")
    nl = "\r\n" if "\r\n" in text else "\n"
    block = ["", "[atmosphere]", "", f"Name\t\t{name}"]
    for key, value in keys:
        _check(f"{where} {name}", key, value)
        block.append(f"{key}\t\t{str(value).strip()}")
    return text.rstrip("\r\n") + nl + nl.join(block) + nl


def presets_of(texts):
    """{name lower: {"name", "file", "keys": [(key, value)]}} from {file: text}."""
    out = {}
    for f, text in texts.items():
        for p in parse(text):
            out.setdefault(p["name"].lower(), {"name": p["name"], "file": f, "keys": p["keys"]})
    return out


def apply(texts, doc, where="sky"):
    """{file: text} with a sky/<map>.json applied: the base keys go in the
    first file (the config), preset changes where each preset is, and new
    presets (copied from "copy") at the end of the file holding the presets.
    Returns ({file: new text}, [changes])."""
    texts = dict(texts)
    files = list(texts)
    changes = []
    if doc.get("base"):
        texts[files[0]] = apply_base(texts[files[0]], doc["base"])
        changes.append(f"base sky: {', '.join(doc['base'])}")
    known = presets_of(texts)
    home = files[-1]                                  # the Xbox file, where retail keeps its presets
    for name, keys in (doc.get("presets") or {}).items():
        if not isinstance(keys, dict):
            raise ValueError(f"{where}: preset {name!r} is an object of settings")
        hit = known.get(name.lower())
        if hit:
            texts[hit["file"]], _ = apply_presets(texts[hit["file"]], {hit["name"]: keys}, where)
            changes.append(f"preset {hit['name']}: {', '.join(k for k in keys if k != 'copy')}")
            continue
        base_name = keys.get("copy")
        src = known.get(str(base_name or "").lower())
        if base_name and not src:
            raise ValueError(f"{where}: new preset {name!r} copies {base_name!r}, which the map hasn't")
        merged = list(src["keys"]) if src else []
        for key, value in keys.items():
            if key == "copy":
                continue
            values = value if isinstance(value, list) else [value]
            at = [j for j, (k, _) in enumerate(merged) if k.lower() == key.lower()]
            new = [(merged[at[0]][0] if at else key, v) for v in values]
            if at:
                merged = merged[:at[0]] + new + [kv for j, kv in enumerate(merged[at[0]:], at[0]) if j not in at]
            else:
                merged += new
        texts[home] = add_preset(texts[home], name, merged, where)
        changes.append(f"new preset {name}" + (f" (from {src['name']})" if src else ""))
        known = presets_of(texts)
    return texts, changes
