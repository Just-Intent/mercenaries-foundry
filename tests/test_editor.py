import math
import random
import unittest

from mercfoundry.editor import scene, server
from mercfoundry.modbuild import _turn

IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


class EditorSceneTests(unittest.TestCase):
    def test_rotation_round_trip(self):
        random.seed(3)
        for _ in range(50):
            rows = _turn(IDENTITY, random.uniform(-180, 180))
            back = scene.from_three(scene.to_three(rows))
            for r1, r2 in zip(rows, back):
                for a, b in zip(r1, r2):
                    self.assertAlmostEqual(a, b, places=9)

    def test_turn_between_matches_rule_turn(self):
        random.seed(4)
        for _ in range(50):
            rows = _turn(IDENTITY, random.uniform(-180, 180))
            t = random.uniform(-170, 170)
            self.assertAlmostEqual(scene.turn_between(rows, _turn(rows, t)), t, places=3)

    def test_three_turn_is_game_turn(self):
        # The game's world is right-handed like three.js: a three.js turn about
        # +Y is the same turn in the game.
        th = math.radians(30)
        ry = [math.cos(th), 0, -math.sin(th), 0, 1, 0, math.sin(th), 0, math.cos(th)]
        self.assertAlmostEqual(scene.turn_between(IDENTITY, scene.from_three(ry)), 30.0, places=3)

    def test_placed_yaw_round_trip(self):
        from mercfoundry.encounters import _rotation
        for yaw in (0.0, 37.5, -90.0, 179.0):
            rows = scene.from_three(scene.to_three(_rotation(yaw)))
            self.assertAlmostEqual(math.degrees(math.atan2(-rows[0][2], rows[0][0])), yaw, places=6)

    def test_static_files_are_served_from_the_package(self):
        self.assertIn(b"importmap", server._static("static/index.html"))
        self.assertIsNotNone(server._static("vendor/three/build/three.module.js"))
        self.assertIsNone(server._static("static/../scene.py"))
        self.assertIsNone(server._static("static/missing.js"))


class TerrainSaveTests(unittest.TestCase):
    def test_painted_samples_merge(self):
        import json, tempfile
        from pathlib import Path
        from mercfoundry.editor.scene import Scene
        with tempfile.TemporaryDirectory() as d:
            sc = Scene.__new__(Scene)
            sc.mod, sc._terrain = Path(d), "cached"
            sc.save_terrain("sw", [[3, 4, 100], [5, 4, 90]])
            done = sc.save_terrain("sw", [[3, 4, 120], [1, 0, 7]])       # repainted and new samples
            doc = json.loads((Path(d) / "terrain" / "sw.heights.changes.json").read_text())
            self.assertEqual(doc, {"terrain": "sw", "samples": [[1, 0, 7], [3, 4, 120], [5, 4, 90]]})
            self.assertEqual((done["samples"], sc._terrain), (3, None))     # the map is read again
            (Path(d) / "terrain" / "sw.heights.png").write_bytes(b"")
            with self.assertRaises(ValueError):                               # a whole-map image wins
                sc.save_terrain("sw", [[0, 0, 1]])


