import json
import tempfile
import unittest
from pathlib import Path

from mercfoundry import dsk, model, modbuild, png, registry, script, texture, world
from mercfoundry.hashing import name_hash
from tests.test_decoders import text_record, world_record
from tests.test_dsk import build
from tests.test_model_import import edit_obj, sample
from tests.test_texture import texture_record

SCRIPT, WORLD, REGISTRY = name_hash("script"), name_hash("world"), name_hash("registry")


def red(w, h):
    return bytes((255, 0, 0, 255)) * (w * h)


class ModBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.data, self.project, self.out = d / "DATAxbox", d / "proj", d / "out"
        self.data.mkdir()
        mr, sr = sample()
        self.mr, self.sr = mr, sr
        tex = texture.encode(texture.replace_image(
            texture.decode(texture_record("tex", 8, 8, 1, texture.DXT1, b"\0" * 32)), 8, 8, red(8, 8)))
        build(self.data / "assets.dsk", [
            (name_hash("mission"), SCRIPT, script.encode(script.new("mission", "x = 1\n"))),
            (name_hash("base_layer"), WORLD, world_record()),
        ])
        build(self.data / "english.dsk", [(name_hash("english"), REGISTRY, text_record())])
        build(self.data / "streamed.dsk", [
            (name_hash("sample"), model.MODEL, mr), (name_hash("sample"), model.SEGMENTS, sr),
            (name_hash("tex"), texture.TEXTURE, tex),
        ])
        build(self.data / "locked.dsk", [(name_hash("sample"), model.MODEL, mr),
                                         (name_hash("sample"), model.SEGMENTS, sr)])
        modbuild.new_project(self.project, "Test")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, data):
        p = self.project / rel
        p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))

    def records(self, name):
        arc = dsk.Archive(self.out / name)
        return {r.ident: arc.read(r) for r in arc.records}

    def test_empty_project_writes_nothing(self):
        result = modbuild.build(self.project, self.data, self.out)
        self.assertEqual(result.written, [])
        self.assertTrue((self.out / "foundry-mod.json").exists())

    def test_everything(self):
        self.write("text/t.json", json.dumps({"veh.jeep": "Buggy", "veh.boat": "Boat"}))
        self.write("scripts/mission.lua", "x = 2\r\n")
        self.write("scripts/brand_new.lua", "y = 1\n")
        self.write("objects/o.json", json.dumps({"template": "jeep_template",
                                                 "set": {"Faction": "Allies", "hitPoints": "999"}}))
        self.write("textures/tex.png", png.encode(8, 8, bytes((0, 0, 255, 255)) * 64, 4))
        self.write("textures/new_tex.png", png.encode(4, 4, bytes((0, 255, 0, 128)) * 16, 4))
        obj, _ = model.to_obj(model.decode(self.mr, self.sr), 0)
        self.write("models/sample.obj", edit_obj(obj, lambda n, x, y, z: (x, y + 1, z) if n.startswith("p000") else (x, y, z)))

        result = modbuild.build(self.project, self.data, self.out)
        self.assertEqual(sorted(result.written), ["assets.dsk", "english.dsk", "locked.dsk", "streamed.dsk"])

        strings = registry.Registry(self.records("english.dsk")[(name_hash("english"), REGISTRY)]).strings()
        self.assertEqual((strings["veh.jeep"], strings["veh.boat"]), ("Buggy", "Boat"))

        assets = self.records("assets.dsk")
        self.assertEqual(script.decode(assets[(name_hash("mission"), SCRIPT)]).source, "x = 2\n")
        self.assertEqual(script.decode(assets[(name_hash("brand_new"), SCRIPT)]).source, "y = 1\n")
        inst = [c for c in world.to_json(assets[(name_hash("base_layer"), WORLD)])["chunks"] if c["tag"] == "inst"][0]
        self.assertIn(["Faction", "Allies"], inst["properties"])
        self.assertIn(["hitPoints", "999"], inst["properties"])

        streamed = self.records("streamed.dsk")
        self.assertEqual(texture.decode(streamed[(name_hash("tex"), texture.TEXTURE)]).rgba()[2][:4], bytes((0, 0, 255, 255)))
        new_tex = texture.decode(streamed[(name_hash("new_tex"), texture.TEXTURE)])
        self.assertEqual((new_tex.name, new_tex.format_name), ("new_tex", "DXT3"))
        for archive in ("streamed.dsk", "locked.dsk"):
            recs = self.records(archive)
            m = model.decode(recs[(name_hash("sample"), model.MODEL)], recs[(name_hash("sample"), model.SEGMENTS)])
            self.assertAlmostEqual(m.pieces[0].positions[0][1], 1.0, places=3)  # moved up from 0
            self.assertNotEqual(recs[(name_hash("sample"), model.SEGMENTS)], self.sr)

        summary = json.loads((self.out / "foundry-mod.json").read_text())
        self.assertEqual(summary["name"], "Test")

    def test_only_changed_archives_and_cleanup(self):
        self.write("text/t.json", json.dumps({"veh.jeep": "Buggy"}))
        modbuild.build(self.project, self.data, self.out)
        self.assertEqual(sorted(p.name for p in self.out.glob("*.dsk")), ["english.dsk"])
        (self.project / "text" / "t.json").unlink()
        self.write("scripts/mission.lua", "x = 3\n")
        modbuild.build(self.project, self.data, self.out)
        self.assertEqual(sorted(p.name for p in self.out.glob("*.dsk")), ["assets.dsk"])

    def test_unchanged_values_write_nothing(self):
        self.write("text/t.json", json.dumps({"veh.jeep": "Jeep"}))
        self.write("objects/o.json", json.dumps({"template": "jeep_template", "set": {"Faction": "China"}}))
        self.assertEqual(modbuild.build(self.project, self.data, self.out).written, [])

    def test_errors(self):
        self.write("objects/o.json", json.dumps({"template": "nothing_like_this", "set": {"a": "b"}}))
        with self.assertRaises(ValueError):
            modbuild.build(self.project, self.data, self.out)
        (self.project / "objects" / "o.json").unlink()
        self.write("models/missing.obj", "o p000\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
        with self.assertRaises(ValueError):
            modbuild.build(self.project, self.data, self.out)

    def test_write_modified_keeps_untouched_records(self):
        arc = dsk.Archive(self.data / "streamed.dsk")
        rec = arc.records[2]
        dsk.write_modified(arc, self.out / "x.dsk", {rec.ident: b"new"}, [(1, 2, b"added")])
        out = dsk.Archive(self.out / "x.dsk")
        self.assertEqual(out.read(out.records[0]), arc.read(arc.records[0]))
        self.assertEqual(out.read(out.records[2]), b"new")
        self.assertEqual(out.read(out.records[3]), b"added")
        with self.assertRaises(ValueError):
            dsk.write_modified(arc, self.out / "y.dsk", add=[(rec.key, rec.kind, b"dup")])


if __name__ == "__main__":
    unittest.main()
