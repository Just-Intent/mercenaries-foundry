import json
import tempfile
import unittest
import unittest.mock
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

    def test_new_model(self):
        # models/<name>.new.json + <name>.glb: a model under a new name, on a game model's skeleton
        from mercfoundry import gltf
        from tests.test_gltf_model import write_glb
        m = model.decode(self.mr, self.sr)
        doc, binary = gltf.read(gltf.export(m))
        for n in doc["nodes"]:
            if "mesh" in n:
                n["name"] = "new_" + n["name"]                      # all new pieces
        write_glb(self.project / "models" / "thing.glb", doc, binary)
        self.write("models/thing.new.json", json.dumps({"base": "sample"}))
        result = modbuild.build(self.project, self.data, self.out)
        self.assertTrue(any(c.startswith("model thing: new, from thing.glb on sample's skeleton") for c in result.changes))
        arc = dsk.Archive(self.out / "streamed.dsk")
        recs = {r.kind: arc.read(r) for r in arc.records if r.key == name_hash("thing")}
        new = model.decode(recs[model.MODEL], recs[model.SEGMENTS])
        self.assertEqual(new.name, "thing")
        self.assertEqual(len(new.pieces), len([n for n in doc["nodes"] if "mesh" in n]))   # none of the base's left
        # the base stays as it was
        self.assertEqual(self.records("streamed.dsk")[(name_hash("sample"), model.MODEL)], self.mr)
        for spec, msg in (({"base": "nothere"}, "names no game model"), ({"base": "sample"}, "already has a model")):
            p = self.project / "models"
            for f in p.glob("*"):
                f.unlink()
            name = "sample" if "already" in msg else "thing"
            write_glb(p / f"{name}.glb", doc, binary)
            (p / f"{name}.new.json").write_text(json.dumps(spec))
            with self.assertRaisesRegex(ValueError, msg):
                modbuild.build(self.project, self.data, self.out)

    def test_new_model_keeps_unedited_pieces(self):
        # a file with the base's pieces as exported (p<n> names, untouched, as
        # a reskinned character's): they stay, drawn at every level of detail
        from mercfoundry import gltf
        from tests.test_gltf_model import write_glb
        m = model.decode(self.mr, self.sr)
        doc, binary = gltf.read(gltf.export(m))
        write_glb(self.project / "models" / "same.glb", doc, binary)
        self.write("models/same.new.json", json.dumps({"base": "sample"}))
        modbuild.build(self.project, self.data, self.out)
        arc = dsk.Archive(self.out / "streamed.dsk")
        recs = {r.kind: arc.read(r) for r in arc.records if r.key == name_hash("same")}
        new = model.decode(recs[model.MODEL], recs[model.SEGMENTS])
        self.assertEqual(len(new.pieces), len([n for n in doc["nodes"] if "mesh" in n]))
        lods = 0
        for p in m.pieces:
            lods |= p.lod_mask
        self.assertEqual({p.lod_mask for p in new.pieces}, {lods})

    def test_new_model_shares_a_mod_texture(self):
        # a new model whose material names a texture the mod adds (textures/)
        # loads it wherever the model loads, not only where its maker does
        from mercfoundry import encounters as enc, gltf
        from tests.test_gltf_model import write_glb
        m = model.decode(self.mr, self.sr)
        doc, binary = gltf.read(gltf.export(m))
        for n in doc["nodes"]:
            if "mesh" in n:
                n["name"] = "new_" + n["name"]
        doc["materials"] = [{"name": "shared_tex"}]
        for mesh in doc["meshes"]:
            for prim in mesh["primitives"]:
                prim["material"] = 0
        write_glb(self.project / "models" / "thing.glb", doc, binary)
        self.write("models/thing.new.json", json.dumps({"base": "sample"}))
        (self.project / "textures").mkdir(exist_ok=True)
        (self.project / "textures" / "shared_tex.png").write_bytes(png.encode(4, 4, b"\x80" * 64, 4))
        (self.project / "encounters").mkdir(exist_ok=True)
        self.write("encounters/town.json", json.dumps({"map": "swn", "copy_loads": False, "objects": [
            {"from": "jeep_template", "name": "b0", "position": [0, 0, 0], "set": {"GeometryFile": "thing"}}]}))
        modbuild.build(self.project, self.data, self.out)
        lists = [enc.parse_list(body.decode("latin-1")) for (key, kind), body in self.records("streamed.dsk").items()
                 if kind == name_hash("list") and key == name_hash("town")]
        lists += [enc.parse_list(body.decode("latin-1")) for (key, kind), body in self.records("assets.dsk").items()
                  if kind == name_hash("list") and key == name_hash("town")]
        self.assertTrue(lists)
        self.assertIn("thing", lists[0].get("model", []))
        self.assertIn("shared_tex", lists[0].get("texture", []))

    def test_new_model_loads_a_game_texture_it_names(self):
        # a new model whose material names one of the game's own textures
        # (a tank's track texture) loads it wherever the model loads, even on
        # a map that never loaded the game model drawing it
        from mercfoundry import encounters as enc, gltf
        from tests.test_gltf_model import write_glb
        m = model.decode(self.mr, self.sr)
        doc, binary = gltf.read(gltf.export(m))
        for n in doc["nodes"]:
            if "mesh" in n:
                n["name"] = "new_" + n["name"]
        doc["materials"] = [{"name": "tex"}]
        for mesh in doc["meshes"]:
            for prim in mesh["primitives"]:
                prim["material"] = 0
        write_glb(self.project / "models" / "thing.glb", doc, binary)
        self.write("models/thing.new.json", json.dumps({"base": "sample"}))
        (self.project / "encounters").mkdir(exist_ok=True)
        self.write("encounters/town.json", json.dumps({"map": "swn", "copy_loads": False, "objects": [
            {"from": "jeep_template", "name": "b0", "position": [0, 0, 0], "set": {"GeometryFile": "thing"}}]}))
        modbuild.build(self.project, self.data, self.out)
        lists = [enc.parse_list(body.decode("latin-1")) for arc in ("streamed.dsk", "assets.dsk")
                 for (key, kind), body in self.records(arc).items()
                 if kind == name_hash("list") and key == name_hash("town")]
        self.assertTrue(lists)
        self.assertIn("thing", lists[0].get("model", []))
        self.assertIn("tex", lists[0].get("texture", []))

    def test_factions(self):
        # factions.json renames a faction in the game's text (after the mod's own text)
        self.write("text/t.json", json.dumps({"interface.datapod.mafia": "RUSSIAN MAFIA ", "veh.jeep": "Mafia jeep"}))
        self.write("factions.json", json.dumps({"mafia": {"name": "Yakuza"}}))
        result = modbuild.build(self.project, self.data, self.out)
        self.assertIn("factions: 1 strings renamed", result.changes)
        strings = {}
        for (key, kind), body in self.records("english.dsk").items():
            if kind == REGISTRY:
                strings.update(registry.Registry(body).strings())
        self.assertEqual(strings["interface.datapod.mafia"], "YAKUZA ")
        self.assertEqual(strings["veh.jeep"], "Mafia jeep")                     # not the interface's: kept

    def test_new_map(self):
        # maps/<name>.json: a copy of a game map under a new name, and its level
        from mercfoundry import ucfb
        tern = ucfb.wrap(b"tern", ucfb.pack_chunk(b"NAME", b"blank\0") + ucfb.pack_chunk(b"INFO", b"\0" * 8))
        wrld = ucfb.wrap(b"wrld", ucfb.pack_chunk(b"NAME", b"blank\0") + ucfb.pack_chunk(b"TNAM", b"blank\0"))
        build(self.data / "assets.dsk", [
            (name_hash("mission"), SCRIPT, script.encode(script.new("mission", "x = 1\n"))),
            (name_hash("blank"), SCRIPT, script.encode(script.new("blank", "function ScriptInit()\nend\n"))),
            (name_hash("blank"), name_hash("Terrain"), tern), (name_hash("blank"), WORLD, wrld),
            (name_hash("blank"), name_hash("list"), b"\n[terrain]\n\nload blank\n\n[world]\n\nload blank\n\n[model]\n\nload blankish\n"),
            (name_hash("sw"), name_hash("list"), b"\n[animation]\n\nload walk\n\n[terrain]\n\nload sw\n\n[world]\n\nload sw\n\n"
                                                 b"[model]\n\nload blankish\nload jeep\n"),
        ])
        (self.data / "levels.ini").write_text("[LevelNamesList]\n\nLevelName\tLV_blank\n\n[LV_blank]\nName\t\t\tblank\n"
                                              "Path\t\t\t\\TEST\\\nConfigFile\t\t\\TEST\\blank.ini\n", encoding="latin-1")
        (self.data / "TEST").mkdir()
        (self.data / "TEST" / "blank.ini").write_text("SkyTexture\t\tsky\n", encoding="latin-1")
        (self.project / "maps").mkdir(exist_ok=True)
        self.write("maps/island.json", json.dumps({"from": "blank"}))
        result = modbuild.build(self.project, self.data, self.out)
        self.assertTrue(any(c.startswith("map island: new, from blank") for c in result.changes))
        recs = self.records("assets.dsk")
        self.assertIn(b"NAME\x07\x00\x00\x00island\x00\x00INFO", recs[(name_hash("island"), name_hash("Terrain"))])   # padded
        self.assertIn(b"TNAM", recs[(name_hash("island"), WORLD)])
        self.assertNotIn(b"blank", recs[(name_hash("island"), WORLD)])
        # its own layers, and what free roam (sw) loads but sw's terrain and layers: any template works on it
        self.assertEqual(recs[(name_hash("island"), name_hash("list"))],
                         b"\n[terrain]\n\nload island\n\n[world]\n\nload island\n\n[model]\n\nload blankish\nload jeep\n"
                         b"\n[animation]\n\nload walk\n")
        levels = (self.out / "DATAxbox" / "levels.ini").read_text(encoding="latin-1")
        self.assertIn("LevelName\tLV_blank\nLevelName\tLV_island\n", levels)
        self.assertIn("[LV_island]\nName\t\t\tisland\n", levels)
        self.assertIn("ConfigFile\t\t\\TEST\\island.ini", levels)
        # it runs its own script (blank's config names none, so blank's never ran)
        self.assertEqual((self.out / "DATAxbox" / "TEST" / "island.ini").read_text(encoding="latin-1"),
                         "MainMasterScript\tisland\n\nSkyTexture\t\tsky\n")
        # its master script (its load list loads one of its own name; a missing one hangs the game)
        self.assertEqual(script.decode(recs[(name_hash("island"), SCRIPT)]).source, "function ScriptInit()\nend\n")
        # a mod's own scripts/island.lua wins
        (self.project / "scripts").mkdir(exist_ok=True)
        self.write("scripts/island.lua", "function ScriptInit()\nUi_PrintHudMessage( 'Island' )\nend\n")
        modbuild.build(self.project, self.data, self.out)
        self.assertIn("'Island'", script.decode(self.records("assets.dsk")[(name_hash("island"), SCRIPT)]).source)
        # where you arrive, a message, and the layers that load with it
        (self.project / "scripts" / "island.lua").unlink()
        self.write("maps/island.json", json.dumps({"from": "blank", "start": {"position": [10, 5, -20], "heading": 90},
                                                  "message": "Welcome to the island"}))
        self.write("encounters/isle_camp.json", json.dumps({"map": "island", "load": "map", "objects": [],
                                                            "traffic": {"isle-peds": {"density": 30, "spawn": ["template_civ_a"]}},
                                                            "paths": {"p_isle-peds_01": {"points": [[0, 0, 0], [9, 0, 0]],
                                                                                         "properties": {"width": 3}}}}))
        result = modbuild.build(self.project, self.data, self.out)
        # its traffic zones go in its own config
        self.assertIn("[TrafficZone]\r\nName\t\t\tisle-peds\r\nDensity\t\t\t30\r\nSpawnObject\t\ttemplate_civ_a\r\n",
                      (self.out / "DATAxbox" / "TEST" / "island.ini").read_bytes().decode("latin-1"))
        source = script.decode(self.records("assets.dsk")[(name_hash("island"), SCRIPT)]).source
        self.assertIn("Utility_ReadLevelFile('isle_camp')\nActor_SetPosition('player0', 10.00, 5.00, -20.00)\n"
                      "Actor_SetYaw('player0', 90.0)\nUi_PrintHudMessage('Welcome to the island')\nend\n", source)
        self.assertIn("Utility_ReadLevelFile('\\\\TEMPLATE\\\\template_vehicles_ch2')", source)
        from mercfoundry import luacheck
        self.assertEqual(luacheck.check(source), [])
        self.assertFalse(any("isle_camp" in w for w in result.warnings))
        self.assertTrue(any("loads with the new map island" in c for c in result.changes))
        # its paths at load are counted: the game's path table hangs when full
        self.assertIn("map island: loads 1 paths of the game's 2047 (isle_camp 1)", result.changes)
        with unittest.mock.patch.object(modbuild, "PATH_TABLE", 1):
            with self.assertRaisesRegex(ValueError, "it loads 1 paths"):
                modbuild.build(self.project, self.data, self.out)
        # "scripts": a mod script run after the master script's own set-up
        self.write("scripts/isle_events.lua", "function isle_events_Start()\nend\n")
        self.write("maps/island.json", json.dumps({"from": "blank", "message": "Welcome to the island",
                                                  "scripts": ["isle_events"]}))
        result = modbuild.build(self.project, self.data, self.out)
        self.assertFalse([w for w in result.warnings if "isle_events.lua is new" in w])
        recs = self.records("assets.dsk")
        source = script.decode(recs[(name_hash("island"), SCRIPT)]).source
        self.assertIn("Ui_PrintHudMessage('Welcome to the island')\nUtility_LoadUtilityScript('isle_events')\n"
                      "if isle_events_Start then isle_events_Start() end\nend\n", source)
        self.assertEqual(luacheck.check(source), [])
        self.assertIn(b"load isle_events", recs[(name_hash("island"), name_hash("list"))])
        self.write("maps/island.json", json.dumps({"from": "blank", "scripts": ["isle_nothing"]}))
        with self.assertRaisesRegex(ValueError, "no script isle_nothing"):
            modbuild.build(self.project, self.data, self.out)
        (self.project / "scripts" / "isle_events.lua").unlink()
        self.write("maps/island.json", json.dumps({"from": "blank"}))
        # maps/<name>/boundaries.json: zones in the map's own lineregion record
        # (blank has none here: one is made)
        from mercfoundry import aidata
        sea = {"points": [[0, 19, 0], [100, 19, 0], [100, 19, 100], [0, 19, 100]],
               "properties": {"type": "water", "height": "19"}}
        (self.project / "maps" / "island").mkdir()
        self.write("maps/island/boundaries.json", json.dumps({"boundaries": {"island_sea": sea}}))
        result = modbuild.build(self.project, self.data, self.out)
        doc = aidata.to_json(self.records("assets.dsk")[(name_hash("island"), name_hash("lineregion"))], "lineregion")
        names = [n for t, n, _ in aidata.items(doc) if t == "lrg_"]
        self.assertEqual(names, ["island_sea"])
        self.write("maps/blank.json", json.dumps({"from": "blank"}))
        with self.assertRaisesRegex(ValueError, "already a map"):
            modbuild.build(self.project, self.data, self.out)

    def test_new_map_leaves_out_scenery_and_skies(self):
        # "loads_like" sw copies sw's list but not what only sw's own layer
        # draws (and only that), nor skies its own config doesn't name
        from mercfoundry import ucfb

        def layer(name, objs):
            strings = sorted({name} | {s for o in objs for s in [o["model"], o["template"]] + sum(o["properties"], [])})
            return world.from_json({"format": "mercfoundry-world-1", "name": name, "strings": strings, "chunks": [
                {"tag": "NAME"}, {"tag": "TABL"}] + [dict(o, tag="inst") for o in objs]})
        prop = {"model": "sample", "template": "", "properties": []}
        tern = ucfb.wrap(b"tern", ucfb.pack_chunk(b"NAME", b"blank\0") + ucfb.pack_chunk(b"INFO", b"\0" * 8))
        wrld = ucfb.wrap(b"wrld", ucfb.pack_chunk(b"NAME", b"blank\0") + ucfb.pack_chunk(b"TNAM", b"blank\0"))
        recs = [
            (name_hash("blank"), name_hash("Terrain"), tern), (name_hash("blank"), WORLD, wrld),
            (name_hash("blank"), SCRIPT, script.encode(script.new("blank", "x = 1\n"))),
            (name_hash("blank"), name_hash("list"), b"\n[world]\n\nload blank\n\n[texture]\n\nload skyother\nload sky\n"),
            (name_hash("sw"), name_hash("list"), b"\n[world]\n\nload sw\n\n[model]\n\nload sample\nload coded\n\n"
                                                 b"[texture]\n\nload tex\nload tex_nm\nload effect\n"),
            (name_hash("sw"), WORLD, layer("sw", [prop])),
        ]
        build(self.data / "assets.dsk", recs)
        (self.data / "levels.ini").write_text("[LevelNamesList]\n\nLevelName\tLV_blank\n\n[LV_blank]\nName\t\t\tblank\n"
                                              "Path\t\t\t\\TEST\\\nConfigFile\t\t\\TEST\\blank.ini\n", encoding="latin-1")
        (self.data / "TEST").mkdir()
        (self.data / "TEST" / "blank.ini").write_text("SkyTexture\t\tsky\n", encoding="latin-1")
        (self.data / "TEST" / "other.ini").write_text("SkyTexture\t\tskyother\n", encoding="latin-1")
        (self.project / "maps").mkdir(exist_ok=True)
        self.write("maps/island.json", json.dumps({"from": "blank"}))
        result = modbuild.build(self.project, self.data, self.out)
        loads = self.records("assets.dsk")[(name_hash("island"), name_hash("list"))].decode("latin-1")
        # sample is only sw's scenery: it and the textures only it draws go; a
        # model no object names (the code's) and a texture no model draws stay
        self.assertNotIn("load sample", loads)
        self.assertNotIn("load tex\n", loads)
        self.assertNotIn("load tex_nm", loads)
        self.assertIn("load coded", loads)
        self.assertIn("load effect", loads)
        # another level's sky goes; the one its config names stays
        self.assertNotIn("skyother", loads)
        self.assertIn("load sky\n", loads)
        self.assertTrue(any("not sw's scenery: 1 models, 2 textures" in c for c in result.changes))
        total = [c for c in result.changes if "its list loads" in c]
        self.assertEqual(len(total), 1, result.changes)
        self.assertNotIn("MB of textures", " ".join(result.warnings))
        with unittest.mock.patch.object(modbuild, "MAP_TEXTURE_BUDGET", -1):
            result = modbuild.build(self.project, self.data, self.out)
        self.assertTrue(any("MB of textures" in w for w in result.warnings), result.warnings)
        self.assertTrue(any("doesn't load the skies skyother" in c for c in result.changes))
        # a template that draws it (spawnable anywhere) keeps it
        recs.append((name_hash("template_vehicles"), WORLD,
                     layer("template_vehicles", [dict(prop, template="template_thing")])))
        build(self.data / "assets.dsk", recs)
        modbuild.build(self.project, self.data, self.out)
        loads = self.records("assets.dsk")[(name_hash("island"), name_hash("list"))].decode("latin-1")
        self.assertIn("load sample", loads)
        self.assertIn("load tex\n", loads)

    def test_new_map_templates(self):
        # "templates": copies of templates a new map doesn't load, with the
        # templates they name unless every map has them, and only their loads
        from mercfoundry import ucfb

        def layer(name, objs):
            return world.from_json({"format": "mercfoundry-world-1", "name": name, "strings": [], "chunks": [
                {"tag": "NAME"}, {"tag": "TABL"}] + [dict(o, tag="inst") for o in objs]})
        tank = {"model": "sample", "template": "template_tank",
                "properties": [["GeometryFile", "sample"], ["weapon_A", "template_wep_big"],
                               ["RiderOccupant_A", "template_allies_driver"], ["weapon_B", "template_wep_small"]]}
        tern = ucfb.wrap(b"tern", ucfb.pack_chunk(b"NAME", b"blank\0") + ucfb.pack_chunk(b"INFO", b"\0" * 8))
        wrld = ucfb.wrap(b"wrld", ucfb.pack_chunk(b"NAME", b"blank\0") + ucfb.pack_chunk(b"TNAM", b"blank\0"))
        build(self.data / "assets.dsk", [
            (name_hash("blank"), name_hash("Terrain"), tern), (name_hash("blank"), WORLD, wrld),
            (name_hash("blank"), SCRIPT, script.encode(script.new("blank", "x = 1\n"))),
            (name_hash("blank"), name_hash("list"), b"\n[world]\n\nload blank\n"),
            (name_hash("template_vehicles_ch3"), WORLD, layer("template_vehicles_ch3", [tank])),
            (name_hash("template_vehicles_ch3"), name_hash("list"),
             b"\n[world]\n\nload template_vehicles_ch3\n\n[model]\n\nload sample\nload other\n\n"
             b"[texture]\n\nload tex\nload other_tex\n"),
            (name_hash("template_special"), WORLD, layer("template_special", [
                {"model": "", "template": "template_wep_big", "properties": [["ammo", "big"]]}])),
            (name_hash("template_humans"), WORLD, layer("template_humans", [
                {"model": "", "template": "template_allies_driver", "properties": []}])),
            (name_hash("template_vehicles_ch2"), WORLD, layer("template_vehicles_ch2", [
                {"model": "", "template": "template_wep_small", "properties": []}])),
        ])
        (self.data / "levels.ini").write_text("[LevelNamesList]\n\nLevelName\tLV_blank\n\n[LV_blank]\nName\t\t\tblank\n"
                                              "Path\t\t\t\\TEST\\\nConfigFile\t\t\\TEST\\blank.ini\n", encoding="latin-1")
        (self.data / "TEST").mkdir()
        (self.data / "TEST" / "blank.ini").write_text("SkyTexture\t\tsky\n", encoding="latin-1")
        (self.project / "maps").mkdir(exist_ok=True)
        self.write("maps/island.json", json.dumps({"from": "blank", "loads_like": None, "templates": ["template_tank"]}))
        result = modbuild.build(self.project, self.data, self.out)
        recs = self.records("assets.dsk")
        objs = list(world.iter_objects(recs[(name_hash("island_templates"), WORLD)]))
        # what it names comes too, but not what every map loads already
        # (the level loader's template layers, and ch2 from the map's script)
        self.assertEqual([o["name"] for o in objs], ["template_tank", "template_wep_big"])
        self.assertEqual(list(objs[0]["properties"][0]), ["GeometryFile", "sample"])
        loads = recs[(name_hash("island_templates"), name_hash("list"))].decode("latin-1")
        self.assertIn("[world]\n\nload island_templates\n", loads)
        self.assertIn("load sample\n", loads)
        self.assertIn("load tex\n", loads)
        self.assertNotIn("other", loads)
        self.assertIn("Utility_ReadLevelFile('island_templates')",
                      script.decode(recs[(name_hash("island"), SCRIPT)]).source)
        self.assertTrue(any("1 templates in island_templates (and 1 they name: template_wep_big)" in c
                            for c in result.changes))
        # one every map has isn't copied
        self.write("maps/island.json", json.dumps({"from": "blank", "loads_like": None,
                                                  "templates": ["template_allies_driver"]}))
        result = modbuild.build(self.project, self.data, self.out)
        self.assertNotIn((name_hash("island_templates"), WORLD), self.records("assets.dsk"))
        self.assertTrue(any("1 templates load on every map already, not copied (template_allies_driver)" in c
                            for c in result.changes))
        self.write("maps/island.json", json.dumps({"from": "blank", "loads_like": None, "templates": ["template_nothing"]}))
        with self.assertRaisesRegex(ValueError, "no game template named 'template_nothing'"):
            modbuild.build(self.project, self.data, self.out)

    def test_rule_wheels_and_break_up_pieces_load(self):
        # a rule giving an object wheels or helicopter break-up pieces: its
        # layer's list loads those models too (a Hilux's wheels were invisible)
        build(self.data / "assets.dsk", [
            (name_hash("mission"), SCRIPT, script.encode(script.new("mission", "x = 1\n"))),
            (name_hash("base_layer"), WORLD, world_record()),
            (name_hash("base_layer"), name_hash("list"), b"\n[world]\n\nload base_layer\n\n[model]\n\nload jeep_model\n"),
        ])
        (self.project / "objects").mkdir(exist_ok=True)
        self.write("objects/hilux.json", json.dumps([{"template": "jeep_template", "set": {
            "GeometryFile": "hilux", "geometrynamewheel": "hilux_wheel", "geometryNameRuinA": "global_invisible"}}]))
        modbuild.build(self.project, self.data, self.out)
        loads = self.records("assets.dsk")[(name_hash("base_layer"), name_hash("list"))].decode("latin-1")
        for m in ("hilux", "hilux_wheel", "global_invisible"):
            self.assertIn(f"load {m}\n", loads)

    def test_layer_name_limit(self):
        # more names than the game's named-object table can take are refused
        # (a full table hangs it); "unnamed" scenery is fine
        objs = [{"from": "jeep_template", "name": f"b{i}", "position": [i, 0, 0]}
                for i in range(modbuild.MAX_LAYER_NAMES + 1)]
        (self.project / "encounters").mkdir(exist_ok=True)
        self.write("encounters/town.json", json.dumps({"map": "swn", "objects": objs}))
        with self.assertRaisesRegex(ValueError, "named-object table"):
            modbuild.build(self.project, self.data, self.out)
        self.write("encounters/town.json", json.dumps({"map": "swn", "unnamed": True, "objects": objs}))
        modbuild.build(self.project, self.data, self.out)

    def test_rule_new_model(self):
        # a rule giving an object another model (GeometryFile): its model word follows
        self.write("objects/o.json", json.dumps({"template": "jeep_template", "set": {"GeometryFile": "foundry_crate"}}))
        modbuild.build(self.project, self.data, self.out)
        arc = dsk.Archive(self.out / "assets.dsk")
        rec = next(arc.read(r) for r in arc.records if r.kind == WORLD)
        inst = next(world.iter_objects(rec))
        self.assertEqual(inst["model"], "foundry_crate")
        self.assertIn(("GeometryFile", "foundry_crate"), [tuple(p) for p in inst["properties"]])

    def test_sky(self):
        # sky/<map>.json: the base sky and atmosphere presets in the map's level config files
        (self.data / "levels.ini").write_text("[LV_SW]\r\nName\t\tSW\r\nConfigFile\t\t\\SW\\SW.ini\r\n", encoding="latin-1")
        (self.data / "SW").mkdir()
        (self.data / "SW" / "SW.ini").write_bytes(b"SkyTexture\t\tskySummer\r\nFogRGBA\t\t1 2 3 4\r\n\r\n[SoundAmbience]\r\n")
        (self.data / "SW" / "SW_XBOX.ini").write_bytes(b"[atmosphere]\r\nName\t\tsummer1\r\nRainChance\t0.0\r\n")
        (self.project / "sky").mkdir(exist_ok=True)
        self.write("sky/sw.json", json.dumps({"base": {"FogRGBA": "9 9 9 9"},
                                              "presets": {"summer1": {"RainChance": "1"},
                                                          "storm": {"copy": "summer1", "SnowChance": "1"}}}))
        result = modbuild.build(self.project, self.data, self.out)
        config = (self.out / "DATAxbox" / "SW" / "SW.ini").read_bytes().decode("latin-1")
        xbox = (self.out / "DATAxbox" / "SW" / "SW_XBOX.ini").read_bytes().decode("latin-1")
        self.assertIn("FogRGBA\t\t9 9 9 9", config)
        self.assertIn("RainChance\t\t1", xbox)
        self.assertIn("Name\t\tstorm\r\nRainChance\t\t0.0\r\nSnowChance\t\t1", xbox)
        self.assertTrue(any(c.startswith("sky sw: ") for c in result.changes))
        self.write("sky/nowhere.json", "{}")
        with self.assertRaises(ValueError):
            modbuild.build(self.project, self.data, self.out)

    def test_lua_checked(self):
        # a mod's scripts are checked as the game's Lua 5.0 will compile them
        self.write("scripts/mission.lua", "if x then\n  y = #t\nend\n")
        self.write("scripts/brand_new.lua", "Not_A_Game_Function(1)\nEvent_X('helper', 'b')\n")
        result = modbuild.build(self.project, self.data, self.out)
        self.assertIn("script mission: line 2: unexpected character '#' (the length operator # came with Lua 5.1; "
                      "use table.getn)", result.warnings)
        self.assertIn("script brand_new: line 1: calls Not_A_Game_Function, which nothing defines "
                      "(not this script, not the game's)", result.warnings)
        self.assertIn("script brand_new: line 2: an event calls back helper, which nothing defines", result.warnings)
        self.write("scripts/brand_new.lua", "function helper() end\n")
        result = modbuild.build(self.project, self.data, self.out)
        self.assertFalse([w for w in result.warnings if w.startswith("script brand_new: line")])

    def test_empty_project_writes_nothing(self):
        result = modbuild.build(self.project, self.data, self.out)
        self.assertEqual(result.written, [])
        self.assertTrue((self.out / "foundry-mod.json").exists())

    def test_big_texture_warns(self):
        # 1024x1024 opaque builds as DXT1 with mips, about 699 KB: over retail's 512 KB
        self.write("textures/big_tex.png", png.encode(1024, 1024, red(1024, 1024), 4))
        self.write("textures/small_tex.png", png.encode(256, 256, red(256, 256), 4))
        result = modbuild.build(self.project, self.data, self.out)
        big = [w for w in result.warnings if "main heap" in w]
        self.assertEqual(len(big), 1, result.warnings)
        self.assertIn("textures/big_tex.png", big[0])

    def test_edge_wall_positions(self):
        # a 1024-cell map spans x -4096..4088, z -4088..4096: walls just outside, every 128 m or less
        walls = modbuild.edge_wall_positions(1024)
        self.assertEqual(len(walls), 4 * 64)
        t = modbuild.EDGE_HALF[2]
        north = sorted(x for x, z, yaw in walls if z == 4096 + t)
        east = sorted(z for x, z, yaw in walls if x == 4088 + t)
        self.assertEqual(len(north), 64)
        self.assertEqual(len(east), 64)
        self.assertTrue(all(yaw == 90.0 for x, z, yaw in walls if x == 4088 + t))
        self.assertLessEqual(max(b - a for a, b in zip(north, north[1:])), modbuild.EDGE_STEP)
        # each wall reaches its neighbour: no gap between them
        self.assertGreaterEqual(2 * modbuild.EDGE_HALF[0], max(b - a for a, b in zip(north, north[1:])))
        self.assertLess(north[0] - modbuild.EDGE_HALF[0], -4096)
        self.assertGreater(north[-1] + modbuild.EDGE_HALF[0], 4088)

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


