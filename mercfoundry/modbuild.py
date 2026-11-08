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
    textures/<name>.png   replaces the texture <name>, or adds it if it's new
    models/<name>.obj|.glb|.gltf  an edited export from the `model` command
    animations/*.glb|.gltf   clips replace the game animations with the same
                          names (export them with `model --glb --anim`)
    animations/new.json   {"new clip": "template clip"}: clips with these
                          names are added as new animations, built on the
                          template's skeleton and loaded wherever it is
    terrain/<name>.heights.png  replaces a terrain's heights (from the
                          `terrain` command; keep the .heights.json beside it)
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

Get the originals to edit with `extract --decode`, `texture` and `model`.
"""


@dataclass
class Result:
    written: list = field(default_factory=list)      # archive file names
    changes: list = field(default_factory=list)      # human-readable lines
    warnings: list = field(default_factory=list)


class _Data:
    """Original archives, opened on demand, with pending changes."""

    def __init__(self, data_dir):
        self.dir = Path(data_dir)
        self.archives = {}
        self.replace = {}   # archive name -> {(key, kind): bytes}
        self.add = {}       # archive name -> [(key, kind, bytes)]

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
        pending = self.replace.get(name, {}).get(rec.ident)
        return pending if pending is not None else self.archive(name).read(rec)

    def set(self, name, rec, data):
        if data != self.archive(name).read(rec):
            self.replace.setdefault(name, {})[rec.ident] = data
        else:
            self.replace.get(name, {}).pop(rec.ident, None)

    def new(self, name, key, kind, data):
        self.add.setdefault(name, []).append((key, kind, data))

    def added(self):
        """(key, kind, archive name) of every record added so far."""
        return [(k, kind, a) for a, recs in self.add.items() for k, kind, _ in recs]


def new_project(folder, name=None):
    folder = Path(folder)
    name = name or folder.resolve().name
    for sub in ("text", "scripts", "objects", "textures", "models", "layout", "animations", "animtables", "terrain"):
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
    result.changes.append(f"text: {changed} strings changed, {added} entries added")


def _apply_scripts(data, folder, result):
    for f in sorted((folder / "scripts").glob("*.lua")):
        name = f.stem
        source = f.read_bytes().decode("latin-1")
        key = name_hash(name)
        hit = [(a, r) for a, r in data.records(SCRIPT) if r.key == key]
        if hit:
            for a, r in hit:
                old = script.decode(data.current(a, r))
                text = source if b"\r\n" in old.text else source.replace("\r\n", "\n")
                data.set(a, r, script.encode(script.with_source(old, text)))
            result.changes.append(f"script {name}: replaced")
        else:
            data.new("assets.dsk", key, SCRIPT, script.encode(script.new(name, source.replace("\r\n", "\n"))))
            result.changes.append(f"script {name}: added")


ACTIONS = ("set", "move", "position", "turn", "copy")


def _rules(folder):
    for f in sorted((folder / "objects").glob("*.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        for rule in doc if isinstance(doc, list) else [doc]:
            if not isinstance(rule, dict) or not any(k in rule for k in ACTIONS):
                raise ValueError(f"{f.name}: each rule needs one of {', '.join(ACTIONS)}")
            if "set" in rule and not isinstance(rule["set"], dict):
                raise ValueError(f"{f.name}: \"set\" must be an object of property -> value")
            if not (rule.get("name") or rule.get("template") or rule.get("model") or rule.get("near")):
                raise ValueError(f"{f.name}: each rule needs a \"name\", \"model\" or \"near\" to match")
            if "near" in rule and (not isinstance(rule["near"], list) or len(rule["near"]) != 3):
                raise ValueError(f"{f.name}: \"near\" must be [x, y, z]")
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


def _apply_objects(data, folder, result):
    rules = list(_rules(folder))
    if not rules:
        return
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
                if rule.get("copy"):
                    twin = copy.deepcopy(c)
                    twin["template"] = rule["as"]
                    _apply_rule(twin, rule)
                    chunks.insert(k, twin)
                    k += 1
                else:
                    _apply_rule(c, rule)
        if doc is not None:
            data.set(a, r, world.from_json(doc))
    for (fname, rule), n in zip(rules, hits):
        what = rule.get("name") or rule.get("template") or rule.get("model") or f"object near {rule['near']}"
        if not n:
            raise ValueError(f"{fname}: no object matches {what!r}" +
                             (f" in layer {rule['layer']!r}" if rule.get("layer") else ""))
        verb = f"copied as {rule['as']!r}" if rule.get("copy") else "updated"
        result.changes.append(f"objects {what}: {n} instances {verb}")


def _apply_textures(data, folder, result):
    for f in sorted((folder / "textures").glob("*.png")):
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
    for f in sorted((folder / "animtables").glob("*.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        name = doc.get("name") or f.stem
        key = name_hash(name)
        hit = [(a, r) for a, r in data.records(animtable.ANIMTABLE) if r.key == key]
        if not hit:
            raise ValueError(f"animtables/{f.name}: no animation table named {name!r}")
        new = animtable.from_json(doc)
        norm = animtable.to_json(new)
        for a, r in hit:
            old = animtable.to_json(data.current(a, r))
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
    from . import terrain
    for f in sorted((folder / "terrain").glob("*.heights.png")):
        name = f.name[:-len(".heights.png")]
        meta_path = f.with_name(name + ".heights.json")
        if not meta_path.exists():
            raise ValueError(f"terrain/{f.name}: {meta_path.name} is missing (export both with the terrain command)")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        key = name_hash(name)
        hit = [(a, r) for a, r in data.records(name_hash("Terrain")) if r.key == key]
        if not hit:
            raise ValueError(f"terrain/{f.name}: no terrain named {name!r}")
        for a, r in hit:
            record = data.current(a, r)
            chunks = terrain.tern_chunks(record)
            units = terrain.units_from_png(f.read_bytes(), meta)
            _, old, _ = terrain.decode_height_units(chunks)
            changed = sum(1 for z in range(len(units)) for x in range(len(units)) if units[z][x] != old[z][x])
            data.set(a, r, terrain.rebuild(record, terrain.encode_height_units(chunks, units)))
        result.changes.append(f"terrain {name}: {changed} height samples changed")


LAYOUT_KINDS = ("path", "lineregion", "region", "encounter", "list")


def _apply_layout(data, folder, result):
    from . import aidata
    files = sorted(list((folder / "layout").glob("*.json")) + list((folder / "layout").glob("*.list.txt")))
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
            if is_gltf:
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


def build(project, data_dir, out_dir) -> Result:
    project, out = Path(project), Path(out_dir)
    if not project.is_dir():
        raise FileNotFoundError(f"{project} is not a folder")
    data = _Data(data_dir)
    result = Result()
    _apply_text(data, project, result)
    _apply_scripts(data, project, result)
    _apply_objects(data, project, result)
    _apply_textures(data, project, result)
    _apply_models(data, project, result)
    _apply_layout(data, project, result)
    _apply_animations(data, project, result)
    _apply_animtables(data, project, result)
    _apply_terrain(data, project, result)
    names = sorted(set(data.replace) | set(data.add))
    names = [n for n in names if data.replace.get(n) or data.add.get(n)]
    out.mkdir(parents=True, exist_ok=True)
    # Fail before writing anything if the game has the files open.
    for n in names:
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
    # Archives written by an earlier build but no longer changed would
    # silently override the game; remove them.
    for n in ARCHIVES:
        if n not in names and (out / n).exists() and (out / "foundry-mod.json").exists():
            (out / n).unlink()
    meta = {}
    if (project / "mod.json").exists():
        meta = json.loads((project / "mod.json").read_text(encoding="utf-8"))
    (out / "foundry-mod.json").write_text(json.dumps({
        "name": meta.get("name", project.resolve().name),
        "description": meta.get("description", ""),
        "built_by": "mercfoundry",
        "archives": result.written,
        "changes": result.changes,
    }, indent=1) + "\n", encoding="utf-8")
    return result
