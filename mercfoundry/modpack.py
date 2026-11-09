"""Changes-only files, so mods can be shared without game content.

A mod made from exported game files (an edited copy of a script, the map's
heightmap, a repainted texture) would carry the game's own content. `mod pack`
rewrites such files as just the author's changes, and the build applies them
to the player's own game data:

    scripts/<name>.lua.diff            unified diff against the game's script
    terrain/<map>.heights.changes.json changed height samples only
    terrain/<map>.layerNN.<texture>.changes.png
                                       grey + alpha: alpha 255 marks changed
                                       pixels, the grey value is the new opacity
    textures/<name>.changes.png + <name>.mask.png
                                       new pixels where the mask is white
"""
import difflib
import json
import re

# ---------------------------------------------------------------- text diffs


def _lines(text):
    return text.splitlines(keepends=True)


def make_diff(old: str, new: str, name: str) -> str:
    """A unified diff (3 lines of context) that apply_diff can apply. A last
    line without a newline is marked as in diff(1)."""
    a, b = _lines(old), _lines(new)
    out = [f"--- a/{name}\n", f"+++ b/{name}\n"]
    for group in difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(3):
        i1, i2, j1, j2 = group[0][1], group[-1][2], group[0][3], group[-1][4]
        out.append(f"@@ -{i1 + 1},{i2 - i1} +{j1 + 1},{j2 - j1} @@\n")
        for tag, a1, a2, b1, b2 in group:
            rows = []
            if tag == "equal":
                rows = [(" ", line) for line in a[a1:a2]]
            else:
                rows = [("-", line) for line in a[a1:a2]] + [("+", line) for line in b[b1:b2]]
            for mark, line in rows:
                if line.endswith("\n"):
                    out.append(mark + line)
                else:
                    out.append(mark + line + "\n\\ No newline at end of file\n")
    return "".join(out) if len(out) > 2 else ""


_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def apply_diff(old: str, diff: str) -> str:
    """Apply a unified diff to old. Hunks may have moved by a few lines (the
    context is searched for); raises ValueError if one doesn't match."""
    src = _lines(old)
    rows = diff.splitlines(keepends=True)
    hunks, i = [], 0
    while i < len(rows):
        m = _HUNK.match(rows[i])
        i += 1
        if not m:
            continue
        start = int(m.group(1))
        old_n = int(m.group(2)) if m.group(2) is not None else 1
        new_n = int(m.group(4)) if m.group(4) is not None else 1
        before, after, mark = [], [], None
        while i < len(rows) and (len(before) < old_n or len(after) < new_n or rows[i].startswith("\\")):
            row = rows[i]
            i += 1
            if row.startswith("\\"):
                # "\ No newline at end of file": the previous line has none.
                if mark in (" ", "-") and before and before[-1].endswith("\n"):
                    before[-1] = before[-1][:-1]
                if mark in (" ", "+") and after and after[-1].endswith("\n"):
                    after[-1] = after[-1][:-1]
                continue
            mark, line = row[:1], row[1:]
            if mark not in (" ", "-", "+"):
                raise ValueError(f"unexpected line in the diff: {row[:40]!r}")
            if mark in (" ", "-"):
                before.append(line)
            if mark in (" ", "+"):
                after.append(line)
        hunks.append((start, before, after))
    out, pos = [], 0
    for start, before, after in hunks:
        want = max(start - 1, 0) if before else start
        at = None
        for delta in range(0, 200):
            for cand in (want + delta, want - delta):
                if pos <= cand <= len(src) - len(before) and src[cand:cand + len(before)] == before:
                    at = cand
                    break
            if at is not None:
                break
        if at is None:
            raise ValueError(f"the change at line {start} doesn't match the game's script")
        out += src[pos:at] + after
        pos = at + len(before)
    return "".join(out + src[pos:])


# ---------------------------------------------------------------- terrain


def heights_changes(name: str, base_units, new_units) -> dict:
    samples = [[x, z, new_units[z][x]] for z in range(len(base_units)) for x in range(len(base_units))
               if new_units[z][x] != base_units[z][x]]
    return {"terrain": name, "samples": samples}


def apply_heights_changes(base_units, doc) -> list:
    units = [list(row) for row in base_units]
    n = len(units)
    for x, z, v in doc.get("samples", []):
        if not (0 <= x < n and 0 <= z < n):
            raise ValueError(f"height sample ({x}, {z}) is off the {n} x {n} map")
        units[z][x] = int(v)
    return units


