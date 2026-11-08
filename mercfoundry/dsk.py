"""Read, extract, repack and compare Mercenaries .dsk archives.

Layout (all little-endian u32):
    count, extra
    count x (size, key, kind)        directory, 12 bytes per record
    record data, back to back, in directory order
    tail                              optional; kept byte-for-byte

`extra` is an optional offset to grid data, not a version. The tail is either
empty, a 2048-byte pad, or grid-index data (starts with 0x00FF8040). We don't
decode the tail yet, so it is always preserved exactly on repack.
See docs/formats/dsk.md.
"""
import hashlib
import json
import os
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from .hashing import NameBook, type_book
from .ucfb import record_name

HEADER = struct.Struct("<II")
ENTRY = struct.Struct("<III")
GRID_MAGIC = 0x00FF8040
MANIFEST = "manifest.json"
COPY_BLOCK = 1 << 20


@dataclass
class Record:
    index: int
    size: int
    key: int
    kind: int
    offset: int  # absolute offset of the data in the archive

    @property
    def ident(self):
        return (self.key, self.kind)


class Archive:
    def __init__(self, path):
        self.path = Path(path)
        self.file_size = self.path.stat().st_size
        with self.path.open("rb") as f:
            head = f.read(HEADER.size)
            if len(head) != HEADER.size:
                raise ValueError(f"{self.path}: truncated header")
            self.count, self.extra = HEADER.unpack(head)
            if self.count > (self.file_size - HEADER.size) // ENTRY.size:
                raise ValueError(f"{self.path}: directory count {self.count} too large")
            table = f.read(self.count * ENTRY.size)
        self.records = []
        offset = HEADER.size + self.count * ENTRY.size
        seen = set()
        for i in range(self.count):
            size, key, kind = ENTRY.unpack_from(table, i * ENTRY.size)
            rec = Record(i, size, key, kind, offset)
            offset += size
            if offset > self.file_size:
                raise ValueError(f"{self.path}: record {i} runs past end of file")
            if rec.ident in seen:
                raise ValueError(f"{self.path}: duplicate key/kind at record {i}")
            seen.add(rec.ident)
            self.records.append(rec)
        self.data_end = offset
        self.tail_size = self.file_size - offset

    def read(self, rec: Record) -> bytes:
        with self.path.open("rb") as f:
            f.seek(rec.offset)
            return f.read(rec.size)

    def read_tail(self) -> bytes:
        with self.path.open("rb") as f:
            f.seek(self.data_end)
            return f.read()

    def tail_kind(self) -> str:
        if not self.tail_size:
            return "none"
        tail = self.read_tail()
        if len(tail) >= 4 and struct.unpack_from("<I", tail)[0] == GRID_MAGIC:
            return "grid"
        if len(tail) == 2048 and not any(tail[:-4]):
            return "padding"
        return "unknown"

    def by_ident(self):
        return {r.ident: r for r in self.records}


def _copy_range(src, dst, offset, size):
    src.seek(offset)
    left = size
    while left:
        block = src.read(min(left, COPY_BLOCK))
        if not block:
            raise ValueError("unexpected end of file while copying")
        dst.write(block)
        left -= len(block)


def label(book: NameBook, value: int) -> str:
    return book.get(value) or f"{value:08X}"


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)[:80] or "unnamed"


