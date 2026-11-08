import struct
import unittest
import zlib

from mercfoundry import png, texture, ucfb


def texture_record(name, width, height, mips, fmt, body, kind=1):
    info = struct.pack("<6H3I", width, height, 1, mips, kind, 1, fmt, 0, len(body))
    fields = (ucfb.pack_chunk(b"NAME", name.encode() + b"\0") if name else b"")
    fields += ucfb.pack_chunk(b"INFO", info) + ucfb.pack_chunk(b"BODY", body)
    return ucfb.wrap(b"tex_", fields)


RED, BLUE = 0xF800, 0x001F


def dxt1_block(c0, c1, indices):
    bits = 0
    for i, v in enumerate(indices):
        bits |= v << (2 * i)
    return struct.pack("<HHI", c0, c1, bits)


def pixel(rgba, width, x, y):
    o = (y * width + x) * 4
    return tuple(rgba[o:o + 4])


class TextureTests(unittest.TestCase):
    def test_dxt1_palette(self):
        block = dxt1_block(RED, BLUE, [0, 1, 2, 3] * 4)
        t = texture.decode(texture_record("t", 4, 4, 1, texture.DXT1, block))
        w, h, px = t.rgba()
        self.assertEqual((w, h), (4, 4))
        self.assertEqual(pixel(px, 4, 0, 0), (255, 0, 0, 255))
        self.assertEqual(pixel(px, 4, 1, 0), (0, 0, 255, 255))
        self.assertEqual(pixel(px, 4, 2, 0), (170, 0, 85, 255))   # 2/3 red + 1/3 blue
        self.assertEqual(pixel(px, 4, 3, 0), (85, 0, 170, 255))

    def test_dxt1_transparent_mode(self):
        block = dxt1_block(BLUE, RED, [3] * 16)  # c0 <= c1: index 3 is transparent
        _, _, px = texture.decode(texture_record("t", 4, 4, 1, texture.DXT1, block)).rgba()
        self.assertEqual(pixel(px, 4, 0, 0), (0, 0, 0, 0))

    def test_dxt3_alpha(self):
        alphas = sum((i % 16) << (4 * i) for i in range(16))
        block = struct.pack("<Q", alphas) + dxt1_block(RED, RED, [0] * 16)
        _, _, px = texture.decode(texture_record("t", 4, 4, 1, texture.DXT3, block)).rgba()
        self.assertEqual(pixel(px, 4, 0, 0)[3], 0)
        self.assertEqual(pixel(px, 4, 3, 3)[3], 255)
        self.assertEqual(pixel(px, 4, 1, 0)[3], 17)

    def test_mip_levels(self):
        big = dxt1_block(RED, RED, [0] * 16) * 4   # 8x8
        small = dxt1_block(BLUE, BLUE, [0] * 16)   # 4x4
        t = texture.decode(texture_record("t", 8, 8, 2, texture.DXT1, big + small))
        w, h, px = t.rgba(1)
        self.assertEqual((w, h), (4, 4))
        self.assertEqual(pixel(px, 4, 0, 0), (0, 0, 255, 255))

    def test_swizzled_argb(self):
        # 4x2 image: linear pixel value = index; store it in Morton order.
        width, height = 4, 2
        data = bytearray(width * height * 4)
        for y in range(height):
            for x in range(width):
                s = texture._morton(x, y, width, height) * 4
                data[s:s + 4] = bytes((0, 0, y * width + x, 255))  # B G R A
        _, _, px = texture.decode(texture_record("t", width, height, 1, texture.ARGB8, bytes(data))).rgba()
        self.assertEqual([pixel(px, width, x, y)[0] for y in range(height) for x in range(width)], list(range(8)))
        self.assertEqual(texture._morton(1, 0, 4, 4), 1)
        self.assertEqual(texture._morton(0, 1, 4, 4), 2)
        self.assertEqual(texture._morton(2, 2, 4, 4), 12)

    def test_cube_map_faces(self):
        faces = b"".join(dxt1_block(c, c, [0] * 16) for c in (RED, BLUE, RED, BLUE, RED, BLUE))
        t = texture.decode(texture_record("", 4, 4, 1, texture.DXT1, faces, kind=2))
        self.assertEqual(t.faces, 6)
        self.assertEqual(pixel(t.rgba(0, 1)[2], 4, 0, 0), (0, 0, 255, 255))
        with self.assertRaises(ValueError):
            t.rgba(0, 6)

    def test_truncated(self):
        with self.assertRaises(ValueError):
            texture.decode(texture_record("t", 8, 8, 1, texture.DXT1, b"\0" * 8)).rgba()

    def test_png(self):
        data = png.encode(2, 1, bytes((1, 2, 3, 4, 5, 6, 7, 8)), 4)
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        idat = data.index(b"IDAT")
        size = struct.unpack(">I", data[idat - 4:idat])[0]
        self.assertEqual(zlib.decompress(data[idat + 4:idat + 4 + size]), b"\0" + bytes(range(1, 9)))
        with self.assertRaises(ValueError):
            png.encode(2, 2, b"\0" * 4, 4)


if __name__ == "__main__":
    unittest.main()
