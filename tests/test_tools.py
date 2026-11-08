import tempfile
import unittest
from pathlib import Path

from mercfoundry import names, script, verify
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


if __name__ == "__main__":
    unittest.main()
