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
        self.assertEqual(patches[15].layers, [0, 1])
        alpha = terrain_layers.decode_alph(new[b"ALPH"], patches)
        self.assertEqual(alpha[15][1][0], 15)              # opaque and drawn
        self.assertEqual(alpha[15][0][0] & 8, 0)           # grass hidden under it: not drawn
        self.assertEqual(terrain_paint.layer_images(new)[1][1], bytes(rock))

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
        # nothing past the ends, and u stays on the texture
        xs = [c for c, _ in by_cell]
        self.assertGreaterEqual(min(xs), (-100 + 2048) // 8 - 1)
        self.assertLessEqual(max(xs), (100 + 2048) // 8)
        self.assertTrue(all(0 <= u <= 2048 for uv in by_cell.values() for u, _ in uv))

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
