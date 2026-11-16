"""The editor's Animations panel: the game's animation tables (which clip a
character plays for each handle the game asks for), the models that use
each, the clips there are, a clip on a model for the 3D preview, and the
mod's changes (animtables/<table>.changes.json, or the whole table when the
mod replaces it with animtables/<table>.json)."""
import json

from .. import animation, animtable, find, gltf, model

INFO_KEYS = ("meaning",)        # read-out keys the tables carry for reading, not data


def _game_tables(scene):
    if getattr(scene, "_anim_tables", None) is None:
        out = {}
        for a in scene.archives:
            for r in a.records:
                if r.kind == animtable.ANIMTABLE:
                    doc = animtable.to_json(a.read(r))
                    out.setdefault(doc["name"].lower(), doc)
        scene._anim_tables = out
    return scene._anim_tables


def _users(scene):
    """{table (lower case): sorted models drawn by characters that use it}."""
    if getattr(scene, "_anim_users", None) is None:
        users = {}
        for o in find.objects(scene.archives):
            props = {k.lower(): v for k, v in o["properties"]}
            t = str(props.get("animationtable") or "").lower()
            m = str(props.get("geometryfile") or o.get("model") or "")
            if t and m:
                users.setdefault(t, set()).add(m.lower())
        scene._anim_users = {t: sorted(ms) for t, ms in users.items()}
    return scene._anim_users


def tables(scene):
    """Every table, with the models of the characters that use it."""
    users = _users(scene)
    mine = _mod_files(scene)
    return [{"name": doc["name"], "fallback": doc.get("fallback"), "models": users.get(key, []),
             "changed": key in mine}
            for key, doc in sorted(_game_tables(scene).items())]


def clip_names(scene):
    """Every clip in the game, by name (for picking another)."""
    if getattr(scene, "_clip_names", None) is None:
        names = set()
        for a in scene.archives:
            with a.path.open("rb") as f:
                for r in a.records:
                    if r.kind == animation.ANIMATION:
                        f.seek(r.offset)
                        head = f.read(min(r.size, 160))
                        # ucfb > anim > NAME first (all 1,835 retail clips): read just that
                        if head[:4] == b"ucfb" and head[8:12] == b"anim" and head[16:20] == b"NAME":
                            size = int.from_bytes(head[20:24], "little")
                            n = head[24:24 + size].split(b"\0", 1)[0].decode("latin-1")
                            if n:
                                names.add(n)
        scene._clip_names = sorted(names, key=str.lower)
    return scene._clip_names


def _mod_files(scene):
    d = scene.mod / "animtables"
    out = set()
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        out.add(p.name[:-len(".changes.json")].lower() if p.name.endswith(".changes.json") else p.stem.lower())
    return out


def _find_table(scene, name):
    doc = _game_tables(scene).get(str(name).lower())
    if doc is None:
        raise KeyError(f"no animation table {name!r}")
    return doc


def _paths(scene, doc):
    d = scene.mod / "animtables"
    return d / f"{doc['name']}.json", d / f"{doc['name']}.changes.json"


def table(scene, name):
    """The game's table, the mod's version of it, and the models using it."""
    from .. import modpack
    game = _find_table(scene, name)
    whole, changes = _paths(scene, game)
    if whole.exists():
        mod = json.loads(whole.read_text(encoding="utf-8"))
        how = f"animtables/{whole.name} (the whole table)"
    elif changes.exists():
        mod = modpack.apply_animtable_changes(game, json.loads(changes.read_text(encoding="utf-8")))
        how = f"animtables/{changes.name}"
    else:
        mod, how = game, None
    flags = {f"0x{bit:X}": list(v) for bit, v in animtable.FLAGS.items()}
    return {"name": game["name"], "game": game, "table": _with_meaning(mod), "file": how,
            "models": _users(scene).get(game["name"].lower(), []), "flags": flags}


def _with_meaning(doc):
    doc = json.loads(json.dumps(doc))
    for s in doc.get("sets", []):
        for e in s["entries"]:
            e.pop("meaning", None)
            if int(str(e.get("flags", "0")), 0):
                e["meaning"] = animtable.flag_words(e["flags"])
    return doc


def _entry_key(e):
    """An entry as data (its read-out dropped, flags as a number)."""
    out = {k: v for k, v in e.items() if k not in INFO_KEYS}
    out["flags"] = int(str(out.get("flags", "0")), 0)
    out["priority"] = int(out.get("priority", 40))
    return json.dumps(out, sort_keys=True)


def save_table(scene, name, doc):
    """The mod's version of a table: written as the entries that differ from
    the game's (animtables/<table>.changes.json), or the whole table when the
    mod already replaces it (animtables/<table>.json). Nothing left to change
    removes the file."""
    game = _find_table(scene, name)
    whole, changes_path = _paths(scene, game)
    clean = json.loads(json.dumps(doc))
    clean["name"] = game["name"]
    for s in clean.get("sets", []):
        for e in s["entries"]:
            for k in INFO_KEYS:
                e.pop(k, None)
            if "flags" in e:
                e["flags"] = f"0x{int(str(e['flags']), 0):X}"
    animtable.from_json(clean)                  # checks it (variants, priorities)
    whole.parent.mkdir(parents=True, exist_ok=True)
    if whole.exists():
        whole.write_text(json.dumps(clean, indent=1) + "\n", encoding="utf-8")
        return {"file": f"animtables/{whole.name}", "changed": None}
    old = {(s["name"], e["handle"]): e for s in game["sets"] for e in s["entries"]}
    new = {(s["name"], e["handle"]): e for s in clean["sets"] for e in s["entries"]}
    entries = []
    for (set_name, handle), e in new.items():
        if (set_name, handle) not in old or _entry_key(old[(set_name, handle)]) != _entry_key(e):
            entries.append({"set": set_name, **{k: v for k, v in e.items() if k not in INFO_KEYS}})
    for (set_name, handle) in old:
        if (set_name, handle) not in new:
            entries.append({"set": set_name, "handle": handle, "remove": True})
    out = {"entries": entries}
    for key in ("variants", "fallback"):
        if clean.get(key) != game.get(key):
            out[key] = clean.get(key)
    if not entries and len(out) == 1:
        if changes_path.exists():
            changes_path.unlink()
        return {"file": None, "changed": 0}
    changes_path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    return {"file": f"animtables/{changes_path.name}", "changed": len(entries)}


def clip_glb(scene, model_name, clip):
    """A model (no textures) carrying one clip, in place, for the preview: the
    page plays its animation on the textured model it already has."""
    mr, sr = model.find(scene.archives, model_name)
    m = model.decode(mr, sr)
    anim = animation.decode(animation.find(scene.archives, clip))
    return gltf.export(m, 0, False, {}, lambda k: f"{k:08X}", [anim], True)
