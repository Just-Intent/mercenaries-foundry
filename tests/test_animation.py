import struct
import unittest

from mercfoundry import animation, gltf, model, script, ucfb
from tests.test_model import QUAD, joint, model_record, segm, segments_record


def chunk(tag, data):
    return ucfb.pack_chunk(tag, data)


def channel(start, tokens):
    out = struct.pack("<h", start)
    for t in tokens:
        if t[0] == "d":
            out += struct.pack("<b", t[1])
        elif t[0] == "hold":
            out += bytes((0x80, t[1]))
        elif t[0] == "abs":
            out += b"\x81" + struct.pack("<h", t[1])
    return out


def anim_record(compress=False, stray=True):
    frames = 3
    # Joint "root": per-frame translation, then rotation (identity, holding).
    trans = struct.pack("<9h", 0, 100, 0, 0, 100, -50, 0, 100, -100)
    root_ch = [channel(0, [("hold", 2)])] * 3 + [channel(2047, [("hold", 2)])]
    # Joint "bone_a": x rises by deltas, z jumps with an absolute value.
    a_ch = [channel(0, [("d", 10), ("d", 10)]), channel(0, [("hold", 2)]),
            channel(0, [("abs", 30), ("hold", 1)]), channel(2047, [("hold", 2)])]
    data = trans + b"".join(root_ch) + (b"\xee" if stray else b"") + b"".join(a_ch)
    cjnt = script.compress(data) if compress else data
    info = struct.pack("<HHfHII", frames, 2, 1000.0, 0, len(cjnt), len(data))
    j_root = chunk(b"NAME", b"root\0") + chunk(b"DXLT", struct.pack("<f", 0.01)) \
        + chunk(b"QROT", struct.pack("<4H", *[len(c) for c in root_ch]))
    j_a = chunk(b"NAME", b"bone_a\0") + chunk(b"SXLT", struct.pack("<3h", 500, 0, -250)) \
        + chunk(b"QROT", struct.pack("<4H", *[len(c) for c in a_ch]))
    body = chunk(b"NAME", b"test_anim\0") + chunk(b"INFO", info) + chunk(b"CJNT", cjnt) \
        + chunk(b"jont", j_root) + chunk(b"jont", j_a)
    return ucfb.wrap(b"anim", body)


class AnimationTests(unittest.TestCase):
    def check(self, a):
        self.assertEqual((a.name, a.frames, len(a.tracks)), ("test_anim", 3, 2))
        root, bone = a.tracks
        for got, want in zip(root.translations[2], (0.0, 1.0, -1.0)):
            self.assertAlmostEqual(got, want, places=5)
        self.assertEqual(root.rotations[1], (0.0, 0.0, 0.0, 1.0))
        self.assertEqual(bone.translation, (0.5, 0.0, -0.25))
        self.assertEqual([round(r[0] * 2047) for r in bone.rotations], [0, 10, 20])
        self.assertEqual([round(r[2] * 2047) for r in bone.rotations], [0, 30, 30])

    def test_raw_with_stray_byte(self):
        self.check(animation.decode(anim_record(compress=False, stray=True)))

    def test_compressed(self):
        self.check(animation.decode(anim_record(compress=True, stray=False)))

    def test_bad_rotation_data(self):
        rec = bytearray(anim_record())
        # Corrupt the root's w start value so no shift gives a unit quaternion.
        i = rec.index(struct.pack("<h", 2047))
        rec[i:i + 2] = struct.pack("<h", 900)
        with self.assertRaises(ValueError):
            animation.decode(bytes(rec))

    def test_gltf_animation_and_in_place(self):
        joints = [joint("root", (0, 0, 0, 1), (0, 0, 0), ""), joint("bone_a", (0, 0, 0, 1), (1, 0, 0), "root")]
        m = model.decode(model_record("guy", joints), segments_record([segm(QUAD, [0, 1, 2, 3], "bone_a")]))
        a = animation.decode(anim_record())
        doc, binary = gltf.read(gltf.export(m, animations=[a]))
        clip = doc["animations"][0]
        self.assertEqual(clip["name"], "test_anim")
        paths = sorted((doc["nodes"][c["target"]["node"]]["name"], c["target"]["path"]) for c in clip["channels"])
        self.assertEqual(paths, [("bone_a", "rotation"), ("bone_a", "translation"),
                                 ("root", "rotation"), ("root", "translation")])
        times = doc["accessors"][clip["samplers"][0]["input"]]
        self.assertAlmostEqual(times["max"][0], 2 / animation.FPS, places=5)
        doc, _ = gltf.read(gltf.export(m, animations=[a], in_place=True))
        paths = {(doc["nodes"][c["target"]["node"]]["name"], c["target"]["path"]) for c in doc["animations"][0]["channels"]}
        self.assertNotIn(("root", "translation"), paths)


if __name__ == "__main__":
    unittest.main()
