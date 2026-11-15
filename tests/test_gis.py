import json
import math
import tempfile
import unittest
from pathlib import Path

from mercfoundry import gis, png


def district(**kw):
    spec = {"name": "d", "center": [31.5, 34.45], "size": 1000, "turn": 0, "at": [0, 0], "feather": 100}
    spec.update(kw)
    return gis.District(spec)


class FlatElevation:
    def __init__(self, f):
        self.f = f

    def at(self, lat, lon):
        return self.f(lat, lon)


class DistrictTest(unittest.TestCase):
    def test_round_trip(self):
        d = district(turn=36, at=[300, -200])
        for x, z in [(300, -200), (10, 20), (-400, 700)]:
            lat, lon = d.to_world(x, z)
            bx, bz = d.to_map(lat, lon)
            self.assertAlmostEqual(bx, x, places=6)
            self.assertAlmostEqual(bz, z, places=6)

    def test_north_is_lower_z_east_is_x(self):
        d = district()
        x, z = d.to_map(31.5 + 0.001, 34.45)
        self.assertAlmostEqual(x, 0, places=6)
        self.assertAlmostEqual(z, -110.574, places=3)
        x, z = d.to_map(31.5, 34.45 + 0.001)
        self.assertGreater(x, 90)
        self.assertAlmostEqual(z, 0, places=6)

    def test_turn_is_counter_clockwise(self):
        d = district(turn=90)
        x, z = d.to_map(31.5, 34.45 + 0.001)       # east -> north (lower z)
        self.assertAlmostEqual(x, 0, places=6)
        self.assertLess(z, -90)

    def test_weight_feathers(self):
        d = district()
        self.assertEqual(d.weight(499, 0), 1.0)
        self.assertEqual(d.weight(601, 0), 0.0)
        self.assertTrue(0 < d.weight(550, 0) < 1)

    def test_bbox_covers_square(self):
        d = district(turn=36)
        s, w, n, e = d.bbox()
        for x, z in [(-600, -600), (600, 600), (-600, 600), (600, -600)]:
            lat, lon = d.to_world(x, z)
            self.assertTrue(s <= lat <= n and w <= lon <= e)

    def test_tiles_for(self):
        tiles = gis.tiles_for(district().bbox())
        self.assertTrue(1 <= len(tiles) <= 4)
        z, x, y = tiles[0]
        self.assertEqual(z, gis.ZOOM)


class HeightsTest(unittest.TestCase):
    def test_blend_base_and_sea(self):
        d = district(size=800, feather=200)
        plan = {"districts": [d], "base": 20.0, "vertical": 1.0, "sea_floor": 12.0}
        # land 10 m high east of the centre, sea west of it
        elev = FlatElevation(lambda lat, lon: 10.0 if lon > 34.45 else -1.0)
        hm = gis.heights(plan, elev)
        mid = gis.SIZE // 2
        self.assertAlmostEqual(hm[mid][mid + 10], 30.0)
        self.assertAlmostEqual(hm[mid][mid - 10], 12.0)
        self.assertEqual(hm[0][0], 20.0)                 # far outside: base

    def test_png_round_trip(self):
        hm = [[20.0 + (r + c) * 0.01 for c in range(gis.SIZE)] for r in range(gis.SIZE)]
        data, meta = gis.heights_png(hm)
        from mercfoundry import terrain
        units = terrain.units_from_png(data, meta)
        self.assertAlmostEqual(units[100][200] * gis.STEP, hm[100][200], delta=gis.STEP)


class GeometryTest(unittest.TestCase):
    def test_min_rect_of_turned_box(self):
        a = math.radians(30)
        c, s = math.cos(a), math.sin(a)
        box = [(x * c - z * s + 5, x * s + z * c - 3) for x, z in [(-10, -4), (10, -4), (10, 4), (-10, 4)]]
        cx, cz, L, W, ang = gis.min_rect(box)
        self.assertAlmostEqual(cx, 5, places=6)
        self.assertAlmostEqual(cz, -3, places=6)
        self.assertAlmostEqual(L, 20, places=6)
        self.assertAlmostEqual(W, 8, places=6)
        self.assertAlmostEqual(math.sin(2 * ang), math.sin(2 * a), places=6)

    def test_fill_polygon(self):
        side = 11
        grid = bytearray(side * side)
        gis.fill_polygon(grid, side, [(2, 2), (6, 2), (6, 6), (2, 6)], 200, 1.0, 0.0)
        self.assertEqual(grid[4 * side + 4], 200)
        self.assertEqual(grid[8 * side + 8], 0)

    def test_clip_line(self):
        d = district(size=100)
        parts = gis.clip_line([(-80, 0), (-40, 0), (0, 0), (40, 0), (80, 0)], d)
        self.assertEqual(parts, [[(-40, 0), (0, 0), (40, 0)]])