class GroundSaveTests(unittest.TestCase):
    def scene(self, d):
        from pathlib import Path
        from mercfoundry.editor.scene import Scene
        sc = Scene.__new__(Scene)
        sc.mod, sc._terrain = Path(d), "cached"
        sc.terrain_look = lambda name: ({"paint_size": 4}, b"", b"")
        return sc

    def test_roads_add_up(self):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            sc = self.scene(d)
            sc.save_roads("sw", [{"texture": "sw_decal_road_straight", "span": 20, "points": [[0, 0], [50.123, 7]]}])
            done = sc.save_roads("sw", [{"texture": "sw_decal_road_straight", "points": [[1, 1], [2, 2]]}])
            doc = json.loads((Path(d) / "terrain" / "sw.decals.json").read_text())
            self.assertEqual(done["roads"], 2)
            self.assertEqual(doc["roads"][0], {"texture": "sw_decal_road_straight", "span": 20.0,
                                               "points": [[0.0, 0.0], [50.12, 7.0]]})
            with self.assertRaises(ValueError):
                sc.save_roads("sw", [{"texture": "x", "points": [[0, 0]]}])

    def test_paint_merges_into_a_changes_image(self):
        import tempfile
        from pathlib import Path
        from mercfoundry import png
        with tempfile.TemporaryDirectory() as d:
            sc = self.scene(d)
            sc.save_paint("sw", {"3": {"texture": "sw_tt_grass", "pixels": [[1, 0, 200]]}})
            done = sc.save_paint("sw", {"3": {"texture": "sw_tt_grass", "pixels": [[2, 3, 50]]}})
            self.assertEqual(done["files"], ["terrain/sw.layer03.sw_tt_grass.changes.png"])
            w, h, rgba = png.decode((Path(d) / "terrain" / "sw.layer03.sw_tt_grass.changes.png").read_bytes())
            marked = {i: rgba[4 * i] for i in range(w * h) if rgba[4 * i + 3] >= 128}
            self.assertEqual(((w, h), marked), ((4, 4), {1: 200, 14: 50}))
            (Path(d) / "terrain" / "sw.layer05.rock.png").write_bytes(b"")
            with self.assertRaises(ValueError):                 # a whole-layer image wins
                sc.save_paint("sw", {"5": {"texture": "rock", "pixels": [[0, 0, 1]]}})


class LiveLinkTests(unittest.TestCase):
    def test_commands_and_poll(self):
        from mercfoundry.editor.live import Live
        live = Live()
        self.assertEqual(live.state()["connected"], False)
        live.send([{"op": "move", "name": "", "model": "dmz_bld_skpost", "from": [[1, 2, 3], [4, 5, 6]],
                    "position": [7, 8, 9], "heading": 30},
                   {"op": "remove", "name": "FoundryPatroller", "model": "x y", "from": []},
                   {"op": "teleport", "position": [1, 2, 3]}])
        reply = live.poll("player 1 2 3 0 1\ngameplay 1\nresult dmz_bld_skpost moved\n")
        self.assertEqual(reply.splitlines(), [
            "move - dmz_bld_skpost 2 1.000 2.000 3.000 4.000 5.000 6.000 7.000 8.000 9.000 30.000",
            "remove FoundryPatroller - 0",
            "teleport 1.000 2.000 3.000"])
        state = live.state()
        self.assertTrue(state["connected"] and state["gameplay"])
        self.assertEqual(state["player"]["position"], [1.0, 2.0, 3.0])
        self.assertEqual(state["results"], ["dmz_bld_skpost moved"])
        self.assertEqual(live.poll(""), "")      # commands go once
        live.send([{"op": "loaded"}])
        self.assertEqual(live.poll(""), "loaded\n")
        live.poll("loaded-begin\nloaded b_soldier a_tank\nloaded c_jeep\nloaded-end\n")
        self.assertEqual(live.state()["loaded"], ["a_tank", "b_soldier", "c_jeep"])
        with self.assertRaises(ValueError):
            live.send([{"op": "explode"}])
        # A copy of unnamed scenery: found as a move finds it, spawned under the new name.
        live.send([{"op": "copy", "name": "", "model": "dmz_bld_skpost", "from": [[1, 2, 3]], "as": "post_copy",
                    "position": [7, 8, 9], "heading": 90}])
        self.assertEqual(live.poll(""), "copy - dmz_bld_skpost 1 1.000 2.000 3.000 post_copy 7.000 8.000 9.000 90.000\n")
        with self.assertRaises(ValueError):
            live.send([{"op": "copy", "name": "", "model": "m", "from": [], "as": "two words", "position": [0, 0, 0]}])
        live.send([{"op": "respawn", "name": "", "model": "gate", "from": [[1, 2, 3]], "as": "fmv_x",
                    "position": [4, 5, 6], "heading": 0}])
        self.assertEqual(live.poll(""), "respawn - gate 1 1.000 2.000 3.000 fmv_x 4.000 5.000 6.000 0.000\n")
        live.send([{"op": "inspect", "position": [1, 2, 3], "radius": 10}])
        self.assertEqual(live.poll(""), "inspect 1.000 2.000 3.000 10.000\n")


