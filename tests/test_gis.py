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


def plan_of(places, size=64, **kw):
    p = {"grid": gis.Grid(size), "places": places,
         "districts": [d for d in places if isinstance(d, gis.District)], "base": 20.0}
    p.update(kw)
    return p


class GridTest(unittest.TestCase):
    def test_game_offsets(self):
        g = gis.Grid(512)
        self.assertEqual(g.x(0), -2048)
        self.assertEqual(g.z(0), -2040)          # the game's own z offset
        self.assertEqual(g.col(g.x(7)), 7)
        self.assertEqual(gis.Grid(1024).x(0), -4096)


class DistrictTest(unittest.TestCase):
    def test_round_trip(self):
        d = district(turn=36, at=[300, -200])
        for x, z in [(300, -200), (10, 20), (-400, 700)]:
            bx, bz = d.to_map(*d.to_world(x, z))
            self.assertAlmostEqual(bx, x, places=6)
            self.assertAlmostEqual(bz, z, places=6)

    def test_north_is_lower_z_east_is_x(self):
        d = district()
        x, z = d.to_map(31.5 + 0.001, 34.45)
        self.assertAlmostEqual(x, 0, places=6)
        self.assertAlmostEqual(z, -110.574, places=3)
        x, z = d.to_map(31.5, 34.45 + 0.001)
        self.assertGreater(x, 90)

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

    def test_tiles_for(self):
        tiles = gis.tiles_for(district().bbox())
        self.assertTrue(1 <= len(tiles) <= 4)
        self.assertEqual(tiles[0][0], gis.ZOOM)


def strip(**kw):
    spec = {"origin": [31.5, 34.45], "bearing": 0, "coast_x": -100, "across": 0.5, "curve": 0.0,
            "along": [[-4, 400], [0, 0], [4, -200]]}
    spec.update(kw)
    return gis.Strip(spec)


class StripTest(unittest.TestCase):
    def setUp(self):
        # the shore runs north-south 1 km west of the origin (bearing 0: along = north, across = east)
        kx, _ = gis._local(31.5)
        self.shore_lon = 34.45 - 1000 / kx
        self.elev = FlatElevation(lambda lat, lon: 5.0 if lon > self.shore_lon else -3.0)

    def test_shore_found_and_put_at_coast_x(self):
        s = strip()
        s.prepare(self.elev)
        self.assertAlmostEqual(s._shore(0), -1000, delta=60)
        x, z = s.to_map(31.5, self.shore_lon)
        self.assertAlmostEqual(x, -100, delta=30)
        x, _ = s.to_map(31.5, 34.45)                          # 1 km inland: 500 game metres east
        self.assertAlmostEqual(x, 400, delta=30)

    def test_along_squeezes_unevenly(self):
        s = strip()
        s.prepare(self.elev)
        _, z0 = s.to_map(31.5, 34.45)
        _, zn = s.to_map(31.5 + 4000 / 110574, 34.45)          # 4 km north: z -200
        _, zs = s.to_map(31.5 - 4000 / 110574, 34.45)          # 4 km south: z 400
        self.assertAlmostEqual(z0, 0, delta=1)
        self.assertAlmostEqual(zn, -200, delta=1)
        self.assertAlmostEqual(zs, 400, delta=1)

    def test_round_trip(self):
        s = strip(bearing=36, curve=0.3)
        s.prepare(self.elev)
        for x, z in [(0, 0), (250, -150), (-300, 350)]:
            bx, bz = s.to_map(*s.to_world(x, z))
            self.assertAlmostEqual(bx, x, places=4)
            self.assertAlmostEqual(bz, z, places=4)

    def test_along_must_run_north(self):
        with self.assertRaises(ValueError):
            strip(along=[[0, 0], [4, 100]])

    def test_outline_clips(self):
        s = strip()
        ring = [(31.49, 34.43), (31.49, 34.47), (31.51, 34.47), (31.51, 34.43), (31.49, 34.43)]
        s.prepare(self.elev, [ring])
        self.assertTrue(s.contains_map(*s.to_map(31.5, 34.45)))
        self.assertFalse(s.contains_map(*s.to_map(31.5, 34.49)))
        self.assertTrue(s.contains_world(31.5, 34.45))

    def test_rotated_frame(self):
        s = strip(rotate=45)
        s.prepare(self.elev)
        for x, z in [(0, 0), (250, -150), (-300, 350)]:
            bx, bz = s.to_map(*s.to_world(x, z))
            self.assertAlmostEqual(bx, x, places=4)
            self.assertAlmostEqual(bz, z, places=4)
            fx, fz = s.frame(*s.unframe(x, z))
            self.assertAlmostEqual(fx, x, places=6)
            self.assertAlmostEqual(fz, z, places=6)
        # north (frame -z) points to the map's top right; inland (frame +x) to the bottom right
        nx, nz = s.unframe(0, -100)
        self.assertGreater(nx, 50)
        self.assertLess(nz, -50)
        ex, ez = s.unframe(100, 0)
        self.assertGreater(ex, 50)
        self.assertGreater(ez, 50)
        # the sea is north-west of the shore
        self.assertTrue(s.is_sea(*s.unframe(-400, 0)))
        self.assertFalse(s.is_sea(*s.unframe(200, 0)))

    def test_stitch_rings(self):
        a, b, c, d = (0, 0), (0, 1), (1, 1), (1, 0)
        rings = gis.stitch_rings([[a, b], [c, b], [c, d, a]])
        self.assertEqual(len(rings), 1)
        self.assertEqual(rings[0][0], rings[0][-1])
        self.assertEqual(len(rings[0]), 5)


