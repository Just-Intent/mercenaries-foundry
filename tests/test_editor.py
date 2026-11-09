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

    def test_static_files_are_served_from_the_package(self):
        self.assertIn(b"importmap", server._static("static/index.html"))
        self.assertIsNotNone(server._static("vendor/three/build/three.module.js"))
        self.assertIsNone(server._static("static/../scene.py"))
        self.assertIsNone(server._static("static/missing.js"))
