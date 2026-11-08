import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from mercfoundry import dsk, png, texture
from mercfoundry.hashing import name_hash
from tests.test_dsk import build
from tests.test_texture import texture_record


def gradient(width, height, alpha=255):
    return bytes(v for y in range(height) for x in range(width)
                 for v in (x * 255 // max(1, width - 1), y * 255 // max(1, height - 1), 128, alpha))


def png_chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def handmade_png(width, height, ctype, rows, plte=None, trns=None):
    """rows: list of (filter byte, raw row bytes)."""
    out = png.SIGNATURE + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, ctype, 0, 0, 0))
    if plte:
        out += png_chunk(b"PLTE", plte)
    if trns:
        out += png_chunk(b"tRNS", trns)
    raw = b"".join(bytes([f]) + r for f, r in rows)
    return out + png_chunk(b"IDAT", zlib.compress(raw)) + png_chunk(b"IEND", b"")


def max_error(a, b, channels=(0, 1, 2)):
    return max(abs(a[i] - b[i]) for i in range(len(a)) if i % 4 in channels)


class PngReaderTests(unittest.TestCase):
    def test_roundtrip(self):
        img = gradient(5, 3)
        self.assertEqual(png.decode(png.encode(5, 3, img, 4)), (5, 3, img))

    def test_filters(self):
        # RGB 2x2, rows using Sub and Up filters.
        row0 = bytes((10, 20, 30, 5, 5, 5))        # Sub: second pixel = 15, 25, 35
        row1 = bytes((1, 1, 1, 2, 2, 2))           # Up:  adds to row 0
        w, h, px = png.decode(handmade_png(2, 2, 2, [(1, row0), (2, row1)]))
        self.assertEqual(px[4:8], bytes((15, 25, 35, 255)))
        self.assertEqual(px[8:12], bytes((11, 21, 31, 255)))
        self.assertEqual(px[12:16], bytes((17, 27, 37, 255)))

    def test_paeth_and_average(self):
        row0 = bytes((100, 50))                    # grey, 2 pixels, no filter
        row1 = bytes((4, 6))                       # Average: left/up mean added
        row2 = bytes((1, 1))                       # Paeth
        _, _, px = png.decode(handmade_png(2, 3, 0, [(0, row0), (3, row1), (4, row2)]))
        self.assertEqual(px[8], (4 + (0 + 100) // 2) & 255)            # 54
        self.assertEqual(px[12], (6 + (54 + 50) // 2) & 255)           # 58
        self.assertEqual(px[16], 54 + 1)                               # paeth picks up
        self.assertEqual(px[20], 58 + 1)

    def test_palette_with_transparency(self):
        data = handmade_png(2, 1, 3, [(0, bytes((0, 1)))], plte=bytes((255, 0, 0, 0, 0, 255)), trns=bytes((0,)))
        _, _, px = png.decode(data)
        self.assertEqual(px, bytes((255, 0, 0, 0, 0, 0, 255, 255)))

    def test_rejects(self):
        with self.assertRaises(ValueError):
            png.decode(b"not a png")
        interlaced = png.SIGNATURE + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 1))
        with self.assertRaises(ValueError):
            png.decode(interlaced)


class EncoderTests(unittest.TestCase):
    def test_solid_colours_are_exact(self):
        img = bytes((255, 0, 0, 255)) * 64   # 8x8 red: exact in 565
        for fmt, alpha in ((texture.DXT1, False), (texture.DXT3, True)):
            data = texture.encode_dxt(img, 8, 8, alpha)
            self.assertEqual(texture.decode_dxt(data, 8, 8, alpha), img)

    def test_gradient_quality(self):
        # Colours along one line per block (what DXT can represent): close fit.
        img = bytes(v for y in range(16) for x in range(16) for v in (x * 16, x * 8, 255 - x * 16, 255))
        back = texture.decode_dxt(texture.encode_dxt(img, 16, 16, False), 16, 16, False)
        self.assertLessEqual(max_error(img, back), 10)
        # Two independent directions in a block can't be represented exactly.
        img = gradient(16, 16)
        back = texture.decode_dxt(texture.encode_dxt(img, 16, 16, False), 16, 16, False)
        self.assertLessEqual(max_error(img, back), 48)

    def test_dxt1_punch_through(self):
        img = bytearray(gradient(4, 4))
        img[3] = 0                                        # first pixel transparent
        back = texture.decode_dxt(texture.encode_dxt(bytes(img), 4, 4, False), 4, 4, False)
        self.assertEqual(back[3], 0)
        self.assertTrue(all(back[i] == 255 for i in range(7, 64, 4)))

    def test_dxt3_alpha_levels(self):
        img = bytes(v for i in range(16) for v in (0, 0, 0, i * 17))
        back = texture.decode_dxt(texture.encode_dxt(img, 4, 4, True), 4, 4, True)
        self.assertEqual([back[i] for i in range(3, 64, 4)], [i * 17 for i in range(16)])

    def test_swizzle_inverse(self):
        img = gradient(8, 4)
        self.assertEqual(texture.decode_argb8_swizzled(texture.encode_argb8_swizzled(img, 8, 4), 8, 4), img)

    def test_downsample(self):
        img = bytes((0, 0, 0, 255, 255, 255, 255, 255)) * 2   # 2x2 checker columns
        self.assertEqual(texture.downsample(img, 2, 2), (1, 1, bytes((128, 128, 128, 255))))


class ReplaceTests(unittest.TestCase):
    def original(self, fmt=texture.DXT1, size=8, mips=4):
        levels, s = [], size
        for _ in range(mips):
            n = max(1, (s + 3) // 4) ** 2
            levels.append(b"\0" * n * (8 if fmt == texture.DXT1 else 16) if fmt != texture.ARGB8 else b"\0" * s * s * 4)
            s = max(1, s // 2)
        return texture.decode(texture_record("tex", size, size, mips, fmt, b"".join(levels)))

    def test_unchanged_record_is_identical(self):
        rec = texture_record("tex", 8, 8, 1, texture.DXT1, b"\1" * 32)
        self.assertEqual(texture.encode(texture.decode(rec)), rec)

    def test_replace_keeps_format_and_builds_mips(self):
        for fmt in (texture.DXT1, texture.DXT3, texture.ARGB8):
            new = texture.replace_image(self.original(fmt), 8, 8, gradient(8, 8))
            again = texture.decode(texture.encode(new))
            self.assertEqual((again.format, again.width, again.mips), (fmt, 8, 4))
            self.assertEqual(len(again.body), sum(again.level_size(i) for i in range(4)))
            self.assertEqual(again.rgba(3)[:2], (1, 1))

    def test_bigger_image_and_fewer_mips(self):
        new = texture.replace_image(self.original(mips=4), 16, 16, gradient(16, 16))
        self.assertEqual((new.width, new.mips), (16, 4))
        small = texture.replace_image(self.original(mips=4), 2, 2, gradient(2, 2))
        self.assertEqual(small.mips, 2)

    def test_rejects_bad_sizes(self):
        with self.assertRaises(ValueError):
            texture.replace_image(self.original(), 6, 8, gradient(6, 8))


class WorkflowTests(unittest.TestCase):
    def test_edit_revert(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            rec = texture.encode(texture.replace_image(ReplaceTests().original(), 8, 8, gradient(8, 8)))
            build(d / "a.dsk", [(name_hash("tex"), texture.TEXTURE, rec)])
            m = dsk.extract(d / "a.dsk", d / "x", decode=True, textures=True)
            entry = m["records"][0]
            self.assertTrue(entry["decoded"].endswith(".png"))
            pic = d / "x" / entry["decoded"]
            original_png = pic.read_bytes()

            self.assertEqual(dsk.rebuild(d / "x"), [])                 # untouched
            red = bytes((255, 0, 0, 255)) * 64
            pic.write_bytes(png.encode(8, 8, red, 4))
            self.assertEqual(len(dsk.rebuild(d / "x")), 1)
            self.assertEqual(dsk.rebuild(d / "x"), [])                 # already applied
            dsk.pack(d / "x", d / "b.dsk")
            arc = dsk.Archive(d / "b.dsk")
            self.assertEqual(texture.decode(arc.read(arc.records[0])).rgba()[2], red)

            pic.write_bytes(original_png)                               # put it back
            self.assertEqual(len(dsk.rebuild(d / "x")), 1)
            dsk.pack(d / "x", d / "c.dsk")
            self.assertEqual((d / "a.dsk").read_bytes(), (d / "c.dsk").read_bytes())

    def test_textures_off_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            rec = texture_record("tex", 4, 4, 1, texture.DXT1, b"\0" * 8)
            build(d / "a.dsk", [(1, texture.TEXTURE, rec)])
            m = dsk.extract(d / "a.dsk", d / "x", decode=True)
            self.assertNotIn("decoded", m["records"][0])


if __name__ == "__main__":
    unittest.main()
