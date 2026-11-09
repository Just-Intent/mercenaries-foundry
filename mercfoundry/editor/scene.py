"""Map data for the editor, and object edits as mod rules.

Coordinates: the game is left-handed; the editor (three.js, like the glTF
export) uses (x, y, -z). An object's stored rotation is three rows, the
object's own axes in world space.
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
    g = [[rows[c][r] for c in range(3)] for r in range(3)]     # columns are the axes
    s = (1, 1, -1)
    m = [[g[r][c] * s[r] * s[c] for c in range(3)] for r in range(3)]
    return [m[r][c] for c in range(3) for r in range(3)]


def from_three(m3):
    """Game rotation rows from a column-major 3x3 three.js rotation."""
    s = (1, 1, -1)
    m = [[m3[c * 3 + r] for c in range(3)] for r in range(3)]
    g = [[m[r][c] * s[r] * s[c] for c in range(3)] for r in range(3)]
    return [[g[r][c] for r in range(3)] for c in range(3)]


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

    def maps(self):
        tern = name_hash("Terrain")
        out = []
        for a in self.archives:
            for r in a.records:
                if r.kind == tern:
                    n = self.names().get(r.key)
                    if n:
                        out.append(n.lower())
        return sorted(set(out))

    def _modded_terrain(self):
        """The context mods' terrain steps, applied as a build would (once)."""
        if self._terrain is None:
            from .. import modbuild
            data = modbuild._Data(self.data)
            for p in self.context:
                if (Path(p) / "terrain").is_dir():
                    data.project = modbuild.project_name(p)
                    modbuild._apply_terrain(data, Path(p), modbuild.Result())
            self._terrain = data
        return self._terrain

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
        meta = {"name": name, "size": size, "spacing": sp,
                "x0": -size * sp / 2, "z0": -(size * sp / 2 - sp)}
        return meta, struct.pack(f"<{size * size}f", *(h for row in heights for h in row))

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
                        "model": o["model"], "position": list(o["position"]), "rotation": to_three(rows)})
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

    def save_edits(self, moved, copies, removed=()):
        """Write objects/editor.json from {id: {position, rotation}},
        [{source, as, position, rotation}] and [id, ...] to remove. Returns
        the rule count."""
        # Copies first: a copy finds its source by its original position,
        # which a move rule for the same source would change.
        rules = []
        names = {o["name"].lower() for o in self.all_objects() if o["name"]}
        for c in copies:
            new_name = c.get("as") or ""
            if not new_name:
                raise ValueError("a placed copy needs a name")
            if new_name.lower() in names:
                raise ValueError(f"the game already has an object named {new_name!r}")
            names.add(new_name.lower())
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
        p = self.rules_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rules, indent=1) + "\n", encoding="utf-8")
        return len(rules)

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
