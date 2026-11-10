"""Build an install-ready mod from a folder of readable edits.

Project layout (every part optional):

    mod.json              {"name": ..., "description": ...}
    text/*.json           {"dotted.key": "new text", ...}; unknown keys are added
    scripts/<name>.lua    replaces the script <name>, or adds it if it's new
    objects/*.json        object rules, a rule or a list of rules. Match on
                          "name" (the object's name; "template" also works)
                          and/or "model", and/or "near" [x, y, z] (within
                          "radius" metres, default 0.5), optionally within
                          one "layer".
                          Actions: "set" {property: value} (new properties
                          are appended), "move" [dx, dy, dz], "position"
                          [x, y, z] (metres), "turn" degrees about the
                          vertical axis, and "copy": true with "as": a new
                          name, to place a duplicate (other actions then
                          apply to the copy).
    encounters/<layer>.json  a new world layer: objects copied from existing
                          ones, encounters, point sets and squads (see
                          docs/ENCOUNTERS.md); load it from a script
    missions/<map>_<faction><n>.json  a new contract: added to the
                          faction's arc, its script listed in the map, a
                          mission.ini section (supply the script and layer)
    textures/<name>.png   replaces the texture <name>, or adds it if it's new
    models/<name>.obj|.glb|.gltf  an edited export from the `model` command
    models/<name>.collision.json  {"shapes": {"0": 7}}: sets collision
                          shape flags (see docs/formats/dsk.md)
    animations/*.glb|.gltf   clips replace the game animations with the same
                          names (export them with `model --glb --anim`)
    animations/new.json   {"new clip": "template clip"}: clips with these
                          names are added as new animations, built on the
                          template's skeleton and loaded wherever it is
    terrain/<name>.heights.png  replaces a terrain's heights (from the
                          `terrain` command; keep the .heights.json beside it)
    terrain/<name>.layerNN.*.png  repaints ground layer NN (from `terrain`);
                          <name>.layers.json changes or adds layer textures;
                          <name>.decals.json replaces decals or lays roads
    animtables/<name>.json   replaces a character's animation table (from
                          the `animtable` command): point handles at clips
    layout/<layer>.<kind>.json  replaces a layer's path, lineregion, region
                          or encounter data (from `layout` or `extract
                          --decode`); layout/<layer>.list.txt replaces its
                          asset load list

`build` writes only the archives that change into the output folder, which
can be dropped into Mercenaries Recompiled's mods/ folder as is.
"""
import copy
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import dsk, model, model_import, png, registry, script, texture, world
from .hashing import name_hash

SCRIPT, WORLD, REGISTRY = name_hash("script"), name_hash("world"), name_hash("registry")
ARCHIVES = ("assets.dsk", "english.dsk", "locked.dsk", "streamed.dsk")

EXAMPLE_README = """# {name}

Edit the files in this folder, then build:

    python -m mercfoundry mod build . --data path/to/DATAxbox --out path/to/mods/{name}

- text/*.json: {{"interface.shell.newgame": "NEW GAME"}}; new keys are added.
- scripts/<name>.lua: replaces or adds the script <name>.
- objects/*.json: {{"template": "player_humvee", "set": {{"physicsenginetorque": "3400"}}}}
- textures/<name>.png: replaces or adds the texture <name> (power-of-two sizes).
- models/<name>.obj (or .glb/.gltf): an edited export from `python -m mercfoundry model`.
- sounds/<sound bank>/<cue>.wav: replaces the sound a cue plays (export them with `sound export`).

Get the originals to edit with `extract --decode`, `texture`, `model` and `sound export`.
"""


@dataclass
class Result:
    written: list = field(default_factory=list)      # archive file names
    files: list = field(default_factory=list)        # loose game files, "DATAxbox/..."
    records: dict = field(default_factory=dict)      # archive -> {(key, kind)} changed or added
    changes: list = field(default_factory=list)      # human-readable lines
    warnings: list = field(default_factory=list)


class _Data:
    """Original archives, opened on demand, with pending changes."""

    def __init__(self, data_dir):
        self.dir = Path(data_dir)
        self.archives = {}
        self.replace = {}   # archive name -> {(key, kind): bytes}
        self.add = {}       # archive name -> [(key, kind, bytes)]
        self.files = {}     # loose game files, "DATAxbox/SW/SWN.ini" -> bytes
        # Several mods built together: which mod is applying, which mod last
        # changed each record, and which records each mod read first. A mod
        # that changes a record another mod changed without reading it
        # replaces that change (reported in self.conflicts).
        self.project = None
        self.changed_by = {}    # (archive, ident) -> mod name
        self.added_by = {}      # (key, kind) -> mod name
        self.reads = set()      # (mod name, archive, ident)
        self.conflicts = []
        self._names = None

    def archive(self, name):
        if name not in self.archives:
            path = self.dir / name
            if not path.exists():
                raise FileNotFoundError(f"{path} not found; --data should point at DATAxbox")
            self.archives[name] = dsk.Archive(path)
        return self.archives[name]

    def records(self, kind, names=ARCHIVES):
        """Yield (archive name, record) for every record of a kind."""
        for name in names:
            if (self.dir / name).exists():
                for r in self.archive(name).records:
                    if r.kind == kind:
                        yield name, r

    def current(self, name, rec):
        self.reads.add((self.project, name, rec.ident))
        pending = self.replace.get(name, {}).get(rec.ident)
        return pending if pending is not None else self.archive(name).read(rec)

    def peek(self, name, rec):
        """Like current(), for a step that replaces the record outright (so
        building on it doesn't count as merging with earlier mods)."""
        pending = self.replace.get(name, {}).get(rec.ident)
        return pending if pending is not None else self.archive(name).read(rec)

    def set(self, name, rec, data):
        prev = self.changed_by.get((name, rec.ident))
        if prev is not None and prev != self.project and (self.project, name, rec.ident) not in self.reads:
            self.conflicts.append(f"{self.project} replaces {self.label(rec.key, rec.kind)}, which {prev} "
                                  f"also changes; {prev}'s change to it is lost")
        if data != self.archive(name).read(rec):
            self.replace.setdefault(name, {})[rec.ident] = data
            self.changed_by[(name, rec.ident)] = self.project
        else:
            self.replace.get(name, {}).pop(rec.ident, None)

    def new(self, name, key, kind, data):
        prev = self.added_by.get((key, kind))
        if prev is not None:
            raise ValueError(f"{prev} and {self.project} both add {self.label(key, kind)}" if prev != self.project
                             else f"{self.label(key, kind)} is added twice")
        self.added_by[(key, kind)] = self.project
        self.add.setdefault(name, []).append((key, kind, data))

    def label(self, key, kind):
        """'<kind> <name>' for messages, naming the record from the archives."""
        if self._names is None:
            from .hashing import type_book
            self._names = type_book()
            for n in ARCHIVES:
                if (self.dir / n).exists():
                    dsk.collect_names(self.archive(n), self._names)
            for recs in self.add.values():
                for k, kd, body in recs:
                    found = dsk.record_name(body)
                    if found:
                        self._names.add(found)
        return f"{dsk.label(self._names, kind)} {dsk.label(self._names, key)}"

    def replace_added(self, key, kind, data):
        """Change the body of a record added earlier in this build."""
        for recs in self.add.values():
            for i, (k, kd, _) in enumerate(recs):
                if k == key and kd == kind:
                    recs[i] = (k, kd, data)
                    return True
        return False

    def find_added(self, key, kind):
        for recs in self.add.values():
            for k, kd, body in recs:
                if k == key and kd == kind:
                    return body
        return None

    def added(self):
        """(key, kind, archive name) of every record added so far."""
        return [(k, kind, a) for a, recs in self.add.items() for k, kind, _ in recs]


