import json
import struct
import tempfile
import unittest
from pathlib import Path

import math

from mercfoundry import dsk, modbuild, png, terrain, terrain_decals, terrain_layers, terrain_light, terrain_paint, terrain_surfaces, ucfb
from mercfoundry.hashing import name_hash
from tests.test_dsk import build as build_dsk

SIZE, BLOCK, STEP = 32, 16, 0.35


def chunk(tag, data):
    return ucfb.pack_chunk(tag, data)


def lit_colr(units, sun=(-0.5, 0.7071, -0.5), amb=(40, 38, 36), dif=(44, 48, 60)):
    """COLR for heights lit by one sun (no shadows), 16 patches of 9 x 9."""
    H = [[u * STEP for u in row] for row in units]
    side = SIZE // 8
    out = b""
    for pz in range(side):
        for px in range(side):
            vals = []
            for j in range(9):
                for i in range(9):
                    n = terrain_light._normal(H, SIZE, 8.0, (px * 8 + i) % SIZE, (pz * 8 + j) % SIZE)
                    lam = max(0.0, sum(a * b for a, b in zip(n, sun)))
                    vals.append(terrain_layers.rgb_colr(*(min(248, round((a + d * lam) / 8) * 8) for a, d in zip(amb, dif))))
            out += terrain_layers.pack_blob(struct.pack("<81H", *vals))
    return out


def hills():
    return [[int(60 * math.sin(x / 3.1) * math.cos(z / 4.3) + 20 * math.sin((x + z) / 2.2)) for x in range(SIZE)]
            for z in range(SIZE)]


def painted_chunks():
    """Two layers: grass everywhere, rock fading in over patch 5 only."""
    tl = terrain_layers
    patches, alph = [], b""
    for n in range(16):
        if n == 5:
            patches.append(tl.Patch(0b11, 0))
            rock = [tl.nibble(255 if i >= 4 else 0) for j in range(9) for i in range(9)]
            alph += tl.pack_blob(tl._pack_layer([15] * 81) + tl._pack_layer(rock))
        else:
            patches.append(tl.Patch(0b01, 0))
    return {b"PTCH": tl.encode_ptch(patches), b"ALPH": alph}


def tern_record(units, colr=None, paint=None):
    """A 32 x 32 terrain (four 16 x 16 blocks) holding units[z][x], encoded
    the way the retail data is: base = block minimum, smallest fitting shift."""
    bpr = SIZE // BLOCK
    hexp, raw = b"", bytearray()
    for bz in range(bpr):
        for bx in range(bpr):
            vals = [units[bz * BLOCK + z][bx * BLOCK + x] for z in range(BLOCK) for x in range(BLOCK)]
            shift, base, lo, hi = terrain._hexp_entry(vals)
            hexp += struct.pack("<4h", shift, base, lo, hi)
            raw += bytes((v - base) >> shift for v in vals)
    lo = min(min(r) for r in units) * STEP
    hi = max(max(r) for r in units) * STEP
    info = struct.pack("<4f6h", 8.0, STEP, lo, hi, SIZE, 8, BLOCK, 2 if paint else 1, 0, 0) + struct.pack("<3f", 0, 0, 0)
    body = chunk(b"INFO", info) + chunk(b"HEXP", hexp) + chunk(b"HGT8", terrain.pack_hgt8(bytes(raw)))
    body += chunk(b"LTEX", b"grass\0rock\0" if paint else b"grass\0")
    if paint:
        body += chunk(b"SCAL", struct.pack("<2f", 16, 32)) + chunk(b"AXIS", bytes([0, 5]))
        body += chunk(b"PTCH", paint[b"PTCH"]) + chunk(b"ALPH", paint[b"ALPH"])
    if colr is not None:
        body += chunk(b"COLR", colr)
    return chunk(b"ucfb", chunk(b"tern", body))


def sample_units():
    return [[(x * 3 + z * 5) % 40 - 10 for x in range(SIZE)] for z in range(SIZE)]


