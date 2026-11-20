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
import struct
from dataclasses import dataclass, field
from pathlib import Path

from . import dsk, model, model_import, png, registry, script, texture, ucfb, world
from .hashing import name_hash

SCRIPT, WORLD, REGISTRY = name_hash("script"), name_hash("world"), name_hash("registry")
ARCHIVES = ("assets.dsk", "english.dsk", "locked.dsk", "streamed.dsk")
# Most named objects one encounter layer may add: the game's named-object
# table holds 2048 in all, and a full one hangs it (HashTable_Find).
MAX_LAYER_NAMES = 500

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

    def built_model(self, name):
        """(model record, segments record) of a model as this build has it so
        far: added by a mod, else replaced, else the game's. KeyError if missing."""
        key = name_hash(name)
        found = {}
        for kind in (model.MODEL, model.SEGMENTS):
            body = self.find_added(key, kind)
            if body is None:
                hit = next(((a, r) for a, r in self.records(kind) if r.key == key), None)
                if hit:
                    body = self.peek(*hit)
            if body is not None:
                found[kind] = body
        if len(found) < 2:
            raise KeyError(f"model not found: {name}")
        return found[model.MODEL], found[model.SEGMENTS]

    def added(self):
        """(key, kind, archive name) of every record added so far."""
        return [(k, kind, a) for a, recs in self.add.items() for k, kind, _ in recs]


def new_project(folder, name=None):
    folder = Path(folder)
    name = name or folder.resolve().name
    for sub in ("text", "scripts", "objects", "encounters", "missions", "textures", "models", "layout", "animations",
                "animtables", "terrain", "sounds", "sky", "maps"):
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
        if "map" in doc:
            # {"map": <a mod's new map>, "strings": {key: text}}: text there only
            strings = doc.get("strings")
            if not isinstance(strings, dict):
                raise ValueError(f"text/{f.name}: with \"map\", the texts go in \"strings\": {{key: text}}")
            n = _map_strings(data, str(doc["map"]), {str(k): str(v) for k, v in strings.items()},
                             f"text/{f.name}")
            result.changes.append(f"text/{f.name}: {n} strings of {doc['map']}'s own (the game's stay elsewhere)")
            continue
        edits.update({str(k): str(v) for k, v in doc.items()})
    if edits:
        changed, added = _set_strings(data, edits)
        result.changes.append(f"text: {changed} strings changed, {added} entries added")


MAP_TEXT_PREFIX = "foundry_map_"


def _map_strings(data, map_name, edits, where):
    """Texts a mod's new map shows instead of the game's (text/*.json or
    factions.json with "map"): each game key's text goes in beside it under
    <key>.foundry_map_<map> (the same string table), and the map's master
    script asks Foundry Console to show it for the key while the map is
    loaded (Foundry_MapText; put back on the next level load). A key the
    game hasn't is simply added. Returns the count."""
    m = map_name.lower()
    if m not in getattr(data, "new_maps", set()):
        raise ValueError(f"{where}: \"map\": {map_name!r} isn't a mod's new map (maps/<name>.json); "
                         f"text only for a game map isn't possible yet")
    have = game_strings(data)
    lower = {k.lower(): k for k in have}
    plain, alt = {}, {}
    pairs = data.map_text = getattr(data, "map_text", {})
    for key, text in edits.items():
        game_key = lower.get(key.lower())
        if game_key is None:
            plain[key] = text
            continue
        other = f"{game_key}.{MAP_TEXT_PREFIX}{m}"
        alt[other] = text
        pairs.setdefault(m, {})[game_key.lower()] = (game_key, other)
    if plain or alt:
        _set_strings(data, {**plain, **alt})
    return len(plain) + len(alt)


def _apply_deck(data, folder, result):
    """deck/<suit>.json: a mod's own Deck of 52 on its new map (deck.py): the
    cards' texts and portraits shown there instead of the game's (map text,
    HUD art), and foundry_deck, which swaps the PDA's card states while free
    roam is there and arms its number cards."""
    from . import deck as deckmod
    files = sorted((folder / "deck").glob("*.json"))
    if not files:
        return
    mod_maps = {f.stem.lower() for f in (folder / "maps").glob("*.json")} if (folder / "maps").is_dir() else set()
    data.decks = getattr(data, "decks", [])
    mine = []
    for f in files:                     # several suits on a map: one file each
        where = f"deck/{f.name}"
        d = deckmod.parse(where, json.loads(f.read_text(encoding="utf-8")), mod_maps, folder)
        if any(o["map"] == d["map"] and o["suit"] == d["suit"] for o in data.decks + [m for _, m in mine]):
            raise ValueError(f"{where}: {d['map']} already has a {d['suit']} deck")
        mine.append((where, d))
    for where, d in mine:
        _map_strings(data, d["map"], deckmod.strings(d), where)
        faces = 0
        for c in d["cards"]:
            if not c["face"]:
                continue
            path = folder / c["face"]
            if not path.is_file():
                raise ValueError(f"{where}: no {c['face']} (the {deckmod.RANK_NAMES[c['rank']].lower()}'s portrait)")
            w, h, rgba = png.decode(path.read_bytes())
            if (w, h) != (128, 128):
                result.warnings.append(f"{where}: {c['face']} is {w}x{h}; the game's card portraits are 128x128 "
                                       "(the PDA draws them at that shape)")
            tname = deckmod.face_texture(d["map"], d["suit"], c["rank"])
            built = texture.encode(texture.new(tname, w, h, rgba))
            data.new("streamed.dsk", name_hash(tname), texture.TEXTURE, built)
            data.map_hud = getattr(data, "map_hud", {})
            data.map_hud.setdefault(d["map"], {})[f"{d['suit']}{c['rank']}_face"] = tname
            faces += 1
        data.decks.append(d)
        number = [c for c in d["cards"] if c["rank"] <= 10]
        placed = [c for c in number if c["actor"]]
        result.changes.append(
            f"{where}: {d['suit']} on {d['map']}: {len(d['cards'])} cards' texts, {faces} portraits, "
            f"{len(placed)} of {len(number)} number cards placed (verify objectives in free roam there)")
        for c in number:
            if not c["actor"]:
                result.warnings.append(f"{where}: the {deckmod.RANK_NAMES[c['rank']].lower()} has no \"actor\" "
                                       "(its placed character): it can't be found or verified yet")
    # "others" (one deck file per map gives it): the suits no deck on the map uses
    for where, d in mine:
        o = d.get("others")
        if not o:
            continue
        used = {x["suit"] for x in data.decks if x["map"] == d["map"]}
        _map_strings(data, d["map"], deckmod.others_strings(o, used), where)
        if o["face"]:
            path = folder / o["face"]
            if not path.is_file():
                raise ValueError(f"{where}: no {o['face']} (\"others\" \"face\")")
            w, h, rgba = png.decode(path.read_bytes())
            tname = deckmod.other_face_texture(d["map"])
            if data.find_added(name_hash(tname), texture.TEXTURE) is None:
                data.new("streamed.dsk", name_hash(tname), texture.TEXTURE,
                         texture.encode(texture.new(tname, w, h, rgba)))
            data.map_hud = getattr(data, "map_hud", {})
            for suit in deckmod.SUITS:
                if suit not in used:
                    for rank in range(2, 15):
                        data.map_hud.setdefault(d["map"], {})[f"{suit}{rank}_face"] = tname
        result.changes.append(f"{where}: \"others\" for {', '.join(x for x in deckmod.SUITS if x not in used)} "
                              f"on {d['map']}")
    key = name_hash(deckmod.SCRIPT)
    src = deckmod.script(data.decks)
    if data.find_added(key, SCRIPT) is not None:
        data.replace_added(key, SCRIPT, script.encode(script.new(deckmod.SCRIPT, src)))
    else:
        data.new("assets.dsk", key, SCRIPT, script.encode(script.new(deckmod.SCRIPT, src)))


def _write_map_text(data, result):
    """After every step: each new map with texts of its own (_map_strings)
    gets a script, foundry_maptext_<map>, listing them, that its master
    script runs first thing (with Foundry Console; without it the game's
    texts show)."""
    from . import missions
    have = {r.key for _, r in data.records(texture.TEXTURE)} | \
        {k for k, kind, _ in data.added() if kind == texture.TEXTURE}
    for m, pairs in sorted(getattr(data, "map_text", {}).items()):
        name = f"foundry_maptext_{m}"
        hud = getattr(data, "map_hud", {}).get(m, {})
        for old, new in hud.items():
            for t in (old, new):
                if name_hash(t) not in have:
                    raise ValueError(f"maps/{m}.json: \"hud_textures\": no texture named {t!r}"
                                     + (f" (add textures/{t}.png)" if t == new else ""))
        rows = "".join(f"{missions._lua(k)}, {missions._lua(o)},\n" for k, o in sorted(pairs.values()))
        hud_rows = "".join(f"{missions._lua(k)}, {missions._lua(o)},\n" for k, o in sorted(hud.items()))
        src = (f"-- Generated by Mercenaries Foundry: {m}'s own texts (Foundry Console puts the game's back\n"
               f"-- when another level loads)\n"
               f"tFoundryMapText = {{\n{rows}}}\n"
               f"function foundry_MapText()\n"
               f"if not Foundry_MapText then return 0 end\n"
               f"local n = 0\n"
               f"for i = 1, table.getn(tFoundryMapText), 2 do\n"
               f"if Foundry_MapText(tFoundryMapText[i], tFoundryMapText[i + 1]) then n = n + 1 end\n"
               f"end\n"
               f"return n\n"
               f"end\n"
               f"-- the HUD and PDA draw these textures as the second ones (their sprites' texture\n"
               f"-- hashes, put back on the next level load)\n"
               f"tFoundryMapHud = {{\n{hud_rows}}}\n"
               f"function foundry_MapHud()\n"
               f"if not Foundry_MapTexture then return 0 end\n"
               f"local n = 0\n"
               f"for i = 1, table.getn(tFoundryMapHud), 2 do\n"
               f"if Foundry_MapTexture(tFoundryMapHud[i], tFoundryMapHud[i + 1]) then n = n + 1 end\n"
               f"end\n"
               f"return n\n"
               f"end\n")
        data.new("assets.dsk", name_hash(name), SCRIPT, script.encode(script.new(name, src)))
        from .missions import add_to_list
        LIST = name_hash("list")
        body = data.find_added(name_hash(m), LIST)
        if body is None:
            raise ValueError(f"maps/{m}.json: no load list for the map's texts")
        ltext = add_to_list(body.decode("latin-1"), "script", name)
        for new in hud.values():                  # loaded with the map, or nothing draws
            ltext = add_to_list(ltext, "texture", new)
        data.replace_added(name_hash(m), LIST, ltext.encode("latin-1"))
        master = data.find_added(name_hash(m), SCRIPT)
        if master is None:
            raise ValueError(f"maps/{m}.json: no master script for the map's texts")
        old = script.decode(master)
        text = old.source
        at = text.find("function ScriptInit()")
        if at < 0:
            raise ValueError(f"maps/{m}.json: its master script has no ScriptInit for the map's texts")
        at = text.index("\n", at) + 1
        nl = "\r\n" if "\r\n" in text else "\n"
        call = (f"Utility_LoadUtilityScript('{name}'){nl}"
                f"foundry_MapText(){nl}"
                f"foundry_MapHud(){nl}")
        data.replace_added(name_hash(m), SCRIPT, script.encode(script.with_source(old, text[:at] + call + text[at:])))
        result.changes.append(f"maps/{m}.json: {len(pairs)} texts" + (f" and {len(hud)} HUD textures" if hud else "")
                              + f" of its own, shown there with Foundry Console ({name})")


def _write_map_templates(data, result):
    """After every step: a new map with "template_overrides" has its master
    script's ScriptInit tell Foundry Console which templates spawn as which
    while it's loaded (Foundry_MapTemplate; forgotten on the next level load;
    without Foundry Console the game's spawn)."""
    from . import missions
    for m, swaps in sorted(getattr(data, "map_template_swaps", {}).items()):
        master = data.find_added(name_hash(m), SCRIPT)
        if master is None:
            raise ValueError(f"maps/{m}.json: no master script for its \"template_overrides\"")
        old = script.decode(master)
        text = old.source
        at = text.find("function ScriptInit()")
        if at < 0:
            raise ValueError(f"maps/{m}.json: its master script has no ScriptInit for \"template_overrides\"")
        at = text.index("\n", at) + 1
        nl = "\r\n" if "\r\n" in text else "\n"
        call = (f"if Foundry_MapTemplate then{nl}"
                + "".join(f"Foundry_MapTemplate({missions._lua(t)}, {missions._lua(to)}){nl}" for t, to in swaps)
                + f"end{nl}")
        data.replace_added(name_hash(m), SCRIPT, script.encode(script.with_source(old, text[:at] + call + text[at:])))
        result.changes.append(f"maps/{m}.json: {len(swaps)} game templates spawn as the map's own there "
                              f"({', '.join(f'{t} as {to}' for t, to in swaps)}), with Foundry Console")


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
            if "map" in rule and not (isinstance(rule["map"], str) and rule["map"].strip()):
                raise ValueError(f"{f.name}: \"map\" names a map (\"sw\", or a mod's new map)")
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


def check_spawn_points(medevac, hq_respawn, where):
    """maps json "medevac" and "hq_respawn" checked (the editor checks with
    this too); returns hq_respawn ({} if none)."""
    from . import missions

    def checked(spot, what):
        if not isinstance(spot, dict) or not isinstance(spot.get("position"), (list, tuple)) \
                or len(spot["position"]) != 3:
            raise ValueError(f"{where}: {what} is {{\"position\": [x, y, z], \"heading\"?: degrees}}")
        return spot
    if medevac:
        checked(medevac, "\"medevac\"")
    hq_respawn = hq_respawn or {}
    if not isinstance(hq_respawn, dict):
        raise ValueError(f"{where}: \"hq_respawn\" is {{faction: {{\"position\", \"heading\"?}}}}")
    for f, spot in hq_respawn.items():
        if f not in missions.HQ_FACTIONS:
            raise ValueError(f"{where}: \"hq_respawn\": a faction is one of {', '.join(missions.HQ_FACTIONS)}")
        checked(spot, f"\"hq_respawn\" {f}")
    return hq_respawn


def _move_player_start(body, medevac, where, hq_respawn=None):
    """maps json "medevac": {"position", "heading"?}: where the player is put
    back on the map. The game respawns the player (a medevac from the pause
    menu, a contract given up after dying, free roam starting) at the
    location scribble memory's PlayerStartLocationName names, which retail's
    scripts set to 'loc_playerstart' (on SW the Allied MASH). A new map's
    copy of its source's world has the source's loc_playerstart (blank's at
    about -73, 20, -70); this moves it. heading: placed objects' yaw (0
    along +z).
    hq_respawn ({faction: {"position", "heading"?}}): locations
    loc_hq-<faction>-respawn, where a contract accepted from that faction
    respawns the player after a death (retail's scripts set
    PlayerStartLocationName to it while a contract runs). Copies of
    loc_playerstart, added after the layer's other objects (saves know
    objects by their place in the layer)."""
    hq_respawn = check_spawn_points(medevac, hq_respawn, where)
    doc = world.to_json(body)
    found = []

    def walk(n):
        if isinstance(n, dict):
            if n.get("tag") == "inst" and str(n.get("template") or "").lower() == "loc_playerstart":
                found.append(n)
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(doc)
    if not found:
        raise ValueError(f"{where}: \"medevac\"/\"hq_respawn\": the map's world has no loc_playerstart "
                         f"(its \"from\" map has none)")

    def place(inst, spot):
        a = math.radians(float(spot.get("heading") or 0))
        inst["position"] = [float(v) for v in spot["position"]]
        inst["rotation"] = [[math.cos(a), 0.0, -math.sin(a)], [0.0, 1.0, 0.0], [math.sin(a), 0.0, math.cos(a)]]
    template = copy.deepcopy(found[0])
    if medevac:
        for inst in found:
            place(inst, medevac)
    names = {str(c.get("template") or "").lower() for c in doc["chunks"] if c.get("tag") == "inst"}
    last = max(i for i, c in enumerate(doc["chunks"]) if c.get("tag") == "inst")
    for f, spot in hq_respawn.items():
        name = f"loc_hq-{f}-respawn"
        if name in names:
            raise ValueError(f"{where}: \"hq_respawn\" {f}: the map already has {name}")
        inst = copy.deepcopy(template)
        inst["template"] = name
        place(inst, spot)
        last += 1
        doc["chunks"].insert(last, inst)
    return world.from_json(doc)


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
        if prop.lower() == "geometryfile" and value:
            inst["model"] = str(value)           # it draws the model it names (its model word follows)
    if any(k in rule for k in ("move", "position", "turn")) and "position" not in inst:
        raise ValueError(f"object {inst['template']!r} has no transform to change")
    if "position" in rule:
        inst["position"] = [float(v) for v in rule["position"]]
    if "move" in rule:
        inst["position"] = [p + float(d) for p, d in zip(inst["position"], rule["move"])]
    if "turn" in rule:
        inst["rotation"] = _turn(inst["rotation"], float(rule["turn"]))
    if "rotation" in rule:                 # a full rotation (tilted, as to the ground's slope)
        from .encounters import rotation_rows
        inst["rotation"] = rotation_rows(rule["rotation"])


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


def _load_textures_with_map(data, map_name, textures):
    """A map's load list loads these textures too (ground layers added to its
    terrain: a texture from another map's ground isn't loaded otherwise)."""
    from .missions import add_to_list
    LIST, key = name_hash("list"), name_hash(map_name)
    body = data.find_added(key, LIST)
    if body is not None:
        text = body.decode("latin-1")
        for t in textures:
            text = add_to_list(text, "texture", t)
        data.replace_added(key, LIST, text.encode("latin-1"))
        return
    for a, r in data.records(LIST):
        if r.key == key:
            text = data.current(a, r).decode("latin-1")
            new = text
            for t in textures:
                new = add_to_list(new, "texture", t)
            if new != text:
                data.set(a, r, new.encode("latin-1"))


def _trim_like_loads(data, more, own_layers):
    """What a new map copies from another map's list ("loads_like"), less
    what only that map's own layers draw: models that a game object names
    (its model or a property: ruin, wheels...) but no object outside those
    layers does now (after the mod's rules, so a vehicle a mod gives another
    model drops its old one), and textures that only those models draw (with
    each one's _nm), and textures only the shipped look of a model on the
    list drew (a mod replaced the model with one drawing others: its old
    textures and _nm). Models no object names (ones the code loads by name)
    and textures no model on the list draws (effects, interface, skies) stay.
    Returns (loads, "N models, M textures") or (loads, "") when nothing goes."""
    from . import find, model as model_mod
    own = {name_hash(n) for n in own_layers}
    archives = [data.archive(n) for n in ARCHIVES if (data.dir / n).exists()]

    def names(obj):
        yield str(obj.get("model") or "").lower()
        for _, v in obj.get("properties") or []:
            yield str(v).lower()
    ever = {n for obj in find.objects(archives) for n in names(obj)}        # the game's objects, as shipped
    # what can be on the new map, as built: the shared template layers'
    # objects (spawned anywhere: by scripts, spawners, support, traffic) and
    # the mods' own layers; the game's other layers (other maps', missions',
    # with their own templates) bring their own lists
    now = set()
    for a, r in data.records(WORLD):
        if r.key not in own:
            for obj in world.iter_objects(data.current(a, r)):
                if str(obj.get("layer") or "").lower().startswith("template"):
                    now.update(names(obj))
    for recs in data.add.values():
        for key, kind, body in recs:
            if kind == WORLD and key not in own:
                for obj in world.iter_objects(body):
                    now.update(names(obj))
    mkey = next((k for k in more if k.lower() == "model"), None)
    tkey = next((k for k in more if k.lower() == "texture"), None)
    if not mkey:
        return more, ""
    gone = [m for m in more[mkey] if m.lower() in ever and m.lower() not in now]

    def draws(found):
        out = set()
        try:
            for p in model_mod.decode(*found()).pieces:
                out.add(p.texture)
                out.update(w for w in p.material if w)
        except (KeyError, ValueError, IndexError):
            return None
        return out
    gone_set = {m.lower() for m in gone}
    # textures the list's models drew as shipped, and those the kept ones (as
    # the mod built them: a replaced model may draw others) and the mod's own
    # models draw now; a texture only the first drew is the old look's
    shipped_draw, kept_draw = set(), set()
    for m in more[mkey]:
        shipped = draws(lambda: model_mod.find(archives, m))
        built = draws(lambda: data.built_model(m))
        if shipped is None or built is None:
            gone_set.discard(m.lower())            # can't tell what it draws: keep it and what it may draw
            kept_draw.update(shipped or ())
            kept_draw.update(built or ())
            continue
        shipped_draw.update(shipped)
        if m.lower() not in gone_set:
            kept_draw.update(built)
    for k, kind, _ in data.added():
        if kind == model.MODEL:
            body, seg = data.find_added(k, kind), data.find_added(k, model.SEGMENTS)
            if seg is not None:
                kept_draw.update(draws(lambda: (body, seg)) or ())
    only_old = shipped_draw - kept_draw
    out = dict(more)
    out[mkey] = [m for m in more[mkey] if m.lower() not in gone_set]
    n_tex = 0
    if tkey:
        def only_theirs(t):
            h = name_hash(t)
            return h in only_old or (t.lower().endswith("_nm") and name_hash(t[:-3]) in only_old
                                     and h not in kept_draw)
        out[tkey] = [t for t in more[tkey] if not only_theirs(t)]
        n_tex = len(more[tkey]) - len(out[tkey])
    n_mod = len(more[mkey]) - len(out[mkey])
    return out, f"{n_mod} models, {n_tex} textures" if n_mod or n_tex else ""