class HeightsTest(unittest.TestCase):
    def test_blend_base_and_sea(self):
        d = district(size=300, feather=100)
        plan = plan_of([d], size=256, vertical=1.0, sea_drop=2.0, sea_vertical=0.5, sea_x=0)
        # land 10 m high east of the centre, sea (3 m deep) west of it
        elev = FlatElevation(lambda lat, lon: 10.0 if lon > 34.45 else -3.0)
        hm, sea = gis.heights(plan, elev)
        g = plan["grid"]
        mid = int(g.col(0))
        row = int(g.row(0))
        self.assertAlmostEqual(hm[row][mid + 5], 30.0)
        self.assertFalse(sea[row][mid + 5])
        self.assertAlmostEqual(hm[row][mid - 5], 20.0 - 3.5)
        self.assertEqual(hm[0][g.size - 1], 20.0)             # far (over 600 m) from the district: base

    def test_inland_dip_stays_land(self):
        d = district(size=2000, feather=0)
        plan = plan_of([d])
        # a dip below 0 m in the middle, land all round: not sea
        elev = FlatElevation(lambda lat, lon: -1.0 if abs(lon - 34.45) < 0.001 and abs(lat - 31.5) < 0.001 else 4.0)
        hm, sea = gis.heights(plan, elev)
        self.assertFalse(any(any(r) for r in sea))

    def test_knee_softens_hills(self):
        d = district(size=2000, feather=0)
        plan = plan_of([d], vertical=1.0, knee=40, above=0.25)
        hm, _ = gis.heights(plan, FlatElevation(lambda lat, lon: 120.0))
        g = plan["grid"]
        self.assertAlmostEqual(hm[int(g.row(0))][int(g.col(0))], 20.0 + 40 + 80 * 0.25)

    def test_sea_floor_falls_from_a_strips_shore(self):
        kx, _ = gis._local(31.5)
        shore_lon = 34.45 - 1000 / kx
        elev = FlatElevation(lambda lat, lon: 5.0 if lon > shore_lon else 0.0)    # the sea reads 0 m
        s = strip(along=[[-4, 1000], [0, 0], [4, -1000]], across=1.0)
        s.prepare(elev)
        plan = plan_of([s], size=256, sea_drop=3.0, sea_floor_min=14.0, sea_slope=0.02)
        hm, sea = gis.heights(plan, elev)
        g = plan["grid"]
        row = int(g.row(0))
        shore_c = int(g.col(s.shore_x(0)))
        near, far = hm[row][shore_c - 2], hm[row][shore_c - 100]
        self.assertTrue(sea[row][shore_c - 2] and sea[row][shore_c - 100])
        self.assertAlmostEqual(near, 20.0 - 3.0 - 16 * 0.02, delta=0.5)
        self.assertAlmostEqual(far, 20.0 - 14.0, delta=0.01)                # down to the floor's limit

    def test_png_round_trip(self):
        n = 64
        hm = [[20.0 + (r + c) * 0.01 for c in range(n)] for r in range(n)]
        data, meta = gis.heights_png(hm)
        from mercfoundry import terrain
        units = terrain.units_from_png(data, meta)
        self.assertAlmostEqual(units[10][20] * gis.STEP, hm[10][20], delta=gis.STEP)


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
        parts = gis.clip_line([(-80, 0), (-40, 0), (0, 0), (40, 0), (80, 0)], d.contains_map)
        self.assertEqual(parts, [[(-40, 0), (0, 0), (40, 0)]])

    def test_point_in_polygon(self):
        sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
        self.assertTrue(gis.point_in_polygon(5, 5, sq))
        self.assertFalse(gis.point_in_polygon(15, 5, sq))