class MissionGiverSaveTests(unittest.TestCase):
    """The Missions panel's giver: the HQ contact, someone placed on the map,
    or no one, written as the mission file's "contact"."""

    def save(self, d, giver):
        import json
        from pathlib import Path
        from mercfoundry.editor.scene import Scene
        sc = Scene.__new__(Scene)
        sc.mod = Path(d)
        sc.save_mission("sw", {"faction": "china", "number": 9, "title": "T", "objectives": [], "giver": giver})
        return json.loads((Path(d) / "missions" / "sw_china9.json").read_text(encoding="utf-8"))

    def test_givers(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            doc = self.save(d, {"mode": "placed", "template": "template_civ_civilian01", "name": "Mr Lee",
                                "prompt": "", "position": [10.123, 2, -30], "heading": 90})
            self.assertEqual(doc["contact"], {"name": "Mr Lee", "position": [10.12, 2.0, -30.0], "heading": 90.0,
                                              "template": "template_civ_civilian01"})
            doc = self.save(d, {"mode": "hq", "name": "Mr Lee", "prompt": "Talk"})
            self.assertEqual(doc["contact"], {"name": "Mr Lee", "prompt": "Talk"})     # placement dropped
            doc = self.save(d, {"mode": "none"})
            self.assertIs(doc["contact"], False)
            doc = self.save(d, {"mode": "hq", "name": "", "prompt": ""})
            self.assertNotIn("contact", doc)                                         # the default HQ contact
            with self.assertRaises(ValueError):
                self.save(d, {"mode": "placed"})


class MissionFileToolTests(unittest.TestCase):
    """The Missions panel's Duplicate and Delete."""

    def test_duplicate_and_delete(self):
        import json
        import tempfile
        from pathlib import Path
        from mercfoundry.editor.scene import Scene
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "missions").mkdir()
            (root / "encounters").mkdir()
            (root / "missions" / "sw_china9.json").write_text(json.dumps({
                "title": "No Russian", "mail": {"subject": "Contract: 'No Russian'", "body": "b"},
                "objectives": [{"type": "destroy", "text": "Kill", "squad": "sw_china9encounter1:sw_china9squad1"}],
                "contact": {"name": "Lee", "position": [1, 2, 3]}, "hq": {"after": 2}}))
            (root / "encounters" / "sw_china9.json").write_text(json.dumps({
                "map": "SW", "encounters": [{"name": "sw_china9encounter1",
                                             "squads": [{"name": "sw_china9squad1", "members": [{"name": "sw_china9encounter1_sw_china9squad1_1"}]}]}]}))
            (root / "missions" / "sw_china10.json").write_text(json.dumps({"title": "Next", "requires": ["sw_china9"]}))
            sc = Scene.__new__(Scene)
            sc.mod = root
            sc._arc_numbers = lambda m: {"allies": set(), "china": {1, 2, 9, 10}, "mafia": set(), "sk": set()}
            out = sc.duplicate_mission("sw_china9")
            self.assertEqual(out["id"], "sw_china11")
            self.assertEqual(len(out["notes"]), 2)                     # the HQ place and the placed giver
            doc = json.loads((root / "missions" / "sw_china11.json").read_text(encoding="utf-8"))
            self.assertEqual(doc["title"], "No Russian (copy)")
            self.assertEqual(doc["mail"]["subject"], "Contract: 'No Russian (copy)'")
            self.assertEqual(doc["objectives"][0]["squad"], "sw_china11encounter1:sw_china11squad1")
            self.assertNotIn("contact", doc)
            self.assertNotIn("hq", doc)
            layer = json.loads((root / "encounters" / "sw_china11.json").read_text(encoding="utf-8"))
            self.assertEqual(layer["encounters"][0]["squads"][0]["members"][0]["name"], "sw_china11encounter1_sw_china11squad1_1")
            self.assertEqual(sc.duplicate_mission("sw_china9", "mafia")["id"], "sw_mafia9")
            out = sc.delete_mission("sw_china9")
            self.assertEqual(out["moved"], ["missions/sw_china9.json", "encounters/sw_china9.json"])
            self.assertEqual(out["required_by"], ["sw_china10"])
            self.assertFalse((root / "missions" / "sw_china9.json").exists())
            self.assertTrue((root / out["trash"] / "missions" / "sw_china9.json").exists())
            with self.assertRaises(ValueError):
                sc.delete_mission("sw_china9")


