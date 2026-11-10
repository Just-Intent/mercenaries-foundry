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

    def test_requires_newer_toolkit(self):
        meta = json.loads((self.project / "mod.json").read_text(encoding="utf-8"))
        (self.project / "mod.json").write_text(json.dumps({**meta, "requires": "99.0"}), encoding="utf-8")
        with self.assertRaises(ValueError) as cm:
            modbuild.build(self.project, self.data, self.out)
        self.assertIn("needs Mercenaries Foundry 99.0 or newer", str(cm.exception))
        (self.project / "mod.json").write_text(json.dumps({**meta, "requires": "0.1"}), encoding="utf-8")
        modbuild.build(self.project, self.data, self.out)

    def test_remove_object(self):
        self.write("objects/o.json", json.dumps({"template": "jeep_template", "remove": True}))
        result = modbuild.build(self.project, self.data, self.out)
        self.assertIn("objects jeep_template: 1 instances removed", result.changes)
        self.assertTrue(any("scripts that use it by name" in w for w in result.warnings))
        doc = world.to_json(self.records("assets.dsk")[(name_hash("base_layer"), WORLD)])
        # Its place in the layer is kept (saved games know objects by place),
        # as an invisible, unnamed location marker.
        insts = [c for c in doc["chunks"] if c.get("tag") == "inst"]
        self.assertEqual([(c["model"], c["template"]) for c in insts], [("global_location", "")])
        self.assertIn(["objectType", "location"], insts[0]["properties"])
        self.write("objects/o.json", json.dumps({"template": "jeep_template", "remove": True, "turn": 5}))
        with self.assertRaises(ValueError):
            modbuild.build(self.project, self.data, self.out)

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


class SeveralModsTests(ModBuildTests):
    """Mods built together, in load order."""

    def second(self):
        p = Path(self.tmp.name) / "proj2"
        modbuild.new_project(p, "Second")
        return p

    def test_text_from_both_mods(self):
        self.write("text/t.json", json.dumps({"veh.jeep": "Buggy"}))
        p2 = self.second()
        (p2 / "text" / "t.json").write_text(json.dumps({"veh.boat": "Raft"}), encoding="utf-8")
        result = modbuild.build([self.project, p2], self.data, self.out, name="Both")
        strings = registry.Registry(self.records("english.dsk")[(name_hash("english"), REGISTRY)]).strings()
        self.assertEqual((strings["veh.jeep"], strings["veh.boat"]), ("Buggy", "Raft"))
        self.assertFalse([w for w in result.warnings if w.startswith("conflict")])
        meta = json.loads((self.out / "foundry-mod.json").read_text(encoding="utf-8"))
        self.assertEqual((meta["name"], meta["sources"]), ("Both", ["Test", "Second"]))

    def test_same_script_replaced_is_a_conflict(self):
        self.write("scripts/mission.lua", "x = 2\n")
        p2 = self.second()
        (p2 / "scripts" / "mission.lua").write_text("x = 3\n", encoding="utf-8")
        result = modbuild.build([self.project, p2], self.data, self.out)
        rec = self.records("assets.dsk")[(name_hash("mission"), SCRIPT)]
        self.assertEqual(script.decode(rec).source, "x = 3\n")
        conflicts = [w for w in result.warnings if w.startswith("conflict")]
        self.assertEqual(len(conflicts), 1)
        self.assertIn("Second replaces", conflicts[0])
        self.assertIn("Test", conflicts[0])

    def test_same_new_script_is_an_error(self):
        self.write("scripts/brand_new.lua", "y = 1\n")
        p2 = self.second()
        (p2 / "scripts" / "brand_new.lua").write_text("y = 2\n", encoding="utf-8")
        with self.assertRaises(ValueError) as cm:
            modbuild.build([self.project, p2], self.data, self.out)
        self.assertIn("both add", str(cm.exception))


class SyncTests(unittest.TestCase):
    def test_read_loader_utf16_and_sources(self):
        from mercfoundry import sync
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            text = ("[Loader]\r\nCount=3\r\n\r\n[Mod.1]\r\nFolder=\"B\"\r\nEnabled=0\r\n\r\n"
                    "[Mod.0]\r\nFolder=\"A Mod\"\r\nEnabled=1\r\n\r\n[Mod.2]\r\nFolder=\"Built\"\r\nEnabled=1\r\n")
            (game / "mod-loader.ini").write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))
            mods = sync.read_loader(game)
            self.assertEqual([(m.folder, m.enabled) for m in mods], [("A Mod", True), ("B", False), ("Built", True)])
            src = game / "mods" / "A Mod"
            modbuild.new_project(src, "A Mod")
            built = game / "mods" / "Built"
            built.mkdir()
            (built / "assets.dsk").write_bytes(b"")
            (built / "foundry-mod.json").write_text("{}", encoding="utf-8")
            self.assertTrue(sync.is_source(src))
            self.assertFalse(sync.is_source(built))

    def test_set_enabled_keeps_order_and_settings(self):
        from mercfoundry import sync
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            sync.write_loader(game, [sync.Mod("A", True), sync.Mod("B", False)], separate_saves=False)
            self.assertTrue(sync.set_enabled(game, sync.OUTPUT, True))
            self.assertFalse(sync.set_enabled(game, sync.OUTPUT, True))
            mods = sync.read_loader(game)
            self.assertEqual([(m.folder, m.enabled) for m in mods], [("A", True), ("B", False), (sync.OUTPUT, True)])
            self.assertFalse(sync._separate_saves(game))
            self.assertTrue((game / "mod-loader.ini").read_bytes().startswith(b"\xff\xfe"))


class PackTests(unittest.TestCase):
    def setUp(self):
        self.base = ModBuildTests("test_empty_project_writes_nothing")
        self.base.setUp()

    def tearDown(self):
        self.base.tearDown()

    def test_pack_script_and_texture_as_changes(self):
        from mercfoundry import modpack
        b = self.base
        b.write("scripts/mission.lua", "x = 1\ny = 2\n")
        w, h, rgba = texture.decode(dsk.Archive(b.data / "streamed.dsk").read(
            next(r for r in dsk.Archive(b.data / "streamed.dsk").records if r.kind == texture.TEXTURE))).rgba()
        edited = bytearray(rgba)
        edited[0:4] = bytes((1, 2, 3, 255))
        b.write("textures/tex.png", png.encode(w, h, bytes(edited), 4))
        packed = Path(b.tmp.name) / "packed"
        report = modpack.pack(b.project, packed, b.data)
        self.assertTrue((packed / "scripts" / "mission.lua.diff").exists())
        self.assertFalse((packed / "scripts" / "mission.lua").exists())
        self.assertTrue((packed / "textures" / "tex.changes.png").exists())
        self.assertFalse((packed / "textures" / "tex.png").exists())
        self.assertEqual(len(report["converted"]), 2)
        self.assertEqual(modpack.check(b.project, packed, b.data), [])