class BuildingsTest(unittest.TestCase):
    def setUp(self):
        g = gis.Grid(64)
        self.ground = gis.Ground([[20.0] * g.size for _ in range(g.size)], g)

    def test_kind_and_model(self):
        self.assertEqual(gis.building_kind({"building": "warehouse"}, 300, 0), "shed")
        self.assertEqual(gis.building_kind({}, 100, 8), "tower")
        self.assertEqual(gis.building_kind({}, 100, 4), "block")
        self.assertEqual(gis.building_kind({}, 100, 0), "house")
        model, turned, ml, mw = gis.pick_model("shed", 23, 13.5)
        self.assertEqual(model, "warehouse_bld_warehouse01")
        self.assertTrue(turned)

    def test_place_skips_overlaps_and_small(self):
        box = lambda x, z, w=16, d=9: [(x - w / 2, z - d / 2), (x + w / 2, z - d / 2), (x + w / 2, z + d / 2), (x - w / 2, z + d / 2)]
        foot = [({"building": "yes"}, box(0, 0)), ({"building": "yes"}, box(1, 1)),
                ({"building": "yes"}, box(100, 0)), ({"building": "yes"}, box(200, 0, 4, 4))]
        objects, skipped = gis.place_buildings_simple(foot, self.ground)
        self.assertEqual(len(objects), 2)
        self.assertEqual(skipped, 1)
        self.assertTrue(objects[0]["from"].startswith("model:"))
        self.assertEqual(objects[0]["position"][1], 20.0)

    def test_yaw_follows_long_side(self):
        foot = [({"building": "house"}, [(-4.5, -8), (4.5, -8), (4.5, 8), (-4.5, 8)])]
        objects, _ = gis.place_buildings_simple(foot, self.ground)
        self.assertAlmostEqual(abs(objects[0]["yaw"]), 90.0, places=3)

    def test_footprint_keeps_real_size_in_a_squeezed_place(self):
        s = strip(along=[[-4, 1000], [4, -1000]])           # along: 1/4 scale
        s.prepare(FlatElevation(lambda lat, lon: 5.0 if lon > 34.44 else -3.0))
        kx, ky = gis._local(31.5)
        lat, lon = 31.5, 34.45
        # 20 m east-west by 10 m north-south
        geo = [(lat - 5 / ky, lon - 10 / kx), (lat - 5 / ky, lon + 10 / kx), (lat + 5 / ky, lon + 10 / kx),
               (lat + 5 / ky, lon - 10 / kx)]
        area, tags, cx, cz, L, W, ang, outline = gis.footprint(s, {"building": "yes"}, geo)
        self.assertAlmostEqual(L, 20, delta=0.1)
        self.assertAlmostEqual(W, 10, delta=0.1)
        self.assertAlmostEqual(math.sin(ang), 0, delta=0.01)   # long side stays east-west


def _overlap(a, b):
    """Separating-axis test of two convex quads [(x, z)] (touching is not overlap)."""
    for quad in (a, b):
        for i in range(4):
            (x0, z0), (x1, z1) = quad[i], quad[(i + 1) % 4]
            nx, nz = z0 - z1, x1 - x0
            pa = [nx * x + nz * z for x, z in a]
            pb = [nx * x + nz * z for x, z in b]
            if max(pa) <= min(pb) + 1e-6 or max(pb) <= min(pa) + 1e-6:
                return False
    return True


