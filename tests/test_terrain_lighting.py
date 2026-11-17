import json
import tempfile
import unittest
from pathlib import Path

from mercfoundry import modbuild, terrain, terrain_layers, terrain_light
from mercfoundry.hashing import name_hash
from tests.test_dsk import build as build_dsk
from tests.test_terrain import SIZE, STEP, hills, lit_colr, tern_record

LEVELS = "[LevelNamesList]\nLV_isle\n\n[LV_isle]\nName\t\tisle\nPath\t\t\\TEST\\\nConfigFile\t\\TEST\\isle.ini\n"


def lum(v):
    return sum(terrain_layers.colr_rgb(v))


class SunAngleTests(unittest.TestCase):
    """SunAngle a b: the light travels along (-sin a cos b, cos a, -sin a sin b) (0x211C00)."""

    def test_angle_to_bearing_and_height(self):
        self.assertEqual(terrain_light.sun_from_angle("140 180"), (270.0, 50.0))      # SW: 50 up, in the west
        self.assertEqual(terrain_light.sun_from_angle("130\t80"), (170.0, 40.0))      # blank, NW
        self.assertEqual(terrain_light.sun_angle(270, 50), "140 180")
        self.assertEqual(terrain_light.sun_angle(200.5, 62.25), "152.25 110.5")
        for bearing, height in ((0, 10), (90, 45), (245, 61.5)):
            back = terrain_light.sun_from_angle(terrain_light.sun_angle(bearing, height))
            self.assertAlmostEqual(back[0], bearing)
            self.assertAlmostEqual(back[1], height)
        # the game's light direction for "140 180", turned round, is the sun's
        import math
        a, b = math.radians(140), math.radians(180)
        light = (-math.sin(a) * math.cos(b), math.cos(a), -math.sin(a) * math.sin(b))
        for s, l in zip(terrain_light.sun_vector(270, 50), light):
            self.assertAlmostEqual(s, -l)
        self.assertGreater(terrain_light.sun_vector(90, 30)[0], 0.8)       # east is +x
        self.assertGreater(terrain_light.sun_vector(180, 30)[2], 0.8)      # south is +z

    def test_shadow_behind_a_wall(self):
        H = [[0.0] * SIZE for _ in range(SIZE)]
        for z in range(SIZE):
            H[z][10] = 30.0                               # a wall along z at x = 10
        sun = terrain_light.sun_vector(270, 20)           # low, in the west (-x)
        mask = terrain_light.shadow_mask(H, SIZE, 8.0, sun)
        self.assertEqual(mask[5][12], 1)                  # just east of the wall: in its shadow
        self.assertEqual(mask[5][8], 0)                   # west of it: in the sun
        self.assertEqual(mask[5][31], 0)                  # far east: the shadow has ended
        high = terrain_light.shadow_mask(H, SIZE, 8.0, terrain_light.sun_vector(270, 85))
        self.assertEqual(sum(sum(r) for r in high), 0)    # a sun overhead casts none past a cell

    def test_settings(self):
        s = terrain_light.lighting_settings({}, "140 180")
        self.assertEqual((s["bearing"], s["height"]), (270.0, 50.0))
        self.assertEqual(s["ambient"], tuple(float(c) for c in terrain_light.DEFAULT_AMBIENT))
        for bad in ({"bearing": 10}, {"bearing": 10, "height": 95}, {"ambient": [1, 2]}, {"shadow": 2}, {"sun": 1},
                    {"diffuse": [0, 0, 300]}):
            with self.assertRaises(ValueError):
                terrain_light.lighting_settings(bad, "140 180")
        with self.assertRaises(ValueError):
            terrain_light.lighting_settings({}, None)


class TintMapTests(unittest.TestCase):
    """lighting.json "tint_map": a PNG whose colour tints the lit ground (128 neutral)."""

    def test_tint_multiplies_and_strength_eases(self):
        import os
        from mercfoundry import png
        units = [[0] * SIZE for _ in range(SIZE)]                       # flat: every vertex lit the same
        flat = terrain_layers.encode_colr([[terrain_layers.rgb_colr(160, 200, 224)] * 81] * 16)
        chunks = terrain.tern_chunks(tern_record(units, flat))
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            old = os.environ.get("MERCFOUNDRY_CACHE")
            os.environ["MERCFOUNDRY_CACHE"] = str(d / "cache")
            try:
                # left half (x < 16) warm (255, 128, 0), right half neutral
                px = bytes(v for z in range(SIZE) for x in range(SIZE) for v in ((255, 128, 0, 255) if x < 16 else (128, 128, 128, 255)))
                (d / "t.png").write_bytes(png.encode(SIZE, SIZE, px))
                tint = terrain_light.load_tint_map(d / "t.png", SIZE)
                base = {"bearing": 180, "height": 60, "ambient": [60, 60, 60], "diffuse": [60, 60, 60]}
                plain, _ = terrain_light.bake(chunks, terrain_light.lighting_settings(base))
                full, note = terrain_light.bake(chunks, terrain_light.lighting_settings({**base, "tint_map": "t.png"}), tint)
                half, _ = terrain_light.bake(chunks, terrain_light.lighting_settings(
                    {**base, "tint_map": "t.png", "tint_strength": 0.5}), tint)
                with self.assertRaises(ValueError):                    # named but not loaded
                    terrain_light.bake(chunks, terrain_light.lighting_settings({**base, "tint_map": "t.png"}))
                with self.assertRaises(ValueError):                    # wrong size
                    (d / "small.png").write_bytes(png.encode(4, 4, bytes(64)))
                    terrain_light.load_tint_map(d / "small.png", SIZE)
            finally:
                if old is None:
                    del os.environ["MERCFOUNDRY_CACHE"]
                else:
                    os.environ["MERCFOUNDRY_CACHE"] = old
        self.assertIn("tinted by t.png", note)
        rgb = lambda body, x: terrain_layers.colr_rgb(terrain_layers.decode_colr(body, 16)[1][2 * 9 + (x - 8)])
        p, f, h = rgb(plain, 10), rgb(full, 10), rgb(half, 10)          # patch 1 is x 8-16: x = 10 is warm
        self.assertAlmostEqual(f[0], min(248, p[0] * 255 / 128), delta=8)
        self.assertAlmostEqual(f[1], p[1], delta=8)
        self.assertEqual(f[2], 0)
        self.assertAlmostEqual(h[2], p[2] / 2, delta=8)
        self.assertEqual(rgb(full, 16), rgb(plain, 16))                 # x = 16: neutral
        for bad in ({"tint_map": "../x.png"}, {"tint_map": "x.jpg"}, {"tint_map": "x.png", "tint_strength": 2}):
            with self.assertRaises(ValueError):
                terrain_light.lighting_settings({**base, **bad})
        self.assertNotIn("tint_strength", terrain_light.lighting_settings(base))