# The template layers the level loader (Level_Load, 0x184890) reads for every
# level, and the one a new map's own script loads (map_script): their
# templates are there already (tried in game on the Gaza map, 2026-11-18: a
# template_vehicles one spawned, a template_vehicles_ch3 one wasn't loaded).
LEVEL_TEMPLATE_LAYERS = ("template_weapons", "template_humans", "template_vehicles", "template_ruin", "template_misc")
NEW_MAP_TEMPLATE_LAYERS = LEVEL_TEMPLATE_LAYERS + ("template_vehicles_ch2",)


def _map_templates(data, name, wanted, where, rules=()):
    """maps/<name>.json "templates": copies of game templates, under their own
    names, in a layer of their own that loads with the map (<name>_templates),
    for templates a new map doesn't have: those in the chapter layers
    (template_vehicles_ch1, _ch3, _ch4) and missions' layers (the shared
    ones, NEW_MAP_TEMPLATE_LAYERS, load on every map). The world layer loader
    makes a template of any loaded object named template_* (it registers its
    properties and doesn't place it), so the copies spawn like the game's.
    They are copied as the build has them (with a mod's rules applied), with
    only their own loads (models, ruins, wheels, the textures those draw), and
    with the weapon, ammunition and crew templates they name that aren't in
    those shared layers. A listed template that is in them isn't copied
    (it's reported). Returns (layer, template names, shared ones listed,
    records, unused rules); layer and records are None when there's nothing
    to copy.

    An entry {"template": <game template>, "as": <new name>, "set": {...}}
    is a copy under a name of its own (template_...), with those properties
    (GeometryFile: the model it draws, a copied model of the mod's own), even
    of a template every map has: this map's own version of it, for its
    encounters and missions, leaving the game's alone.
    rules: objects rules with "map": <this map> (file, rule), applied to the
    copies they match (by "template" or "model"): changes on this map only;
    the ones that matched no copy come back (for a warning)."""
    from . import encounters
    archives = [data.archive(n) for n in ARCHIVES if (data.dir / n).exists()]
    by_name, _ = _world_index(data, archives)
    always = {n.lower() for n in NEW_MAP_TEMPLATE_LAYERS}
    layer_docs, lists = {}, {}

    def source(t):
        """(layer, its inst) of template t as built: a shared template layer's first, else any layer's."""
        hits = [h for h in by_name.get(t.lower(), []) if t.lower().startswith("template_")]
        if not hits:
            raise ValueError(f"{where}: \"templates\": no game template named {t!r}")
        hits.sort(key=lambda h: not h["layer"].lower().startswith("template"))
        lay = hits[0]["layer"]
        if lay not in layer_docs:
            a, r = next((a, r) for a, r in data.records(WORLD) if r.key == name_hash(lay))
            layer_docs[lay] = world.to_json(data.current(a, r))
        inst = next(c for c in layer_docs[lay]["chunks"]
                    if c.get("tag") == "inst" and c.get("template", "").lower() == t.lower())
        return lay, inst

    def in_always(t):
        return any(h["layer"].lower() in always for h in by_name.get(t.lower(), []))

    specs = []
    for w in wanted:
        if isinstance(w, dict):
            t, as_, set_ = str(w.get("template") or ""), w.get("as"), w.get("set") or {}
            if not t:
                raise ValueError(f"{where}: \"templates\": an entry {{...}} names its game \"template\"")
            if as_ is not None:
                as_ = str(as_)
                if not re.fullmatch(r"template_[A-Za-z0-9_]+", as_):
                    raise ValueError(f"{where}: \"templates\": \"as\": {as_!r} isn't a template name "
                                     f"(template_ then letters, digits and _)")
                if by_name.get(as_.lower()):
                    raise ValueError(f"{where}: \"templates\": the game already has an object named {as_!r}")
            elif set_:
                raise ValueError(f"{where}: \"templates\": {t}: \"set\" needs \"as\", a name of its own "
                                 f"(the game's {t} stays as it is)")
            if not isinstance(set_, dict):
                raise ValueError(f"{where}: \"templates\": {t}: \"set\" is {{property: value}}")
            specs.append((t, as_, set_))
        else:
            specs.append((str(w), None, None))
    for t, _, _ in specs:
        source(t)                          # each must be a game template
    news = [a.lower() for _, a, _ in specs if a]
    if len(set(news)) != len(news):
        raise ValueError(f"{where}: \"templates\": two copies with the same \"as\"")
    shared = [t for t, a, _ in specs if not a and in_always(t)]
    todo = [(t, a, st) for t, a, st in specs if a or not in_always(t)]
    done, insts, loads = set(), [], {}
    used = [False] * len(rules)
    while todo:
        t, as_, set_ = todo.pop(0)
        if (as_ or t).lower() in done:
            continue
        done.add((as_ or t).lower())
        lay, inst = source(t)
        inst = copy.deepcopy(inst)
        if as_:
            inst["template"] = as_
        if set_:
            _apply_rule(inst, {"set": set_})
        for k, (fname, rule) in enumerate(rules):
            want = (rule.get("template") or rule.get("name") or "").lower()
            if want and want not in (t.lower(), (as_ or "").lower()):
                continue
            if rule.get("model") and str(inst.get("model") or "").lower() != rule["model"].lower():
                continue
            _apply_rule(inst, {"set": rule.get("set") or {}})
            used[k] = True
        insts.append(inst)
        if lay not in lists:
            hit = [(a, r) for a, r in data.records(name_hash("list")) if r.key == name_hash(lay)]
            body = data.current(*hit[0]) if hit else data.find_added(name_hash(lay), name_hash("list"))
            lists[lay] = encounters.parse_list(body.decode("latin-1")) if body else {}
        found = _object_loads({"model": inst.get("model"), "properties": inst.get("properties")},
                              lists[lay], archives)
        # and the models it draws by name (a mod's rule may give it ones its layer's list doesn't load)
        found.setdefault("model", []).extend(str(v) for k, v in inst.get("properties") or []
                                             if str(k).lower() in encounters.MODEL_PROPERTIES and v)
        for kind, names_ in found.items():
            have = loads.setdefault(kind, [])
            for n in names_:
                if n.lower() not in {h.lower() for h in have}:
                    have.append(n)
        # the templates it names (its weapons, its crew), unless every map has them
        for _, v in inst.get("properties") or []:
            v = str(v)
            if v.lower().startswith("template_") and v.lower() not in done and by_name.get(v.lower()) \
                    and not in_always(v):
                todo.append((v, None, None))
    unused = [r for r, u in zip(rules, used) if not u]
    if not insts:
        return None, [], shared, None, unused
    layer = f"{name}_templates"
    wdoc = {"format": world.FORMAT, "name": layer, "strings": [], "chunks": [
        {"tag": "NAME"}, {"tag": "TNAM", "hex": (name.upper().encode("ascii") + b"\0").hex()},
        {"tag": "INFO", "rest": "0000000000000000"}, {"tag": "TABL"}] + insts}
    sections = {"world": [layer]}
    for kind, names_ in loads.items():
        if kind.lower() not in encounters.LAYER_KINDS:
            sections.setdefault(kind, []).extend(names_)
    text = "".join(f"\n[{k}]\n\n" + "".join(f"load {n}\n" for n in sections[k]) for k in sorted(sections))
    return layer, [t["template"] for t in insts], shared, \
        {WORLD: world.from_json(wdoc), name_hash("list"): text.encode("latin-1")}, unused


def _object_loads(obj, full, archives):
    """What one object needs from a load list that loads much more (a map's):
    entries its model or properties name (its model, ruin model, animation
    table...), and the textures those models draw (by the texture hashes in
    their materials, with each one's _nm normal map)."""
    from . import model as model_mod
    # an animationTable value names an animation table only: vehicles' are
    # named after their game model (a redrawn Abrams keeps "allies_veh_m1a2")
    props = obj.get("properties") or []
    anim = {str(v).lower() for k, v in props if str(k).lower() == "animationtable"}
    wanted = {str(obj.get("model") or "").lower()} | {str(v).lower() for k, v in props
                                                      if str(k).lower() != "animationtable"}
    out = {}
    for kind, names in full.items():
        if kind.lower() in ("texture", "terrain"):
            continue
        want = wanted | anim if kind.lower() == "animtable" else wanted
        keep = [n for n in names if n.lower() in want]
        if keep:
            out[kind] = keep
    hashes = set()
    for m in out.get("model", []):
        try:
            for p in model_mod.decode(*model_mod.find(archives, m)).pieces:
                hashes.add(p.texture)
                hashes.update(w for w in p.material if w)
        except (KeyError, ValueError, IndexError):
            continue
    tex = [n for n in full.get("texture", []) if name_hash(n) in hashes
           or (n.lower().endswith("_nm") and name_hash(n[:-3]) in hashes)]
    if tex:
        out["texture"] = tex
    return out


def _add_traffic(data, f, layer, doc, result):
    """A layer's "traffic" zones, declared in its map's level config (a loose
    DATAxbox file, like terrain surfaces)."""
    from . import encounters, terrain_surfaces
    map_ = doc.get("map") or ""
    if map_.lower() in getattr(data, "new_maps", ()):
        # a mod's new map: its config is written later (the maps step), with these
        data.new_map_traffic = getattr(data, "new_map_traffic", {})
        zones = data.new_map_traffic.setdefault(map_.lower(), {})
        for zone, spec in doc["traffic"].items():
            if zone.lower() in (z.lower() for z in zones):
                raise ValueError(f"encounters/{f.name}: traffic zone {zone} is declared twice")
            encounters.add_traffic_zones("", {zone: spec})          # checks it as the config will
            zones[zone] = spec
        for zone in doc["traffic"]:
            if not encounters.zone_paths(doc, zone):
                result.warnings.append(f"encounters/{f.name}: traffic zone {zone} has no routes; name paths "
                                       f"t_{zone}_01 (vehicles) or p_{zone}_01 (people)")
        result.changes.append(f"layer {layer}: traffic zone{'s' if len(doc['traffic']) != 1 else ''} "
                              f"{', '.join(doc['traffic'])} declared in the new map {map_.lower()}'s config")
        return
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
    # A mission giver placed on the map stands in a layer of its own that
    # loads with the map: copied from its template (with the template's
    # model and texture loads), so any character works, even one whose
    # template lives in a mission's layer the game loads only then.
    givers = []
    mission_docs = {}            # mission id -> its file (for squads that appear later: "waves")
    for mf in sorted((folder / "missions").glob("*.json")) if (folder / "missions").is_dir() else []:
        mdoc = json.loads(mf.read_text(encoding="utf-8"))
        mission_docs[mf.stem.lower()] = mdoc
        c = mdoc.get("contact")
        if isinstance(c, dict) and c.get("position"):
            from . import missions as _ms
            map_, faction, _ = _ms.parse_id(mf.stem.lower())
            map_ = _ms.play_map(mf.stem.lower(), mdoc) or map_        # played on a new map: it stands there
            givers.append((f"{mf.stem.lower()}_giver", {
                "map": map_.upper(), "load": "map",
                "objects": [{"from": c.get("template") or f"template_{faction}_bouncer",
                             "name": _ms.contact_actor(mf.stem.lower()),
                             "position": [float(v) for v in c["position"]], "yaw": float(c.get("heading", 0))}]}))
    if not files and not givers and not any((folder / "maps").glob("*.json")):
        return                  # (maps: their travel pilots and HQ guards are layers too)
    archives = [data.archive(n) for n in ARCHIVES if (data.dir / n).exists()]
    existing = {r.key for _, r in data.records(WORLD)}
    layers = {}

    current_map = [""]
    by_name, by_model = _world_index(data, archives)
    # a new map's own templates ("templates" {"template", "as", "set"}): built
    # in the maps step, after this one, so an object copied from one here is
    # its game template with the copy's properties, under the copy's name
    aliases = {}
    for mf in sorted((folder / "maps").glob("*.json")) if (folder / "maps").is_dir() else []:
        try:
            tdoc = json.loads(mf.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for t in tdoc.get("templates") or []:
            if isinstance(t, dict) and t.get("as") and t.get("template"):
                aliases[str(t["as"]).lower()] = (str(t["as"]), str(t["template"]), t.get("set") or {})

    def source_hit(name):
        """The object to copy: a named one, or for "model:<model>" the first
        unnamed scenery object with that model (preferring this map's layers)."""
        if name.lower() in aliases and not by_name.get(name.lower()):
            return source_hit(aliases[name.lower()][1])
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
        if name.lower() in aliases and not by_name.get(name.lower()):
            as_, base, set_ = aliases[name.lower()]
            inst = copy.deepcopy(find_source(base))
            inst["template"] = as_
            if set_:
                _apply_rule(inst, {"set": set_})
            return inst
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

    map_keys = {r.key for _, r in data.records(name_hash("Terrain"))}

    def source_loads(name):
        if name.lower() in aliases and not by_name.get(name.lower()):
            as_, base, set_ = aliases[name.lower()]
            loads = {k: list(v) for k, v in source_loads(base).items()}
            # the models its "set" gives it (a copied character's, ...)
            for k, v in set_.items():
                if str(k).lower() in encounters.MODEL_PROPERTIES and v and \
                        str(v).lower() not in {m.lower() for m in loads.get("model", [])}:
                    loads.setdefault("model", []).append(str(v))
            return loads
        obj = source_hit(name)
        layer = obj["layer"]
        if layer not in lists:
            hit = [(a, r) for a, r in data.records(name_hash("list")) if r.key == name_hash(layer)]
            lists[layer] = encounters.parse_list(data.current(*hit[0]).decode("latin-1")) if hit else {}
        if name_hash(layer) in map_keys:
            # a map's own layer (sw, nw): its list loads the whole map, terrain
            # and all; take only what this object needs
            return _object_loads(obj, lists[layer], archives)
        return lists[layer]

    for f in files:
        layer = f.stem
        if name_hash(layer) in existing:
            raise ValueError(f"encounters/{f.name}: the game already has a layer named {layer!r}")
        doc = json.loads(f.read_text(encoding="utf-8"))
        named = len(encounters.member_names(doc)) - (len(doc.get("objects", [])) if doc.get("unnamed") else 0)
        if named > MAX_LAYER_NAMES:
            raise ValueError(
                f"encounters/{f.name}: {named} named objects; the game's named-object table holds 2048 in all "
                f"(its own layers use over a thousand) and a full one hangs the game while the layer loads. "
                f"For scenery add \"unnamed\": true to the layer (its objects then have no names in game)")
        current_map[0] = (doc.get("map") or "").lower()
        if layer.lower() in mission_docs and mission_docs[layer.lower()].get("waves"):
            # a mission's squads that appear later go in layers of their own
            from . import missions as _ms
            doc, waves = _ms.split_waves(layer.lower(), mission_docs[layer.lower()], doc)
            for wl, wdoc in waves:
                if name_hash(wl) in existing:
                    raise ValueError(f"the game already has a layer named {wl!r} (for a squad that appears later)")
                for kind, body in encounters.build(wl, wdoc, find_source, source_loads).items():
                    data.new("assets.dsk", name_hash(wl), kind, body)
                result.changes.append(f"layer {wl}: {wdoc['encounters'][0]['squads'][0]['name']}, which appears later "
                                      f"in mission {layer}")
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
            if m in getattr(data, "new_maps", ()):
                # a mod's new map: its master script loads the layer (_apply_maps)
                data.new_map_layers = getattr(data, "new_map_layers", {})
                data.new_map_layers.setdefault(m, []).append(layer)
            elif m not in missions.FREE_ROAM_MAPS:
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
                 else f"loads with the new map {(doc.get('map') or '').lower()} (its master script)"
                 if doc.get("load") == "map" and (doc.get("map") or "").lower() in getattr(data, "new_maps", ())
                 else f"load it from a script with Utility_ReadLevelFile('{layer}')")
        result.changes.append(f"layer {layer}: {n} objects, {len(doc.get('encounters', []))} encounters ({loads})")
    for layer, doc in givers:
        if name_hash(layer) in existing:
            raise ValueError(f"the game already has a layer named {layer!r} (for a mission giver)")
        current_map[0] = doc["map"].lower()
        try:
            records = encounters.build(layer, doc, find_source, source_loads)
        except ValueError as e:
            raise ValueError(f"missions/{layer[:-len('_giver')]}.json: the mission giver: {e}") from None
        for kind, body in records.items():
            data.new("assets.dsk", name_hash(layer), kind, body)
        if doc["map"].lower() in ("sw", "nw"):
            data.map_layers = getattr(data, "map_layers", []) + [
                {"map": doc["map"].lower(), "layer": layer, "check": doc["objects"][0]["name"]}]
        else:                    # on a mod's new map: its own script loads it
            data.new_map_layers = getattr(data, "new_map_layers", {})
            data.new_map_layers.setdefault(doc["map"].lower(), []).append(layer)
        result.changes.append(f"layer {layer}: the mission giver, a copy of {doc['objects'][0]['from']} "
                              f"(loads with the {doc['map']} map in free roam)")
    # Travel pilots for the mod's new maps (maps/<name>.json "travel" and
    # "return"): each in a layer of its own that loads with its map.
    for layer, map_, obj in _travel_pilots(data, folder, result):
        if name_hash(layer) in existing:
            raise ValueError(f"the game already has a layer named {layer!r} (for a travel pilot)")
        current_map[0] = map_
        doc = {"map": map_.upper(), "objects": [obj]}
        try:
            records = encounters.build(layer, doc, find_source, source_loads)
        except ValueError as e:
            raise ValueError(f"maps: the travel pilot {obj['name']}: {e}") from None
        for kind, body in records.items():
            data.new("assets.dsk", name_hash(layer), kind, body)
        if map_ in ("sw", "nw"):
            data.map_layers = getattr(data, "map_layers", []) + [{"map": map_, "layer": layer, "check": obj["name"]}]
        else:
            data.new_map_layers = getattr(data, "new_map_layers", {})
            data.new_map_layers.setdefault(map_, []).append(layer)
    # Walls round a new map's edge (maps/<name>.json "edge": "wall", the
    # default): a layer of its own that loads with the map.
    for layer, map_, doc in _edge_walls(data, folder, result):
        if name_hash(layer) in existing:
            raise ValueError(f"the game already has a layer named {layer!r} (for the map's edge)")
        current_map[0] = map_
        records = encounters.build(layer, doc, find_source, source_loads)
        for kind, body in records.items():
            data.new("assets.dsk", name_hash(layer), kind, body)
        data.new_map_layers = getattr(data, "new_map_layers", {})
        data.new_map_layers.setdefault(map_, []).append(layer)
        result.changes.append(f"layer {layer}: {len(doc['objects'])} invisible walls round the map's edge")


# A new map's edge: retail maps (all 512 cells or fewer) end in sea and
# mountains; a new one ends at its terrain's edge, which the player could walk
# or drive off. Invisible walls stand along it: copies of an indestructible
# rock (EDGE_ROCK) drawn as EDGE_MODEL, a collision box and no visible
# triangles, every EDGE_STEP m (a static's collision comes with its model,
# which streams in by distance, so many short walls rather than four long).
EDGE_MODEL = "foundry_edge_wall"
EDGE_ROCK = "summer_env_rock02"
EDGE_STEP = 128.0
EDGE_HALF = (EDGE_STEP / 2 + 2.0, 400.0, 2.0)     # along, up, across (m)
EDGE_CENTRE_Y = 150.0                             # -250 m to 550 m


def _edge_wall_model(data):
    """Add EDGE_MODEL once: retail's global_invisible (one small cube) with
    its triangles collapsed to a point and one box shape (flags 7: solid,
    hit by bullets, blocks sight) EDGE_HALF in size."""
    from . import collision
    key = name_hash(EDGE_MODEL)
    if data.find_added(key, model.MODEL) is not None:
        return
    mr, sr = data.built_model("global_invisible")
    mr = model.renamed(mr, EDGE_MODEL)
    m = model.decode(mr, sr)
    text, _ = model.to_obj(m, 0)
    text = re.sub(r"(?m)^v .*$", "v 0 0 0", text)
    mr, sr, _ = model_import.import_obj(mr, sr, text, collision=False)
    joint = name_hash(m.joints[0].name)
    sr = collision.add_segments_box_block(sr, 7, joint, [((0.0, EDGE_CENTRE_Y, 0.0), EDGE_HALF)])
    data.new("streamed.dsk", key, model.MODEL, mr)
    data.new("streamed.dsk", key, model.SEGMENTS, sr)