class VoiceLineTests(unittest.TestCase):
    """Radio lines' voice search: the game's lines and the mod's own."""

    def test_mod_voices(self):
        import tempfile
        from pathlib import Path
        from mercfoundry.editor.scene import Scene
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "sounds" / "fio" / "new").mkdir(parents=True)
            (root / "sounds" / "fio" / "new" / "MyLine01.wav").write_bytes(b"RIFF-test")
            sc = Scene.__new__(Scene)
            sc.mod = root
            sc._voices = [{"cue": "fio.01011FIO", "text": "Hello."}]
            self.assertEqual(sc.voice_lines(), [{"cue": "fio.MyLine01", "text": "", "mod": True},
                                                {"cue": "fio.01011FIO", "text": "Hello."}])
            self.assertEqual(sc.voice_wav("fio.MyLine01"), b"RIFF-test")
            for bad in ("fio", "fio./../x", "nobank"):
                with self.assertRaises(ValueError):
                    sc.voice_wav(bad)


class NewMapTests(unittest.TestCase):
    """The Map panel: make a new map, set where you arrive and what loads."""

    def test_new_map_and_settings(self):
        import json
        import tempfile
        from pathlib import Path
        from mercfoundry.editor.scene import Scene
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            sc = Scene.__new__(Scene)
            sc.mod = root
            sc.context = [root]
            sc.maps = lambda: ["blank", "sw"] + sorted(f.stem for f in (root / "maps").glob("*.json"))
            self.assertEqual(sc.new_map("Fiefdom"), {"name": "fiefdom"})
            self.assertEqual(json.loads((root / "maps" / "fiefdom.json").read_text(encoding="utf-8")), {"from": "blank"})
            for bad in ("fiefdom", "sw", "bad name"):
                with self.assertRaises(ValueError):
                    sc.new_map(bad)
            with self.assertRaises(ValueError):
                sc.new_map("isle", "fiefdom")                 # copies game maps only
            self.assertEqual(sc.game_maps(), ["blank", "sw"])
            self.assertEqual(sc.map_settings("sw"), {"new": False, "free_roam": True, "pilots": [], "new_maps": ["fiefdom"]})
            (root / "encounters").mkdir()
            (root / "encounters" / "camp.json").write_text(json.dumps({"map": "FIEFDOM", "objects": []}))
            (root / "encounters" / "other.json").write_text(json.dumps({"map": "SW", "objects": []}))
            got = sc.map_settings("fiefdom")
            self.assertEqual(got["layers"], [{"name": "camp", "loads": False}])
            got = sc.save_map_settings("fiefdom", {"start": {"position": [1, 2.345, 3], "heading": 90},
                                                   "message": " Hi ", "layers": {"camp": True, "other": True}})
            self.assertEqual(got["start"], {"position": [1.0, 2.35, 3.0], "heading": 90.0})
            self.assertEqual(got["message"], "Hi")
            self.assertEqual(got["layers"], [{"name": "camp", "loads": True}])
            self.assertNotIn("load", json.loads((root / "encounters" / "other.json").read_text(encoding="utf-8")))
            got = sc.save_map_settings("fiefdom", {"start": None, "message": "", "layers": {"camp": False}})
            self.assertEqual(json.loads((root / "maps" / "fiefdom.json").read_text(encoding="utf-8")), {"from": "blank"})
            self.assertFalse(got["layers"][0]["loads"])


