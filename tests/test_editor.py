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