def extract(archive_path, out_dir, names: NameBook = None, decode: bool = False,
            textures: bool = False) -> dict:
    """Write every record to its own file plus a manifest for exact repacking.

    With decode=True, scripts, world layers and text also get a readable copy
    in decoded/ (and textures as PNG with textures=True). Edit those, then run
    rebuild() before pack().
    """
    from . import codecs
    arc = Archive(archive_path)
    names = names or NameBook()
    types = type_book()
    out = Path(out_dir)
    (out / "records").mkdir(parents=True, exist_ok=True)
    entries = []
    with arc.path.open("rb") as src:
        for rec in arc.records:
            src.seek(rec.offset)
            payload = src.read(rec.size)
            found = record_name(payload)
            if found:
                names.add(found)
            name = names.get(rec.key)
            kind_name = types.get(rec.kind)
            fname = f"{rec.index:05d}_{_safe(kind_name or f'{rec.kind:08X}')}_{_safe(name or f'{rec.key:08X}')}.bin"
            (out / "records" / fname).write_bytes(payload)
            entry = {
                "index": rec.index,
                "key": f"{rec.key:08X}",
                "kind": f"{rec.kind:08X}",
                "name": name,
                "type": kind_name,
                "size": rec.size,
                "file": f"records/{fname}",
            }
            if decode:
                try:
                    want = textures is True or (isinstance(textures, str) and name
                                                and textures.lower() in name.lower())
                    readable = codecs.decode(rec.kind, payload, bool(want))
                except (ValueError, KeyError, UnicodeError, struct.error) as err:
                    entry["decode_error"] = str(err)
                    readable = None
                if readable:
                    suffix, data = readable
                    dname = f"decoded/{fname[:-4]}{suffix}"
                    (out / "decoded").mkdir(exist_ok=True)
                    (out / dname).write_bytes(data)
                    entry["decoded"] = dname
                    entry["decoded_sha1"] = hashlib.sha1(data).hexdigest()
            entries.append(entry)
        tail_file = None
        if arc.tail_size:
            tail_file = "tail.bin"
            with (out / tail_file).open("wb") as dst:
                _copy_range(src, dst, arc.data_end, arc.tail_size)
    manifest = {
        "format": "mercfoundry-dsk-1",
        "source": arc.path.name,
        "extra": arc.extra,
        "tail": {"kind": arc.tail_kind(), "size": arc.tail_size, "file": tail_file},
        "records": entries,
    }
    (out / MANIFEST).write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


def pack(src_dir, out_path) -> int:
    """Rebuild an archive from an extracted folder. Returns the record count.

    Records are written in manifest order. Edit a record by replacing its file;
    sizes are re-measured. Add one by appending an entry with key/kind (or a
    "name"/"type" to hash). The `extra` word and tail are kept as recorded.
    """
    from .hashing import name_hash
    src = Path(src_dir)
    manifest = json.loads((src / MANIFEST).read_text(encoding="utf-8"))
    if manifest.get("format") != "mercfoundry-dsk-1":
        raise ValueError("not a mercfoundry dsk manifest")
    rows = []
    seen = set()
    for e in manifest["records"]:
        key = int(e["key"], 16) if e.get("key") else name_hash(e["name"])
        kind = int(e["kind"], 16) if e.get("kind") else name_hash(e["type"])
        path = src / e["file"]
        size = path.stat().st_size
        if (key, kind) in seen:
            raise ValueError(f"duplicate key/kind for {e['file']}")
        seen.add((key, kind))
        rows.append((size, key, kind, path))
    tail = manifest.get("tail") or {}
    tail_path = src / tail["file"] if tail.get("file") else None
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    with tmp.open("wb") as dst:
        dst.write(HEADER.pack(len(rows), manifest.get("extra", 0)))
        for size, key, kind, _ in rows:
            dst.write(ENTRY.pack(size, key, kind))
        for size, _, _, path in rows:
            with path.open("rb") as f:
                _copy_range(f, dst, 0, size)
        if tail_path:
            with tail_path.open("rb") as f:
                _copy_range(f, dst, 0, tail_path.stat().st_size)
    os.replace(tmp, out)
    return len(rows)


