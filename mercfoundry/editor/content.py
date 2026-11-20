"""The editor's panels for what used to be file-only: a new map's HQs (flags,
interiors, medevac, HQ respawn points), its own look (HUD art), and its Deck
of 52. Each reads and writes the mod's own files, checked by the build's own
code so the editor never saves what the build would refuse."""
import base64
import json
import math
import re
from pathlib import Path

from . import gitpanel

# maps/<name>.json keys these panels edit
MAP_KEYS = ("hqs", "interiors", "medevac", "hq_respawn", "hud_textures")


def _read(p):
    return json.loads(Path(p).read_text(encoding="utf-8")) if Path(p).exists() else None


def _write(p, doc):
    """As the mod's other JSON (indent 1), keeping the file's line endings."""
    p = Path(p)
    text = json.dumps(doc, indent=1, ensure_ascii=False) + "\n"
    if p.exists() and b"\r\n" in p.read_bytes()[:4096]:
        text = text.replace("\n", "\r\n")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode("utf-8"))


def _generated_keys(mod, rel):
    """The keys of a mod file its own tools write (mod.json "generated")."""
    g = gitpanel.generated(rel, gitpanel.generated_rules(mod))
    return True if g is True else list(g or [])


def map_keys(mod, name):
    """A new map's HQ, medevac and HUD art keys, and which of them the mod's
    tools write (editing those is undone by the tools' next run)."""
    rel = f"maps/{name.lower()}.json"
    doc = _read(Path(mod) / rel)
    if doc is None:
        return {"new": False}
    gen = _generated_keys(mod, rel)
    out = {"new": True, "keys": {k: doc.get(k) for k in MAP_KEYS},
           "generated": list(MAP_KEYS) if gen is True else [k for k in MAP_KEYS if k in gen]}
    # the HQ factions' names as the mod has them (factions.json), for labels
    fx = _read(Path(mod) / "factions.json") or {}
    out["faction_names"] = {f: (fx.get(f) or {}).get("name") for f in ("allies", "china", "mafia", "sk")}
    return out


def check_map_keys(mod, name, keys):
    """The build's own checks on the keys (raises ValueError as the build would)."""
    from .. import missions, modbuild
    where = f"maps/{name.lower()}.json"
    if keys.get("hqs"):
        missions.hq_flags(where, keys["hqs"])
    if keys.get("interiors"):
        missions.interiors(where, name.lower(), {"interiors": keys["interiors"]})
    if keys.get("medevac") or keys.get("hq_respawn"):
        modbuild.check_spawn_points(keys.get("medevac"), keys.get("hq_respawn"), where)
    hud = keys.get("hud_textures")
    if hud is not None and (not isinstance(hud, dict)
                            or not all(isinstance(k, str) and isinstance(v, str) and k and v for k, v in hud.items())):
        raise ValueError("\"hud_textures\" is {<game texture>: <texture>}")


def save_map_keys(mod, name, changes):
    """changes: {key: value | null}: set or remove those keys of maps/<name>.json."""
    p = Path(mod) / "maps" / f"{name.lower()}.json"
    doc = _read(p)
    if doc is None:
        raise ValueError(f"{name} isn't one of this mod's new maps")
    bad = [k for k in changes if k not in MAP_KEYS]
    if bad:
        raise ValueError(f"not a key this panel edits: {', '.join(bad)}")
    after = dict(doc)
    for k, v in changes.items():
        if v in (None, {}, []):
            after.pop(k, None)
        else:
            after[k] = v
    check_map_keys(mod, name, {k: after.get(k) for k in MAP_KEYS})
    _write(p, after)
    return {"file": f"maps/{name.lower()}.json", "keys": sorted(changes)}


# ---------------------------------------------------------------- the Deck