class PlaceablesTests(unittest.TestCase):
    def test_templates_objects_and_models_from_any_map(self):
        from mercfoundry.editor.scene import Scene
        sc = Scene.__new__(Scene)
        obj = lambda layer, name, model, props=(): {"layer": layer, "name": name, "model": model, "properties": list(props)}
        sc.all_objects = lambda: [
            obj("sw_poi", "", "house01"), obj("nw_poi", "", "house01"), obj("nw_poi", "", "barn"),
            obj("template_humans", "template_NK_Soldier", "nk_hum", [("ObjectType", "human")]),
            obj("sw_mafia6", "mafia6_crate", "crate", [("GeometryFile", "crate_b")]),
            obj("nw_mafia6", "Mafia6_Crate", "crate")]
        got = sc.placeables()
        self.assertEqual([(p["name"], p["kind"]) for p in got],
                         [("template_NK_Soldier", "template"), ("mafia6_crate", "object"),
                          ("model:barn", "model"), ("model:house01", "model")])
        self.assertEqual(got[1]["geometry"], "crate_b")
        self.assertEqual(got[0]["type"], "human")


class GroundLayerTests(unittest.TestCase):
    def test_add_ground_layer(self):
        import json
        import tempfile
        from pathlib import Path
        from mercfoundry.editor.scene import Scene
        with tempfile.TemporaryDirectory() as d:
            sc = Scene.__new__(Scene)
            sc.mod = Path(d)
            sc.ground_textures = lambda: [{"texture": "sw_tt_greengrass", "scale": 16.0, "axis": 0, "map": "sw"},
                                          {"texture": "sw_tt_cliffs", "scale": 16.0, "axis": 4, "map": "sw"}]
            layers = [{"texture": "nk_tt_dirt", "scale": 16.0, "axis": 0}]
            sc.terrain_look = lambda m: ({"layers": layers}, b"", b"")
            sc._forget_terrain = lambda m: None
            out = sc.add_ground_layer("fiefdom", "SW_TT_CLIFFS")
            self.assertEqual(out["layers"][1], {"texture": "sw_tt_cliffs", "scale": 16.0, "axis": 4})
            doc = json.loads((Path(d) / "terrain" / "fiefdom.layers.json").read_text(encoding="utf-8"))
            self.assertEqual(len(doc["layers"]), 2)
            with self.assertRaises(ValueError):
                sc.add_ground_layer("fiefdom", "not_a_ground")
            layers[:] = [{"texture": "x", "scale": 16.0, "axis": 0}] * 16
            with self.assertRaisesRegex(ValueError, "16 layers"):
                sc.add_ground_layer("fiefdom", "sw_tt_cliffs")


class TravelPilotTests(unittest.TestCase):
    def test_pilots_there_and_back(self):
        import json
        import tempfile
        from pathlib import Path
        from mercfoundry.editor.scene import Scene
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "maps").mkdir()
            (root / "maps" / "isle.json").write_text(json.dumps({"from": "blank"}))
            sc = Scene.__new__(Scene)
            sc.mod = root
            got = sc.add_travel_pilot("isle", "SW", [1, 2.004, 3], 90)
            self.assertEqual(got["pilots"], [{"to": "isle", "index": 0, "position": [1.0, 2.0, 3.0], "heading": 90.0}])
            with self.assertRaises(ValueError):
                sc.add_travel_pilot("isle", "blank", [1, 2, 3])
            sc.save_map_settings("isle", {"return": {"position": [5, 6, 7], "to": "nw"}})
            self.assertEqual(sc.map_settings("isle")["return"], {"position": [5.0, 6.0, 7.0], "heading": 0.0, "to": "nw"})
            sc.remove_travel_pilot("isle", 0, "sw")
            sc.save_map_settings("isle", {"return": None})
            self.assertEqual(json.loads((root / "maps" / "isle.json").read_text(encoding="utf-8")), {"from": "blank"})