def edge_wall_positions(size, spacing=8.0):
    """[(x, z, yaw)] for walls along a size-cell map's edge, each just
    outside it (its inner face on the edge). The terrain spans x from
    -size*spacing/2 to size*spacing/2 - spacing and z from -(size*spacing/2 -
    spacing) to size*spacing/2 (the game's own offsets, terrain.py)."""
    half = size * spacing / 2
    x0, x1, z0, z1 = -half, half - spacing, -(half - spacing), half
    t = EDGE_HALF[2]
    out = []
    n = max(1, math.ceil((x1 - x0) / EDGE_STEP))
    for i in range(n):
        x = x0 + (i + 0.5) * (x1 - x0) / n
        out += [(x, z0 - t, 0.0), (x, z1 + t, 0.0)]
    n = max(1, math.ceil((z1 - z0) / EDGE_STEP))
    for i in range(n):
        z = z0 + (i + 0.5) * (z1 - z0) / n
        out += [(x0 - t, z, 90.0), (x1 + t, z, 90.0)]
    return out


def _edge_walls(data, folder, result):
    """(layer, map, layer doc) for each of this mod's new maps with walls round its edge."""
    out = []
    mdir = folder / "maps"
    for mf in sorted(mdir.glob("*.json")) if mdir.is_dir() else []:
        mdoc = json.loads(mf.read_text(encoding="utf-8"))
        edge = mdoc.get("edge", "wall")
        if edge in (False, "none", "open"):
            continue
        if edge not in (True, "wall"):
            raise ValueError(f"maps/{mf.name}: \"edge\" is \"wall\" (the default) or \"open\", not {edge!r}")
        name = mf.stem.lower()
        size = int(mdoc.get("size") or _source_terrain_size(data, mdoc.get("from") or "blank"))
        try:
            _edge_wall_model(data)
        except KeyError:
            result.warnings.append(f"maps/{mf.name}: no walls at the map's edge (the game data has no "
                                   f"global_invisible model to make them from)")
            continue
        if not any(r.key == name_hash(EDGE_ROCK) for _, r in data.records(model.MODEL)):
            result.warnings.append(f"maps/{mf.name}: no walls at the map's edge (the game data has no {EDGE_ROCK})")
            continue
        objects = [{"from": f"model:{EDGE_ROCK}", "name": f"{name}_edge{i}", "position": [round(x, 2), 0.0, round(z, 2)],
                    "yaw": yaw, "set": {"GeometryFile": EDGE_MODEL}}
                   for i, (x, z, yaw) in enumerate(edge_wall_positions(size))]
        out.append((f"{name}_edge", name, {"map": name.upper(), "load": "map", "unnamed": True, "objects": objects}))
    return out


def _source_terrain_size(data, source):
    from . import terrain as _terrain
    hit = next(((a, r) for a, r in data.records(name_hash("Terrain")) if r.key == name_hash(source)), None)
    try:
        return _terrain.parse_info(_terrain.tern_chunks(data.peek(*hit))[b"INFO"])["size"]
    except (TypeError, KeyError, ValueError, struct.error):
        return 512


TRAVEL_PILOT = "template_allies_teleport_pilot"      # retail's quadrant pilot


def default_arrive(pilot_position):
    """Where a player coming back lands when the pilot that brought them
    says nowhere ("arrive"): just beside it, a little above the ground."""
    x, y, z = (float(v) for v in pilot_position)
    return [round(x + 1.5, 2), round(y + 0.5, 2), round(z, 2)]


def _travel_pilots(data, folder, result):
    """Travel pilots from the mod's maps/<name>.json: "travel" [{map (sw or
    nw), position, heading, template?}] stand on those maps and take the
    player to the new map (to its "start"); "return" {position, heading,
    to (sw or nw), template?} stands on the new map and takes them back
    (next to the pilot that brought them). Records the pilots for the
    scripts (data.travel_pilots for free roam, data.new_map_pilots[map]);
    returns [(layer, map, object)]."""
    from . import missions
    mdir = folder / "maps"
    out = []
    for f in sorted(mdir.glob("*.json")) if mdir.is_dir() else []:
        name = f.stem.lower()
        mdoc = json.loads(f.read_text(encoding="utf-8"))
        title = str(mdoc.get("title") or name.capitalize())
        brought = {}
        for k, t in enumerate(mdoc.get("travel") or [], 1):
            m = str(t.get("map") or "sw").lower()
            if m not in missions.FREE_ROAM_MAPS:
                raise ValueError(f"maps/{f.name}: travel {k}: a pilot stands on {', '.join(missions.FREE_ROAM_MAPS)}"
                                 f" (free roam), not {m!r}")
            pos = [float(v) for v in t.get("position") or []]
            if len(pos) != 3:
                raise ValueError(f"maps/{f.name}: travel {k}: \"position\" is [x, y, z]")
            actor = f"foundry_travel_{name}_{k}"
            out.append((actor, m, {"from": t.get("template") or TRAVEL_PILOT, "name": actor, "position": pos,
                                   "yaw": float(t.get("heading") or 0)}))
            data.travel_pilots = getattr(data, "travel_pilots", []) + [{
                "map": m, "actor": actor, "to": name, "prompt": str(t.get("prompt") or f"Travel to {title}"),
                "title": title, "question": str(t.get("question") or f"Travel to {title}?")}]
            # back where its "arrive" says, else beside this pilot
            arrive = t.get("arrive") or {}
            brought.setdefault(m, ([float(v) for v in arrive["position"]] if arrive.get("position")
                                   else default_arrive(pos), float(arrive.get("heading", t.get("heading") or 0))))
            result.changes.append(f"travel pilot {actor}: on {m.upper()}, takes you to {name}")
        back = mdoc.get("return")
        if back:
            to = str(back.get("to") or next(iter(brought), "sw")).lower()
            if to not in missions.FREE_ROAM_MAPS:
                raise ValueError(f"maps/{f.name}: return: \"to\" is {' or '.join(missions.FREE_ROAM_MAPS)}, not {to!r}")
            pos = [float(v) for v in back.get("position") or []]
            if len(pos) != 3:
                raise ValueError(f"maps/{f.name}: return: \"position\" is [x, y, z]")
            actor = f"foundry_return_{name}"
            out.append((actor, name, {"from": back.get("template") or TRAVEL_PILOT, "name": actor, "position": pos,
                                      "yaw": float(back.get("heading") or 0)}))
            data.new_map_pilots = getattr(data, "new_map_pilots", {})
            region = {"sw": "the South", "nw": "the North"}[to]
            data.new_map_pilots.setdefault(name, []).append({
                "map": name, "actor": actor, "to": to, "prompt": str(back.get("prompt") or f"Travel back to {region}"),
                "title": region[4:], "question": str(back.get("question") or f"Travel back to {region}?"),
                "arrive": brought[to][0] if to in brought else None,
                "arrive_heading": brought[to][1] if to in brought else None})
            data.travel_arrive = True
            result.changes.append(f"travel pilot {actor}: on {name}, takes you back to {to.upper()}"
                                  + (" (beside the pilot there)" if brought.get(to) else ""))
        # HQ interiors ("interiors"): each faction's guard, the way in, in a
        # layer of its own that loads with the map, like the pilots.
        inside = missions.interiors(f"maps/{f.name}", name, mdoc)
        for fac, i in inside.items():
            out.append((i["guard"], name, {"from": i["template"], "name": i["guard"], "position": i["gpos"],
                                           "yaw": i["gheading"]}))
            result.changes.append(f"HQ interior {fac} on {name}: guard {i['guard']} ({i['template']}), "
                                  f"{len(i['leaders'])} leaders, out by {i['door']}")
        if inside:
            data.new_map_interiors = getattr(data, "new_map_interiors", {})
            data.new_map_interiors[name] = inside
    return out


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


def mission_drafts(doc):
    """What a mission file has left unfinished (no squad, zone, time...): the
    build writes no script for it until these are done."""
    drafts = [f"objective {i}" for i, o in enumerate(doc.get("objectives") or [], 1)
              if (o.get("type") in ("destroy", "escort", "subdue", "use", "verify") and not (o.get("squad") or o.get("targets")))
              or (o.get("type") in ("goto", "escort", "deliver", "hold") and not o.get("zone"))
              or (o.get("type") in ("hold", "fast") and not o.get("seconds"))
              or (o.get("type") == "deliver" and not o.get("vehicle"))
              or str(o.get("reveal") or "") in ("zone:", "dead:")]
    drafts += [f"wave {k}" for k, w in enumerate(doc.get("waves") or [], 1) if not w.get("squad")]
    drafts += [f"fail condition {k}" for k, c in enumerate(doc.get("fail") or [], 1)
               if (c.get("type") in ("dies", "hostile") and not (c.get("squad") or c.get("targets")))
               or (c.get("type") == "alarm" and not c.get("targets"))
               or (c.get("type") == "spotted" and not c.get("faction"))
               or (c.get("type") == "leave" and not c.get("zone"))
               or (c.get("type") == "timer" and not c.get("seconds"))
               or str(c.get("from") or "") in ("zone:", "dead:")]
    return drafts


def _mission_names(doc):
    """(zones, paths, named objects) a mission file uses."""
    objs = doc.get("objectives") or []
    zones = {o.get("zone") for o in objs} | {c.get("zone") for c in doc.get("fail") or []}
    for key, items in (("reveal", objs), ("when", doc.get("radio") or []), ("when", doc.get("orders") or []),
                       ("when", doc.get("waves") or []), ("from", doc.get("fail") or [])):
        zones |= {str(x[key])[5:] for x in items if str(x.get(key) or "").startswith("zone:")}
    zones.discard(None)
    zones.discard("")
    paths = {o.get("to") for o in doc.get("orders") or [] if o.get("do") == "path"}
    objects = {t.lower() for o in objs for t in (o.get("targets") or [])}
    objects |= {o["vehicle"].lower() for o in objs if o.get("vehicle")}
    return zones, paths, objects


def mission_resolvers(folder, asset_id, map_, doc, fname):
    """(squad_members, zone_centre, used, layers) for writing a mission's
    script: squads and zones come from the mission's own layer or a layer of
    the mod that loads with the map; "used" collects those map layers (they
    load only in free roam, so the script loads them)."""
    layers = []
    for lf in sorted((folder / "encounters").glob("*.json")) if (folder / "encounters").is_dir() else []:
        ldoc = json.loads(lf.read_text(encoding="utf-8"))
        if lf.stem.lower() == asset_id or ((ldoc.get("map") or "").lower() == map_ and ldoc.get("load") == "map"):
            layers.append((lf.stem, ldoc))
    used = set()

    def squad_members(name):
        # "encounter:squad", or a squad name alone (every squad of that name).
        enc, _, sq = name.rpartition(":")
        found = []
        for stem, ldoc in layers:
            sqs = [(e["name"], s) for e in ldoc.get("encounters", []) for s in e.get("squads", [])]
            sqs += [(s.get("encounter", ""), s) for s in ldoc.get("game_squads", [])]
            got = [mm["name"] for en, s in sqs
                   if s.get("name", "").lower() == sq.lower() and (not enc or en.lower() == enc.lower())
                   for mm in s.get("members", [])]
            if got and stem.lower() != asset_id:
                used.add(stem)
            found += got
        if not found:
            raise ValueError(f"missions/{fname}: no squad {name!r} in encounters/{asset_id}.json "
                             "or a layer of this mod that loads with the map")
        return found

    zones, paths, objects = _mission_names(doc)
    for stem, ldoc in layers:
        if stem.lower() != asset_id and (zones & set(ldoc.get("boundaries") or {})
                                         or paths & set(ldoc.get("paths") or {})
                                         or objects & {str(x.get("name", "")).lower() for x in ldoc.get("objects") or []}):
            used.add(stem)

    def zone_centre(name):
        for _, ldoc in layers:
            zone = (ldoc.get("boundaries") or {}).get(name)
            pts = zone.get("points") if isinstance(zone, dict) else None
            if pts:
                return [sum(p[k] for p in pts) / len(pts) for k in range(3)]
        return None

    return squad_members, zone_centre, used, layers


def check_mission(folder, asset_id, known=None):
    """The editor's Check: what would stop this mission's script being
    written, or break it in game, as the build sees it. {"problems": [...],
    "notes": [...]}; no problems means the build writes its script. known:
    the names the game's scripts define or call, to check the script's calls."""
    from . import missions, hqsequence, luacheck
    folder = Path(folder)
    problems, notes = [], []
    p = folder / "missions" / f"{asset_id}.json"
    if not p.exists():
        return {"problems": [f"no mission file missions/{asset_id}.json yet (save it first)"], "notes": []}
    doc = json.loads(p.read_text(encoding="utf-8"))
    try:
        map_, faction, number = missions.parse_id(asset_id)
    except ValueError as e:
        return {"problems": [str(e)], "notes": []}
    if not doc.get("objectives"):
        problems.append("no objectives yet")
    drafts = mission_drafts(doc)
    if drafts:
        problems.append(f"not finished: {', '.join(drafts)}")
    if (folder / "scripts" / f"{asset_id}.lua").exists():
        notes.append(f"scripts/{asset_id}.lua is the mod's own script: the objectives aren't used")
    if not (folder / "encounters" / f"{asset_id}.json").exists():
        notes.append(f"no layer encounters/{asset_id}.json yet (Add squad or Add zone makes it)")
    if doc.get("objectives") and not drafts:
        squad_members, zone_centre, used, layers = mission_resolvers(folder, asset_id, map_, doc, p.name)
        try:
            body = missions.objectives_script(asset_id, doc, squad_members, sorted(used), zone_centre)
        except ValueError as e:
            problems.append(str(e))
        else:
            # as the game's Lua will compile it (known: the game's own names, to check calls too)
            lua = luacheck.check(body) or (luacheck.unknown(body, known) if known else []) + luacheck.choices(body)
            problems += [f"the script it writes: {p}" for p in lua]
        zones, paths, _ = _mission_names(doc)
        known_zones = set().union(*[set(l.get("boundaries") or {}) for _, l in layers]) if layers else set()
        known_paths = set().union(*[set(l.get("paths") or {}) for _, l in layers]) if layers else set()
        for z in sorted(zones - known_zones):
            problems.append(f"zone {z!r} isn't in the mission's layer or a map layer of this mod")
        for x in sorted(paths - known_paths):
            problems.append(f"path {x!r} isn't in the mission's layer or a map layer of this mod")
        if used:
            notes.append(f"loads the map layers {', '.join(sorted(used))} during the mission")
    if doc.get("waves"):
        lp = folder / "encounters" / f"{asset_id}.json"
        try:
            missions.split_waves(asset_id, doc, json.loads(lp.read_text(encoding="utf-8")) if lp.exists() else {})
        except ValueError as e:
            problems.append(str(e))
    contact = doc.get("contact")
    if isinstance(contact, dict) and contact.get("position"):
        try:
            missions.marker_type(contact, faction)
        except ValueError as e:
            problems.append(str(e))
    if doc.get("hq"):
        try:
            hqsequence.plan(asset_id, doc, missions.parse_id)
        except ValueError as e:
            problems.append(str(e))
    if doc.get("test_from"):
        notes.append(f"TESTING: it starts at objective {doc['test_from']} (the ones before are skipped); "
                     "set it back to the first before sharing the mod")
    if contact is False and not doc.get("hq"):
        notes.append("no mission giver: it starts only from its Lua line")
    return {"problems": problems, "notes": notes}


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
        play = missions.play_map(asset_id, doc)
        if play:
            if play in missions.MAPS:
                raise ValueError(f"missions/{f.name}: \"map\": {play!r} is a free-roam map; a mission is played "
                                 f"on its own arc's map ({map_}) or on a mod's new map")
            if play not in getattr(data, "new_maps", ()):
                raise ValueError(f"missions/{f.name}: \"map\": {play!r} isn't a mod's new map (maps/{play}.json)")
            data.map_missions = getattr(data, "map_missions", {})
            data.map_missions.setdefault(play, []).append(asset_id)
        hit = [(a, r) for a, r in data.records(SCRIPT) if r.key == name_hash("util_misc")]
        if not hit:
            raise ValueError("the game data has no util_misc script (needed for mission arcs)")
        from . import hqsequence
        try:
            insertion = hqsequence.plan(asset_id, doc, missions.parse_id)
        except ValueError as e:
            raise ValueError(f"missions/{f.name}: {e}") from None
        for k, (a, r) in enumerate(hit):
            old = script.decode(data.current(a, r))
            before = missions.arc(old.source, faction, map_)
            if k == 0 and doc.get("after") is not None and doc["after"] != (before[-1] if before else None) \
                    and number not in before:
                # Once a number in the middle of the arc; that shifts every
                # later retail position (chapters, face cards, aces, the end).
                result.warnings.append(f"missions/{f.name}: \"after\": {doc['after']} is ignored; the mission goes "
                                       f"at the end of the {faction} {map_} arc. To have the HQ offer it between "
                                       f"retail contracts, use \"hq\": {{\"after\": <arc position>}}")
            text = missions.add_to_arc(old.source, faction, map_, number)
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
        overrides = {**ini_text, **(doc.get("ini") or {})}
        if doc.get("objectives"):
            overrides["BonusFee"] = ini_text["BonusFee"]     # flat bonus + bonus objectives' rewards
        if insertion and not any(k.lower() == "chapter" for k in (doc.get("ini") or {})):
            # the copied section has the faction's first mission's chapter
            overrides["Chapter"] = hqsequence.ini_chapter(insertion["chapter"])
        ini, section = missions.add_to_mission_ini(ini, asset_id, overrides)
        listed = missions.mission_count(ini)
        if listed > missions.MAX_MISSIONS:
            raise ValueError(
                f"mission {asset_id}: mission.ini would list {listed} contracts, and the game holds "
                f"{missions.MAX_MISSIONS}: past that it hangs at start-up, before the main menu (its mission "
                f"table, read at boot, asks for the same entry forever). Retail lists 52, so the mods turned "
                f"on can add {missions.MAX_MISSIONS - 52} new contracts in all")
        data.files[path] = ini.encode("latin-1")
        done = missions.done_mail(asset_id, doc, probe_section.replace("_", ""))      # as text_plan keyed it
        for m in [x for x in (mail, done) if x]:
            mpath = "DATAxbox/DATAPOD/emails.ini"
            mails = data.files[mpath].decode("latin-1") if mpath in data.files else \
                (data.dir / "DATAPOD" / "emails.ini").read_bytes().decode("latin-1")
            data.files[mpath] = missions.add_mail(mails, *m).encode("latin-1")
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
        drafts = mission_drafts(doc)
        if drafts:
            result.warnings.append(f"missions/{f.name}: {', '.join(drafts)} isn't finished (no squad, zone or time "
                                   "yet), so no script was written for it")
        if doc.get("objectives") and not drafts and name_hash(asset_id) not in known:
            # No script of the mod's own: one written from the objectives.
            # Squads: in the mission's layer, or a layer of the mod that loads
            # with the map (there during the mission too).
            squad_members, zone_centre, used, _ = mission_resolvers(folder, asset_id, map_, doc, f.name)
            missions.objectives_script(asset_id, doc, squad_members)          # finds the layers used
            body = missions.objectives_script(asset_id, doc, squad_members, sorted(used), zone_centre)
            data.new("assets.dsk", name_hash(asset_id), SCRIPT, script.encode(script.new(asset_id, body)))
            known.add(name_hash(asset_id))
            extras_script = f"script written from {len(doc['objectives'])} objectives, "
        else:
            extras_script = ""
        if name_hash(asset_id) not in known:
            result.warnings.append(f"missions/{f.name}: no script {asset_id} (add scripts/{asset_id}.lua)")
        if not (folder / "encounters" / f"{asset_id}.json").exists():
            result.warnings.append(f"missions/{f.name}: no layer {asset_id} (mission_GenericInit loads it; "
                                   f"add encounters/{asset_id}.json)")
        arc = missions.arc(text, faction, map_)
        extras = (f"{len(strings)} strings, " if strings else "") + ("opening mail, " if mail else "") + extras_script
        if doc.get("contact", True) is not False:
            contact, contact_strings = missions.contract(
                asset_id, doc, missions.section_value(ini, section, "MissionTitleDbKey"),
                missions.section_value(ini, section, "ShortDescriptionDbKey"))
            contracts.append(contact)
            _set_strings(data, contact_strings)
            extras += (f"offered inside the {faction} HQ on {contact['map']}, " if contact.get("interior")
                       else f"a contact at the {faction} HQ, ")
        if insertion:
            insertion["title_key"] = missions.section_value(ini, section, "MissionTitleDbKey")
            insertion["description_key"] = missions.section_value(ini, section, "ShortDescriptionDbKey")
            data.hq_insertions = getattr(data, "hq_insertions", []) + [insertion]
            extras += (f"offered by the {faction} HQ bouncer after its contract {insertion['after']} "
                       f"(chapter {insertion['chapter']}), ")
            result.warnings += [f"missions/{f.name}: \"hq\": {n}" for n in insertion["notes"]]
        if play:
            extras += f"played on {play} (the contract takes the player there), "
        result.changes.append(f"mission {asset_id}: [{section}] in mission.ini, {extras}script listed in {map_}, "
                              f"{faction} {map_} arc now {arc} "
                              f"(start it with DebugSkipToMission('{faction}', {number}))")
    return contracts