def deck_files(mod):
    """Every deck/<suit>.json as written, with the build's check of each."""
    from .. import deck
    folder = Path(mod) / "deck"
    maps = {f.stem.lower() for f in (Path(mod) / "maps").glob("*.json")} if (Path(mod) / "maps").is_dir() else set()
    out = []
    for f in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        rel = f"deck/{f.name}"
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            out.append({"file": rel, "error": f"not JSON: {e}"})
            continue
        problem = None
        try:
            deck.parse(rel, doc, maps, Path(mod))
        except ValueError as e:
            problem = str(e)
        portraits = {}
        for c in doc.get("cards") or []:
            rank = deck.RANKS.get(str(c.get("card", "")).upper())
            if rank is None:
                continue
            face = c.get("face")
            if not face:
                for cand in (f"deck/{doc.get('suit')}{rank}.png", f"deck/{doc.get('suit')}_{rank}.png",
                             f"deck/{doc.get('suit')}/{rank}.png"):
                    if (Path(mod) / cand).is_file():
                        face = cand
                        break
            portraits[str(rank)] = face if face and (Path(mod) / face).is_file() else None
        out.append({"file": rel, "doc": doc, "problem": problem, "portraits": portraits,
                    "generated": _generated_keys(mod, rel)})
    return {"files": out, "maps": sorted(maps), "suits": list(deck.SUITS),
            "ranks": ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]}


def save_deck(mod, rel, doc):
    from .. import deck
    rel = rel.replace("\\", "/")
    if not re.fullmatch(r"deck/[A-Za-z0-9_\-]+\.json", rel):
        raise ValueError("a deck file is deck/<name>.json")
    maps = {f.stem.lower() for f in (Path(mod) / "maps").glob("*.json")} if (Path(mod) / "maps").is_dir() else set()
    deck.parse(rel, doc, maps, Path(mod))            # as the build checks it
    _write(Path(mod) / rel, doc)
    return {"file": rel}


def save_portrait(mod, suit, rank, data_b64):
    """deck/<suit><rank>.png from an uploaded PNG (128x128 as the game's)."""
    from .. import deck, png
    if suit not in deck.SUITS:
        raise ValueError("not a suit")
    r = deck.RANKS.get(str(rank).upper())
    if r is None:
        raise ValueError("not a card")
    raw = base64.b64decode(data_b64)
    w, h, _ = png.decode(raw)                         # it must be a PNG the build can read
    rel = f"deck/{suit}{r}.png"
    (Path(mod) / "deck").mkdir(parents=True, exist_ok=True)
    (Path(mod) / rel).write_bytes(raw)
    return {"file": rel, "size": [w, h], "warning": None if (w, h) == (128, 128) else
            f"{w}x{h}: the game's portraits are 128x128 (the PDA draws them at that shape)"}


def save_texture(mod, name, data_b64):
    """textures/<name>.png from an uploaded PNG (a map's own HUD art)."""
    from .. import png
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", name or ""):
        raise ValueError("a texture's name is letters, digits, _ and -")
    raw = base64.b64decode(data_b64)
    w, h, _ = png.decode(raw)
    (Path(mod) / "textures").mkdir(parents=True, exist_ok=True)
    (Path(mod) / "textures" / f"{name}.png").write_bytes(raw)
    return {"file": f"textures/{name}.png", "size": [w, h]}


def mod_png(mod, rel):
    """The bytes of one of the mod's own PNGs (read only, inside the mod)."""
    rel = rel.replace("\\", "/").lstrip("/")
    if not rel.lower().endswith(".png") or ".." in rel.split("/"):
        return None
    p = (Path(mod) / rel).resolve()
    if Path(mod).resolve() not in p.parents or not p.is_file():
        return None
    return p.read_bytes()


# ---------------------------------------------------------------- text and mail

def text_files(mod):
    """The mod's text/*.json: [{file, map (or null: everywhere), strings}]."""
    folder = Path(mod) / "text"
    out = []
    for f in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        rel = f"text/{f.name}"
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            out.append({"file": rel, "error": f"not JSON: {e}"})
            continue
        if isinstance(doc, dict) and "map" in doc:
            out.append({"file": rel, "map": doc.get("map"), "strings": doc.get("strings") or {},
                        "generated": _generated_keys(mod, rel)})
        else:
            out.append({"file": rel, "map": None, "strings": doc if isinstance(doc, dict) else {},
                        "generated": _generated_keys(mod, rel)})
    return out