class ObjectLoadsTests(unittest.TestCase):
    def test_only_what_one_object_needs_from_a_map_list(self):
        # an object copied from a map's own layer: not that map's whole list (its terrain above all)
        full = {"terrain": ["nw"], "model": ["shed", "shed_ruin", "barn"], "animtable": ["large_class", "nk_civilian"],
                "texture": ["shed_wall", "barn_wall"], "script": ["global"]}
        obj = {"model": "shed", "properties": [("GeometryNameRuined", "shed_ruin"), ("AnimationTable", "large_class")]}
        self.assertEqual(modbuild._object_loads(obj, full, []),
                         {"model": ["shed", "shed_ruin"], "animtable": ["large_class"]})


class MapTextureLoadTests(unittest.TestCase):
    def test_ground_textures_load_with_the_map(self):
        class FakeData:
            def __init__(self):
                self.added = {(name_hash("isle"), name_hash("list")): b"\n[texture]\n\nload nk_tt_dirt\n"}

            def find_added(self, key, kind):
                return self.added.get((key, kind))

            def replace_added(self, key, kind, body):
                self.added[(key, kind)] = body

            def records(self, kind):
                return []
        data = FakeData()
        modbuild._load_textures_with_map(data, "isle", ["nk_tt_dirt", "nw_tt_snow"])
        self.assertEqual(data.added[(name_hash("isle"), name_hash("list"))],
                         b"\n[texture]\n\nload nk_tt_dirt\nload nw_tt_snow\n")