def rebuild(src_dir) -> list:
    """Re-encode records whose decoded/ file was edited. Returns their files.

    The first time a record is replaced, its original is kept in originals/.
    If a decoded file is later put back exactly as extracted, the original
    record is restored rather than re-encoded.
    """
    from . import codecs
    src = Path(src_dir)
    manifest_path = src / MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    changed, dirty = [], False
    for e in manifest["records"]:
        if not e.get("decoded"):
            continue
        rec_path, dec_path = src / e["file"], src / e["decoded"]
        if not dec_path.exists():
            continue
        data = dec_path.read_bytes()
        digest = hashlib.sha1(data).hexdigest()
        original = src / "originals" / Path(e["file"]).name
        if digest == e.get("decoded_sha1"):
            if original.exists():
                os.replace(original, rec_path)
                e.pop("rebuilt_sha1", None)
                changed.append(e["file"])
                dirty = True
            continue
        if digest == e.get("rebuilt_sha1"):
            continue
        base = original.read_bytes() if original.exists() else rec_path.read_bytes()
        try:
            new = codecs.encode(int(e["kind"], 16), data, base,
                                check_unchanged="decoded_sha1" not in e)
        except (ValueError, KeyError, UnicodeError, struct.error) as err:
            raise ValueError(f"{e['decoded']}: {err}") from err
        if new == base and not original.exists():
            continue
        if not original.exists():
            original.parent.mkdir(exist_ok=True)
            original.write_bytes(base)
        rec_path.write_bytes(new)
        if "decoded_sha1" in e:
            e["rebuilt_sha1"] = digest
            dirty = True
        changed.append(e["file"])
    if dirty:
        manifest_path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return changed


def diff(a_path, b_path):
    """Compare two archives by (key, kind). Returns (added, removed, changed)."""
    a, b = Archive(a_path), Archive(b_path)
    ma, mb = a.by_ident(), b.by_ident()
    added = [mb[i] for i in mb if i not in ma]
    removed = [ma[i] for i in ma if i not in mb]
    changed = []
    for ident in ma.keys() & mb.keys():
        ra, rb = ma[ident], mb[ident]
        if ra.size != rb.size or a.read(ra) != b.read(rb):
            changed.append(rb)
    changed.sort(key=lambda r: r.index)
    return added, removed, changed


def collect_names(archive: Archive, book: NameBook) -> int:
    """Harvest record names from NAME chunks. Returns how many were added."""
    before = len(book)
    with archive.path.open("rb") as f:
        for rec in archive.records:
            f.seek(rec.offset)
            found = record_name(f.read(rec.size))
            if found:
                book.add(found)
    return len(book) - before


def write_modified(archive: Archive, out_path, replace: dict = None, add=()) -> int:
    """Copy an archive, swapping in new bytes for some records.

    replace: {(key, kind): bytes}; add: [(key, kind, bytes)] appended at the
    end. Unchanged records are copied straight from the source file. The
    extra word and tail are kept. Returns the record count."""
    replace = replace or {}
    missing = set(replace) - {r.ident for r in archive.records}
    if missing:
        raise KeyError(f"records to replace aren't in {archive.path.name}: {len(missing)}")
    known = {r.ident for r in archive.records}
    for key, kind, _ in add:
        if (key, kind) in known:
            raise ValueError(f"record {key:08X}/{kind:08X} already exists in {archive.path.name}")
        known.add((key, kind))
    rows = [(replace.get(r.ident), r) for r in archive.records]
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    with archive.path.open("rb") as src, tmp.open("wb") as dst:
        dst.write(HEADER.pack(len(rows) + len(add), archive.extra))
        for data, r in rows:
            dst.write(ENTRY.pack(len(data) if data is not None else r.size, r.key, r.kind))
        for key, kind, data in add:
            dst.write(ENTRY.pack(len(data), key, kind))
        for data, r in rows:
            if data is None:
                _copy_range(src, dst, r.offset, r.size)
            else:
                dst.write(data)
        for _, _, data in add:
            dst.write(data)
        if archive.tail_size:
            _copy_range(src, dst, archive.data_end, archive.tail_size)
    os.replace(tmp, out)
    return len(rows) + len(add)
