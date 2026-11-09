"""Command line: python -m mercfoundry <command> ..."""
import argparse
import collections
import sys
from pathlib import Path

from . import dsk, names as namelib, ucfb, verify
from .hashing import NameBook, name_hash, type_book


def _names(args) -> NameBook:
    book = NameBook()
    for path in getattr(args, "names", None) or []:
        book.add_many(Path(path).read_text(encoding="utf-8").splitlines())
    return book


def cmd_info(args):
    arc = dsk.Archive(args.archive)
    types = type_book()
    print(f"{arc.path.name}: {arc.count} records, extra word {arc.extra:#x}, "
          f"tail {arc.tail_kind()} ({arc.tail_size} bytes)")
    counts = collections.Counter(r.kind for r in arc.records)
    sizes = collections.Counter()
    for r in arc.records:
        sizes[r.kind] += r.size
    guesses = namelib.guess_types(k for k in counts if not types.get(k))
    for kind, n in counts.most_common():
        shown = types.get(kind) or (f"{guesses[kind]}?" if kind in guesses else f"{kind:08X}")
        print(f"  {shown:>10}  {n:6d} records  {sizes[kind]:12,d} bytes")
    if guesses:
        print("  (? = name guessed from its hash; check the records before trusting it)")


def cmd_list(args):
    arc = dsk.Archive(args.archive)
    names = _names(args)
    dsk.collect_names(arc, names)
    types = type_book()
    want = None
    if args.type:
        want = int(args.type, 16) if all(c in "0123456789abcdefABCDEF" for c in args.type) and len(args.type) == 8 else name_hash(args.type)
    guesses = namelib.guess_types({r.kind for r in arc.records})
    for r in arc.records:
        if want is not None and r.kind != want:
            continue
        kind = types.get(r.kind) or (f"{guesses[r.kind]}?" if r.kind in guesses else f"{r.kind:08X}")
        print(f"{r.index:5d}  {r.key:08X}  {kind:>10}  {r.size:10d}  {names.get(r.key) or ''}")


def cmd_extract(args):
    if args.textures and not args.decode:
        sys.exit("--textures needs --decode")
    if args.textures is True:
        print("decoding all textures; this can take a few minutes for streamed.dsk")
    m = dsk.extract(args.archive, args.out, _names(args), decode=args.decode, textures=args.textures)
    named = sum(1 for e in m["records"] if e["name"])
    print(f"extracted {len(m['records'])} records ({named} named) to {args.out}")
    if args.decode:
        done = sum(1 for e in m["records"] if e.get("decoded"))
        failed = [e for e in m["records"] if e.get("decode_error")]
        print(f"decoded {done} records into {args.out}/decoded")
        for e in failed[:10]:
            print(f"  could not decode {e['file']}: {e['decode_error']}")
        if len(failed) > 10:
            print(f"  ...and {len(failed) - 10} more (see manifest.json)")


def cmd_rebuild(args):
    changed = dsk.rebuild(args.folder)
    for f in changed:
        print(f"rebuilt {f}")
    print(f"{len(changed)} records rebuilt from decoded files")


def cmd_pack(args):
    if not args.no_rebuild:
        for f in dsk.rebuild(args.folder):
            print(f"rebuilt {f}")
    n = dsk.pack(args.folder, args.out)
    print(f"packed {n} records into {args.out}")


def cmd_diff(args):
    types = type_book()
    names = _names(args)
    for path in (args.a, args.b):
        dsk.collect_names(dsk.Archive(path), names)
    added, removed, changed = dsk.diff(args.a, args.b)
    for tag, rows in (("+", added), ("-", removed), ("~", changed)):
        for r in rows:
            print(f"{tag} {r.key:08X} {dsk.label(types, r.kind):>10} {names.get(r.key) or ''}")
    print(f"{len(added)} added, {len(removed)} removed, {len(changed)} changed")


def cmd_tree(args):
    arc = dsk.Archive(args.archive)
    names = _names(args)
    dsk.collect_names(arc, names)
    target = args.record
    for r in arc.records:
        if target.isdigit() and int(target) == r.index or \
           names.get(r.key) == target or f"{r.key:08X}" == target.upper():
            print("\n".join(ucfb.dump(ucfb.parse(arc.read(r)))))
            return
    sys.exit(f"record not found: {target}")


def cmd_verify(args):
    archives = verify.find_archives(args.target)
    if not archives:
        sys.exit(f"no .dsk archives found in {args.target}")
    bad = 0
    for path in archives:
        rep = verify.verify_archive(path)
        checked = ", ".join(f"{n} {label}" for label, n in sorted(rep.checked.items())) or "no decodable records"
        status = "OK" if rep.ok else "FAIL"
        print(f"{status}  {rep.archive}: {rep.records} records, layout {'ok' if rep.layout_ok else 'DIFFERS'}; checked {checked}")
        for index, label, error in rep.failures:
            print(f"      record {index} ({label}): {error}")
        bad += not rep.ok
    print(f"{len(archives) - bad} of {len(archives)} archives verified")
    sys.exit(1 if bad else 0)


