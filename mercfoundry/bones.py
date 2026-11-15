"""Moved skin bones on characters: animations of their own.

Every retail clip stores a translation for each joint it moves (SXLT fixed,
or DXLT per frame), and the clips are shared: all 88 skinned skeletons have
the same bone lengths (to 0.3 mm), so the game never needed the skeleton's.
A character whose bones a mod moved (a longer leg, a higher hip) would be
pulled back to the shared lengths by every clip. So the build gives it
clips of its own: copies of the clips its animation table (and the tables
that one falls back to) names, with each moved joint's translation shifted
by the move, a copy of each table naming them, and the model's characters
(world objects drawing it, templates included) set to that table.
"""
import math

from . import animation, animtable
from .hashing import name_hash

TOLERANCE = 1e-3     # metres: smaller differences aren't a move (retail skeletons differ by up to 0.3 mm)


def moved_joints(old_joints, new_joints):
    """{joint name (lower case): (dx, dy, dz)} for joints whose translation
    changed (joints added by the mod aren't in any clip)."""
    old = {j.name.lower(): j.translation for j in old_joints}
    out = {}
    for j in new_joints:
        t = old.get(j.name.lower())
        if t is not None and math.dist(t, j.translation) > TOLERANCE:
            out[j.name.lower()] = tuple(b - a for a, b in zip(t, j.translation))
    return out


def suffix_name(name, model_name):
    return f"{name}_{model_name}".lower()


def retarget(anim, moves, new_name):
    """A copy of a clip with each moved joint's translation shifted by its
    move, or None when the clip moves none of those joints."""
    touched = False
    for t in anim.tracks:
        d = moves.get(t.joint.lower())
        if not d:
            continue
        if t.translations:
            t.translations = [tuple(v + dv for v, dv in zip(p, d)) for p in t.translations]
        else:
            t.translation = tuple(v + dv for v, dv in zip(t.translation or (0.0, 0.0, 0.0), d))
        touched = True
    if not touched:
        return None
    # fixed translations are int16 x the clip's scale (units per metre),
    # set by the original's longest: a longer bone may need a coarser one
    peak = max((abs(v) for t in anim.tracks if not t.translations and t.translation for v in t.translation),
               default=0.0)
    if anim.scale and peak * anim.scale > 32767:
        anim.scale = 32767 / peak * 0.999
    anim.name = new_name
    return anim


def rename_clips(doc, names, table_names):
    """A table doc with clips (and its fallback table) renamed: names maps old
    clip names (lower case) to new ones, table_names old tables to new."""
    out = dict(doc)
    out["name"] = table_names[doc["name"].lower()]
    if doc.get("fallback") and doc["fallback"].lower() in table_names:
        out["fallback"] = table_names[doc["fallback"].lower()]
    out["variants"] = [names.get(v.lower(), v) for v in doc.get("variants", [])]
    out["sets"] = []
    for s in doc.get("sets", []):
        entries = []
        for e in s["entries"]:
            e = dict(e)
            if "anim" in e:
                e["anim"] = names.get(e["anim"].lower(), e["anim"])
            entries.append(e)
        out["sets"].append(dict(s, entries=entries))
    return out


def table_chain(tables, name):
    """The table and those it falls back to, in order (tables: lower name ->
    (archive, record, doc))."""
    chain = []
    while name and name.lower() in tables and name.lower() not in chain:
        chain.append(name.lower())
        name = tables[name.lower()][2].get("fallback")
    return chain


def load_beside(data, pairs):
    """Add "load <new>" after each "load <old>" in every load list (pairs:
    old name (lower case) -> new names). Returns how many lists changed."""
    from . import aidata
    changed = 0
    for la, lr in data.records(aidata.LIST):
        text = data.current(la, lr).decode("latin-1")
        lines = text.split("\n")
        have = {ln.strip()[5:].strip().lower() for ln in lines if ln.strip().lower().startswith("load ")}
        if not have & set(pairs):
            continue
        out, added = [], False
        for ln in lines:
            out.append(ln)
            s = ln.strip()
            if s.lower().startswith("load "):
                for new in pairs.get(s[5:].strip().lower(), ()):
                    if new.lower() not in have:
                        cr = "\r" if ln.endswith("\r") else ""
                        out.append(ln[:len(ln) - len(ln.lstrip())] + f"load {new}" + cr)
                        have.add(new.lower())
                        added = True
        if added:
            data.set(la, lr, "\n".join(out).encode("latin-1"))
            changed += 1
    return changed


