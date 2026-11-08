import struct
import tempfile
import unittest
from pathlib import Path

from mercfoundry import names, script, verify, xiso
from mercfoundry.hashing import name_hash
from tests.test_decoders import text_record, world_record
from tests.test_dsk import build


def sample(path):
    build(path, [
        (name_hash("mission"), name_hash("script"),
         script.encode(script.new("mission", "Spawn('jeep_template')\n"))),
        (name_hash("base_layer"), name_hash("world"), world_record()),
        (name_hash("english"), name_hash("registry"), text_record()),
        (name_hash("hidden_asset"), name_hash("texture"), b"\x00" * 8),
    ])


class VerifyTests(unittest.TestCase):
    def test_clean_archive_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.dsk"
            sample(path)
            rep = verify.verify_archive(path)
            self.assertTrue(rep.ok, rep.failures)
            self.assertEqual(dict(rep.checked), {"script": 1, "world": 1, "text": 1})

    def test_broken_record_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.dsk"
            build(path, [(1, name_hash("world"), b"not a chunk tree")])
            rep = verify.verify_archive(path)
            self.assertFalse(rep.ok)
            self.assertEqual(rep.failures[0][:2], (0, "world"))


class NameTests(unittest.TestCase):
    def test_strings_and_path_pieces(self):
        found = set(names.strings_in(b"\x00\x01" + rb"data\models\jeep_chinese.msh" + b"\x00ok\x00Spawn Tank\x00"))
        want = {r"data\models\jeep_chinese.msh", "jeep_chinese.msh", "jeep_chinese", "Spawn", "Tank"}
        self.assertLessEqual(want, found)
        self.assertNotIn("ok", found)

    def test_harvest_matches_archive_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            sample(d / "a.dsk")
            (d / "default.xbe").write_bytes(b"\x00junk\x00hidden_asset\x00texture\x00unrelated_name\x00")
            wanted = {name_hash(n) for n in ("hidden_asset", "texture", "jeep_template")}
            book = names.harvest([d / "default.xbe", d / "a.dsk"], wanted)
            got = {n for _, n in book.items()}
            # jeep_template is only inside the compressed script and the world table.
            self.assertEqual(got, {"hidden_asset", "texture", "jeep_template"})

    def test_type_guesses(self):
        self.assertEqual(names.guess_types([name_hash("texture"), 123]), {name_hash("texture"): "texture"})


def dir_entry(left, right, sector, size, attr, name):
    e = struct.pack("<HHIIBB", left, right, sector, size, attr, len(name)) + name
    return e + b"\xff" * (-len(e) % 4)


def tiny_image():
    """Root holds default.xbe (right child: folder DATA, holding a.dsk)."""
    S = xiso.SECTOR
    img = bytearray(S * 40)
    first_len = len(dir_entry(0, 0, 0, 0, 0, b"default.xbe"))
    root = dir_entry(0, first_len // 4, 36, 4, 0x20, b"default.xbe") + dir_entry(0, 0, 37, S, 0x10, b"DATA")
    sub = dir_entry(0, 0, 38, 5, 0x20, b"a.dsk")
    img[32 * S:32 * S + 28] = xiso.MAGIC + struct.pack("<II", 35, len(root))
    img[35 * S:35 * S + len(root)] = root
    img[36 * S:36 * S + 4] = b"XBEH"
    img[37 * S:38 * S] = b"\xff" * S
    img[37 * S:37 * S + len(sub)] = sub
    img[38 * S:38 * S + 5] = b"hello"
    return bytes(img)


class XisoTests(unittest.TestCase):
    def test_list_and_extract(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "game.iso").write_bytes(tiny_image())
            with xiso.Image(d / "game.iso") as img:
                paths = sorted(e.path for e in img.entries())
            self.assertEqual(paths, ["DATA", "DATA/a.dsk", "default.xbe"])
            self.assertEqual(xiso.extract(d / "game.iso", d / "out"), 2)
            self.assertEqual((d / "out" / "DATA" / "a.dsk").read_bytes(), b"hello")
            self.assertEqual((d / "out" / "default.xbe").read_bytes(), b"XBEH")

    def test_not_an_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.iso"
            p.write_bytes(b"\x00" * 100000)
            with self.assertRaises(ValueError):
                xiso.Image(p)


if __name__ == "__main__":
    unittest.main()