class LayoutTest(unittest.TestCase):
    def setUp(self):
        kx, _ = gis._local(31.5)
        self.shore_lon = 34.45 - 1000 / kx
        self.elev = FlatElevation(lambda lat, lon: 5.0 if lon > self.shore_lon else -3.0)

    def test_density_and_style(self):
        s = strip()
        s.prepare(self.elev)
        d = gis.Density(s, cell=50, reach=0)
        kx, ky = gis._local(31.5)
        # a 20 x 20 m house in one 50 m cell: 16% covered
        lat, lon = 31.5 + 10 / ky, 34.45 + 10 / kx
        sq = [(lat - 10 / ky, lon - 10 / kx), (lat - 10 / ky, lon + 10 / kx), (lat + 10 / ky, lon + 10 / kx),
              (lat + 10 / ky, lon - 10 / kx), (lat - 10 / ky, lon - 10 / kx)]
        d.add({"building": "house", "building:levels": "2"}, sq)
        share, floors, sheds = d.at_world(*s._ac(lat, lon))
        self.assertAlmostEqual(share, 0.16, places=2)
        self.assertEqual((floors, sheds), (2.0, 0.0))
        self.assertIsNone(gis.style_of(0.0, 0, 0))
        self.assertEqual(gis.style_of(0.05, 0, 0), "sparse")
        self.assertEqual(gis.style_of(0.12, 0, 0), "suburb")
        self.assertEqual(gis.style_of(0.25, 2, 0), "city")
        self.assertEqual(gis.style_of(0.5, 1, 0), "camp")
        self.assertEqual(gis.style_of(0.3, 8, 0), "towers")
        self.assertEqual(gis.style_of(0.3, 1, 0.8), "industry")

    def test_blocks_stay_in_their_rooms(self):
        import random
        for style in gis.STYLES:
            if style == "sparse":
                continue
            got = gis.fill_block(0, 0, 80, 120, style, 0.5, random.Random(3))
            self.assertTrue(got, style)
            for kind, model, cx, cz, ex, ez, ang, room in got:
                self.assertIn(kind, gis.STYLES[style]["kinds"])
                self.assertGreaterEqual(cx - ex / 2, -1e-6)
                self.assertLessEqual(cx + ex / 2, 80 + 1e-6)
                self.assertGreaterEqual(cz - ez / 2, -1e-6)
                self.assertLessEqual(cz + ez / 2, 120 + 1e-6)

    def test_alternatives_slide_then_shrink(self):
        b = ("block", "dandong_bld_house02", 20, 20, 20.1, 12.6, 0.0, (0, 0, 40, 40))
        alts = list(gis.alternatives(b, ("block",)))
        self.assertEqual(alts[0][1], "dandong_bld_house02")
        self.assertGreater(len(alts), 5)
        for k, m, px, pz, sx, sz, a in alts:
            self.assertTrue(0 <= px - sx / 2 and px + sx / 2 <= 40 and 0 <= pz - sz / 2 and pz + sz / 2 <= 40)

    def test_lay_out_keeps_roads_clear_and_buildings_apart(self):
        s = strip(rotate=45, along=[[-2, 600], [0, 0], [2, -600]], across=0.6)
        ring = [(31.49, 34.43), (31.49, 34.47), (31.51, 34.47), (31.51, 34.43), (31.49, 34.43)]
        s.prepare(self.elev, [ring])
        dens = gis.Density(s)
        dens.at_world = lambda a, c: (0.3, 2.0, 0.0)          # built up everywhere: city
        road_z = 0.0                                           # a main road across the strip at frame z 0

        def blocked(x, z):
            return abs(s.frame(x, z)[1] - road_z) < 12 or not s.contains_map(x, z)
        g = gis.Grid(256)
        ground = gis.Ground([[20.0] * 256 for _ in range(256)], g)
        side = 257
        paint = bytearray(side * side)
        plan = {"marks": [], "max_buildings": None}
        objs = gis.lay_out(plan, s, dens, blocked, ground, lambda x, z: False, paint, side, g.x0, g.z0,
                           log=lambda *a: None)
        self.assertGreater(len(objs), 20)
        self.assertTrue(any(paint))
        rects = [o["_rect"] for o in objs]
        for o in objs:
            for x, z in o["_rect"]:
                self.assertFalse(blocked(x, z))
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                near = (abs(objs[i]["position"][0] - objs[j]["position"][0]) < 80
                        and abs(objs[i]["position"][2] - objs[j]["position"][2]) < 80)
                if near:
                    self.assertFalse(_overlap(rects[i], rects[j]), (objs[i], objs[j]))


class SeaZoneTest(unittest.TestCase):
    def test_zone_covers_sea_to_the_shore(self):
        g = gis.Grid(64)
        sea = [[c < 10 for c in range(64)] for _ in range(64)]
        hm = [[15.0 if c < 10 else 22.0 for c in range(64)] for _ in range(64)]
        zone = gis.sea_zone(sea, hm, g, 19.7)
        self.assertEqual(zone["properties"]["type"], "water")
        self.assertEqual(zone["properties"]["height"], "19.70")
        xs = [p[0] for p in zone["points"]]
        self.assertAlmostEqual(max(xs), g.x(9) + 24, places=3)
        self.assertLess(min(xs), g.x(0))
        self.assertIsNone(gis.sea_zone([[False] * 64 for _ in range(64)], hm, g, 19.7))