def _apply_contracts(data, contracts, result, insertions=()):
    """Contacts at faction HQs that offer mod missions, and missions the HQ
    bouncers offer in their contract sequence ("hq"; see _apply_hq_sequence)."""
    from . import missions
    data.contract_maps = {c["map"] for c in contracts} | {c["play"] for c in contracts if c.get("play")}
    data.interior_contracts = [c for c in contracts if c.get("interior")]
    data.all_contracts = list(contracts)
    body = missions.contracts_script(contracts, insertions)
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
    if contracts:
        result.changes.append(f"contacts: {', '.join(c['id'] for c in contracts)} offered at faction HQs "
                              f"(global's LoadNoMissionState runs {missions.CONTRACTS_SCRIPT})")


def _apply_hq_sequence(data, insertions, result):
    """Mod missions offered by their faction's own HQ bouncer between its
    retail contracts: text patches into util_misc, bouncer, global and
    gameflow (hqsequence.PATCHES), which foundry_contracts' functions drive."""
    from . import hqsequence
    for name, patch in hqsequence.PATCHES:
        hit = [(a, r) for a, r in data.records(SCRIPT) if r.key == name_hash(name)]
        if not hit:
            raise ValueError(f"the game data has no {name} script (needed for \"hq\" missions)")
        for a, r in hit:
            old = script.decode(data.current(a, r))
            data.set(a, r, script.encode(script.with_source(old, patch(old.source))))
    result.changes.append("HQ sequence: " + "; ".join(
        f"{c['id']} after {c['faction']} contract {c['after']} on {c['map'].upper()} (chapter {c['chapter']})"
        for c in insertions) + " (util_misc, bouncer, global and gameflow patched)")


def _apply_welcome_mail(data, folder, pname, result):
    """mod.json "welcome_mail" {sender, subject, body, map?}: a mail in
    emails.ini, its strings, and foundry_layers posting it once per save
    when free roam first starts on its map (missions.welcome_mail)."""
    from . import missions
    meta = Path(folder) / "mod.json"
    if not meta.exists():
        return
    m = json.loads(meta.read_text(encoding="utf-8")).get("welcome_mail")
    if not m:
        return
    mail = missions.welcome_mail(pname, m)
    mpath = "DATAxbox/DATAPOD/emails.ini"
    retail = (data.dir / "DATAPOD" / "emails.ini").read_bytes().decode("latin-1")
    mails = data.files[mpath].decode("latin-1") if mpath in data.files else retail
    # A save keeps its inbox as positions in emails.ini (Mail_Post 0xCE860 ->
    # 0xCE010 stores the mail's table index), so a mail that moves shows as
    # another one. Welcome mails go right after the game's own, ahead of
    # mission mails (which come and go as a mod grows), and their position
    # goes in the "posted" flag: a save holding another position posts it again.
    at = getattr(data, "welcome_end", None)
    head = retail.rstrip("\r\n")
    if at is None:
        at = len(head) if mails.startswith(head) else len(mails.rstrip("\r\n"))
    added = missions.add_mail(mails[:at], *mail[:4])
    rest = mails[at:]
    nl = "\r\n" if "\r\n" in mails else "\n"
    slot = len(re.findall(r"^\[Mail\]", added, re.M)) - 1
    added = added.rstrip("\r\n")
    data.welcome_end = len(added)
    data.files[mpath] = (added + (rest if rest.strip() else nl)).encode("latin-1")
    if re.search(r"^Id\s+%s\s*$" % re.escape(mail[0]), rest, re.M | re.I):
        raise ValueError(f"emails.ini already has a mail {mail[0]!r}")
    mail = mail + (slot,)
    _set_strings(data, mail[4])
    data.welcome = getattr(data, "welcome", []) + [mail]
    result.changes.append(f"welcome mail {mail[0]}: posted once per save when free roam starts on {mail[5].upper()}")


def _apply_map_layers(data, result):
    """Mod layers marked "load": "map": a generated foundry_layers script loads
    them when free roam starts on their map (hooked into global's
    LoadNoMissionState, like the HQ contacts)."""
    from . import missions
    key = name_hash(missions.LAYERS_SCRIPT)
    if any(r.key == key for _, r in data.records(SCRIPT)):
        raise ValueError(f"the game already has a script named {missions.LAYERS_SCRIPT}")
    data.new("assets.dsk", key, SCRIPT, script.encode(script.new(missions.LAYERS_SCRIPT,
                                                                missions.layers_script(getattr(data, "map_layers", []),
                                                                getattr(data, "travel_pilots", []),
                                                                getattr(data, "travel_arrive", False),
                                                                getattr(data, "welcome", []),
                                                                deck=bool(getattr(data, "decks", None))))))
    for map_ in missions.FREE_ROAM_MAPS:
        for a, r in data.records(name_hash("list")):
            if r.key == name_hash(map_):
                text_list = missions.add_to_list(data.current(a, r).decode("latin-1"), "script", missions.LAYERS_SCRIPT)
                if getattr(data, "decks", None):
                    text_list = missions.add_to_list(text_list, "script", "foundry_deck")
                data.set(a, r, text_list.encode("latin-1"))
    hit = [(a, r) for a, r in data.records(SCRIPT) if r.key == name_hash("global")]
    if not hit:
        raise ValueError("the game data has no global script to hook map layers into")
    for a, r in hit:
        old = script.decode(data.current(a, r))
        data.set(a, r, script.encode(script.with_source(old, missions.patch_global_layers(old.source))))
    by_map = {}
    for l in getattr(data, "map_layers", []):
        by_map.setdefault(l["map"], []).append(l["layer"])
    result.changes.append("layers loaded with their map in free roam: " +
                          "; ".join(f"{m.upper()}: {', '.join(ls)}" for m, ls in sorted(by_map.items())))


# Every path a level loads (its own path record and each layer's) goes in one
# table of 2048 slots (made at 0x205427; filled by the path class's register
# method 0x124850 -> 0x2054C0). Adding a name probes for a free slot with no
# limit (0x1F2A60), so the 2048th path hangs the load for ever: Gaza did, at
# 2,771 paths (2026-11-18). Retail's biggest path record has 150. It's a
# table of its own: named objects aren't in it. MAP_PATH_ROOM is kept free
# for paths mission layers load later.
PATH_TABLE = 2048
MAP_PATH_ROOM = 200
PATH = name_hash("path")


def _path_names(data, layer):
    """The names of the paths a layer loads ([] if none)."""
    key = name_hash(layer)
    body = data.find_added(key, PATH)
    if body is None:
        hit = [(a, r) for a, r in data.records(PATH) if r.key == key]
        body = data.current(*hit[0]) if hit else None
    if not body:
        return []
    from . import aidata
    try:
        return [n for t, n, _ in aidata.items(aidata.to_json(body, "path")) if t == "path"]
    except Exception:
        return []


def _count_paths(data, layer):
    return len(_path_names(data, layer))


# Traffic routes (paths named t_<zone>_<id> for vehicles, p_<zone>_<id> for
# walkers) join their zones only up to the game's 1,024; the rest never do
# (Gaza's car lanes, after 1,020 walker routes, stayed empty: "0 of 1024 routes").
ROUTE_CAP = 1024
_ROUTE = re.compile(r"(?i)^[tp]_[^_]+_")


def map_route_count(data, name):
    """{layer: traffic routes} a new map loads at once (as map_path_count)."""
    out = {}
    for layer in map_path_count(data, name):
        n = sum(1 for p in _path_names(data, layer) if p and _ROUTE.match(p))
        if n:
            out[layer] = n
    return out


def map_path_count(data, name):
    """{layer: paths} for what a new map loads at once: its own records, the
    layers its list and master script load, and the shared template layers."""
    from . import encounters
    layers = [name]
    body = data.find_added(name_hash(name), name_hash("list"))
    if body is not None:
        layers += encounters.parse_list(body.decode("latin-1")).get("world", [])
    layers += getattr(data, "new_map_layers", {}).get(name.lower(), [])
    layers += list(NEW_MAP_TEMPLATE_LAYERS)
    counts = {}
    for l in layers:
        if l.lower() not in {k.lower() for k in counts}:
            counts[l] = _count_paths(data, l)
    return {l: n for l, n in counts.items() if n}


def _check_map_paths(data, result):
    for name in getattr(data, "new_map_names", []):
        counts = map_path_count(data, name)
        total = sum(counts.values())
        parts = ", ".join(f"{l} {n}" for l, n in sorted(counts.items(), key=lambda kv: -kv[1]))
        if total >= PATH_TABLE:
            raise ValueError(f"map {name}: it loads {total} paths ({parts}); the game holds {PATH_TABLE - 1} at "
                             f"most and hangs loading the map past that: use fewer (a traffic zone needs a few "
                             f"dozen; retail's busiest town has 56)")
        if total:
            result.changes.append(f"map {name}: loads {total} paths of the game's {PATH_TABLE - 1} ({parts})")
        if total > PATH_TABLE - 1 - MAP_PATH_ROOM:
            result.warnings.append(f"map {name}: {total} paths leave under {MAP_PATH_ROOM} for missions' layers "
                                   f"(the game hangs at {PATH_TABLE})")
        routes = map_route_count(data, name)
        rtotal = sum(routes.values())
        if rtotal:
            rparts = ", ".join(f"{l} {n}" for l, n in sorted(routes.items(), key=lambda kv: -kv[1]))
            result.changes.append(f"map {name}: {rtotal} traffic routes of the game's {ROUTE_CAP} ({rparts})")
            if rtotal > ROUTE_CAP:
                result.warnings.append(f"map {name}: {rtotal} traffic routes (t_ and p_ paths: {rparts}); the game "
                                       f"keeps {ROUTE_CAP}, and the routes past that never join their zones (their "
                                       f"traffic stays empty): use fewer")


# A model's geometry (its segments record's CSEG) is unpacked into one block
# of the game's main heap when it streams in. Retail keeps nearly all of it
# under 138 KB (vehicles and the C-17 aside); Gaza's 158 KB block, on a heap
# whose largest gap was 138 KB, froze the game waiting for memory forever.
BIG_GEOMETRY = 131_072


def _geometry_size(segments_record):
    """Unpacked size of a segments record's geometry, or None."""
    try:
        cseg = ucfb.unwrap(segments_record, b"CSEG")
    except (ValueError, KeyError, struct.error):
        return None
    return struct.unpack_from("<I", cseg)[0] if len(cseg) >= 4 else None


def _check_model_sizes(data, result):
    bodies = [(key, body) for recs in data.add.values() for key, kind, body in recs if kind == model.SEGMENTS]
    for name, pending in data.replace.items():
        by_ident = {r.ident: r for r in data.archive(name).records if r.kind == model.SEGMENTS}
        bodies += [(by_ident[i].key, body) for i, body in pending.items() if i in by_ident]
    big = []
    for key, body in bodies:
        size = _geometry_size(body)
        if size is not None and size > BIG_GEOMETRY:
            big.append((size, data.label(key, model.SEGMENTS).split(" ", 1)[-1]))
    if not big:
        return
    big.sort(reverse=True)
    listed = ", ".join(f"{n} {s // 1024} KB" for s, n in big[:12]) + (f" and {len(big) - 12} more" if len(big) > 12 else "")
    result.warnings.append(
        f"{len(big)} model(s) unpack to more than {BIG_GEOMETRY // 1024} KB of geometry: {listed}. The game "
        "unpacks each model's geometry into one block of its main heap as it streams in; retail keeps buildings "
        "under that, and on a busy map a bigger block may find no gap, freezing the game waiting for memory. "
        "Split big models into several, or cut vertices")


def _check_models_exist(data, result):
    """Every model a mod's load lists name (objects' models and their
    GeometryFile and ruin models, which the layers list) is the game's or one
    the mod builds. A character placed with a model that doesn't exist still
    spawns: the game draws a stand-in, and once the actor wakes (about 220 m
    away) animating its skeleton on the stand-in wrote out of bounds and
    crashed the game later in unrelated places (Gaza's number cards, whose
    models/gaza_card_*.new.json had gone missing: nine crashes)."""
    from . import encounters
    LIST = name_hash("list")
    known = {r.key for _, r in data.records(model.MODEL)} | \
        {k for k, kind, _ in data.added() if kind == model.MODEL}
    # names the game's own lists already give (a copied map's whole list) are
    # retail's business, missing or not; only names the mod brings are checked
    for a, r in data.records(LIST):
        for n in encounters.parse_list(data.archive(a).read(r).decode("latin-1")).get("model", []):
            known.add(name_hash(n))
    missing = {}
    for recs in data.add.values():
        for key, kind, body in recs:
            if kind == LIST:
                for n in encounters.parse_list(body.decode("latin-1")).get("model", []):
                    if name_hash(n) not in known:
                        missing.setdefault(n, []).append(data.label(key, LIST).split(" ", 1)[-1])
    for name, pending in data.replace.items():
        by_ident = {r.ident: r for r in data.archive(name).records if r.kind == LIST}
        for ident, body in pending.items():
            r = by_ident.get(ident)
            if r is None:
                continue
            before = {m.lower() for m in encounters.parse_list(
                data.archive(name).read(r).decode("latin-1")).get("model", [])}
            for n in encounters.parse_list(body.decode("latin-1")).get("model", []):
                if n.lower() not in before and name_hash(n) not in known:
                    missing.setdefault(n, []).append(data.label(r.key, LIST).split(" ", 1)[-1])
    if missing:
        listed = "; ".join(f"{n} (in {', '.join(sorted(set(where))[:4])}"
                           f"{' and more' if len(set(where)) > 4 else ''})" for n, where in sorted(missing.items())[:12])
        raise ValueError(
            f"{len(missing)} model(s) named by the mod's layers don't exist, in the game or the mod: {listed}"
            f"{' and more' if len(missing) > 12 else ''}. An object or character given a model that doesn't exist "
            f"(GeometryFile, a ruin model) still spawns, and a character animated on the game's stand-in corrupts "
            f"memory once it wakes and crashes the game. Add the model (models/<name>.glb or .new.json) or fix the name")


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


def retail_lua_names(data):
    """Every global the game's own scripts define or call (luacheck.known_names):
    the names a mod's script can count on."""
    from . import luacheck
    if getattr(data, "_retail_names", None) is None:
        sources = []
        for a, r in data.records(SCRIPT):
            try:
                sources.append(script.decode(data.archive(a).read(r)).source)
            except Exception:
                continue
        data._retail_names = luacheck.known_names(sources) | luacheck.PLUGIN_COMMANDS
    return data._retail_names


def _check_lua(data, result):
    """The scripts this build adds or changes, checked as the game's Lua 5.0
    will compile them when they load: syntax errors (which lose the whole
    script in game, silently), and calls or event callbacks that nothing
    defines (a misspelt name)."""
    from . import luacheck
    mine = []
    for archive, recs in data.replace.items():
        mine += [body for (key, kind), body in recs.items() if kind == SCRIPT]
    for archive, recs in data.add.items():
        mine += [body for key, kind, body in recs if kind == SCRIPT]
    if not mine:
        return
    scripts = []
    for body in mine:
        s = script.decode(body)
        scripts.append((s.name, s.source))
    known = set(retail_lua_names(data))
    for _, source in scripts:          # what the mod's scripts define, for each other
        try:
            known |= set(luacheck.names(source)[0])
        except luacheck.LuaSyntaxError:
            pass
    for name, source in scripts:
        problems = luacheck.check(source)
        if not problems:
            problems = luacheck.unknown(source, known) + luacheck.choices(source)
        result.warnings += [f"script {name}: {p}" for p in problems]


def _material_warnings(rules):
    out = []
    for fname, rule in rules:
        for prop, value in rule.get("set", {}).items():
            if prop.lower() == "obstaclematerialtype" and str(value).lower() not in MATERIALS:
                out.append(f"objects/{fname}: obstaclematerialtype {value!r} isn't a material the game "
                           f"knows ({', '.join(MATERIALS)}); it will act as none")
    return out


def _load_models(data, layer_key, names):
    """A layer's load list with these models in its [model] section (those
    it doesn't load already)."""
    from . import encounters
    LIST = name_hash("list")
    for a, r in data.records(LIST):
        if r.key != layer_key:
            continue
        text = data.current(a, r).decode("latin-1")
        have = {m.lower() for m in encounters.parse_list(text).get("model", [])}
        add = []
        for n in names:
            if n.lower() not in have and n.lower() not in {x.lower() for x in add}:
                add.append(n)
        if not add:
            continue
        nl = "\r\n" if "\r\n" in text else "\n"
        if "[model]" in text.lower():
            i = text.lower().index("[model]") + len("[model]")
            text = text[:i] + nl + "".join(f"load {n}{nl}" for n in add) + text[i:]
        else:
            text = text.rstrip() + f"{nl}{nl}[model]{nl}{nl}" + "".join(f"load {n}{nl}" for n in add)
        data.set(a, r, text.encode("latin-1"))


def _apply_objects(data, folder, result, skip=()):
    from . import encounters
    # "map": <a mod's new map> rules change that map's template copies (the
    # maps step); "map": <a game map> ones only the layers its load list loads
    new_maps = getattr(data, "new_maps", set())
    rules = [(f, r) for f, r in _rules(folder, skip) if str(r.get("map") or "").lower() not in new_maps]
    if not rules:
        return
    map_layers = {}
    for _, rule in rules:
        m = str(rule.get("map") or "").lower()
        if m and m not in map_layers:
            lists = [data.current(a, r) for a, r in data.records(name_hash("list")) if r.key == name_hash(m)]
            if not lists:
                raise ValueError(f"\"map\": {rule['map']!r} is neither a game map nor a mod's new map")
            map_layers[m] = {name_hash(n) for body in lists
                             for n in encounters.parse_list(body.decode("latin-1")).get("world", [])}
    result.warnings += _material_warnings(rules)
    hits = [0] * len(rules)
    # models rules give objects (a new object's, its ruin, wheels, break-up pieces): their layer loads them
    given = [str(v) for _, rule in rules for k, v in (rule.get("set") or {}).items()
             if k.lower() in encounters.MODEL_PROPERTIES and v]
    for a, r in data.records(WORLD):
        doc = None
        for n, (fname, rule) in enumerate(rules):
            if rule.get("layer") and name_hash(rule["layer"]) != r.key:
                continue
            if rule.get("map") and r.key not in map_layers[str(rule["map"]).lower()]:
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
            if given:
                _load_models(data, r.key, given)
    for (fname, rule), n in zip(rules, hits):
        what = rule.get("name") or rule.get("template") or rule.get("model") or f"object near {rule['near']}"
        if not n:
            raise ValueError(f"{fname}: no object matches {what!r}" +
                             (f" in layer {rule['layer']!r}" if rule.get("layer") else ""))
        verb = f"copied as {rule['as']!r}" if rule.get("copy") else "removed" if rule.get("remove") else "updated"
        result.changes.append(f"objects {what}: {n} instances {verb}")


# The PDA map and the minimap draw a map's radar (its config's RadarTexture,
# "radar" when it names none) as 16 x 16 tiles, <radar>_X<i>Y<j> (0x2E7E94,
# loaded at level start by 0x181ED0): X along world x, Y along world z, each
# 1/16 of the terrain's own extent (size x spacing, centred on 0: 0xF89C0).
# Retail's are 32 x 32 DXT1 crops of a 512 x 512 image (rdrnw et al., which
# nothing draws whole). The image lies like the heightmap PNG: column = x,
# row = z.
RADAR_GRID = 16
RADAR_SIZE = 512


def _radar_tiles(rname, w, rgba):
    """(tile name, record) for each tile of a square radar image."""
    t = w // RADAR_GRID
    for x in range(RADAR_GRID):
        for y in range(RADAR_GRID):
            tile = b"".join(rgba[((y * t + j) * w + x * t) * 4:((y * t + j) * w + x * t + t) * 4] for j in range(t))
            tname = f"{rname}_x{x}y{y}"
            info = struct.pack("<6H3I", t, t, 1, 1, 1, 1, texture.DXT1, 0, 0)
            blank = texture.Texture(tname, t, t, 1, texture.DXT1, info, b"", 1, ())
            yield tname, texture.encode(texture.replace_image(blank, t, t, tile))


def _radar_image(path, where):
    w, h, rgba = png.decode(path.read_bytes())
    if w != h or w < 256 or w & (w - 1):
        raise ValueError(f"{where}: a radar image is square, 256, 512, 1024... pixels a side (this is {w}x{h})")
    while w > RADAR_SIZE:
        w, h, rgba = texture.downsample(rgba, w, h)
    return w, rgba


