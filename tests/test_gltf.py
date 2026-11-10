import struct
import unittest

from mercfoundry import gltf, gltf_check, model, png, script, ucfb
from mercfoundry.hashing import name_hash
from tests.test_model import QUAD, joint, model_record, segm, segments_record


def skinned_segm(bones):
    verts = b""
    for i, b in enumerate(bones):
        q = ((0, 0, 0), (32767, 0, 0), (0, 32767, 0), (32767, 32767, 0))[i]
        verts += struct.pack("<3hHI", *q, b, 0) + b"\xff\xff\xff\xff" + struct.pack("<2h", 0, 0)
    info = struct.pack("<4I", 4, 4, 20 | 0x10000, 1)
    mtrl = struct.pack("<7I", 1, name_hash("DummyRoot"), name_hash("DummyRoot"), name_hash("skin"), 0, 0x100, 0)
    return ucfb.pack_chunk(b"segm", ucfb.pack_chunk(b"INFO", info) + ucfb.pack_chunk(b"MTRL", mtrl)
                           + ucfb.pack_chunk(b"VBUF", verts) + ucfb.pack_chunk(b"IBUF", struct.pack("<4H", 0, 1, 2, 3)))


def sample():
    turn = (0.0, 0.7071068, 0.0, 0.7071068)
    joints = [
        joint("DummyRoot", (0, 0, 0, 1), (0, 0, 0), ""),
        joint("bone_spine", (0, 0, 0, 1), (0, 1, 0), "DummyRoot"),
        joint("hp_gun", turn, (0.5, 0, 0.25), "bone_spine"),
        joint("bone_arm", turn, (1, 0, 0), "bone_spine"),
    ]
    segs = [segm(QUAD, [0, 1, 2, 3], "hp_gun"), skinned_segm([0, 0, 1, 1])]
    return model.decode(model_record("guy", joints), segments_record(segs))


class GltfTests(unittest.TestCase):
    def test_structure_and_placement(self):
        m = sample()
        glb = gltf.export(m)
        doc, binary = gltf.read(glb)
        names = [n["name"] for n in doc["nodes"]]
        self.assertIn("bone_arm", names)
        # The rigid piece hangs from its joint node.
        gun = doc["nodes"][names.index("hp_gun")]
        self.assertIn(names.index("p000_hp_gun"), gun["children"])
        summary = gltf_check.check(glb, m)
        self.assertEqual((summary["meshes"], summary["skins"]), (2, 1))
        self.assertLess(summary["worst_error"], 1e-4)

    def test_skin_uses_bone_palette(self):
        m = sample()
        doc, _ = gltf.read(gltf.export(m))
        names = [n["name"] for n in doc["nodes"]]
        skin = doc["skins"][0]
        self.assertEqual([names[j] for j in skin["joints"]], ["bone_spine", "bone_arm"])
        self.assertEqual(gltf.skin_palette(m.joints), [1, 3])

    def test_half_turn_conversion(self):
        # A turn about Y (not a mirror): left stays left.
        self.assertEqual(gltf._flip_point((1, 2, 3)), (-1, 2, -3))
        self.assertEqual(gltf._flip_quat((0.1, 0.2, 0.3, 0.9)), (-0.1, 0.2, -0.3, 0.9))
        x, y, z = (gltf._flip_point(v) for v in ((1, 0, 0), (0, 1, 0), (0, 0, 1)))
        det = x[0] * (y[1] * z[2] - y[2] * z[1]) - x[1] * (y[0] * z[2] - y[2] * z[0]) + x[2] * (y[0] * z[1] - y[1] * z[0])
        self.assertEqual(det, 1)

    def test_textures_embedded_with_alpha_mode(self):
        m = sample()
        img = png.encode(1, 1, bytes((255, 0, 0, 0)), 4)

        def textured_material(flags):
            for p in m.pieces:
                p.material = (0, 0, 0, p.texture, 0, flags, 0)
            doc, binary = gltf.read(gltf.export(m, textures={name_hash("tex"): (img, True)}))
            return doc, binary, [x for x in doc["materials"] if "baseColorTexture" in x["pbrMetallicRoughness"]][0]

        # Cut-outs only where the game alpha-tests (material flag 0x10, as on leaves).
        doc, binary, mat = textured_material(0x111)
        self.assertEqual(len(doc["images"]), 1)
        self.assertEqual(mat["alphaMode"], "MASK")
        _, _, mat = textured_material(0x100)        # e.g. a building wall: alpha holds other data
        self.assertNotIn("alphaMode", mat)
        view = doc["bufferViews"][doc["images"][0]["bufferView"]]
        self.assertEqual(binary[view["byteOffset"]:view["byteOffset"] + 8], b"\x89PNG\r\n\x1a\n")

    def test_check_catches_damage(self):
        m = sample()
        glb = bytearray(gltf.export(m))
        doc, _ = gltf.read(bytes(glb))
        # Move the root node: every rigid vertex is now misplaced.
        text = bytes(glb[20:20 + struct.unpack_from("<I", glb, 12)[0]])
        bad = text.replace(b'"name":"guy","children"', b'"name":"guy","translation":[0,5,0],"children"', 1)
        self.assertNotEqual(bad, text)
        glb[20:20 + len(text)] = bad[:len(text)] if len(bad) == len(text) else bad
        if len(bad) != len(text):
            pad = bad + b" " * (-len(bad) % 4)
            body = struct.pack("<II", len(pad), 0x4E4F534A) + pad + bytes(glb[20 + len(text):])
            glb = bytearray(struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body)
        with self.assertRaises(ValueError):
            gltf_check.check(bytes(glb), m)

    def test_read_rejects_other_files(self):
        with self.assertRaises(ValueError):
            gltf.read(b"\0" * 32)


if __name__ == "__main__":
    unittest.main()
