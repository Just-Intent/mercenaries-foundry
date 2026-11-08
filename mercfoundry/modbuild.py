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
    models/<name>.obj     an edited export from the `model` command

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
- models/<name>.obj: an edited export from `python -m mercfoundry model`.

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


def new_project(folder, name=None):
    folder = Path(folder)
    name = name or folder.resolve().name
    for sub in ("text", "scripts", "objects", "textures", "models"):
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


def _obj_settings(text):
    first = next((l for l in text.splitlines() if l.startswith("#")), "")
    m = re.search(r"level of detail (\d+)", first)
    return (int(m.group(1)) if m else 0), ("damaged" in first)


def _apply_models(data, folder, result):
    for f in sorted((folder / "models").glob("*.obj")):
        name = f.stem
        text = f.read_text(encoding="utf-8", errors="replace")
        lod, damaged = _obj_settings(text)
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
