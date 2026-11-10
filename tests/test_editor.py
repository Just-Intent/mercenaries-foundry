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

    def test_three_turn_is_opposite_game_turn(self):
        # three.js turns about +Y the other way round from the game (Z is flipped).
        th = math.radians(30)
        ry = [math.cos(th), 0, -math.sin(th), 0, 1, 0, math.sin(th), 0, math.cos(th)]
        self.assertAlmostEqual(scene.turn_between(IDENTITY, scene.from_three(ry)), -30.0, places=3)

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
        live.send([{"op": "inspect", "position": [1, 2, 3], "radius": 10}])
        self.assertEqual(live.poll(""), "inspect 1.000 2.000 3.000 10.000\n")