class BakeTests(unittest.TestCase):
    def test_bake_lights_slopes_facing_the_sun(self):
        units = hills()
        flat = terrain_layers.encode_colr([[terrain_layers.rgb_colr(160, 200, 224)] * 81] * 16)
        chunks = terrain.tern_chunks(tern_record(units, flat))
        with tempfile.TemporaryDirectory() as d:
            import os
            old = os.environ.get("MERCFOUNDRY_CACHE")
            os.environ["MERCFOUNDRY_CACHE"] = d
            try:
                settings = terrain_light.lighting_settings({"bearing": 225, "height": 45, "shadow": 0.5})
                body, note = terrain_light.bake(chunks, settings)
                again, _ = terrain_light.bake(chunks, settings)        # from the cache
            finally:
                if old is None:
                    del os.environ["MERCFOUNDRY_CACHE"]
                else:
                    os.environ["MERCFOUNDRY_CACHE"] = old
        self.assertEqual(body, again)
        self.assertIn("SunAngle 135 135", note)
        self.assertNotIn("tint", note)                      # even colours: no sun of their own to keep tint from
        cols = terrain_layers.decode_colr(body, 16)
        H = [[u * STEP for u in row] for row in units]
        sun = terrain_light.sun_vector(225, 45)
        facing, away = [], []
        for pi, vals in enumerate(cols):
            pz, px = divmod(pi, 4)
            for j in range(8):
                for i in range(8):
                    x, z = px * 8 + i, pz * 8 + j
                    n = terrain_light._normal(H, SIZE, 8.0, x, z)
                    dot = sum(a * b for a, b in zip(n, sun))
                    (facing if dot > 0.9 else away if dot < 0.3 else []).append(lum(vals[j * 9 + i]))
        self.assertTrue(facing and away)
        self.assertGreater(sum(facing) / len(facing), sum(away) / len(away) + 60)

    def test_build_lights_a_map_from_its_sun_angle(self):
        units = hills()
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            data = d / "data"
            (data / "TEST").mkdir(parents=True)
            (data / "levels.ini").write_text(LEVELS, encoding="latin-1")
            (data / "TEST" / "isle.ini").write_text("SunAngle\t\t140 180\n", encoding="latin-1")
            build_dsk(data / "assets.dsk", [(name_hash("isle"), name_hash("Terrain"), tern_record(units, lit_colr(units)))])
            mod = d / "mod"
            (mod / "terrain").mkdir(parents=True)
            (mod / "terrain" / "isle.lighting.json").write_text(json.dumps({"diffuse": [120, 110, 90]}), encoding="utf-8")
            import os
            os.environ["MERCFOUNDRY_CACHE"] = str(d / "cache")
            try:
                result = modbuild.build(mod, data, d / "out")
            finally:
                del os.environ["MERCFOUNDRY_CACHE"]
            self.assertTrue(any("terrain isle" in c and "SunAngle 140 180" in c for c in result.changes), result.changes)
            # a tint map beside it, named in the lighting file
            from mercfoundry import png
            (mod / "terrain" / "isle.tint.png").write_bytes(png.encode(SIZE, SIZE, bytes([200, 128, 90, 255]) * (SIZE * SIZE)))
            (mod / "terrain" / "isle.lighting.json").write_text(json.dumps({"tint_map": "isle.tint.png"}), encoding="utf-8")
            os.environ["MERCFOUNDRY_CACHE"] = str(d / "cache")
            try:
                result = modbuild.build(mod, data, d / "out2")
                self.assertTrue(any("tinted by isle.tint.png" in c for c in result.changes), result.changes)
                (mod / "terrain" / "isle.lighting.json").write_text(json.dumps({"tint_map": "gone.png"}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    modbuild.build(mod, data, d / "out3")
            finally:
                del os.environ["MERCFOUNDRY_CACHE"]
            # the mod's own sky/<map>.json SunAngle wins over the config's
            (mod / "sky").mkdir()
            (mod / "sky" / "isle.json").write_text(json.dumps({"base": {"SunAngle": "150 270"}}), encoding="utf-8")
            self.assertEqual(modbuild._sun_angle(modbuild._Data(data), mod, "isle"), "150 270")


if __name__ == "__main__":
    unittest.main()