class JoinRoadsTest(unittest.TestCase):
    def test_pieces_join_end_to_end_but_not_at_junctions(self):
        r = [("primary", [(0, 0), (10, 0)]), ("primary", [(20, 0), (10, 0)]), ("primary", [(20, 0), (30, 5)])]
        self.assertEqual(gis.join_roads(r), [("primary", [(0, 0), (10, 0), (20, 0), (30, 5)])])
        r.append(("primary", [(10, 0), (10, 10)]))                      # a junction at (10, 0)
        self.assertEqual(len(gis.join_roads(r)), 3)
        r = [("primary", [(0, 0), (10, 0)]), ("secondary", [(10, 0), (20, 0)])]   # classes stay apart
        self.assertEqual(len(gis.join_roads(r)), 2)
        r = [("primary", [(0, 0), (10, 0)]), ("primary", [(10, 0), (2, 3)])]          # a hairpin: not joined
        self.assertEqual(len(gis.join_roads(r)), 2)

    def test_doubled_roads_dropped(self):
        main = ("primary", [(0, 0), (200, 0)])
        twin = ("secondary", [(10, 3), (190, 3)])          # on the primary's asphalt
        apart = ("secondary", [(0, 60), (200, 60)])
        cross = ("secondary", [(100, -100), (100, 100)])   # crosses it: mostly off it
        kept = gis.drop_doubled_roads([twin, main, apart, cross])
        self.assertEqual([k for k, _ in kept].count("primary"), 1)
        self.assertNotIn(twin, kept)
        self.assertIn(apart, kept)
        self.assertIn(cross, kept)


class SeaZonesTest(unittest.TestCase):
    def test_tiles_cover_a_diagonal_sea(self):
        g = gis.Grid(256)                                  # 2 km map
        n = g.size
        sea = [[c + r < 200 for c in range(n)] for r in range(n)]
        hm = [[15.0] * n for _ in range(n)]
        zones = gis.sea_zones(sea, hm, g, 19.7, tile=512)
        self.assertGreater(len(zones), 1)
        polys = [[(p[0], p[2]) for p in z["points"]] for z in zones]
        for z in zones:
            xs = [p[0] for p in z["points"]]
            self.assertLessEqual(max(xs) - min(xs), 512 + 8 + 1e-6)
            self.assertGreater(min(xs), g.x(0))                 # inside the map's edges
            self.assertGreater(min(p[2] for p in z["points"]), g.z(0))
            self.assertEqual((z["properties"]["numgrids"], z["properties"]["gridsize"]), ("16", "128"))
            self.assertLessEqual(len(z["points"]), 75)
            self.assertEqual(z["properties"]["type"], "water")
        for r in range(2, n, 6):
            for c in range(2, n, 6):
                if sea[r][c] and c + r < 190:
                    self.assertTrue(any(gis.point_in_polygon(g.x(c), g.z(r), p) for p in polys), (r, c))
        self.assertEqual(gis.sea_zones([[False] * n for _ in range(n)], hm, g, 19.7), [])


class BuildTest(unittest.TestCase):
    def test_build_writes_mod_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod = Path(tmp)
            (mod / "maps").mkdir()
            (mod / "maps" / "town.json").write_text('{"from": "blank", "size": 64}', encoding="utf-8")
            d = district(size=400, feather=50)
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
            farm = [ll(60, 60), ll(150, 60), ll(150, 150), ll(60, 150), ll(60, 60)]
            road = [ll(-180, 50), ll(180, 50)]
            doc = {"elements": [
                {"type": "way", "tags": {"building": "house"}, "geometry": house},
                {"type": "way", "tags": {"landuse": "farmland"}, "geometry": farm},
                {"type": "way", "tags": {"highway": "primary"}, "geometry": road},
                {"type": "way", "tags": {"highway": "footway"}, "geometry": road},
            ]}
            (cache / "osm" / "d.json").write_text(json.dumps(doc), encoding="utf-8")
            plan = plan_of([d], map="town")
            out = gis.build(plan, mod, log=lambda *a: None)
            self.assertEqual(out["objects"], 1)
            self.assertEqual(out["roads"], 1)
            self.assertFalse(out["sea"])
            layer = json.loads((mod / "encounters" / "town_gis.json").read_text(encoding="utf-8"))
            self.assertEqual(layer["map"], "TOWN")
            self.assertTrue(layer["unnamed"])
            self.assertAlmostEqual(layer["objects"][0]["position"][1], 25.0, delta=0.5)
            meta = json.loads((mod / "terrain" / "town.heights.json").read_text(encoding="utf-8"))
            self.assertEqual((meta["terrain"], meta["size"]), ("town", 64))
            table = json.loads((mod / "terrain" / "town.layers.json").read_text(encoding="utf-8"))["layers"]
            self.assertEqual(len(table), len(gis.LAYERS))
            farm_i = [l["key"] for l in gis.LAYERS].index("farm")
            w, h, rgba = png.decode((mod / "terrain" / f"town.layer{farm_i:02d}.nk_tt_farm.png").read_bytes())
            self.assertEqual(w, 65)
            g = plan["grid"]
            c, r = int(round(g.col(100))), int(round(g.row(100)))
            self.assertEqual(rgba[(r * w + c) * 4], 255)
            self.assertTrue((mod / "gis" / "town.preview.png").exists())