class BuildingsTest(unittest.TestCase):
    def test_kind_and_model(self):
        self.assertEqual(gis.building_kind({"building": "warehouse"}, 300, 0), "shed")
        self.assertEqual(gis.building_kind({}, 100, 8), "tower")
        self.assertEqual(gis.building_kind({}, 100, 4), "block")
        self.assertEqual(gis.building_kind({}, 100, 0), "house")
        model, turned, ml, mw = gis.pick_model("shed", 23, 13.5)
        self.assertEqual(model, "warehouse_bld_warehouse01")
        self.assertTrue(turned)

    def test_place_skips_overlaps_and_small(self):
        ground = gis.Ground([[20.0] * gis.SIZE for _ in range(gis.SIZE)])
        box = lambda x, z, w=16, d=9: [(x - w / 2, z - d / 2), (x + w / 2, z - d / 2), (x + w / 2, z + d / 2), (x - w / 2, z + d / 2)]
        foot = [({"building": "yes"}, box(0, 0)), ({"building": "yes"}, box(1, 1)),
                ({"building": "yes"}, box(100, 0)), ({"building": "yes"}, box(200, 0, 4, 4))]
        objects, skipped = gis.place_buildings(foot, ground)
        self.assertEqual(len(objects), 2)
        self.assertEqual(skipped, 1)
        self.assertTrue(objects[0]["from"].startswith("model:"))
        self.assertEqual(objects[0]["position"][1], 20.0)
        self.assertEqual(len({o["name"] for o in objects}), 2)

    def test_yaw_follows_long_side(self):
        ground = gis.Ground([[20.0] * gis.SIZE for _ in range(gis.SIZE)])
        # a 16 x 9 house turned so its long side runs along +z: yaw +-90
        foot = [({"building": "house"}, [(-4.5, -8), (4.5, -8), (4.5, 8), (-4.5, 8)])]
        objects, _ = gis.place_buildings(foot, ground)
        self.assertAlmostEqual(abs(objects[0]["yaw"]), 90.0, places=3)


class BuildTest(unittest.TestCase):
    def test_build_writes_mod_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod = Path(tmp)
            (mod / "maps").mkdir()
            (mod / "maps" / "town.json").write_text('{"from": "blank"}', encoding="utf-8")
            d = district(size=600, feather=50)
            cache = mod / "gis" / "cache"
            (cache / "osm").mkdir(parents=True)
            (cache / "dem").mkdir(parents=True)
            # elevation: 5 m everywhere, as Terrarium tiles
            v = 5 + 32768
            px = bytes([v // 256, v % 256, 0, 255]) * (256 * 256)
            for z, x, y in gis.tiles_for(d.bbox()):
                (cache / "dem" / f"{z}_{x}_{y}.png").write_bytes(png.encode(256, 256, px, 4))
            ll = lambda x, z: dict(zip(("lat", "lon"), d.to_world(x, z)))
            house = [ll(-8, -5), ll(8, -5), ll(8, 5), ll(-8, 5), ll(-8, -5)]
            farm = [ll(100, 100), ll(200, 100), ll(200, 200), ll(100, 200), ll(100, 100)]
            road = [ll(-250, 50), ll(250, 50)]
            doc = {"elements": [
                {"type": "way", "tags": {"building": "house"}, "geometry": house},
                {"type": "way", "tags": {"landuse": "farmland"}, "geometry": farm},
                {"type": "way", "tags": {"highway": "primary"}, "geometry": road},
                {"type": "way", "tags": {"highway": "footway"}, "geometry": road},
            ]}
            (cache / "osm" / "d.json").write_text(json.dumps(doc), encoding="utf-8")
            plan = {"map": "town", "districts": [d], "base": 20.0}
            out = gis.build(plan, mod, log=lambda *a: None)
            self.assertEqual(out["objects"], 1)
            self.assertEqual(out["roads"], 1)
            layer = json.loads((mod / "encounters" / "town_gis.json").read_text(encoding="utf-8"))
            self.assertEqual(layer["map"], "TOWN")
            self.assertAlmostEqual(layer["objects"][0]["position"][1], 25.0, delta=0.5)
            meta = json.loads((mod / "terrain" / "town.heights.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["terrain"], "town")
            table = json.loads((mod / "terrain" / "town.layers.json").read_text(encoding="utf-8"))["layers"]
            self.assertEqual(len(table), len(gis.LAYERS))
            farm_i = [l["key"] for l in gis.LAYERS].index("farm")
            w, h, rgba = png.decode((mod / "terrain" / f"town.layer{farm_i:02d}.nk_tt_farm.png").read_bytes())
            self.assertEqual(w, gis.SIZE + 1)
            c = int((150 + gis.HALF) / gis.SPACING)
            self.assertEqual(rgba[(c * w + c) * 4], 255)
            self.assertTrue((mod / "gis" / "town.preview.png").exists())


if __name__ == "__main__":
    unittest.main()