class TerrainTests(unittest.TestCase):
    def test_decode(self):
        units = sample_units()
        info, got, _ = terrain.decode_height_units(terrain.tern_chunks(tern_record(units)))
        self.assertEqual((info["size"], info["block"]), (SIZE, BLOCK))
        self.assertEqual(got, units)

    def test_hgt8_round_trip(self):
        raw = bytes([0] * 300 + list(range(200)) + [7, 7, 9])
        packed = terrain.pack_hgt8(raw)
        self.assertEqual(terrain.unpack_hgt8(packed, len(raw)), (raw, len(packed)))

    def test_unchanged_rebuild_is_identical(self):
        rec = tern_record(sample_units())
        chunks = terrain.tern_chunks(rec)
        _, units, _ = terrain.decode_height_units(chunks)
        self.assertEqual(terrain.rebuild(rec, terrain.encode_height_units(chunks, units)), rec)

    def test_edit_round_trip(self):
        rec = tern_record(sample_units())
        chunks = terrain.tern_chunks(rec)
        _, units, _ = terrain.decode_height_units(chunks)
        units[3][4] = 900          # needs a larger shift in block 0
        units[20][20] = -50
        new = terrain.rebuild(rec, terrain.encode_height_units(chunks, units))
        new_chunks = terrain.tern_chunks(new)
        _, got, _ = terrain.decode_height_units(new_chunks)
        self.assertEqual(got[20][20], -50)
        self.assertLessEqual(abs(got[3][4] - 900), 1 << terrain.parse_hexp(new_chunks[b"HEXP"], 4)[0][0])
        self.assertEqual(got[30][30], units[30][30])        # untouched block exact
        self.assertEqual(new_chunks[b"LTEX"], b"grass\0")   # other chunks kept
        info = terrain.parse_info(new_chunks[b"INFO"])
        self.assertAlmostEqual(info["min"], -50 * STEP, places=4)

    def test_png_round_trip(self):
        chunks = terrain.tern_chunks(tern_record(sample_units()))
        image, meta = terrain.heightmap_png(chunks)
        self.assertEqual(png.decode_grey16(image)[:2], (SIZE, SIZE))
        self.assertEqual(terrain.units_from_png(image, meta), sample_units())

    def test_grey16(self):
        values = [0, 1, 255, 256, 65535, 4097]
        self.assertEqual(png.decode_grey16(png.encode_grey16(3, 2, values)), (3, 2, values))

    def test_lz_round_trip(self):
        data = bytes(range(50)) * 4 + b"\0" * 100
        packed = terrain_layers.lz_compress(data)
        self.assertEqual(terrain_layers.lz_decompress(packed)[0], data)

    def test_fit_finds_the_sun(self):
        units = hills()
        H = [[u * STEP for u in row] for row in units]
        chunks = terrain.tern_chunks(tern_record(units, lit_colr(units)))
        rgb = terrain_light.vertex_rgb(terrain_layers.decode_colr(chunks[b"COLR"], 16), SIZE)
        light = terrain_light.fit(H, rgb, SIZE, 8.0, stride=1)
        self.assertGreater(sum(a * b for a, b in zip(light.sun, (-0.5, 0.7071, -0.5))), 0.98)
        self.assertTrue(light.usable)

    def test_relight(self):
        units = hills()
        rec = tern_record(units, lit_colr(units))
        chunks = terrain.tern_chunks(rec)
        new_units = [row[:] for row in units]
        for z in range(8, 17):                       # a 9 x 9 bump at (12, 12)
            for x in range(8, 17):
                new_units[z][x] += int(120 * math.exp(-((x - 12) ** 2 + (z - 12) ** 2) / 6))
        heights = terrain.encode_height_units(chunks, new_units)
        colr, note = terrain_light.relight_chunks(chunks, heights)
        self.assertIn("relit", note)
        old = terrain_layers.decode_colr(chunks[b"COLR"], 16)
        new = terrain_layers.decode_colr(colr, 16)
        lum = lambda v: sum(terrain_layers.colr_rgb(v))
        # patch 5 is cells 8-16 x 8-16; vertex (j=2, i=2) faces the sun (-x, -z), (6, 6) faces away
        self.assertGreater(lum(new[5][2 * 9 + 2]) - lum(old[5][2 * 9 + 2]),
                           lum(new[5][6 * 9 + 6]) - lum(old[5][6 * 9 + 6]))
        # a far patch, out of the bump's reach, keeps its blob byte for byte
        blobs_old = terrain_light.colr_blobs(chunks[b"COLR"], 16)
        blobs_new = terrain_light.colr_blobs(colr, 16)
        self.assertEqual(blobs_old[0], blobs_new[0])

    def test_layer_images_round_trip(self):
        rec = tern_record(sample_units(), paint=painted_chunks())
        chunks = terrain.tern_chunks(rec)
        self.assertEqual(terrain_paint.layer_table(chunks), [{"texture": "grass", "scale": 16.0, "axis": 0},
                                                             {"texture": "rock", "scale": 32.0, "axis": 5}])
        w, images = terrain_paint.layer_images(chunks)
        self.assertEqual(w, 33)
        self.assertEqual(images[1][9 * w + 12], 255)    # patch 5 = (1, 1): cells 8-16
        self.assertEqual(images[1][9 * w + 9], 0)
        self.assertEqual(images[1][0], 0)
        replace, n = terrain_paint.apply(chunks, images, terrain_paint.layer_table(chunks))
        self.assertEqual((replace, n), ({}, 0))

    def test_paint(self):
        rec = tern_record(sample_units(), paint=painted_chunks())
        chunks = terrain.tern_chunks(rec)
        w, images = terrain_paint.layer_images(chunks)
        rock = bytearray(images[1])
        for y in range(w):
            for x in range(w):
                if y >= 24 and x >= 24:      # rock over the last patch (3, 3)
                    rock[y * w + x] = 255
                if 8 <= y <= 16 and 8 <= x <= 16:
                    rock[y * w + x] = 0      # and none in patch 5
        replace, n = terrain_paint.apply(chunks, {1: bytes(rock)})
        self.assertEqual(n, 6)   # 5 and 1 (its shared top edge); 15 and 10, 11, 14 (sharing its edges)
        new = terrain.tern_chunks(terrain.rebuild(rec, replace))
        patches = terrain_layers.decode_ptch(new[b"PTCH"])
        self.assertEqual(patches[5].layers, [0])
        # grass hidden under opaque rock draws no cell, so it's left out: a
        # pass that draws nothing hangs the game
        self.assertEqual(patches[15].layers, [1])
        self.assertEqual(terrain_paint.layer_images(new)[1][1], bytes(rock))

    def test_no_empty_pass(self):
        # Four layers under an opaque fifth: the first pass of four would draw
        # nothing (the game hangs on it); the hidden layers are dropped.
        windows = {l: [128] * 81 for l in range(4)}
        windows[4] = [255] * 81
        grids = terrain_paint.encode_patch(windows, [0, 1, 2, 3, 4])
        self.assertFalse(any(terrain_paint.drawn(grids[l]) for l in range(4)))
        self.assertTrue(terrain_paint.drawn(grids[4]))
        rec = tern_record(sample_units(), paint=painted_chunks())
        chunks = terrain.tern_chunks(rec)
        table = terrain_paint.layer_table(chunks) + [{"texture": t, "scale": 16, "axis": 0} for t in ("a", "b", "c")]
        w = SIZE + 1
        images = {l: bytes([128] * (w * w)) for l in range(4)}
        images[4] = bytes([255] * (w * w))
        replace, n = terrain_paint.apply(chunks, images, table)
        patches = terrain_layers.decode_ptch(replace[b"PTCH"])
        self.assertTrue(all(p.layers == [4] for p in patches))

    def test_new_layer(self):
        chunks = terrain.tern_chunks(tern_record(sample_units(), paint=painted_chunks()))
        table = terrain_paint.layer_table(chunks) + [{"texture": "sand", "scale": 8, "axis": 0}]
        replace, _ = terrain_paint.apply(chunks, table=table)
        self.assertEqual(terrain_paint.layer_count({**chunks, **replace}), 3)
        self.assertEqual(terrain_paint.layer_table({**chunks, **replace})[2], {"texture": "sand", "scale": 8.0, "axis": 0})
        with self.assertRaises(ValueError):
            terrain_paint.apply(chunks, table=table[:1])

    def test_mod_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "data").mkdir()
            build_dsk(d / "data" / "assets.dsk", [(name_hash("sw"), name_hash("Terrain"), tern_record(sample_units()))])
            modbuild.new_project(d / "proj")
            image, meta = terrain.heightmap_png(terrain.tern_chunks(tern_record(sample_units())))
            w, h, values = png.decode_grey16(image)
            values[5 * SIZE + 6] += 20 * meta["levels_per_step"]
            (d / "proj" / "terrain" / "sw.heights.png").write_bytes(png.encode_grey16(w, h, values))
            (d / "proj" / "terrain" / "sw.heights.json").write_text(json.dumps(meta))
            result = modbuild.build(d / "proj", d / "data", d / "out")
            self.assertIn("terrain sw: 1 height samples changed; no baked lighting", result.changes)
            out = dsk.Archive(d / "out" / "assets.dsk")
            rec = out.read(out.records[0])
            _, got, _ = terrain.decode_height_units(terrain.tern_chunks(rec))
            self.assertEqual(got[5][6], sample_units()[5][6] + 20)

    def test_mod_build_paints_an_added_layer(self):
        # paint saved for a layer the mod's table adds: the game's map has no
        # paint for it to change, so it starts empty (this used to stop the build)
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "data").mkdir()
            build_dsk(d / "data" / "assets.dsk", [(name_hash("sw"), name_hash("Terrain"),
                                                   tern_record(sample_units(), paint=painted_chunks()))])
            modbuild.new_project(d / "proj")
            table = terrain_paint.layer_table(terrain.tern_chunks(tern_record(sample_units(), paint=painted_chunks())))
            (d / "proj" / "terrain" / "sw.layers.json").write_text(json.dumps(
                {"layers": table + [{"texture": "sand", "scale": 8, "axis": 0}]}))
            w = SIZE + 1
            la = bytearray(w * w * 2)
            for y in range(24, w):
                for x in range(24, w):
                    la[2 * (y * w + x)], la[2 * (y * w + x) + 1] = 255, 255
            (d / "proj" / "terrain" / "sw.layer02.sand.changes.png").write_bytes(png.encode(w, w, bytes(la), 2))
            # sand is the mod's own texture: no warning about it
            build_dsk(d / "data" / "streamed.dsk", [])         # where new textures go
            (d / "proj" / "textures" / "sand.png").write_bytes(png.encode(8, 8, bytes([200, 180, 120, 255]) * 64, 4))
            result = modbuild.build(d / "proj", d / "data", d / "out")
            self.assertFalse([x for x in result.warnings if "'sand'" in x], result.warnings)
            out = dsk.Archive(d / "out" / "assets.dsk")
            new = terrain.tern_chunks(out.read(next(r for r in out.records if r.kind == name_hash("Terrain"))))
            self.assertEqual(terrain_paint.layer_count(new), 3)
            self.assertEqual(terrain_paint.layer_images(new)[1][2][30 * w + 30], 255)
            self.assertEqual(terrain_paint.layer_images(new)[1][2][0], 0)


