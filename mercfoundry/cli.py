"""Command line: python -m mercfoundry <command> ..."""
import argparse
import collections
import sys
from pathlib import Path

from . import dsk, ucfb
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
    for kind, n in counts.most_common():
        print(f"  {dsk.label(types, kind):>10}  {n:6d} records  {sizes[kind]:12,d} bytes")


def cmd_list(args):
    arc = dsk.Archive(args.archive)
    names = _names(args)
    dsk.collect_names(arc, names)
    types = type_book()
    want = None
    if args.type:
        want = int(args.type, 16) if all(c in "0123456789abcdefABCDEF" for c in args.type) and len(args.type) == 8 else name_hash(args.type)
    for r in arc.records:
        if want is not None and r.kind != want:
            continue
        print(f"{r.index:5d}  {r.key:08X}  {dsk.label(types, r.kind):>10}  {r.size:10d}  {names.get(r.key) or ''}")


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
    s = add("hash", cmd_hash, "hash names the way the game does"); s.add_argument("names", nargs="+")

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