def cmd_names(args):
    wanted = None
    if args.match:
        wanted = set()
        for path in args.match:
            for r in dsk.Archive(path).records:
                wanted.update((r.key, r.kind))
    book = namelib.harvest(args.sources, wanted)
    n = namelib.write(book, args.out)
    if wanted is not None:
        print(f"{n} names match {len(book)} of {len(wanted)} hashes in the given archives")
    else:
        print(f"{n} candidate names")
    print(f"wrote {args.out}; use it with --names")


def _data_archives(target):
    p = Path(target)
    paths = sorted(p.glob("*.dsk")) if p.is_dir() else [p]
    # Prefer locked over streamed when a model is in both.
    paths.sort(key=lambda q: (q.name.lower() != "locked.dsk", q.name.lower()))
    return [dsk.Archive(q) for q in paths]


def cmd_model(args):
    from . import model
    arcs = _data_archives(args.data)
    try:
        records = model.find(arcs, args.name)
    except KeyError:
        sys.exit(f"no model named {args.name!r}; list them with: list <archive> --type Model")
    m = model.decode(*records)
    names = _names(args)
    for arc in arcs:
        dsk.collect_names(arc, names)
    lods = m.lods()
    lod = args.lod if args.lod is not None else (lods[0] if lods else 0)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tex_name = lambda h: names.get(h) or f"{h:08X}"
    obj, mtl = model.to_obj(m, lod, tex_name, args.damaged)
    (out / f"{m.name}.obj").write_text(obj, encoding="utf-8")
    (out / f"{m.name}.mtl").write_text(mtl, encoding="utf-8")
    pieces = m.at_lod(lod, args.damaged)
    tris = sum(len(p.triangles) for p in pieces)
    print(f"{m.name}: {len(m.joints)} joints, {len(m.pieces)} pieces, detail levels {lods}")
    print(f"wrote {out / (m.name + '.obj')} (level {lod}: {len(pieces)} pieces, {tris} triangles)")
    textures, png_files = {}, {}
    if not args.no_textures:
        from . import texture
        wanted = {p.texture for p in pieces}
        for arc in arcs:
            with arc.path.open("rb") as f:
                for r in arc.records:
                    if r.kind == texture.TEXTURE and r.key in wanted and r.key not in textures:
                        f.seek(r.offset)
                        try:
                            t = texture.decode(f.read(r.size))
                            textures[r.key] = t.rgba()
                            w, h, px = textures[r.key]
                            from . import png as pngmod
                            png_files[r.key] = (pngmod.encode(w, h, px, 4),
                                                any(px[i] < 255 for i in range(3, len(px), 4)))
                            (out / f"{tex_name(r.key)}.png").write_bytes(png_files[r.key][0])
                        except ValueError as err:
                            print(f"  skipped texture {tex_name(r.key)}: {err}")
        missing = len(wanted - set(textures))
        print(f"wrote {len(textures)} textures" + (f" ({missing} not found)" if missing else ""))
    if args.glb:
        from . import gltf, gltf_check
        anims = []
        if args.anim:
            import fnmatch
            from . import animation
            for arc in arcs:
                with arc.path.open("rb") as f:
                    for r in arc.records:
                        if r.kind != animation.ANIMATION:
                            continue
                        f.seek(r.offset)
                        rec = f.read(r.size)
                        aname = ucfb.record_name(rec) or f"{r.key:08X}"
                        if any(fnmatch.fnmatchcase(aname.lower(), pat.lower()) for pat in args.anim):
                            anims.append(animation.decode(rec))
            joints = {j.name.lower() for j in m.joints}
            fitting = [a for a in anims if sum(t.joint.lower() in joints for t in a.tracks) >= len(a.tracks) // 2]
            if len(fitting) < len(anims):
                print(f"  skipped {len(anims) - len(fitting)} animations that don't fit this skeleton")
            anims = fitting
        data = gltf.export(m, lod, args.damaged, png_files, tex_name, anims, args.in_place)
        gltf_check.check(data, m, lod, args.damaged)
        path = out / f"{m.name}.glb"
        path.write_bytes(data)
        print(f"wrote {path} (glTF with {len(m.joints)} joints"
              + (", skinned" if any(p.bones for p in pieces) else "")
              + (", textures embedded" if png_files else "")
              + (f", {len(anims)} animations" if anims else "") + ")")
    if args.preview:
        png = out / f"{m.name}_preview.png"
        png.write_bytes(model.preview_png(m, lod, damaged=args.damaged, textures=textures))
        print(f"wrote {png}")
    if args.collision:
        boxes = sum(len(s.boxes) for s in m.collision)
        meshes = sum(1 for s in m.collision if s.is_mesh)
        print(f"collision: {len(m.collision)} shapes ({boxes} boxes, {meshes} meshes)")
        if m.collision:
            png = out / f"{m.name}_collision.png"
            png.write_bytes(model.preview_png(m, lod, collision=True))
            print(f"wrote {png}")


def cmd_model_import(args):
    from . import model_import
    src = Path(args.obj)
    text = src if src.suffix.lower() in (".glb", ".gltf") else src.read_text(encoding="utf-8", errors="replace")
    try:
        # Material names are texture names; their hash is the texture key.
        results = model_import.import_into_folders(args.folders, args.name, text, args.lod, args.damaged,
                                                   collision=not args.no_collision)
    except KeyError as err:
        sys.exit(str(err.args[0]))
    for folder, r in results:
        if not r.changed:
            print(f"{folder}: no changes ({r.unchanged} pieces unchanged)")
            continue
        print(f"{folder}: {len(r.edited)} pieces edited, {len(r.added)} added, {len(r.removed)} removed, "
              f"{r.unchanged} unchanged" + ("; quantization box grew" if r.box_grew else "")
              + (f"; {r.collision_boxes} collision boxes added" if r.collision_boxes else ""))
        for w in r.warnings:
            print(f"  warning: {w}")
    print("now run pack on each folder")


def cmd_mod_new(args):
    from . import modbuild
    folder = modbuild.new_project(args.folder, args.name)
    print(f"created mod project {folder}; see its README.md")


def cmd_mod_build(args):
    from . import modbuild, verify as verifier
    try:
        result = modbuild.build(args.folder, args.data, args.out,
                                name=None if len(args.folder) == 1 else Path(args.out).name)
    except (ValueError, KeyError, FileNotFoundError, PermissionError) as err:
        sys.exit(f"build failed: {err}")
    for line in result.changes:
        print(f"  {line}")
    for w in result.warnings:
        print(f"  warning: {w}")
    if not result.written:
        print("nothing to build: no edits change the game")
        return
    print(f"wrote {', '.join(result.written)} to {args.out}")
    bad = 0
    for name in result.written:
        rep = verifier.verify_archive(Path(args.out) / name)
        bad += not rep.ok
        for index, label, error in rep.failures:
            print(f"  verify {name} record {index} ({label}): {error}")
    print("verified" if not bad else "verification FAILED")
    if bad:
        sys.exit(1)


def cmd_mod_pack(args):
    from . import modpack
    try:
        report = modpack.pack(args.folder, args.out, args.data)
    except (ValueError, FileNotFoundError) as err:
        sys.exit(f"pack failed: {err}")
    for line in report["converted"]:
        print(f"  {line}")
    for line in report["dropped"]:
        print(f"  dropped {line}")
    for w in report["warnings"]:
        print(f"  warning: {w}")
    print(f"wrote {args.out}")
    if args.check:
        problems = modpack.check(args.folder, args.out, args.data)
        print("check: the packed mod builds the same" if not problems else "check FAILED: " + "; ".join(problems))
        if problems:
            sys.exit(1)


def cmd_mod_sync(args):
    from . import sync, verify as verifier
    try:
        if args.install_hook:
            print(f"wrote {sync.install_hook(Path(args.game))}")
        res = sync.sync(args.game, args.data, args.force, args.enable or args.install_hook)
    except (ValueError, KeyError, FileNotFoundError, PermissionError) as err:
        sys.exit(f"sync failed: {err}")
    if not res.sources:
        print("no enabled source mods" + (f"; removed mods/{sync.OUTPUT}" if res.removed else ""))
        return
    print(f"source mods, in load order: {', '.join(res.sources)}")
    if res.up_to_date:
        print(f"mods/{sync.OUTPUT} is up to date")
    else:
        for line in res.build.changes:
            print(f"  {line}")
        for w in res.build.warnings:
            print(f"  warning: {w}")
        out = Path(args.game) / "mods" / sync.OUTPUT
        bad = 0
        for name in res.build.written:
            rep = verifier.verify_archive(out / name)
            bad += not rep.ok
            for index, label, error in rep.failures:
                print(f"  verify {name} record {index} ({label}): {error}")
        print(f"wrote mods/{sync.OUTPUT}" + (" (verified)" if not bad else " (verification FAILED)"))
        if bad:
            sys.exit(1)
    for n in res.notes:
        print(f"note: {n}")


def cmd_edit(args):
    from .editor import server
    if not Path(args.folder).is_dir():
        sys.exit(f"{args.folder} is not a folder (make one with: mod new {args.folder})")
    server.serve(args.folder, args.data, args.port, not args.no_browser)


def cmd_texture(args):
    from . import texture
    arcs = _data_archives(args.data)
    try:
        t = texture.decode(texture.find(arcs, args.name))
    except KeyError:
        sys.exit(f"no texture named {args.name!r}; list them with: list <archive> --type Texture")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base = t.name or args.name
    for face in range(t.faces):
        path = out / (f"{base}.png" if t.faces == 1 else f"{base}_face{face}.png")
        path.write_bytes(t.to_png(args.mip, face))
        print(f"wrote {path}")
    print(f"{base}: {t.width}x{t.height} {t.format_name}, {t.mips} mip levels" + (", cube map" if t.faces == 6 else ""))


def cmd_find(args):
    import json as _json
    from . import find
    arcs = _data_archives(args.data)
    near = args.near
    if args.near_name:
        spots = find.locate(arcs, args.near_name, args.layer if args.layer and "*" not in args.layer else None)
        if not spots:
            sys.exit(f"no object named {args.near_name!r}")
        layer, near = spots[0]
        if len(spots) > 1:
            others = ", ".join(l for l, _ in spots[1:])
            print(f"note: {args.near_name} is in several layers; using {layer} (also in {others}; pick with --layer)")
    props = []
    for p in args.property or []:
        key, _, value = p.partition("=")
        props.append((key, value if "=" in p else None))
    radius = args.radius if args.radius is not None else (50.0 if near is not None else None)
    if args.layout:
        names = NameBook()
        for path in getattr(args, "names", None) or []:
            names.add_many(Path(path).read_text(encoding="utf-8").splitlines())
        for arc in arcs:
            dsk.collect_names(arc, names)
        layer_names = {k: n for k, n in names.items()}
        hits = find.search_layout(arcs, layer_names, args.name, args.layer, near, radius, args.limit)
        for h in hits:
            dist = f"{h['distance']:7.1f}m " if "distance" in h else ""
            first = " ".join(f"{v:9.2f}" for v in h["points"][0]) if h["points"] else ""
            print(f"{dist}{h['layer']:<30} {h['kind']:<10} {h['item']:<9} {h['name'] or '-':<28} "
                  f"{len(h['points']):4d} pts  {first}")
        print(f"{len(hits)} items")
        return
    hits = find.search(arcs, args.name, args.model, args.layer, near, radius, props, args.limit)
    if args.json or args.rule:
        out = []
        for h in hits:
            if args.rule:
                out.append(find.rule_for(h))
            else:
                h = dict(h)
                h["yaw"] = find.yaw(h["rotation"])
                out.append(h)
        if args.rule:
            print("[\n" + ",\n".join(" " + _json.dumps(r) for r in out) + "\n]")
        else:
            print(_json.dumps(out, indent=1))
        return
    for h in hits:
        pos = " ".join(f"{v:9.2f}" for v in h["position"]) if h["position"] else " " * 29 + "-"
        yaw = find.yaw(h["rotation"])
        dist = f"{h['distance']:7.1f}m " if "distance" in h else ""
        print(f"{dist}{h['layer']:<28} {h['model']:<34} {h['name'] or '-':<28} {pos}"
              + (f"  yaw {yaw:6.1f}" if yaw is not None else ""))
    print(f"{len(hits)} objects" + (" (limit reached)" if args.limit and len(hits) == args.limit else ""))


def cmd_animtable(args):
    import json as _json
    from . import animtable
    arcs = _data_archives(args.data)
    key = name_hash(args.name)
    for arc in arcs:
        for r in arc.records:
            if r.key == key and r.kind == animtable.ANIMTABLE:
                doc = animtable.to_json(arc.read(r))
                out = Path(args.out)
                out.mkdir(parents=True, exist_ok=True)
                path = out / f"{args.name}.json"
                path.write_text(_json.dumps(doc, indent=1) + "\n", encoding="utf-8")
                entries = sum(len(s["entries"]) for s in doc["sets"])
                print(f"wrote {path} ({len(doc['sets'])} sets, {entries} entries, "
                      f"{len(doc['variants'])} variants" + (f"; falls back to {doc['fallback']}" if doc.get("fallback") else "") + ")")
                return
    sys.exit(f"no animation table named {args.name!r}")


def cmd_layout(args):
    from . import aidata
    arcs = _data_archives(args.data)
    key = name_hash(args.layer)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    wrote = 0
    for arc in arcs:
        with arc.path.open("rb") as f:
            for r in arc.records:
                if r.key != key:
                    continue
                kind = aidata.KINDS.get(r.kind) or ("list" if r.kind == aidata.LIST else None)
                if not kind:
                    continue
                f.seek(r.offset)
                data = f.read(r.size)
                if kind == "list":
                    path = out / f"{args.layer}.list.txt"
                    path.write_bytes(data)
                else:
                    doc = aidata.to_json(data, kind)
                    if not any(True for _ in aidata.items(doc)):
                        continue  # empty for this layer
                    path = out / f"{args.layer}.{kind}.json"
                    import json as _json
                    path.write_text(_json.dumps(doc, indent=1) + "\n", encoding="utf-8")
                print(f"wrote {path}")
                wrote += 1
    if not wrote:
        sys.exit(f"no path, boundary, region, encounter or list data for layer {args.layer!r}")
    print("copy the files you edit into a mod's layout/ folder")


def cmd_unpack_iso(args):
    from . import xiso
    n = xiso.extract(args.image, args.out)
    print(f"extracted {n} files to {args.out}")


PORT_ANALYSIS = "external/Mercenaries-Recompiled/ports/mercenaries/analysis/disasm"


def cmd_funcmap(args):
    import json as _json
    from . import funcmap
    if args.map_cmd == "build":
        doc = funcmap.build(args.xbe, args.analysis, args.manual)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        c = doc["counts"]
        print(f"wrote {out}: {c['named']} named functions ({c['lua_commands']} script commands, "
              f"{c['manual']} hand-curated), {c['with_file']} with a source file "
              f"({c['file_direct']} direct), {c['derived']} named after their script command, "
              f"{c['globals']} globals, {c['structures']} structures")
        if doc.get("unclassified_tables"):
            print("  new Lua tables to classify:", ", ".join(doc["unclassified_tables"]))
        return
    if not Path(args.map).exists():
        sys.exit(f"{args.map} not found; run `funcmap build` first")
    doc = funcmap.load(args.map)
    if args.map_cmd == "find":
        hits = funcmap.find(doc, args.text)
        for e in hits[:args.limit]:
            extra = f"  [{e['file']}]" if e.get("file") else ""
            print(f"{e['address']}  {' / '.join(e.get('names', [])) or '(unnamed)'}{extra}")
        if len(hits) > args.limit:
            print(f"... {len(hits) - args.limit} more (use --limit)")
        if not hits:
            print("no matches")
    elif args.map_cmd == "show":
        lines = funcmap.show(doc, args.what, args.analysis if Path(args.analysis).exists() else None)
        if not lines:
            sys.exit(f"no function named {args.what!r}")
        print("\n".join(lines))
    elif args.map_cmd == "header":
        Path(args.out).write_text(funcmap.header(doc), encoding="utf-8")
        print(f"wrote {args.out}")
    elif args.map_cmd == "names":
        Path(args.out).write_text(_json.dumps(funcmap.ghidra_names(doc), indent=1) + "\n", encoding="utf-8")
        print(f"wrote {args.out} ({len(funcmap.ghidra_names(doc))} names, the port's ghidra_names.json format)")


PORT_GEN = "external/Mercenaries-Recompiled/ports/mercenaries/src/recomp/gen"


def cmd_port_hooks(args):
    from . import funcmap, porthooks
    doc = funcmap.load(args.map)
    names = {funcmap.name_of(e): int(e["address"], 16) for e in doc["functions"] if funcmap.name_of(e)}
    if args.only:
        addresses = []
        for item in args.only:
            if item in names:
                addresses.append(names[item])
            else:
                try:
                    addresses.append(int(item, 16))
                except ValueError:
                    sys.exit(f"{item!r} is neither a mapped function name nor an address")
    else:
        addresses = porthooks.map_addresses(doc)
    hooked, missing = porthooks.apply(args.gen, addresses, remove=args.remove)
    if args.remove:
        print("removed all hook wrappers; generated code restored")
        return
    print(f"{len(hooked)} functions hookable (wrappers in {Path(args.gen) / porthooks.HOOK_FILE})")
    if missing:
        print(f"  {len(missing)} have no generated definition (the port implements them by hand): "
              + ", ".join(f"0x{a:08X}" for a in missing[:8]) + (" ..." if len(missing) > 8 else ""))
    print("now rebuild the port")


def cmd_objectnames(args):
    from . import templates
    names = templates.object_names(_data_archives(args.data))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(names) + "\n", encoding="utf-8")
    print(f"wrote {out}: {len(names)} object names")