if __name__ == "__main__":
    unittest.main()


class FetchSplitTest(unittest.TestCase):
    def test_timed_out_box_fetched_in_quarters(self):
        import urllib.error
        import urllib.parse
        calls = []

        def get(url, body):
            q = urllib.parse.parse_qs(body.decode())["data"][0]
            calls.append(q)
            if len(calls) == 1:
                raise urllib.error.HTTPError(url, 504, "Gateway Timeout", {}, None)
            return json.dumps({"elements": [{"type": "way", "id": 7}, {"type": "way", "id": len(calls)}]}).encode()

        doc = gis._fetch_bbox((31.0, 34.0, 31.2, 34.2), None, get, lambda *a: None, "t")
        self.assertEqual(len(calls), 5)
        self.assertEqual(sorted(e["id"] for e in doc["elements"]), [2, 3, 4, 5, 7])     # way 7 once


class RasterTest(unittest.TestCase):
    def test_utm(self):
        x, y = gis.utm_from_latlon(0.0, 33.0, 36)            # on zone 36's central meridian, at the equator
        self.assertAlmostEqual(x, 500000.0, places=3)
        self.assertAlmostEqual(y, 0.0, places=3)
        x, y = gis.utm_from_latlon(31.5, 34.45, 36)          # Gaza City
        self.assertAlmostEqual(x, 637900, delta=2000)
        self.assertAlmostEqual(y, 3486200, delta=2000)

    def test_cog_window(self):
        # a tiled, zlib-compressed RGB TIFF with the horizontal predictor, read by range requests
        import struct
        import zlib
        w = h = 4
        tw = th = 2
        px = [[(x * 10 + y, x + 100, y * 3) for x in range(w)] for y in range(h)]
        tiles = []
        for ty in range(0, h, th):
            for tx in range(0, w, tw):
                raw = bytearray()
                for y in range(ty, ty + th):
                    row = [c for x in range(tx, tx + tw) for c in px[y][x]]
                    diff = row[:3] + [(row[i] - row[i - 3]) & 255 for i in range(3, len(row))]
                    raw += bytes(diff)
                tiles.append(zlib.compress(bytes(raw)))
        entries = [(256, 3, 1, w), (257, 3, 1, h), (259, 3, 1, 8), (277, 3, 1, 3), (317, 3, 1, 2),
                   (322, 3, 1, tw), (323, 3, 1, th)]
        n = len(entries) + 4
        ifd = 8
        data_at = ifd + 2 + n * 12 + 4
        offs_at, cnts_at, scale_at, tie_at = data_at, data_at + 16, data_at + 32, data_at + 56
        tiles_at = tie_at + 48
        offsets, pos = [], tiles_at
        for t in tiles:
            offsets.append(pos)
            pos += len(t)
        entries += [(324, 4, 4, offs_at), (325, 4, 4, cnts_at), (33550, 12, 3, scale_at), (33922, 12, 6, tie_at)]
        entries.sort()
        b = bytearray(b"II" + struct.pack("<HI", 42, ifd) + struct.pack("<H", len(entries)))
        for tag, typ, cnt, val in entries:
            b += struct.pack("<HHII", tag, typ, cnt, val) if typ != 3 or cnt > 2 else struct.pack("<HHIHH", tag, typ, cnt, val, 0)
        b += b"\0\0\0\0"
        b += struct.pack("<4I", *offsets) + struct.pack("<4I", *[len(t) for t in tiles])
        b += struct.pack("<3d", 10.0, 10.0, 0.0) + struct.pack("<6d", 0, 0, 0, 1000.0, 2000.0, 0)
        for t in tiles:
            b += t
        blob = bytes(b)
        f = gis.CogFile("mem", get_range=lambda url, s, e: blob[s:e + 1])
        self.assertEqual((f.width, f.height, f.samples, f.predictor), (4, 4, 3, 2))
        ww, hh, out = f.window(1, 1, 4, 3, log=lambda *a: None)
        self.assertEqual((ww, hh), (3, 2))
        self.assertEqual(tuple(out[0:3]), px[1][1])
        self.assertEqual(tuple(out[(1 * 3 + 2) * 3:(1 * 3 + 2) * 3 + 3]), px[2][3])

    def test_raster_lookup(self):
        import zlib
        with tempfile.TemporaryDirectory() as tmp:
            meta = {"crs": "latlon", "x0": 34.0, "y0": 32.0, "dx": 0.5, "dy": 0.5, "w": 2, "h": 2, "samples": 1}
            gis._save_raster(tmp, "lc", meta, bytes([10, 40, 50, 60]))
            r = gis.Raster(tmp, "lc")
            self.assertEqual(r.at(31.9, 34.1), (10,))
            self.assertEqual(r.at(31.2, 34.9), (60,))
            self.assertIsNone(r.at(30.0, 34.1))
            self.assertEqual(sorted(r.around(31.6, 34.4, 1)), [(10,), (40,), (50,), (60,)])


