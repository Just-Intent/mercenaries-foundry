import json
import struct
import tempfile
import unittest
from pathlib import Path

from mercfoundry import dsk, modbuild, png, terrain, terrain_layers, ucfb
from mercfoundry.hashing import name_hash
from tests.test_dsk import build as build_dsk

SIZE, BLOCK, STEP = 32, 16, 0.35


def chunk(tag, data):
    return ucfb.pack_chunk(tag, data)


def tern_record(units):
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
    info = struct.pack("<4f6h", 8.0, STEP, lo, hi, SIZE, 8, BLOCK, 1, 0, 0) + struct.pack("<3f", 0, 0, 0)
    body = chunk(b"INFO", info) + chunk(b"HEXP", hexp) + chunk(b"HGT8", terrain.pack_hgt8(bytes(raw)))
    body += chunk(b"LTEX", b"grass\0")
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
            self.assertIn("terrain sw: 1 height samples changed", result.changes)
            out = dsk.Archive(d / "out" / "assets.dsk")
            rec = out.read(out.records[0])
            _, got, _ = terrain.decode_height_units(terrain.tern_chunks(rec))
            self.assertEqual(got[5][6], sample_units()[5][6] + 20)


if __name__ == "__main__":
    unittest.main()
