import tempfile
import unittest
from pathlib import Path

from mercfoundry import glb_textures, png
from mercfoundry.hashing import name_hash
from tests.test_gltf_model import write_glb


def glb_with(materials, images):
    """A .glb whose one mesh uses every material; images: [bytes] embedded."""
    binary, views = b"", []
    for data in images:
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(data)})
        binary += data + b"\0" * (-len(data) % 4)
    return {"asset": {"version": "2.0"},
            "meshes": [{"primitives": [{"attributes": {}, "material": i} for i in range(len(materials))]}],
            "materials": materials, "bufferViews": views,
            "images": [{"bufferView": i, "mimeType": "image/png"} for i in range(len(images))],
            "textures": [{"source": i} for i in range(len(images))]}, binary


class GlbTextureTests(unittest.TestCase):
    def test_textures(self):
        img = png.encode(3, 5, bytes([255, 0, 0, 255]) * 15, 4)              # 3x5: not powers of two
        jpeg = b"\xff\xd8\xff\xe0" + b"\0" * 12
        doc, binary = glb_with([
            {"name": "Wood", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},
            {"name": "Paint.001", "pbrMetallicRoughness": {"baseColorFactor": [0, 0, 1, 1]}},
            {"name": "5AF2BF58"},                                               # the game's (an export's)
            {"name": "00000000"},                                               # an export's untextured piece
            {"name": "ECE9C997"},                                               # an exported key the game lacks
            {"name": "summer_env_bark", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},  # the game's by name
            {"name": "Photo", "pbrMetallicRoughness": {"baseColorTexture": {"index": 1}}}], [img, jpeg])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "thing.glb"
            write_glb(p, doc, binary)
            game = {0x5AF2BF58, name_hash("summer_env_bark")}
            made, warnings = glb_textures.textures(p, "thing", lambda h: h in game)
        self.assertEqual([(m, t, w, h) for m, t, w, h, _ in made],
                         [("Wood", "thing_wood", 4, 4), ("Paint.001", "thing_paint_001", 8, 8), ("Photo", "thing_photo", 8, 8)])
        self.assertEqual(made[0][4][:4], bytes([255, 0, 0, 255]))
        self.assertEqual(made[1][4][:4], bytes([0, 0, 255, 255]))
        self.assertEqual(len(warnings), 1)
        self.assertIn("isn't a PNG", warnings[0])
        lookup = glb_textures.material_hash(made)
        self.assertEqual(lookup("Wood"), name_hash("thing_wood"))
        self.assertEqual(lookup("Paint.001"), name_hash("thing_paint_001"))
        self.assertEqual(lookup("5AF2BF58"), 0x5AF2BF58)                       # kept: the game's
        self.assertEqual(lookup("00000000"), 0)                                # still no texture
        self.assertEqual(lookup("ECE9C997"), 0xECE9C997)                       # kept as the key it was
        self.assertEqual(lookup("summer_env_bark.002"), name_hash("summer_env_bark"))

    def test_resize(self):
        px = bytes([0, 0, 0, 255, 255, 255, 255, 255]) * 2                    # 2x2 checker rows
        self.assertEqual(glb_textures.resize(2, 2, px, 1, 1), bytes([127, 127, 127, 255]))
        self.assertEqual(len(glb_textures.resize(2, 2, px, 4, 4)), 64)
        self.assertEqual([glb_textures._pow2(n) for n in (1, 3, 5, 100, 2000, 3000)], [1, 4, 4, 128, 1024, 1024])


if __name__ == "__main__":
    unittest.main()
