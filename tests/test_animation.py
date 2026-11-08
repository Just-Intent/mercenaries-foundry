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


import base64 as _b64
import json as _json
import tempfile as _tempfile
from pathlib import Path as _Path

from mercfoundry import dsk as _dsk, gltf_anim, modbuild
from mercfoundry.hashing import name_hash as _nh
from tests.test_dsk import build as _build


def _model():
    joints = [joint("root", (0, 0, 0, 1), (0, 0, 0), ""), joint("bone_a", (0, 0, 0, 1), (1, 0, 0), "root")]
    return model.decode(model_record("guy", joints), segments_record([segm(QUAD, [0, 1, 2, 3], "bone_a")]))


class EncodeTests(unittest.TestCase):
    def test_channel_tokens(self):
        enc = animation.encode_channel
        self.assertEqual(enc([5]), struct.pack("<h", 5))
        self.assertEqual(enc([0, 0, 0]), b"\0\0\0\0")                    # short run stays literal
        self.assertEqual(enc([0, 0, 0, 0]), b"\0\0\x80\x03")            # run of 3 -> hold
        self.assertEqual(enc([0] * 258), b"\0\0\x80\xff\x00\x00")         # 255 hold + 2 literal
        self.assertEqual(enc([0, 127, -1]), b"\0\0\x7f\x81\xff\xff")    # +127 fits, -128 doesn't
        self.assertEqual(enc([0, -126]), b"\0\0\x82")                     # -126 is the lowest delta
        for vals in ([0, 300, 300, 300, 299, -5000], [2047] * 600, list(range(-200, 200, 7))):
            got = animation._channel(enc(vals), 0, len(vals))
            self.assertEqual(got[0], vals)

    def test_encode_decode_same_values(self):
        for compress in (False, True):
            rec = anim_record(compress=compress, stray=True)
            a = animation.decode(rec)
            b = animation.decode(animation.encode(a, rec))
            self.assertEqual([t.rotations for t in a.tracks], [t.rotations for t in b.tracks])
            self.assertEqual(a.tracks[1].translation, b.tracks[1].translation)
            for p, q in zip(a.tracks[0].translations, b.tracks[0].translations):
                for x, y in zip(p, q):
                    self.assertAlmostEqual(x, y, places=6)

    def test_encode_without_base(self):
        a = animation.decode(anim_record())
        b = animation.decode(animation.encode(a))
        self.assertEqual((b.name, b.frames, [t.joint for t in b.tracks]), ("test_anim", 3, ["root", "bone_a"]))

    def test_frame_count_check(self):
        a = animation.decode(anim_record())
        a.tracks[0].rotations.pop()
        with self.assertRaises(ValueError):
            animation.encode(a)


class GltfImportTests(unittest.TestCase):
    def test_lossless_round_trip(self):
        rec = anim_record()
        a = animation.decode(rec)
        with _tempfile.TemporaryDirectory() as tmp:
            path = _Path(tmp) / "x.glb"
            path.write_bytes(gltf.export(_model(), animations=[a]))
            clips = gltf_anim.clips(path)
        self.assertEqual(list(clips), ["test_anim"])
        b = animation.decode(animation.encode(gltf_anim.to_animation(clips["test_anim"], a), rec))
        self.assertEqual([t.rotations for t in a.tracks], [t.rotations for t in b.tracks])

    def test_names_and_edits(self):
        self.assertEqual(gltf_anim.clip_name("Armature|walk.001"), "walk")
        a = animation.decode(anim_record())
        turned = (0.0, 0.0, 0.7071068, 0.7071068)   # glTF axes
        tracks = {"bone_a": {"rotation": ([0.0, 2 / 30], [turned, turned], "LINEAR")}}
        b = gltf_anim.to_animation(tracks, a)
        self.assertEqual(b.frames, 3)
        x, y, z, w = b.tracks[1].rotations[1]
        self.assertAlmostEqual(z, 0.7071068, places=5)      # mirroring Z negates x and y, not z
        self.assertEqual((x, y), (0.0, 0.0))
        self.assertEqual(b.tracks[0].rotations, a.tracks[0].rotations)  # untouched joint kept

    def test_mod_animations_folder(self):
        with _tempfile.TemporaryDirectory() as tmp:
            d = _Path(tmp)
            (d / "data").mkdir()
            _build(d / "data" / "streamed.dsk", [(_nh("test_anim"), animation.ANIMATION, anim_record())])
            modbuild.new_project(d / "proj")
            turned = [0.0, 0.0, 0.7071068, 0.7071068]
            buf = struct.pack("<2f", 0.0, 2 / 30) + struct.pack("<8f", *turned, *turned)
            doc = {"asset": {"version": "2.0"}, "nodes": [{"name": "bone_a"}],
                   "buffers": [{"byteLength": len(buf), "uri": "data:application/octet-stream;base64,"
                                + _b64.b64encode(buf).decode()}],
                   "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 8},
                                   {"buffer": 0, "byteOffset": 8, "byteLength": 32}],
                   "accessors": [{"bufferView": 0, "componentType": 5126, "count": 2, "type": "SCALAR"},
                                 {"bufferView": 1, "componentType": 5126, "count": 2, "type": "VEC4"}],
                   "animations": [{"name": "Armature|test_anim", "samplers": [{"input": 0, "output": 1}],
                                   "channels": [{"sampler": 0, "target": {"node": 0, "path": "rotation"}}]},
                                  {"name": "nonexistent", "samplers": [{"input": 0, "output": 1}],
                                   "channels": [{"sampler": 0, "target": {"node": 0, "path": "rotation"}}]}]}
            (d / "proj" / "animations" / "edit.gltf").write_text(_json.dumps(doc))
            result = modbuild.build(d / "proj", d / "data", d / "out")
            self.assertEqual(result.written, ["streamed.dsk"])
            self.assertTrue(any("nonexistent" in w for w in result.warnings))
            arc = _dsk.Archive(d / "out" / "streamed.dsk")
            got = animation.decode(arc.read(arc.records[0]))
            self.assertAlmostEqual(got.tracks[1].rotations[2][2], 0.7071, places=3)
