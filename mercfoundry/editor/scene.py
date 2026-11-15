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

    def new_map(self, name, src="blank"):
        """A new map: maps/<name>.json, a copy of a game map (blank: flat,
        nearly empty). The build makes its records, level and master script."""
        name = str(name or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            raise ValueError("a map name is letters, digits and _")
        if name.lower() in self.maps():
            raise ValueError(f"there's already a map named {name}")
        src = str(src or "blank").lower()
        if src not in self.game_maps():
            raise ValueError(f"{src} isn't a game map")
        (self.mod / "maps").mkdir(parents=True, exist_ok=True)
        (self.mod / "maps" / f"{name.lower()}.json").write_text(json.dumps({"from": src}, indent=1) + "\n",
                                                                encoding="utf-8")
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
            return {"new": False}
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
                "layers": layers, "own_script": (self.mod / "scripts" / f"{name.lower()}.lua").exists()}

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
        """The context mods' new-map files and when each last changed."""
        out = []
        for p in self.context:
            mdir = Path(p) / "maps"
            if mdir.is_dir():
                out += [(str(f), f.stat().st_mtime_ns) for f in sorted(mdir.glob("*.json"))]
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
                "template_allies_humvee_empty"]
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
        if int(which) == 2:
            layer, doc = self._test_mission_2(aid, enc, at, square, squad)
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

    def missions(self, map_name):
        """The mod's missions on this map, each with its layer's squads and
        zones, and the next free number per faction."""
        from .. import missions as ms
        m = map_name.lower()
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
            if mp != m:
                continue
            doc = json.loads(p.read_text(encoding="utf-8"))
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
        taken = self._arc_numbers(m)
        nxt = {f: max([8] + sorted(taken[f])) + 1 for f in ms.FACTIONS}
        return {"missions": out, "next": nxt, "factions": list(ms.FACTIONS),
                "squads": shared_squads, "zones": shared_zones, "paths": shared_paths, "sets": shared_sets,
                "hq": self._hq_points(m)}

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
        return {"level": True, "files": [f[len("DATAxbox/"):] for f in files], "base": sk.base(texts[files[0]]),
                "presets": presets, "mod": mod, "textures": self._sky_textures,
                "preset_keys": list(sk.PRESET_KEYS), "base_keys": list(sk.BASE_KEYS)}

    def save_sky(self, map_name, doc):
        """Write sky/<map>.json (checked by applying it to the game's files);
        an empty one is removed."""
        from .. import sky as sk
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
        clean = {k: v for k, v in clean.items() if v}
        sk.apply(texts, clean, f"sky/{map_name.lower()}.json")        # raises on anything the build would refuse
        p = self.mod / "sky" / f"{map_name.lower()}.json"
        if clean:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(clean, indent=1) + "\n", encoding="utf-8")
        elif p.exists():
            p.unlink()
        return {"file": f"sky/{p.name}", "empty": not clean}

    def factions(self):
        """The mod's factions.json and the game's names for the four employers."""
        from .. import factions as fx
        p = self.mod / "factions.json"
        doc = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        return {"doc": doc, "factions": list(fx.FACTIONS),
                "old": {f: [o for o, _ in fx.OLD[f]] for f in fx.FACTIONS}}

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
        clean = {f: {k: str(v).strip() for k, v in (doc.get(f) or {}).items() if str(v).strip()}
                 for f in fx.FACTIONS}
        clean = {f: v for f, v in clean.items() if v.get("name")}
        if clean and doc.get("everywhere"):
            clean["everywhere"] = True
        if clean and doc.get("subtitles"):
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
        asset_id = f"{map_name.lower()}_{mission['faction']}{int(mission['number'])}"
        ms.parse_id(asset_id)
        for i, o in enumerate(mission.get("objectives") or [], 1):
            if o.get("type") not in ms.OBJECTIVE_TYPES:
                raise ValueError(f"objective {i}: unknown type {o.get('type')!r}")
            # An objective without its squad or zone yet is kept as a draft;
            # the build skips writing the script until it's complete.
        p = self.mod / "missions" / f"{asset_id}.json"
        doc = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
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
        if getattr(self, "_ctx_world", None) is None:
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
        if own.is_file() and (self.mod / "models" / f"{name}.new.json").is_file():
            return own.read_bytes()          # the mod's new model, as it'll be imported
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
            new_rows = _turn(rows, float(rule.get("turn", 0)))
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

    def layout(self, map_name):
        from .. import aidata, modpack
        m = map_name.lower()
        items = []
        for a in self.archives:
            for r in a.records:
                rkind = aidata.KINDS.get(r.kind)
                if rkind not in ("path", "lineregion"):
                    continue
                layer = self.names().get(r.key)
                if not layer or not self._in_map(layer, m):
                    continue
                doc = aidata.to_json(a.read(r), rkind)
                ch = self._layout_changes(layer, rkind)
                if ch:
                    doc = modpack.apply_layout_changes(doc, ch)
                for tag, name, node in aidata.items(doc):
                    if tag not in ("path", "lrg_") or not name:
                        continue
                    kids = node["children"]
                    pts = next((c["points"] for c in kids if c["tag"] == "PNTS"), [])
                    prop = next((c for c in kids if c["tag"] == "PROP"), {})
                    props = dict(prop.get("pairs") or [(k, v) for k, v in prop.get("values", [])])
                    kind = "path" if tag == "path" else "boundary"
                    items.append({"id": f"game:{layer}:{kind}:{name}", "source": "game", "layer": layer,
                                  "kind": kind, "name": name, "points": pts,
                                  "properties": {k: str(v) for k, v in props.items()}})
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
        return {"items": items, "files": files}

    def save_layout(self, map_name, game_edits, mod_files):
        """game_edits: [{layer, kind, name, points}] for game items (same
        point count). mod_files: {file name: {"paths": {...}, "boundaries":
        {...}}}, each replacing those two parts of a mod layer file (a new
        file is made for map_name). Returns a summary."""
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
        return done

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
                            "rotation": to_three(_rotation(float(o.get("yaw", 0))))})
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
            if fname.lower() == own or not p.exists():
                doc["load"] = "map"
            out = []
            for o in objs:
                rows = from_three(o["rotation"])
                item = {"from": o["from"], "name": o["name"],
                        "position": [round(float(v), 3) for v in o["position"]],
                        "yaw": round(math.degrees(math.atan2(-rows[0][2], rows[0][0])), 2)}
                if o.get("set"):
                    item["set"] = o["set"]
                out.append(item)
            if out:
                doc["objects"] = out
            else:
                doc.pop("objects", None)
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
        turn = turn_between(rows, from_three(e["rotation"]))
        if abs(turn) > 1e-3:
            rule["turn"] = turn
        return rule