def apply(data, models, result):
    """models: {model name: {joint: move}} for the models this build moved
    bones of. Adds the clips and tables, and points the model's characters
    at them."""
    from . import world
    from .modbuild import WORLD, _apply_rule
    if not models:
        return
    tables = {}
    for a, r in data.records(animtable.ANIMTABLE):
        doc = animtable.to_json(data.current(a, r))
        tables.setdefault(doc["name"].lower(), (a, r, doc))
    clips = {r.key: (a, r) for a, r in data.records(animation.ANIMATION)}
    # which table each model's characters use
    uses = {m.lower(): set() for m in models}
    for a, r in data.records(WORLD):
        for o in world.iter_objects(data.current(a, r)):
            m = (o["model"] or "").lower()
            props = {k.lower(): v for k, v in o["properties"]}
            m = str(props.get("geometryfile") or m).lower()
            if m in uses and props.get("animationtable"):
                uses[m].add(props["animationtable"].lower())
    for model_name, moves in models.items():
        key = model_name.lower()
        used = sorted(t for t in uses[key] if t in tables)
        if not used:
            result.warnings.append(f"model {model_name}: moved bones ({', '.join(sorted(moves)[:4])}), but no "
                                   "character drawing it names an animation table; its clips will pull the "
                                   "bones back")
            continue
        chain = []
        for t in used:
            chain += [c for c in table_chain(tables, t) if c not in chain]
        table_names = {t: suffix_name(t, key) for t in chain}
        names, loads, made, missing = {}, {}, 0, set()
        wanted = set()
        for t in chain:
            wanted |= {c.lower() for c in animtable.clips(tables[t][2])}
        for clip in sorted(wanted):
            hit = clips.get(name_hash(clip))
            if not hit:
                missing.add(clip)
                continue
            a, r = hit
            base_rec = data.current(a, r)
            new_name = suffix_name(clip, key)
            anim = retarget(animation.decode(base_rec), moves, new_name)
            if anim is None:
                continue
            data.new(a, name_hash(new_name), animation.ANIMATION, animation.encode(anim, base_rec))
            names[clip] = new_name
            loads.setdefault(clip, []).append(new_name)
            made += 1
        for t in chain:
            a, r, doc = tables[t]
            data.new(a, name_hash(table_names[t]), animtable.ANIMTABLE,
                     animtable.from_json(rename_clips(doc, names, table_names)))
            loads.setdefault(t, []).append(table_names[t])
        lists = load_beside(data, loads)
        # its characters use the new tables
        set_on = 0
        for a, r in data.records(WORLD):
            doc = None
            for o in world.iter_objects(data.current(a, r)):
                props = {k.lower(): v for k, v in o["properties"]}
                if str(props.get("geometryfile") or o["model"] or "").lower() == key and \
                        (props.get("animationtable") or "").lower() in table_names:
                    doc = world.to_json(data.current(a, r))
                    break
            if doc is None:
                continue
            for c in doc["chunks"]:
                if c.get("tag") != "inst" or "properties" not in c:
                    continue
                props = {k.lower(): v for k, v in c["properties"]}
                if str(props.get("geometryfile") or c.get("model") or "").lower() != key:
                    continue
                t = (props.get("animationtable") or "").lower()
                if t in table_names:
                    _apply_rule(c, {"set": {"AnimationTable": table_names[t]}})
                    set_on += 1
            data.set(a, r, world.from_json(doc))
        result.changes.append(f"model {model_name}: moved bones ({', '.join(sorted(moves)[:4])}"
                              f"{' ...' if len(moves) > 4 else ''}) get clips of their own: {made} clips and "
                              f"{len(chain)} animation tables ({', '.join(table_names[t] for t in chain)}), "
                              f"added to {lists} load lists; {set_on} characters use them")
        if missing:
            result.warnings.append(f"model {model_name}: {len(missing)} clips its tables name aren't in the game "
                                   f"(left as they are): {', '.join(sorted(missing)[:4])}")