def cmd_templates(args):
    from . import templates
    rows = templates.collect(_data_archives(args.data))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(templates.to_tsv(rows), encoding="utf-8")
    print(f"wrote {out}: {len(rows)} templates")


def cmd_vehicles(args):
    import json as _json
    from . import vehicles
    found = vehicles.collect(_data_archives(args.data))
    if not args.model:
        print(f"{'type':<11} {'model':<34} {'objects':>7}  display name / templates")
        for model, e in found.items():
            if e["handling"]:
                print(f"{e['type']:<11} {model:<34} {e['objects']:>7}  {e['display']}  "
                      f"({', '.join(e['templates'][:3])}{' ...' if len(e['templates']) > 3 else ''})")
        print("export one as a mod rule: vehicles DATAxbox <model> -o objects/<model>.json")
        return
    e = found.get(args.model) or next((v for k, v in found.items() if k.lower() == args.model.lower()), None)
    if not e or not e["handling"]:
        sys.exit(f"no vehicle model {args.model!r} with handling values (list them with: vehicles DATAxbox)")
    model = next(k for k, v in found.items() if v is e)
    text = _json.dumps(vehicles.rule(model, e), indent=1) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}: {len(e['handling'])} handling values for {e['objects']} {model} objects")
    else:
        print(text, end="")
    if e["varies"]:
        print(f"note: copies of {model} differ in {', '.join(e['varies'])}; the rule sets them all to these values",
              file=sys.stderr)


