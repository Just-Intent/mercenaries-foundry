import json
import struct
import tempfile
import unittest
from pathlib import Path

from mercfoundry import dsk, ucfb
from mercfoundry.hashing import name_hash


def chunk(tag: bytes, data: bytes) -> bytes:
    body = struct.pack("<4sI", tag, len(data)) + data
    return body + b"\0" * (-len(body) % 4)


def script_record(name: str, text: bytes) -> bytes:
    inner = chunk(b"NAME", name.encode() + b"\0") + chunk(b"INFO", b"\0" * 5) + chunk(b"BODY", text)
    return chunk(b"ucfb", chunk(b"scr_", inner))


def build(path: Path, records, extra=0, tail=b""):
    out = struct.pack("<II", len(records), extra)
    for key, kind, data in records:
        out += struct.pack("<III", len(data), key, kind)
    for _, _, data in records:
        out += data
    path.write_bytes(out + tail)


class HashTests(unittest.TestCase):
    def test_known_type_names(self):
        self.assertEqual(name_hash("script"), 0x203E6FAA)
        self.assertEqual(name_hash("world"), 0x37A3E893)
        self.assertEqual(name_hash("registry"), 0x3884598E)

    def test_case_folded(self):
        self.assertEqual(name_hash("Mission"), name_hash("mission"))
        self.assertEqual(name_hash(""), 0)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.script = name_hash("script")
        self.recs = [
            (name_hash("mission"), self.script, script_record("mission", b"print('hi')")),
            (0x12345678, 0xDEADBEEF, b"\x01\x02\x03"),
        ]

    def tearDown(self):
        self.tmp.cleanup()

    def roundtrip(self, **kw):
        src = self.dir / "a.dsk"
        build(src, self.recs, **kw)
        dsk.extract(src, self.dir / "x")
        dsk.pack(self.dir / "x", self.dir / "b.dsk")
        self.assertEqual(src.read_bytes(), (self.dir / "b.dsk").read_bytes())
        return json.loads((self.dir / "x" / dsk.MANIFEST).read_text())

    def test_roundtrip_plain(self):
        m = self.roundtrip()
        self.assertEqual(m["records"][0]["name"], "mission")
        self.assertEqual(m["records"][0]["type"], "script")
        self.assertEqual(m["tail"]["kind"], "none")

    def test_roundtrip_padding_with_signature(self):
        m = self.roundtrip(tail=b"\0" * 2044 + b"\xAA\xBB\xCC\xDD")
        self.assertEqual(m["tail"]["kind"], "padding")

    def test_roundtrip_grid_tail_and_extra(self):
        m = self.roundtrip(extra=0x40, tail=struct.pack("<I", dsk.GRID_MAGIC) + b"griddata")
        self.assertEqual(m["tail"]["kind"], "grid")
        self.assertEqual(m["extra"], 0x40)

    def test_edit_and_add_record(self):
        src = self.dir / "a.dsk"
        build(src, self.recs)
        m = dsk.extract(src, self.dir / "x")
        (self.dir / "x" / m["records"][1]["file"]).write_bytes(b"changed!")
        (self.dir / "x" / "records" / "new.bin").write_bytes(script_record("added", b"x"))
        m["records"].append({"name": "added", "type": "script", "file": "records/new.bin"})
        (self.dir / "x" / dsk.MANIFEST).write_text(json.dumps(m))
        dsk.pack(self.dir / "x", self.dir / "b.dsk")
        added, removed, changed = dsk.diff(src, self.dir / "b.dsk")
        self.assertEqual([r.key for r in added], [name_hash("added")])
        self.assertEqual(removed, [])
        self.assertEqual([r.key for r in changed], [0x12345678])

    def test_rejects_overrun(self):
        bad = self.dir / "bad.dsk"
        bad.write_bytes(struct.pack("<II", 1, 0) + struct.pack("<III", 999, 1, 2))
        with self.assertRaises(ValueError):
            dsk.Archive(bad)

    def test_rejects_duplicates(self):
        bad = self.dir / "dup.dsk"
        build(bad, [(1, 2, b"a"), (1, 2, b"b")])
        with self.assertRaises(ValueError):
            dsk.Archive(bad)


class ChunkTests(unittest.TestCase):
    def test_tree_and_name(self):
        data = script_record("mission", b"\x00\x01binary")
        self.assertEqual(ucfb.record_name(data), "mission")
        tree = ucfb.parse(data)
        self.assertEqual(tree[0].name, "ucfb")
        self.assertEqual(tree[0].children[0].name, "scr_")
        self.assertIsNotNone(tree[0].children[0].child(b"BODY"))

    def test_not_ucfb(self):
        self.assertIsNone(ucfb.record_name(b"\x01\x02\x03"))


if __name__ == "__main__":
    unittest.main()