def grey_changes(base: bytes, new: bytes) -> bytes:
    """Grey + alpha pixels: new value with alpha 255 where it differs from base."""
    out = bytearray(len(new) * 2)
    for i, (b, v) in enumerate(zip(base, new)):
        if b != v:
            out[2 * i] = v
            out[2 * i + 1] = 255
    return bytes(out)


def apply_grey_changes(base: bytes, rgba: bytes) -> bytes:
    """base grey pixels with the changes (decoded RGBA: grey in R, mark in A)."""
    out = bytearray(base)
    for i in range(len(out)):
        if rgba[4 * i + 3] >= 128:
            out[i] = rgba[4 * i]
    return bytes(out)


# ---------------------------------------------------------------- textures


def rgba_changes(base: bytes, new: bytes) -> tuple:
    """(RGBA with only changed pixels, grey mask: 255 where changed)."""
    n = len(new) // 4
    pix, mask = bytearray(len(new)), bytearray(n)
    for i in range(n):
        if new[4 * i:4 * i + 4] != base[4 * i:4 * i + 4]:
            pix[4 * i:4 * i + 4] = new[4 * i:4 * i + 4]
            mask[i] = 255
    return bytes(pix), bytes(mask)


def apply_rgba_changes(base: bytes, pix: bytes, mask_rgba: bytes) -> bytes:
    out = bytearray(base)
    for i in range(len(out) // 4):
        if mask_rgba[4 * i] >= 128:
            out[4 * i:4 * i + 4] = pix[4 * i:4 * i + 4]
    return bytes(out)


# ---------------------------------------------------------------- animation tables


def animtable_changes(base: dict, new: dict) -> dict:
    """Entries (by set and handle) that differ, are new or are gone, plus the
    variants and fallback when they changed."""
    def index(doc):
        return {(s["name"], e["handle"]): e for s in doc["sets"] for e in s["entries"]}
    old_e, new_e = index(base), index(new)
    entries = [{"set": k[0], **e} for k, e in new_e.items() if old_e.get(k) != e]
    entries += [{"set": k[0], "handle": k[1], "remove": True} for k in old_e if k not in new_e]
    out = {"kind": "animtable changes", "name": new.get("name", base.get("name")), "entries": entries}
    for key in ("variants", "fallback"):
        if new.get(key) != base.get(key):
            out[key] = new.get(key)
    return out


def apply_animtable_changes(doc: dict, changes: dict) -> dict:
    import copy
    doc = copy.deepcopy(doc)
    sets = {s["name"]: s for s in doc["sets"]}
    for e in changes.get("entries", []):
        e = dict(e)
        set_name = e.pop("set")
        if set_name not in sets:
            sets[set_name] = {"name": set_name, "entries": []}
            doc["sets"].append(sets[set_name])
        entries = sets[set_name]["entries"]
        at = next((i for i, old in enumerate(entries) if old["handle"] == e["handle"]), None)
        if e.pop("remove", False):
            if at is not None:
                del entries[at]
        elif at is None:
            entries.append(e)
        else:
            entries[at] = e
    for key in ("variants", "fallback"):
        if key in changes:
            doc[key] = changes[key]
    return doc


# ---------------------------------------------------------------- layouts


def _layout_items(doc):
    root = doc["tree"][0]["children"]
    keyed = {}
    for item in root:
        name = next((c.get("text") for c in item.get("children", []) if c.get("tag") == "NAME"), None)
        if name is None:
            return None
        keyed[(item["tag"], name.lower())] = item
    return keyed


def layout_changes(base: dict, new: dict):
    """Items (by chunk tag and NAME) that differ, are new or are gone, or
    None if the items can't be told apart by name."""
    old_i, new_i = _layout_items(base), _layout_items(new)
    if old_i is None or new_i is None:
        return None
    return {"format": "mercfoundry-ai-changes-1", "kind": new.get("kind"),
            "changed": [item for k, item in new_i.items() if old_i.get(k) != item],
            "removed": [[tag, name] for (tag, name) in old_i if (tag, name) not in new_i]}


def apply_layout_changes(doc: dict, changes: dict) -> dict:
    import copy
    doc = copy.deepcopy(doc)
    root = doc["tree"][0]["children"]
    removed = {(t, n.lower()) for t, n in changes.get("removed", [])}

    def key(item):
        name = next((c.get("text") for c in item.get("children", []) if c.get("tag") == "NAME"), "")
        return item["tag"], (name or "").lower()

    root[:] = [item for item in root if key(item) not in removed]
    at = {key(item): i for i, item in enumerate(root)}
    for item in changes.get("changed", []):
        k = key(item)
        if k in at:
            root[at[k]] = item
        else:
            at[k] = len(root)
            root.append(item)
    return doc


# ---------------------------------------------------------------- mod pack

def _game_record(archives, key, kind):
    for a in archives:
        for r in a.records:
            if r.key == key and r.kind == kind:
                return a, r
    return None


def pack(src, out, data_dir) -> dict:
    """Copy the mod at src to out with game-derived files rewritten as
    changes only. Returns {"converted": [...], "dropped": [...], "warnings": [...]}."""
    import shutil
    from pathlib import Path
    from . import dsk, png, script, terrain, terrain_paint, texture
    from .hashing import name_hash
    src, out, data_dir = Path(src), Path(out), Path(data_dir)
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} is not empty")
    archives = [dsk.Archive(p) for p in sorted(data_dir.glob("*.dsk"))]
    shutil.copytree(src, out, dirs_exist_ok=True)
    report = {"converted": [], "dropped": [], "warnings": []}
    # Shared mods record the toolkit they were packed with, so an older
    # toolkit says it's too old instead of failing on a newer feature.
    from . import __version__
    meta_path = out / "mod.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"name": src.name}
    if "requires" not in meta:
        meta["requires"] = ".".join(__version__.split(".")[:2])
        meta_path.write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")

    # Scripts that replace a game script become diffs.
    for f in sorted((out / "scripts").glob("*.lua")) if (out / "scripts").is_dir() else []:
        hit = _game_record(archives, name_hash(f.stem), name_hash("script"))
        if not hit:
            continue
        old = script.decode(hit[0].read(hit[1]))
        new = f.read_bytes().decode("latin-1")
        new = new if b"\r\n" in old.text else new.replace("\r\n", "\n")
        diff = make_diff(old.source, new, f.name)
        f.unlink()
        if diff:
            (f.parent / (f.name + ".diff")).write_bytes(diff.encode("latin-1"))
            report["converted"].append(f"scripts/{f.name} -> {f.name}.diff")
        else:
            report["dropped"].append(f"scripts/{f.name} (same as the game's)")

    tdir = out / "terrain"
    terrains = {}

    def tern(name):
        if name not in terrains:
            hit = _game_record(archives, name_hash(name), name_hash("Terrain"))
            terrains[name] = terrain.tern_chunks(hit[0].read(hit[1])) if hit else None
        return terrains[name]

    for f in sorted(tdir.glob("*.heights.png")) if tdir.is_dir() else []:
        name = f.name[:-len(".heights.png")]
        meta_path = f.with_name(name + ".heights.json")
        chunks = tern(name)
        if chunks is None or not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        units = terrain.units_from_png(f.read_bytes(), meta)
        _, base, _ = terrain.decode_height_units(chunks)
        doc = heights_changes(name, base, units)
        if meta.get("relight") is False:
            doc["relight"] = False
        f.unlink()
        meta_path.unlink()
        (tdir / f"{name}.heights.changes.json").write_text(json.dumps(doc, separators=(",", ":")) + "\n",
                                                            encoding="utf-8")
        report["converted"].append(f"terrain/{f.name} -> {name}.heights.changes.json "
                                   f"({len(doc['samples'])} samples)")

    for f in sorted(tdir.glob("*.png")) if tdir.is_dir() else []:
        if f.name.lower().endswith(".changes.png"):
            continue
        parsed = terrain_paint.parse_layer_file(f.name)
        if not parsed or tern(parsed[0]) is None:
            continue
        name, index = parsed
        _, base_imgs = terrain_paint.layer_images(tern(name))
        w, h, rgba = png.decode(f.read_bytes())
        base = base_imgs.get(index)
        if base is None or len(base) != w * h:
            continue      # a new layer: the author's own image
        changes = grey_changes(base, bytes(rgba[0::4]))
        f.unlink()
        if any(changes[1::2]):
            (f.parent / (f.name[:-4] + ".changes.png")).write_bytes(png.encode(w, h, changes, 2))
            report["converted"].append(f"terrain/{f.name} -> {f.name[:-4]}.changes.png")
        else:
            report["dropped"].append(f"terrain/{f.name} (same as the game's)")

    for f in sorted(tdir.glob("*.decals.json")) if tdir.is_dir() else []:
        if "decals" in json.loads(f.read_text(encoding="utf-8")):
            report["warnings"].append(f"terrain/{f.name} lists the map's decals, which are game data; "
                                      "keep only \"roads\" (or your own decals) before sharing")

    # Textures that edit a game texture become changed pixels plus a mask.
    for f in sorted((out / "textures").glob("*.png")) if (out / "textures").is_dir() else []:
        if f.name.lower().endswith((".changes.png", ".mask.png")):
            continue
        hit = _game_record(archives, name_hash(f.stem), texture.TEXTURE)
        if not hit:
            continue
        bw, bh, base = texture.decode(hit[0].read(hit[1])).rgba()
        w, h, rgba = png.decode(f.read_bytes())
        if (w, h) != (bw, bh):
            report["warnings"].append(f"textures/{f.name} replaces the game's {bw}x{bh} texture with a {w}x{h} "
                                      "image; it is shared as is, so it should be entirely your own work")
            continue
        pix, mask = rgba_changes(base, rgba)
        f.unlink()
        if any(mask):
            f.with_name(f.stem + ".changes.png").write_bytes(png.encode(w, h, pix, 4))
            f.with_name(f.stem + ".mask.png").write_bytes(png.encode(w, h, mask, 1))
            report["converted"].append(f"textures/{f.name} -> {f.stem}.changes.png + {f.stem}.mask.png")
        else:
            report["dropped"].append(f"textures/{f.name} (same as the game's)")

    # Edited glTF models keep only the changed and new pieces.
    from . import model, model_changes
    for f in sorted((out / "models").iterdir()) if (out / "models").is_dir() else []:
        if f.suffix.lower() not in (".glb", ".gltf") or f.stem.lower().endswith(".changes"):
            continue
        try:
            mr, sr = model.find(archives, f.stem)
        except KeyError:
            continue
        glb, rep = model_changes.make_changes(mr, sr, f)
        size = f.stat().st_size
        f.unlink()
        if f.suffix.lower() == ".gltf":
            report["warnings"].append(f"models/{f.name}: delete the .bin and texture files beside it before sharing")
        if glb is None:
            report["dropped"].append(f"models/{f.name} (same as the game's)")
            continue
        (f.parent / f"{f.stem}.changes.glb").write_bytes(glb)
        report["converted"].append(f"models/{f.name} -> {f.stem}.changes.glb ({len(rep.edited)} pieces edited, "
                                   f"{len(rep.added)} added, {len(rep.removed)} removed; {size // 1024} KB -> "
                                   f"{max(1, len(glb) // 1024)} KB)")

    # Animation files keep only the channels that change a game clip (the
    # game's motion fills in the rest), and no character mesh or textures.
    adir = out / "animations"
    if adir.is_dir():
        from . import animation as anim_mod
        new_clips = {}
        if (adir / "new.json").exists():
            new_clips = {k.lower(): v for k, v in json.loads((adir / "new.json").read_text(encoding="utf-8")).items()}

        def game_clip(name):
            # A new clip is built on its template clip, which fills in the
            # joints the clip leaves out, as the game's own clip does.
            base = new_clips.get(name.lower(), name)
            hit = _game_record(archives, name_hash(base), anim_mod.ANIMATION)
            return hit[0].read(hit[1]) if hit else None

        for f in sorted(list(adir.glob("*.glb")) + list(adir.glob("*.gltf"))):
            glb, summary = model_changes.animation_changes(f, game_clip)
            size = f.stat().st_size
            f.unlink()
            if glb is None:
                report["dropped"].append(f"animations/{f.name} (its clips match the game's)")
                continue
            (adir / f"{f.stem}.glb").write_bytes(glb)
            clips = ", ".join(f"{k} {a}/{b} channels" for k, (a, b) in sorted(summary.items()))
            report["converted"].append(f"animations/{f.name} -> {f.stem}.glb ({clips}; "
                                       f"{size // 1024} KB -> {max(1, len(glb) // 1024)} KB)")

    # Animation tables: only the entries that differ.
    from . import aidata, animtable
    tdir2 = out / "animtables"
    for f in sorted(tdir2.glob("*.json")) if tdir2.is_dir() else []:
        if f.name.endswith(".changes.json"):
            continue
        doc = json.loads(f.read_text(encoding="utf-8"))
        name = doc.get("name") or f.stem
        hit = _game_record(archives, name_hash(name), animtable.ANIMTABLE)
        if not hit:
            continue
        changes = animtable_changes(animtable.to_json(hit[0].read(hit[1])), doc)
        f.unlink()
        if changes["entries"] or len(changes) > 3:
            (tdir2 / f"{f.stem}.changes.json").write_text(json.dumps(changes, indent=1) + "\n", encoding="utf-8")
            report["converted"].append(f"animtables/{f.name} -> {f.stem}.changes.json "
                                       f"({len(changes['entries'])} entries)")
        else:
            report["dropped"].append(f"animtables/{f.name} (same as the game's)")

    # Layouts: changed items only; load lists as diffs.
    ldir = out / "layout"
    for f in sorted(ldir.iterdir()) if ldir.is_dir() else []:
        if f.name.endswith((".changes.json", ".diff")):
            continue
        if f.name.endswith(".list.txt"):
            layer = f.name[:-len(".list.txt")]
            hit = _game_record(archives, name_hash(layer), name_hash("list"))
            if not hit:
                continue
            diff = make_diff(hit[0].read(hit[1]).decode("latin-1"), f.read_bytes().decode("latin-1"), f.name)
            f.unlink()
            if diff:
                (ldir / f"{f.name}.diff").write_bytes(diff.encode("latin-1"))
                report["converted"].append(f"layout/{f.name} -> {f.name}.diff")
            else:
                report["dropped"].append(f"layout/{f.name} (same as the game's)")
            continue
        if f.suffix != ".json":
            continue
        layer, _, kind = f.name[:-len(".json")].rpartition(".")
        hit = _game_record(archives, name_hash(layer), name_hash(kind)) if layer else None
        if not hit:
            continue
        doc = json.loads(f.read_text(encoding="utf-8"))
        changes = layout_changes(aidata.to_json(hit[0].read(hit[1]), kind), doc)
        if changes is None:
            report["warnings"].append(f"layout/{f.name} still holds the game's layout data (its items have no "
                                      "names to match changes by)")
            continue
        f.unlink()
        if changes["changed"] or changes["removed"]:
            (ldir / f"{layer}.{kind}.changes.json").write_text(json.dumps(changes, indent=1) + "\n",
                                                               encoding="utf-8")
            report["converted"].append(f"layout/{f.name} -> {layer}.{kind}.changes.json "
                                       f"({len(changes['changed'])} changed or new, {len(changes['removed'])} removed)")
        else:
            report["dropped"].append(f"layout/{f.name} (same as the game's)")

    for f in sorted((out / "models").glob("*.obj")) if (out / "models").is_dir() else []:
        report["warnings"].append(f"models/{f.name} still holds game geometry; edit the model as glTF "
                                  "(model --glb) so mod pack can keep only your changes")
    return report


def check(original, packed, data_dir) -> list:
    """Build both mods and list any differences in what they produce."""
    import tempfile
    from pathlib import Path
    from . import dsk, modbuild
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp) / "original", Path(tmp) / "packed"
        modbuild.build(original, data_dir, a)
        modbuild.build(packed, data_dir, b)
        for n in sorted({p.name for p in a.glob("*.dsk")} | {p.name for p in b.glob("*.dsk")}):
            if not (a / n).exists() or not (b / n).exists():
                problems.append(f"{n}: built by only one of them")
                continue
            ra, rb = dsk.Archive(a / n), dsk.Archive(b / n)
            ma = {r.ident: ra.read(r) for r in ra.records}
            mb = {r.ident: rb.read(r) for r in rb.records}
            bad = [k for k in set(ma) | set(mb) if ma.get(k) != mb.get(k)]
            if bad:
                problems.append(f"{n}: {len(bad)} records differ")
        for p in sorted(a.rglob("*")):
            if p.is_file() and p.suffix != ".dsk" and p.name != "foundry-mod.json":
                rel = p.relative_to(a)
                if not (b / rel).exists() or (b / rel).read_bytes() != p.read_bytes():
                    problems.append(f"{rel.as_posix()} differs")
    return problems
