"""Map data for the editor, and object edits as mod rules.

Coordinates: the game's world is right-handed, like three.js, so the editor
uses the game's (x, y, z) as they are (checked in game: a plaza's parking lot
on the +z side shows on the left looking -x). An object's stored rotation is
three rows, the object's own axes in world space.
"""
import json
import math
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
        return sorted(set(out))

    def _modded_terrain(self):
        """The context mods' terrain steps, applied as a build would (once)."""
        if self._terrain is None:
            from .. import modbuild
            gen = getattr(self, "_terrain_gen", 0)
            data = modbuild._Data(self.data)
            for p in self.context:
                if (Path(p) / "terrain").is_dir():
                    data.project = modbuild.project_name(p)
                    modbuild._apply_terrain(data, Path(p), modbuild.Result())
            # A save during the build makes it stale: use it this once, keep nothing.
            if gen != getattr(self, "_terrain_gen", 0):
                return data
            self._terrain = data
        return self._terrain

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
        rec = next(((a, r) for a, r in data.records(name_hash("Terrain")) if r.key == name_hash(name)), None)
        if rec is None:
            raise KeyError(name)
        chunks = terrain.tern_chunks(data.current(*rec))
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
            rec = next(((a, r) for a, r in data.records(name_hash("Terrain")) if r.key == name_hash(name)), None)
            if rec is None:
                raise KeyError(name)
            chunks = terrain.tern_chunks(data.current(*rec))
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
        rec = next(((a, r) for a, r in data.records(name_hash("Terrain")) if r.key == name_hash(name)), None)
        if rec is None:
            raise KeyError(name)
        doc = terrain_decals.export(terrain.tern_chunks(data.current(*rec)))
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
            rules.append({**self._rule(c["source"], c), "copy": True, "as": new_name})
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
                doc = {"map": map_name.upper(), "load": "map"}       # editor layers load with their map
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