def new_project(folder, name=None):
    folder = Path(folder)
    name = name or folder.resolve().name
    for sub in ("text", "scripts", "objects", "encounters", "missions", "textures", "models", "layout", "animations",
                "animtables", "terrain", "sounds"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    meta = folder / "mod.json"
    if not meta.exists():
        meta.write_text(json.dumps({"name": name, "description": ""}, indent=1) + "\n", encoding="utf-8")
    readme = folder / "README.md"
    if not readme.exists():
        readme.write_text(EXAMPLE_README.format(name=name), encoding="utf-8")
    return folder


def _apply_text(data, folder, result):
    files = sorted((folder / "text").glob("*.json"))
    if not files:
        return
    edits = {}
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            raise ValueError(f"{f.name}: expected an object of key -> text")
        edits.update({str(k): str(v) for k, v in doc.items()})
    changed, added = _set_strings(data, edits)
    result.changes.append(f"text: {changed} strings changed, {added} entries added")


def _set_strings(data, edits):
    """Set text strings (key -> text) in english.dsk; new keys go into the
    registry record holding the key with the longest shared prefix.
    Returns (changed, added)."""
    regs = [(name, rec, registry.Registry(data.current(name, rec)))
            for name, rec in data.records(REGISTRY, ("english.dsk",))]
    if not regs:
        raise ValueError("no text archive (english.dsk) found")
    owner = {}
    for i, (_, _, reg) in enumerate(regs):
        for key in reg.strings():
            owner.setdefault(key, i)
    plans = {i: {} for i in range(len(regs))}
    for key, text in edits.items():
        if key in owner:
            plans[owner[key]][key] = text
            continue
        # New key: put it with the existing key sharing the longest prefix.
        best, best_len = 0, -1
        parts = key.split(".")
        for existing, i in owner.items():
            n = 0
            for a, b in zip(parts, existing.split(".")):
                if a.lower() != b.lower():
                    break
                n += 1
            if n > best_len:
                best, best_len = i, n
        plans[best][key] = text
    changed = added = 0
    for i, (name, rec, reg) in enumerate(regs):
        if plans[i]:
            c, a = reg.apply(plans[i])
            changed, added = changed + c, added + a
            data.set(name, rec, reg.to_bytes())
    return changed, added


def _apply_scripts(data, folder, result):
    # scripts/<name>.lua.diff: a change to a game script (mod pack writes
    # these). It applies to the script as earlier mods left it, so two mods'
    # diffs to one script both apply when they touch different lines.
    from . import modpack
    for f in sorted((folder / "scripts").glob("*.lua.diff")):
        name = f.name[:-len(".lua.diff")]
        hit = [(a, r) for a, r in data.records(SCRIPT) if r.key == name_hash(name)]
        if not hit:
            raise ValueError(f"scripts/{f.name}: no game script named {name!r} to change")
        diff = f.read_bytes().decode("latin-1")
        for a, r in hit:
            old = script.decode(data.current(a, r))
            try:
                text = modpack.apply_diff(old.source, diff)
            except ValueError as err:
                raise ValueError(f"scripts/{f.name}: {err}") from err
            data.set(a, r, script.encode(script.with_source(old, text)))
        result.changes.append(f"script {name}: changed by {f.name}")
    for f in sorted((folder / "scripts").glob("*.lua")):
        name = f.stem
        source = f.read_bytes().decode("latin-1")
        key = name_hash(name)
        hit = [(a, r) for a, r in data.records(SCRIPT) if r.key == key]
        if hit:
            for a, r in hit:
                old = script.decode(data.peek(a, r))
                text = source if b"\r\n" in old.text else source.replace("\r\n", "\n")
                data.set(a, r, script.encode(script.with_source(old, text)))
            result.changes.append(f"script {name}: replaced")
        else:
            data.new("assets.dsk", key, SCRIPT, script.encode(script.new(name, source.replace("\r\n", "\n"))))
            result.changes.append(f"script {name}: added")


ACTIONS = ("set", "move", "position", "turn", "copy", "remove")


def _rules(folder, skip=()):
    for f in sorted((folder / "objects").glob("*.json")):
        if f.name in skip:
            continue
        doc = json.loads(f.read_text(encoding="utf-8"))
        for rule in doc if isinstance(doc, list) else [doc]:
            if not isinstance(rule, dict) or not any(k in rule for k in ACTIONS):
                raise ValueError(f"{f.name}: each rule needs one of {', '.join(ACTIONS)} (a mod made with a "
                                 f"newer toolkit may use actions this one doesn't know)")
            if "set" in rule and not isinstance(rule["set"], dict):
                raise ValueError(f"{f.name}: \"set\" must be an object of property -> value")
            if not (rule.get("name") or rule.get("template") or rule.get("model") or rule.get("near")):
                raise ValueError(f"{f.name}: each rule needs a \"name\", \"model\" or \"near\" to match")
            if "near" in rule and (not isinstance(rule["near"], list) or len(rule["near"]) != 3):
                raise ValueError(f"{f.name}: \"near\" must be [x, y, z]")
            if rule.get("remove") and any(k in rule for k in ("set", "move", "position", "turn", "copy")):
                raise ValueError(f"{f.name}: a \"remove\" rule can't also change or copy the object")
            if rule.get("copy") and not rule.get("as"):
                raise ValueError(f"{f.name}: \"copy\" needs \"as\", the new object's name")
            for key in ("move", "position"):
                if key in rule and (not isinstance(rule[key], list) or len(rule[key]) != 3):
                    raise ValueError(f"{f.name}: \"{key}\" must be [x, y, z]")
            yield f.name, rule


def _turn(rotation, degrees):
    """Rotate an object's axes (rotation rows) about the vertical (Y) axis."""
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    return [[r[0] * c + r[2] * s, r[1], -r[0] * s + r[2] * c] for r in rotation]


def _to_marker(inst):
    """An object's place in its layer kept as an invisible, unnamed location
    marker: saved games know objects by their place in the layer."""
    inst.update(model="global_location", template="",
                properties=[["GeometryFile", "global_humanmarker"], ["objectType", "location"]])


def _apply_rule(inst, rule):
    for prop, value in rule.get("set", {}).items():
        for pair in inst["properties"]:
            if pair[0].lower() == prop.lower():
                pair[1] = str(value)
                break
        else:
            inst["properties"].append([prop, str(value)])
    if any(k in rule for k in ("move", "position", "turn")) and "position" not in inst:
        raise ValueError(f"object {inst['template']!r} has no transform to change")
    if "position" in rule:
        inst["position"] = [float(v) for v in rule["position"]]
    if "move" in rule:
        inst["position"] = [p + float(d) for p, d in zip(inst["position"], rule["move"])]
    if "turn" in rule:
        inst["rotation"] = _turn(inst["rotation"], float(rule["turn"]))


# obstaclematerialtype values the game knows (0x12B5F0); anything else is 0 ("none")
MATERIALS = ("wood", "solidmetal", "sheetmetal", "vehicle", "dirt", "concrete", "rock", "plant",
             "snow", "glass", "rubber", "chain", "none")


def _world_index(data, archives):
    """Every world object in the game's archives, by name and (unnamed ones)
    by model, in archive order; built once per build, since scanning all
    objects takes seconds."""
    if getattr(data, "world_index", None) is None:
        from . import find
        by_name, by_model = {}, {}
        for obj in find.objects(archives):
            if obj.get("name"):
                by_name.setdefault(obj["name"].lower(), []).append(obj)
            elif obj.get("model"):
                by_model.setdefault(obj["model"].lower(), []).append(obj)
        data.world_index = (by_name, by_model)
    return data.world_index


def _add_traffic(data, f, layer, doc, result):
    """A layer's "traffic" zones, declared in its map's level config (a loose
    DATAxbox file, like terrain surfaces)."""
    from . import encounters, terrain_surfaces
    map_ = doc.get("map") or ""
    levels = data.dir / "levels.ini"
    config = terrain_surfaces.level_config(levels.read_text(encoding="latin-1"), map_) if levels.exists() else None
    if not config or not (data.dir / config).exists():
        raise ValueError(f"encounters/{f.name}: traffic needs the layer's \"map\" to be a level (e.g. SW); "
                         f"no level config for {map_!r}")
    path = "DATAxbox/" + config.replace("\\", "/")
    ini = data.files[path].decode("latin-1") if path in data.files else \
        (data.dir / config).read_bytes().decode("latin-1")
    try:
        ini = encounters.add_traffic_zones(ini, doc["traffic"])
    except ValueError as err:
        raise ValueError(f"encounters/{f.name}: {err}") from err
    data.files[path] = ini.encode("latin-1")
    for zone in doc["traffic"]:
        routes = encounters.zone_paths(doc, zone)
        if not routes:
            result.warnings.append(f"encounters/{f.name}: traffic zone {zone} has no routes; name paths "
                                   f"t_{zone}_01 (vehicles) or p_{zone}_01 (people)")
    result.changes.append(f"layer {layer}: traffic zone{'s' if len(doc['traffic']) != 1 else ''} "
                          f"{', '.join(doc['traffic'])} declared in {config}")


def _apply_encounters(data, folder, result):
    """encounters/<layer>.json: a new world layer (objects, encounter, list)."""
    from . import encounters, find
    files = sorted((folder / "encounters").glob("*.json")) if (folder / "encounters").is_dir() else []
    if not files:
        return
    archives = [data.archive(n) for n in ARCHIVES if (data.dir / n).exists()]
    existing = {r.key for _, r in data.records(WORLD)}
    layers = {}

    current_map = [""]
    by_name, by_model = _world_index(data, archives)

    def source_hit(name):
        """The object to copy: a named one, or for "model:<model>" the first
        unnamed scenery object with that model (preferring this map's layers)."""
        if name.lower().startswith("model:"):
            m = name[6:]
            hits = sorted(by_model.get(m.lower(), []),
                          key=lambda h: not h["layer"].lower().startswith(current_map[0]))
            if not hits:
                raise ValueError(f"no world object with model {m!r} to copy")
        else:
            hits = by_name.get(name.lower(), [])
            if not hits:
                raise ValueError(f"no world object named {name!r} to copy")
        return hits[0]

    def find_source(name):
        hit = source_hit(name)
        layer = hit["layer"]
        if layer not in layers:
            a, r = next((a, r) for a, r in data.records(WORLD) if r.key == name_hash(layer))
            layers[layer] = world.to_json(data.current(a, r))
        insts = [c for c in layers[layer]["chunks"] if c.get("tag") == "inst"]
        if name.lower().startswith("model:"):
            return insts[hit["index"]]
        return next(c for c in insts if c.get("template", "").lower() == name.lower())

    lists = {}

    def source_loads(name):
        layer = source_hit(name)["layer"]
        if layer not in lists:
            hit = [(a, r) for a, r in data.records(name_hash("list")) if r.key == name_hash(layer)]
            lists[layer] = encounters.parse_list(data.current(*hit[0]).decode("latin-1")) if hit else {}
        return lists[layer]

    for f in files:
        layer = f.stem
        if name_hash(layer) in existing:
            raise ValueError(f"encounters/{f.name}: the game already has a layer named {layer!r}")
        doc = json.loads(f.read_text(encoding="utf-8"))
        current_map[0] = (doc.get("map") or "").lower()
        records = encounters.build(layer, doc, find_source, source_loads)
        for kind, body in records.items():
            data.new("assets.dsk", name_hash(layer), kind, body)
        if doc.get("traffic"):
            _add_traffic(data, f, layer, doc, result)
        if doc.get("load"):
            if doc["load"] != "map":
                raise ValueError(f"encounters/{f.name}: \"load\" is \"map\" (load with its map in free roam)")
            from . import missions
            m = (doc.get("map") or "").lower()
            if m not in missions.FREE_ROAM_MAPS:
                result.warnings.append(f"encounters/{f.name}: \"load\": \"map\" works on the free-roam maps "
                                       f"({', '.join(missions.FREE_ROAM_MAPS).upper()}), not {m.upper() or 'no map'}; "
                                       f"load this layer from a script")
            else:
                names = encounters.member_names(doc)
                data.map_layers = getattr(data, "map_layers", []) + [
                    {"map": m, "layer": layer, "check": names[0] if names else None}]
        n = len(encounters.member_names(doc))
        loads = (f"loads with the {(doc.get('map') or '').upper()} map in free roam" if doc.get("load") == "map"
                 and (doc.get("map") or "").lower() in ("sw", "nw")
                 else f"load it from a script with Utility_ReadLevelFile('{layer}')")
        result.changes.append(f"layer {layer}: {n} objects, {len(doc.get('encounters', []))} encounters ({loads})")


def _mission_order(projects):
    """(project, mod name, mission file) for every mod's missions, ordered so
    a mission that goes "after" another mod's mission is added after it."""
    from . import missions
    items = []
    for project, pname in projects:
        d = project / "missions"
        for f in sorted(d.glob("*.json")) if d.is_dir() else []:
            map_, faction, number = missions.parse_id(f.stem.lower())
            after = json.loads(f.read_text(encoding="utf-8")).get("after")
            items.append(((map_, faction, number), (map_, faction, after), project, pname, f))
    provided = {it[0] for it in items}
    done, ordered = set(), []
    while len(ordered) < len(items):
        progress = False
        for it in items:
            if it[0] in done:
                continue
            if it[1] in provided and it[1] not in done and it[1] != it[0]:
                continue
            ordered.append(it)
            done.add(it[0])
            progress = True
        if not progress:
            raise ValueError("missions go after each other in a loop: " +
                             ", ".join(it[4].stem for it in items if it[0] not in done))
    return [(project, pname, f) for _, _, project, pname, f in ordered]


def _apply_missions(data, folder, result, files=None):
    """missions/<map>_<faction><n>.json: a new contract in a faction's arc."""
    from . import missions
    if files is None:
        files = sorted((folder / "missions").glob("*.json")) if (folder / "missions").is_dir() else []
    contracts = []
    for f in files:
        asset_id = f.stem.lower()
        map_, faction, number = missions.parse_id(asset_id)
        doc = json.loads(f.read_text(encoding="utf-8"))
        hit = [(a, r) for a, r in data.records(SCRIPT) if r.key == name_hash("util_misc")]
        if not hit:
            raise ValueError("the game data has no util_misc script (needed for mission arcs)")
        for a, r in hit:
            old = script.decode(data.current(a, r))
            text = missions.add_to_arc(old.source, faction, map_, number, doc.get("after"))
            data.set(a, r, script.encode(script.with_source(old, text)))
        lists = [(a, r) for a, r in data.records(name_hash("list")) if r.key == name_hash(map_)]
        if not lists:
            raise ValueError(f"the game data has no {map_} load list")
        for a, r in lists:
            text_list = missions.add_to_list(data.current(a, r).decode("latin-1"), "script", asset_id)
            data.set(a, r, text_list.encode("latin-1"))
        path = "DATAxbox/mission.ini"
        ini = data.files[path].decode("latin-1") if path in data.files else \
            (data.dir / "mission.ini").read_bytes().decode("latin-1")
        _, probe_section = missions.add_to_mission_ini(ini, asset_id)       # the section name only
        strings, ini_text, mail = missions.text_plan(asset_id, doc, probe_section)
        ini, section = missions.add_to_mission_ini(ini, asset_id, {**ini_text, **(doc.get("ini") or {})})
        data.files[path] = ini.encode("latin-1")
        if mail:
            mpath = "DATAxbox/DATAPOD/emails.ini"
            mails = data.files[mpath].decode("latin-1") if mpath in data.files else \
                (data.dir / "DATAPOD" / "emails.ini").read_bytes().decode("latin-1")
            data.files[mpath] = missions.add_mail(mails, *mail).encode("latin-1")
        # Titles and descriptions go in the always-loaded missioninfo text;
        # the mission's own strings (mail, objectives) in a text record named
        # after the mission, which its layer loads, as retail missions do.
        shared = {k: v for k, v in strings.items() if k.lower().startswith("missioninfo.")}
        own = {k: v for k, v in strings.items() if k not in shared}
        if shared:
            _set_strings(data, shared)
        if own:
            if any(r.key == name_hash(asset_id) for _, r in data.records(REGISTRY, ("english.dsk",))):
                raise ValueError(f"missions/{f.name}: english.dsk already has text named {asset_id}")
            reg = registry.Registry(registry.new_record())
            reg.apply(own)
            data.new("english.dsk", name_hash(asset_id), REGISTRY, reg.to_bytes())
            layer_list = data.find_added(name_hash(asset_id), name_hash("list"))
            if layer_list is None:
                result.warnings.append(f"missions/{f.name}: its strings are in text {asset_id}, which only loads "
                                       f"with layer {asset_id} (add encounters/{asset_id}.json)")
            else:
                text_list = missions.add_to_list(layer_list.decode("latin-1"), "registry", asset_id)
                data.replace_added(name_hash(asset_id), name_hash("list"), text_list.encode("latin-1"))
        known = {r.key for _, r in data.records(SCRIPT)} | {k for k, kind, _ in data.added() if kind == SCRIPT}
        if name_hash(asset_id) not in known:
            result.warnings.append(f"missions/{f.name}: no script {asset_id} (add scripts/{asset_id}.lua)")
        if not (folder / "encounters" / f"{asset_id}.json").exists():
            result.warnings.append(f"missions/{f.name}: no layer {asset_id} (mission_GenericInit loads it; "
                                   f"add encounters/{asset_id}.json)")
        arc = missions.arc(text, faction, map_)
        extras = (f"{len(strings)} strings, " if strings else "") + ("opening mail, " if mail else "")
        if doc.get("contact", True) is not False:
            contact, contact_strings = missions.contract(
                asset_id, doc, missions.section_value(ini, section, "MissionTitleDbKey"),
                missions.section_value(ini, section, "ShortDescriptionDbKey"))
            contracts.append(contact)
            _set_strings(data, contact_strings)
            extras += f"a contact at the {faction} HQ, "
        result.changes.append(f"mission {asset_id}: [{section}] in mission.ini, {extras}script listed in {map_}, "
                              f"{faction} {map_} arc now {arc} "
                              f"(start it with DebugSkipToMission('{faction}', {number}))")
    return contracts


def _apply_contracts(data, contracts, result):
    """Contacts at faction HQs that offer mod missions (missions never come up
    in the HQ's own contract order, which is tied to fixed arc positions)."""
    from . import missions
    body = missions.contracts_script(contracts)
    key = name_hash(missions.CONTRACTS_SCRIPT)
    if any(r.key == key for _, r in data.records(SCRIPT)):
        raise ValueError(f"the game already has a script named {missions.CONTRACTS_SCRIPT}")
    data.new("assets.dsk", key, SCRIPT, script.encode(script.new(missions.CONTRACTS_SCRIPT, body)))
    for map_ in sorted({c["map"] for c in contracts} | {"sw", "nw"}):
        for a, r in data.records(name_hash("list")):
            if r.key == name_hash(map_):
                text_list = missions.add_to_list(data.current(a, r).decode("latin-1"), "script",
                                                 missions.CONTRACTS_SCRIPT)
                data.set(a, r, text_list.encode("latin-1"))
    hit = [(a, r) for a, r in data.records(SCRIPT) if r.key == name_hash("global")]
    if not hit:
        raise ValueError("the game data has no global script to hook the contacts into")
    for a, r in hit:
        old = script.decode(data.current(a, r))
        data.set(a, r, script.encode(script.with_source(old, missions.patch_global(old.source))))
    result.changes.append(f"contacts: {', '.join(c['id'] for c in contracts)} offered at faction HQs "
                          f"(global's LoadNoMissionState runs {missions.CONTRACTS_SCRIPT})")


def _apply_map_layers(data, result):
    """Mod layers marked "load": "map": a generated foundry_layers script loads
    them when free roam starts on their map (hooked into global's
    LoadNoMissionState, like the HQ contacts)."""
    from . import missions
    key = name_hash(missions.LAYERS_SCRIPT)
    if any(r.key == key for _, r in data.records(SCRIPT)):
        raise ValueError(f"the game already has a script named {missions.LAYERS_SCRIPT}")
    data.new("assets.dsk", key, SCRIPT, script.encode(script.new(missions.LAYERS_SCRIPT,
                                                                missions.layers_script(data.map_layers))))
    for map_ in missions.FREE_ROAM_MAPS:
        for a, r in data.records(name_hash("list")):
            if r.key == name_hash(map_):
                text_list = missions.add_to_list(data.current(a, r).decode("latin-1"), "script", missions.LAYERS_SCRIPT)
                data.set(a, r, text_list.encode("latin-1"))
    hit = [(a, r) for a, r in data.records(SCRIPT) if r.key == name_hash("global")]
    if not hit:
        raise ValueError("the game data has no global script to hook map layers into")
    for a, r in hit:
        old = script.decode(data.current(a, r))
        data.set(a, r, script.encode(script.with_source(old, missions.patch_global_layers(old.source))))
    by_map = {}
    for l in data.map_layers:
        by_map.setdefault(l["map"], []).append(l["layer"])
    result.changes.append("layers loaded with their map in free roam: " +
                          "; ".join(f"{m.upper()}: {', '.join(ls)}" for m, ls in sorted(by_map.items())))


def _check_new_scripts(data, result):
    """The game only runs scripts that a load list names under [script]."""
    from . import encounters
    new = {k: None for k, kind, _ in data.added() if kind == SCRIPT}
    if not new:
        return
    listed = set()
    for a, r in data.records(name_hash("list")):
        for n in encounters.parse_list(data.current(a, r).decode("latin-1")).get("script", []):
            listed.add(name_hash(n))
    for recs in data.add.values():
        for key, kind, body in recs:
            if kind == name_hash("list"):
                for n in encounters.parse_list(body.decode("latin-1")).get("script", []):
                    listed.add(name_hash(n))
    for archive, recs in data.add.items():
        for key, kind, body in recs:
            if kind == SCRIPT and key not in listed:
                name = script.decode(body).name
                result.warnings.append(f"scripts/{name}.lua is new but no load list names it under [script], so "
                                       f"the game won't find it (missions/ lists mission scripts in their map)")


def _material_warnings(rules):
    out = []
    for fname, rule in rules:
        for prop, value in rule.get("set", {}).items():
            if prop.lower() == "obstaclematerialtype" and str(value).lower() not in MATERIALS:
                out.append(f"objects/{fname}: obstaclematerialtype {value!r} isn't a material the game "
                           f"knows ({', '.join(MATERIALS)}); it will act as none")
    return out


def _apply_objects(data, folder, result, skip=()):
    rules = list(_rules(folder, skip))
    if not rules:
        return
    result.warnings += _material_warnings(rules)
    hits = [0] * len(rules)
    for a, r in data.records(WORLD):
        doc = None
        for n, (fname, rule) in enumerate(rules):
            if rule.get("layer") and name_hash(rule["layer"]) != r.key:
                continue
            if doc is None:
                doc = world.to_json(data.current(a, r))
            want = (rule.get("name") or rule.get("template") or "").lower()
            chunks = doc["chunks"]
            twins = []
            k = 0
            while k < len(chunks):
                c = chunks[k]
                k += 1
                if c.get("tag") != "inst" or "properties" not in c:
                    continue
                if want and c["template"].lower() != want:
                    continue
                if rule.get("model") and c["model"].lower() != rule["model"].lower():
                    continue
                if rule.get("near"):
                    if "position" not in c or math.dist(c["position"], rule["near"]) > float(rule.get("radius", 0.5)):
                        continue
                hits[n] += 1
                if rule.get("remove"):
                    # Kept as an invisible location marker rather than deleted,
                    # for the same reason copies go last: saved games know
                    # objects by their place in the layer.
                    named = c.get("template")
                    _to_marker(c)
                    if named:
                        result.warnings.append(f"{fname}: removed the named object {named!r}; "
                                               "scripts that use it by name will fail")
                elif rule.get("copy"):
                    # Copies go at the end of the layer: saved games keep each
                    # object's state by its place in the layer, so inserting
                    # one mid-layer hands every later object the saved state
                    # (position too) of the object before it.
                    twin = copy.deepcopy(c)
                    twin["template"] = rule["as"]
                    _apply_rule(twin, rule)
                    twins.append(twin)
                elif any(k in rule for k in ("move", "position", "turn")):
                    # Moved: placed again at the end of the layer, its old place
                    # kept as a marker. A saved game restores the state (position
                    # too) of the object in each place, so a moved gate would load
                    # back where the save had it; the new place is new to saves.
                    twin = copy.deepcopy(c)
                    _apply_rule(twin, rule)
                    twins.append(twin)
                    _to_marker(c)
                else:
                    _apply_rule(c, rule)
            chunks += twins          # later rules can still move them
        if doc is not None:
            data.set(a, r, world.from_json(doc))
    for (fname, rule), n in zip(rules, hits):
        what = rule.get("name") or rule.get("template") or rule.get("model") or f"object near {rule['near']}"
        if not n:
            raise ValueError(f"{fname}: no object matches {what!r}" +
                             (f" in layer {rule['layer']!r}" if rule.get("layer") else ""))
        verb = f"copied as {rule['as']!r}" if rule.get("copy") else "removed" if rule.get("remove") else "updated"
        result.changes.append(f"objects {what}: {n} instances {verb}")


def _apply_textures(data, folder, result):
    from . import modpack
    for f in sorted((folder / "textures").glob("*.png")):
        low = f.name.lower()
        if low.endswith(".mask.png"):
            continue
        if low.endswith(".changes.png"):
            # Changed pixels only (mod pack): laid over the game's texture.
            name = f.name[:-len(".changes.png")]
            mask_path = f.with_name(name + ".mask.png")
            if not mask_path.exists():
                raise ValueError(f"textures/{f.name}: {mask_path.name} is missing")
            hit = [(a, r) for a, r in data.records(texture.TEXTURE) if r.key == name_hash(name)]
            if not hit:
                raise ValueError(f"textures/{f.name}: no game texture named {name!r} to change")
            w, h, pix = png.decode(f.read_bytes())
            mw, mh, mask = png.decode(mask_path.read_bytes())
            for a, r in hit:
                tex = texture.decode(data.archive(a).read(r))
                bw, bh, base = tex.rgba()
                if (bw, bh) != (w, h) or (mw, mh) != (w, h):
                    raise ValueError(f"textures/{f.name}: the game's {name} is {bw}x{bh}, the changes are {w}x{h}")
                rgba = modpack.apply_rgba_changes(base, pix, mask)
                data.set(a, r, texture.encode(texture.replace_image(tex, w, h, rgba)))
            result.changes.append(f"texture {name}: changed by {f.name}")
            continue
        name = f.stem
        w, h, rgba = png.decode(f.read_bytes())
        key = name_hash(name)
        hit = [(a, r) for a, r in data.records(texture.TEXTURE) if r.key == key]
        if hit:
            for a, r in hit:
                tex = texture.decode(data.archive(a).read(r))
                data.set(a, r, texture.encode(texture.replace_image(tex, w, h, rgba)))
            result.changes.append(f"texture {name}: replaced ({w}x{h})")
        else:
            data.new("streamed.dsk", key, texture.TEXTURE, texture.encode(texture.new(name, w, h, rgba)))
            result.changes.append(f"texture {name}: added ({w}x{h})")


def _apply_animations(data, folder, result):
    from . import animation, gltf_anim
    files = sorted(list((folder / "animations").glob("*.glb")) + list((folder / "animations").glob("*.gltf")))
    new_path = folder / "animations" / "new.json"
    new_clips = {}
    if new_path.exists():
        new_clips = {k.lower(): v for k, v in json.loads(new_path.read_text(encoding="utf-8")).items()}
    made = set()
    for f in files:
        clips = gltf_anim.clips(f)
        if not clips:
            result.warnings.append(f"animations/{f.name}: no animation clips")
        for name, tracks in clips.items():
            key = name_hash(name)
            hit = [(a, r) for a, r in data.records(animation.ANIMATION) if r.key == key]
            if name.lower() in new_clips:
                if hit:
                    raise ValueError(f"animations/new.json: {name!r} is already a game animation; "
                                     "leave it out of new.json to replace it")
                _add_animation(data, name, new_clips[name.lower()], tracks, result)
                made.add(name.lower())
                continue
            if not hit:
                result.warnings.append(f"animations/{f.name}: no game animation named {name!r}; "
                                       "add it to animations/new.json to make it a new one")
                continue
            for a, r in hit:
                base_rec = data.archive(a).read(r)
                base = animation.decode(base_rec)
                new = gltf_anim.to_animation(tracks, base)
                data.set(a, r, animation.encode(new, base_rec))
            touched = sum(1 for t in base.tracks if t.joint.lower() in tracks)
            result.changes.append(f"animation {name}: {new.frames} frames, {touched} of {len(base.tracks)} joints from the clip")


    for name in sorted(set(new_clips) - made):
        result.warnings.append(f"animations/new.json: no clip named {name!r} in the animation files")


def _add_animation(data, name, template, tracks, result):
    """Add a new animation built on a template clip's skeleton, and load it
    wherever the template is loaded."""
    from . import animation, gltf_anim
    tkey = name_hash(template)
    hit = [(a, r) for a, r in data.records(animation.ANIMATION) if r.key == tkey]
    if not hit:
        raise ValueError(f"animations/new.json: template {template!r} isn't a game animation")
    a, r = hit[0]
    base_rec = data.current(a, r)
    base = animation.decode(base_rec)
    new = gltf_anim.to_animation(tracks, base)
    new.name = name
    data.new(a, name_hash(name), animation.ANIMATION, animation.encode(new, base_rec))
    # Load lists: "load <clip>" lines; add the new clip beside the template.
    from . import aidata
    lists = 0
    for la, lr in data.records(aidata.LIST):
        text = data.current(la, lr).decode("latin-1")
        lines = text.split("\n")
        names = {ln.strip()[5:].strip().lower() for ln in lines if ln.strip().lower().startswith("load ")}
        if template.lower() not in names or name.lower() in names:
            continue
        out = []
        for ln in lines:
            out.append(ln)
            if ln.strip().lower() == f"load {template.lower()}":
                out.append(ln[:len(ln) - len(ln.lstrip())] + f"load {name}" + ("\r" if ln.endswith("\r") else ""))
        data.set(la, lr, "\n".join(out).encode("latin-1"))
        lists += 1
    touched = sum(1 for t in base.tracks if t.joint.lower() in tracks)
    result.changes.append(f"animation {name}: new, {new.frames} frames, {touched} of {len(base.tracks)} joints "
                          f"from the clip, built on {template}, added to {lists} load lists")


def _apply_animtables(data, folder, result):
    from . import animtable
    from . import modpack
    for f in sorted((folder / "animtables").glob("*.changes.json")):
        # Entries only (mod pack), applied to the table as earlier mods left it.
        changes = json.loads(f.read_text(encoding="utf-8"))
        name = changes.get("name") or f.name[:-len(".changes.json")]
        hit = [(a, r) for a, r in data.records(animtable.ANIMTABLE) if r.key == name_hash(name)]
        if not hit:
            raise ValueError(f"animtables/{f.name}: no animation table named {name!r}")
        for a, r in hit:
            doc = modpack.apply_animtable_changes(animtable.to_json(data.current(a, r)), changes)
            data.set(a, r, animtable.from_json(doc))
        result.changes.append(f"animtable {name}: {len(changes.get('entries', []))} entries changed by {f.name}")
    for f in sorted(p for p in (folder / "animtables").glob("*.json") if not p.name.endswith(".changes.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        name = doc.get("name") or f.stem
        key = name_hash(name)
        hit = [(a, r) for a, r in data.records(animtable.ANIMTABLE) if r.key == key]
        if not hit:
            raise ValueError(f"animtables/{f.name}: no animation table named {name!r}")
        new = animtable.from_json(doc)
        norm = animtable.to_json(new)
        for a, r in hit:
            old = animtable.to_json(data.peek(a, r))
            data.set(a, r, new)
        old_map = {(st["name"], e["handle"]): e for st in old["sets"] for e in st["entries"]}
        changed = [f"{st['name']}/{e['handle']}" for st in norm["sets"] for e in st["entries"]
                   if old_map.get((st["name"], e["handle"])) != e]
        known = {r.key for _, r in data.records(name_hash("animation"))} | {k for k, _, _ in data.added()}
        # Only clips this mod newly refers to (some retail entries name clips the game lacks).
        missing = sorted(c for c in animtable.clips(norm) - animtable.clips(old) if name_hash(c) not in known)
        result.changes.append(f"animtable {name}: {len(changed)} entries changed"
                              + (f" ({', '.join(changed[:6])}{' ...' if len(changed) > 6 else ''})" if changed else ""))
        if missing:
            result.warnings.append(f"animtables/{f.name}: clips not in the game or this mod: {', '.join(missing[:6])}")


def _apply_terrain(data, folder, result):
    from . import png, terrain, terrain_decals, terrain_light, terrain_paint
    tdir = folder / "terrain"
    work = {}       # terrain name -> {"heights": path, "table": path, "layers": {index: path}}
    for f in sorted(tdir.glob("*.png")):
        # <name>.layerNN.<texture>.changes.png: changed pixels only (mod pack).
        is_changes = f.name.lower().endswith(".changes.png")
        plain = f.name[:-len(".changes.png")] + ".png" if is_changes else f.name
        if plain.endswith(".heights.png") and not is_changes:
            work.setdefault(f.name[:-len(".heights.png")], {})["heights"] = f
        elif terrain_paint.parse_layer_file(plain):
            name, index = terrain_paint.parse_layer_file(plain)
            layers = work.setdefault(name, {}).setdefault("layers", {})
            if index in layers:
                raise ValueError(f"terrain/{f.name}: layer {index} has two images")
            layers[index] = f
        else:
            result.warnings.append(f"terrain/{f.name}: not a heights or layer image (name.heights.png, name.layerNN.png)")
    for f in sorted(tdir.glob("*.heights.changes.json")):
        work.setdefault(f.name[:-len(".heights.changes.json")], {})["heights"] = f
    for f in sorted(tdir.glob("*.layers.json")):
        work.setdefault(f.name[:-len(".layers.json")], {})["table"] = f
    for f in sorted(tdir.glob("*.decals.json")):
        work.setdefault(f.name[:-len(".decals.json")], {})["decals"] = f
    for f in sorted(tdir.glob("*.surfaces.json")):
        _apply_surfaces(data, f, f.name[:-len(".surfaces.json")], result)
    for name, job in work.items():
        key = name_hash(name)
        hit = [(a, r) for a, r in data.records(name_hash("Terrain")) if r.key == key]
        if not hit:
            raise ValueError(f"terrain/{name}.*: no terrain named {name!r}")
        notes = []
        for a, r in hit:
            notes = []
            record = data.current(a, r)
            chunks = terrain.tern_chunks(record)
            replace = {}
            if "heights" in job:
                f = job["heights"]
                _, base, _ = terrain.decode_height_units(terrain.tern_chunks(data.archive(a).read(r)))
                if f.name.endswith(".changes.json"):
                    from . import modpack
                    meta = json.loads(f.read_text(encoding="utf-8"))
                    units = modpack.apply_heights_changes(base, meta)
                else:
                    meta_path = f.with_name(name + ".heights.json")
                    if not meta_path.exists():
                        raise ValueError(f"terrain/{f.name}: {meta_path.name} is missing (export both with the terrain command)")
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    units = terrain.units_from_png(f.read_bytes(), meta)
                # Apply only the samples this mod changed from the game's
                # heights, so mods built together keep each other's edits.
                _, cur, _ = terrain.decode_height_units(chunks)
                n = len(units)
                changed = overlap = 0
                for z in range(n):
                    for x in range(n):
                        if units[z][x] != base[z][x]:
                            changed += 1
                            overlap += cur[z][x] != base[z][x] and cur[z][x] != units[z][x]
                        else:
                            units[z][x] = cur[z][x]
                if overlap:
                    data.conflicts.append(f"{data.project} reshapes {overlap} height samples of terrain {name} "
                                          f"that an earlier mod also reshaped; {data.project}'s heights win there")
                replace = terrain.encode_height_units(chunks, units)
                note = "lighting kept (relight: false)"
                if meta.get("relight", True):
                    colr, note = terrain_light.relight_chunks(chunks, replace)
                    if colr is not None:
                        replace[b"COLR"] = colr
                notes.append(f"{changed} height samples changed; {note}")
            if "table" in job or "layers" in job:
                table = None
                if "table" in job:
                    table = json.loads(job["table"].read_text(encoding="utf-8"))["layers"]
                    known = {rec.key for _, rec in data.records(name_hash("Texture"))}
                    for t in table:
                        if name_hash(t["texture"]) not in known:
                            result.warnings.append(f"terrain/{job['table'].name}: no game texture named {t['texture']!r}")
                images = {}
                if job.get("layers"):
                    # Only pixels this mod changed from the game's paint
                    # replace the current paint (other mods' included).
                    _, base_imgs = terrain_paint.layer_images(terrain.tern_chunks(data.archive(a).read(r)))
                    _, cur_imgs = terrain_paint.layer_images({**chunks, **replace})
                for index, f in job.get("layers", {}).items():
                    w, h, rgba = png.decode(f.read_bytes())
                    if w != h:
                        raise ValueError(f"terrain/{f.name}: layer images are square")
                    base_img, cur_img = base_imgs.get(index), cur_imgs.get(index)
                    if f.name.lower().endswith(".changes.png"):
                        from . import modpack
                        if base_img is None or len(base_img) != w * h:
                            raise ValueError(f"terrain/{f.name}: the map has no layer {index} of this size to change")
                        img = modpack.apply_grey_changes(base_img, rgba)
                    else:
                        img = bytes(rgba[0::4])          # grey (or the red channel)
                    if base_img is not None and cur_img is not None and len(base_img) == len(img) == len(cur_img):
                        img = bytes(m if m != b else c for m, b, c in zip(img, base_img, cur_img))
                    images[index] = img
                paint, patches = terrain_paint.apply({**chunks, **replace}, images, table)
                replace.update(paint)
                if table is not None:
                    notes.append(f"{len(table)} layers in the table")
                if images:
                    notes.append(f"{patches} patches repainted")
            if "decals" in job:
                doc = json.loads(job["decals"].read_text(encoding="utf-8"))
                decal_replace, count = terrain_decals.apply({**chunks, **replace}, doc)
                replace.update(decal_replace)
                known = {rec.key for _, rec in data.records(name_hash("Texture"))}
                for t in terrain_decals.export({**chunks, **replace})["textures"]:
                    if t and name_hash(t) not in known:
                        result.warnings.append(f"terrain/{job['decals'].name}: no game texture named {t!r}")
                roads = len(doc.get("roads", []))
                notes.append(f"{count} decals" + (f" ({roads} road{'s' if roads != 1 else ''} laid)" if roads else ""))
            data.set(a, r, terrain.rebuild(record, replace))
        result.changes.append(f"terrain {name}: " + "; ".join(notes))


def _apply_surfaces(data, f, name, result):
    """terrain/<name>.surfaces.json -> the level config (a loose DATAxbox file)."""
    from . import terrain_surfaces
    levels = data.dir / "levels.ini"
    config = terrain_surfaces.level_config(levels.read_text(encoding="latin-1"), name) if levels.exists() else None
    if not config or not (data.dir / config).exists():
        raise ValueError(f"terrain/{f.name}: no level uses terrain {name!r}")
    path = "DATAxbox/" + config.replace("\\", "/")
    ini = data.files[path].decode("latin-1") if path in data.files else \
        (data.dir / config).read_bytes().decode("latin-1")
    text, changes, warnings = terrain_surfaces.apply(ini, json.loads(f.read_text(encoding="utf-8")))
    result.warnings += [f"terrain/{f.name}: {w}" for w in warnings]
    if changes:
        data.files[path] = text.encode("latin-1")
        result.changes.append(f"surfaces {name} ({config}): " + ", ".join(changes))


LAYOUT_KINDS = ("path", "lineregion", "region", "encounter", "list")


def _apply_layout(data, folder, result):
    from . import aidata, modpack
    ldir = folder / "layout"
    # Changes files (mod pack): items or lines applied to the layer as
    # earlier mods left it.
    for f in sorted(ldir.glob("*.changes.json")):
        layer, _, kind = f.name[:-len(".changes.json")].rpartition(".")
        if kind not in LAYOUT_KINDS or not layer or kind == "list":
            raise ValueError(f"layout/{f.name}: name it <layer>.<kind>.changes.json")
        hit = [(a, r) for a, r in data.records(name_hash(kind)) if r.key == name_hash(layer)]
        if not hit:
            raise ValueError(f"layout/{f.name}: no {kind} record for layer {layer!r}")
        changes = json.loads(f.read_text(encoding="utf-8"))
        for a, r in hit:
            doc = modpack.apply_layout_changes(aidata.to_json(data.current(a, r), kind), changes)
            data.set(a, r, aidata.from_json(doc))
        result.changes.append(f"layout {layer} {kind}: {len(changes.get('changed', []))} items changed or added, "
                              f"{len(changes.get('removed', []))} removed")
    for f in sorted(ldir.glob("*.list.txt.diff")):
        layer = f.name[:-len(".list.txt.diff")]
        hit = [(a, r) for a, r in data.records(name_hash("list")) if r.key == name_hash(layer)]
        if not hit:
            raise ValueError(f"layout/{f.name}: no load list for layer {layer!r}")
        for a, r in hit:
            try:
                text = modpack.apply_diff(data.current(a, r).decode("latin-1"), f.read_bytes().decode("latin-1"))
            except ValueError as err:
                raise ValueError(f"layout/{f.name}: {err}") from err
            data.set(a, r, text.encode("latin-1"))
        result.changes.append(f"layout {layer} list: changed by {f.name}")
    files = sorted([p for p in ldir.glob("*.json") if not p.name.endswith(".changes.json")]
                   + list(ldir.glob("*.list.txt")))
    for f in files:
        stem = f.name[:-len(".list.txt")] if f.name.endswith(".list.txt") else f.name[:-len(".json")]
        layer, _, kind = stem.rpartition(".") if not f.name.endswith(".list.txt") else (stem, ".", "list")
        if kind not in LAYOUT_KINDS or not layer:
            raise ValueError(f"layout/{f.name}: name it <layer>.<kind>.json with kind one of "
                             f"path, lineregion, region, encounter (or <layer>.list.txt)")
        key, kind_hash = name_hash(layer), name_hash(kind)
        hit = [(a, r) for a, r in data.records(kind_hash) if r.key == key]
        if not hit:
            raise ValueError(f"layout/{f.name}: no {kind} record for layer {layer!r}")
        if kind == "list":
            new = f.read_bytes()
        else:
            doc = json.loads(f.read_text(encoding="utf-8"))
            if doc.get("kind") != kind:
                raise ValueError(f"layout/{f.name}: file holds {doc.get('kind')!r} data, not {kind!r}")
            new = aidata.from_json(doc)
        for a, r in hit:
            data.set(a, r, new)
        result.changes.append(f"layout {layer} {kind}: replaced")


def _obj_settings(text):
    first = next((l for l in text.splitlines() if l.startswith("#")), "")
    m = re.search(r"level of detail (\d+)", first)
    return (int(m.group(1)) if m else 0), ("damaged" in first)


def _apply_models(data, folder, result):
    files = sorted(f for f in (folder / "models").glob("*") if f.suffix.lower() in (".obj", ".glb", ".gltf"))
    for f in files:
        name = f.stem
        is_changes = name.lower().endswith(".changes")     # mod pack output
        if is_changes:
            name = name[:-len(".changes")]
        is_gltf = f.suffix.lower() != ".obj"
        text = None if is_gltf else f.read_text(encoding="utf-8", errors="replace")
        lod, damaged = (None, None) if is_gltf else _obj_settings(text)
        key = name_hash(name)
        found = {}
        for a in ARCHIVES:
            if not (data.dir / a).exists():
                continue
            recs = {r.kind: r for r in data.archive(a).records if r.key == key and r.kind in (model.MODEL, model.SEGMENTS)}
            if len(recs) == 2:
                found[a] = recs
        if not found:
            raise ValueError(f"models/{f.name}: no model named {name!r}")
        report = None
        for a, recs in found.items():
            mr = data.archive(a).read(recs[model.MODEL])
            sr = data.archive(a).read(recs[model.SEGMENTS])
            if is_changes and is_gltf:
                from . import model_changes
                nm, ns, report = model_changes.import_changes(mr, sr, f)
            elif is_gltf:
                from . import gltf_model
                nm, ns, report = gltf_model.import_gltf(mr, sr, f, lod, damaged)
            else:
                nm, ns, report = model_import.import_obj(mr, sr, text, lod, damaged)
            data.set(a, recs[model.MODEL], nm)
            data.set(a, recs[model.SEGMENTS], ns)
        result.changes.append(f"model {name}: {len(report.edited)} pieces edited, {len(report.added)} added, "
                              f"{len(report.removed)} removed, {report.collision_boxes} collision boxes added "
                              f"(in {', '.join(found)})")
        result.warnings += [f"model {name}: {w}" for w in report.warnings]


def _apply_collision_flags(data, folder, result):
    """models/<name>.collision.json: {"shapes": {"0": 7, "1": 9}} sets collision
    shape flags (see docs/formats/dsk.md); runs after models/ edits."""
    from . import collision
    for f in sorted((folder / "models").glob("*.collision.json")) if (folder / "models").is_dir() else []:
        name = f.name[:-len(".collision.json")]
        doc = json.loads(f.read_text(encoding="utf-8"))
        flags = {int(k): int(v) for k, v in (doc.get("shapes") or {}).items()}
        hits = [(a, r) for a, r in data.records(model.SEGMENTS) if r.key == name_hash(name)]
        if not hits:
            raise ValueError(f"models/{f.name}: no model named {name!r}")
        for a, r in hits:
            data.set(a, r, collision.set_segments_flags(data.current(a, r), flags))
        result.changes.append(f"model {name}: collision flags " +
                              ", ".join(f"shape {k} = {v}" for k, v in sorted(flags.items())))


def _version(text):
    return tuple(int(p) for p in re.findall(r"\d+", str(text))[:3])


def check_requires(folder):
    """mod.json "requires": the oldest toolkit version that builds the mod."""
    from . import __version__
    meta = Path(folder) / "mod.json"
    if not meta.exists():
        return
    need = json.loads(meta.read_text(encoding="utf-8")).get("requires")
    if need and _version(need) > _version(__version__):
        raise ValueError(f"{project_name(folder)} needs Mercenaries Foundry {need} or newer; "
                         f"this toolkit is {__version__} (update foundry\\mercfoundry.pyz)")


def project_name(folder) -> str:
    folder = Path(folder)
    meta = folder / "mod.json"
    if meta.exists():
        name = json.loads(meta.read_text(encoding="utf-8")).get("name")
        if name:
            return str(name)
    return folder.resolve().name


def _apply_sounds(data, folder, result):
    from . import sounds
    sounds.apply(data, folder, result)


def _apply_game_squads(data, folder, result):
    """encounters/*.json "game_squads": squad records added to the game's own
    encounters (after layout changes, which replace whole encounters); their
    members were built into the mod's layer with it."""
    from . import aidata, encounters
    by_layer = {}
    for f in sorted((folder / "encounters").glob("*.json")) if (folder / "encounters").is_dir() else []:
        for sq in json.loads(f.read_text(encoding="utf-8")).get("game_squads", []):
            by_layer.setdefault(sq["layer"], []).append(sq)
    for layer, squads in by_layer.items():
        hit = [(a, r) for a, r in data.records(name_hash("encounter")) if r.key == name_hash(layer)]
        if not hit:
            raise ValueError(f"game_squads: no encounter record for layer {layer!r}")
        for a, r in hit:
            doc = aidata.to_json(data.current(a, r), "encounter")
            encounters.add_game_squads(doc, squads)
            data.set(a, r, aidata.from_json(doc))
        for sq in squads:
            result.changes.append(f"encounter {sq['encounter']} ({layer}): squad {sq['name']} added, "
                                  f"{len(sq.get('members', []))} members")


STEPS = (_apply_text, _apply_scripts, _apply_objects, _apply_encounters, _apply_missions,
         _apply_textures, _apply_models, _apply_collision_flags, _apply_layout, _apply_game_squads,
         _apply_animations, _apply_animtables, _apply_terrain, _apply_sounds)


def build(projects, data_dir, out_dir, name=None, description=None) -> Result:
    """Build one mod folder, or several in load order (later ones win where
    they change the same thing; such conflicts are reported as warnings),
    into one install-ready folder."""
    projects = [Path(projects)] if isinstance(projects, (str, Path)) else [Path(p) for p in projects]
    out = Path(out_dir)
    for project in projects:
        if not project.is_dir():
            raise FileNotFoundError(f"{project} is not a folder")
        check_requires(project)
    mod_names = [project_name(p) for p in projects]
    if len(set(mod_names)) != len(mod_names):
        raise ValueError("two mods have the same name: " + ", ".join(mod_names))
    data = _Data(data_dir)
    result = Result()
    contracts = []
    for step in STEPS:
        if step is _apply_missions:
            # Across mods, in the order their arcs need (not load order).
            jobs = [(project, pname, [f]) for project, pname, f in _mission_order(zip(projects, mod_names))]
        else:
            jobs = [(project, pname, None) for project, pname in zip(projects, mod_names)]
        for project, pname, files in jobs:
            data.project = pname
            part = Result()
            try:
                found = step(data, project, part, files) if files else step(data, project, part)
            except ValueError as err:
                raise ValueError(f"{pname}: {err}" if len(projects) > 1 else str(err)) from err
            if step is _apply_missions and found:
                contracts += found
            prefix = f"{pname}: " if len(projects) > 1 else ""
            result.changes += [prefix + c for c in part.changes]
            result.warnings += [prefix + w for w in part.warnings]
        if step is _apply_missions:
            data.project = "Foundry"
            if contracts:
                _apply_contracts(data, contracts, result)
            if getattr(data, "map_layers", None):
                _apply_map_layers(data, result)
            _check_new_scripts(data, result)
    from . import sounds
    data.project = "Foundry"
    sounds.finish(data, result)
    result.warnings += [f"conflict: {c}" for c in data.conflicts]
    names = sorted(set(data.replace) | set(data.add))
    names = [n for n in names if data.replace.get(n) or data.add.get(n)]
    out.mkdir(parents=True, exist_ok=True)
    # Fail before writing anything if the game has the files open.
    for n in names + sorted(data.files):
        target = out / n
        if target.exists():
            try:
                with target.open("r+b"):
                    pass
            except PermissionError as err:
                raise PermissionError(f"can't replace {target}: it's in use (close the game first)") from err
    for n in names:
        dsk.write_modified(data.archive(n), out / n, data.replace.get(n, {}), data.add.get(n, ()))
        result.written.append(n)
        result.records[n] = set(data.replace.get(n, {})) | {(k, kind) for k, kind, _ in data.add.get(n, ())}
    # Archives written by an earlier build but no longer changed would
    # silently override the game; remove them.
    for n in ARCHIVES:
        if n not in names and (out / n).exists() and (out / "foundry-mod.json").exists():
            (out / n).unlink()
    # Loose game files (level configs); remove ones an earlier build wrote
    # that this one doesn't.
    previous = []
    if (out / "foundry-mod.json").exists():
        previous = json.loads((out / "foundry-mod.json").read_text(encoding="utf-8")).get("files", [])
    for rel in previous:
        if rel not in data.files and (out / rel).is_file():
            (out / rel).unlink()
    for rel, body in sorted(data.files.items()):
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_bytes(body)
        result.files.append(rel)
    meta = {}
    if len(projects) == 1 and (projects[0] / "mod.json").exists():
        meta = json.loads((projects[0] / "mod.json").read_text(encoding="utf-8"))
    (out / "foundry-mod.json").write_text(json.dumps({
        "name": name or meta.get("name", mod_names[0] if len(mod_names) == 1 else "Foundry Mods"),
        "description": description if description is not None else meta.get("description", ""),
        "built_by": "mercfoundry",
        "sources": mod_names,
        "archives": result.written,
        "files": sorted(data.files),
        "changes": result.changes,
    }, indent=1) + "\n", encoding="utf-8")
    return result