def save_text(mod, rel, map_name, strings):
    """text/<name>.json: {key: text}, or {"map", "strings"} for a new map's own."""
    rel = rel.replace("\\", "/")
    if not re.fullmatch(r"text/[A-Za-z0-9_\-]+\.json", rel):
        raise ValueError("a text file is text/<name>.json")
    if not isinstance(strings, dict) or not all(isinstance(k, str) and k and isinstance(v, str) for k, v in strings.items()):
        raise ValueError("the strings are {key: text}")
    if map_name:
        maps = {f.stem.lower() for f in (Path(mod) / "maps").glob("*.json")} if (Path(mod) / "maps").is_dir() else set()
        if str(map_name).lower() not in maps:
            raise ValueError(f"{map_name!r} isn't one of this mod's new maps (text only for a game map isn't possible)")
        doc = {"map": str(map_name).lower(), "strings": strings}
    else:
        doc = strings
    _write(Path(mod) / rel, doc)
    return {"file": rel, "count": len(strings)}


def search_strings(english, q, limit=60):
    """The game's strings whose key or text has q: [[key, text]]."""
    q = (q or "").strip().lower()
    if len(q) < 2:
        return []
    out = []
    for k, v in english.items():
        if q in k.lower() or q in str(v).lower():
            out.append([k, str(v)])
            if len(out) >= limit:
                break
    return out


def welcome_mail(mod):
    meta = _read(Path(mod) / "mod.json") or {}
    return {"mail": meta.get("welcome_mail") or None}


def save_welcome_mail(mod, mail):
    """mod.json "welcome_mail" ({sender, subject, body, map?}, or null)."""
    from .. import missions
    from ..modbuild import project_name
    p = Path(mod) / "mod.json"
    meta = _read(p) or {}
    if mail:
        missions.welcome_mail(project_name(mod), mail)       # as the build checks it
        meta["welcome_mail"] = {k: v for k, v in mail.items() if v not in (None, "")}
    else:
        meta.pop("welcome_mail", None)
    _write(p, meta)
    return {"file": "mod.json"}


# ---------------------------------------------------------------- world generators

def kits(mod):
    """The mod's prop kits (kits/<name>.json: {"objects": [{from, offset, yaw, set?}], "half": [x, z]})."""
    folder = Path(mod) / "kits"
    out = []
    for f in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        out.append({"name": f.stem, **doc})
    return out


def save_kit(mod, name, doc):
    """A kit from the editor's selection: its objects relative to its middle."""
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", name or ""):
        raise ValueError("a kit's name is letters, digits, _ and -")
    objs = doc.get("objects") or []
    if not objs or not all(isinstance(o.get("from"), str) and o["from"] and len(o.get("offset") or []) == 3 for o in objs):
        raise ValueError("a kit is objects, each {from, offset: [x, y, z], yaw}")
    half = doc.get("half") or [2, 2]
    clean = {"objects": [{"from": o["from"], "offset": [round(float(v), 2) for v in o["offset"]],
                          "yaw": round(float(o.get("yaw") or 0), 1), **({"set": o["set"]} if o.get("set") else {})}
                         for o in objs],
             "half": [round(float(half[0]), 2), round(float(half[1]), 2)]}
    _write(Path(mod) / "kits" / f"{name}.json", clean)
    return {"file": f"kits/{name}.json", "objects": len(clean["objects"])}


def road_lines(mod, map_name):
    """The map's roads as drawn ([{span, points: [[x, z]...]}]): terrain/<map>.decals.json "roads"."""
    doc = _read(Path(mod) / "terrain" / f"{map_name.lower()}.decals.json") or {}
    return [{"span": float(r.get("span") or 8), "points": [[float(p[0]), float(p[1])] for p in r["points"]]}
            for r in doc.get("roads") or [] if len(r.get("points") or []) >= 2]


