import struct
import unittest

from mercfoundry import model, script, ucfb
from mercfoundry.hashing import name_hash


def chunk(tag, data):
    return ucfb.pack_chunk(tag, data)


def joint(name, rot, pos, parent):
    return chunk(b"JONT", name.encode() + b"\0" + struct.pack("<4f3f", *rot, *pos) + parent.encode() + b"\0")


def model_record(name, joints, qbox=(-1, -1, -1, 1, 1, 1)):
    info = struct.pack("<4H", 9, 2, 0, 0x100) + struct.pack("<12f", *qbox, *qbox)
    info += struct.pack("<I4ff", len(joints), 0, 50, 100, 200, 0.1) + b"\0"
    skel = chunk(b"NBNS", struct.pack("<I", len(joints))) + chunk(b"FSBI", b"\xff" * 4) + b"".join(joints)
    body = chunk(b"NAME", name.encode() + b"\0") + chunk(b"NODE", b"root\0") + chunk(b"INFO", info) + chunk(b"SKEL", skel)
    return ucfb.wrap(b"modl", body)


def vertex(q, normal=0, uv=(0, 0), color=b"\xff\xff\xff\xff"):
    return struct.pack("<3hHI", *q, 0, normal) + color + struct.pack("<2h", *uv)


def segm(verts, strip, joint_name, lod=1, texture="tex"):
    info = struct.pack("<4I", len(verts), len(strip), 20, lod)
    mtrl = struct.pack("<7I", 1, name_hash(joint_name), name_hash(joint_name), name_hash(texture), 0, 0x100, 0)
    return chunk(b"segm", chunk(b"INFO", info) + chunk(b"MTRL", mtrl)
                 + chunk(b"VBUF", b"".join(verts)) + chunk(b"IBUF", struct.pack(f"<{len(strip)}H", *strip)))


def segments_record(segms):
    tree = chunk(b"ucfb", b"".join(segms))
    packed = script.compress(tree)
    return ucfb.wrap(b"CSEG", struct.pack("<II", len(tree), len(packed)) + packed)


QUAD = [vertex((0, 0, 0)), vertex((32767, 0, 0)), vertex((0, 32767, 0)), vertex((32767, 32767, 0))]


class ModelTests(unittest.TestCase):
    def build(self):
        turn = (0.0, 0.7071068, 0.0, 0.7071068)  # 90 degrees about Y
        joints = [
            joint("root", (0, 0, 0, 1), (0, 0, 0), ""),
            joint("arm", turn, (5, 0, 0), "root"),
            joint("segment_pristine_L", (0, 0, 0, 1), (0, 0, 0), "root"),
            joint("segment_damage_L", (0, 0, 0, 1), (0, 0, 0), "root"),
        ]
        segs = [
            segm(QUAD, [0, 1, 2, 3], "root", lod=1),
            segm(QUAD, [0, 1, 2, 3], "arm", lod=3),
            segm(QUAD, [0, 1, 2], "segment_pristine_L", lod=1),
            segm(QUAD, [0, 1, 2], "segment_damage_L", lod=1),
        ]
        return model.decode(model_record("test_model", joints), segments_record(segs))

    def test_header_and_joints(self):
        m = self.build()
        self.assertEqual(m.name, "test_model")
        self.assertEqual(m.node, "root")
        self.assertEqual(m.lod_distances, (0, 50, 100, 200))
        self.assertEqual([j.name for j in m.joints][:2], ["root", "arm"])
        self.assertEqual(m.lods(), [0, 1])

    def test_positions_use_box_and_joint(self):
        m = self.build()
        root, arm = m.pieces[0], m.pieces[1]
        self.assertAlmostEqual(root.positions[1][0], 1.0, places=4)
        # (1, 0, 0) turned 90 degrees about Y is (0, 0, -1), then moved by (5, 0, 0).
        x, y, z = arm.positions[1]
        self.assertAlmostEqual(x, 5.0, places=4)
        self.assertAlmostEqual(z, -1.0, places=4)

    def test_strip_and_degenerates(self):
        self.assertEqual(model.strip_to_triangles([0, 1, 2, 3]), [(0, 1, 2), (2, 1, 3)])
        self.assertEqual(model.strip_to_triangles([0, 1, 2, 2, 5, 5, 6, 7]), [(0, 1, 2), (6, 5, 7)])

    def test_normal_unpacking(self):
        up = (511 << 22)  # z = +1
        self.assertEqual(model.unpack_normal(up), (0.0, 0.0, 1.0))
        self.assertAlmostEqual(model.unpack_normal(0x400 << 11)[1], -1024 / 1023)

    def test_lod_and_damage_filter(self):
        m = self.build()
        self.assertEqual(len(m.at_lod(0)), 3)                 # root, arm, pristine
        self.assertEqual(len(m.at_lod(0, damaged=True)), 3)   # root, arm, damaged
        self.assertEqual(len(m.at_lod(1)), 1)                 # only arm (mask 3)

    def test_obj_and_preview(self):
        m = self.build()
        obj, mtl = model.to_obj(m, 0, lambda h: "tex" if h == name_hash("tex") else "?")
        self.assertEqual(obj.count("\nv "), 12)
        self.assertEqual(obj.count("\nf "), 5)  # 2 + 2 + 1 triangles
        self.assertIn("map_Kd tex.png", mtl)
        png = model.preview_png(m, size=64)
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_rejects_bad_index(self):
        segs = [segm(QUAD, [0, 1, 9], "root")]
        with self.assertRaises(ValueError):
            model.decode(model_record("bad", [joint("root", (0, 0, 0, 1), (0, 0, 0), "")]), segments_record(segs))


if __name__ == "__main__":
    unittest.main()