class RotationRuleTests(unittest.TestCase):
    def test_rule_rotation_replaces_the_whole_rotation(self):
        inst = {"template": "x", "properties": [], "position": [0, 0, 0],
                "rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}
        modbuild._apply_rule(inst, {"rotation": [[1, 0, 0], [0, 0.98, -0.2], [0, 0.2, 0.98]]})
        self.assertEqual(inst["rotation"][1], [0.0, 0.98, -0.2])
        with self.assertRaises(ValueError):
            modbuild._apply_rule(inst, {"rotation": [[1, 0], [0, 1]]})


class ModelSizeTests(unittest.TestCase):
    def test_big_geometry_warns(self):
        # a model's geometry unpacks into one heap block as it streams in; a
        # 158 KB one froze Gaza on a heap whose largest gap was 138 KB
        import struct
        from mercfoundry import ucfb
        seg = lambda n: ucfb.wrap(b"CSEG", struct.pack("<II", n, 4) + b"\0" * 4)
        data = unittest.mock.Mock(replace={})
        data.add = {"streamed.dsk": [(1, model.SEGMENTS, seg(158_176)), (2, model.SEGMENTS, seg(90_000)),
                                     (3, model.MODEL, b"")]}
        data.label = lambda key, kind: f"segments block_{key}"
        result = modbuild.Result()
        modbuild._check_model_sizes(data, result)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("block_1 154 KB", result.warnings[0])
        self.assertNotIn("block_2", result.warnings[0])


class MapScriptTests(unittest.TestCase):
    def test_weather_on_arrival(self):
        # a new map can force one of the weather presets as the player arrives
        # (the one on in the map they came from otherwise stays)
        text = modbuild.map_script("gaza", {"weather": "gaza", "start": {"position": [1, 2, 3]}}, [])
        self.assertIn("Mission_EnableAtmosphere('gaza', true)", text)
        from mercfoundry import luacheck
        self.assertEqual(luacheck.check(text), [])
        self.assertNotIn("Mission_EnableAtmosphere", modbuild.map_script("gaza", {}, []))
