"""Map data for the editor, and object edits as mod rules.

Coordinates: the game's world is right-handed, like three.js, so the editor
uses the game's (x, y, z) as they are (checked in game: a plaza's parking lot
on the +z side shows on the left looking -x). An object's stored rotation is
three rows, the object's own axes in world space.
"""
import json
import math
import re
import struct
from pathlib import Path

from .. import dsk, find, gltf, model, terrain, texture
from ..hashing import name_hash

EDITOR_RULES = "editor.json"      # objects/editor.json in the mod


def to_three(rows):
    """Column-major 3x3 for three.js from the game's rotation rows."""
    return [rows[c][r] for c in range(3) for r in range(3)]   # columns are the axes


def tilted(rows):
    """Whether a rotation (rows) leans: its up axis isn't straight up."""
    return abs(float(rows[1][1]) - 1.0) > 1e-4


def from_three(m3):
    """Game rotation rows from a column-major 3x3 three.js rotation."""
    return [[m3[c * 3 + r] for r in range(3)] for c in range(3)]


def look(o):
    """What the editor draws for an object: the model the game draws (its
    GeometryFile, which some objects' own model field doesn't name, e.g. night
    lampposts and soldiers) and its kind (objectType); for gates, the door
    models the game hangs on the gate's closed-door joints ("parts")."""
    props = {k.lower(): v for k, v in o["properties"]}
    out = {"geometry": props.get("geometryfile") or o["model"], "type": props.get("objecttype", "")}
    parts = [[f"k_gateclosed{side}", props[f"geometrynamegate{side.lower()}"]]
             for side in ("A", "B") if props.get(f"geometrynamegate{side.lower()}")]
    if parts:
        out["parts"] = parts
    return out


def heading(rows):
    return find.yaw([v for row in rows for v in row])


def turn_between(old_rows, new_rows):
    """The rule "turn" (degrees) that takes old_rows to new_rows' heading."""
    t = heading(new_rows) - heading(old_rows)
    return round((t + 180.0) % 360.0 - 180.0, 4)


def context_mods(mod_dir):
    """The mods whose terrain the editor shows, in load order: when the mod
    sits in a game's mods folder, every enabled source mod (as `mod sync`
    builds them, the edited mod included even if it's off); else the mod."""
    from .. import sync
    mod = Path(mod_dir).resolve()
    game = mod.parent.parent
    if mod.parent.name.lower() == "mods" and (game / "mod-loader.ini").exists():
        out = []
        for m in sync.read_loader(game):
            p = (mod.parent / m.folder).resolve()
            if (m.enabled or p == mod) and sync.is_source(p):
                out.append(p)
        if mod not in out:
            out.append(mod)
        return out
    return [mod]


def _terrain_rec(data, name):
    """A map's terrain record in a build's data: the game's, or a new map's
    (maps/), which the build added."""
    rec = next(((a, r) for a, r in data.records(name_hash("Terrain")) if r.key == name_hash(name)), None)
    if rec is None and data.find_added(name_hash(name), name_hash("Terrain")) is not None:
        rec = (None, name_hash(name))
    return rec


def _terrain_bytes(data, rec):
    a, r = rec
    return data.current(a, r) if a else data.find_added(r, name_hash("Terrain"))