class MapMarkTests(unittest.TestCase):
    def test_marks_seen_and_moved(self):
        import json
        import tempfile
        from pathlib import Path
        from mercfoundry.editor.scene import Scene, to_three
        from mercfoundry.encounters import _rotation
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "maps").mkdir()
            (root / "maps" / "isle.json").write_text(json.dumps({
                "from": "blank", "start": {"position": [1, 2, 3], "heading": 0},
                "return": {"position": [4, 5, 6], "to": "sw"},
                "travel": [{"map": "sw", "position": [7, 8, 9]}, {"map": "nw", "position": [1, 1, 1]}]}))
            sc = Scene.__new__(Scene)
            sc.mod = root
            self.assertEqual([(m["kind"], m["index"]) for m in sc.map_marks("sw")], [("travel", 0), ("arrive", 0)])
            # where the pilot back lands the player: beside the pilot, a little up, until it's moved
            self.assertEqual(sc.map_marks("sw")[1]["position"], [8.5, 8.5, 9.0])
            sc.save_map_marks([{"kind": "arrive", "to": "isle", "index": 0, "position": [20, 21, 22],
                                "rotation": to_three(_rotation(30))}])
            self.assertEqual(sc.map_marks("sw")[1]["position"], [20.0, 21.0, 22.0])
            self.assertEqual(sorted(m["kind"] for m in sc.map_marks("isle")), ["return", "start"])
            sc.save_map_marks([{"kind": "travel", "to": "isle", "index": 0, "position": [10, 11, 12],
                                "rotation": to_three(_rotation(45))},
                               {"kind": "start", "to": "isle", "index": 0, "position": [0, 0, 0],
                                "rotation": to_three(_rotation(-90))}])
            doc = json.loads((root / "maps" / "isle.json").read_text(encoding="utf-8"))
            self.assertEqual(doc["travel"][0], {"map": "sw", "position": [10.0, 11.0, 12.0], "heading": 45.0,
                                                "arrive": {"position": [20.0, 21.0, 22.0], "heading": 30.0}})
            self.assertEqual(doc["start"], {"position": [0.0, 0.0, 0.0], "heading": -90.0})
            self.assertEqual(doc["travel"][1], {"map": "nw", "position": [1, 1, 1]})


class GroundLayerEditTests(unittest.TestCase):
    def test_change_and_remove(self):
        import json
        import tempfile
        from pathlib import Path
        from mercfoundry.editor.scene import Scene
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "terrain").mkdir()
            sc = Scene.__new__(Scene)
            sc.mod = root
            sc.ground_textures = lambda: [{"texture": t, "scale": 16.0, "axis": 0, "map": "sw"}
                                          for t in ("dirt", "grass", "rock", "sand")]
            layers = [{"texture": "dirt", "scale": 16.0, "axis": 0}, {"texture": "grass", "scale": 16.0, "axis": 0},
                      {"texture": "rock", "scale": 16.0, "axis": 0}, {"texture": "rock", "scale": 16.0, "axis": 0}]
            sc.terrain_look = lambda m: ({"layers": layers}, b"", b"")
            sc._forget_terrain = lambda m: None
            sc._base_layer_count = lambda m: 1
            for k, t in ((1, "grass"), (2, "rock"), (3, "rock")):
                (root / "terrain" / f"isle.layer{k:02d}.{t}.changes.png").write_bytes(b"x%d" % k)
            out = sc.remove_ground_layer("isle", 2)        # the duplicate
            self.assertEqual([t["texture"] for t in out["layers"]], ["dirt", "grass", "rock"])
            self.assertEqual(sorted(f.name for f in (root / "terrain").glob("*.png")),
                             ["isle.layer01.grass.changes.png", "isle.layer02.rock.changes.png"])
            self.assertEqual((root / "terrain" / "isle.layer02.rock.changes.png").read_bytes(), b"x3")
            with self.assertRaisesRegex(ValueError, "came with the map"):
                sc.remove_ground_layer("isle", 0)
            out = sc.change_ground_layer("isle", 1, "sand")
            self.assertEqual(out["layers"][1]["texture"], "sand")
            self.assertTrue((root / "terrain" / "isle.layer01.sand.changes.png").exists())
            self.assertEqual(json.loads((root / "terrain" / "isle.layers.json").read_text())["layers"][1]["texture"], "sand")


