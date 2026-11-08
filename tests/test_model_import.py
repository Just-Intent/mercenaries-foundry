import json
import math
import tempfile
import unittest
from pathlib import Path

from mercfoundry import dsk, model, model_import as mi
from mercfoundry.hashing import name_hash
from tests.test_dsk import build
from tests.test_model import QUAD, joint, model_record, segm, segments_record, vertex


def sample():
    turn = (0.0, 0.7071068, 0.0, 0.7071068)
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
        segm(QUAD[:3], [0, 1, 2], "root", lod=2),
    ]
    return model_record("sample", joints), segments_record(segs)


def edit_obj(obj, fn):
    """Apply fn(object name, x, y, z) -> (x, y, z) to OBJ vertex lines."""
    out, name = [], None
    for line in obj.splitlines():
        if line.startswith("o "):
            name = line[2:]
        if line.startswith("v "):
            p = line.split()
            x, y, z = fn(name, float(p[1]), float(p[2]), float(p[3]))
            line = " ".join(["v", f"{x:.8f}", f"{y:.8f}", f"{z:.8f}"] + p[4:])
        out.append(line)
    return "\n".join(out) + "\n"


class ObjReaderTests(unittest.TestCase):
    def test_blender_style(self):
        text = """# made by hand
mtllib x.mtl
o Cube
v 0 0 0
v 1 0 0
v 1 1 0
v 0 1 0
vt 0 0
vt 1 0
vt 1 1
vt 0 1
usemtl brick.001
f 1/1 2/2 3/3 4/4
f -4/-4 -2/-2 -1/-1
"""
        (p,) = mi.parse_obj(text)
        self.assertEqual(p.name, "Cube")
        self.assertEqual(p.material, "brick.001")
        self.assertEqual(len(p.triangles), 3)       # quad -> 2, plus one triangle
        self.assertEqual(p.positions[1], (1.0, 0.0, -0.0))
        self.assertEqual(p.uvs[2], (1.0, 0.0))      # V flipped back
        self.assertIsNone(p.normals[0])

    def test_strip_keeps_triangles_and_winding(self):
        tris = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (2, 1, 9)]
        self.assertEqual(model.strip_to_triangles(mi.triangles_to_strip(tris)), tris)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.mr, self.sr = sample()
        self.m = model.decode(self.mr, self.sr)

    def test_unchanged_is_identical(self):
        for lod in (0, 1):
            for damaged in (False, True):
                obj, _ = model.to_obj(self.m, lod, damaged=damaged)
                nm, ns, rep = mi.import_obj(self.mr, self.sr, obj, lod, damaged)
                self.assertEqual((nm, ns), (self.mr, self.sr))
                self.assertFalse(rep.changed)

    def test_edit_one_piece(self):
        obj, _ = model.to_obj(self.m, 0)
        moved = edit_obj(obj, lambda n, x, y, z: (x * 0.5, y, z) if n.startswith("p000") else (x, y, z))
        nm, ns, rep = mi.import_obj(self.mr, self.sr, moved, 0)
        self.assertEqual(rep.edited, [0])
        m2 = model.decode(nm, ns)
        self.assertAlmostEqual(m2.pieces[0].positions[1][0], 0.5, places=3)
        # Other pieces are untouched, byte for byte.
        self.assertEqual(m2.pieces[1].positions, self.m.pieces[1].positions)
        self.assertEqual(m2.pieces[3].positions, self.m.pieces[3].positions)

    def test_edit_on_rotated_joint_stays_local(self):
        obj, _ = model.to_obj(self.m, 0)
        lifted = edit_obj(obj, lambda n, x, y, z: (x, y + 0.25, z) if n.startswith("p001") else (x, y, z))
        nm, ns, rep = mi.import_obj(self.mr, self.sr, lifted, 0)
        self.assertEqual(rep.edited, [1])
        m2 = model.decode(nm, ns)
        for a, b in zip(self.m.pieces[1].positions, m2.pieces[1].positions):
            self.assertAlmostEqual(b[1], a[1] + 0.25, places=3)
            self.assertAlmostEqual(b[0], a[0], places=3)

    def test_box_grows_and_untouched_pieces_survive(self):
        obj, _ = model.to_obj(self.m, 0)
        big = edit_obj(obj, lambda n, x, y, z: (x * 4, y, z) if n.startswith("p000") else (x, y, z))
        nm, ns, rep = mi.import_obj(self.mr, self.sr, big, 0)
        self.assertTrue(rep.box_grew)
        m2 = model.decode(nm, ns)
        self.assertAlmostEqual(m2.pieces[0].positions[1][0], 4.0, places=3)
        for a, b in zip(self.m.pieces[3].positions, m2.pieces[3].positions):
            self.assertLess(math.dist(a, b), 1e-3)
        self.assertGreaterEqual(m2.bounds[1][0], 4.0 - 1e-3)

    def test_add_and_remove(self):
        obj, _ = model.to_obj(self.m, 0)
        lines, skip = [], False
        for line in obj.splitlines():
            if line.startswith("o "):
                skip = line[2:].startswith("p002")
            if not skip:
                lines.append(line)
        # Removing p002 shifts nothing else because each piece's vertices
        # are referenced with absolute indices; rebuild the file from parts.
        parts = [p for p in mi.parse_obj(obj) if not p.name.startswith("p002")]
        new = mi.ObjPiece("new_arm", "shiny")
        new.positions = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
        new.uvs = [(0, 0)] * 3
        new.normals = [None] * 3
        new.colors = [None] * 3
        new.triangles = [(0, 1, 2)]
        text = []
        base = 1
        for p in parts + [new]:
            text.append(f"o {p.name}")
            text.append(f"usemtl {p.material}")
            text += [f"v {x:.8f} {y:.8f} {-z:.8f}" for x, y, z in p.positions]
            text += [f"vt {u} {1 - v}" for u, v in p.uvs]
            text += [f"f {a + base}/{a + base} {c + base}/{c + base} {b + base}/{b + base}" for a, b, c in p.triangles]
            base += len(p.positions)
        nm, ns, rep = mi.import_obj(self.mr, self.sr, "\n".join(text) + "\n", 0)
        self.assertEqual(rep.removed, [2])
        self.assertEqual(rep.added, ["new_arm"])
        m2 = model.decode(nm, ns)
        self.assertEqual(len(m2.pieces), 5)  # 5 - 1 + 1
        added = m2.pieces[-1]
        self.assertEqual(added.joint, name_hash("arm"))
        self.assertEqual(added.texture, name_hash("shiny"))
        self.assertEqual(added.lod_mask, 3)
        # The new piece is placed in model space where the OBJ put it.
        self.assertAlmostEqual(added.positions[1][0], 1.0, places=3)

    def test_folders_and_undo(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            key = name_hash("sample")
            build(d / "a.dsk", [(key, model.MODEL, self.mr), (key, model.SEGMENTS, self.sr), (7, 8, b"x")])
            dsk.extract(d / "a.dsk", d / "x")
            obj, _ = model.to_obj(self.m, 0)
            moved = edit_obj(obj, lambda n, x, y, z: (x, y + 1, z) if n.startswith("p000") else (x, y, z))
            ((_, rep),) = mi.import_into_folders([d / "x"], "sample", moved)
            self.assertEqual(rep.edited, [0])
            self.assertTrue((d / "x" / "originals").exists())
            dsk.pack(d / "x", d / "b.dsk")
            _, _, changed = dsk.diff(d / "a.dsk", d / "b.dsk")
            self.assertEqual(len(changed), 2)
            # Importing the untouched export restores the originals.
            mi.import_into_folders([d / "x"], "sample", obj)
            dsk.pack(d / "x", d / "c.dsk")
            self.assertEqual((d / "a.dsk").read_bytes(), (d / "c.dsk").read_bytes())
            with self.assertRaises(KeyError):
                mi.import_into_folders([d / "x"], "missing", obj)


if __name__ == "__main__":
    unittest.main()