if __name__ == "__main__":
    unittest.main()


def decal_chunks():
    """16 patches; patch 5 has two decals (one stored just outside it), and
    DTEX lists 'road' twice, as some retail maps do."""
    tl = terrain_layers
    decals = [tl.Decal(3, 4, 0, terrain_decals.EXTRA, [(0, 0), (512, 0), (0, 512), (512, 512)]),
              tl.Decal(255, 2, 2, terrain_decals.EXTRA, [(-300, 32700), (300, -32700), (0, 0), (1, 1)])]
    patches = [tl.Patch(1, 2 if n == 5 else 0) for n in range(16)]
    info = struct.pack("<4f6h", 8.0, STEP, 0, 1, SIZE, 8, BLOCK, 1, 3, 2) + struct.pack("<3f", 0, 0, 0)
    return {b"INFO": info, b"DTEX": b"road\0cross\0road\0", b"PTCH": terrain_layers.encode_ptch(patches),
            b"DCAL": terrain_layers.encode_dcal(decals)}


class DecalTests(unittest.TestCase):
    def test_export(self):
        doc = terrain_decals.export(decal_chunks())
        self.assertEqual(doc["textures"], ["road", "cross", "road"])
        self.assertEqual(doc["decals"][0], {"cell": [11, 12], "texture": 0, "uv": [[0, 0], [512, 0], [0, 512], [512, 512]]})
        self.assertEqual(doc["decals"][1]["cell"], [7, 10])
        self.assertEqual(doc["decals"][1]["patch"], [1, 1])

    def test_round_trip(self):
        chunks = decal_chunks()
        doc = json.loads(json.dumps(terrain_decals.export(chunks)))
        self.assertEqual(terrain_decals.apply(chunks, doc), ({}, 2))

    def test_replace(self):
        chunks = decal_chunks()
        doc = {"textures": ["road"], "decals": [{"cell": [30, 1], "texture": "cross", "uv": [[0, 0]] * 4}]}
        replace, n = terrain_decals.apply(chunks, doc)
        new = {**chunks, **replace}
        self.assertEqual(n, 1)
        self.assertEqual(terrain_decals.export(new),
                         {"textures": ["road", "cross"], "decals": [{"cell": [30, 1], "texture": "cross", "uv": [[0, 0]] * 4}]})
        patches = terrain_layers.decode_ptch(new[b"PTCH"])
        self.assertEqual([p.decal_count for p in patches].index(1), 3)

    def test_road(self):
        road = {"texture": "asphalt", "points": [[-100, 0], [100, 0]], "span": 24}
        cells = terrain_decals.road_decals(road, 512, 8.0)
        by_cell = {tuple(e["cell"]): e["uv"] for e in cells}
        row = (0 + 2040) // 8                      # z = 0 lies in row 255 (z + 2040)
        self.assertIn((256, row), by_cell)
        # along the road, neighbouring cells share their edge (modulo one texture repeat)
        a, b = by_cell[(256, row)], by_cell[(257, row)]
        # stored corners: (x, z+1), (x+1, z+1), (x, z), (x+1, z)
        self.assertEqual(a[3][0], b[2][0])
        self.assertEqual((a[3][1] - b[2][1]) % 2048, 0)
        self.assertEqual(a[1][0], b[0][0])
        self.assertEqual(round((a[3][1] - a[2][1]) % 2048), round(8 / 24 * 2048))
        # nothing past the ends, and u no further past the texture's edges than its see-through margins
        xs = [c for c, _ in by_cell]
        self.assertGreaterEqual(min(xs), (-100 + 2048) // 8 - 1)
        self.assertLessEqual(max(xs), (100 + 2048) // 8)
        m = terrain_decals.MARGIN * 2048
        self.assertTrue(all(-m - 1 <= u <= 2048 + m + 1 for uv in by_cell.values() for u, _ in uv))

    def test_diagonal_road_cells_join(self):
        # a road at 45 degrees: every pair of neighbouring cells agrees on the
        # corners they share (no clamping that bends each cell's texture)
        road = {"texture": "asphalt", "points": [[-100, -100], [100, 100]], "span": 30}
        cells = {tuple(e["cell"]): e["uv"] for e in terrain_decals.road_decals(road, 512, 8.0)}
        self.assertGreater(len(cells), 40)
        at = lambda uv: {(dx, dz): uv[i] for i, (dz, dx) in enumerate(terrain_decals.CORNERS)}
        pairs = 0
        for (c, r), uv in cells.items():
            for (nx, nz), mine, theirs in (((1, 0), [(1, 0), (1, 1)], [(0, 0), (0, 1)]),
                                           ((0, 1), [(0, 1), (1, 1)], [(0, 0), (1, 0)])):
                other = cells.get((c + nx, r + nz))
                if other is None:
                    continue
                a, b = at(uv), at(other)
                for p, q in zip(mine, theirs):
                    self.assertAlmostEqual(a[p][0], b[q][0], delta=1)
                    self.assertAlmostEqual((a[p][1] - b[q][1] + 1) % 2048, 1, delta=1)
                pairs += 1
        self.assertGreater(pairs, 40)

    def test_crossing_roads_nearest_wins(self):
        chunks = decal_chunks()
        doc = {"roads": [{"texture": "asphalt", "points": [[-60, 0], [60, 0]], "span": 30},
                         {"texture": "dirt", "points": [[0, -60], [0, 60]], "span": 30}]}
        replace, n = terrain_decals.apply(chunks, doc)
        out = terrain_decals.export({**chunks, **replace})
        tex = {tuple(d["cell"]): d["texture"] for d in out["decals"]}
        half = SIZE * 8 / 2
        cell = lambda x, z: (int((x + half) // 8), int((z + half - 8) // 8))
        self.assertEqual(tex[cell(-40, 0)], "asphalt")      # on the first road, away from the crossing
        self.assertEqual(tex[cell(0, -36)], "dirt")         # on the second, away from the first
        self.assertEqual(tex[cell(4, 0)], "dirt")           # nearer the second's centre line
        self.assertEqual(tex[cell(-12, 4)], "asphalt")      # nearer the first's

    def test_junction_pieces(self):
        # where roads of a family with junction textures meet square: the
        # game's cross and T pieces over the strips' overlap, as retail lays
        # them (a T's u rises toward its stem); a skewed T keeps the roads
        st = "sw_decal_road_straight"
        roads = [{"texture": st, "points": [[-120, 0], [120, 0]], "span": 26},
                 {"texture": st, "points": [[0, -120], [0, 120]], "span": 26},       # a cross at (0, 0)
                 {"texture": st, "points": [[300, 0], [600, 0]], "span": 26},
                 {"texture": st, "points": [[450, 0], [450, 120]], "span": 26}]      # a T at (450, 0), stem +z
        out = terrain_decals.junction_decals(roads, SIZE, 8.0)
        half = SIZE * 8 / 2
        cell = lambda x, z: (int((x + half) // 8), int((z + half - 8) // 8))
        tex = {tuple(e["cell"]): e for e in out}
        self.assertEqual(tex[cell(2, 2)]["texture"], "sw_decal_road_cross")
        self.assertEqual(tex[cell(452, 4)]["texture"], "sw_decal_road_t")
        self.assertEqual(tex[cell(452, -4)]["texture"], "sw_decal_road_t")    # across the whole through road
        self.assertNotIn(cell(452, 30), tex)                                  # the stem goes on as itself
        uv = tex[cell(452, 4)]["uv"]                       # corners (x, z+1), (x+1, z+1), (x, z), (x+1, z)
        self.assertGreater(uv[0][0], uv[2][0])             # u rises with z, toward the stem
        skewed = roads[2:3] + [{"texture": st, "points": [[450, 0], [550, 60]], "span": 26}]
        self.assertEqual(terrain_decals.junction_decals(skewed, SIZE, 8.0), [])
        self.assertIsNone(terrain_decals.junction_texture("asphalt", "t"))

    def test_roads_keep_existing_decals(self):
        chunks = decal_chunks()
        doc = {"roads": [{"texture": "asphalt", "points": [[-100, 0], [-60, 0]]}]}
        replace, n = terrain_decals.apply(chunks, doc)
        new = terrain_decals.export({**chunks, **replace})
        self.assertGreater(n, 2)
        self.assertIn("asphalt", new["textures"])
        cells = [d["cell"] for d in new["decals"]]
        self.assertIn([11, 12], cells)       # the map's own decals are still there
        self.assertIn([7, 10], cells)

    def test_roads_replace_not_stack(self):
        # Crossing roads, one doubling back, and one over a map decal: one decal
        # per cell, so no patch passes the game's 64 (more corrupted its memory).
        chunks = decal_chunks()
        doc = {"roads": [{"texture": "asphalt", "points": [[-60, 0], [60, 0]]},
                         {"texture": "asphalt", "points": [[0, -60], [0, 60]]},
                         {"texture": "dirt", "points": [[-50, 8], [50, 8], [-50, 8]]},
                         {"texture": "dirt", "points": [[-40, -24], [-30, -24]]}]}
        replace, n = terrain_decals.apply(chunks, doc)
        new = {**chunks, **replace}
        out = terrain_decals.export(new)
        cells = [tuple(d["cell"]) for d in out["decals"]]
        self.assertEqual(len(cells), len(set(cells)))
        self.assertEqual(len(cells), n)
        self.assertLessEqual(max(p.decal_count for p in terrain_layers.decode_ptch(new[b"PTCH"])),
                             terrain_decals.MAX_PER_PATCH)
        at = {tuple(d["cell"]): d["texture"] for d in out["decals"]}
        self.assertEqual(at[(11, 12)], "dirt")        # the road over the map's decal took its cell
        self.assertIn((7, 10), at)                    # one it doesn't cover stays

    def test_too_many_decals_in_a_patch(self):
        chunks = decal_chunks()
        doc = {"textures": ["road"],
               "decals": [{"cell": [i % 8, i // 8], "texture": "road", "uv": [[0, 0]] * 4} for i in range(64)]
               + [{"cell": [0, 0], "texture": "road", "uv": [[0, 0]] * 4}]}
        with self.assertRaisesRegex(ValueError, "at most 64"):
            terrain_decals.apply(chunks, doc)


LEVELS_INI = "\n".join(["[LevelNamesList]", "LevelName\tLV_X", "", "[LV_X]", "Name\t\t\tSWN",
                        r"Path\t\t\t\SW" + "\\", r"ConfigFile		\SW\SWN.ini", "", ""])
LEVEL_INI = ("RadarTexture rdrSWN\r\n\r\nDirt\t\t 0\tgeneric\r\n\r\nterrainPhysicsType \t0 \t\tdirt\r\n"
             "terrainPhysicsType \t8 \t\troad\r\n\r\nTerrainDust \t0 \t\tdirt\r\n\r\nlayereffect 8 blowingpaper\r\n")


class SurfaceTests(unittest.TestCase):
    def test_level_config(self):
        self.assertEqual(terrain_surfaces.level_config(LEVELS_INI, "swn"), r"SW\SWN.ini")
        self.assertIsNone(terrain_surfaces.level_config(LEVELS_INI, "nw"))

    def test_export(self):
        doc = terrain_surfaces.export(LEVEL_INI, ["dirt_tex"] + [""] * 7 + ["concrete"], ["road_tex"])
        by_slot = {e["slot"]: e for e in doc["textures"]}
        self.assertEqual(by_slot[0], {"slot": 0, "texture": "dirt_tex", "kind": "layer", "surface": "dirt",
                                      "dust": "dirt", "debris": "generic", "effect": None})
        self.assertEqual(by_slot[8]["effect"], "blowingpaper")
        self.assertEqual(by_slot[16]["kind"], "decal")
        self.assertEqual(terrain_surfaces.apply(LEVEL_INI, doc), (LEVEL_INI, [], []))

    def test_apply(self):
        doc = {"textures": [{"slot": 8, "surface": "snow", "effect": None},
                            {"slot": 3, "surface": "rock", "dust": "rock"},
                            {"slot": 17, "surface": "mud"}]}
        text, changes, warnings = terrain_surfaces.apply(LEVEL_INI, doc)
        lines = text.split("\r\n")
        self.assertIn("terrainPhysicsType \t8 \t\tsnow", lines)
        self.assertIn("terrainPhysicsType \t3 \t\trock", lines)
        self.assertNotIn("layereffect 8 blowingpaper", lines)
        self.assertEqual(lines.index("TerrainDust \t3 \t\trock"), lines.index("TerrainDust \t0 \t\tdirt") + 1)
        self.assertEqual(len(warnings), 1)                      # mud isn't a surface
        self.assertEqual(terrain_surfaces.read(text)["surface"], {0: "dirt", 8: "snow", 3: "rock", 17: "mud"})
        with self.assertRaises(ValueError):
            terrain_surfaces.apply(LEVEL_INI, {"textures": [{"slot": 40, "surface": "dirt"}]})

    def test_mod_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "data" / "SW").mkdir(parents=True)
            (d / "data" / "levels.ini").write_text(LEVELS_INI)
            (d / "data" / "SW" / "SWN.ini").write_bytes(LEVEL_INI.encode())
            modbuild.new_project(d / "proj")
            surfaces = d / "proj" / "terrain" / "swn.surfaces.json"
            surfaces.write_text(json.dumps({"textures": [{"slot": 0, "surface": "snow"}]}))
            result = modbuild.build(d / "proj", d / "data", d / "out")
            out = d / "out" / "DATAxbox" / "SW" / "SWN.ini"
            self.assertIn(b"terrainPhysicsType \t0 \t\tsnow\r\n", out.read_bytes())
            self.assertEqual(json.loads((d / "out" / "foundry-mod.json").read_text())["files"], ["DATAxbox/SW/SWN.ini"])
            self.assertEqual(result.written, [])
            surfaces.unlink()                                     # a later build without it removes the file
            modbuild.build(d / "proj", d / "data", d / "out")
            self.assertFalse(out.exists())


class TestRangeTests(unittest.TestCase):
    def test_flatten(self):
        from mercfoundry import testrange
        info = {"size": 512, "spacing": 8.0, "step": 0.5}
        units = [[(x * 7 + z * 3) % 400 for x in range(512)] for z in range(512)]
        new, height = testrange.flatten(units, info)
        x0, z0 = -512 * 8 / 2, -(512 * 8 / 2 - 8)
        cx, cz = testrange.CENTRE
        at = lambda x, z: (int((z - z0) / 8), int((x - x0) / 8))
        iz, ix = at(cx, cz)
        target = new[iz][ix]
        self.assertEqual(height, target * 0.5)
        for dx, dz in ((-110, -110), (110, 110), (0, 115)):
            jz, jx = at(cx + dx, cz + dz)
            self.assertEqual(new[jz][jx], target)
        far = at(cx + 300, cz)
        self.assertEqual(new[far[0]][far[1]], units[far[0]][far[1]])


class FlatTerrainTests(unittest.TestCase):
    def test_flat_record_of_another_size(self):
        tl = terrain_layers
        colr = tl.encode_colr([[tl.rgb_colr(128, 128, 128)] * 81] * 16)
        rec = tern_record(sample_units(), colr=colr, paint=painted_chunks())
        flat = terrain.flat_record(rec, 256, height=7.0)
        ch = terrain.tern_chunks(flat)
        info, units, _ = terrain.decode_height_units(ch)
        self.assertEqual(info["size"], 256)
        self.assertTrue(all(v == round(7.0 / STEP) for row in units for v in row))
        self.assertEqual(len(tl.decode_ptch(ch[b"PTCH"])), 32 * 32)
        self.assertEqual(terrain_paint.layer_table(ch), terrain_paint.layer_table(terrain.tern_chunks(rec)))
        with self.assertRaises(ValueError):
            terrain.flat_record(rec, 2048)