class KitTest(unittest.TestCase):
    KIT = {"gaza_house_a": {"kind": "house", "base": "nkvillage_bld_house02", "x": 10, "z": 12, "height": 10},
           "gaza_palm_a": {"kind": "palm", "base": "haeju_fruittree", "x": 8, "z": 8, "height": 10}}

    def test_own_kit_replaces_kinds_and_draws_own_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "models").mkdir()
            (Path(tmp) / "models" / "kit.json").write_text(json.dumps(self.KIT))
            kit, plants, geometry = gis.load_kit({"kit": "models/kit.json"}, tmp, log=lambda *a: None)
            self.assertEqual(kit["house"], [("gaza_house_a", 10.0, 12.0, 10.0)])
            self.assertEqual(kit["block"], gis.KIT["block"])                      # kinds it lacks stay retail
            self.assertEqual(list(plants), ["palm"])
            kit2, _, _ = gis.load_kit({"kit": {"file": "models/kit.json", "retail": True}}, tmp, log=lambda *a: None)
            self.assertEqual(len(kit2["house"]), len(gis.KIT["house"]) + 1)
        objs = gis.use_geometry([{"from": "model:gaza_house_a"}, {"from": "model:city_bld_office03"}], geometry)
        self.assertEqual(objs[0], {"from": "model:nkvillage_bld_house02", "set": {"GeometryFile": "gaza_house_a"}})
        self.assertEqual(objs[1], {"from": "model:city_bld_office03"})

    def test_no_kit_is_retail(self):
        self.assertEqual(gis.load_kit({}, ".", log=lambda *a: None), (gis.KIT, {}, {}))


class PlantsTest(unittest.TestCase):
    def test_trees_from_land_cover_away_from_roads_and_buildings(self):
        kx, _ = gis._local(31.5)
        shore = 34.45 - 1000 / kx
        s = strip(rotate=45, along=[[-2, 600], [0, 0], [2, -600]], across=0.6)
        s.prepare(FlatElevation(lambda lat, lon: 5.0 if lon > shore else -3.0))

        class Cover:                                           # tree cover everywhere
            def at(self, lat, lon):
                return (10,)
        g = gis.Grid(64)
        side = 65
        hm = [[30.0] * 64 for _ in range(64)]
        ground = gis.Ground(hm, g)
        road = ("primary", [(-200.0, 0.0), (200.0, 0.0)])
        house = {"_rect": [(50, 50), (70, 50), (70, 70), (50, 70)]}
        urban = bytearray(b"\xff" * (side * side))
        plants = {"palm": [("gaza_palm_a", 8, 8, 10)], "olive": [("gaza_olive_a", 6, 6, 5)]}
        plan = {"plants": {"max": 400, "spacing": 9.0}}
        out = gis.place_plants(plan, s, Cover(), ground, hm, 20.0, [road], [house], urban, side, g.x0, g.z0,
                               plants, log=lambda *a: None)
        self.assertTrue(0 < len(out) <= 400)
        width = gis.ROADS["primary"][1]
        for o in out:
            x, y, z = o["position"]
            self.assertEqual(y, 30.0)
            self.assertFalse(50 - 1 < x < 70 + 1 and 50 - 1 < z < 70 + 1, o)        # not in the house
            if abs(x) < 190:
                self.assertGreater(abs(z), width / 2, o)                              # not on the road
            self.assertIn(o["from"], ("model:gaza_palm_a", "model:gaza_olive_a"))
        self.assertTrue(any(o["_kind"] == "palm" and abs(abs(o["position"][2]) - (width / 2 + 3)) < 0.01
                            for o in out))                                            # palms line the road