# traffic as Gaza's tools lay it (tools/make_traffic.py, make_peds.py): cars one
# lane each way on the right, walkers just inside each kerb
TRAFFIC_KINDS = {
    "cars": {"step": 40.0, "per_path": 9, "join": 22.0, "width": 6, "prefix": "t",
             "settings": {"density": 250, "urgency": 10, "civ_density": 0}},
    "walkers": {"step": 25.0, "per_path": 9, "join": 14.0, "width": 2, "prefix": "p",
                "settings": {"density": 240, "urgency": 10, "civ_density": 40}},
}
ASPHALT = 0.44      # a road decal's tarmac, as a share of its span (the rest is its see-through edge)


def _resample(pts, step):
    out = [tuple(pts[0])]
    carry = 0.0
    for (x0, z0), (x1, z1) in zip(pts, pts[1:]):
        seg = math.hypot(x1 - x0, z1 - z0)
        if seg <= 0:
            continue
        t = step - carry
        while t <= seg:
            out.append((x0 + (x1 - x0) * t / seg, z0 + (z1 - z0) * t / seg))
            t += step
        carry = seg - (t - step)
    if math.dist(out[-1], pts[-1]) > step * 0.3:
        out.append(tuple(pts[-1]))
    return out


def _offset(pts, d):
    """The line moved d metres to its left (negative: right), point by point."""
    out = []
    for k, (x, z) in enumerate(pts):
        a = pts[max(0, k - 1)]
        b = pts[min(len(pts) - 1, k + 1)]
        dx, dz = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dz) or 1.0
        out.append((x - dz / n * d, z + dx / n * d))
    return out