class Scene:
    def __init__(self, mod_dir, data_dir, context=None):
        self.mod = Path(mod_dir)
        self.data = Path(data_dir)
        self.archives = [dsk.Archive(p) for p in sorted(self.data.glob("*.dsk"))]
        self.context = context if context is not None else context_mods(mod_dir)
        self._objects = None
        self._models = {}
        self._names = None
        self._terrain = None

    # ------------------------------------------------------------ mods
    # The mod the editor saves to is self.mod; in a game's mods folder the
    # editor lists the folder's source mods to switch to, make, edit or
    # delete (moved to the game's foundry-trash folder, not erased).
    _GAME_CACHES = {"archives", "data", "_names", "_objects", "_placeables", "_pngs", "_carry_tables",
                    "_sound_lib", "_voices", "_sky_textures", "_lua_names", "_terrain_gen"}

    def game_dir(self):
        """The game folder when the mod sits in its mods folder, else None."""
        p = self.mod.resolve()
        game = p.parent.parent
        return game if p.parent.name.lower() == "mods" and (game / "mod-loader.ini").exists() else None

    def mods(self):
        """[{folder, name, description, enabled, current}] for the game's
        source mods (just this one outside a game folder)."""
        from .. import sync
        game = self.game_dir()
        current = self.mod.resolve()
        if game is None:
            folders = [current]
            enabled = {}
        else:
            folders = sorted((p for p in (game / "mods").iterdir() if p.is_dir() and sync.is_source(p)),
                             key=lambda p: p.name.lower())
            enabled = {m.folder: m.enabled for m in sync.read_loader(game)}
        out = []
        for p in folders:
            meta = p / "mod.json"
            doc = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
            out.append({"folder": p.name, "name": doc.get("name") or p.name, "description": doc.get("description") or "",
                        "enabled": enabled.get(p.name), "current": p.resolve() == current})
        return {"mods": out, "game": game is not None}

    def _mod_folder(self, folder):
        game = self.game_dir()
        if game is None:
            raise ValueError("the editor isn't in a game's mods folder, so it has only this mod")
        if not folder or "/" in folder or "\\" in folder or folder in (".", ".."):
            raise ValueError(f"bad mod folder {folder!r}")
        return game / "mods" / folder

    def switch_mod(self, folder):
        """Edit (and save to) another of the game's source mods."""
        from .. import sync
        p = self._mod_folder(folder)
        if not sync.is_source(p):
            raise ValueError(f"mods/{folder} isn't a source mod (a mod.json and source folders)")
        self.mod = p
        self.context = context_mods(p)
        for k in [k for k in vars(self) if k.startswith("_") and k not in self._GAME_CACHES]:
            delattr(self, k)
        self._terrain = None
        self._models = {}
        self.terrain_errors = []
        return self.mods()

    def create_mod(self, name, description=""):
        """A new source mod in the game's mods folder, enabled in the
        launcher's list (so it's built), and the editor switched to it."""
        from .. import modbuild, sync
        name = str(name or "").strip()
        folder = re.sub(r"[^A-Za-z0-9 _-]+", "", name).strip()
        if not folder:
            raise ValueError("a mod needs a name (letters, digits, spaces, _ and -)")
        p = self._mod_folder(folder)
        if p.exists():
            raise ValueError(f"mods/{folder} already exists")
        modbuild.new_project(p, name)
        if description:
            meta = p / "mod.json"
            doc = json.loads(meta.read_text(encoding="utf-8"))
            doc["description"] = str(description)
            meta.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        sync.set_enabled(self.game_dir(), folder, True)
        return self.switch_mod(folder)

    def update_mod(self, folder, name=None, description=None, enabled=None):
        """A mod's name and description (its mod.json) and whether the
        launcher builds it (enabled in its list)."""
        from .. import sync
        p = self._mod_folder(folder)
        meta = p / "mod.json"
        if not meta.exists():
            raise ValueError(f"mods/{folder} has no mod.json")
        doc = json.loads(meta.read_text(encoding="utf-8"))
        if name is not None:
            if not str(name).strip():
                raise ValueError("a mod needs a name")
            doc["name"] = str(name).strip()
        if description is not None:
            doc["description"] = str(description)
        meta.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        if enabled is not None:
            sync.set_enabled(self.game_dir(), folder, bool(enabled))
        return self.mods()

    def delete_mod(self, folder):
        """Move a mod out of the mods folder (to the game's foundry-trash,
        from where it can be moved back) and off the launcher's list. Not
        the one being edited."""
        import shutil
        import time
        from .. import sync
        p = self._mod_folder(folder)
        if p.resolve() == self.mod.resolve():
            raise ValueError("that's the mod being edited: switch to another first")
        if not sync.is_source(p):
            raise ValueError(f"mods/{folder} isn't a source mod")
        game = self.game_dir()
        listed = sync.read_loader(game)
        if any(m.folder == folder for m in listed):
            sync.write_loader(game, [m for m in listed if m.folder != folder], sync._separate_saves(game))
        trash = game / "foundry-trash"
        trash.mkdir(exist_ok=True)
        dest = trash / f"{folder} {time.strftime('%Y%m%d-%H%M%S')}"
        shutil.move(str(p), str(dest))
        out = self.mods()
        out["moved_to"] = str(dest.relative_to(game))
        return out

    # ------------------------------------------------------------ maps
    def names(self):
        if self._names is None:
            from ..hashing import type_book
            self._names = type_book()
            for a in self.archives:
                dsk.collect_names(a, self._names)
        return self._names

    def animation_names(self):
        """Names of the game's animation clips (for path events), sorted."""
        from ..animation import ANIMATION
        book = self.names()
        out = {book.get(r.key) for a in self.archives for r in a.records if r.kind == ANIMATION}
        return sorted((n for n in out if n), key=str.lower)

    def maps(self):
        """Terrains that are maps: ones with a world layer of the same name."""
        tern, world_kind = name_hash("Terrain"), name_hash("world")
        worlds = {r.key for a in self.archives for r in a.records if r.kind == world_kind}
        out = []
        for a in self.archives:
            for r in a.records:
                if r.kind == tern and r.key in worlds:
                    n = self.names().get(r.key)
                    if n:
                        out.append(n.lower())
        # new maps (maps/<name>.json) of the mods the editor shows
        for p in self.context:
            mdir = Path(p) / "maps"
            out += [f.stem.lower() for f in mdir.glob("*.json")] if mdir.is_dir() else []
        return sorted(set(out))

    def new_map(self, name, src="blank", size=None):
        """A new map: maps/<name>.json, a copy of a game map (blank: flat,
        nearly empty), optionally flat ground of another size (cells a side:
        256, 512, 1024). The build makes its records, level and master script."""
        name = str(name or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            raise ValueError("a map name is letters, digits and _")
        if name.lower() in self.maps():
            raise ValueError(f"there's already a map named {name}")
        src = str(src or "blank").lower()
        if src not in self.game_maps():
            raise ValueError(f"{src} isn't a game map")
        doc = {"from": src}
        if size:
            from ..terrain import SIZES
            if int(size) not in SIZES:
                raise ValueError(f"a new map's ground is {', '.join(map(str, SIZES))} cells a side")
            doc["size"] = int(size)
        (self.mod / "maps").mkdir(parents=True, exist_ok=True)
        (self.mod / "maps" / f"{name.lower()}.json").write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        return {"name": name.lower()}

    def game_maps(self):
        """The game's own maps (ones a new map can copy)."""
        new = {f.stem.lower() for p in self.context if (Path(p) / "maps").is_dir()
               for f in (Path(p) / "maps").glob("*.json")}
        return [m for m in self.maps() if m not in new]

    def map_settings(self, name):
        """A mod's new map: what it copies, where the player arrives, its
        message, the layers that load with it, and whether the mod has its
        own master script. {"new": false} for the game's maps."""
        p = self.mod / "maps" / f"{name.lower()}.json"
        if not p.exists():
            # a game map: the mod's travel pilots standing on it, to its new maps
            pilots, new_maps = [], []
            for f in sorted((self.mod / "maps").glob("*.json")) if (self.mod / "maps").is_dir() else []:
                new_maps.append(f.stem.lower())
                for k, t in enumerate(json.loads(f.read_text(encoding="utf-8")).get("travel") or []):
                    if str(t.get("map") or "sw").lower() == name.lower():
                        pilots.append({"to": f.stem.lower(), "index": k, "position": t.get("position"),
                                       "heading": t.get("heading", 0)})
            return {"new": False, "free_roam": name.lower() in ("sw", "nw"), "pilots": pilots, "new_maps": new_maps}
        doc = json.loads(p.read_text(encoding="utf-8"))
        layers = []
        for f in sorted((self.mod / "encounters").glob("*.json")) if (self.mod / "encounters").is_dir() else []:
            try:
                ld = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if str(ld.get("map") or "").lower() == name.lower():
                layers.append({"name": f.stem, "loads": ld.get("load") == "map"})
        return {"new": True, "name": name.lower(), "from": doc.get("from") or "blank",
                "start": doc.get("start") or {}, "message": doc.get("message") or "",
                "layers": layers, "own_script": (self.mod / "scripts" / f"{name.lower()}.lua").exists(),
                "return": doc.get("return"), "travel": doc.get("travel") or [],
                "weather": doc.get("weather") or "", "presets": self._arrival_presets(name)}

    def _arrival_presets(self, name):
        """Weather presets a new map can switch to on arrival: its own config's
        and SW's (presets seem to carry over a map switch), with the new ones
        this mod's sky/<map>.json and sky/sw.json add."""
        from .. import sky as sk
        out = []
        for m in (name.lower(), "sw"):
            try:
                texts = sk.level_texts(self.data, m) or sk.level_texts(self.data, m, self._modded_terrain().files)
            except (AttributeError, ValueError, KeyError, OSError):      # no game data to read (tests)
                texts = {}
            out += [v["name"] for v in sk.presets_of(texts).values()] if texts else []
            sp = self.mod / "sky" / f"{m}.json"
            if sp.exists():
                out += list((json.loads(sp.read_text(encoding="utf-8")).get("presets") or {}))
        seen, names = set(), []
        for n in out:
            if n.lower() not in seen:
                seen.add(n.lower())
                names.append(n)
        return names

    def map_marks(self, map_name):
        """What the mod's maps/*.json put on this map, to see and move like
        objects: travel pilots (on SW, NW), a new map's pilot back and its
        arrival point. [{kind: travel|return|start, to, index, label, model,
        position, rotation (editor form)}]."""
        from ..encounters import _rotation
        m = map_name.lower()
        out = []
        mdir = self.mod / "maps"
        for f in sorted(mdir.glob("*.json")) if mdir.is_dir() else []:
            doc = json.loads(f.read_text(encoding="utf-8"))
            name = f.stem.lower()

            def mark(kind, index, spec, label, model):
                pos = spec.get("position") or []
                if len(pos) == 3:
                    out.append({"kind": kind, "to": name, "index": index, "label": label, "model": model,
                                "position": [float(v) for v in pos],
                                "rotation": to_three(_rotation(float(spec.get("heading") or 0)))})
            for k, t in enumerate(doc.get("travel") or []):
                if str(t.get("map") or "sw").lower() == m:
                    mark("travel", k, t, f"Pilot to {name}", "allies_hum_pilot")
                    # where the pilot back from the new map puts the player (its "arrive",
                    # or the default beside this pilot): the first pilot here that goes there
                    back = doc.get("return") or {}
                    first = next(i for i, x in enumerate(doc["travel"]) if str(x.get("map") or "sw").lower() == m)
                    if back.get("position") and str(back.get("to") or "sw").lower() == m and k == first:
                        from ..modbuild import default_arrive
                        arrive = t.get("arrive") or {}
                        spot = {"position": arrive.get("position") or (default_arrive(t["position"])
                                                                         if t.get("position") else None),
                                "heading": arrive.get("heading", t.get("heading") or 0)}
                        mark("arrive", k, spot, f"Where you arrive back from {name}", "global_humanmarker")
            if name == m:
                if doc.get("return"):
                    mark("return", 0, doc["return"], f"Pilot back to {str(doc['return'].get('to') or 'sw').upper()}",
                         "allies_hum_pilot")
                if doc.get("start"):
                    mark("start", 0, doc["start"], f"Where you arrive on {name}", "global_humanmarker")
        return out

    def save_map_marks(self, marks):
        """Moved or turned marks (map_marks) written back to maps/<to>.json."""
        docs = {}
        for mk in marks:
            to = str(mk["to"]).lower()
            if to not in docs:
                p = self.mod / "maps" / f"{to}.json"
                if not p.exists():
                    raise ValueError(f"{to} isn't one of this mod's new maps")
                docs[to] = json.loads(p.read_text(encoding="utf-8"))
            doc = docs[to]
            if mk["kind"] in ("travel", "arrive"):
                travel = doc.get("travel") or []
                if not 0 <= int(mk["index"]) < len(travel):
                    raise ValueError(f"{to} has no travel pilot {mk['index']}")
                spec = travel[int(mk["index"])]
                if mk["kind"] == "arrive":
                    spec = spec.setdefault("arrive", {})
            elif mk["kind"] in ("return", "start"):
                spec = doc.get(mk["kind"])
                if not spec:
                    raise ValueError(f"{to} has no {mk['kind']}")
            else:
                raise ValueError(f"no mark kind {mk['kind']!r}")
            rows = from_three(mk["rotation"])
            spec["position"] = [round(float(v), 2) for v in mk["position"]]
            spec["heading"] = round(math.degrees(math.atan2(-rows[0][2], rows[0][0])), 1)
        for to, doc in docs.items():
            (self.mod / "maps" / f"{to}.json").write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        return {"files": [f"maps/{to}.json" for to in docs]}

    def add_travel_pilot(self, to_map, on_map, position, heading=0):
        """A pilot on a free-roam map (sw, nw) that takes the player to one of
        the mod's new maps: added to maps/<to>.json "travel"."""
        p = self.mod / "maps" / f"{str(to_map).lower()}.json"
        if not p.exists():
            raise ValueError(f"{to_map} isn't one of this mod's new maps")
        if str(on_map).lower() not in ("sw", "nw"):
            raise ValueError("a travel pilot stands on SW or NW (free roam)")
        pos = [round(float(v), 2) for v in position]
        if len(pos) != 3:
            raise ValueError("position is [x, y, z]")
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc.setdefault("travel", []).append({"map": str(on_map).lower(), "position": pos,
                                             "heading": round(float(heading or 0), 1)})
        p.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        return self.map_settings(on_map)

    def remove_travel_pilot(self, to_map, index, on_map):
        p = self.mod / "maps" / f"{str(to_map).lower()}.json"
        if not p.exists():
            raise ValueError(f"{to_map} isn't one of this mod's new maps")
        doc = json.loads(p.read_text(encoding="utf-8"))
        travel = doc.get("travel") or []
        if not 0 <= int(index) < len(travel):
            raise ValueError(f"{to_map} has no travel pilot {index}")
        del travel[int(index)]
        if travel:
            doc["travel"] = travel
        else:
            doc.pop("travel", None)
        p.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        return self.map_settings(on_map)

    def save_map_settings(self, name, doc):
        """Write a new map's arrival point and message (maps/<name>.json), and
        which of its layers load with it (their "load": "map")."""
        p = self.mod / "maps" / f"{name.lower()}.json"
        if not p.exists():
            raise ValueError(f"{name} isn't one of this mod's new maps")
        out = json.loads(p.read_text(encoding="utf-8"))
        start = doc.get("start") or {}
        if start.get("position"):
            pos = [round(float(v), 2) for v in start["position"]]
            if len(pos) != 3:
                raise ValueError("start position is [x, y, z]")
            out["start"] = {"position": pos, "heading": round(float(start.get("heading") or 0), 1)}
        else:
            out.pop("start", None)
        msg = str(doc.get("message") or "").strip()
        if msg:
            out["message"] = msg
        else:
            out.pop("message", None)
        if "weather" in doc:         # the preset its master script switches to on arrival
            weather = str(doc.get("weather") or "").strip()
            if weather and not re.fullmatch(r"[A-Za-z0-9_\-]+", weather):
                raise ValueError("a weather preset's name is letters, digits, _ and -")
            if weather:
                out["weather"] = weather
            else:
                out.pop("weather", None)
        if "return" in doc:          # the pilot on this map that takes the player back
            back = doc.get("return") or {}
            if back.get("position"):
                pos = [round(float(v), 2) for v in back["position"]]
                if len(pos) != 3:
                    raise ValueError("the pilot back's position is [x, y, z]")
                to = str(back.get("to") or "sw").lower()
                if to not in ("sw", "nw"):
                    raise ValueError("the pilot back goes to SW or NW")
                out["return"] = {"position": pos, "heading": round(float(back.get("heading") or 0), 1), "to": to}
            else:
                out.pop("return", None)
        p.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
        for layer, on in (doc.get("layers") or {}).items():
            lp = self.mod / "encounters" / f"{layer}.json"
            if not lp.exists():
                continue
            ld = json.loads(lp.read_text(encoding="utf-8"))
            if str(ld.get("map") or "").lower() != name.lower() or bool(ld.get("load") == "map") == bool(on):
                continue
            if on:
                ld["load"] = "map"
            else:
                ld.pop("load", None)
            lp.write_text(json.dumps(ld, indent=1) + "\n", encoding="utf-8")
        return self.map_settings(name)

    def _modded_terrain(self):
        """The context mods' terrain steps, applied as a build would (once,
        again when a mod's maps/*.json is added, changed or removed)."""
        stamp = self._maps_stamp()
        if stamp != getattr(self, "_terrain_stamp", None):
            self._terrain = None
            if hasattr(self, "_looks"):
                self._looks.clear()
        if self._terrain is None:
            from .. import modbuild
            gen = getattr(self, "_terrain_gen", 0)
            data = modbuild._Data(self.data)
            errors = []
            for p in self.context:
                data.project = modbuild.project_name(p)
                # A mod's file the build refuses leaves its ground as far as it
                # got (shown as an error), not every map unloadable.
                for step, folder in ((modbuild._apply_maps, "maps"), (modbuild._apply_terrain, "terrain")):
                    if (Path(p) / folder).is_dir():
                        try:
                            step(data, Path(p), modbuild.Result())
                        except (ValueError, KeyError, OSError) as e:
                            errors.append(f"{data.project}: {e}")
                            print(f"terrain: {data.project}: {e}", flush=True)
            self.terrain_errors = errors
            # A save during the build makes it stale: use it this once, keep nothing.
            if gen != getattr(self, "_terrain_gen", 0):
                return data
            self._terrain = data
            self._terrain_stamp = stamp
        return self._terrain

    def _maps_stamp(self):
        """The shown mods' map, terrain, layer and object-rule files and when
        each last changed, so files another mod or tool writes while the
        editor runs show (e.g. `gis build`). A change to the launcher's mod
        list (a mod switched on or off) re-reads which mods are shown."""
        game = self.game_dir()
        loader = game / "mod-loader.ini" if game else None
        lstamp = loader.stat().st_mtime_ns if loader and loader.exists() else None
        if lstamp != getattr(self, "_loader_stamp", lstamp):
            self.context = context_mods(self.mod)
        self._loader_stamp = lstamp
        out = [("loader", lstamp)]
        mine = self.mod.resolve()
        for p in self.context:
            # the edited mod's own object saves refresh what they change already
            own = Path(p).resolve() == mine
            for folder in ("maps", "terrain") if own else ("maps", "terrain", "encounters", "objects"):
                d = Path(p) / folder
                if d.is_dir():
                    out += [(str(f), f.stat().st_mtime_ns, f.stat().st_size) for f in sorted(d.iterdir()) if f.is_file()]
        return tuple(out)

    def _forget_terrain(self, map_name):
        """After a terrain save: read the mods' terrain files again next time."""
        self._terrain = None
        self._terrain_gen = getattr(self, "_terrain_gen", 0) + 1
        if hasattr(self, "_looks"):
            self._looks.pop(map_name, None)

    def terrain(self, name):
        """(meta, float32 heights bytes), row z from the map's -z edge, with
        the context mods' terrain edits."""
        data = self._modded_terrain()
        rec = _terrain_rec(data, name)
        if rec is None:
            raise KeyError(name)
        chunks = terrain.tern_chunks(_terrain_bytes(data, rec))
        info = terrain.parse_info(chunks[b"INFO"])
        size, heights = terrain.decode_heights(chunks)
        sp = info["spacing"]
        meta = {"name": name, "size": size, "spacing": sp, "step": info["step"],
                "x0": -size * sp / 2, "z0": -(size * sp / 2 - sp)}
        return meta, struct.pack(f"<{size * size}f", *(h for row in heights for h in row))

    def save_paint(self, map_name, layers):
        """Ground paint from the editor ({layer: {texture, pixels: [[col, row,
        opacity]]}}) merged into the mod's terrain/<map>.layerNN.<texture>.changes.png
        (grey = opacity, alpha 255 = changed; the build applies it)."""
        import re
        from .. import png
        if not re.fullmatch(r"[A-Za-z0-9_]+", map_name):
            raise ValueError(f"bad map name {map_name!r}")
        meta, _, _ = self.terrain_look(map_name)
        w = meta["paint_size"]
        tdir = self.mod / "terrain"
        written = []
        for key, layer in layers.items():
            index = int(key)
            texture = re.sub(r"[^A-Za-z0-9_]", "_", layer.get("texture") or "")
            existing = sorted(tdir.glob(f"{map_name}.layer{index:02d}.*")) if tdir.is_dir() else []
            whole = [f for f in existing if not f.name.lower().endswith(".changes.png")]
            if whole:
                raise ValueError(f"terrain/{whole[0].name} repaints this layer as a whole image; edit that image")
            p = existing[0] if existing else tdir / f"{map_name}.layer{index:02d}.{texture}.changes.png"
            la = bytearray(w * w * 2)
            if p.exists():
                pw, ph, rgba = png.decode(p.read_bytes())
                if (pw, ph) != (w, w):
                    raise ValueError(f"terrain/{p.name} is {pw} x {ph}, not {w} x {w}")
                for i in range(w * w):
                    if rgba[4 * i + 3] >= 128:
                        la[2 * i], la[2 * i + 1] = rgba[4 * i], 255
            for x, z, v in layer.get("pixels", []):
                i = int(z) * w + int(x)
                la[2 * i], la[2 * i + 1] = max(0, min(255, int(v))), 255
            tdir.mkdir(parents=True, exist_ok=True)
            p.write_bytes(png.encode(w, w, bytes(la), 2))
            written.append(f"terrain/{p.name}")
        self._forget_terrain(map_name)
        return {"files": written}

    def ground_textures(self):
        """Every ground texture the game's maps paint with, its scale and the
        projection (axis) the maps give it most (top-down, 0, on a tie):
        [{texture, scale, axis, map}]."""
        if getattr(self, "_ground_textures", None) is None:
            from collections import Counter
            from .. import terrain_paint
            out, seen, axes = [], set(), {}
            for m in self.game_maps():
                rec = _terrain_rec(self._modded_terrain(), m)
                if rec is None:
                    continue
                try:
                    table = terrain_paint.layer_table(terrain.tern_chunks(_terrain_bytes(self._modded_terrain(), rec)))
                except (KeyError, ValueError):
                    continue
                for t in table:
                    if not t["texture"]:
                        continue
                    axes.setdefault(t["texture"].lower(), Counter())[int(t["axis"])] += 1
                    if t["texture"].lower() not in seen:
                        seen.add(t["texture"].lower())
                        out.append({**t, "map": m})
            for t in out:
                c = axes[t["texture"].lower()]
                t["axis"] = max(c, key=lambda a: (c[a], a == 0))
            out.sort(key=lambda t: t["texture"].lower())
            self._ground_textures = out
        return self._ground_textures

    def add_ground_layer(self, map_name, texture, scale=None, axis=None):
        """A new ground texture layer on a map (up to 16), to paint with:
        written as the mod's terrain/<map>.layers.json, the whole layer table."""
        from .. import terrain_paint
        if not re.fullmatch(r"[A-Za-z0-9_]+", map_name):
            raise ValueError(f"bad map name {map_name!r}")
        texture = str(texture or "").strip()
        known = {t["texture"].lower(): t for t in self.ground_textures()}
        if texture.lower() not in known:
            raise ValueError(f"{texture!r} isn't a ground texture the game's maps use")
        meta, _, _ = self.terrain_look(map_name)
        table = [dict(t) for t in meta["layers"]]
        if len(table) >= terrain_paint.MAX_LAYERS:
            raise ValueError(f"{map_name} already has {terrain_paint.MAX_LAYERS} layers, the most a map can have")
        base = known[texture.lower()]
        table.append({"texture": base["texture"], "scale": float(scale if scale is not None else base["scale"]),
                      "axis": int(axis if axis is not None else base["axis"])})
        p = self.mod / "terrain" / f"{map_name.lower()}.layers.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"layers": table}, indent=1) + "\n", encoding="utf-8")
        self._forget_terrain(map_name)
        return {"file": f"terrain/{p.name}", "layers": table}

    def _base_layer_count(self, map_name):
        """How many ground layers the map has before any mod's terrain files
        (a new map: its source's): the ones that can't be removed."""
        from .. import modbuild, terrain_paint
        data = modbuild._Data(self.data)
        for p in self.context:
            if (Path(p) / "maps").is_dir():
                try:
                    modbuild._apply_maps(data, Path(p), modbuild.Result())
                except (ValueError, KeyError, OSError):
                    pass
        rec = _terrain_rec(data, map_name)
        if rec is None:
            raise KeyError(map_name)
        return terrain_paint.layer_count(terrain.tern_chunks(_terrain_bytes(data, rec)))

    def _write_layer_table(self, map_name, table):
        p = self.mod / "terrain" / f"{map_name.lower()}.layers.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"layers": table}, indent=1) + "\n", encoding="utf-8")
        self._forget_terrain(map_name)
        return f"terrain/{p.name}"

    def _paint_files(self, map_name, index):
        tdir = self.mod / "terrain"
        return sorted(tdir.glob(f"{map_name}.layer{index:02d}.*")) if tdir.is_dir() else []

    def change_ground_layer(self, map_name, index, texture):
        """Another texture for a ground layer (its paint stays): the layer
        table's entry, with the scale and projection the game gives it."""
        if not re.fullmatch(r"[A-Za-z0-9_]+", map_name):
            raise ValueError(f"bad map name {map_name!r}")
        known = {t["texture"].lower(): t for t in self.ground_textures()}
        if str(texture).lower() not in known:
            raise ValueError(f"{texture!r} isn't a ground texture the game's maps use")
        meta, _, _ = self.terrain_look(map_name)
        table = [dict(t) for t in meta["layers"]]
        index = int(index)
        if not 0 <= index < len(table):
            raise ValueError(f"{map_name} has no layer {index}")
        base = known[str(texture).lower()]
        table[index] = {"texture": base["texture"], "scale": float(base["scale"]), "axis": int(base["axis"])}
        for f in self._paint_files(map_name, index):        # its paint file named after the texture it paints
            parts = f.name.split(".")
            if len(parts) >= 4 and parts[1] == f"layer{index:02d}":
                parts[2] = base["texture"]
                f.rename(f.with_name(".".join(parts)))
        return {"file": self._write_layer_table(map_name, table), "layers": table}

    def remove_ground_layer(self, map_name, index):
        """Take a ground layer the mod added off the map, with its paint; the
        layers above it move down one (their paint files renumbered). The
        map's own layers can't go (paint them out instead)."""
        if not re.fullmatch(r"[A-Za-z0-9_]+", map_name):
            raise ValueError(f"bad map name {map_name!r}")
        meta, _, _ = self.terrain_look(map_name)
        table = [dict(t) for t in meta["layers"]]
        index = int(index)
        base = self._base_layer_count(map_name)
        if not base <= index < len(table):
            raise ValueError(f"layer {index} came with the map (its first {base}): it can't be removed, "
                             f"but you can paint it out or give it another texture"
                             if index < base else f"{map_name} has no layer {index}")
        for f in self._paint_files(map_name, index):
            f.unlink()
        for k in range(index + 1, len(table)):
            for f in self._paint_files(map_name, k):
                parts = f.name.split(".")
                parts[1] = f"layer{k - 1:02d}"
                f.rename(f.with_name(".".join(parts)))
        del table[index]
        return {"file": self._write_layer_table(map_name, table), "layers": table}

    def save_roads(self, map_name, roads):
        """Roads laid in the editor ([{texture, span, points: [[x, z]]}])
        added to the mod's terrain/<map>.decals.json "roads"."""
        import re
        if not re.fullmatch(r"[A-Za-z0-9_]+", map_name):
            raise ValueError(f"bad map name {map_name!r}")
        p = self.mod / "terrain" / f"{map_name}.decals.json"
        doc = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        for r in roads:
            pts = [[round(float(x), 2), round(float(z), 2)] for x, z in r["points"]]
            if len(pts) < 2 or not r.get("texture"):
                raise ValueError("a road needs a texture and two points or more")
            doc.setdefault("roads", []).append({"texture": r["texture"], "span": float(r.get("span", 25)),
                                                 "points": pts})
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        self._forget_terrain(map_name)
        return {"file": f"terrain/{p.name}", "roads": len(doc["roads"])}

    # ------------------------------------------------------------ missions
    # A mission is missions/<map>_<faction><n>.json; its soldiers, objects and
    # zones are in its layer, encounters/<same name>.json, which the mission
    # loads (mission_GenericInit). "objectives" make the build write its script.

    def _arc_numbers(self, map_name):
        """{faction: numbers taken}: the game's arcs and every source mod's missions."""
        from .. import missions as ms, script as sc
        taken = {f: set() for f in ms.FACTIONS}
        if not hasattr(self, "_arcs"):
            text = ""
            for a in self.archives:
                for r in a.records:
                    if r.kind == name_hash("script") and r.key == name_hash("util_misc"):
                        text = sc.decode(a.read(r)).source
            self._arcs = text
        for f in ms.FACTIONS:
            try:
                taken[f].update(ms.arc(self._arcs, f, map_name))
            except ValueError:
                pass
        for folder in list(self.context) + [self.mod]:
            for p in sorted((Path(folder) / "missions").glob("*.json")) if (Path(folder) / "missions").is_dir() else []:
                try:
                    m, f, n = ms.parse_id(p.stem)
                except ValueError:
                    continue
                if m == map_name.lower():
                    taken[f].add(n)
        return taken

    def height_at(self, map_name, x, z):
        """The ground's height at (x, z) on a map (bilinear, as drawn)."""
        meta, raw = self.terrain(map_name)
        n, sp = meta["size"], meta["spacing"]
        heights = struct.unpack(f"<{n * n}f", raw)
        fx, fz = (x - meta["x0"]) / sp, (z - meta["z0"]) / sp
        ix, iz = max(0, min(n - 2, int(fx))), max(0, min(n - 2, int(fz)))
        tx, tz = min(1.0, max(0.0, fx - ix)), min(1.0, max(0.0, fz - iz))
        h = lambda c, r: heights[r * n + c]
        top = h(ix, iz) * (1 - tx) + h(ix + 1, iz) * tx
        bot = h(ix, iz + 1) * (1 - tx) + h(ix + 1, iz + 1) * tx
        return top * (1 - tz) + bot * tz

    def make_test_mission(self, map_name, centre, faction="china", which=1):
        """A mission that exercises most mission features in one run, laid out
        around centre [x, y, z] on the map: its file and its layer (squads,
        objects, zones), at the faction's next free number. Debug messages on.
        Returns {"id"}."""
        from .. import missions as ms, sky as sk
        m = map_name.lower()
        if faction not in ms.FACTIONS:
            raise ValueError(f"no faction {faction!r}")
        number = max([8] + sorted(self._arc_numbers(m)[faction])) + 1
        aid = f"{m}_{faction}{number}"
        if (self.mod / "missions" / f"{aid}.json").exists():
            raise ValueError(f"{aid} already exists")
        names = {o["name"].lower() for o in self.all_objects() if o["name"]}
        need = ["template_A6_NK_Soldier", "template_civ_civilian01", "template_allies_officer",
                "template_allies_humvee_empty"] + (["template_pic_money"] if int(which) == 3 else [])
        missing = [t for t in need if t.lower() not in names]
        if missing:
            raise ValueError(f"the game data lacks {', '.join(missing)}")
        cx, _, cz = (float(v) for v in centre)

        def at(dx, dz):                 # a point on the ground, dx/dz metres from the centre
            x, z = cx + dx, cz + dz
            return [round(x, 2), round(self.height_at(m, x, z) + 0.3, 2), round(z, 2)]

        def square(dx, dz, r):          # a square zone
            return {"points": [at(dx - r, dz - r), at(dx + r, dz - r), at(dx + r, dz + r), at(dx - r, dz + r)]}
        enc = f"{aid}_enc"
        layer = {
            "map": m.upper(),
            "boundaries": {"ft_hold": square(40, 0, 8), "ft_deliver": square(-60, 30, 10),
                           "ft_ambush": square(70, 0, 12)},
            "encounters": [{"name": enc,
                            "points": {"A": [at(80, 10)], "B": [at(-20, 60)], "C": [at(110, -20)]},
                            "squads": [
                                {"name": "Guards", "points": ["A"],
                                 "members": [{"from": "template_A6_NK_Soldier", "name": f"{aid}_guard{k}",
                                              "position": at(80 + 3 * k, 10), "yaw": 270} for k in range(1, 4)]},
                                {"name": "Ambush", "points": ["C"],
                                 "members": [{"from": "template_A6_NK_Soldier", "name": f"{aid}_ambush{k}",
                                              "position": at(110 + 3 * k, -20), "yaw": 270} for k in range(1, 4)]}]}],
            "objects": [
                {"from": "template_allies_officer", "name": f"{aid}_contact", "position": at(30, 15), "yaw": 180},
                {"from": "template_A6_NK_Soldier", "name": f"{aid}_civ1", "position": at(-20, 60), "yaw": 0},
                {"from": "template_A6_NK_Soldier", "name": f"{aid}_civ2", "position": at(-25, 65), "yaw": 0},
                {"from": "template_allies_humvee_empty", "name": f"{aid}_humvee", "position": at(10, -12), "yaw": 90}],
        }
        squad = lambda s: f"{enc}:{s}"
        if int(which) in (2, 3):
            layer, doc = (self._test_mission_2 if int(which) == 2 else self._test_mission_3)(aid, enc, at, square, squad)
            (self.mod / "missions").mkdir(parents=True, exist_ok=True)
            (self.mod / "encounters").mkdir(parents=True, exist_ok=True)
            (self.mod / "missions" / f"{aid}.json").write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
            (self.mod / "encounters" / f"{aid}.json").write_text(json.dumps(layer, indent=1) + "\n", encoding="utf-8")
            return {"id": aid}
        presets = list(sk.presets_of(sk.level_texts(self.data, m)).values())
        preset = next((p["name"] for p in presets if p["name"].lower() == "dmz"), presets[0]["name"] if presets else None)
        doc = {
            "title": "Foundry feature test",
            "description": "Runs through the mission features one by one. Debug messages show each step.",
            "mail": {"sender": "mail.senders.techgal", "subject": "Contract: 'Foundry feature test'",
                     "body": "A test of the mission features. Follow the objectives."},
            "ini": {"ContractFee": 1000, "BonusFee": 500},
            "start": {"position": at(0, 0), "heading": 90},
            "debug": True, "keep_if_hostile": True,
            "mood": {faction: 10},
            "support": [{"item": "template_support_gunship", "count": 1}],
            "unlocks": ["template_support_deliverH2"],
            "done_mail": {"subject": "Feature test done", "body": "The test contract completed."},
            "objectives": [
                {"type": "goto", "text": "1. Go to the marked zone", "zone": "ft_hold"},
                {"type": "hold", "text": "2. Hold the zone for 20 seconds", "zone": "ft_hold", "seconds": 20},
                {"type": "destroy", "text": "3. Kill 2 of the 3 guards", "squad": squad("Guards"), "need": 2},
                {"type": "use", "text": "3b. Talk to the officer", "targets": [f"{aid}_contact"], "prompt": "Talk",
                 "together": True},
                {"type": "subdue", "text": "4. Knock out the lone NK soldier and carry him (capture alive)", "targets": [f"{aid}_civ1"]},
                {"type": "deliver", "text": "5. Drive the Humvee to the marked zone", "vehicle": f"{aid}_humvee",
                 "zone": "ft_deliver"},
                {"type": "verify", "text": "6. Verify the second lone NK soldier (photo or carry)", "targets": [f"{aid}_civ2"],
                 "blip": "spades", "alive": 2000},
                {"type": "fast", "text": "Bonus: finish within 15 minutes", "seconds": 900, "bonus": 3000},
                {"type": "destroy", "text": "Bonus: kill the ambush", "squad": squad("Ambush"), "bonus": 2000,
                 "reveal": f"dead:{squad('Guards')}"},
            ],
            "waves": [{"when": f"dead:{squad('Guards')}", "squad": squad("Ambush"), "delay": 5}],
            "orders": [{"when": f"dead:{squad('Guards')}", "squad": squad("Ambush"), "do": "attack", "delay": 8}],
            "radio": [{"when": "start", "text": "Feature test: follow the objectives."},
                      {"when": f"dead:{squad('Guards')}", "text": "Guards down: an ambush is coming.", "delay": 2},
                      {"when": "end", "text": "Feature test complete."}],
            "music": [{"when": "starts:3", "play": "music.Card_miniboss"}, {"when": "done:3", "play": "normal"}],
            "fail": [{"type": "timer", "seconds": 900, "from": "starts:3", "until": "done:4",
                      "text": "Too slow on step 3."}],
        }
        if preset:
            doc["weather"] = [{"when": "starts:2", "preset": preset}, {"when": "done:2", "preset": "normal"}]
        (self.mod / "missions").mkdir(parents=True, exist_ok=True)
        (self.mod / "encounters").mkdir(parents=True, exist_ok=True)
        (self.mod / "missions" / f"{aid}.json").write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        (self.mod / "encounters" / f"{aid}.json").write_text(json.dumps(layer, indent=1) + "\n", encoding="utf-8")
        return {"id": aid}

    def _test_mission_2(self, aid, enc, at, square, squad):
        """Feature test 2: escort, extract, squad orders (move, path, close in,
        hold), a placed giver that stays put and can't be hurt, and fail
        conditions armed to be set off on purpose (protect, stay in zone,
        turns hostile)."""
        layer = {
            "map": aid.split("_")[0].upper(),
            "boundaries": {"ft_meet": square(30, 0, 6), "ft_safe": square(-50, 40, 10),
                           "ft_hold": square(60, -40, 8), "ft_area": square(60, -40, 40)},
            "paths": {"ft_patrol": {"points": [at(10, -30), at(30, -30), at(30, -10), at(10, -10)]}},
            "encounters": [{"name": enc,
                            "points": {"A": [at(15, -20)], "B": [at(40, -55)], "C": [at(-20, 30)]},
                            "squads": [
                                {"name": "Friends", "points": ["A"],
                                 "members": [{"from": "template_allies_gunner", "name": f"{aid}_friend{k}",
                                              "position": at(15 + 3 * k, -20), "yaw": 90} for k in (1, 2)]},
                                {"name": "Escortees", "points": ["C"],
                                 "members": [{"from": "template_civ_civilian01", "name": f"{aid}_escortee{k}",
                                              "position": at(-20 + 2 * k, 30), "yaw": 0} for k in (1, 2)]}]}],
            "objects": [
                {"from": "template_A6_NK_Soldier", "name": f"{aid}_target", "position": at(70, 10), "yaw": 180},
                {"from": "template_allies_officer", "name": f"{aid}_officer", "position": at(33, 4), "yaw": 180}],
        }
        doc = {
            "title": "Foundry feature test 2",
            "description": "Escort, extract, squad orders, a placed giver and fail conditions to set off on purpose.",
            "mail": {"sender": "mail.senders.techgal", "subject": "Contract: 'Foundry feature test 2'",
                     "body": "Play it through once, then use Start at (testing) to set off each fail condition."},
            "ini": {"ContractFee": 1000, "BonusFee": 0},
            "start": {"position": at(0, 0), "heading": 90},
            "debug": True, "keep_if_hostile": True,
            "contact": {"template": "template_civ_civilian01", "name": "Test contact", "prompt": "Start feature test 2",
                        "position": at(-6, 4), "heading": 90, "stay": True, "invincible": True},
            "objectives": [
                {"type": "goto", "text": "1. Meet at the marked zone", "zone": "ft_meet"},
                {"type": "escort", "text": "2. Escort the civilians to the safe zone (at least 1)",
                 "squad": squad("Escortees"), "zone": "ft_safe", "need": 1},
                {"type": "subdue", "text": "3. Knock out the NK soldier, carry him and call the extraction",
                 "targets": [f"{aid}_target"],
                 "extract": True},
                {"type": "hold", "text": "4. Hold the area for 30 seconds (leaving the larger area fails it)",
                 "zone": "ft_hold", "seconds": 30},
                {"type": "goto", "text": "5. Back to the meeting zone (shooting the officer fails it)", "zone": "ft_meet"},
            ],
            "orders": [
                {"when": "start", "squad": squad("Friends"), "do": "move", "to": "B"},
                {"when": "starts:2", "squad": squad("Friends"), "do": "path", "to": "ft_patrol"},
                {"when": "starts:3", "squad": squad("Friends"), "do": "close"},
                {"when": "done:3", "squad": squad("Friends"), "do": "hold"},
            ],
            "radio": [
                {"when": "start", "text": "Test 2: the friendly squad moves to its point B."},
                {"when": "starts:2", "text": "The friendly squad walks its patrol path."},
                {"when": "starts:3", "text": "The friendly squad closes in on you."},
                {"when": "done:3", "text": "The friendly squad holds where it is."},
                {"when": "end", "text": "Feature test 2 complete."},
            ],
            "fail": [
                {"type": "dies", "squad": squad("Escortees"), "until": "done:2",
                 "text": "An escortee died (protect squad works)."},
                {"type": "leave", "zone": "ft_area", "from": "zone:ft_area", "until": "done:4",
                 "text": "You left the area (stay in zone works)."},
                {"type": "hostile", "targets": [f"{aid}_officer"], "from": "starts:5",
                 "text": "The officer turned hostile (turns hostile works)."},
            ],
        }
        return layer, doc

    def _test_mission_3(self, aid, enc, at, square, squad):
        """Feature test 3: the newer objective types. Defend civilians while
        a clock runs (an NK squad arrives to attack), collect money pickups,
        and a chase: a runner walks a path to a zone, and reaching it fails
        the mission."""
        layer = {
            "map": aid.split("_")[0].upper(),
            "boundaries": {"ft_escape": square(-90, -60, 8)},
            "paths": {"ft_run": {"points": [at(60, -30), at(20, -45), at(-30, -55), at(-90, -60)]}},
            "encounters": [{"name": enc,
                            "points": {"A": [at(25, 20)], "B": [at(70, 40)], "C": [at(60, -30)]},
                            "squads": [
                                {"name": "Civilians", "points": ["A"],
                                 "members": [{"from": "template_civ_civilian01", "name": f"{aid}_civ{k}",
                                              "position": at(22 + 3 * k, 20), "yaw": 180} for k in (1, 2, 3)]},
                                {"name": "Attackers", "points": ["B"],
                                 "members": [{"from": "template_A6_NK_Soldier", "name": f"{aid}_attacker{k}",
                                              "position": at(70 + 3 * k, 40), "yaw": 200} for k in (1, 2, 3)]},
                                {"name": "Runner", "points": ["C"],
                                 "members": [{"from": "template_A6_NK_Soldier", "name": f"{aid}_runner",
                                              "position": at(60, -30), "yaw": 250}]}]}],
            "objects": [{"from": "template_pic_money", "name": f"{aid}_cash{k}", "position": at(dx, dz), "yaw": 0}
                        for k, (dx, dz) in enumerate([(-15, 10), (-25, 25), (-10, 35)], 1)],
        }
        doc = {
            "title": "Foundry feature test 3",
            "description": "Defend, collect and a chase (a target that fails the mission if he gets away).",
            "mail": {"sender": "mail.senders.techgal", "subject": "Contract: 'Foundry feature test 3'",
                     "body": "Play it through once; play it again and let the civilians die or the runner escape."},
            "ini": {"ContractFee": 1000, "BonusFee": 0},
            "start": {"position": at(0, 0), "heading": 90},
            "debug": True, "keep_if_hostile": True,
            "objectives": [
                {"type": "defend", "text": "1. Keep at least 2 of the 3 civilians alive for 60 seconds",
                 "squad": squad("Civilians"), "seconds": 60, "need": 2},
                {"type": "collect", "text": "2. Collect 2 of the 3 money pickups",
                 "targets": [f"{aid}_cash{k}" for k in (1, 2, 3)], "need": 2},
                {"type": "destroy", "text": "3. Stop the runner before he reaches the marked zone",
                 "squad": squad("Runner")},
            ],
            "waves": [{"when": "starts:1", "squad": squad("Attackers"), "delay": 10}],
            "orders": [{"when": "starts:1", "squad": squad("Attackers"), "do": "attack", "delay": 12},
                       {"when": "starts:3", "squad": squad("Runner"), "do": "path", "to": "ft_run"}],
            "radio": [{"when": "start", "text": "Test 3: an NK squad is coming for the civilians. Hold them off."},
                      {"when": "starts:2", "text": "Now pick up the money."},
                      {"when": "starts:3", "text": "A runner is heading for the marked zone. Stop him."},
                      {"when": "end", "text": "Feature test 3 complete."}],
            "fail": [{"type": "escapes", "squad": squad("Runner"), "zone": "ft_escape", "from": "starts:3",
                      "until": "done:3", "text": "The runner got away (target escapes works)."}],
        }
        return layer, doc

    def _new_map(self, map_name):
        """Whether the map is one of the mods' new maps (maps/<name>.json)."""
        return any((Path(p) / "maps" / f"{map_name.lower()}.json").exists() for p in getattr(self, "context", [])) or \
            (self.mod / "maps" / f"{map_name.lower()}.json").exists()

    def mission_arc(self, map_name):
        """The arc a map's missions are in: its own (SW, NW), or for a new map
        SW's (the game's mission code runs them as SW contracts; "map" says
        they're played on the new map)."""
        return "sw" if self._new_map(map_name) else map_name.lower()

    def missions(self, map_name):
        """The mod's missions on this map, each with its layer's squads and
        zones, and the next free number per faction. On a new map: the
        missions played there ("map"), numbered in SW's arc."""
        from .. import missions as ms
        m = map_name.lower()
        arc_map = self.mission_arc(m)
        out = []
        mdir = self.mod / "missions"

        def layer_names(doc):
            # "encounter:squad": squad names repeat across encounters.
            squads = [f"{e['name']}:{s['name']}" for e in doc.get("encounters", []) for s in e.get("squads", [])]
            squads += [f"{s['encounter']}:{s['name']}" for s in doc.get("game_squads", [])]
            return squads, sorted((doc.get("boundaries") or {}).keys())

        def layer_extras(doc):
            # Paths (squad orders walk them) and each squad's encounter's point
            # sets (squad orders move to them).
            sets = {f"{e['name']}:{s['name']}": sorted((e.get("points") or {}).keys())
                    for e in doc.get("encounters", []) for s in e.get("squads", [])}
            return sorted((doc.get("paths") or {}).keys()), sets

        # The mod's layers that load with the map: their squads and zones are
        # there during any mission on it too.
        shared_squads, shared_zones, shared_paths, shared_sets = [], [], [], {}
        edir = self.mod / "encounters"
        for lp in sorted(edir.glob("*.json")) if edir.is_dir() else []:
            ldoc = json.loads(lp.read_text(encoding="utf-8"))
            if (ldoc.get("map") or "").lower() == m and ldoc.get("load") == "map":
                sq, zn = layer_names(ldoc)
                shared_squads += sq
                shared_zones += zn
                pa, se = layer_extras(ldoc)
                shared_paths += pa
                shared_sets.update(se)
        for p in sorted(mdir.glob("*.json")) if mdir.is_dir() else []:
            try:
                mp, faction, number = ms.parse_id(p.stem)
            except ValueError:
                continue
            doc = json.loads(p.read_text(encoding="utf-8"))
            plays = ms.play_map(p.stem, doc) or mp          # where it's played
            if plays != m:
                continue
            lp = self.mod / "encounters" / f"{p.stem}.json"
            ldoc = json.loads(lp.read_text(encoding="utf-8")) if lp.exists() else {}
            squads, zones = layer_names(ldoc)
            paths, sets = layer_extras(ldoc)
            out.append({"id": p.stem, "faction": faction, "number": number, "doc": doc,
                        "squads": squads + [s for s in shared_squads if s not in squads],
                        "zones": zones + [z for z in shared_zones if z not in zones],
                        "paths": paths + [x for x in shared_paths if x not in paths], "sets": {**shared_sets, **sets},
                        "own": squads,
                        "script": (self.mod / "scripts" / f"{p.stem}.lua").exists()})
        taken = self._arc_numbers(arc_map)
        nxt = {f: max([8] + sorted(taken[f])) + 1 for f in ms.FACTIONS}
        return {"missions": out, "next": nxt, "factions": list(ms.FACTIONS), "arc": arc_map,
                "squads": shared_squads, "zones": shared_zones, "paths": shared_paths, "sets": shared_sets,
                "hq": self._hq_points(m)}

    def bullet_crown(self, model_name, on):
        """Whether bullets hit a tree's branches: models/<model>.collision.json
        "crown" (a box over the branches only bullets hit; the build adds it)."""
        if not model_name or not all(c.isalnum() or c in "_-" for c in model_name):
            raise ValueError(f"bad model name {model_name!r}")
        p = self.mod / "models" / f"{model_name}.collision.json"
        doc = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        if on:
            doc["crown"] = True
        else:
            doc.pop("crown", None)
        if doc:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        elif p.exists():
            p.unlink()
        return {"model": model_name, "crown": bool(on), "file": f"models/{p.name}"}

    def bullet_crowns(self):
        """Models whose branches bullets hit (their collision.json "crown")."""
        d = self.mod / "models"
        out = []
        for p in sorted(d.glob("*.collision.json")) if d.is_dir() else []:
            try:
                if json.loads(p.read_text(encoding="utf-8")).get("crown"):
                    out.append(p.name[:-len(".collision.json")])
            except ValueError:
                continue
        return out

    def new_model(self, name, base, glb):
        """A new object's model: writes models/<name>.glb (glb: the file's
        bytes) and models/<name>.new.json naming the game model it's built on.
        The build makes the model (modbuild._new_models)."""
        import re
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", name or ""):
            raise ValueError("a model name is letters, digits, _ and -")
        if any(r.key == name_hash(name) and r.kind == model.MODEL for a in self.archives for r in a.records):
            raise ValueError(f"the game already has a model named {name!r}: pick another name")
        if glb[:4] != b"glTF":
            raise ValueError("that isn't a .glb file (binary glTF)")
        try:
            model.find(self.archives, base)
        except Exception:
            raise ValueError(f"no game model {base!r} to build it on") from None
        d = self.mod / "models"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.glb").write_bytes(glb)
        (d / f"{name}.new.json").write_text(json.dumps({"base": base}, indent=1) + "\n", encoding="utf-8")
        self._models.pop(name, None)
        return {"files": [f"models/{name}.glb", f"models/{name}.new.json"]}

    def sky(self, map_name):
        """A map's sky and weather: its base sky settings and atmosphere
        presets as the game has them, and the mod's changes (sky/<map>.json)."""
        from .. import sky as sk
        texts = sk.level_texts(self.data, map_name) or sk.level_texts(self.data, map_name, self._modded_terrain().files)
        if not texts:
            return {"level": False}
        files = list(texts)
        p = self.mod / "sky" / f"{map_name.lower()}.json"
        mod = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        presets = [{"name": v["name"], "file": v["file"][len("DATAxbox/"):], "keys": [list(kv) for kv in v["keys"]]}
                   for v in sk.presets_of(texts).values()]
        # the sky textures the game's presets use (for picking one)
        if not hasattr(self, "_sky_textures"):
            seen = set()
            for m in ("sw", "swn", "nw", "aclubs", "admnd", "aheart", "aspade", "shl"):
                for v in sk.presets_of(sk.level_texts(self.data, m)).values():
                    seen |= {val for k, val in v["keys"] if k.lower() == "skytexture"}
                for t in sk.level_texts(self.data, m).values():
                    seen |= {v for k, v in sk.base(t).items() if k.lower() == "skytexture"}
            self._sky_textures = sorted(seen, key=str.lower)
        # The config's season sections ([Winter], [Fall], [Summer]: blank's and so
        # every new map's), each as its lines in order (ProcessRGBA repeats).
        seasons, cur = [], None
        for line in sk._lines(texts[files[0]]):
            s_ = line.strip()
            if s_.startswith("["):
                name = s_.strip("[]").strip()
                cur = next((n for n in sk.SEASONS if n.lower() == name.lower()), None)
                if cur:
                    seasons.append({"name": cur, "keys": []})
                continue
            parts = s_.split(None, 1)
            if cur and len(parts) == 2:
                seasons[-1]["keys"].append([parts[0], parts[1].strip()])
        from .. import terrain_light
        lp = self.mod / "terrain" / f"{map_name.lower()}.lighting.json"
        tdir = self.mod / "terrain"
        # PNGs a tint map could be: the mod's terrain images that aren't heights or paint layers
        tint_maps = sorted(f.name for f in tdir.glob("*.png") if not f.name.lower().endswith(".heights.png")
                           and ".layer" not in f.name.lower()) if tdir.is_dir() else []
        lighting = {"doc": json.loads(lp.read_text(encoding="utf-8")) if lp.exists() else None,
                    "file": f"terrain/{lp.name}", "tint_maps": tint_maps,
                    "defaults": {"ambient": list(terrain_light.DEFAULT_AMBIENT),
                                 "diffuse": list(terrain_light.DEFAULT_DIFFUSE), "shadow": terrain_light.DEFAULT_SHADOW}}
        return {"level": True, "files": [f[len("DATAxbox/"):] for f in files], "base": sk.base(texts[files[0]]),
                "seasons": seasons, "lighting": lighting,
                "presets": presets, "mod": mod, "textures": self._sky_textures,
                "preset_keys": list(sk.PRESET_KEYS), "base_keys": list(sk.BASE_KEYS)}

    def _sun_angle(self, map_name, base=None):
        """The map's SunAngle: base (the Sky panel's edits) or the level config's."""
        from .. import sky as sk
        for k, v in (base or {}).items():
            if k.lower() == "sunangle" and str(v).strip():
                return str(v)
        texts = sk.level_texts(self.data, map_name) or sk.level_texts(self.data, map_name, self._modded_terrain().files)
        for k, v in (sk.base(next(iter(texts.values()))) if texts else {}).items():
            if k.lower() == "sunangle":
                return v
        return None

    def lighting_preview(self, map_name, lighting, base=None):
        """The ground lit from a chosen sun (terrain/<map>.lighting.json's
        settings, with the Sky panel's SunAngle), as terrain_look's light bytes:
        what the build will bake."""
        from .. import terrain_layers as tl, terrain_light
        settings = terrain_light.lighting_settings(lighting or {}, self._sun_angle(map_name, base))
        data = self._modded_terrain()
        rec = _terrain_rec(data, map_name)
        if rec is None:
            raise KeyError(map_name)
        chunks = terrain.tern_chunks(_terrain_bytes(data, rec))
        tint_rgb = None
        if settings.get("tint_map"):
            tp = self.mod / "terrain" / settings["tint_map"]
            if not tp.exists():
                raise ValueError(f"the tint map terrain/{tp.name} is missing")
            tint_rgb = terrain_light.load_tint_map(tp, terrain.parse_info(chunks[b"INFO"])["size"])
        body, note = terrain_light.bake(chunks, settings, tint_rgb)
        size = terrain.parse_info(chunks[b"INFO"])["size"]
        side = size // tl.PATCH
        rgb = terrain_light.vertex_rgb(tl.decode_colr(body, side * side), size)
        return b"".join(bytes(c) for row in rgb for c in row), note

    def save_lighting(self, map_name, lighting, base=None):
        """terrain/<map>.lighting.json (None removes it), checked with the map's SunAngle."""
        from .. import terrain_light
        p = self.mod / "terrain" / f"{map_name.lower()}.lighting.json"
        if lighting is None:
            if p.exists():
                p.unlink()
            return None
        terrain_light.lighting_settings(lighting, self._sun_angle(map_name, base))      # raises if the build would refuse it
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(lighting, indent=1) + "\n", encoding="utf-8")
        return f"terrain/{p.name}"

    def save_sky(self, map_name, doc, lighting="keep"):
        """Write sky/<map>.json (checked by applying it to the game's files);
        an empty one is removed. lighting: terrain/<map>.lighting.json's
        settings, None to remove it, or "keep"."""
        from .. import sky as sk
        if isinstance(lighting, dict):          # checked before anything is written
            from .. import terrain_light
            terrain_light.lighting_settings(lighting, self._sun_angle(map_name, doc.get("base")))
        texts = sk.level_texts(self.data, map_name) or sk.level_texts(self.data, map_name, self._modded_terrain().files)
        if not texts:
            raise ValueError(f"{map_name} isn't a level with sky settings")
        clean = {}
        if doc.get("base"):
            clean["base"] = {k: v for k, v in doc["base"].items() if str(v).strip()}
        presets = {n: {k: v for k, v in keys.items() if (v if isinstance(v, list) else str(v).strip())}
                   for n, keys in (doc.get("presets") or {}).items()}
        presets = {n: keys for n, keys in presets.items() if keys}
        if presets:
            clean["presets"] = presets
        seasons = {n: {k: v for k, v in keys.items() if (v if isinstance(v, list) else str(v).strip())}
                   for n, keys in (doc.get("seasons") or {}).items()}
        seasons = {n: keys for n, keys in seasons.items() if keys}
        if seasons:
            clean["seasons"] = seasons
        clean = {k: v for k, v in clean.items() if v}
        sk.apply(texts, clean, f"sky/{map_name.lower()}.json")        # raises on anything the build would refuse
        p = self.mod / "sky" / f"{map_name.lower()}.json"
        if clean:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(clean, indent=1) + "\n", encoding="utf-8")
        elif p.exists():
            p.unlink()
        out = {"file": f"sky/{p.name}", "empty": not clean}
        if lighting != "keep":
            out["lighting"] = self.save_lighting(map_name, lighting, clean.get("base"))
        if lighting != "keep" or (self.mod / "terrain" / f"{map_name.lower()}.lighting.json").exists():
            self._forget_terrain(map_name.lower())          # the ground is lit from this sun: bake it again
        return out

    def factions(self):
        """The mod's factions.json and the game's names for the four employers."""
        from .. import factions as fx
        p = self.mod / "factions.json"
        doc = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        people, models = self.faction_people()
        return {"doc": doc, "factions": list(fx.FACTIONS),
                "old": {f: [o for o, _ in fx.OLD[f]] for f in fx.FACTIONS},
                "people": people, "models": models, "vehicles": self.faction_vehicles()}

    VEHICLE_KINDS = ("jeep", "tank", "helicopter", "truck", "trucklarge", "airplane", "boat")

    def _joints(self, model_name):
        """A model's joint names (cached); [] if it can't be read."""
        if not hasattr(self, "_joint_cache"):
            self._joint_cache = {}
        key = model_name.lower()
        if key not in self._joint_cache:
            try:
                self._joint_cache[key] = [j.name for j in model.decode(*model.find(self.archives, model_name)).joints]
            except Exception:
                self._joint_cache[key] = []
        return self._joint_cache[key]

    def faction_vehicles(self):
        """Each employer's vehicles: {faction: [{model, kind, templates,
        shared, can: [models of the same kind with all its working joints
        (seats, doors, wheels, guns, rotors, treads)]}]}, most used first."""
        from collections import Counter
        from .. import factions as fx
        by, kinds = {}, {}
        for t in self.templates():
            kind = t["type"].lower()
            # vehicle models only (an air strike's "airplane" may draw a pickup, shared by all)
            if kind in self.VEHICLE_KINDS and t["geometry"] and "_veh_" in t["geometry"].lower():
                by.setdefault(t["faction"].lower(), Counter())[t["geometry"]] += 1
                kinds.setdefault(t["geometry"], kind)
        everyone = sorted(kinds, key=str.lower)
        out = {}
        for f in fx.FACTIONS:
            rows = []
            for m, n in (by.get(f) or Counter()).most_common():
                mine = self._joints(m)
                can = [o for o in everyone if o.lower() != m.lower() and kinds[o] == kinds[m] and self._joints(o)
                       and not fx.missing_joints(mine, self._joints(o))]
                rows.append({"model": m, "kind": kinds[m], "templates": n, "can": can,
                             "shared": sorted(g for g, c in by.items() if g != f and m in c)})
            out[f] = rows
        return out

    @staticmethod
    def model_role(model_name):
        """A character model's role, for matching one faction's to another's:
        soldier, heavy, elite, officer, or what its name says after _hum_."""
        role = model_name.lower().split("_hum_", 1)[-1]
        if role.startswith("heavy"):
            return "heavy"
        if "elite" in role:
            return "elite"
        if role in ("soldier", "mgsoldier"):
            return "soldier"
        return role

    def faction_people(self):
        """Each employer's people: the models its human templates draw
        ({faction: [{model, role, templates, shared}]}, most used first; shared:
        other factions whose templates draw it too), and every character model
        a look can use (the game's and the mod's new ones)."""
        from collections import Counter
        from .. import factions as fx
        by = {}
        for t in self.templates():
            if t["type"].lower() == "human" and t["geometry"]:
                by.setdefault(t["faction"].lower(), Counter())[t["geometry"]] += 1
        people = {}
        for f in fx.FACTIONS:
            people[f] = [{"model": m, "role": self.model_role(m), "templates": n,
                          "shared": sorted(g for g, c in by.items() if g != f and m in c)}
                         for m, n in (by.get(f) or Counter()).most_common()]
        models = sorted({m for c in by.values() for m in c}, key=str.lower)
        mdir = self.mod / "models"
        models += sorted(p.name[:-len(".new.json")] for p in mdir.glob("*.new.json")) if mdir.is_dir() else []
        return people, models

    def _english(self):
        if not hasattr(self, "_strings"):
            from .. import registry
            self._strings = {}
            for a in self.archives:
                if a.path.name.lower() != "english.dsk":
                    continue
                for r in a.records:
                    if r.kind == name_hash("registry"):
                        try:
                            self._strings.update(registry.Registry(a.read(r)).strings())
                        except Exception:
                            continue
        return self._strings

    def faction_preview(self, doc):
        """What factions.json (doc) would change: {"count", "samples": [[key, old, new]]}."""
        from .. import factions as fx
        strings = self._english()
        edits = fx.plan(strings, doc)
        keys = sorted(edits, key=lambda k: (not k.lower().startswith("interface"), k.lower()))
        return {"count": len(edits), "samples": [[k, strings[k], edits[k]] for k in keys[:40]]}

    def save_factions(self, doc):
        from .. import factions as fx
        fx.plan(self._english(), doc)                       # raises on what the build would refuse
        fx.looks(doc)
        clean = {}
        for f in fx.FACTIONS:
            spec = doc.get(f) or {}
            names = {k: str(v).strip() for k, v in spec.items() if k != "looks" and str(v).strip()}
            looks = {str(o).strip(): str(n).strip() for o, n in (spec.get("looks") or {}).items()
                     if str(o).strip() and str(n).strip() and str(o).strip().lower() != str(n).strip().lower()}
            entry = names if names.get("name") else {}
            if looks:
                entry["looks"] = looks
            if entry:
                clean[f] = entry
        named = any(v.get("name") for v in clean.values())
        if named and doc.get("everywhere"):
            clean["everywhere"] = True
        if named and doc.get("subtitles"):
            clean["subtitles"] = True
        p = self.mod / "factions.json"
        if clean:
            p.write_text(json.dumps(clean, indent=1) + "\n", encoding="utf-8")
        elif p.exists():
            p.unlink()
        return {"file": "factions.json", "empty": not clean}

    def music_tracks(self):
        """The game's music tracks (the music sound bank's cues): ["music.Card_Ace", ...]."""
        try:
            return [f"music.{c.name}" for c in self.sound_library().sound_bank("music").cues]
        except KeyError:
            return []

    def voice_lines(self):
        """The game's voiced lines a radio line can play: [{"cue": "fio.01011FIO",
        "text": subtitle}], from the subtitles (vo.<bank>.<cue>) whose cue is in
        its sound bank, plus the mod's own new ones (sounds/<bank>/new/<cue>.wav,
        "mod": true)."""
        if not hasattr(self, "_voices"):
            from .. import registry, xsb
            subs = {}
            for a in self.archives:
                if a.path.name.lower() != "english.dsk":
                    continue
                for r in a.records:
                    if r.kind == name_hash("registry"):
                        try:
                            strings = registry.Registry(a.read(r)).strings()
                        except Exception:          # not a text registry
                            continue
                        for k, v in strings.items():
                            if k.lower().startswith("vo.") and k.count(".") >= 2:
                                subs.setdefault(k.lower(), v)
            lib = self.sound_library()
            out, seen = [], set()
            for key in sorted(subs):
                _, bank, cue = key.split(".", 2)
                try:
                    c = lib.sound_bank(bank).cue(cue)
                except KeyError:
                    continue
                if c and (bank, c.name.lower()) not in seen:
                    seen.add((bank, c.name.lower()))
                    out.append({"cue": f"{bank}.{c.name}", "text": subs[key]})
            self._voices = out
        own = [{"cue": f"{p.parent.parent.name.lower()}.{p.stem}", "text": "", "mod": True}
               for p in sorted((self.mod / "sounds").glob("*/new/*.wav"))] if (self.mod / "sounds").is_dir() else []
        return own + self._voices

    def sound_library(self):
        if not hasattr(self, "_sound_lib"):
            from .. import xsb
            self._sound_lib = xsb.Library(self.data)
        return self._sound_lib

    def voice_wav(self, cue):
        """A voice line as a .wav to listen to: the mod's own new one, or the
        game's (its first wave)."""
        from .. import sounds
        bank, _, name = cue.partition(".")
        if not bank or not name or "/" in cue or "\\" in cue:
            raise ValueError(f"no voice {cue!r}")
        own = self.mod / "sounds" / bank / "new" / f"{name}.wav"
        if own.is_file():
            return own.read_bytes()
        lib = self.sound_library()
        try:
            c = lib.sound_bank(bank).cue(name)
        except KeyError:
            c = None
        waves = lib.sound_bank(bank).cue_waves(c) if c else []
        if not waves:
            raise ValueError(f"no voice {cue!r}")
        return sounds.decoded(lib, *waves[0])

    def _mission_titles(self):
        """{string key: text} of the game's mission titles (MissionInfo.MissionTitle.*)."""
        if not hasattr(self, "_titles"):
            from .. import registry
            self._titles = {}
            for a in self.archives:
                if a.path.name.lower() != "english.dsk":
                    continue
                for r in a.records:
                    if r.kind == name_hash("registry"):
                        try:
                            strings = registry.Registry(a.read(r)).strings()
                        except Exception:          # not a text registry
                            continue
                        self._titles.update({k: v for k, v in strings.items()
                                             if k.lower().startswith("missioninfo.missiontitle.")})
        return self._titles

    def _hq_points(self, map_name):
        """For the giver's "In the faction's HQ contract sequence": per faction,
        its retail contracts on the map in arc order ({pos, number, chapter,
        title}) and the points a mission can go ({after, chapter, default,
        label}; hqsequence.plan accepts each)."""
        from .. import missions as ms, hqsequence as hq
        self._arc_numbers(map_name)                      # reads util_misc once
        if map_name not in hq.MAP_CHAPTERS:
            return {}
        ini_path = self.data / "mission.ini"
        ini = ini_path.read_bytes().decode("latin-1") if ini_path.exists() else ""
        titles = self._mission_titles()
        out = {}
        for f in ms.FACTIONS:
            try:
                arc = ms.arc(self._arcs, f, map_name)
            except ValueError:
                continue
            contracts = hq.retail_contracts(arc, ini, map_name, f, titles)
            points = []
            for after in range(0, len(contracts) + 1):
                try:
                    default = hq.plan(f"{map_name}_{f}0", {"hq": {"after": after}}, ms.parse_id)["chapter"]
                except ValueError:
                    default = None
                for chapter in hq.MAP_CHAPTERS[map_name]:
                    try:
                        hq.plan(f"{map_name}_{f}0", {"hq": {"after": after, "chapter": chapter}}, ms.parse_id)
                    except ValueError:
                        continue
                    if after == 0:
                        what = "Before contract 1"
                        c = contracts[0] if contracts else None
                    else:
                        what = f"After contract {after}"
                        c = contracts[after - 1]
                    if c and c.get("title"):
                        what += f" ({c['title']})"
                    when = (f"start of chapter {chapter}" if after and c and c["chapter"] != chapter
                            else f"chapter {chapter}")
                    points.append({"after": after, "chapter": chapter, "default": chapter == default,
                                   "label": f"{what}, {when}"})
            out[f] = {"contracts": contracts, "points": points}
        return out

    def duplicate_mission(self, asset_id, faction=None):
        """Copy a mission and its layer to the next free number (of its own
        faction, or another). Names made from the id (its encounters, squads
        and people, "sw_china9encounter1") take the new id. A giver placed on
        the map or a place in an HQ sequence isn't copied: the copy is offered
        by an HQ contact. Returns {"id", "notes"}."""
        import re
        from .. import missions as ms
        old = asset_id.lower()
        map_, own, _ = ms.parse_id(old)
        faction = faction or own
        if faction not in ms.FACTIONS:
            raise ValueError(f"no faction {faction!r}")
        src = self.mod / "missions" / f"{old}.json"
        if not src.exists():
            raise ValueError(f"no mission {old}")
        taken = self._arc_numbers(map_)[faction]
        number = max([8] + sorted(taken)) + 1
        new = f"{map_}_{faction}{number}"
        swap = re.compile(rf"(?<![A-Za-z0-9]){re.escape(old)}(?![0-9])", re.IGNORECASE)
        doc = json.loads(swap.sub(new, src.read_text(encoding="utf-8")))
        notes = []
        doc["title"] = f"{doc.get('title') or old} (copy)"
        if (doc.get("mail") or {}).get("subject"):
            doc["mail"]["subject"] = f"Contract: '{doc['title']}'"
        if doc.pop("hq", None):
            notes.append("its place in the HQ contract sequence isn't copied")
        contact = doc.get("contact")
        if isinstance(contact, dict) and contact.get("position"):
            doc.pop("contact")
            notes.append("its giver placed on the map isn't copied: an HQ contact offers the copy")
        if faction != own and doc.get("mood"):
            notes.append("check its mood changes: the contract's faction changed")
        (self.mod / "missions" / f"{new}.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        lsrc = self.mod / "encounters" / f"{old}.json"
        if lsrc.exists():
            (self.mod / "encounters" / f"{new}.json").write_text(swap.sub(new, lsrc.read_text(encoding="utf-8")),
                                                                  encoding="utf-8")
        if (self.mod / "scripts" / f"{old}.lua").exists():
            notes.append(f"scripts/{old}.lua (the mod's own script) isn't copied")
        return {"id": new, "notes": notes}

    def delete_mission(self, asset_id):
        """Move a mission's files (missions/, its layer, its own script) to
        .trash/<id>-<time>/ in the mod: the build no longer sees them, and
        moving them back undoes it. Returns {"moved", "trash", "required_by"}."""
        import time
        from .. import missions as ms
        aid = asset_id.lower()
        ms.parse_id(aid)
        files = [p for p in (self.mod / "missions" / f"{aid}.json", self.mod / "encounters" / f"{aid}.json",
                             self.mod / "scripts" / f"{aid}.lua") if p.exists()]
        if not files or files[0].parent.name != "missions":
            raise ValueError(f"no mission {aid}")
        trash = self.mod / ".trash" / f"{aid}-{time.strftime('%Y%m%d-%H%M%S')}"
        moved = []
        for p in files:
            rel = p.relative_to(self.mod)
            (trash / rel.parent).mkdir(parents=True, exist_ok=True)
            p.replace(trash / rel)
            moved.append(rel.as_posix())
        required_by = []
        for p in sorted((self.mod / "missions").glob("*.json")):
            if aid in [str(r).lower() for r in json.loads(p.read_text(encoding="utf-8")).get("requires") or []]:
                required_by.append(p.stem)
        return {"moved": moved, "trash": trash.relative_to(self.mod).as_posix(), "required_by": required_by}

    def check_mission(self, asset_id):
        """The build's checks for a saved mission (modbuild.check_mission),
        and characters to capture that can't be carried."""
        from ..modbuild import check_mission
        out = check_mission(self.mod, asset_id.lower(), self.lua_names())
        try:
            out["notes"] += self._carry_notes(asset_id.lower())
        except Exception as e:                     # a check that can't run doesn't hold up the rest
            out["notes"].append(f"(couldn't check who can be carried: {e})")
        return out

    def _carryable(self, table):
        """Whether an animation table has the "carried" clips (a captive on
        the player's back: retail's nk_soldier_ehuman has them, nk_civilian
        doesn't, so a civilian drags along the ground)."""
        if not hasattr(self, "_carry_tables"):
            self._carry_tables = {}
        low = (table or "").lower()
        if low not in self._carry_tables:
            from .. import animtable
            doc = None
            for a in self.archives:
                for r in a.records:
                    if r.kind == name_hash("animtable") and r.key == name_hash(low):
                        doc = animtable.to_json(a.read(r))
                        break
                if doc:
                    break
            self._carry_tables[low] = None if doc is None else any(
                "carry_passenger" in c.lower() for c in animtable.clips(doc))
        return self._carry_tables[low]

    def _carry_notes(self, aid):
        """Capture, extract and verify targets whose animation table can't be carried."""
        p = self.mod / "missions" / f"{aid}.json"
        if not p.exists():
            return []
        doc = json.loads(p.read_text(encoding="utf-8"))
        lp = self.mod / "encounters" / f"{aid}.json"
        layer = json.loads(lp.read_text(encoding="utf-8")) if lp.exists() else {}
        # the layer's people: name -> (template, its set)
        people, squads = {}, {}
        for e in layer.get("encounters") or []:
            for s in e.get("squads") or []:
                for mbr in s.get("members") or []:
                    people[mbr["name"].lower()] = (mbr.get("from"), mbr.get("set") or {})
                    squads.setdefault(f"{e['name']}:{s['name']}".lower(), []).append(mbr["name"].lower())
        for o in layer.get("objects") or []:
            people[o["name"].lower()] = (o.get("from"), o.get("set") or {})
        by_name = {o["name"].lower(): o for o in self.all_objects() if o["name"]}
        notes = []
        for i, o in enumerate(doc.get("objectives") or [], 1):
            if o.get("type") not in ("subdue", "verify"):
                continue
            names = [t.lower() for t in o.get("targets") or []] + squads.get(str(o.get("squad") or "").lower(), [])
            bad = []
            for n in names:
                src, sets = people.get(n, (n, {}))
                obj = by_name.get(str(src or "").lower())
                props = {k.lower(): v for k, v in (obj["properties"] if obj else [])}
                props.update({k.lower(): v for k, v in sets.items()})
                table = props.get("animationtable")
                if table and self._carryable(table) is False:
                    bad.append(f"{n} ({src}: {table})")
            if bad:
                notes.append(f"objective {i}: {', '.join(bad)} can't be carried (that animation table has no "
                             f"carry animations, so a captive drags along the ground): use a soldier or officer "
                             f"template, as retail's capture targets are (nk_soldier_ehuman has them)")
        return notes

    def lua_names(self):
        """Every global the game's scripts define or call (luacheck.known_names)."""
        if not hasattr(self, "_lua_names"):
            from .. import luacheck, script as sc
            sources = []
            for a in self.archives:
                for r in a.records:
                    if r.kind == name_hash("script"):
                        try:
                            sources.append(sc.decode(a.read(r)).source)
                        except Exception:
                            continue
            self._lua_names = luacheck.known_names(sources)
        return self._lua_names

    def save_mission(self, map_name, mission):
        """Write missions/<id>.json from the editor's fields (title,
        description, briefing, fee, objectives), keeping anything else the file
        has, and make sure the mission has its layer file."""
        from .. import missions as ms
        asset_id = f"{self.mission_arc(map_name)}_{mission['faction']}{int(mission['number'])}"
        ms.parse_id(asset_id)
        for i, o in enumerate(mission.get("objectives") or [], 1):
            if o.get("type") not in ms.OBJECTIVE_TYPES:
                raise ValueError(f"objective {i}: unknown type {o.get('type')!r}")
            # An objective without its squad or zone yet is kept as a draft;
            # the build skips writing the script until it's complete.
        p = self.mod / "missions" / f"{asset_id}.json"
        doc = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        if self._new_map(map_name):
            doc["map"] = map_name.lower()           # played there (the contract takes the player there)
        for key in ("title", "description"):
            if mission.get(key) is not None:
                doc[key] = mission[key]
        if mission.get("briefing") is not None:
            mail = doc.setdefault("mail", {"sender": "mail.senders.techgal"})
            mail["subject"] = f"Contract: '{doc.get('title', asset_id)}'"
            mail["body"] = mission["briefing"]
        if mission.get("fee") is not None:
            doc.setdefault("ini", {})["ContractFee"] = int(mission["fee"])
        if mission.get("bonus") is not None:
            doc.setdefault("ini", {})["BonusFee"] = int(mission["bonus"])
        if "unlocks" in mission:
            unlocks = [str(u).strip() for u in mission.get("unlocks") or [] if str(u).strip()]
            if unlocks:
                doc["unlocks"] = unlocks
            else:
                doc.pop("unlocks", None)
        if "done_mail" in mission:
            m = {k: str(v) for k, v in (mission.get("done_mail") or {}).items() if k in ("subject", "body") and str(v).strip()}
            if m:
                doc["done_mail"] = m
            else:
                doc.pop("done_mail", None)
        if "keep_if_hostile" in mission:
            if mission.get("keep_if_hostile"):
                doc["keep_if_hostile"] = True
            else:
                doc.pop("keep_if_hostile", None)
        if "test_from" in mission:
            if mission.get("test_from"):
                doc["test_from"] = int(mission["test_from"])
            else:
                doc.pop("test_from", None)
        if "debug" in mission:
            if mission.get("debug"):
                doc["debug"] = True
            else:
                doc.pop("debug", None)
        if "mood" in mission:
            mood = {f: int(float(v)) for f, v in (mission.get("mood") or {}).items()
                    if f in ms.FACTIONS and v not in ("", None) and int(float(v))}
            if mood:
                doc["mood"] = mood
            else:
                doc.pop("mood", None)
        doc["objectives"] = [{k: v for k, v in o.items() if k in ("type", "text", "prompt", "squad", "targets", "vehicle", "zone", "seconds", "marker", "blip", "alive", "extract", "together", "need",
                                                            "bonus", "reveal")
                              and not (k == "bonus" and not v) and not (k == "reveal" and (v in ("", "start") or not o.get("bonus")))}
                             for o in mission.get("objectives") or []]
        if "support" in mission:
            support = []
            for s in mission.get("support") or []:
                if not s.get("item"):
                    continue
                row = {"item": str(s["item"])}
                if int(float(s.get("count") or 0)) > 0:
                    row["count"] = int(float(s["count"]))
                support.append(row)
            if support:
                doc["support"] = support
            else:
                doc.pop("support", None)
        if "waves" in mission:
            waves = [{k: v for k, v in w.items() if k in ("when", "squad", "delay") and v not in ("", None, 0)}
                     for w in mission.get("waves") or []]
            if waves:
                doc["waves"] = waves
            else:
                doc.pop("waves", None)
        if "orders" in mission:
            orders = [{k: v for k, v in o.items() if k in ("when", "squad", "do", "to", "delay") and v not in ("", None, 0)}
                      for o in mission.get("orders") or []]
            for k, o in enumerate(orders, 1):
                if o.get("do") not in ms.ORDER_TYPES:
                    raise ValueError(f"squad order {k}: unknown order {o.get('do')!r}")
            if orders:
                doc["orders"] = orders
            else:
                doc.pop("orders", None)
        if "weather" in mission:
            weather = [{k: v for k, v in w.items() if k in ("when", "preset", "delay") and v not in ("", None, 0)}
                       for w in mission.get("weather") or []]
            weather = [w for w in weather if w.get("preset")]
            if weather:
                doc["weather"] = weather
            else:
                doc.pop("weather", None)
        if "music" in mission:
            music = [{k: v for k, v in m.items() if k in ("when", "play", "delay") and v not in ("", None, 0)}
                     for m in mission.get("music") or []]
            music = [m for m in music if m.get("play")]
            if music:
                doc["music"] = music
            else:
                doc.pop("music", None)
        if "radio" in mission:
            radio = [{k: v for k, v in r.items() if k in ("when", "sound", "text", "delay") and v not in ("", None, 0)}
                     for r in mission.get("radio") or []]
            radio = [r for r in radio if r.get("sound") or r.get("text")]     # empty lines aren't kept
            if radio:
                doc["radio"] = radio
            else:
                doc.pop("radio", None)
        if "fail" in mission:
            fails = [{k: v for k, v in c.items() if k in ("type", "text", "squad", "targets", "seconds", "zone", "faction", "from", "until")
                      and v not in ("", None)} for c in mission.get("fail") or []]
            for k, c in enumerate(fails, 1):
                if c.get("type") not in ms.FAIL_TYPES:
                    raise ValueError(f"fail condition {k}: unknown type {c.get('type')!r}")
            if fails:
                doc["fail"] = fails
            else:
                doc.pop("fail", None)
        start = mission.get("start")
        if start and start.get("position"):
            doc["start"] = {"position": [round(float(v), 2) for v in start["position"]],
                            "heading": round(float(start.get("heading", 0)), 1)}
        elif "start" in mission:
            doc.pop("start", None)
        if "requires" in mission:
            req = [str(r).lower() for r in mission.get("requires") or [] if str(r).lower() != asset_id]
            if req:
                doc["requires"] = req
            else:
                doc.pop("requires", None)
        giver = mission.get("giver")
        if giver:
            # Who offers it: the HQ contact (name and prompt optional), someone
            # placed on the map, or no one (the mission file's "contact": false).
            mode = giver.get("mode", "hq")
            if mode == "sequence":
                # The faction's own HQ bouncer offers it between its retail
                # contracts (the mission file's "hq"); an HQ contact as well
                # only if asked for.
                from .. import hqsequence
                seq = {"after": int(giver.get("after") or 0)}
                if giver.get("chapter") not in (None, ""):
                    try:
                        default = hqsequence.plan(asset_id, {"hq": seq}, ms.parse_id)["chapter"]
                    except ValueError:
                        default = None             # only valid with the chapter given
                    if int(giver["chapter"]) != default:
                        seq["chapter"] = int(giver["chapter"])
                hqsequence.plan(asset_id, {"hq": seq}, ms.parse_id)          # a point the HQ reaches
                doc["hq"] = seq
                self._require("0.74")
            else:
                doc.pop("hq", None)
            if mode == "sequence" and not giver.get("contact"):
                doc["contact"] = False
            elif mode == "none":
                doc["contact"] = False
            else:
                old = doc.get("contact") if isinstance(doc.get("contact"), dict) else {}
                c = {k: v for k, v in old.items() if k not in ("position", "heading", "template", "side", "marker",
                                                               "stay", "invincible", "lose_if_killed")}
                for k in ("name", "prompt"):
                    if (giver.get(k) or "").strip():
                        c[k] = giver[k].strip()
                    else:
                        c.pop(k, None)
                if mode == "placed":
                    if not giver.get("position"):
                        raise ValueError("place the mission giver first")
                    c["position"] = [round(float(v), 2) for v in giver["position"]]
                    c["heading"] = round(float(giver.get("heading", 0)), 1)
                    if (giver.get("template") or "").strip():
                        c["template"] = giver["template"].strip()
                    if giver.get("side") and giver["side"] != "civ":
                        c["side"] = giver["side"]
                    if giver.get("marker") and giver["marker"] != "gold":
                        c["marker"] = giver["marker"]
                    for k in ("stay", "invincible", "lose_if_killed"):
                        if giver.get(k):
                            c[k] = True
                if c:
                    doc["contact"] = c
                else:
                    doc.pop("contact", None)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        lp = self.mod / "encounters" / f"{asset_id}.json"
        if not lp.exists():
            lp.parent.mkdir(parents=True, exist_ok=True)
            lp.write_text(json.dumps({"map": map_name.upper()}, indent=1) + "\n", encoding="utf-8")
        else:
            ldoc = json.loads(lp.read_text(encoding="utf-8"))
            if ldoc.pop("load", None) is not None:      # made before the mission: it loads with the mission now
                lp.write_text(json.dumps(ldoc, indent=1) + "\n", encoding="utf-8")
        self._require("0.69")
        return {"id": asset_id, "file": f"missions/{p.name}"}

    def save_terrain(self, map_name, samples):
        """Height samples painted in the editor ([column, row, height units])
        merged into the mod's terrain/<map>.heights.changes.json (the build
        sets them and relights changed slopes)."""
        import re
        if not re.fullmatch(r"[A-Za-z0-9_]+", map_name):
            raise ValueError(f"bad map name {map_name!r}")
        tdir = self.mod / "terrain"
        if (tdir / f"{map_name}.heights.png").exists():
            raise ValueError(f"terrain/{map_name}.heights.png reshapes this map as a whole image; "
                             "edit that image, or remove it to paint in the editor")
        p = tdir / f"{map_name}.heights.changes.json"
        doc = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"terrain": map_name, "samples": []}
        merged = {(int(x), int(z)): int(v) for x, z, v in doc.get("samples", [])}
        for x, z, v in samples:
            merged[(int(x), int(z))] = int(v)
        doc = {"terrain": map_name, "samples": [[x, z, v] for (x, z), v in sorted(merged.items(), key=lambda kv: (kv[0][1], kv[0][0]))]}
        tdir.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, separators=(",", ":")) + "\n", encoding="utf-8")
        self._forget_terrain(map_name)
        return {"file": f"terrain/{p.name}", "samples": len(doc["samples"])}

    def terrain_look(self, name):
        """How the ground is drawn, as the context mods leave it: (meta, paint
        bytes, light bytes). meta has the layer table (texture, scale, axis) and
        the paint size; paint is each layer's opacity (0-255), layer by layer,
        size x size; light is the baked lighting (RGB per height sample)."""
        if not hasattr(self, "_looks"):
            self._looks = {}
        self._modded_terrain()          # forgets the looks when a mod's terrain files changed (a save)
        if name not in self._looks:
            from .. import terrain_layers as tl, terrain_light, terrain_paint
            data = self._modded_terrain()
            rec = _terrain_rec(data, name)
            if rec is None:
                raise KeyError(name)
            chunks = terrain.tern_chunks(_terrain_bytes(data, rec))
            table = terrain_paint.layer_table(chunks)
            w, images = terrain_paint.layer_images(chunks)
            paint = b"".join(images.get(i, bytes(w * w)) for i in range(len(table)))
            info = terrain.parse_info(chunks[b"INFO"])
            size = info["size"]
            light = bytearray(size * size * 3)
            if b"COLR" in chunks:
                side = size // tl.PATCH
                rgb = terrain_light.vertex_rgb(tl.decode_colr(chunks[b"COLR"], side * side), size)
                i = 0
                for row in rgb:
                    for c in row:
                        light[i:i + 3] = bytes(c) if c else b"\x80\x80\x80"
                        i += 3
            meta = {"layers": table, "paint_size": w, "size": size, "lit": b"COLR" in chunks}
            self._looks[name] = (meta, paint, bytes(light))
        return self._looks[name]

    def terrain_decals(self, name):
        """The ground's decals (roads, concrete), as the context mods leave
        them: {"textures": [name...], "decals": [[cell x, cell z, texture
        index, u, v x 4 corners in 1/2048ths], ...]}. Corners go (x, z+1),
        (x+1, z+1), (x, z), (x+1, z), as stored."""
        from .. import terrain_decals
        data = self._modded_terrain()
        rec = _terrain_rec(data, name)
        if rec is None:
            raise KeyError(name)
        doc = terrain_decals.export(terrain.tern_chunks(_terrain_bytes(data, rec)))
        names = list(doc["textures"])
        out = []
        for d in doc["decals"]:
            t = d["texture"]
            k = t if isinstance(t, int) else names.index(t)
            out.append([d["cell"][0], d["cell"][1], k] + [v for uv in d["uv"] for v in uv])
        return {"textures": names, "decals": out}

    def texture_png(self, name):
        """A game texture as PNG (its full-size image), cached."""
        if not hasattr(self, "_pngs"):
            self._pngs = {}
        if name not in self._pngs:
            from .. import png
            rec = next(((a, r) for a in self.archives for r in a.records
                        if r.kind == texture.TEXTURE and r.key == name_hash(name)), None)
            if rec is None:
                raise KeyError(name)
            w, h, px = texture.decode(rec[0].read(rec[1])).rgba()
            self._pngs[name] = png.encode(w, h, px, 4)
        return self._pngs[name]

    # ------------------------------------------------------------ objects
    def all_objects(self):
        if self._objects is None:
            self._objects = [o for o in find.objects(self.archives) if o["position"] and o["rotation"]]
        return self._objects

    def objects(self, map_name):
        m = map_name.lower()
        out = []
        for o in self.all_objects():
            layer = o["layer"].lower()
            if layer != m and not layer.startswith(m + "_"):
                continue
            rows = [list(o["rotation"][0:3]), list(o["rotation"][3:6]), list(o["rotation"][6:9])]
            out.append({"id": f"{o['layer']}:{o['index']}", "layer": o["layer"], "name": o["name"],
                        "model": o["model"], **look(o),
                        "position": list(o["position"]), "rotation": to_three(rows)})
        return out

    def object_properties(self, ref):
        """An object's properties, [[key, value], ...]: "id:<layer>:<index>"
        (a game object), or "from:<name>" / "from:model:<model>" (the object a
        copy or placed object comes from)."""
        kind, _, what = ref.partition(":")
        o = None
        if kind == "id":
            layer, _, index = what.rpartition(":")
            o = next((x for x in self.all_objects()
                      if x["layer"].lower() == layer.lower() and str(x["index"]) == index), None)
        elif kind == "from":
            low = what.lower()
            if low.startswith("model:"):
                o = next((x for x in self.all_objects() if not x["name"] and x["model"].lower() == low[6:]), None)
            else:
                o = next((x for x in self.all_objects() if x["name"].lower() == low), None)
        if o is None:
            raise ValueError(f"no object {ref!r}")
        return {"name": o["name"], "model": o["model"], "layer": o["layer"],
                "properties": [list(p) for p in o["properties"]]}

    def bounds(self, map_name, context=False):
        """{model: [min x, y, z, max x, y, z]} for the models this map's objects
        draw (from each model's header), for sizing far-away stand-ins."""
        import struct
        from .. import ucfb
        if not hasattr(self, "_bounds"):
            self._bounds = {}
        if context:          # the models other mods' objects draw
            want = {a["geometry"] for a in self.context_objects(map_name)["added"]}
        else:
            want = {o["geometry"] for o in self.objects(map_name)}
        out = {}
        for g in sorted(want):
            if g not in self._bounds:
                b = None
                try:
                    mr, _ = model.find(self.archives, g)
                    for tag, data in ucfb.iter_chunks(ucfb.unwrap(mr, b"modl")):
                        if tag == b"INFO" and len(data) >= 56:
                            b = [round(v, 3) for v in struct.unpack_from("<6f", data, 32)]
                            break
                except (KeyError, ValueError):
                    pass
                self._bounds[g] = b
            if self._bounds[g]:
                out[g] = self._bounds[g]
        return out

    # ------------------------------------------------------------ other mods
    def _context_world(self):
        """The context mods' object rules and encounter layers, applied as a
        build would (once): this mod's own too, except the editor's rules
        (shown as edits) and its squad members (shown as squads)."""
        stamp = self._maps_stamp()
        if stamp != getattr(self, "_ctx_stamp", None):
            self._ctx_world = None
        if getattr(self, "_ctx_world", None) is None:
            self._ctx_stamp = stamp
            from .. import modbuild
            data = modbuild._Data(self.data)
            mine = self.mod.resolve()
            for p in self.context:
                p = Path(p)
                data.project = modbuild.project_name(p)
                part = modbuild.Result()
                try:
                    modbuild._apply_objects(data, p, part, skip=(EDITOR_RULES,) if p.resolve() == mine else ())
                    modbuild._apply_encounters(data, p, part)
                except ValueError as err:
                    print(f"editor: skipped {data.project}'s objects: {err}", flush=True)
            self._ctx_world = data
        return self._ctx_world

    def context_objects(self, map_name):
        """Objects other mods add, move or remove on this map:
        {"added": [object...], "removed": {game object id: mod name}}."""
        from .. import world
        m = map_name.lower()
        data = self._context_world()
        mine = self.mod.resolve()
        mine_name = None
        from ..modbuild import project_name
        mine_name = project_name(mine)
        WORLD = name_hash("world")

        def key(o):
            r = o["rotation"] or ()
            return (o["name"].lower(), o["model"].lower(), tuple(round(v, 2) for v in o["position"] or ()),
                    tuple(round(v, 3) for v in r))

        def entry(o, by, n):
            rows = [list(o["rotation"][0:3]), list(o["rotation"][3:6]), list(o["rotation"][6:9])]
            return {"id": f"ctx:{o['layer']}:{n}", "layer": o["layer"], "name": o["name"], "model": o["model"],
                    **look(o), "position": list(o["position"]), "rotation": to_three(rows), "by": by}

        added, removed = [], {}
        for name, changes in data.replace.items():
            for rec in data.archive(name).records:
                if rec.kind != WORLD or rec.ident not in changes:
                    continue
                layer = self.names().get(rec.key) or f"{rec.key:08X}"
                if not self._in_map(layer, m):
                    continue
                by = data.changed_by.get((name, rec.ident), "another mod")
                base = [o for o in world.iter_objects(data.archive(name).read(rec)) if o["position"]]
                now = [o for o in world.iter_objects(changes[rec.ident]) if o["position"]]
                now_keys = {}
                for o in now:
                    now_keys.setdefault(key(o), []).append(o)
                base_keys = {}
                for o in base:
                    base_keys.setdefault(key(o), []).append(o)
                for o in base:
                    if not now_keys.get(key(o)):
                        removed[f"{layer}:{o['index']}"] = by
                    else:
                        now_keys[key(o)].pop()
                for k, rest in now_keys.items():
                    for o in rest:
                        o["layer"] = layer
                        added.append(entry(o, by, len(added)))
        for arc_name, recs in data.add.items():
            for k, kind, body in recs:
                if kind != WORLD:
                    continue
                by = data.added_by.get((k, kind), "another mod")
                objs = [o for o in world.iter_objects(body) if o["position"]]
                if not objs:
                    continue
                layer = objs[0]["layer"]
                for o in objs:
                    props = {a.lower(): b for a, b in o["properties"]}
                    if by == mine_name and props.get("encounter", "none").lower() != "none":
                        continue     # this mod's squad members show as squads
                    added.append(entry(o, by, len(added)))
        # New layers name their map in TNAM; keep the ones on this map.
        maps = self._layer_maps(data)
        added = [a for a in added if maps.get(a["layer"].lower(), m) == m or self._in_map(a["layer"], m)]
        return {"added": added, "removed": removed}

    def _layer_maps(self, data):
        """Map of each added layer (lower-case name -> map), from its TNAM."""
        from .. import world
        out = {}
        for recs in data.add.values():
            for k, kind, body in recs:
                if kind == name_hash("world"):
                    doc = world.to_json(body)
                    tn = next((c for c in doc["chunks"] if c.get("tag") == "TNAM"), None)
                    mp = bytes.fromhex(tn["hex"]).split(b"\0")[0].decode("latin-1").lower() if tn and "hex" in tn else ""
                    out[(doc.get("name") or "").lower()] = mp
        return out

    def find_object(self, oid):
        layer, _, index = oid.rpartition(":")
        for o in self.all_objects():
            if o["layer"] == layer and o["index"] == int(index):
                return o
        raise KeyError(oid)

    # ------------------------------------------------------------ models
    def model_glb(self, name):
        own = self.mod / "models" / f"{name}.glb"
        spec = self.mod / "models" / f"{name}.new.json"
        if own.is_file() and spec.is_file():
            return self._new_model_glb(own.read_bytes(), spec)    # the mod's new model, as it'll be imported
        if name not in self._models:
            mr, sr = model.find(self.archives, name)
            m = model.decode(mr, sr)
            lod = 0
            pieces = m.at_lod(lod, False)
            wanted = {p.texture for p in pieces}
            pngs = {}
            from .. import png
            for a in self.archives:
                for r in a.records:
                    if r.kind == texture.TEXTURE and r.key in wanted and r.key not in pngs:
                        try:
                            w, h, px = texture.decode(a.read(r)).rgba()
                        except ValueError:
                            continue
                        pngs[r.key] = (png.encode(w, h, px, 4), any(px[i] < 255 for i in range(3, len(px), 4)))
            self._models[name] = gltf.export(m, lod, False, pngs, lambda k: f"{k:08X}")
        return self._models[name]

    def _new_model_glb(self, glb, spec):
        """A new model's file for the 3D view, its materials see-through as the
        build makes them: a material's alphaMode decides (MASK or BLEND cut
        out), and one without takes the base model's piece of the same texture,
        else its first piece (model_import's template)."""
        try:
            doc, binary = gltf.read(glb)
        except (ValueError, KeyError, struct.error):
            return glb
        mats = doc.get("materials") or []
        if all("alphaMode" in m for m in mats):
            return glb
        try:
            base = model.decode(*model.find(self.archives, json.loads(spec.read_text(encoding="utf-8")).get("base") or ""))
        except (KeyError, ValueError, OSError):
            return glb
        for m in mats:
            if "alphaMode" in m:
                continue
            same = [p for p in base.pieces if p.texture == name_hash(m.get("name") or "")]
            t = same[0] if same else (base.pieces[0] if base.pieces else None)
            if t is not None and gltf.cutout_flag(t):
                m["alphaMode"], m["alphaCutoff"] = "MASK", 0.5
        from ..model_changes import _glb
        return _glb(doc, binary)

    # ------------------------------------------------------------ edits
    def rules_path(self):
        return self.mod / "objects" / EDITOR_RULES

    def load_edits(self):
        """The editor's own rules, as {"moved": {id: {...}}, "copies": [...],
        "removed": [id, ...]}."""
        p = self.rules_path()
        rules = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        moved, copies, removed = {}, [], []
        for rule in rules:
            src = self._match(rule)
            if src is None:
                continue
            if rule.get("remove"):
                removed.append(f"{src['layer']}:{src['index']}")
                continue
            rows = [list(src["rotation"][0:3]), list(src["rotation"][3:6]), list(src["rotation"][6:9])]
            from ..modbuild import _turn
            new_rows = rule["rotation"] if rule.get("rotation") else _turn(rows, float(rule.get("turn", 0)))
            entry = {"source": f"{src['layer']}:{src['index']}",
                     "position": rule.get("position", list(src["position"])),
                     "rotation": to_three(new_rows)}
            if rule.get("copy"):
                copies.append({**entry, "as": rule.get("as", "")})
            else:
                moved[entry["source"]] = entry
        return {"moved": moved, "copies": copies, "removed": removed}

    def _match(self, rule):
        for o in self.all_objects():
            if o["layer"].lower() != rule.get("layer", "").lower():
                continue
            if "name" in rule:
                if o["name"].lower() == rule["name"].lower():
                    return o
            elif o["model"].lower() == rule.get("model", "").lower() and "near" in rule:
                if math.dist(o["position"], rule["near"]) <= float(rule.get("radius", 0.5)):
                    return o
        return None

    def save_edits(self, moved, copies, removed=(), map_name=None):
        """Write objects/editor.json from {id: {position, rotation}},
        [{source, as, position, rotation}] and [id, ...] to remove. Returns
        the rule count. With map_name (the map being edited), rules for
        objects on other maps are kept: the editor only holds one map's."""
        kept = []
        p = self.rules_path()
        if map_name and p.exists():
            m = map_name.lower()
            for rule in json.loads(p.read_text(encoding="utf-8")):
                src = self._match(rule)
                if src is None or not self._in_map(src["layer"], m):
                    kept.append(rule)
        # Copies first: a copy finds its source by its original position,
        # which a move rule for the same source would change.
        rules = []
        names = {o["name"].lower() for o in self.all_objects() if o["name"]}
        taken = set()
        for c in copies:
            new_name = c.get("as") or ""
            if not new_name:
                raise ValueError("a placed copy needs a name")
            if new_name.lower() in taken:
                raise ValueError(f"two copies are named {new_name!r}")
            if new_name.lower() in names:
                raise ValueError(f"the game already has an object named {new_name!r}")
            taken.add(new_name.lower())
            rules.append({**self._rule(c["source"], c), "copy": True, "as": new_name,
                          **({"set": dict(c["set"])} if c.get("set") else {})})
        removed = set(removed)
        for oid, e in moved.items():
            if oid not in removed:
                rules.append(self._rule(oid, e))
        # Removals last: copies of a removed object still find it first.
        for oid in sorted(removed):
            rule = find.rule_for(self.find_object(oid))
            rule.pop("move", None)
            rules.append({**rule, "remove": True})
        if removed:
            self._require("0.51")          # the first toolkit with "remove" rules
        rules = kept + rules
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rules, indent=1) + "\n", encoding="utf-8")
        return len(rules)

    # ------------------------------------------------------------ paths and zones
    # Game items (in the game's own layers) can have their points moved: a
    # path's orientations, pauses and links are indexed by point, so the
    # count stays. Edits go in layout/<layer>.<kind>.changes.json. The mod's
    # own items, in its encounters/*.json layers, are fully editable.

    RECORD_KIND = {"path": "path", "boundary": "lineregion"}
    ITEM_TAG = {"path": "path", "boundary": "lrg_"}

    def _in_map(self, layer, m):
        layer = layer.lower()
        return layer == m or layer.startswith(m + "_")

    def _layout_changes(self, layer, rkind):
        p = self.mod / "layout" / f"{layer}.{rkind}.changes.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    # The map's own zones: the game makes water only from a map's own
    # lineregion record (all 26 retail water zones are in one; none in a
    # layer), and holds 16 water zones a map. Zones the mod adds there go in
    # layout/<map>.lineregion.changes.json on a game map, and in
    # maps/<map>/boundaries.json on a new map (in the mod that makes it).
    WATER_LIMIT = 16

    def _map_owner(self, m):
        """The mod folder whose maps/<m>.json makes this new map (the edited mod first), or None."""
        for p in [self.mod, *getattr(self, "context", [])]:
            if (Path(p) / "maps" / f"{m}.json").exists():
                return Path(p)
        return None

    def _own_zones(self, m):
        owner = self._map_owner(m)
        if owner is not None:
            mine = owner.resolve() == self.mod.resolve()
            return {"new_map": True, "owner": owner, "editable": mine, "file": f"maps/{m}/boundaries.json",
                    "note": None if mine else f"{m} is made by the mod in {owner.name}: switch to it (Mods) to change its own zones"}
        return {"new_map": False, "owner": None, "editable": True, "file": f"layout/{m}.lineregion.changes.json", "note": None}

    @staticmethod
    def _own_item(m, layer, name, points, props, file, editable):
        return {"id": f"map:{m}:boundary:{name}", "source": "map", "layer": layer, "file": file,
                "kind": "boundary", "name": name, "points": points, "properties": props,
                "spec": {"points": points, "properties": props}, "editable": editable}

    def layout(self, map_name):
        from .. import aidata, modpack
        m = map_name.lower()
        items = []
        own = self._own_zones(m)
        for a in self.archives:
            for r in a.records:
                rkind = aidata.KINDS.get(r.kind)
                if rkind not in ("path", "lineregion"):
                    continue
                layer = self.names().get(r.key)
                if not layer or not self._in_map(layer, m):
                    continue
                doc = aidata.to_json(a.read(r), rkind)
                base = {(t, (n or "").lower()) for t, n, _ in aidata.items(doc)}
                ch = self._layout_changes(layer, rkind)
                if ch:
                    doc = modpack.apply_layout_changes(doc, ch)
                own_record = rkind == "lineregion" and layer.lower() == m
                for tag, name, node in aidata.items(doc):
                    if tag not in ("path", "lrg_") or not name:
                        continue
                    kids = node["children"]
                    pts = next((c["points"] for c in kids if c["tag"] == "PNTS"), [])
                    prop = next((c for c in kids if c["tag"] == "PROP"), {})
                    props = dict(prop.get("pairs") or [(k, v) for k, v in prop.get("values", [])])
                    kind = "path" if tag == "path" else "boundary"
                    props = {k: str(v) for k, v in props.items()}
                    if own_record and (tag, name.lower()) not in base:
                        # a zone the mod added to the map's own record
                        items.append(self._own_item(m, layer, name, pts, props, own["file"], True))
                        continue
                    items.append({"id": f"game:{layer}:{kind}:{name}", "source": "game", "layer": layer,
                                  "kind": kind, "name": name, "points": pts, "properties": props})
        # A new map's own zones (maps/<map>/boundaries.json, in the mod that makes the map)
        if own["new_map"]:
            bf = own["owner"] / "maps" / m / "boundaries.json"
            for name, spec in (json.loads(bf.read_text(encoding="utf-8")).get("boundaries", {})
                               if bf.exists() else {}).items():
                items.append(self._own_item(m, m, name, spec["points"],
                                            {k: str(v) for k, v in (spec.get("properties") or {}).items()},
                                            own["file"], own["editable"]))
        files = []
        edir = self.mod / "encounters"
        for f in sorted(edir.glob("*.json")) if edir.is_dir() else []:
            doc = json.loads(f.read_text(encoding="utf-8"))
            if (doc.get("map") or "").lower() != m:
                continue
            files.append(f.name)
            for kind, key in (("path", "paths"), ("boundary", "boundaries")):
                for name, spec in (doc.get(key) or {}).items():
                    items.append({"id": f"mod:{f.name}:{kind}:{name}", "source": "mod", "file": f.name,
                                  "layer": f.stem, "kind": kind, "name": name, "points": spec["points"],
                                  "properties": {k: str(v) for k, v in (spec.get("properties") or {}).items()},
                                  "spec": spec})
        return {"items": items, "files": files,
                "own": {"file": own["file"], "editable": own["editable"], "note": own["note"],
                        "new_map": own["new_map"], "water_limit": self.WATER_LIMIT}}

    def save_layout(self, map_name, game_edits, mod_files, own=None):
        """game_edits: [{layer, kind, name, points}] for game items (same
        point count). mod_files: {file name: {"paths": {...}, "boundaries":
        {...}}}, each replacing those two parts of a mod layer file (a new
        file is made for map_name). own: {name: spec}, all the zones the mod
        adds to the map's own record (save_own_zones), or None to leave
        them. Returns a summary."""
        import copy
        import re
        from .. import aidata
        done = {"game": 0, "files": []}
        groups = {}
        for e in game_edits:
            rkind = self.RECORD_KIND[e["kind"]]
            groups.setdefault((e["layer"], rkind), []).append(e)
        for (layer, rkind), edits in groups.items():
            rec = next(((a, r) for a in self.archives for r in a.records
                        if r.key == name_hash(layer) and aidata.KINDS.get(r.kind) == rkind), None)
            if rec is None:
                raise ValueError(f"the game has no {rkind} data for layer {layer!r}")
            base = {(t, (n or "").lower()): node for t, n, node in aidata.items(aidata.to_json(rec[0].read(rec[1]), rkind))}
            changes = self._layout_changes(layer, rkind) or {"format": "mercfoundry-ai-changes-1", "kind": rkind,
                                                              "changed": [], "removed": []}
            for e in edits:
                tag = self.ITEM_TAG[e["kind"]]
                node = base.get((tag, e["name"].lower()))
                if node is None:
                    raise ValueError(f"layer {layer} has no {e['kind']} named {e['name']!r}")
                node = copy.deepcopy(node)
                pnts = next(c for c in node["children"] if c["tag"] == "PNTS")
                if len(e["points"]) != len(pnts["points"]):
                    raise ValueError(f"{e['name']}: a game {e['kind']} keeps its {len(pnts['points'])} points")
                same = all(math.dist(a, b) < 1e-4 for a, b in zip(e["points"], pnts["points"]))
                pnts["points"] = [[round(float(v), 3) for v in p] for p in e["points"]]

                def key(item):
                    nm = next((c.get("text") for c in item["children"] if c["tag"] == "NAME"), "")
                    return item["tag"], (nm or "").lower()
                changes["changed"] = [c for c in changes["changed"] if key(c) != (tag, e["name"].lower())]
                if not same:
                    changes["changed"].append(node)
                done["game"] += 1
            p = self.mod / "layout" / f"{layer}.{rkind}.changes.json"
            if changes["changed"] or changes["removed"]:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(changes, indent=1) + "\n", encoding="utf-8")
            elif p.exists():
                p.unlink()
        world_layers = {(self.names().get(r.key) or "").lower() for a in self.archives for r in a.records
                        if r.kind == name_hash("world")}
        for fname, parts in mod_files.items():
            if not re.fullmatch(r"[A-Za-z0-9_]+\.json", fname):
                raise ValueError(f"layer file names use letters, digits and _ only: {fname!r}")
            p = self.mod / "encounters" / fname
            if p.exists():
                doc = json.loads(p.read_text(encoding="utf-8"))
            else:
                if p.stem.lower() in world_layers:
                    raise ValueError(f"the game already has a layer named {p.stem!r}")
                doc = {"map": map_name.upper()}
            for key, need in (("paths", 2), ("boundaries", 3)):
                specs = parts.get(key) or {}
                for name, spec in specs.items():
                    if len(spec.get("points", [])) < need:
                        raise ValueError(f"{name}: a {key[:-1] if key == 'paths' else 'zone'} needs at least {need} points")
                    spec["points"] = [[round(float(v), 3) for v in pt] for pt in spec["points"]]
                if specs:
                    doc[key] = specs
                else:
                    doc.pop(key, None)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
            done["files"].append(f"encounters/{fname}")
        if own is not None:
            f = self.save_own_zones(map_name, own)
            if f:
                done["files"].append(f)
        return done

    def save_own_zones(self, map_name, zones):
        """zones: {name: {points, properties}}, every zone the mod adds to the
        map's own record (none: the file goes). Returns the file written or
        removed."""
        from .. import aidata, encounters
        m = map_name.lower()
        own = self._own_zones(m)
        if not own["editable"]:
            raise ValueError(own["note"])
        nodes = []
        for name, spec in zones.items():
            if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
                raise ValueError(f"zone names use letters, digits, _ and - only: {name!r}")
            spec["points"] = [[round(float(v), 3) for v in pt] for pt in spec.get("points", [])]
            spec["properties"] = {str(k): str(v) for k, v in (spec.get("properties") or {}).items()}
            nodes.append(encounters._boundary_node(name, spec))        # checks it has three points
        is_water = lambda props: str(dict((k.strip().lower(), v) for k, v in props.items()).get("type", "")).lower() == "water"
        water = sum(is_water(s["properties"]) for s in zones.values())
        if own["new_map"]:
            base_water, taken = 0, set()
            bf = own["owner"] / "maps" / m / "boundaries.json"
        else:
            rec = next(((a, r) for a in self.archives for r in a.records
                        if r.key == name_hash(m) and aidata.KINDS.get(r.kind) == "lineregion"), None)
            if rec is None:
                raise ValueError(f"the game has no zones record of {m}'s own to add to")
            base = list(aidata.items(aidata.to_json(rec[0].read(rec[1]), "lineregion")))
            changes = self._layout_changes(m, "lineregion") or {"format": "mercfoundry-ai-changes-1",
                                                               "kind": "lineregion", "changed": [], "removed": []}
            gone = {(t, (n or "").lower()) for t, n in changes.get("removed", [])}
            taken = {(n or "").lower() for t, n, _ in base if t == "lrg_"}
            base_water = sum(1 for t, n, node in base if t == "lrg_" and (t, (n or "").lower()) not in gone and is_water(
                dict(next((c for c in node["children"] if c["tag"] == "PROP"), {}).get("pairs") or [])))
            bf = self.mod / "layout" / f"{m}.lineregion.changes.json"
        clash = [n for n in zones if n.lower() in taken]
        if clash:
            raise ValueError(f"{m} already has a zone named {clash[0]}")
        if base_water + water > self.WATER_LIMIT:
            raise ValueError(f"{m} would have {base_water + water} water zones ({base_water} of the game's); "
                             f"the game holds {self.WATER_LIMIT} a map")
        if own["new_map"]:
            if zones:
                bf.parent.mkdir(parents=True, exist_ok=True)
                bf.write_text(json.dumps({"boundaries": zones}, indent=1) + "\n", encoding="utf-8")
            elif bf.exists():
                bf.unlink()
        else:
            def key(item):
                nm = next((c.get("text") for c in item["children"] if c["tag"] == "NAME"), "")
                return item["tag"], (nm or "").lower()
            # the game's zones the mod changed stay; the added ones are replaced
            changes["changed"] = [c for c in changes["changed"] if c["tag"] != "lrg_" or key(c)[1] in taken] + nodes
            if changes["changed"] or changes["removed"]:
                bf.parent.mkdir(parents=True, exist_ok=True)
                bf.write_text(json.dumps(changes, indent=1) + "\n", encoding="utf-8")
            elif bf.exists():
                bf.unlink()
        return own["file"]

    # ------------------------------------------------------------ encounters
    # An encounter has named spawn point sets and squads; a squad's members
    # are objects (copies of a template or placed character in a mod layer;
    # in the game, objects whose "encounter" and "squad" properties name it).

    def encounters(self, map_name):
        from .. import aidata
        m = map_name.lower()
        members = {}
        for o in self.all_objects():
            if not self._in_map(o["layer"], m):
                continue
            props = {k.lower(): v for k, v in o["properties"]}
            enc, sq = props.get("encounter", "none"), props.get("squad", "none")
            if enc.lower() == "none":
                continue
            rows = [list(o["rotation"][0:3]), list(o["rotation"][3:6]), list(o["rotation"][6:9])]
            members.setdefault((enc.lower(), sq.lower()), []).append(
                {"name": o["name"], "model": o["model"], "position": list(o["position"]),
                 "yaw": round(heading(rows), 2)})
        game = []
        for a in self.archives:
            for r in a.records:
                if aidata.KINDS.get(r.kind) != "encounter":
                    continue
                layer = self.names().get(r.key)
                if not layer or not self._in_map(layer, m):
                    continue
                doc = aidata.to_json(a.read(r), "encounter")
                ch = self._layout_changes(layer, "encounter")
                if ch:
                    from .. import modpack
                    doc = modpack.apply_layout_changes(doc, ch)
                for tag, name, node in aidata.items(doc):
                    if tag != "enc_" or not name:
                        continue
                    sets, squads = {}, []
                    for n in aidata._walk(node["children"]):
                        if n.get("tag") == "PSET":
                            nm = next((c.get("text") for c in n["children"] if c["tag"] == "NAME"), "")
                            sets[nm] = next((c["points"] for c in n["children"] if c["tag"] == "PNTS"), [])
                        elif n.get("tag") == "SQD_":
                            nm = next((c.get("text") for c in n["children"] if c["tag"] == "NAME"), "")
                            fp = next((c.get("sets", []) for c in n["children"] if c["tag"] == "FPNT"), [])
                            squads.append({"name": nm, "points": fp,
                                           "members": members.get((name.lower(), nm.lower()), [])})
                    game.append({"name": name, "layer": layer, "points": sets, "squads": squads})
        mod = []
        by_enc = {(g["layer"].lower(), g["name"].lower()): g for g in game}
        edir = self.mod / "encounters"
        for f in sorted(edir.glob("*.json")) if edir.is_dir() else []:
            doc = json.loads(f.read_text(encoding="utf-8"))
            if (doc.get("map") or "").lower() == m:
                mod.append({"file": f.name, "layer": f.stem, "encounters": doc.get("encounters", [])})
                # The mod's squads for the game's encounters, shown on them.
                for sq in doc.get("game_squads", []):
                    g = by_enc.get((sq.get("layer", "").lower(), sq.get("encounter", "").lower()))
                    if g is not None:
                        g["squads"].append({"name": sq["name"], "points": sq.get("points", []),
                                            "members": sq.get("members", []), "file": f.name})
        return {"game": game, "mod": mod}

    # ------------------------------------------------------------ placed objects
    # Objects placed from templates go in the mod's own layer for the map
    # (encounters/<mod>_<map>.json, "objects"), which loads with the map in
    # free roam ("load": "map").

    def editor_layer_file(self, map_name):
        import re
        meta = self.mod / "mod.json"
        name = json.loads(meta.read_text(encoding="utf-8")).get("name") if meta.exists() else None
        slug = re.sub(r"[^a-z0-9]+", "_", (name or self.mod.name).lower()).strip("_") or "mod"
        return f"{slug}_{map_name.lower()}.json"

    def load_placed(self, map_name):
        """Objects in the mod's layers for this map: [{file, from, name,
        position, rotation (editor form)}]."""
        from ..encounters import _rotation
        m = map_name.lower()
        out = []
        edir = self.mod / "encounters"
        for f in sorted(edir.glob("*.json")) if edir.is_dir() else []:
            doc = json.loads(f.read_text(encoding="utf-8"))
            if (doc.get("map") or "").lower() != m:
                continue
            for o in doc.get("objects", []):
                out.append({"file": f.name, "from": o["from"], "name": o["name"],
                            "position": o["position"], "set": o.get("set"),
                            "rotation": to_three(o["rotation"] if o.get("rotation")
                                                 else _rotation(float(o.get("yaw", 0))))})
        return out

    def save_placed(self, map_name, files):
        """files: {layer file name: [{from, name, position, rotation, set?}]},
        replacing those files' "objects". The editor's own layer file (and
        any new file) loads with the map."""
        import re
        taken = {o["name"].lower() for o in self.all_objects() if o["name"]}
        world_layers = {(self.names().get(r.key) or "").lower() for a in self.archives for r in a.records
                        if r.kind == name_hash("world")}
        seen = set()
        for fname, objs in files.items():
            if not re.fullmatch(r"[A-Za-z0-9_]+\.json", fname):
                raise ValueError(f"layer file names use letters, digits and _ only: {fname!r}")
            for o in objs:
                n = o["name"].lower()
                if not re.fullmatch(r"[A-Za-z0-9_\-]+", o["name"]):
                    raise ValueError(f"object names use letters, digits, _ and - only: {o['name']!r}")
                if n in taken or n in seen:
                    raise ValueError(f"the name {o['name']!r} is already used; placed objects need new names")
                seen.add(n)
        own = self.editor_layer_file(map_name).lower()
        for fname, objs in files.items():
            p = self.mod / "encounters" / fname
            if p.exists():
                doc = json.loads(p.read_text(encoding="utf-8"))
            else:
                if p.stem.lower() in world_layers:
                    raise ValueError(f"the game already has a layer named {p.stem!r}")
                doc = {"map": map_name.upper()}
            before = json.dumps(doc, sort_keys=True)
            if fname.lower() == own or not p.exists():
                doc["load"] = "map"
            # keys an object has that the editor doesn't edit stay (by its name)
            old = {str(o.get("name") or "").lower(): o for o in doc.get("objects") or [] if isinstance(o, dict)}
            out = []
            for o in objs:
                rows = from_three(o["rotation"])
                item = {"from": o["from"], "name": o["name"],
                        "position": [round(float(v), 3) for v in o["position"]],
                        "yaw": round(math.degrees(math.atan2(-rows[0][2], rows[0][0])), 2)}
                if tilted(rows):        # leaning (to the ground's slope): the whole rotation
                    item["rotation"] = [[round(v, 6) for v in row] for row in rows]
                if o.get("set"):
                    item["set"] = o["set"]
                for k, v in (old.get(o["name"].lower()) or {}).items():
                    if k not in ("from", "name", "position", "yaw", "rotation", "set"):
                        item[k] = v
                prev = old.get(o["name"].lower())
                if prev and prev.get("from") == item["from"] and "rotation" not in item and "rotation" not in prev:
                    # unmoved: keep its numbers as written (the editor's round trip can differ in the last digit)
                    if all(abs(float(a) - float(b)) < 1e-3 for a, b in zip(prev.get("position") or [], item["position"])) \
                            and abs(float(prev.get("yaw") or 0) - item["yaw"]) < 0.01:
                        item["position"], item["yaw"] = prev["position"], prev.get("yaw", item["yaw"])
                out.append(item)
            if out:
                doc["objects"] = out
            else:
                doc.pop("objects", None)
            # every other key of the layer (unnamed, boundaries, copy_loads...) is kept;
            # a layer nothing changed in isn't written again
            if p.exists() and json.dumps(doc, sort_keys=True) == before:
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        if files:
            self._require("0.65")          # the first toolkit with "load": "map"
        return {"files": [f"encounters/{f}" for f in files]}

    def templates(self):
        """Objects a squad member can copy: templates (template_*), with
        their kind, faction, model and the model they draw (GeometryFile)."""
        out = []
        for o in self.all_objects():
            if not o["name"].lower().startswith("template_"):
                continue
            props = {k.lower(): v for k, v in o["properties"]}
            out.append({"name": o["name"], "type": props.get("objecttype", ""),
                        "faction": props.get("faction", ""), "model": o["model"], "layer": o["layer"],
                        "geometry": props.get("geometryfile", o["model"])})
        out.sort(key=lambda t: (t["type"] != "human", t["name"].lower()))
        return out

    def placeables(self):
        """Everything Place can copy, on any map: templates, the game's other
        named objects, and each model an unnamed object draws ("model:<model>",
        buildings and props). [{name, kind, model, geometry, type, layer}]."""
        if getattr(self, "_placeables", None) is None:
            out, seen, models = [], set(), set()
            for o in self.all_objects():
                props = {k.lower(): v for k, v in o["properties"]}
                look = {"model": o["model"], "geometry": props.get("geometryfile", o["model"]),
                        "type": props.get("objecttype", ""), "layer": o["layer"]}
                if o["name"]:
                    low = o["name"].lower()
                    if low in seen:
                        continue
                    seen.add(low)
                    out.append({"name": o["name"], "kind": "template" if low.startswith("template_") else "object",
                                **look})
                elif o["model"] and o["model"].lower() not in models:
                    models.add(o["model"].lower())
                    out.append({"name": f"model:{o['model']}", "kind": "model", **look})
            order = {"template": 0, "object": 1, "model": 2}
            out.sort(key=lambda p: (order[p["kind"]], p["name"].lower()))
            self._placeables = out
        return self._placeables

    def save_game_encounters(self, edits):
        """edits: [{layer, name, points: {set: [[x, y, z], ...]}}] for the
        game's own encounters: their spawn point sets are replaced, and the
        encounter goes into layout/<layer>.encounter.changes.json (the whole
        encounter, matched by name, as the build applies layout changes).
        Returns the files written."""
        from .. import aidata, modpack
        by_layer = {}
        for e in edits:
            by_layer.setdefault(e["layer"], []).append(e)
        written = []
        for layer, items in by_layer.items():
            rec = next(((a, r) for a in self.archives for r in a.records
                        if aidata.KINDS.get(r.kind) == "encounter" and (self.names().get(r.key) or "") == layer), None)
            if rec is None:
                raise ValueError(f"no encounter record for layer {layer!r}")
            base = aidata.to_json(rec[0].read(rec[1]), "encounter")
            doc = base
            ch = self._layout_changes(layer, "encounter")
            if ch:
                doc = modpack.apply_layout_changes(base, ch)
            import copy
            doc = copy.deepcopy(doc)
            for e in items:
                node = next((n for t, nm, n in aidata.items(doc) if t == "enc_" and nm == e["name"]), None)
                if node is None:
                    raise ValueError(f"layer {layer} has no encounter {e['name']!r}")
                kids = node["children"]
                psts = next((c for c in kids if c["tag"] == "PSTS"), None)
                if psts is None:
                    psts = {"tag": "PSTS", "children": []}
                    at = next((i for i, c in enumerate(kids) if c["tag"] == "SQDS"), len(kids))
                    kids.insert(at, psts)
                old = {next((c.get("text") for c in s["children"] if c["tag"] == "NAME"), ""): s
                       for s in psts["children"] if s["tag"] == "PSET"}
                sets = []
                for set_name, pts in e["points"].items():
                    if not pts:
                        continue
                    pts = [[round(float(v), 3) for v in p] for p in pts]
                    s = old.get(set_name)
                    if s is None:
                        s = {"tag": "PSET", "children": [{"tag": "NAME", "text": set_name},
                                                         {"tag": "INFO", "hex": "010000000000", "auto": True},
                                                         {"tag": "PNTS", "points": pts}]}
                    else:
                        for c in s["children"]:
                            if c["tag"] == "PNTS":
                                c["points"] = pts
                    sets.append(s)
                psts["children"] = sets
            changes = modpack.layout_changes(base, doc)
            p = self.mod / "layout" / f"{layer}.encounter.changes.json"
            if changes is None:
                raise ValueError(f"layer {layer}'s encounters can't be told apart by name")
            if changes["changed"] or changes["removed"]:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(changes, indent=1) + "\n", encoding="utf-8")
                written.append(f"layout/{p.name}")
            elif p.exists():
                p.unlink()
        return written

    def save_encounters(self, map_name, files, game=(), game_squads=None):
        """files: {layer file name: [encounter, ...]}, replacing the
        encounters of those mod layer files (new files are made for map_name);
        game: edits to the game's own encounters (see save_game_encounters);
        game_squads: {layer file name: [squad for a game encounter, ...]}
        (encounters.py "game_squads"), replacing those files' ones."""
        game_squads = game_squads or {}
        game_files = self.save_game_encounters(game) if game else []
        import re
        taken = {o["name"].lower() for o in self.all_objects() if o["name"]}
        world_layers = {(self.names().get(r.key) or "").lower() for a in self.archives for r in a.records
                        if r.kind == name_hash("world")}
        seen = set()
        for fname, sqs in game_squads.items():
            if not re.fullmatch(r"[A-Za-z0-9_]+\.json", fname):
                raise ValueError(f"layer file names use letters, digits and _ only: {fname!r}")
            for sq in sqs:
                for mem in sq.get("members", []):
                    n = mem["name"].lower()
                    if n in taken or n in seen:
                        raise ValueError(f"the name {mem['name']!r} is already used; members need new names")
                    seen.add(n)
                    mem["position"] = [round(float(v), 3) for v in mem["position"]]
        for fname, encs in files.items():
            if not re.fullmatch(r"[A-Za-z0-9_]+\.json", fname):
                raise ValueError(f"layer file names use letters, digits and _ only: {fname!r}")
            for enc in encs:
                sets = enc.get("points", {})
                for sq in enc.get("squads", []):
                    for s in sq.get("points", []):
                        if s not in sets:
                            raise ValueError(f"squad {sq['name']} uses spawn points {s!r}, which encounter "
                                             f"{enc['name']} doesn't have")
                    for mem in sq.get("members", []):
                        n = mem["name"].lower()
                        if n in taken or n in seen:
                            raise ValueError(f"the name {mem['name']!r} is already used; members need new names")
                        seen.add(n)
                        mem["position"] = [round(float(v), 3) for v in mem["position"]]
                for k in list(sets):
                    sets[k] = [[round(float(v), 3) for v in p] for p in sets[k]]
        for fname in sorted(set(files) | set(game_squads)):
            encs = files.get(fname)
            p = self.mod / "encounters" / fname
            if p.exists():
                doc = json.loads(p.read_text(encoding="utf-8"))
            else:
                if p.stem.lower() in world_layers:
                    raise ValueError(f"the game already has a layer named {p.stem!r}")
                doc = {"map": map_name.upper()}
                if not (self.mod / "missions" / f"{p.stem}.json").exists():
                    doc["load"] = "map"          # editor layers load with their map; a mission's with it
                self._require("0.65")
            if fname.lower() == self.editor_layer_file(map_name).lower() and "load" not in doc:
                doc["load"] = "map"
                self._require("0.65")
            if encs:
                doc["encounters"] = encs
            elif encs is not None:
                doc.pop("encounters", None)
            if fname in game_squads:
                if game_squads[fname]:
                    doc["game_squads"] = [{k: v for k, v in sq.items() if k != "file"} for sq in game_squads[fname]]
                    self._require("0.66")
                else:
                    doc.pop("game_squads", None)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        return {"files": [f"encounters/{f}" for f in sorted(set(files) | set(game_squads))] + game_files}

    def _require(self, version):
        """Raise mod.json "requires" to at least version."""
        from ..modbuild import _version
        meta_path = self.mod / "mod.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"name": self.mod.name}
        if _version(meta.get("requires", "0")) < _version(version):
            meta["requires"] = version
            meta_path.write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")

    def _rule(self, oid, e):
        src = self.find_object(oid)
        rule = find.rule_for(src)
        rule.pop("move", None)
        rows = [list(src["rotation"][0:3]), list(src["rotation"][3:6]), list(src["rotation"][6:9])]
        rule["position"] = [round(float(v), 3) for v in e["position"]]
        new = from_three(e["rotation"])
        if tilted(new) or tilted(rows):        # not upright (or wasn't): the whole rotation
            rule["rotation"] = [[round(v, 6) for v in row] for row in new]
            return rule
        turn = turn_between(rows, new)
        if abs(turn) > 1e-3:
            rule["turn"] = turn
        return rule