def _radar_names(folder):
    """Radar names of the mod's new maps (maps/<name>.json "radar", else rdr<name>)."""
    names = set()
    for f in sorted((folder / "maps").glob("*.json")) if (folder / "maps").is_dir() else []:
        try:
            given = json.loads(f.read_text(encoding="utf-8")).get("radar")
        except (ValueError, AttributeError):
            given = None
        names.add(str(given or f"rdr{f.stem}").lower())
    return names


def _map_radar(data, folder, name, mdoc, where, made):
    """The new map's radar: its image cut into tiles (maps/<name>.radar.png,
    else textures/<radar>.png), or a game radar it names. None for neither."""
    given = mdoc.get("radar")
    if given is not None and not re.fullmatch(r"[A-Za-z0-9_]+", str(given)):
        raise ValueError(f"{where}: \"radar\" is a texture name (letters, digits and _)")
    rname = str(given or f"rdr{name}")
    img = next((p for p in (folder / "maps" / f"{name}.radar.png", folder / "textures" / f"{rname}.png")
                if p.is_file()), None)
    game_has = any(r.key == name_hash(f"{rname}_x0y0") for _, r in data.records(texture.TEXTURE))
    if img is None:
        if given is None:
            return None
        if game_has:
            made.append(f"the game's radar {rname}")
            return rname
        raise ValueError(f"{where}: \"radar\": no maps/{name}.radar.png or textures/{rname}.png, "
                         f"and the game has no radar {rname}")
    if game_has:
        raise ValueError(f"{where}: the game already has a radar {rname}; name the map's own with \"radar\"")
    rel = img.relative_to(folder).as_posix()
    w, rgba = _radar_image(img, rel)
    for tname, body in _radar_tiles(rname, w, rgba):
        data.new("streamed.dsk", name_hash(tname), texture.TEXTURE, body)
    made.append(f"radar {rname} from {rel} ({RADAR_GRID * RADAR_GRID} tiles)")
    return rname


def _set_config_line(text, key, value):
    """A level config with `key value` (replaced, or added at the top)."""
    nl = "\r\n" if "\r\n" in text else "\n"
    line = f"{key}\t{value}"
    pat = re.compile(rf"(?im)^[ \t]*{re.escape(key)}\b[^\r\n]*")
    return pat.sub(lambda m: line, text, count=1) if pat.search(text) else line + nl + text


# A level config's [SoundAmbience]: soundscapes (Thresholds <name> a b c and
# up to six Ambience <name> TYPE_<weather/war> <bank.cue> lines) and the
# Default one; zones of type ambience (sound = a name, width = fade m) pick
# others. Retail SW, NW and CDDN define 21 each; blank none (silence).
AMBIENCE_TYPES = ("TYPE_NORMAL", "TYPE_DIST_WAR", "TYPE_HVY_WAR", "TYPE_RAIN", "TYPE_RAIN_DIST_WAR",
                  "TYPE_RAIN_HVY_WAR")


def _ambience_span(text):
    """(start, end) of a config's [SoundAmbience] section, without the
    comment lines that introduce the next section; None if it has none."""
    m = re.search(r"(?im)^\[SoundAmbience\][^\r\n]*\r?\n", text)
    if not m:
        return None
    nxt = re.search(r"(?m)^\[", text[m.end():])
    end = m.end() + nxt.start() if nxt else len(text)
    body = text[m.start():end]
    # trailing comments and blank lines belong to the next section
    trail = re.search(r"(?:(?:^[ \t]*(?:;[^\r\n]*)?\r?\n))*\Z", body, re.M)
    return m.start(), m.start() + (trail.start() if trail else len(body))