class _Footprints:
    """Whether a point is inside (or within margin of) one of the given
    footprints [x, z, yaw degrees, half x, half z] (the editor's objects)."""
    def __init__(self, items, margin=1.5):
        self.margin = margin
        self.cell = {}
        for x, z, yaw, hx, hz in items:
            ob = (x, z, math.radians(yaw), hx, hz)
            r = max(hx, hz) + margin
            for cx in range(int((x - r) // 50), int((x + r) // 50) + 1):
                for cz in range(int((z - r) // 50), int((z + r) // 50) + 1):
                    self.cell.setdefault((cx, cz), []).append(ob)

    def __call__(self, p):
        for x, z, yaw, hx, hz in self.cell.get((int(p[0] // 50), int(p[1] // 50)), ()):
            dx, dz = p[0] - x, p[1] - z
            lx = dx * math.cos(yaw) - dz * math.sin(yaw)
            lz = dx * math.sin(yaw) + dz * math.cos(yaw)
            if abs(lx) <= hx + self.margin and abs(lz) <= hz + self.margin:
                return True
        return False


def traffic_from_roads(mod, map_name, kind, area, footprints, heights, layer, zone, spawn=None, density=None,
                       max_paths=28):
    """A traffic zone laid on the map's drawn roads inside area ({centre: [x, z],
    radius}), its paths kept clear of the footprints, written into
    encounters/<layer>.json (the zone's old paths replaced). heights(x, z):
    the ground. Returns the counts."""
    if kind not in TRAFFIC_KINDS:
        raise ValueError(f"kind is {' or '.join(TRAFFIC_KINDS)}")
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", layer or "") or not re.fullmatch(r"[A-Za-z0-9_\-]+", zone or ""):
        raise ValueError("the layer's and zone's names are letters, digits, _ and -")
    spec = TRAFFIC_KINDS[kind]
    roads = road_lines(mod, map_name)
    if not roads:
        raise ValueError(f"{map_name} has no drawn roads (terrain/{map_name.lower()}.decals.json \"roads\")")
    cx, cz = (float(v) for v in area["centre"])
    radius = float(area.get("radius") or 300)
    inside = lambda p: math.hypot(p[0] - cx, p[1] - cz) <= radius        # noqa: E731
    blocked = _Footprints(footprints or [])
    runs = []
    for r in roads:
        line = _resample(r["points"], spec["step"])
        tarmac = r["span"] * ASPHALT
        if kind == "cars":
            sides = [_offset(line, -tarmac / 4), _offset(line[::-1], -tarmac / 4)]   # each way, on its right
        else:
            sides = [_offset(line, tarmac / 2 + 1.0), _offset(line, -(tarmac / 2 + 1.0))]  # each kerb
        for side in sides:
            run = []
            for p in side + [None]:
                ok = p is not None and inside(p) and not blocked(p)
                if ok and run:
                    q = run[-1]
                    n = int(math.dist(q, p) // 4)
                    ok = not any(blocked((q[0] + (p[0] - q[0]) * k / (n + 1), q[1] + (p[1] - q[1]) * k / (n + 1)))
                                 for k in range(1, n + 1))
                if ok:
                    run.append(p)
                    continue
                if len(run) >= 2:
                    runs.append(run)
                run = [p] if p is not None and inside(p) and not blocked(p) else []
    paths = []
    per = spec["per_path"]
    for run in runs:
        k = 0
        while k < len(run) - 1:
            paths.append(run[k:k + per])
            k += per - 1
    paths.sort(key=lambda pts: math.dist(pts[0], (cx, cz)))
    paths = paths[:int(max_paths)]
    names = [f"{spec['prefix']}_{zone}_{i + 1:02d}" for i in range(len(paths))]
    out = {}
    for i, (name, pts) in enumerate(zip(names, paths)):
        links = []
        for j, other in enumerate(paths):
            if j == i:
                continue
            if kind == "cars":            # lanes going on from this one's end
                if math.dist(other[0], pts[-1]) <= spec["join"]:
                    links.append({"path": names[j], "their_point": 0})
            else:                          # walkers: any point of another path near this one's end
                near = min(range(len(other)), key=lambda k: math.dist(other[k], pts[-1]))
                if math.dist(other[near], pts[-1]) <= spec["join"]:
                    links.append({"path": names[j], "their_point": near})
        out[name] = {"points": [[round(x, 2), round(heights(x, z) + 0.3, 2), round(z, 2)] for x, z in pts],
                     "properties": {"width": spec["width"]}, "links": links[:4]}
    p = Path(mod) / "encounters" / f"{layer}.json"
    doc = _read(p) or {"map": map_name.lower(), "load": "map"}
    prefix = f"{spec['prefix']}_{zone}_"
    doc["paths"] = {k: v for k, v in (doc.get("paths") or {}).items() if not k.startswith(prefix)}
    doc["paths"].update(out)
    settings = dict(spec["settings"])
    if density is not None:
        settings["density"] = int(density)
    if spawn:
        settings["spawn"] = spawn
    doc.setdefault("traffic", {})[zone] = settings
    if not out:
        doc["traffic"].pop(zone, None)
    _write(p, doc)
    km = sum(math.dist(a, b) for pts in paths for a, b in zip(pts, pts[1:])) / 1000
    return {"file": f"encounters/{layer}.json", "zone": zone, "paths": len(out), "km": round(km, 1),
            "linked": sum(1 for v in out.values() if v["links"])}


def route_counts(mod):
    """The game's limits for traffic: routes (paths in traffic layers) and
    names (every path and zone the mod's layers name), over the mod's
    encounters/*.json."""
    routes = names = 0
    by_map = {}
    folder = Path(mod) / "encounters"
    for f in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        doc = _read(f) or {}
        paths = doc.get("paths") or {}
        zones = doc.get("zones") or {}
        m = str(doc.get("map") or "").lower()
        e = by_map.setdefault(m, {"routes": 0, "names": 0})
        if doc.get("traffic"):
            e["routes"] += len(paths)
        e["names"] += len(paths) + len(zones)
    return by_map


def height_fn(meta, data):
    """The ground's height at (x, z) from the editor's terrain grid (scene.terrain)."""
    import struct
    size, sp = meta["size"], meta["spacing"]
    x0, z0 = meta["x0"], meta["z0"]
    h = struct.unpack(f"<{size * size}f", data)

    def at(x, z):
        fx = min(max((x - x0) / sp, 0), size - 1.001)
        fz = min(max((z - z0) / sp, 0), size - 1.001)
        ix, iz = int(fx), int(fz)
        tx, tz = fx - ix, fz - iz
        a = h[iz * size + ix] * (1 - tx) + h[iz * size + ix + 1] * tx
        b = h[(iz + 1) * size + ix] * (1 - tx) + h[(iz + 1) * size + ix + 1] * tx
        return a * (1 - tz) + b * tz
    return at


# ---------------------------------------------------------------- sounds and effects

def sound_banks(lib):
    return sorted(lib.sound_paths)


def bank_cues(mod, lib, bank):
    """A bank's cues, with what the mod does to each: [{name, waves, mod}],
    the mod's new ones too ("new": a .wav of its own, "like": a copy)."""
    sb = lib.sound_bank(bank)
    folder = Path(mod) / "sounds" / bank
    out = []
    for c in sb.cues:
        mine = []
        if (folder / f"{c.name}.wav").is_file():
            mine.append("replaced")
        out.append({"name": c.name, "waves": len(sb.cue_waves(c)), "mod": mine})
    for f in sorted((folder / "new").glob("*")) if (folder / "new").is_dir() else []:
        if f.suffix.lower() == ".json":
            try:
                like = json.loads(f.read_text(encoding="utf-8")).get("like")
            except ValueError:
                like = None
            tracks = sorted(p.name for p in (folder / "new").glob(f"{f.stem}.*.wav"))
            out.append({"name": f.stem, "waves": len(tracks), "mod": ["new"], "like": like, "tracks": tracks})
        elif f.suffix.lower() == ".wav" and f.stem.count(".") == 0:
            out.append({"name": f.stem, "waves": 1, "mod": ["new"]})
    return {"bank": bank, "cues": out}


def cue_wav(mod, lib, bank, cue, n=0):
    """A cue's nth sound as .wav: the mod's own (a replacement, a new cue, a
    copy's track) where it has one, else the game's."""
    from .. import sounds
    folder = Path(mod) / "sounds" / bank
    for f in (folder / f"{cue}.wav", folder / "new" / f"{cue}.wav", folder / "new" / f"{cue}.{n + 1}.wav"):
        if f.is_file():
            return f.read_bytes()
    sb = lib.sound_bank(bank)
    c = sb.cue(cue)
    like = None
    if c is None:
        spec = folder / "new" / f"{cue}.json"
        if spec.is_file():
            like = (json.loads(spec.read_text(encoding="utf-8")) or {}).get("like")
            c = sb.cue(like) if like else None
    if c is None:
        raise ValueError(f"no cue {bank}.{cue}")
    waves = sb.cue_waves(c)
    if not waves:
        raise ValueError(f"{bank}.{cue} plays no waves")
    return sounds.decoded(lib, *waves[min(int(n), len(waves) - 1)])


def save_sound(mod, lib, bank, cue, kind, wav_b64=None, like=None, track=None):
    """kind "replace": sounds/<bank>/<cue>.wav (a game cue's sound);
    "new": sounds/<bank>/new/<cue>.wav; "like": sounds/<bank>/new/<cue>.json
    {"like"} (a copy of a game cue), and with a .wav its track (n, from 1)."""
    if bank not in lib.sound_paths:
        raise ValueError(f"no sound bank {bank!r}")
    if not re.fullmatch(r"[A-Za-z0-9_]+", cue or ""):
        raise ValueError("a cue's name is letters, digits and _")
    folder = Path(mod) / "sounds" / bank
    sb = lib.sound_bank(bank)
    raw = base64.b64decode(wav_b64) if wav_b64 else None
    if raw is not None and raw[:4] != b"RIFF":
        raise ValueError("that isn't a .wav file")
    if kind == "replace":
        if sb.cue(cue) is None:
            raise ValueError(f"{bank} has no cue {cue!r} to replace")
        if raw is None:
            raise ValueError("give a .wav")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{cue}.wav").write_bytes(raw)
        return {"file": f"sounds/{bank}/{cue}.wav"}
    if sb.cue(cue) is not None:
        raise ValueError(f"{bank} already has a cue {cue!r}: pick a new name")
    (folder / "new").mkdir(parents=True, exist_ok=True)
    if kind == "new":
        if raw is None:
            raise ValueError("give a .wav")
        (folder / "new" / f"{cue}.wav").write_bytes(raw)
        return {"file": f"sounds/{bank}/new/{cue}.wav"}
    if kind == "like":
        if like and sb.cue(like) is None:
            raise ValueError(f"{bank} has no cue {like!r} to copy")
        spec = folder / "new" / f"{cue}.json"
        if like:
            _write(spec, {"like": like})
        elif not spec.is_file():
            raise ValueError("name the cue it copies")
        files = [f"sounds/{bank}/new/{cue}.json"]
        if raw is not None:
            n = int(track or 1)
            (folder / "new" / f"{cue}.{n}.wav").write_bytes(raw)
            files.append(f"sounds/{bank}/new/{cue}.{n}.wav")
        return {"file": ", ".join(files)}
    raise ValueError("kind is replace, new or like")


def effects_table(archives):
    """The game's effects table: {entry: {key: value}} (INFO, STRG and FLOT pairs)."""
    from .. import ucfb
    from ..hashing import name_hash
    from ..modbuild import _fx_pairs
    out = {}
    for a in archives:
        if a.path.name.lower() != "assets.dsk":
            continue
        for r in a.records:
            if r.kind == name_hash("effects") and r.key == name_hash("effects"):
                roots = ucfb.parse(a.read(r))
                for c in roots[0].children:
                    vals = {}
                    for k in c.children:
                        if k.tag in (b"INFO", b"STRG", b"FLOT"):
                            vals.update(dict(_fx_pairs(k.data)))
                    if vals.get("name"):
                        out[vals["name"]] = {k: v for k, v in vals.items() if k != "name"}
                return out
    return out


def effects_doc(mod):
    return _read(Path(mod) / "effects.json") or {}


def save_effects(mod, doc, table):
    """effects.json, checked against the game's table as the build checks it."""
    if not isinstance(doc, dict):
        raise ValueError("effects.json is {entry: {key: value}}")
    names = {k.lower() for k in table}
    for entry, values in doc.items():
        if not isinstance(values, dict):
            raise ValueError(f"{entry}: give an object of keys and values")
        if values.get("copy") is not None:
            if entry.lower() in names:
                raise ValueError(f"the effects table already has an entry {entry!r}")
            if str(values["copy"]).lower() not in names:
                raise ValueError(f"{entry}: no entry {values['copy']!r} to copy")
            if not re.fullmatch(r"[A-Za-z0-9_]+", entry):
                raise ValueError(f"{entry!r}: name new entries with letters, digits and _")
        elif entry.lower() not in names:
            raise ValueError(f"the effects table has no entry {entry!r}")
        for k, v in values.items():
            if k == "copy":
                continue
            if any(ch.isspace() for ch in k) or k.lower() in ("name", "num_components"):
                raise ValueError(f"{entry}: {k!r} can't be set")
            if v is not None and not isinstance(v, (str, int, float)):
                raise ValueError(f"{entry}: {k}: a string, a number or null")
    p = Path(mod) / "effects.json"
    if doc:
        _write(p, doc)
    elif p.exists():
        p.unlink()
    return {"file": "effects.json", "entries": len(doc)}
