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
    m = dsk.extract(args.archive, args.out, _names(args), decode=args.decode)
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
    textures = {}
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
                            (out / f"{tex_name(r.key)}.png").write_bytes(pngmod.encode(w, h, px, 4))
                        except ValueError as err:
                            print(f"  skipped texture {tex_name(r.key)}: {err}")
        missing = len(wanted - set(textures))
        print(f"wrote {len(textures)} textures" + (f" ({missing} not found)" if missing else ""))
    if args.preview:
        png = out / f"{m.name}_preview.png"
        png.write_bytes(model.preview_png(m, lod, damaged=args.damaged, textures=textures))
        print(f"wrote {png}")


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


def cmd_unpack_iso(args):
    from . import xiso
    n = xiso.extract(args.image, args.out)
    print(f"extracted {n} files to {args.out}")


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
    names_opt(s)
    s = add("texture", cmd_texture, "export a texture to PNG")
    s.add_argument("data", help="DATAxbox folder or a .dsk file")
    s.add_argument("name", help="texture name or 8-digit key")
    s.add_argument("-o", "--out", default="textures")
    s.add_argument("--mip", type=int, default=0, help="mip level (0 = full size)")
    s = add("unpack-iso", cmd_unpack_iso, "extract an Xbox disc image (.iso or .xiso)")
    s.add_argument("image"); s.add_argument("out")
    s = add("hash", cmd_hash, "hash names the way the game does"); s.add_argument("names", nargs="+")

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