class TimelineTest(unittest.TestCase):
    BANDS = [[31.30, {"rubble": 0.6, "tents": 0.5}], [31.50, {"rubble": 0.0, "tents": 0.0}]]

    def test_bands_interpolate_and_clamp(self):
        self.assertEqual(gis.timeline_at(self.BANDS, 31.6), {"rubble": 0.0, "tents": 0.0})
        self.assertEqual(gis.timeline_at(self.BANDS, 31.2), {"rubble": 0.6, "tents": 0.5})
        mid = gis.timeline_at(self.BANDS, 31.40)
        self.assertAlmostEqual(mid["rubble"], 0.3)
        self.assertAlmostEqual(mid["tents"], 0.25)

    def test_damage_swaps_models_by_latitude(self):
        import random

        class Place:                       # latitude from z: north at z = 0, south at z = 1000
            def to_world(self, x, z):
                return 31.5 - z / 5000, 34.4
        kit = {"house": [("h", 10.0, 10.0, 6.0)]}
        extras = {"damaged": {"h": {1: "h_dmg", 2: "h_ruin"}}, "rubble": [("heap", 10.0, 10.0, 4.0)]}
        objs = [{"from": "model:h", "position": [0, 0, z]} for z in [0] * 50 + [1000] * 200]
        plan = {"timeline": {"bands": [[31.5, {}], [31.3, {"damaged": 0.3, "ruined": 0.3, "rubble": 0.3}]]}}
        gis.apply_timeline(plan, Place(), objs, kit, extras, random.Random(1), log=lambda *a: None)
        self.assertTrue(all(o["from"] == "model:h" for o in objs[:50]))          # the north stays intact
        south = [o["from"] for o in objs[50:]]
        for m in ("model:h", "model:h_dmg", "model:h_ruin", "model:heap"):
            self.assertGreater(south.count(m), 5, m)

    def test_mosques_replace_town_buildings_that_fit(self):
        import random
        kit = {"block": [("b", 20.0, 20.0, 12.0)], "house": [("s", 6.0, 6.0, 4.0)]}
        extras = {"mosque": [("m", 16.0, 14.0, 20.0)]}
        objs = [{"from": "model:b", "_style": "city"} for _ in range(300)]
        objs += [{"from": "model:s", "_style": "city"} for _ in range(300)]
        objs += [{"from": "model:b", "_style": "industry"} for _ in range(300)]
        n = gis.add_mosques({"mosques": {"every": 10}}, objs, kit, extras, random.Random(2), log=lambda *a: None)
        self.assertGreater(n, 10)
        self.assertTrue(all(o["from"] == "model:s" for o in objs[300:600]))      # too small for one
        self.assertTrue(all(o["from"] == "model:b" for o in objs[600:]))         # not in industry

    def test_camps_fill_cells_off_roads_up_to_the_max(self):
        kx, _ = gis._local(31.5)
        shore = 34.45 - 1000 / kx
        s = strip(rotate=45, along=[[-2, 600], [0, 0], [2, -600]], across=0.6)
        s.prepare(FlatElevation(lambda lat, lon: 5.0 if lon > shore else -3.0))

        class Cover:
            def at(self, lat, lon):
                return (60,)
        g = gis.Grid(64)
        side = 65
        hm = [[30.0] * 64 for _ in range(64)]
        road = ("primary", [(-200.0, 0.0), (200.0, 0.0)])
        extras = {"tent": [("t", 4.0, 3.0, 2.0)], "aid": [("a", 6.0, 6.0, 3.0)]}
        plan = {"timeline": {"bands": [[0, {"tents": 1.0, "aid": 1.0}]]}, "camps": {"max": 150}}
        out = gis.place_camps(plan, s, Cover(), gis.Ground(hm, g), hm, 20.0, [road], [], side, g.x0, g.z0, extras,
                              log=lambda *a: None)
        self.assertEqual(len(out), 150)
        self.assertIn("aid", {o["_kind"] for o in out})
        width = gis.ROADS["primary"][1]
        for o in out:
            x, _, z = o["position"]
            if abs(x) < 190:
                self.assertGreater(abs(z), width / 2, o)