def cmd_terrain(args):
    import json as _json
    from . import terrain
    key = name_hash(args.name)
    for arc in _data_archives(args.data):
        for r in arc.records:
            if r.key == key and r.kind == name_hash("Terrain"):
                chunks = terrain.tern_chunks(arc.read(r))
                image, meta = terrain.heightmap_png(chunks)
                meta["terrain"] = args.name
                out = Path(args.out)
                out.mkdir(parents=True, exist_ok=True)
                (out / f"{args.name}.heights.png").write_bytes(image)
                (out / f"{args.name}.heights.json").write_text(_json.dumps(meta, indent=1) + "\n", encoding="utf-8")
                info = terrain.parse_info(chunks[b"INFO"])
                print(f"wrote {out / (args.name + '.heights.png')} ({meta['size']} x {meta['size']}, "
                      f"{meta['size'] * info['spacing']:.0f} m across, heights {info['min']:.1f} to {info['max']:.1f} m) "
                      f"and {args.name}.heights.json")
                from . import png, terrain_paint
                table = terrain_paint.layer_table(chunks)
                (out / f"{args.name}.layers.json").write_text(_json.dumps({"terrain": args.name, "layers": table}, indent=1) + "\n",
                                                             encoding="utf-8")
                w, images = terrain_paint.layer_images(chunks)
                for k, t in enumerate(table):
                    (out / terrain_paint.layer_file(args.name, k, t["texture"])).write_bytes(png.encode(w, w, images[k], 1))
                from . import terrain_decals
                decals = terrain_decals.export(chunks)
                (out / f"{args.name}.decals.json").write_text(_json.dumps(decals, indent=None, separators=(",", ":"))
                                                             .replace('{"cell"', '\n{"cell"') + "\n", encoding="utf-8")
                print(f"wrote {args.name}.decals.json ({len(decals['decals'])} decals)")
                from . import terrain_surfaces
                data_dir = Path(args.data) if Path(args.data).is_dir() else Path(args.data).parent
                levels = data_dir / "levels.ini"
                config = terrain_surfaces.level_config(levels.read_text(encoding="latin-1"), args.name) \
                    if levels.exists() else None
                if config and (data_dir / config).exists():
                    ini = (data_dir / config).read_bytes().decode("latin-1")
                    surfaces = terrain_surfaces.export(ini, [t["texture"] for t in table], decals["textures"])
                    surfaces = {"config": config.replace("\\", "/"), **surfaces}
                    (out / f"{args.name}.surfaces.json").write_text(_json.dumps(surfaces, indent=1) + "\n", encoding="utf-8")
                    print(f"wrote {args.name}.surfaces.json (surface types and effects per texture, from {config})")
                else:
                    print(f"no level uses terrain {args.name!r}, so no surfaces.json")
                print(f"wrote {args.name}.layers.json and {len(table)} ground layer images ({w} x {w}, "
                      f"{args.name}.layer00.*.png ...); a mod needs only the ones you change")
                return
    sys.exit(f"no terrain named {args.name!r} (list them with list --type Terrain)")


