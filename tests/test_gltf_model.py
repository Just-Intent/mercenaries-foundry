import json
import struct
import tempfile
import unittest
from pathlib import Path

from mercfoundry import dsk, gltf, gltf_model, model, modbuild
from mercfoundry.hashing import name_hash
from tests.test_dsk import build as build_dsk
from tests.test_model import QUAD, chunk, joint, model_record, segm, segments_record

JOINTS = [joint("root", (0, 0, 0, 1), (0, 0, 0), ""),
          joint("bone_a", (0, 0, 0, 1), (0.5, 0, 0), "root"),
          joint("bone_b", (0, 0, 0, 1), (0, 0.5, 0), "bone_a")]


def skinned_segm(bones):
    verts = [struct.pack("<3hHI", *q, b, 0) + b"\xff\xff\xff\xff" + struct.pack("<2h", 0, 0)
             for q, b in zip([(0, 0, 0), (16000, 0, 0), (0, 16000, 0), (16000, 16000, 0)], bones)]
    strip = [0, 1, 2, 3]
    info = struct.pack("<4I", 4, 4, 20 | 0x10000, 1)
    mtrl = struct.pack("<7I", 1, name_hash("root"), name_hash("root"), name_hash("skin"), 0, 0x100, 0)
    return chunk(b"segm", chunk(b"INFO", info) + chunk(b"MTRL", mtrl)
                 + chunk(b"VBUF", b"".join(verts)) + chunk(b"IBUF", struct.pack("<4H", *strip)))


def records():
    mr = model_record("guy", JOINTS)
    sr = segments_record([segm(QUAD, [0, 1, 2, 3], "bone_a"), skinned_segm([0, 0, 1, 1])])
    return mr, sr


def write_glb(path, doc, binary):
    doc["buffers"] = [{"byteLength": len(binary)}]
    text = json.dumps(doc).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", len(binary), 0x004E4942) + binary
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body)


def chunk_items(model_record_bytes):
    from mercfoundry import ucfb
    return ucfb.iter_chunks(ucfb.unwrap(model_record_bytes, b"modl"))


def node(doc, name):
    return next(i for i, n in enumerate(doc["nodes"]) if n.get("name") == name)


class GltfModelImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.mr, self.sr = records()
        self.model = model.decode(self.mr, self.sr)
        self.doc, binary = gltf.read(gltf.export(self.model))
        self.bin = bytearray(binary)
        self.path = self.dir / "guy.glb"

    def tearDown(self):
        self.tmp.cleanup()

    def run_import(self):
        write_glb(self.path, self.doc, bytes(self.bin))
        return gltf_model.import_gltf(self.mr, self.sr, self.path)

    def test_unedited_is_unchanged(self):
        nm, ns, rep = self.run_import()
        self.assertEqual((nm, ns, rep.changed), (self.mr, self.sr, False))
        self.assertEqual(rep.unchanged, 2)

    def test_moved_piece(self):
        n = self.doc["nodes"][node(self.doc, "p000_bone_a")]
        n["translation"] = [0.0, 0.25, 0.0]
        nm, ns, rep = self.run_import()
        self.assertEqual(rep.edited, [0])
        before, after = self.model.pieces[0].positions, model.decode(nm, ns).pieces[0].positions
        for a, b in zip(sorted(before), sorted(after)):
            self.assertAlmostEqual(b[1] - a[1], 0.25, places=3)

    def test_new_piece_takes_parent_joint(self):
        mesh = self.doc["nodes"][node(self.doc, "p000_bone_a")]["mesh"]
        self.doc["nodes"].append({"name": "fin", "mesh": mesh, "translation": [0, 0, -0.1]})
        self.doc["nodes"][node(self.doc, "bone_b")].setdefault("children", []).append(len(self.doc["nodes"]) - 1)
        nm, ns, rep = self.run_import()
        self.assertEqual(rep.added, ["fin"])
        new = model.decode(nm, ns).pieces[-1]
        self.assertEqual(new.joint, name_hash("bone_b"))
        # Placed under bone_b (0.5, 0.5, 0 in game space), 0.1 m along game +Z.
        want = sorted((p[0], p[1] + 0.5, p[2] + 0.1) for p in self.model.pieces[0].positions)
        for a, b in zip(want, sorted(new.positions)):
            for x, y in zip(a, b):
                self.assertAlmostEqual(x, y, places=3)

    def test_new_pieces_cut_out_by_alpha_mode(self):
        # each new piece on its own material: MASK and BLEND cut out, OPAQUE not, none as the template
        base = self.doc["meshes"][self.doc["nodes"][node(self.doc, "p000_bone_a")]["mesh"]]
        root = node(self.doc, "root")
        for name, mode in (("frond", "MASK"), ("glass", "BLEND"), ("wall", "OPAQUE"), ("plain", None)):
            mat = {"name": f"{name}_mat"}
            if mode:
                mat["alphaMode"] = mode
            self.doc["materials"].append(mat)
            prim = dict(base["primitives"][0], material=len(self.doc["materials"]) - 1)
            self.doc["meshes"].append({"primitives": [prim]})
            self.doc["nodes"].append({"name": name, "mesh": len(self.doc["meshes"]) - 1})
            self.doc["nodes"][root].setdefault("children", []).append(len(self.doc["nodes"]) - 1)
        nm, ns, rep = self.run_import()
        self.assertEqual(rep.added, ["frond", "glass", "wall", "plain"])
        pieces = model.decode(nm, ns).pieces[-4:]
        template = gltf.cutout_flag(self.model.pieces[0])
        self.assertEqual([gltf.cutout_flag(p) for p in pieces], [True, True, False, template])

    def test_alpha_mode_word(self):
        from mercfoundry.model_import import _cutout
        self.assertEqual(_cutout([0, 0, 0, 0, 0, 0x100, 0], "MASK")[5], 0x110)
        self.assertEqual(_cutout([0, 0, 0, 0, 0, 0x110, 0], "OPAQUE")[5], 0x100)
        self.assertEqual(_cutout([0, 0, 0, 0, 0, 0x110, 0], None)[5], 0x110)

    def test_metallic_factor_zero_drops_reflection(self):
        # a piece copied from a shiny template (a chrome hub's reflection
        # texture in word 4, flag 0x20 in word 6): metallicFactor 0 makes it
        # matte; none given, or above 0, leaves it shiny
        from mercfoundry.model_import import _finish
        shiny = [1, 2, 2, 3, 0x5973c764, 0x100, 0x21]
        self.assertEqual(_finish(shiny, None, 0.0)[4:], [0, 0x100, 0x1])
        self.assertEqual(_finish(shiny, None, None), shiny)
        self.assertEqual(_finish(shiny, None, 1.0), shiny)
        self.assertEqual(_finish(shiny, "MASK", 0)[5:], [0x110, 0x1])

    def test_template_is_the_texture_s_usual_material(self):
        # a base whose first glass piece is the odd opaque one (word 5 0x100)
        # and the rest translucent (0x101): a new glass piece copies the
        # translucent one; a texture the base doesn't draw: its first piece
        from types import SimpleNamespace as P
        from mercfoundry.model_import import template_piece
        glass, body = name_hash("glass"), name_hash("body")
        odd = P(texture=glass, material=(1, 2, 2, glass, 7, 0x100, 0x21))
        usual = P(texture=glass, material=(1, 3, 3, glass, 7, 0x101, 0x21))
        first = P(texture=body, material=(1, 4, 4, body, 0, 0x100, 0))
        pieces = [first, odd, usual, P(texture=glass, material=usual.material)]
        self.assertIs(template_piece(pieces, glass), usual)
        self.assertIs(template_piece(pieces, name_hash("other")), first)
        self.assertIsNone(template_piece([], glass))

    def test_editor_shows_new_models_cut_out_as_built(self):
        from types import SimpleNamespace
        from mercfoundry.editor import scene
        from tests.test_model import segm
        leaf = segm(QUAD, [0, 1, 2, 3], "bone_a", texture="leaf").replace(
            struct.pack("<2I", 0, 0x100), struct.pack("<2I", 0, 0x110))
        build_dsk(self.dir / "streamed.dsk", [
            (name_hash("leafy"), model.MODEL, model_record("leafy", JOINTS)),
            (name_hash("leafy"), model.SEGMENTS, segments_record([leaf, segm(QUAD, [0, 1, 2, 3], "bone_a", texture="bark")]))])
        spec = self.dir / "palm.new.json"
        spec.write_text(json.dumps({"base": "leafy"}))
        self.doc["materials"] = [{"name": "bark"}, {"name": "frond"}, {"name": "wall", "alphaMode": "OPAQUE"}]
        write_glb(self.path, self.doc, bytes(self.bin))
        fake = SimpleNamespace(archives=[dsk.Archive(self.dir / "streamed.dsk")])
        out, _ = gltf.read(scene.Scene._new_model_glb(fake, self.path.read_bytes(), spec))
        # bark: the base's bark piece (opaque); frond: a new texture, so the base's first piece (cut out)
        self.assertEqual([m.get("alphaMode") for m in out["materials"]], [None, "MASK", "OPAQUE"])

    def test_reweighted_skin(self):
        prim = self.doc["meshes"][self.doc["nodes"][node(self.doc, "p001_root")]["mesh"]]["primitives"][0]
        acc = self.doc["accessors"][prim["attributes"]["JOINTS_0"]]
        off = self.doc["bufferViews"][acc["bufferView"]]["byteOffset"]
        for v in range(acc["count"]):
            struct.pack_into("<H", self.bin, off + 8 * v, 1)
        nm, ns, rep = self.run_import()
        self.assertEqual(rep.edited, [1])
        got = model.decode(nm, ns).pieces[1]
        self.assertEqual(got.bones, [1, 1, 1, 1])
        for a, b in zip(sorted(self.model.pieces[1].positions), sorted(got.positions)):
            for x, y in zip(a, b):
                self.assertAlmostEqual(x, y, places=3)

    def test_settings_recorded_or_guessed(self):
        write_glb(self.path, self.doc, bytes(self.bin))
        _, _, settings = gltf_model.read(self.path, self.model)
        self.assertEqual(settings, {"lod": 0, "damaged": False})
        del self.doc["asset"]["extras"]
        write_glb(self.path, self.doc, bytes(self.bin))
        pieces, _, settings = gltf_model.read(self.path, self.model)
        self.assertEqual(settings, {})
        self.assertEqual(gltf_model.guess_view(self.model, pieces), (0, False))

    def test_moved_joint(self):
        # bone_b is local (0, 0.5, 0) to bone_a; move it up to (0, 0.9, 0)
        self.doc["nodes"][node(self.doc, "bone_b")]["translation"] = [0, 0.9, 0]
        nm, ns, rep = self.run_import()
        self.assertTrue(any("moved joints: bone_b" in w for w in rep.warnings))
        joints = {j.name: j for j in model.decode(nm, ns).joints}
        self.assertEqual([round(v, 4) for v in joints["bone_b"].translation], [0, 0.9, 0])
        self.assertEqual(joints["bone_a"].translation, (0.5, 0, 0))        # untouched
        self.assertEqual(joints["bone_b"].rotation, (0, 0, 0, 1))           # rotations kept

    def test_moved_parent_carries_children(self):
        self.doc["nodes"][node(self.doc, "bone_a")]["translation"] = [-0.5, 0.0, -0.3]  # glTF x, z = game -x, -z
        nm, ns, rep = self.run_import()
        m = model.decode(nm, ns)
        joints = {j.name: j for j in m.joints}
        self.assertEqual([round(v, 4) for v in joints["bone_a"].translation], [0.5, 0, 0.3])
        self.assertEqual(joints["bone_b"].translation, (0, 0.5, 0))         # child keeps its offset
        # the rigid piece on bone_a moved with it and is stored unchanged
        # (joint-local); the skinned piece is re-read, its vertices follow the bone
        self.assertEqual(rep.unchanged, 1)
        rigid = [p for p in m.pieces if p.bones is None][0]
        self.assertAlmostEqual(min(v[2] for v in rigid.positions), 0.3, places=3)

    def test_new_joint(self):
        a = node(self.doc, "bone_a")
        self.doc["nodes"].append({"name": "hp_new", "translation": [0.0, 0.0, -0.2]})
        self.doc["nodes"][a].setdefault("children", []).append(len(self.doc["nodes"]) - 1)
        nm, ns, rep = self.run_import()
        self.assertTrue(any("new joints: hp_new" in w for w in rep.warnings))
        m = model.decode(nm, ns)
        self.assertEqual(len(m.joints), 4)
        new = m.joints[-1]
        self.assertEqual((new.name, new.parent), ("hp_new", "bone_a"))
        self.assertEqual([round(v, 4) for v in new.translation], [0, 0, 0.2])
        self.assertEqual(struct.unpack_from("<I", dict(chunk_items(nm))[b"INFO"], 56)[0], 4)
        self.assertEqual(model.joint_transforms(m.joints)[name_hash("hp_new")][1][0], 0.5)

    def test_unchanged_skeleton_rebuilds_exactly(self):
        self.assertEqual(gltf_model.with_skeleton(self.mr, self.model.joints), self.mr)

    def test_mod_models_folder(self):
        n = self.doc["nodes"][node(self.doc, "p000_bone_a")]
        n["translation"] = [0.0, 0.25, 0.0]
        (self.dir / "data").mkdir()
        build_dsk(self.dir / "data" / "streamed.dsk", [(name_hash("guy"), model.MODEL, self.mr),
                                                       (name_hash("guy"), model.SEGMENTS, self.sr)])
        modbuild.new_project(self.dir / "proj")
        write_glb(self.dir / "proj" / "models" / "guy.glb", self.doc, bytes(self.bin))
        result = modbuild.build(self.dir / "proj", self.dir / "data", self.dir / "out")
        self.assertEqual(result.written, ["streamed.dsk"])
        arc = dsk.Archive(self.dir / "out" / "streamed.dsk")
        recs = {r.kind: arc.read(r) for r in arc.records}
        got = model.decode(recs[model.MODEL], recs[model.SEGMENTS]).pieces[0].positions
        self.assertAlmostEqual(sum(p[1] for p in got) / 4 - sum(p[1] for p in self.model.pieces[0].positions) / 4,
                               0.25, places=3)


if __name__ == "__main__":
    unittest.main()


class ModelChangesTests(GltfModelImportTests):
    def test_changes_file_builds_the_same(self):
        from mercfoundry import model_changes
        self.doc["nodes"][node(self.doc, "p000_bone_a")]["translation"] = [0.0, 0.25, 0.0]
        full = self.run_import()
        glb, rep = model_changes.make_changes(self.mr, self.sr, self.path)
        self.assertEqual(rep.edited, [0])
        doc, _ = gltf.read(glb)
        names = [n.get("name") for n in doc["nodes"] if "mesh" in n]
        self.assertEqual(names, ["p000_bone_a"])          # the unchanged skinned piece is gone
        self.assertNotIn("images", doc)
        changes = self.dir / "guy.changes.glb"
        changes.write_bytes(glb)
        self.assertEqual(model_changes.import_changes(self.mr, self.sr, changes)[:2], full[:2])

    def test_unedited_makes_no_changes_file(self):
        from mercfoundry import model_changes
        write_glb(self.path, self.doc, bytes(self.bin))
        self.assertIsNone(model_changes.make_changes(self.mr, self.sr, self.path)[0])