def _map_ambience(data, name, mdoc, where, conf_text, made):
    """The new map's config with "ambience" (a game map whose soundscapes it
    copies), "ambience_add" ({name: {TYPE_...: cue, "thresholds": [a, b, c]}})
    and "ambience_default" applied."""
    from . import sky
    src, add, default = mdoc.get("ambience"), mdoc.get("ambience_add") or {}, mdoc.get("ambience_default")
    if not (src or add or default):
        return conf_text
    nl = "\r\n" if "\r\n" in conf_text else "\n"
    span = _ambience_span(conf_text)
    section = conf_text[span[0]:span[1]] if span else ""
    if src:
        texts = sky.level_texts(data.dir, str(src), data.files)
        span_src = _ambience_span(next(iter(texts.values()))) if texts else None
        if not span_src:
            raise ValueError(f"{where}: \"ambience\": {src!r} is no map with soundscapes (SW, NW and CDDN have them)")
        first = next(iter(texts.values()))
        section = first[span_src[0]:span_src[1]].replace("\r\n", "\n").replace("\n", nl)
        made.append(f"{src}'s soundscapes")
    if not section:
        section = f"[SoundAmbience]{nl}{nl}"
    for scape, lines in add.items():
        if not re.fullmatch(r"[A-Za-z0-9_]+", scape):
            raise ValueError(f"{where}: \"ambience_add\": {scape!r} isn't a soundscape name (letters, digits and _)")
        bad = [k for k in lines if k not in AMBIENCE_TYPES and k != "thresholds"]
        if bad:
            raise ValueError(f"{where}: \"ambience_add\": {scape}: {', '.join(bad)} isn't one of "
                             f"{', '.join(AMBIENCE_TYPES)} or thresholds")
        if "TYPE_NORMAL" not in lines:
            raise ValueError(f"{where}: \"ambience_add\": {scape} needs a TYPE_NORMAL cue")
        for k, cue in lines.items():
            if k != "thresholds" and not re.fullmatch(r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+", str(cue)):
                raise ValueError(f"{where}: \"ambience_add\": {scape} {k}: {cue!r} isn't a bank.cue name")
        section = re.sub(rf"(?im)^[ \t]*(Thresholds|Ambience)[ \t]+{re.escape(scape)}\b[^\r\n]*\r?\n", "", section)
        th = lines.get("thresholds", [0.3, 0.3, 0.4])
        block = f"Thresholds\t{scape} {' '.join(str(v) for v in th)}{nl}" + "".join(
            f"Ambience\t{scape} {k}\t\t\t{lines[k]}{nl}" for k in AMBIENCE_TYPES if k in lines)
        section = section.rstrip("\r\n") + nl + nl + block
        made.append(f"soundscape {scape}")
    names = {m.lower() for m in re.findall(r"(?im)^[ \t]*(?:Thresholds|Ambience)[ \t]+(\S+)", section)}
    if default:
        if str(default).lower() not in names:
            raise ValueError(f"{where}: \"ambience_default\": no soundscape {default!r} "
                             f"(it has {', '.join(sorted(names)) or 'none'})")
        if re.search(r"(?im)^[ \t]*Default\b", section):
            section = re.sub(r"(?im)^[ \t]*Default\b[^\r\n]*", f"Default\t\t{default}", section, count=1)
        else:
            head = re.match(r"(?i)\[SoundAmbience\][^\r\n]*\r?\n", section)
            section = section[:head.end()] + f"{nl}Default\t\t{default}{nl}" + section[head.end():]
        made.append(f"ambience {default}")
    section = section.rstrip("\r\n") + nl + nl
    if span:
        return conf_text[:span[0]] + section + conf_text[span[1]:]
    return conf_text.rstrip("\r\n") + nl + nl + section


# The terrain's near-camera detail textures, from the Xbox level config
# (SW_XBOX.ini): detailType <n> <texture> (retail: 0 grass, 1 concrete, 2
# rock, 3 snow) and layerDetail <slot> <n> for every ground layer (0-15) and
# decal texture (16-31). Blank has no Xbox config, so a map copied from it
# had none and drew a default stipple near the player on every layer.
DETAIL_TYPES = ("grass", "concrete", "rock", "snow")
DETAIL_TEXTURES = ("detail_grass01", "detail_concrete01", "detail_rock01", "detail_snow01")


def _detail_for_texture(name):
    """A ground layer's detail type from its texture's name, as retail pairs
    them (SW: dirt, sand, stone and mountains rock; grass and farms grass;
    concrete concrete)."""
    n = name.lower()
    if any(k in n for k in ("grass", "field", "farm", "meadow", "crop")):
        return 0
    if any(k in n for k in ("concrete", "asphalt", "road", "street", "tarmac", "pave")):
        return 1
    if "snow" in n or "ice" in n:
        return 3
    return 2


def _map_detail(folder, name, mdoc, where, xbox_text, made):
    """The new map's Xbox config with detailType and layerDetail lines: from
    maps json "detail" ({slot: grass|concrete|rock|snow}), else, when the
    source config has none, each ground layer's from its texture name
    (terrain/<name>.layerNN.<texture>.png) and concrete for decals."""
    given = mdoc.get("detail")
    if not given and re.search(r"(?im)^\s*layerDetail\b", xbox_text or ""):
        return xbox_text
    layers = {}
    for p in (folder / "terrain").glob(f"{name}.layer*.png") if (folder / "terrain").is_dir() else []:
        m = re.fullmatch(rf"(?i){re.escape(name)}\.layer(\d+)\.(.+)\.png", p.name)
        if m:
            layers[int(m.group(1))] = m.group(2)
    slots = {s: (_detail_for_texture(layers[s]) if s in layers else 2) for s in range(16)}
    slots.update({s: 1 for s in range(16, 32)})
    for k, v in (given or {}).items():
        try:
            s = int(k)
        except ValueError:
            raise ValueError(f"{where}: \"detail\": {k!r} isn't a texture slot (0-31)") from None
        if not 0 <= s < 32 or str(v).lower() not in DETAIL_TYPES:
            raise ValueError(f"{where}: \"detail\": slot {k}: {v!r} isn't one of {', '.join(DETAIL_TYPES)}")
        slots[s] = DETAIL_TYPES.index(str(v).lower())
    text = xbox_text or ""
    text = re.sub(r"(?im)^\s*(detailType|layerDetail)\b[^\r\n]*\r?\n?", "", text)
    nl = "\r\n" if "\r\n" in text else "\n"
    block = "".join(f"detailType {i} {t}{nl}" for i, t in enumerate(DETAIL_TEXTURES)) + nl + \
        "".join(f"layerDetail {s} {slots[s]}{nl}" for s in range(32))
    made.append("terrain detail (" + ", ".join(f"{s} {DETAIL_TYPES[slots[s]]}" for s in sorted(layers) or [0]) + ")")
    return block + nl + text


def _apply_textures(data, folder, result):
    from . import modpack
    radars = _radar_names(folder)       # new maps' radar images: the maps step cuts them
    for f in sorted((folder / "textures").glob("*.png")):
        low = f.name.lower()
        if low.endswith(".mask.png"):
            continue
        if f.stem.lower() in radars:
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
                built = texture.encode(texture.replace_image(tex, w, h, rgba))
                data.set(a, r, built)
            result.changes.append(f"texture {name}: replaced ({w}x{h})")
            # a game radar (rdrnw...): the maps draw its tiles, not it
            tiles = {r.key: (a, r) for a, r in data.records(texture.TEXTURE)}
            if name_hash(f"{name}_x0y0") in tiles:
                rw, rrgba = _radar_image(f, f"textures/{f.name}")
                for tname, body in _radar_tiles(name, rw, rrgba):
                    if name_hash(tname) in tiles:
                        data.set(*tiles[name_hash(tname)], body)
                result.changes.append(f"texture {name}: its {RADAR_GRID * RADAR_GRID} radar tiles too")
        else:
            built = texture.encode(texture.new(name, w, h, rgba))
            data.new("streamed.dsk", key, texture.TEXTURE, built)
            result.changes.append(f"texture {name}: added ({w}x{h})")
        _warn_big_texture(result, f"textures/{f.name}", len(built))


# Retail's streamed textures are at most 524,372 bytes but one (699,124).
BIG_TEXTURE = 524_372


def _warn_big_texture(result, where, size):
    """The game streams each texture through its main heap in one piece: on a
    full heap a gap that big may not be free, and the game then waits for memory
    forever (seen on Gaza with a 1024x1024 texture)."""
    if size > BIG_TEXTURE:
        result.warnings.append(
            f"{where}: {size // 1024} KB built, more than any retail texture but one "
            f"({BIG_TEXTURE // 1024} KB); the game loads each texture through its main heap "
            "in one piece, and on a busy map that can freeze it waiting for memory. "
            "Halve its size if you can")


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


def _apply_bone_moves(data, folder, result):
    """Models this build moved joints of get clips of their own (bones.py),
    since the game's clips set every joint's translation."""
    from . import bones
    moved, seen = {}, set()
    for a, r in data.records(model.MODEL):
        # this mod's changes only (with several mods, each step runs once per mod)
        if r.ident not in data.replace.get(a, {}) or r.key in seen or \
                data.changed_by.get((a, r.ident)) != data.project:
            continue
        seen.add(r.key)
        old, new = (model.skeleton(data.archive(a).read(r)), model.skeleton(data.current(a, r)))
        moves = bones.moved_joints(old, new)
        if moves:
            moved[model.model_name(data.current(a, r)) or f"{r.key:08X}"] = moves
    bones.apply(data, moved, result)


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


MAP_KINDS = ("Terrain", "world", "encounter", "region", "lineregion", "path", "list")


def _renamed_record(body, new):
    """A map record (Terrain, world, ...) under a new name: its NAME chunk,
    and a world's TNAM (the terrain it's on)."""
    from . import ucfb
    roots, outer_pad = ucfb.split(body)
    if len(roots) != 1 or roots[0][0] != b"ucfb":
        return body                      # a list's text, or nothing to rename
    kids, inner_pad = ucfb.split(roots[0][1])
    if len(kids) != 1:
        return body
    tag, inner = kids[0]
    items, pad_last = ucfb.split(inner)
    items = [(t, new.encode("latin-1") + b"\0" if t in (b"NAME", b"TNAM") else b) for t, b in items]
    return ucfb.wrap(tag, ucfb.pack_children(items, pad_last), (outer_pad, inner_pad))


OBJECTIVE_SLOTS = 16      # objectives the game holds at once (0x3C31B8, 0x1760 bytes each)


def map_script(name, doc, layers, pilots=(), contacts=False, extra=(), hqs=None, inside=None, deck=False):
    """A new map's master script (run as the map loads): blank's set-up, its
    layers, where the player arrives, a message, and its travel pilots
    (missions.travel_lua) armed. extra: scripts (maps json "scripts") loaded
    into it last (Utility_LoadUtilityScript: their functions become the
    master script's, so their timers' callbacks work), each started with its
    <script>_Start() if it has one. hqs: faction HQ flags ("hqs",
    missions.hq_flags_lua); inside: HQ interiors ("interiors",
    missions.interiors_lua); both set up when free roam starts there. deck:
    the map has a mod's Deck of 52 (deck/*.json): free roam there shows its
    cards in the PDA and arms its number cards (deck.script)."""
    from .missions import _lua_str, travel_lua
    lines = [f"-- {name}: written by mercfoundry from maps/{name}.json (scripts/{name}.lua replaces it)",
             "function ScriptInit()",
             "Debug_EnableCallStack(false)",
             "bDebugOutput = false",
             "bHudOutput = false",
             "Utility_ReadLevelFile('\\\\TEMPLATE\\\\template_vehicles_ch2')   -- vehicle templates, as blank's"]
    lines += [f"Utility_ReadLevelFile({_lua_str(l)})" for l in layers]
    start = doc.get("start") or {}
    if start.get("position"):
        x, y, z = (float(v) for v in start["position"])
        lines.append(f"Actor_SetPosition('player0', {x:.2f}, {y:.2f}, {z:.2f})")
        lines.append(f"Actor_SetYaw('player0', {__import__('mercfoundry.missions', fromlist=['player_yaw']).player_yaw(start.get('heading')):.1f})")
    if doc.get("message"):
        lines.append(f"Ui_PrintHudMessage({_lua_str(str(doc['message']))})")
    if doc.get("weather"):
        # a weather preset forced on arrival, as retail missions do: the one
        # last on (from the map the player came from) otherwise stays, over
        # this map's own sky
        lines.append(f"Mission_EnableAtmosphere({_lua_str(str(doc['weather']))}, true)")
    prefix = f"foundry_{name.lower()}_Travel"
    if pilots:
        lines.append(f"{prefix}Arm()")
    hq = []                       # free roam here: the HQ flags and interiors
    if hqs:
        hq.append(f"foundry_{name.lower()}_HqFlags()")
    if inside:
        hq.append(f"foundry_{name.lower()}_HqArm()")
    if deck:
        hq += ["Utility_LoadUtilityScript('foundry_deck')",
               f"foundry_DeckEnter({_lua_str(name.lower())})",
               f"foundry_DeckArm({_lua_str(name.lower())})"]
    if contacts:                  # a mission played here, else free roam with its contacts
        from .missions import map_mission_lua
        start, funcs = map_mission_lua(name.lower(), hq)
        lines += start
    else:
        lines += hq
    for e in extra:
        lines.append(f"Utility_LoadUtilityScript({_lua_str(e)})")
        lines.append(f"if {e}_Start then {e}_Start() end")
    text = "\n".join(lines + ["end", ""])
    if pilots:
        text += travel_lua(prefix, list(pilots), by_map=False)
    if contacts:
        text += funcs
    if hqs:
        from .missions import hq_flags_lua
        text += hq_flags_lua(f"foundry_{name.lower()}_", hqs)
    if inside:
        from .missions import interiors_lua
        if not contacts:
            text += f"sFoundryOnMap = {_lua_str(name.lower())}\n"
        text += interiors_lua(f"foundry_{name.lower()}_", inside, flagged=list(hqs or {}))
    return text


def _apply_maps(data, folder, result):
    """maps/<name>.json {"from": "blank"}: a new map, a copy of a game map
    under a new name: its terrain, world layer, encounters, regions, zones,
    paths and load list (the list loading the new name's own), a level in
    levels.ini and a copy of its level config. Reshape and paint it with
    terrain/<name>.* like any map; enter it from Foundry Console with
    Utility_ResetToBaseLayerOnly('<name>'). maps/<name>/boundaries.json adds
    zones to its own lineregion record (the game makes water only there)."""
    from . import missions, sky
    mdir = folder / "maps"
    for f in sorted(mdir.glob("*.json")) if mdir.is_dir() else []:
        name = f.stem
        where = f"maps/{f.name}"
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            raise ValueError(f"{where}: a map name is letters, digits and _")
        key = name_hash(name)
        if any(r.key == key for _, r in data.records(name_hash("Terrain"))) or \
                data.find_added(key, name_hash("Terrain")) is not None:
            raise ValueError(f"{where}: there's already a map named {name!r}")
        mdoc = json.loads(f.read_text(encoding="utf-8"))
        data.new_map_names = getattr(data, "new_map_names", []) + [name]
        src = str(mdoc.get("from") or "blank")
        # What else it loads: everything a free-roam map's list loads but that
        # map's own terrain and layers (characters' animations, vehicles,
        # weapons, effects...), so any template placed on it works. blank's
        # own list has next to none of it. null: only the source's.
        like = mdoc.get("loads_like", "sw")
        made = []
        # maps/<name>/boundaries.json {"boundaries": {name: {points, properties}}}:
        # zones in the map's own lineregion record. The game makes water only
        # from a map's own record (every retail water zone is in one), not
        # from a layer's.
        bf = mdir / name / "boundaries.json"
        own_bounds = json.loads(bf.read_text(encoding="utf-8")).get("boundaries", {}) if bf.exists() else {}
        for kind in MAP_KINDS:
            hit = [(a, r) for a, r in data.records(name_hash(kind)) if r.key == name_hash(src)]
            if not hit and kind == "lineregion" and own_bounds:
                from . import aidata, encounters
                bdoc = {"format": aidata.FORMAT, "kind": "lineregion", "tree": [{"tag": "ucfb", "children": [
                    encounters._boundary_node(n, s_) for n, s_ in own_bounds.items()]}]}
                ta, _ = next((a, r) for a, r in data.records(name_hash("Terrain")) if r.key == name_hash(src))
                data.new(ta, key, name_hash(kind), aidata.from_json(bdoc))
                made.append(f"lineregion ({len(own_bounds)} zones of its own)")
                continue
            if not hit:
                if kind in ("Terrain", "world", "list"):
                    raise ValueError(f"{where}: \"from\" names no game map ({src!r} has no {kind} record)")
                continue
            a, r = hit[0]
            body = data.current(a, r)
            if kind == "list":
                # the copy loads its own layer records, not the source's
                text = body.decode("latin-1")
                text = re.sub(rf"(?im)^load {re.escape(src)}[ \t]*(?=\r?$)", f"load {name}", text)
                if like and str(like).lower() != src.lower():
                    from . import encounters
                    lhit = [(la, lr) for la, lr in data.records(name_hash("list")) if lr.key == name_hash(str(like))]
                    if not lhit:
                        raise ValueError(f"{where}: \"loads_like\" names no game map ({like!r} has no load list)")
                    skip = {k.lower() for k in encounters.LAYER_KINDS} | {"terrain"}
                    like_list = encounters.parse_list(data.current(*lhit[0]).decode("latin-1"))
                    more = {k: v for k, v in like_list.items() if k.lower() not in skip}
                    own_layers = [n for k, v in like_list.items() if k.lower() == "world" for n in v]
                    more, dropped = _trim_like_loads(data, more, own_layers)
                    if dropped:
                        made.append(f"not {like}'s scenery: {dropped}")
                    from .missions import add_to_list
                    for kind_, names_ in more.items():
                        for n_ in names_:
                            text = add_to_list(text, kind_, n_)
                    made.append(f"loads what {like} loads")
                # its skies: only those its own config names (_trim_map_skies, after the sky step)
                data.like_skies = getattr(data, "like_skies", set())
                data.like_skies.add(name.lower())
                body = text.encode("latin-1")
            else:
                body = _renamed_record(body, name)
            if kind == "world" and (mdoc.get("medevac") or mdoc.get("hq_respawn")):
                body = _move_player_start(body, mdoc.get("medevac"), where, mdoc.get("hq_respawn"))
                made.append(", ".join((["its medevac point (loc_playerstart moved)"] if mdoc.get("medevac") else [])
                                      + ([f"HQ respawn points for {', '.join(mdoc['hq_respawn'])}"]
                                         if mdoc.get("hq_respawn") else [])))
            if kind == "lineregion" and own_bounds:
                from . import aidata, encounters, modpack
                doc = aidata.to_json(body, "lineregion")
                doc = modpack.apply_layout_changes(doc, {"changed": [
                    encounters._boundary_node(n, s_) for n, s_ in own_bounds.items()]})
                body = aidata.from_json(doc)
                made.append(f"{len(own_bounds)} zones of its own")
            if kind == "Terrain" and mdoc.get("size"):
                # a size of its own: flat ground that size (the copy's layers kept)
                from . import terrain as _terrain
                size = int(mdoc["size"])
                if size != _terrain.parse_info(_terrain.tern_chunks(body)[b"INFO"])["size"]:
                    try:
                        body = _terrain.flat_record(body, size)
                    except ValueError as e:
                        raise ValueError(f"{where}: {e}") from None
                    made.append(f"flat terrain {size} x {size}")
                    if src.lower() != "blank":
                        result.warnings.append(f"{where}: the copy of {src} has flat ground {size} cells a side: "
                                               f"{src}'s own objects and layers stand on its old ground")
            data.new(a, key, name_hash(kind), body)
            made.append(kind)
            if kind == "Terrain":
                data.new_map_terrain = getattr(data, "new_map_terrain", {})
                data.new_map_terrain[key] = body
        # Its master script: the copied load list loads a script of the map's
        # own name (blank's loads 'blank'), and the game hangs loading a
        # script that isn't there. A mod's scripts/<name>.lua (added by the
        # scripts step) wins; otherwise a copy of the source map's.
        # The map's file can say where the player arrives and what shows; its
        # layers ("load": "map") load with it.
        map_rules = [(fn, r) for fn, r in _rules(folder) if str(r.get("map") or "").lower() == name.lower()]
        for fn, r in map_rules:
            if any(k in r for k in ("move", "position", "turn", "copy", "remove")):
                raise ValueError(f"objects/{fn}: a rule with \"map\": {name} changes properties (\"set\") of the "
                                 f"map's template copies; it can't move, copy or remove")
        # "template_overrides": {<game template>: {<property>: value} or <template>}:
        # what the game spawns by name (the extraction helicopter, support
        # aircraft, the shop's deliveries) spawns as the map's own while it's
        # loaded: a property set makes a copy of the map's own
        # (template_<map>_<rest>), a name uses that template (the map's "as"
        # copy or a game template); Foundry Console swaps it at the spawner
        overrides = mdoc.get("template_overrides") or {}
        if not isinstance(overrides, dict):
            raise ValueError(f"{where}: \"template_overrides\" is {{<game template>: {{<property>: value}} or <template>}}")
        templates_wanted = list(mdoc.get("templates") or [])
        own_copies = {str(t.get("as")).lower() for t in templates_wanted if isinstance(t, dict) and t.get("as")}
        swaps = []
        for t, spec in overrides.items():
            t = str(t)
            if not re.fullmatch(r"template_[A-Za-z0-9_]+", t):
                raise ValueError(f"{where}: \"template_overrides\": {t!r} isn't a template name")
            if isinstance(spec, dict):
                if not spec:
                    raise ValueError(f"{where}: \"template_overrides\": {t}: no properties to change")
                as_ = f"template_{name.lower()}_{t[len('template_'):]}"
                if as_.lower() in own_copies:
                    raise ValueError(f"{where}: \"template_overrides\": {t}: \"templates\" already has a copy named "
                                     f"{as_}; give that one the properties, or name it in the override")
                templates_wanted.append({"template": t, "as": as_, "set": spec})
                own_copies.add(as_.lower())
                swaps.append((t, as_))
            elif isinstance(spec, str) and spec:
                if not re.fullmatch(r"template_[A-Za-z0-9_]+", spec):
                    raise ValueError(f"{where}: \"template_overrides\": {t}: {spec!r} isn't a template name")
                swaps.append((t, spec))
            else:
                raise ValueError(f"{where}: \"template_overrides\": {t}: give {{<property>: value}} or a template's name")
        if swaps:
            by_name_, _ = _world_index(data, [data.archive(n) for n in ARCHIVES if (data.dir / n).exists()])
            known_templates = {k for k in by_name_ if k.startswith("template_")}
            for t, to in swaps:
                if t.lower() not in known_templates:
                    raise ValueError(f"{where}: \"template_overrides\": the game has no template {t!r}")
                if to.lower() not in own_copies and to.lower() not in known_templates:
                    raise ValueError(f"{where}: \"template_overrides\": {t}: no template {to!r} (a game template, "
                                     f"or one of this map's \"templates\" copies)")
            data.map_template_swaps = getattr(data, "map_template_swaps", {})
            data.map_template_swaps[name.lower()] = swaps
        if templates_wanted or map_rules:
            tlayer, tnames, tshared, trecs, unused = _map_templates(data, name, templates_wanted, where,
                                                                     map_rules)
            for fn, r in unused:
                what = r.get("template") or r.get("name") or r.get("model")
                result.warnings.append(
                    f"objects/{fn}: \"map\": {name}: no template {name} copies matches {what!r}, so the rule "
                    f"changes nothing there. A template every map loads (template_vehicles_ch2...) isn't copied: "
                    f"give {where} \"templates\" a copy under a name of its own ({{\"template\", \"as\", \"set\"}}) "
                    f"and use that name on {name}")
            if tshared:
                made.append(f"{len(tshared)} templates load on every map already, not copied "
                            f"({', '.join(tshared)})")
            if tlayer:
                if any(r.key == name_hash(tlayer) for _, r in data.records(WORLD)):
                    raise ValueError(f"{where}: the game already has a layer named {tlayer!r}")
                for kind_, body_ in trecs.items():
                    data.new("assets.dsk", name_hash(tlayer), kind_, body_)
                data.new_map_layers = getattr(data, "new_map_layers", {})
                data.new_map_layers.setdefault(name.lower(), []).insert(0, tlayer)
                listed = len(mdoc.get("templates") or []) - len(tshared)
                made.append(f"{listed} templates in {tlayer}" + (
                    f" (and {len(tnames) - listed} they name: {', '.join(tnames[listed:])})" if len(tnames) > listed else ""))
        layers = getattr(data, "new_map_layers", {}).get(name.lower(), [])
        pilots = getattr(data, "new_map_pilots", {}).get(name.lower(), [])
        played = getattr(data, "map_missions", {}).get(name.lower(), [])
        contacts = name.lower() in getattr(data, "contract_maps", set()) or bool(played)
        hqs = missions.hq_flags(where, mdoc["hqs"]) if mdoc.get("hqs") else None
        # "hud_textures": {<game HUD texture>: <texture>}: the HUD and PDA draw
        # the map's own art while it's loaded (Foundry Console; written with
        # the map's texts after every step, when every texture is in)
        hud = mdoc.get("hud_textures") or {}
        if not isinstance(hud, dict):
            raise ValueError(f"{where}: \"hud_textures\" is {{<game texture>: <texture>}}")
        if hud:
            data.map_hud = getattr(data, "map_hud", {})
            data.map_hud.setdefault(name.lower(), {}).update({str(k): str(v) for k, v in hud.items()})
            data.map_text = getattr(data, "map_text", {})
            data.map_text.setdefault(name.lower(), {})
        inside = getattr(data, "new_map_interiors", {}).get(name.lower())
        for c in getattr(data, "interior_contracts", []):
            if c["map"] == name.lower() and c["interior"] not in (inside or {}):
                raise ValueError(f"missions/{c['id']}.json: \"contact\" \"interior\": {c['interior']}, but "
                                 f"{where} has no \"interiors\" {c['interior']}")
        # The game holds 16 objectives at once (Objective_FindOrCreate's pool;
        # a full pool makes Objective_* calls do nothing): free roam here
        # marks each placed giver, each HQ flag and each marked guard.
        givers = [c for c in getattr(data, "all_contracts", []) if c["map"] == name.lower() and c.get("position")
                  and c.get("target_type")]
        guards = [f for f, i in (inside or {}).items() if i["marker"] == "hq"]
        marks = len(givers) + len(hqs or {}) + len(guards)
        if marks > OBJECTIVE_SLOTS:
            result.warnings.append(
                f"{where}: free roam here marks {marks} things ({len(givers)} mission givers, {len(hqs or {})} HQ "
                f"flags, {len(guards)} HQ guards), but the game holds {OBJECTIVE_SLOTS} objectives at once: the "
                f"rest get no marker (give some givers \"marker\": \"none\", or offer them inside an HQ)")
        deck = any(d["map"] == name.lower() for d in getattr(data, "decks", []))
        if deck:
            lkey = data.find_added(key, name_hash("list"))
            if lkey is not None:
                text = lkey.decode("latin-1")
                for s_ in ("mission_objective", "mission_objectiveverify", "util_misc", "util_debug", "foundry_deck"):
                    text = missions.add_to_list(text, "script", s_)
                data.replace_added(key, name_hash("list"), text.encode("latin-1"))
        if (hqs or inside) and not contacts:
            lkey = data.find_added(key, name_hash("list"))
            if lkey is not None:
                text = lkey.decode("latin-1")
                for s_ in ("util_misc", "util_objectives"):
                    text = missions.add_to_list(text, "script", s_)
                data.replace_added(key, name_hash("list"), text.encode("latin-1"))
        if contacts:
            # the scripts its missions and contacts run (in case its list doesn't
            # load what SW's does)
            lkey = data.find_added(key, name_hash("list"))
            if lkey is not None:
                text = lkey.decode("latin-1")
                for s_ in ["mission", "mission_objective", "mission_objectiveverb", "mission_objectiveverify",
                           "util_misc", "util_debug", "gameflow", "hud", "util_banter", "util_objectives",
                           missions.CONTRACTS_SCRIPT] + played:
                    if any(r.key == name_hash(s_) for _, r in data.records(SCRIPT)) or \
                            data.find_added(name_hash(s_), SCRIPT) is not None:
                        text = missions.add_to_list(text, "script", s_)
                data.replace_added(key, name_hash("list"), text.encode("latin-1"))
        # maps json "scripts": the mod's (or game's) scripts its master script
        # runs after its own set-up, in its load list
        extra = [str(e) for e in mdoc.get("scripts") or []]
        for e in extra:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", e):
                raise ValueError(f"{where}: \"scripts\": {e!r} isn't a script name (letters, digits and _)")
            if e.lower() == name.lower():
                raise ValueError(f"{where}: \"scripts\": {e} is the map's own master script")
            if not any(r.key == name_hash(e) for _, r in data.records(SCRIPT)) and \
                    data.find_added(name_hash(e), SCRIPT) is None:
                raise ValueError(f"{where}: \"scripts\": no script {e} (add scripts/{e}.lua)")
        if extra:
            lbody = data.find_added(key, name_hash("list"))
            if lbody is not None:
                text = lbody.decode("latin-1")
                for e in extra:
                    text = missions.add_to_list(text, "script", e)
                data.replace_added(key, name_hash("list"), text.encode("latin-1"))
        if not any(r.key == key for _, r in data.records(SCRIPT)) and data.find_added(key, SCRIPT) is None:
            if mdoc.get("start") or mdoc.get("message") or mdoc.get("weather") or layers or pilots or contacts or extra \
                    or hqs or inside or deck:
                source = map_script(name, mdoc, layers, pilots, contacts, extra, hqs, inside, deck=deck)
                made.append("script (" + ", ".join(
                    (["where you arrive"] if (mdoc.get("start") or {}).get("position") else [])
                    + ([f"loads {', '.join(layers)}"] if layers else [])
                    + (["a message"] if mdoc.get("message") else [])
                    + (["a pilot back"] if pilots else [])
                    + ([f"missions {', '.join(played)}" if played else "contracts"] if contacts else [])
                    + ([f"runs {', '.join(extra)}"] if extra else [])
                    + ([f"HQ flags for {', '.join(hqs)}"] if hqs else [])
                    + ([f"HQ interiors for {', '.join(inside)}"] if inside else [])
                    + (["its Deck of 52"] if deck else [])) + ")")
            else:
                hit = [(a, r) for a, r in data.records(SCRIPT) if r.key == name_hash(src)]
                source = script.decode(data.current(*hit[0])).source if hit else map_script(name, mdoc, [])
                made.append(f"script (a copy of {src}'s)" if hit else "script")
            data.new("assets.dsk", key, SCRIPT, script.encode(script.new(name, source.replace("\r\n", "\n"))))
        else:
            if layers:
                result.warnings.append(f"{where}: scripts/{name}.lua is its master script, so load "
                                       f"{', '.join(layers)} from it (Utility_ReadLevelFile)")
            if extra:
                result.warnings.append(f"{where}: scripts/{name}.lua is its master script, so run "
                                       f"{', '.join(extra)} from it (Utility_LoadUtilityScript)")
        # its level: a levels.ini section (as the source's) and a copy of its config
        lpath = "DATAxbox/levels.ini"
        levels = data.files[lpath].decode("latin-1") if lpath in data.files else \
            (data.dir / "levels.ini").read_bytes().decode("latin-1")
        texts = sky.level_texts(data.dir, src, data.files)
        if texts:
            conf_path = next(iter(texts))                       # DATAxbox/TEST/blank.ini
            conf_dir = conf_path.rsplit("/", 1)[0]
            new_conf = f"{conf_dir}/{name}.ini"
            # The level runs the script its config names (MainMasterScript):
            # blank's names none, so its script never ran. Name the map's own,
            # unless the source names one (sw's: global, free roam itself).
            def with_master(text):
                if re.search(r"(?im)^\s*MainMasterScript\b", text):
                    return text
                nl_ = "\r\n" if "\r\n" in text else "\n"
                return f"MainMasterScript\t{name}{nl_}{nl_}" + text
            master = re.search(r"(?im)^\s*MainMasterScript\s+(\S+)", texts[conf_path])
            if master and layers:
                result.warnings.append(f"{where}: {src}'s config runs the script {master.group(1)}, not "
                                       f"{name}'s, so {', '.join(layers)} won't load with it; load them from "
                                       f"{master.group(1)} or copy blank")
            conf_text = with_master(texts[conf_path])
            radar = _map_radar(data, folder, name, mdoc, where, made)
            if radar:
                conf_text = _set_config_line(conf_text, "RadarTexture", radar)
            conf_text = _map_ambience(data, name, mdoc, where, conf_text, made)
            traffic =getattr(data, "new_map_traffic", {}).get(name.lower())
            if traffic:
                from . import encounters as _enc
                conf_text = _enc.add_traffic_zones(conf_text, traffic)
                made.append(f"{len(traffic)} traffic zone{'s' if len(traffic) != 1 else ''}")
            data.files[new_conf] = conf_text.encode("latin-1")
            xbox = next(iter(list(texts.values())[1:]), None)   # its Xbox config, if any
            xbox = _map_detail(folder, name, mdoc, where, with_master(xbox) if xbox else None, made)
            if xbox:
                data.files[f"{conf_dir}/{name}_XBOX.ini"] = xbox.encode("latin-1")
            rel = "\\" + new_conf[len("DATAxbox/"):].replace("/", "\\")
            nl = "\r\n" if "\r\n" in levels else "\n"
            if not re.search(rf"(?im)^\[LV_{re.escape(name)}\]", levels):
                section = (f"[LV_{name}]{nl}Name\t\t\t{name}{nl}Path\t\t\t\\{conf_dir[len('DATAxbox/'):]}\\{nl}"
                           f"AssetFile\t\t\\{conf_dir[len('DATAxbox/'):]}\\{name}.lvl{nl}"
                           f"TextureFile\t\t\\{conf_dir[len('DATAxbox/'):]}\\{name}t.lvl{nl}"
                           f"WorldFile\t\t\\{conf_dir[len('DATAxbox/'):]}\\{name}w.lvl{nl}ConfigFile\t\t{rel}{nl}")
                levels = levels.rstrip("\r\n") + nl + nl + section
                # and in the list of level names, as the game lists them
                m = re.search(r"(?im)^\[LevelNamesList\][^\[]*", levels)
                if m:
                    block = m.group(0).rstrip("\r\n")
                    levels = (levels[:m.start()] + block + nl + f"LevelName\tLV_{name}" + nl + nl
                              + levels[m.end():].lstrip("\r\n"))
                data.files[lpath] = levels.encode("latin-1")
        result.changes.append(f"map {name}: new, from {src} ({', '.join(made)}; a level in levels.ini"
                              + (f", config {name}.ini" if texts else "") + f"); enter it with "
                              f"Utility_ResetToBaseLayerOnly('{name}')")


def _apply_terrain(data, folder, result):
    from . import png, terrain, terrain_decals, terrain_layers, terrain_light, terrain_paint
    tdir = folder / "terrain"
    work = {}       # terrain name -> {"heights": path, "table": path, "layers": {index: path}}
    tint_maps = set()       # images a <map>.lighting.json names as its tint map
    for lj in tdir.glob("*.lighting.json"):
        try:
            t = json.loads(lj.read_text(encoding="utf-8")).get("tint_map")
        except (ValueError, AttributeError):
            continue
        if t:
            tint_maps.add(Path(str(t)).name.lower())
    for f in sorted(tdir.glob("*.png")):
        if f.name.lower() in tint_maps:
            continue
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
    for f in sorted(tdir.glob("*.lighting.json")):
        work.setdefault(f.name[:-len(".lighting.json")], {})["lighting"] = f
    for f in sorted(tdir.glob("*.surfaces.json")):
        _apply_surfaces(data, f, f.name[:-len(".surfaces.json")], result)
    for name, job in work.items():
        key = name_hash(name)
        hit = [(a, r) for a, r in data.records(name_hash("Terrain")) if r.key == key]
        if not hit and data.find_added(key, name_hash("Terrain")) is not None:
            hit = [(None, None)]                 # a new map's (maps/): its copied terrain
        if not hit:
            raise ValueError(f"terrain/{name}.*: no terrain named {name!r}")
        notes = []
        new_maps = getattr(data, "new_map_terrain", {})
        for a, r in hit:
            notes = []
            record = data.current(a, r) if a else data.find_added(key, name_hash("Terrain"))
            original = data.archive(a).read(r) if a else new_maps[key]
            chunks = terrain.tern_chunks(record)
            replace = {}
            if "heights" in job:
                f = job["heights"]
                _, base, _ = terrain.decode_height_units(terrain.tern_chunks(original))
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
                if "lighting" in job:
                    note = f"lit from {job['lighting'].name}"
                elif meta.get("relight", True):
                    colr, note = terrain_light.relight_chunks(chunks, replace)
                    if colr is not None:
                        replace[b"COLR"] = colr
                notes.append(f"{changed} height samples changed; {note}")
            if "table" in job or "layers" in job:
                table = None
                if "table" in job:
                    table = json.loads(job["table"].read_text(encoding="utf-8"))["layers"]
                    # the game's textures, ones added so far, and the mod's own textures/*.png
                    known = {rec.key for _, rec in data.records(name_hash("Texture"))}
                    known |= {k for k, kind, _ in data.added() if kind == name_hash("Texture")}
                    tex_dir = folder / "textures"
                    known |= {name_hash(f.name[:-len(".png")]) for f in tex_dir.glob("*.png")
                              if not f.name.lower().endswith((".mask.png", ".changes.png"))} if tex_dir.is_dir() else set()
                    for t in table:
                        if name_hash(t["texture"]) not in known:
                            result.warnings.append(f"terrain/{job['table'].name}: no texture named {t['texture']!r} in the game or the mod")
                    _load_textures_with_map(data, name, [t["texture"] for t in table if t.get("texture")])
                images = {}
                if job.get("layers"):
                    # Only pixels this mod changed from the game's paint
                    # replace the current paint (other mods' included).
                    base_w, base_imgs = terrain_paint.layer_images(terrain.tern_chunks(original))
                    _, cur_imgs = terrain_paint.layer_images({**chunks, **replace})
                    # layers the table adds start unpainted (the original has none)
                    for k in range(len(table or [])):
                        base_imgs.setdefault(k, bytes(base_w * base_w))
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
                    heavy = sum(1 for p in terrain_layers.decode_ptch(replace.get(b"PTCH", chunks[b"PTCH"]))
                                if bin(p.layer_mask).count("1") > terrain_paint.RETAIL_MAX_PATCH_LAYERS)
                    if heavy:
                        result.warnings.append(
                            f"terrain {name}: {heavy} patches use more than {terrain_paint.RETAIL_MAX_PATCH_LAYERS} "
                            f"ground layers, more than any retail patch (paint fewer layers there if the ground looks wrong)")
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
            if "lighting" in job:
                # the whole ground lit from a chosen sun, after its final heights
                try:
                    settings = terrain_light.lighting_settings(
                        json.loads(job["lighting"].read_text(encoding="utf-8")), _sun_angle(data, folder, name))
                except ValueError as err:
                    raise ValueError(f"terrain/{job['lighting'].name}: {err}") from err
                tint_rgb = None
                if settings.get("tint_map"):
                    tp = folder / "terrain" / settings["tint_map"]
                    if not tp.exists():
                        raise ValueError(f"terrain/{job['lighting'].name}: its tint_map terrain/{tp.name} is missing")
                    try:
                        tint_rgb = terrain_light.load_tint_map(tp, terrain.parse_info(chunks[b"INFO"])["size"])
                    except ValueError as err:
                        raise ValueError(f"terrain/{job['lighting'].name}: {err}") from err
                colr, note = terrain_light.bake({**chunks, **replace}, settings, tint_rgb)
                replace[b"COLR"] = colr
                notes.append(note)
            if a:
                data.set(a, r, terrain.rebuild(record, replace))
            else:
                data.replace_added(key, name_hash("Terrain"), terrain.rebuild(record, replace))
        result.changes.append(f"terrain {name}: " + "; ".join(notes))


def _sun_angle(data, folder, name):
    """The map's SunAngle: the mod's sky/<map>.json, else its level config's."""
    from . import sky
    p = folder / "sky" / f"{name.lower()}.json"
    if p.exists():
        for k, v in (json.loads(p.read_text(encoding="utf-8")).get("base") or {}).items():
            if k.lower() == "sunangle":
                return v
    texts = sky.level_texts(data.dir, name, data.files)
    if texts:
        for k, v in sky.base(next(iter(texts.values()))).items():
            if k.lower() == "sunangle":
                return v
    return None


def _apply_surfaces(data, f, name, result):
    """terrain/<name>.surfaces.json -> the level config (a loose DATAxbox file)."""
    from . import terrain_surfaces
    # levels.ini as the build has it: a mod's new map adds its level there
    # and its config is a loose file the build wrote
    levels = data.dir / "levels.ini"
    lpath = "DATAxbox/levels.ini"
    levels_text = data.files[lpath].decode("latin-1") if lpath in data.files else \
        (levels.read_text(encoding="latin-1") if levels.exists() else "")
    config = terrain_surfaces.level_config(levels_text, name) if levels_text else None
    path = "DATAxbox/" + config.replace("\\", "/").lstrip("/") if config else None
    if not config or not (path in data.files or (data.dir / config.lstrip("\\/")).exists()):
        raise ValueError(f"terrain/{f.name}: no level uses terrain {name!r}")
    config = config.lstrip("\\/")
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


def _glb_textures(data, name, f, result):
    """A glTF model's own textures (materials that aren't the game's
    textures) added as <model>_<material>, and loaded wherever the model is.
    Returns the import's material -> texture hash."""
    from . import encounters, glb_textures
    have = getattr(data, "texture_keys", None)
    if have is None:
        have = data.texture_keys = {r.key for _, r in data.records(texture.TEXTURE)}
    have = have | {k for k, kind, _ in data.added() if kind == texture.TEXTURE}
    made, warnings = glb_textures.textures(f, name, lambda h: h in have)
    result.warnings += [f"model {name}: {w}" for w in warnings]
    for material, tex_name, w, h, rgba in made:
        key = name_hash(tex_name)
        body = texture.encode(texture.new(tex_name, w, h, rgba))
        if not data.replace_added(key, texture.TEXTURE, body):
            hit = [(a, r) for a, r in data.records(texture.TEXTURE) if r.key == key]
            if hit:
                for a, r in hit:
                    data.set(a, r, body)
            else:
                data.new("streamed.dsk", key, texture.TEXTURE, body)
        _warn_big_texture(result, f"model {name}: texture {tex_name}", len(body))
    # textures this file's materials name that the mod added for another
    # model (a damaged version sharing its building's texture), or the game's
    # own (a tank's track texture): they load wherever this model does too,
    # not only where that other model is
    added = {k for k, kind, _ in data.added() if kind == texture.TEXTURE}
    shared = []
    try:
        from . import gltf_anim
        doc, _ = gltf_anim.load(f)
        for mat in doc.get("materials") or []:
            stem = re.sub(r"\.\d{3}$", "", mat.get("name", ""))
            if stem and name_hash(stem) in (added | have) and stem.lower() not in {t.lower() for _, t, *_ in made}:
                shared.append(stem)
    except (OSError, ValueError, KeyError):
        pass
    if made:
        result.changes.append(f"model {name}: textures from {f.name}: " + ", ".join(
            f"{t} ({w}x{h})" for _, t, w, h, _ in made))
    if made or shared:
        names = [t for _, t, *_ in made] + shared
        # every load list that loads the model loads them too (the mod's
        # layers, built earlier, and the game's own lists)
        LIST = name_hash("list")

        def with_textures(text):
            sections = encounters.parse_list(text)
            if name.lower() not in {m.lower() for m in sections.get("model", [])}:
                return None
            have_t = {t.lower() for t in sections.get("texture", [])}
            add = [t for t in names if t.lower() not in have_t]
            if not add:
                return None
            nl = "\r\n" if "\r\n" in text else "\n"
            if "texture" in sections:
                i = text.lower().index("[texture]") + len("[texture]")
                return text[:i] + nl + "".join(f"load {t}{nl}" for t in add) + text[i:]
            return text.rstrip() + f"{nl}{nl}[texture]{nl}{nl}" + "".join(f"load {t}{nl}" for t in add)
        for key, kind, archive in data.added():
            if kind == LIST:
                new = with_textures(data.find_added(key, LIST).decode("latin-1"))
                if new is not None:
                    data.replace_added(key, LIST, new.encode("latin-1"))
        for a, r in data.records(LIST):
            new = with_textures(data.current(a, r).decode("latin-1"))
            if new is not None:
                data.set(a, r, new.encode("latin-1"))
    return glb_textures.material_hash(made)


def _new_models(data, folder, result):
    """models/<name>.new.json {"base": "<a game model>"} with models/<name>.glb
    (or .gltf / .obj): a model under a new name. It starts as a copy of the
    base model (its skeleton and vertex format) without its collision; the
    file's pieces go in (pieces named p<n>, as an exported model's are, take
    the base's piece n's place; the base's other pieces go), with collision
    boxes fitted to them.
    Returns the names made (models/ then skips their files). On a vehicle
    base the boxes form a vehicle's two shapes (body and bullet hull)."""
    from . import collision
    made = set()
    for spec in sorted((folder / "models").glob("*.new.json")) if (folder / "models").is_dir() else []:
        name = spec.name[:-len(".new.json")]
        where = f"models/{spec.name}"
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
            raise ValueError(f"{where}: a model name is letters, digits, _ and -")
        key = name_hash(name)
        if any(r.key == key and r.kind == model.MODEL for _, r in data.records(model.MODEL)):
            raise ValueError(f"{where}: the game already has a model named {name!r} (replace it with models/{name}.glb)")
        spec_doc = json.loads(spec.read_text(encoding="utf-8"))
        base = str(spec_doc.get("base") or "")
        # "shadow": false drops the base's shadow mesh (vehicles draw their
        # shadow from it: a drone on the Little Bird cast the Little Bird's)
        keep_shadow = spec_doc.get("shadow", True)
        if keep_shadow not in (True, False):
            raise ValueError(f"{where}: \"shadow\" is true (the base's, the default) or false (none)")
        src = None
        for a in ("streamed.dsk", "locked.dsk"):
            if not (data.dir / a).exists():
                continue
            recs = {r.kind: r for r in data.archive(a).records
                    if r.key == name_hash(base) and r.kind in (model.MODEL, model.SEGMENTS)}
            if len(recs) == 2:
                src = (a, recs)
                break
        if not src:
            raise ValueError(f"{where}: \"base\" names no game model ({base!r})")
        if spec_doc.get("copy"):
            # not in made: models/<name>.glb (and .lod<N>.glb) then edit the
            # copy as they would edit a game model
            _copy_model(data, name, base, src, spec_doc, where, keep_shadow, result)
            continue
        files = [f for f in (folder / "models").glob(f"{name}.*") if f.suffix.lower() in (".glb", ".gltf", ".obj")]
        if not files:
            raise ValueError(f"{where}: no models/{name}.glb (or .gltf, .obj) with its pieces")
        f = files[0]
        a, recs = src
        mr = model.renamed(data.archive(a).read(recs[model.MODEL]), name)
        dropped_shadow = not keep_shadow and model.has_shadow(mr)
        if dropped_shadow:
            mr = model.without_shadow(mr)
        base_sr = data.archive(a).read(recs[model.SEGMENTS])
        vehicle = collision.is_vehicle_collision(model.decode(data.archive(a).read(recs[model.MODEL]), base_sr).collision)
        sr = model.without_collision(base_sr)
        # none of the base's pieces stay: the file's pieces are all new
        empty = model.decode(mr, sr)
        if f.suffix.lower() == ".obj":
            text = f.read_text(encoding="utf-8", errors="replace")
            lod, damaged = _obj_settings(text)
            nm, ns, report = model_import.import_obj(mr, sr, text, lod, damaged)
        else:
            from . import gltf_model
            nm, ns, report = gltf_model.import_gltf(mr, sr, f, material_hash=_glb_textures(data, name, f, result))
        # the base's pieces the file didn't take over (other levels of detail
        # or damage states the import left alone) go too; originals stay
        # first, in order, the file's new pieces after them
        kept = [i for i in range(len(empty.pieces)) if i not in set(report.removed)]
        in_file = set(report.edited) | set(report.kept)      # a piece the file has as exported stays too
        leftover = [j for j, i in enumerate(kept) if i not in in_file]
        if leftover:
            nm, ns = model.drop_pieces(nm, ns, leftover)
            # the base's other levels of detail went: what's left draws at all
            # of them (as the file's new pieces do), not only near ones
            all_lods = 0
            for p in empty.pieces:
                all_lods |= p.lod_mask
            ns = model.set_lod_masks(ns, all_lods)
        if vehicle and report.collision_boxes and len(model.decode(nm, ns).collision) == 1:
            # a vehicle's boxes as its two bodies: one shape alone becomes the
            # driving body and bullets pass through (collision.VEHICLE_BODY)
            ns = collision.split_segments_vehicle_boxes(ns)
        data.new("streamed.dsk", key, model.MODEL, nm)
        data.new("streamed.dsk", key, model.SEGMENTS, ns)
        made.add(name.lower())
        result.changes.append(f"model {name}: new, from {f.name} on {base}'s skeleton: {len(report.added)} pieces, "
                              f"{report.collision_boxes} collision boxes"
                              + (f", {len(report.edited) + len(report.kept)} of the base's pieces kept from the file"
                                 if report.edited or report.kept else "")
                              + f" ({len(empty.pieces) - len(in_file)} of the base's pieces left out)"
                              + (", without the base's shadow" if dropped_shadow else ""))
        result.warnings += [f"model {name}: {w}" for w in report.warnings]
    return made


def _texture_names(data):
    """Texture hash -> name, from every load list's [texture] entries (the
    game's and the mod's) and the textures the mod added."""
    if getattr(data, "texture_names", None) is None:
        from . import encounters
        names = {}
        LIST = name_hash("list")
        bodies = [data.current(a, r) for a, r in data.records(LIST)]
        bodies += [data.find_added(k, LIST) for k, kind, _ in data.added() if kind == LIST]
        for body in bodies:
            for t in encounters.parse_list(body.decode("latin-1")).get("texture", []):
                names.setdefault(name_hash(t), t)
        data.texture_names = names
    return data.texture_names


def _copy_model(data, name, base, src, spec_doc, where, keep_shadow, result):
    """models/<name>.new.json {"base": <game model>, "copy": true, "textures":
    {<base's texture>: <texture>}}: the base model as it is (pieces, skin,
    skeleton, collision) under a new name, drawing other textures: a new
    character or vehicle that looks its own without changing the base, for
    templates of the mod's own to name (GeometryFile). Each texture named is
    one the game has or the mod adds (textures/<name>.png). Wherever the copy
    is loaded, its textures are too (_load_copied_model_textures)."""
    a, recs = src
    mr = model.renamed(data.archive(a).read(recs[model.MODEL]), name)
    if not keep_shadow and model.has_shadow(mr):
        mr = model.without_shadow(mr)
    sr = data.archive(a).read(recs[model.SEGMENTS])
    swaps = spec_doc.get("textures") or {}
    if not isinstance(swaps, dict):
        raise ValueError(f"{where}: \"textures\" is {{<base's texture>: <texture>}}")
    drawn = model.texture_hashes(sr)
    have = {r.key for _, r in data.records(texture.TEXTURE)} | \
        {k for k, kind, _ in data.added() if kind == texture.TEXTURE}
    names = _texture_names(data)
    mapping = {}
    for old, new in swaps.items():
        old, new = str(old), str(new)
        if name_hash(old) not in drawn:
            raise ValueError(f"{where}: {base} doesn't draw a texture named {old!r} (it draws "
                             f"{', '.join(names.get(h, f'{h:08x}') for h in drawn)})")
        if name_hash(new) not in have:
            raise ValueError(f"{where}: no texture named {new!r} (add textures/{new}.png)")
        mapping[name_hash(old)] = name_hash(new)
        # its normal map, when the game has one for the old texture and the mod one for the new
        if name_hash(old + "_nm") in have and name_hash(new + "_nm") in have:
            mapping[name_hash(old + "_nm")] = name_hash(new + "_nm")
    sr = model.retextured(sr, mapping)
    data.copy_swaps = getattr(data, "copy_swaps", {})
    data.copy_swaps[name.lower()] = mapping
    key = name_hash(name)
    data.new("streamed.dsk", key, model.MODEL, mr)
    data.new("streamed.dsk", key, model.SEGMENTS, sr)
    textures_ = []
    for h in model.texture_hashes(sr):
        n = names.get(h) or next((str(v) for v in swaps.values() if name_hash(str(v)) == h), None)
        if n is None:
            # one the base draws that no list loads by name (or the game hasn't
            # at all): the copy draws it as the base does
            continue
        textures_.append(n)
        nm = n + "_nm"
        if name_hash(nm) in have:
            textures_.append(nm)
    data.copied_models = getattr(data, "copied_models", {})
    data.copied_models[name.lower()] = textures_
    result.changes.append(f"model {name}: a copy of {base}" + (
        ", drawing " + ", ".join(f"{o} as {n}" for o, n in swaps.items()) if swaps else " (its own textures unchanged)"))


def _load_copied_model_textures(data, result):
    """After every step: each load list that loads a copied model (a mod's
    layers, its new maps' lists and the game's) loads the textures it draws."""
    copies = getattr(data, "copied_models", None)
    if not copies:
        return
    from . import encounters
    LIST = name_hash("list")

    def with_textures(text):
        sections = encounters.parse_list(text)
        want = []
        for m in sections.get("model", []):
            for t in copies.get(m.lower(), []):
                if t.lower() not in {x.lower() for x in want}:
                    want.append(t)
        have_t = {t.lower() for t in sections.get("texture", [])}
        add = [t for t in want if t.lower() not in have_t]
        if not add:
            return None
        nl = "\r\n" if "\r\n" in text else "\n"
        if "texture" in sections:
            i = text.lower().index("[texture]") + len("[texture]")
            return text[:i] + nl + "".join(f"load {t}{nl}" for t in add) + text[i:]
        return text.rstrip() + f"{nl}{nl}[texture]{nl}{nl}" + "".join(f"load {t}{nl}" for t in add)
    n = 0
    for key, kind, _ in list(data.added()):
        if kind == LIST:
            new = with_textures(data.find_added(key, LIST).decode("latin-1"))
            if new is not None:
                data.replace_added(key, LIST, new.encode("latin-1"))
                n += 1
    for a, r in data.records(LIST):
        new = with_textures(data.current(a, r).decode("latin-1"))
        if new is not None:
            data.set(a, r, new.encode("latin-1"))
            n += 1
    if n:
        result.changes.append(f"copied models: their textures load with them ({n} load lists)")


def _apply_models(data, folder, result):
    made = _new_models(data, folder, result)
    files = sorted(f for f in (folder / "models").glob("*") if f.suffix.lower() in (".obj", ".glb", ".gltf")
                   and re.sub(r"(?i)\.lod\d+$", "", f.stem).lower() not in made)
    # models/<name>.lod<N>.glb: an edit of the model's level of detail N
    # (exported with --lod N), beside models/<name>.glb for level 0; each
    # builds on the ones before it
    files.sort(key=lambda f: (re.sub(r"(?i)\.lod\d+$", "", f.stem).lower(),
                              int((re.search(r"(?i)\.lod(\d+)$", f.stem) or [0, 0])[1])))
    for f in files:
        name = f.stem
        is_changes = name.lower().endswith(".changes")     # mod pack output
        if is_changes:
            name = name[:-len(".changes")]
        level = re.search(r"(?i)\.lod(\d+)$", name)
        if level:
            name = name[:level.start()]
        is_gltf = f.suffix.lower() != ".obj"
        text = None if is_gltf else f.read_text(encoding="utf-8", errors="replace")
        lod, damaged = (None, None) if is_gltf else _obj_settings(text)
        if level:
            lod = int(level.group(1))
        key = name_hash(name)
        found = {}
        for a in ARCHIVES:
            if not (data.dir / a).exists():
                continue
            recs = {r.kind: r for r in data.archive(a).records if r.key == key and r.kind in (model.MODEL, model.SEGMENTS)}
            if len(recs) == 2:
                found[a] = recs
        copy_swaps = getattr(data, "copy_swaps", {}).get(name.lower())
        if not found and copy_swaps is not None:
            # a copied model (models/<name>.new.json "copy"): edit the copy
            mr, sr = data.find_added(key, model.MODEL), data.find_added(key, model.SEGMENTS)
            if is_changes and is_gltf:
                from . import model_changes
                nm, ns, report = model_changes.import_changes(mr, sr, f)
            elif is_gltf:
                from . import gltf_model
                nm, ns, report = gltf_model.import_gltf(mr, sr, f, lod, damaged,
                                                        material_hash=_glb_textures(data, name, f, result))
            else:
                nm, ns, report = model_import.import_obj(mr, sr, text, lod, damaged)
            # the copy's own textures, on edited pieces that name the base's again
            ns = model.retextured(ns, copy_swaps)
            data.replace_added(key, model.MODEL, nm)
            data.replace_added(key, model.SEGMENTS, ns)
            result.changes.append(f"model {name} (a copy): {len(report.edited)} pieces edited, {len(report.added)} "
                                  f"added, {len(report.removed)} removed, {report.collision_boxes} collision boxes "
                                  f"added (from {f.name})")
            result.warnings += [f"model {name}: {w}" for w in report.warnings]
            continue
        if not found:
            raise ValueError(f"models/{f.name}: no model named {name!r}")
        report = None
        hashes = None
        for a, recs in found.items():
            # as the build has it so far: another level's file may have edited it
            mr = data.peek(a, recs[model.MODEL])
            sr = data.peek(a, recs[model.SEGMENTS])
            if is_changes and is_gltf:
                from . import model_changes
                nm, ns, report = model_changes.import_changes(mr, sr, f)
            elif is_gltf:
                from . import gltf_model
                if hashes is None:
                    hashes = _glb_textures(data, name, f, result)
                nm, ns, report = gltf_model.import_gltf(mr, sr, f, lod, damaged, material_hash=hashes)
            else:
                nm, ns, report = model_import.import_obj(mr, sr, text, lod, damaged)
            data.set(a, recs[model.MODEL], nm)
            data.set(a, recs[model.SEGMENTS], ns)
        result.changes.append(f"model {name}: {len(report.edited)} pieces edited"
                              + (f" ({len(report.retextured)} to another texture)" if report.retextured else "")
                              + f", {len(report.added)} added, "
                              f"{len(report.removed)} removed, {report.collision_boxes} collision boxes added"
                              + (f", {report.collision_boxes_moved} moved with edited pieces" if report.collision_boxes_moved else "")
                              + f" (in {', '.join(found)})")
        result.warnings += [f"model {name}: {w}" for w in report.warnings]


def _apply_collision_flags(data, folder, result):
    """models/<name>.collision.json: {"shapes": {"0": 7, "1": 9}} sets collision
    shape flags (see docs/formats/dsk.md); "crown": true adds a box over a
    tree's branches that only bullets hit (a tree's collision is a thin box
    round its trunk, so shots into the branches pass), and "bullet_boxes":
    [{"centre": [x, y, z], "half": [x, y, z]}] such boxes placed by hand, in
    the model's space. Runs after models/ edits."""
    from . import collision
    for f in sorted((folder / "models").glob("*.collision.json")) if (folder / "models").is_dir() else []:
        name = f.name[:-len(".collision.json")]
        doc = json.loads(f.read_text(encoding="utf-8"))
        flags = {int(k): int(v) for k, v in (doc.get("shapes") or {}).items()}
        hits = [(a, r) for a, r in data.records(model.SEGMENTS) if r.key == name_hash(name)]
        if not hits:
            raise ValueError(f"models/{f.name}: no model named {name!r}")
        notes = []
        if flags:
            for a, r in hits:
                data.set(a, r, collision.set_segments_flags(data.current(a, r), flags))
            notes.append("collision flags " + ", ".join(f"shape {k} = {v}" for k, v in sorted(flags.items())))
        boxes = [([float(v) for v in b["centre"]], [abs(float(v)) for v in b["half"]])
                 for b in doc.get("bullet_boxes") or []]
        if doc.get("crown") or boxes:
            for a, r in hits:
                seg = data.current(a, r)
                mr = next((data.current(ma, mr_) for ma, mr_ in data.records(model.MODEL) if mr_.key == r.key), None)
                if mr is None:
                    raise ValueError(f"models/{f.name}: no model record for {name!r}")
                m = model.decode(mr, seg)
                trunk = next((s for s in m.collision if s.boxes), None)
                joint = (trunk or (m.collision[0] if m.collision else None))
                joint = joint.joint if joint else 0
                these = list(boxes)
                if doc.get("crown"):
                    these.append(collision.crown_box(m.bounds, trunk.boxes[0] if trunk else None))
                data.set(a, r, collision.add_segments_bullet_boxes(seg, joint, these))
            notes.append(f"{len(boxes) + bool(doc.get('crown'))} box(es) only bullets hit"
                         + (" (over its branches)" if doc.get("crown") else ""))
        if notes:
            result.changes.append(f"model {name}: " + "; ".join(notes))


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


def _fx_pairs(body):
    parts = body.split(b"\0")
    return [(parts[i].decode("latin-1"), parts[i + 1].decode("latin-1")) for i in range(0, len(parts) - 1, 2)
            if parts[i]]


def _fx_body(pairs):
    return b"".join(k.encode("latin-1") + b"\0" + v.encode("latin-1") + b"\0" for k, v in pairs)


def _apply_effects(data, folder, result):
    """effects.json {"<entry>": {"<key>": value or null}}: values in the
    game's effects table (assets.dsk "effects": sound and effect sets such as
    a helicopter's littlebird_l, chatter events, impact effects). A string
    goes in the entry's STRG pairs, a number in its FLOT ones; null removes
    the key. E.g. {"littlebird_l": {"rotor": "vh_lbird.air_heli_engine"}}
    gives littlebird_set its high-detail engine at a distance too."""
    from . import ucfb
    f = folder / "effects.json"
    if not f.exists():
        return
    doc = json.loads(f.read_text(encoding="utf-8"))
    hit = [(a, r) for a, r in data.records(name_hash("effects"), ("assets.dsk",)) if r.key == name_hash("effects")]
    if not hit:
        raise ValueError("effects.json: the game data has no effects table")
    a, r = hit[0]
    roots = ucfb.parse(data.current(a, r))
    entries = {}
    for c in roots[0].children:
        info = next((k for k in c.children if k.tag == b"INFO"), None)
        name = dict(_fx_pairs(info.data)).get("name") if info else None
        if name:
            entries[name.lower()] = c
    for entry, values in doc.items():
        if not isinstance(values, dict):
            raise ValueError(f"effects.json: {entry}: give an object of keys and values")
        c = entries.get(entry.lower())
        if values.get("copy") is not None:
            # a new entry: a copy of a game one under this name (a helicopter
            # sound set of a mod's own, for its templates' effectset)
            src = entries.get(str(values["copy"]).lower())
            if c is not None:
                raise ValueError(f"effects.json: the effects table already has an entry {entry!r}")
            if src is None:
                raise ValueError(f"effects.json: {entry}: no entry {values['copy']!r} to copy")
            if not re.fullmatch(r"[A-Za-z0-9_]+", entry):
                raise ValueError(f"effects.json: {entry!r}: name new entries with letters, digits and _")
            c = ucfb.Chunk(src.tag, src.data, [ucfb.Chunk(k.tag, k.data, []) for k in src.children])
            info = next(k for k in c.children if k.tag == b"INFO")
            info.data = _fx_body([(k, entry if k == "name" else v) for k, v in _fx_pairs(info.data)])
            roots[0].children.append(c)
            entries[entry.lower()] = c
            result.changes.append(f"effects {entry}: new, a copy of {values['copy']}")
            values = {k: v for k, v in values.items() if k != "copy"}
        if c is None:
            raise ValueError(f"effects.json: the effects table has no entry {entry!r}")
        for key, value in values.items():
            if any(ch.isspace() or ch == "\0" for ch in key) or key.lower() in ("name", "num_components"):
                raise ValueError(f"effects.json: {entry}: {key!r} can't be set")
            if value is not None and not isinstance(value, (str, int, float)):
                raise ValueError(f"effects.json: {entry}: {key}: a string, a number or null")
            want = None if value is None else (b"STRG" if isinstance(value, str) else b"FLOT")
            text = None if value is None else (value if isinstance(value, str) else f"{float(value):.6f}")
            placed = False
            for tag in (b"STRG", b"FLOT"):
                chunk = next((k for k in c.children if k.tag == tag), None)
                if chunk is None:
                    continue
                pairs = []
                for k, v in _fx_pairs(chunk.data):
                    if k.lower() != key.lower():
                        pairs.append((k, v))
                    elif tag == want:               # changed where it was
                        pairs.append((k, text))
                        placed = True
                chunk.data = _fx_body(pairs)
            if want and not placed:
                chunk = next((k for k in c.children if k.tag == want), None)
                if chunk is None:
                    chunk = ucfb.Chunk(want, b"", [])
                    c.children.append(chunk)
                chunk.data = _fx_body(_fx_pairs(chunk.data) + [(key, text)])
        result.changes.append(f"effects {entry}: " + ", ".join(f"{k} {v}" for k, v in values.items()))

    # always three levels (ucfb > fx3_ > INFO/FLOT/STRG): leaves by their data,
    # whatever parse guessed about them
    body = b"".join(ucfb.pack_chunk(e.tag, b"".join(ucfb.pack_chunk(k.tag, k.data) for k in e.children))
                    for e in roots[0].children)
    data.set(a, r, ucfb.pack_chunk(roots[0].tag, body))


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


def _sky_texts(data, map_name):
    """{"DATAxbox/SW/SW.ini": text, ...}: a map's level config files as the
    build has them so far (another step may have changed them)."""
    from . import sky
    return sky.level_texts(data.dir, map_name, data.files)


def _apply_sky(data, folder, result):
    """sky/<map>.json: the map's base sky and its atmosphere presets (edited
    or new), written into its level config files (loose DATAxbox files)."""
    from . import sky
    for f in sorted((folder / "sky").glob("*.json")) if (folder / "sky").is_dir() else []:
        texts = _sky_texts(data, f.stem)
        if not texts:
            raise ValueError(f"sky/{f.name}: no level named {f.stem!r} (its file is named after the map, e.g. sw.json)")
        doc = json.loads(f.read_text(encoding="utf-8"))
        new, changes = sky.apply(texts, doc, f"sky/{f.name}")
        for path, text in new.items():
            if text != texts[path]:
                data.files[path] = text.encode("latin-1")
        if changes:
            result.changes.append(f"sky {f.stem}: " + "; ".join(changes))


def _sky_textures(texts):
    """The sky textures config files name (SkyTexture and FlatSkyTexture, in base skies and presets)."""
    return {m.group(1).lower() for t in texts.values()
            for m in re.finditer(r"(?im)^\s*(?:Flat)?SkyTexture\s+(\S+)", t)}


def _all_level_skies(data):
    """Every sky texture any level config names (the game's and this build's)."""
    texts = {str(p): p.read_bytes().decode("latin-1") for p in data.dir.rglob("*.ini")}
    texts.update({p: b.decode("latin-1") for p, b in data.files.items() if p.lower().endswith(".ini")})
    return _sky_textures(texts)


def _trim_map_skies(data, folder, result):
    """A new map's list loads the skies its source's and "loads_like" map's
    lists do (blank's: skytexture_peru2 and skytexture_nwwinter, other
    levels' skies, 512 KB each); keep only the sky textures its own config
    names, base sky and presets, as the sky steps left it."""
    from . import encounters
    from .missions import add_to_list
    for name in sorted(getattr(data, "like_skies", set())):
        key, LIST = name_hash(name), name_hash("list")
        body = data.find_added(key, LIST)
        if body is None:
            continue
        used = _sky_textures(_sky_texts(data, name))
        copied = _all_level_skies(data)
        text = body.decode("latin-1")
        have = {t.lower() for k, v in encounters.parse_list(text).items() if k.lower() == "texture" for t in v}
        drop = (copied - used) & have
        lines = [l for l in text.split("\n")
                 if not (l.strip().lower().startswith("load ") and l.strip()[5:].strip().lower() in drop)]
        new = "\n".join(lines)
        data.skies_dropped = getattr(data, "skies_dropped", {})
        gone = data.skies_dropped.setdefault(name, set())
        gone |= drop
        for t in sorted(gone & used):            # dropped for an earlier mod; a later mod's sky uses it
            new = add_to_list(new, "texture", t)
            gone.discard(t)
        if new != text:
            data.replace_added(key, LIST, new.encode("latin-1"))
            if drop:
                result.changes.append(f"map {name}: doesn't load the skies {', '.join(sorted(drop))} "
                                      f"(its own sky doesn't use them)")


# Gaza (2026-11-18), a dense new map: no freezes reported at 13.2 MB of listed
# textures; froze waiting for memory at 18-19 MB and at 24 MB.
MAP_TEXTURE_BUDGET = 14_000_000


def _map_texture_total(data, folder, result):
    """Report each new map's listed texture total, and warn past MAP_TEXTURE_BUDGET."""
    from . import encounters
    maps = sorted(f.stem.lower() for f in (folder / "maps").glob("*.json")) if (folder / "maps").is_dir() else []
    LIST = name_hash("list")
    sizes = None
    for name in maps:
        body = data.find_added(name_hash(name), LIST)
        if body is None:
            continue
        if sizes is None:
            sizes = {}
            for a, r in data.records(texture.TEXTURE):
                if r.key not in sizes:
                    pending = data.replace.get(a, {}).get(r.ident)
                    sizes[r.key] = len(pending) if pending is not None else r.size
            for k, kind, _ in data.added():
                if kind == texture.TEXTURE:
                    sizes[k] = len(data.find_added(k, kind))
        names = {t.lower() for k, v in encounters.parse_list(body.decode("latin-1")).items()
                 if k.lower() == "texture" for t in v}
        total = sum(sizes.get(name_hash(t), 0) for t in names)
        result.changes.append(f"map {name}: its list loads {len(names)} textures, {total / 1e6:.1f} MB")
        if total > MAP_TEXTURE_BUDGET:
            result.warnings.append(
                f"maps/{name}.json: its list loads {total / 1e6:.1f} MB of textures. The Gaza map froze "
                f"waiting for memory at 18 MB and more and ran at 13 MB; keep a dense map under "
                f"{MAP_TEXTURE_BUDGET / 1e6:.0f} MB (smaller skins on vehicles seen only from afar)")


def _apply_configs(data, folder, result):
    """config/<file>.json: lines of the game's root config files (xbox.ini,
    rs.ini, vehicles.ini, msh2temp.ini; see configs.py), written whole as
    loose files beside DATAxbox, which the launcher puts in place of the game's."""
    from . import configs
    cdir = folder / "config"
    for f in sorted(cdir.glob("*.json")) if cdir.is_dir() else []:
        name = configs.file_name(f.name[:-len(".json")])
        if name is None:
            raise ValueError(f"config/{f.name}: name it after one of the game's root config files "
                             f"({', '.join(c + '.json' for c in configs.ROOT_CONFIGS)})")
        if name in data.files:
            text = data.files[name].decode("latin-1")
        elif (data.dir.parent / name).exists():
            text = (data.dir.parent / name).read_bytes().decode("latin-1")
        else:
            raise ValueError(f"config/{f.name}: the game has no {name} beside DATAxbox")
        new, changes = configs.apply(text, json.loads(f.read_text(encoding="utf-8")), f"config/{f.name}")
        data.files[name] = new.encode("latin-1")
        result.changes.append(f"config {name}: " + "; ".join(changes))


def game_strings(data):
    """Every English string as the build has it so far: {key: text}."""
    out = {}
    for name, rec in data.records(REGISTRY, ("english.dsk",)):
        try:
            out.update(registry.Registry(data.current(name, rec)).strings())
        except Exception:
            continue
    return out


def _apply_factions(data, folder, result):
    """factions.json: a faction's new names in the game's text (see factions.py)."""
    from . import factions
    f = folder / "factions.json"
    if not f.exists():
        return
    doc = json.loads(f.read_text(encoding="utf-8"))
    seen_swaps = set()
    data.swapped_models = getattr(data, "swapped_models", {})
    edits = factions.plan(game_strings(data), factions._names_only(doc))
    if edits and doc.get("map"):
        # the new names on that map only (shown there with Foundry Console)
        n = _map_strings(data, str(doc["map"]), edits, "factions.json")
        result.changes.append(f"factions: {n} strings renamed on {doc['map']} only")
    elif edits:
        changed, _ = _set_strings(data, edits)
        result.changes.append(f"factions: {changed} strings renamed")
    for faction, old, new in factions.looks(doc):
        loaded = _swap_model(data, old, new)
        extra = []
        # a vehicle: its working joints, and its ruin and wheel go with it
        from . import model as _model
        try:
            jo = [j.name for j in _model.decode(*_model_bodies(data, old, before=True)).joints]
            jn = [j.name for j in _model.decode(*_model_bodies(data, new, before=True)).joints]
            gone = factions.missing_joints(jo, jn)
        except (KeyError, ValueError):
            gone = []
        if gone:
            result.warnings.append(f"factions.json: {faction}: {new} lacks {old}'s {', '.join(gone)} (its template's "
                                   f"seats, wheels or guns there have nothing to attach to)")
        for prop in ("geometrynameruined", "geometrynamewheel"):
            a, b = _template_prop(data, old, prop), _template_prop(data, new, prop)
            if a and b and a.lower() != b.lower() and a.lower() not in seen_swaps:
                seen_swaps.add(a.lower())
                _swap_model(data, a, b)
                extra.append(f"{a} as {b}")
        result.changes.append(f"factions: {faction}'s {old} looks like {new}"
                              + (f" (its textures load with {loaded} lists)" if loaded else "")
                              + (f"; with it {', '.join(extra)}" if extra else ""))


def _template_prop(data, geometry, prop):
    """A property of the first template that draws a model (its ruin model,
    its wheel model), as the game has it."""
    from . import find
    if getattr(data, "_templates_by_geometry", None) is None:
        archives = [data.archive(n) for n in ARCHIVES if (data.dir / n).exists()]
        index = {}
        for o in find.objects(archives):
            if not o["name"].lower().startswith("template_"):
                continue
            props = {k.lower(): v for k, v in o["properties"]}
            g = str(props.get("geometryfile") or o["model"] or "").lower()
            if g:
                index.setdefault(g, props)
        data._templates_by_geometry = index
    return (data._templates_by_geometry.get(geometry.lower()) or {}).get(prop)


def _model_bodies(data, name, before=False):
    """(model, segments) bodies of a model; before: as the game had it,
    before this build's swaps (a model that stands in for another may
    itself have been swapped)."""
    from . import model
    if before and name.lower() in getattr(data, "swapped_models", {}):
        return data.swapped_models[name.lower()]
    recs = _model_records(data, name)
    if model.MODEL not in recs or model.SEGMENTS not in recs:
        raise KeyError(name)
    return recs[model.MODEL][1], recs[model.SEGMENTS][1]


def _model_records(data, name):
    """{kind: (archive, record) or None (added by a mod) -> body} for a model's
    MODEL and SEGMENTS records, as the build has them now."""
    from . import model
    out = {}
    for kind in (model.MODEL, model.SEGMENTS):
        hit = [(a, r) for a, r in data.records(kind) if r.key == name_hash(name)]
        if hit:
            out[kind] = (hit[0], data.current(*hit[0]))
        else:
            body = data.find_added(name_hash(name), kind)
            if body is not None:
                out[kind] = (None, body)
    return out


def _swap_model(data, old, new):
    """The model old drawn with new's geometry (and collision): old's records
    get new's under old's name, and every load list that loads old loads
    new's textures. Returns how many lists were given textures."""
    from . import encounters, model
    from .missions import add_to_list
    olds, news = _model_records(data, old), _model_records(data, new)
    for kind in (model.MODEL, model.SEGMENTS):
        if kind not in olds:
            raise ValueError(f"factions.json: no model named {old!r} (looks)")
        if kind not in news:
            raise ValueError(f"factions.json: no model named {new!r} to look like (looks)")
    data.swapped_models = getattr(data, "swapped_models", {})
    data.swapped_models.setdefault(old.lower(), (olds[model.MODEL][1], olds[model.SEGMENTS][1]))
    # new as the game had it, if this build swapped it already
    if new.lower() in data.swapped_models:
        news = {model.MODEL: (None, model.renamed(data.swapped_models[new.lower()][0], new)),
                model.SEGMENTS: (None, data.swapped_models[new.lower()][1])}
    for kind in (model.MODEL, model.SEGMENTS):
        body = news[kind][1]
        if kind == model.MODEL:
            body = model.renamed(body, old)
        where = olds[kind][0]
        if where is None:
            data.replace_added(name_hash(old), kind, body)
        else:
            data.set(where[0], where[1], body)
    # new's textures: the names (from the load lists) of the hashes its pieces draw
    m = model.decode(news[model.MODEL][1], news[model.SEGMENTS][1])
    hashes = set()
    for p in m.pieces:
        hashes.add(p.texture)
        hashes.update(w for w in p.material if w)
    LIST = name_hash("list")
    lists = [(a, r, data.current(a, r).decode("latin-1")) for a, r in data.records(LIST)]
    added = [(k, None, b.decode("latin-1")) for k, kind, _ in data.added() if kind == LIST
             for b in [data.find_added(k, LIST)]]
    names = {}
    for _, _, text in lists + added:
        for t in encounters.parse_list(text).get("texture", []):
            if name_hash(t) in hashes or (t.lower().endswith("_nm") and name_hash(t[:-3]) in hashes):
                names.setdefault(t.lower(), t)
    textures = sorted(names.values(), key=str.lower)
    n = 0
    for a, r, text in lists + added:
        if old.lower() not in {x.lower() for x in encounters.parse_list(text).get("model", [])}:
            continue
        new_text = text
        for t in textures:
            new_text = add_to_list(new_text, "texture", t)
        if new_text != text:
            n += 1
            if r is None:
                data.replace_added(a, LIST, new_text.encode("latin-1"))
            else:
                data.set(a, r, new_text.encode("latin-1"))
    return n


STEPS = (_apply_text, _apply_deck, _apply_scripts, _apply_objects, _apply_encounters, _apply_missions,
         _apply_textures, _apply_models, _apply_collision_flags, _apply_layout, _apply_game_squads,
         _apply_animations, _apply_animtables, _apply_bone_moves, _apply_maps, _apply_terrain, _apply_sounds, _apply_effects, _apply_sky,
         _trim_map_skies, _map_texture_total,
         _apply_factions, _apply_configs)


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
    # the mods' new maps (maps/<name>.json), for layers that load with them
    data.new_maps = {f.stem.lower() for p in projects if (p / "maps").is_dir() for f in (p / "maps").glob("*.json")}
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
            insertions = getattr(data, "hq_insertions", [])
            if contracts or insertions:
                _apply_contracts(data, contracts, result, insertions)
            if insertions:
                _apply_hq_sequence(data, insertions, result)
            for project, pname in zip(projects, mod_names):
                _apply_welcome_mail(data, project, pname, result)
            if getattr(data, "map_layers", None) or getattr(data, "travel_arrive", False) \
                    or getattr(data, "welcome", None) or getattr(data, "decks", None):
                _apply_map_layers(data, result)
    # after every step: the Lua check (the maps step writes new maps' master
    # scripts after the missions step), and a new map's step lists the
    # scripts it runs
    _load_copied_model_textures(data, result)
    _write_map_text(data, result)
    _write_map_templates(data, result)
    _check_lua(data, result)
    _check_new_scripts(data, result)
    _check_map_paths(data, result)
    _check_model_sizes(data, result)
    _check_models_exist(data, result)
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