class ModManagementTests(unittest.TestCase):
    def test_list_make_switch_edit_delete(self):
        import json
        import tempfile
        from pathlib import Path
        from mercfoundry import modbuild, sync
        from mercfoundry.editor.scene import Scene
        with tempfile.TemporaryDirectory() as d:
            game = Path(d)
            (game / "mods").mkdir()
            sync.write_loader(game, [])
            modbuild.new_project(game / "mods" / "First", "First mod")
            sc = Scene.__new__(Scene)
            sc.mod = game / "mods" / "First"
            sc.context = [sc.mod]
            sc._terrain = None
            sc._looks = {"x": 1}                          # a cache that belongs to the mod: dropped on a switch
            sc._names = "kept"                            # the game's: kept
            got = sc.mods()
            self.assertEqual([(m["folder"], m["name"], m["current"]) for m in got["mods"]], [("First", "First mod", True)])
            got = sc.create_mod("Second Mod", "a test")
            self.assertEqual(sc.mod.name, "Second Mod")
            self.assertFalse(hasattr(sc, "_looks"))
            self.assertEqual(sc._names, "kept")
            self.assertEqual([m["current"] for m in got["mods"]], [False, True])
            self.assertEqual({m.folder: m.enabled for m in sync.read_loader(game)}, {"Second Mod": True})
            sc.update_mod("Second Mod", name="Renamed", enabled=False)
            self.assertEqual(json.loads((game / "mods" / "Second Mod" / "mod.json").read_text())["name"], "Renamed")
            self.assertEqual({m.folder: m.enabled for m in sync.read_loader(game)}, {"Second Mod": False})
            with self.assertRaisesRegex(ValueError, "being edited"):
                sc.delete_mod("Second Mod")
            sc.switch_mod("First")
            out = sc.delete_mod("Second Mod")
            self.assertFalse((game / "mods" / "Second Mod").exists())
            self.assertTrue((game / out["moved_to"]).is_dir())
            self.assertEqual(sync.read_loader(game), [])
            with self.assertRaises(ValueError):
                sc.switch_mod("../x")


class LiveCommandTests(unittest.TestCase):
    def test_lua_and_heights_lines(self):
        from mercfoundry.editor.live import command_line
        self.assertEqual(command_line({"op": "lua", "line": "Utility_ReadLevelFile('x')\n"}),
                         "lua Utility_ReadLevelFile('x')")
        with self.assertRaises(ValueError):
            command_line({"op": "lua", "line": " "})
        samples = [[k, 2 * k, 100 + k] for k in range(45)]
        text = command_line({"op": "heights", "check": [0, 0, 99], "samples": samples})
        lines = text.split("\n")
        self.assertEqual(len(lines), 2)                       # 40 a line, each with the check
        self.assertTrue(all(line.startswith("heights 0 0 99 ") for line in lines))
        self.assertEqual(len(lines[0].split()), 4 + 3 * 40)
        self.assertTrue(lines[1].endswith("44 88 144"))
        self.assertTrue(all(len(line) < 640 for line in lines))