def cmd_hash(args):
    for s in args.names:
        print(f"{name_hash(s):08X}  {s}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="mercfoundry", description="Mercenaries modding tools")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        s = sub.add_parser(name, help=help_)
        s.set_defaults(fn=fn)
        return s

    def names_opt(s):
        s.add_argument("--names", action="append", help="text file of known names, one per line")

    s = add("info", cmd_info, "summarize an archive"); s.add_argument("archive")
    s = add("list", cmd_list, "list records"); s.add_argument("archive")
    s.add_argument("--type", help="only this type (name or 8-digit hex)"); names_opt(s)
    s = add("extract", cmd_extract, "extract every record plus a manifest")
    s.add_argument("archive"); s.add_argument("out"); names_opt(s)
    s.add_argument("--decode", action="store_true", help="also write readable scripts, world layers and text")
    s.add_argument("--textures", nargs="?", const=True, metavar="NAME_PART",
                   help="with --decode, also write textures as PNG (optionally only names containing NAME_PART)")
    s = add("rebuild", cmd_rebuild, "re-encode records from edited decoded files")
    s.add_argument("folder")
    s = add("pack", cmd_pack, "rebuild an archive from an extracted folder")
    s.add_argument("folder"); s.add_argument("out")
    s.add_argument("--no-rebuild", action="store_true", help="ignore edits to decoded files")
    s = add("diff", cmd_diff, "compare two archives"); s.add_argument("a"); s.add_argument("b"); names_opt(s)
    s = add("tree", cmd_tree, "show a record's chunk tree")
    s.add_argument("archive"); s.add_argument("record", help="index, name or key"); names_opt(s)
    s = add("verify", cmd_verify, "check that archives and decoded records rebuild exactly")
    s.add_argument("target", help="a .dsk file or a folder such as DATAxbox")
    s = add("names", cmd_names, "harvest names from the executable and data files")
    s.add_argument("sources", nargs="+", help="files to scan: default.xbe, .dsk archives, anything")
    s.add_argument("--match", action="append", help="keep only names whose hash appears in this archive")
    s.add_argument("-o", "--out", default="names.txt")
    s = add("model", cmd_model, "export a model to OBJ (with optional preview image)")
    s.add_argument("data", help="DATAxbox folder or a .dsk file")
    s.add_argument("name", help="model name or 8-digit key")
    s.add_argument("-o", "--out", default="models")
    s.add_argument("--lod", type=int, help="level of detail (default: most detailed)")
    s.add_argument("--damaged", action="store_true", help="show damaged body panels instead of pristine")
    s.add_argument("--preview", action="store_true", help="also write a shaded PNG preview")
    s.add_argument("--no-textures", action="store_true", help="don't export textures")
    s.add_argument("--collision", action="store_true", help="also write a preview of the collision shapes")
    s.add_argument("--glb", action="store_true", help="also write a glTF (.glb) with joints, skinning and textures")
    s.add_argument("--anim", action="append", metavar="PATTERN",
                   help="with --glb, add animations whose names match (wildcards ok); repeatable")
    s.add_argument("--in-place", action="store_true", help="drop root motion from animations")
    names_opt(s)
    s = add("model-import", cmd_model_import, "bring an edited OBJ or glTF back into extracted folders")
    s.add_argument("obj", help="edited .obj, .glb or .gltf (from the model command, edited in Blender etc.)")
    s.add_argument("name", help="model name or 8-digit key")
    s.add_argument("folders", nargs="+", help="extract output folders, e.g. work/locked work/streamed")
    s.add_argument("--lod", type=int, default=None,
                   help="level of detail the file was exported at (OBJ default 0; glTF records it)")
    s.add_argument("--damaged", action="store_true", default=None, help="the file was exported with --damaged")
    s.add_argument("--no-collision", action="store_true", help="don't give new pieces collision boxes")
    s = add("mod", None, "create or build a mod project")
    modsub = s.add_subparsers(dest="mod_cmd", required=True)
    m = modsub.add_parser("new", help="create an empty mod project")
    m.set_defaults(fn=cmd_mod_new)
    m.add_argument("folder"); m.add_argument("--name")
    m = modsub.add_parser("build", help="build a mod project into an install-ready folder")
    m.set_defaults(fn=cmd_mod_build)
    m.add_argument("folder", nargs="+", help="mod project folder (several build together, later ones win)")
    m.add_argument("--data", required=True, help="the game's DATAxbox folder")
    m.add_argument("--out", required=True, help="output folder, e.g. <port>/mods/<YourMod>")
    m = modsub.add_parser("pack", help="copy a mod with game-derived files rewritten as changes, for sharing")
    m.set_defaults(fn=cmd_mod_pack)
    m.add_argument("folder", help="mod project folder")
    m.add_argument("out", help="new folder for the shareable copy")
    m.add_argument("--data", required=True, help="the game's DATAxbox folder")
    m.add_argument("--check", action="store_true", help="build both and confirm they give the same result")
    m = modsub.add_parser("sync", help="build the launcher's enabled source mods into mods/Foundry Mods")
    m.set_defaults(fn=cmd_mod_sync)
    m.add_argument("game", help="the folder holding Mercenaries Recompiled.exe and mods/")
    m.add_argument("--data", help="the retail DATAxbox folder (default: the game's own game_files)")
    m.add_argument("--force", action="store_true", help="rebuild even if nothing changed")
    m.add_argument("--enable", action="store_true", help=f"turn on the built mod in the launcher's mod list")
    m.add_argument("--install-hook", action="store_true",
                   help="write foundry-sync.cmd so the launcher syncs before every modded launch")
    s = add("edit", cmd_edit, "open the editor (a local web app) for a mod project")
    s.add_argument("folder", help="mod project folder")
    s.add_argument("--data", required=True, help="the game's DATAxbox folder")
    s.add_argument("--port", type=int, default=8740, help="local port (default 8740)")
    s.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    s = add("texture", cmd_texture, "export a texture to PNG")
    s.add_argument("data", help="DATAxbox folder or a .dsk file")
    s.add_argument("name", help="texture name or 8-digit key")
    s.add_argument("-o", "--out", default="textures")
    s.add_argument("--mip", type=int, default=0, help="mip level (0 = full size)")
    s = add("find", cmd_find, "find objects in the world by name, model, place or property")
    s.add_argument("data", help="DATAxbox folder or a .dsk file (e.g. a mod's assets.dsk)")
    s.add_argument("--name", help="object name (part, or wildcard pattern)")
    s.add_argument("--model", help="model name (part, or wildcard pattern)")
    s.add_argument("--layer", help="world layer (part, or wildcard pattern)")
    s.add_argument("--near", type=float, nargs=3, metavar=("X", "Y", "Z"), help="sort by distance from a point")
    s.add_argument("--near-name", help="sort by distance from a named object, e.g. loc_drop_start")
    s.add_argument("--radius", type=float, help="only within this many metres (default 50 with --near)")
    s.add_argument("--property", action="append", metavar="KEY[=VALUE]",
                   help="has this property (optionally with a matching value); repeatable")
    s.add_argument("--limit", type=int, default=200, help="most results to show (default 200, 0 = all)")
    s.add_argument("--json", action="store_true", help="print matches as JSON")
    s.add_argument("--rule", action="store_true", help="print a mod rule for each match, ready to edit")
    s.add_argument("--layout", action="store_true",
                   help="search paths, boundaries, regions and encounters instead of objects")
    names_opt(s)
    s = add("animtable", cmd_animtable, "export a character's animation table (handle -> clip) as JSON")
    s.add_argument("data", help="DATAxbox folder or a .dsk file")
    s.add_argument("name", help="table name, e.g. player_american (list them with list --type animtable)")
    s.add_argument("-o", "--out", default="animtables")
    s = add("layout", cmd_layout, "export a layer's paths, boundaries, regions, encounters and load list")
    s.add_argument("data", help="DATAxbox folder or a .dsk file")
    s.add_argument("layer", help="world layer name, e.g. swn_allies0")
    s.add_argument("-o", "--out", default="layout")
    s = add("unpack-iso", cmd_unpack_iso, "extract an Xbox disc image (.iso or .xiso)")
    s.add_argument("image"); s.add_argument("out")
    s = add("funcmap", None, "the function map: build it, search it, export it")
    fsub = s.add_subparsers(dest="map_cmd", required=True)
    f = fsub.add_parser("build", help="build map/functions.json from your default.xbe and the port's analysis")
    f.add_argument("--xbe", default="game_files/default.xbe")
    f.add_argument("--analysis", default=PORT_ANALYSIS, help="the port's analysis/disasm folder")
    f.add_argument("--manual", default="map/manual.json")
    f.add_argument("-o", "--out", default="map/functions.json")
    f.set_defaults(fn=cmd_funcmap)
    f = fsub.add_parser("find", help="search names, script commands, source files and notes")
    f.add_argument("text")
    f.add_argument("--limit", type=int, default=40)
    f.add_argument("--map", default="map/functions.json")
    f.set_defaults(fn=cmd_funcmap)
    f = fsub.add_parser("show", help="one function: evidence, source file, callers and callees")
    f.add_argument("what", help="name (e.g. Lua_Mission_Spawn) or address")
    f.add_argument("--map", default="map/functions.json")
    f.add_argument("--analysis", default=PORT_ANALYSIS)
    f.set_defaults(fn=cmd_funcmap)
    f = fsub.add_parser("header", help="C header with an address constant per named function")
    f.add_argument("-o", "--out", default="mercfoundry_functions.h")
    f.add_argument("--map", default="map/functions.json")
    f.set_defaults(fn=cmd_funcmap)
    f = fsub.add_parser("names", help="{address: name} JSON for the port's recompiler (ghidra_names.json format)")
    f.add_argument("-o", "--out", default="ghidra_names.json")
    f.add_argument("--map", default="map/functions.json")
    f.set_defaults(fn=cmd_funcmap)
    s = add("port-hooks", cmd_port_hooks, "make the map's named functions hookable by plugins in the port's generated code")
    s.add_argument("--gen", default=PORT_GEN, help="the port's src/recomp/gen folder")
    s.add_argument("--map", default="map/functions.json")
    s.add_argument("--only", nargs="+", metavar="NAME_OR_ADDRESS", help="just these functions")
    s.add_argument("--remove", action="store_true", help="restore the generated code")
    s = add("objectnames", cmd_objectnames, "list every world object name (for tools that show objects by name)")
    s.add_argument("data", help="DATAxbox folder or a .dsk file")
    s.add_argument("-o", "--out", default="objectnames.txt")
    s = add("templates", cmd_templates, "list the game's object templates (what Spawner_SpawnTemplate spawns) as TSV")
    s.add_argument("data", help="DATAxbox folder or a .dsk file")
    s.add_argument("-o", "--out", default="templates.tsv")
    s = add("vehicles", cmd_vehicles, "list vehicle models, or export one's handling (physics*) as a mod object rule")
    s.add_argument("data", help="DATAxbox folder or a .dsk file")
    s.add_argument("model", nargs="?", help="vehicle model, e.g. allies_veh_humvee")
    s.add_argument("-o", "--out", help="write the rule here (e.g. a mod's objects/humvee.json)")
    s = add("terrain", cmd_terrain, "export a terrain's heightmap (16-bit PNG) and ground layers (greyscale PNGs) for a mod's terrain/ folder")
    s.add_argument("data", help="DATAxbox folder or assets.dsk")
    s.add_argument("name", help="terrain name, e.g. sw (the southern province) or nw")
    s.add_argument("-o", "--out", default="terrain")
    s = add("hash", cmd_hash, "hash names the way the game does"); s.add_argument("names", nargs="+")

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
