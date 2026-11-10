import struct
import unittest

from mercfoundry import cmop, collision, model, model_import as mi, script, ucfb
from mercfoundry.hashing import name_hash
from tests.test_model import QUAD, joint, model_record, segm, segments_record


def box_shape(material, joint_name, boxes, kind=0):
    sinf = struct.pack("<4I", material, name_hash(joint_name), kind | len(boxes) << 16, 0)
    cbox = b"".join(collision.pack_box(material, c, h) for c, h in boxes)
    return ucfb.pack_chunk(b"shap", ucfb.pack_children([(b"sinf", sinf), (b"CBOX", cbox)]))


def cshp(shapes, total_boxes, kinds=(0, 0, 0, 0)):
    """kinds: (kind 2/3 box shapes, trees, kind-4 meshes, kind-5 hulls) for cinf."""
    k23, trees, k4, k5 = kinds
    cinf = struct.pack("<7H", len(shapes), k23, trees, total_boxes, k4, k5, 0)
    return ucfb.pack_chunk(b"cshp", ucfb.pack_chunk(b"cinf", cinf) + b"".join(shapes))


class CollisionTests(unittest.TestCase):
    def test_parse_and_corners(self):
        block = cshp([box_shape(7, "root", [((0, 1, 0), (1, 2, 3))])], 1)
        (shape,) = collision.parse(list(ucfb.iter_chunks(block))[0][1])
        self.assertEqual((shape.material, shape.joint, shape.kind, shape.is_mesh), (7, name_hash("root"), 0, False))
        corners = shape.boxes[0].corners()
        self.assertEqual(min(c[1] for c in corners), -1)
        self.assertEqual(max(c[2] for c in corners), 3)

    def test_set_shape_flags(self):
        body = list(ucfb.iter_chunks(cshp([box_shape(7, "a", [((0, 0, 0), (1, 1, 1))] * 2),
                                           box_shape(9, "b", [((0, 0, 0), (1, 1, 1))])], 3)))[0][1]
        new = collision.set_shape_flags(body, {0: 2})
        a, b = collision.parse(new)
        self.assertEqual((a.material, [x.material for x in a.boxes]), (2, [2, 2]))
        self.assertEqual((b.material, b.boxes[0].material), (9, 9))
        self.assertEqual(len(new), len(body))
        with self.assertRaises(ValueError):
            collision.set_shape_flags(body, {5: 1})

    def test_rotated_box_rows(self):
        # Rows are the box's axes: a box turned 90 degrees about Y swaps X and Z extents.
        b = collision.Box(1, (2, 1, 1), (0, 0, -1, 0, 1, 0, 1, 0, 0), (0, 0, 0))
        xs = [c[0] for c in b.corners()]
        zs = [c[2] for c in b.corners()]
        self.assertAlmostEqual(max(xs), 1)
        self.assertAlmostEqual(max(zs), 2)

    def test_add_box_shape_updates_counts(self):
        body = list(ucfb.iter_chunks(cshp([box_shape(7, "root", [((0, 0, 0), (1, 1, 1))])], 1)))[0][1]
        body = collision.add_box_shape(body, 9, name_hash("arm"), [((1, 1, 1), (0.5, 0.5, 0.5)), ((2, 2, 2), (1, 1, 1))])
        items = list(ucfb.iter_chunks(body))
        shapes, kind23, _, boxes = struct.unpack_from("<4H", items[0][1])
        self.assertEqual((shapes, kind23, boxes), (2, 1, 3))
        added = collision.parse(body)[1]
        self.assertEqual((added.material, added.joint, added.kind, len(added.boxes)), (9, name_hash("arm"), 2, 2))

    def test_new_block(self):
        body = collision.new_block(7, name_hash("root"), [((0, 0, 0), (1, 1, 1))])
        self.assertEqual(struct.unpack_from("<4H", list(ucfb.iter_chunks(body))[0][1]), (1, 1, 0, 1))
        self.assertEqual(len(collision.parse(body)[0].boxes), 1)


class ImportCollisionTests(unittest.TestCase):
    def setUp(self):
        joints = [joint("root", (0, 0, 0, 1), (0, 0, 0), ""), joint("arm", (0, 0, 0, 1), (5, 0, 0), "root")]
        self.mr = model_record("thing", joints)
        self.sr = segments_record([segm(QUAD, [0, 1, 2, 3], "root")])
        self.m = model.decode(self.mr, self.sr)

    def obj_with(self, names):
        obj, _ = model.to_obj(self.m, 0)
        lines = obj.splitlines()
        for n, name in enumerate(names):
            nv = sum(1 for l in lines if l.startswith("v "))
            lines += [f"o {name}", "usemtl tex"]
            lines += [f"v {-x} {y} {-z}" for x, y, z in ((5, 0, 0), (6, 0, 0), (5, 2, 1))]   # OBJ = game turned half round
            lines.append(f"f {nv + 1} {nv + 2} {nv + 3}")
        return "\n".join(lines) + "\n"

    def test_new_piece_gets_box_in_joint_space(self):
        nm, ns, rep = mi.import_obj(self.mr, self.sr, self.obj_with(["new_arm", "new_arm_nocoll"]))
        self.assertEqual(rep.collision_boxes, 1)
        m2 = model.decode(nm, ns)
        (shape,) = m2.collision
        self.assertEqual(shape.joint, name_hash("arm"))
        box = shape.boxes[0]
        # Model-space x 5..6 is 0..1 in the arm joint's space (arm sits at x = 5).
        self.assertAlmostEqual(box.centre[0], 0.5, places=3)
        self.assertAlmostEqual(box.half[1], 1.0, places=3)
        tris = m2.collision_triangles()
        self.assertAlmostEqual(max(v[0] for t in tris for v in t), 6.0, places=3)

    def test_collision_can_be_turned_off(self):
        nm, ns, rep = mi.import_obj(self.mr, self.sr, self.obj_with(["new_arm"]), collision=False)
        self.assertEqual(rep.collision_boxes, 0)
        self.assertEqual(model.decode(nm, ns).collision, [])


if __name__ == "__main__":
    unittest.main()


def mesh_shape(material, joint_name, kind=5):
    sinf = struct.pack("<4I", material, name_hash(joint_name), kind, 1)
    cmsh = struct.pack("<IHHH", 0, 3, 1, 0) + struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0) + struct.pack("<3H", 0, 1, 2)
    return ucfb.pack_chunk(b"shap", ucfb.pack_children([(b"sinf", sinf), (b"CMSH", cmsh)]))


def segments_with_collision(segms, cshp_chunk):
    from mercfoundry import script
    tree = ucfb.pack_chunk(b"ucfb", b"".join(segms) + cshp_chunk)
    packed = script.compress(tree)
    return ucfb.wrap(b"CSEG", struct.pack("<II", len(tree), len(packed)) + packed)


class ShapeLimitTests(unittest.TestCase):
    """New boxes must never give a model a third collision shape."""

    def setUp(self):
        self.joints = [joint("root", (0, 0, 0, 1), (0, 0, 0), ""), joint("arm", (0, 0, 0, 1), (5, 0, 0), "root")]
        self.mr = model_record("thing", self.joints)

    def run_import(self, cshp_chunk):
        sr = segments_with_collision([segm(QUAD, [0, 1, 2, 3], "root")], cshp_chunk)
        m = model.decode(self.mr, sr)
        obj, _ = model.to_obj(m, 0)
        lines = obj.splitlines()
        nv = sum(1 for l in lines if l.startswith("v "))
        lines += ["o new_arm", "usemtl tex", "v -5 0 0", "v -6 0 0", "v -5 2 -1", f"f {nv + 1} {nv + 2} {nv + 3}"]
        nm, ns, rep = mi.import_obj(self.mr, sr, "\n".join(lines) + "\n")
        self.last_sr = ns
        return model.decode(nm, ns), rep

    def test_edited_piece_collision_follows_it(self):
        # A box around the quad (its own) and one far off (another part's).
        plain = model.decode(self.mr, segments_with_collision([segm(QUAD, [0, 1, 2, 3], "root")],
                                                              cshp([box_shape(7, "root", [((0, 0, 0), (1, 1, 1))])], 1)))
        pts = plain.pieces[0].positions
        lo = [min(p[a] for p in pts) for a in range(3)]
        hi = [max(p[a] for p in pts) for a in range(3)]
        own = (tuple((lo[a] + hi[a]) / 2 for a in range(3)), tuple(max((hi[a] - lo[a]) / 2, 0.01) for a in range(3)))
        far = ((40.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        sr = segments_with_collision([segm(QUAD, [0, 1, 2, 3], "root")], cshp([box_shape(7, "root", [own, far])], 2))
        m = model.decode(self.mr, sr)
        obj, _ = model.to_obj(m, 0)
        # Move the piece 3 m along the game's +x (the OBJ's -x).
        lines = [f"v {float(l.split()[1]) - 3} " + " ".join(l.split()[2:]) if l.startswith("v ") else l
                 for l in obj.splitlines()]
        nm, ns, rep = mi.import_obj(self.mr, sr, "\n".join(lines) + "\n")
        self.assertEqual((rep.edited, rep.collision_boxes_moved, rep.collision_boxes), ([0], 1, 0))
        (shape,) = model.decode(nm, ns).collision
        centres = sorted(round(b.centre[0], 2) for b in shape.boxes)
        self.assertEqual(centres, sorted([round(own[0][0] + 3, 2), 40.0]))     # far box kept, own box moved
        # An edit that keeps the outline (here none) leaves collision alone.
        nm2, ns2, rep2 = mi.import_obj(self.mr, sr, obj)
        self.assertEqual((ns2, rep2.collision_boxes_moved), (sr, 0))

    def test_appends_into_existing_box_shape(self):
        block = cshp([mesh_shape(6, "root", kind=4), box_shape(9, "root", [((0, 0, 0), (1, 1, 1))], kind=2)], 1)
        m2, rep = self.run_import(block)
        self.assertEqual(len(m2.collision), 2)            # shape count unchanged
        box_shape_ = m2.collision[1]
        self.assertEqual(len(box_shape_.boxes), 2)
        added = box_shape_.boxes[1]
        self.assertEqual(added.material, 9)
        # Built in the arm's space (x 0..1), stored in the root's space (x 5..6).
        self.assertAlmostEqual(added.centre[0], 5.5, places=3)
        self.assertEqual(rep.collision_boxes, 1)

    def test_converts_convex_hull_to_boxes(self):
        # Like the Humvee: a triangle hull mesh with a tree, and a convex hull.
        block = cshp([tree_mesh_shape(6, "root"), hull_shape(9, "root")], 0, kinds=(0, 1, 1, 1))
        m2, rep = self.run_import(block)
        self.assertEqual(len(m2.collision), 2)            # shape count unchanged
        mesh, boxes = m2.collision
        self.assertEqual((len(mesh.vertices), len(mesh.triangles)), (3, 1))   # mesh untouched
        self.assertEqual((boxes.kind, boxes.material, boxes.is_mesh), (2, 9, False))
        self.assertEqual(rep.collision_boxes, 1)
        # Hull slices stay inside the hull's bounds (unit corner, 0..1)...
        for b in boxes.boxes[:-1]:
            for c in b.corners():
                self.assertTrue(all(-1e-6 <= c[k] <= 1 + 1e-6 for k in range(3)))
        # ...and the new box is stored in the root's space: the arm piece sits at x 5..6.
        self.assertAlmostEqual(boxes.boxes[-1].centre[0], 5.5, places=3)
        cinf = list(ucfb.iter_chunks(raw_cshp(self.last_sr)))[0][1]
        shapes, kind23, trees, total, k4, k5 = struct.unpack_from("<6H", cinf)
        self.assertEqual((shapes, kind23, trees, total, k4, k5), (2, 1, 1, len(boxes.boxes), 1, 0))
        verts, tris = struct.unpack_from("<HH", cinf, 14)
        self.assertEqual((verts, tris), (3, 1))           # hull points no longer counted

    def test_hull_only_model_is_converted(self):
        m2, rep = self.run_import(cshp([hull_shape(6, "root")], 0, kinds=(0, 0, 0, 1)))
        self.assertEqual(len(m2.collision), 1)
        self.assertEqual((m2.collision[0].kind, m2.collision[0].material), (2, 6))

    def test_triangle_mesh_is_last_resort(self):
        block = cshp([tree_mesh_shape(6, "root"), tree_mesh_shape(9, "root")], 0, kinds=(0, 2, 2, 0))
        m2, rep = self.run_import(block)
        self.assertEqual(len(m2.collision), 2)
        mesh = m2.collision[0]
        self.assertEqual((len(mesh.vertices), len(mesh.triangles)), (3 + 8, 1 + 12))
        self.assertTrue(any("triangle mesh" in w for w in rep.warnings))
        # Stored in the root's space: the arm piece sits at x 5..6.
        self.assertAlmostEqual(max(v[0] for v in mesh.vertices), 6.0, places=3)
        # The rebuilt tree finds the new triangles, and cinf totals follow.
        body = raw_cshp(self.last_sr)
        tree = shape_part(body, 0, b"CMOP")
        hits = cmop.query(tree, (5.5, 1.0, 0.0), 0.1)
        self.assertTrue(hits and all(1 <= h < 13 for h in hits))
        cinf = list(ucfb.iter_chunks(body))[0][1]
        verts, tris, size = struct.unpack_from("<HHI", cinf, 14)
        self.assertEqual((verts, tris), (11 + 3, 13 + 1))
        sizes = [struct.unpack_from("<I", shape_part(body, i, b"CMOP"), 16)[0] for i in (0, 1)]
        self.assertEqual(size, sum((n + 0x30 + 15) // 16 * 16 for n in sizes))


class TreeTests(unittest.TestCase):
    def test_build_and_query(self):
        import random
        random.seed(3)
        prims = []
        for _ in range(300):
            c = [random.uniform(-20, 20) for _ in range(3)]
            h = [random.uniform(0.05, 1.5) for _ in range(3)]
            prims.append(([c[k] - h[k] for k in range(3)], [c[k] + h[k] for k in range(3)]))
        data = cmop.build(prims)
        self.assertEqual(sorted(cmop.query(data, (0, 0, 0), 100)), list(range(300)))
        for _ in range(200):
            c = [random.uniform(-22, 22) for _ in range(3)]
            r = random.uniform(0.1, 6)
            got = set(cmop.query(data, c, r))
            for i, (a, b) in enumerate(prims):
                d = sum(max(a[k] - c[k], 0, c[k] - b[k]) ** 2 for k in range(3)) ** 0.5
                if d < r:
                    self.assertIn(i, got)

    def test_walker_opcodes(self):
        def tree(stream):  # min (0,0,0); scale makes one 8-bit cell 1 m
            return struct.pack("<4fI", 0, 0, 0, 65536.0, len(stream)) + bytes(stream)
        # Split on x: left (max 10) is leaf 0; right (min 20) adds 2 to the base, then leaf 7.
        s = [0x10, 10, 20, 1, 0x30, 0x09, 2, 0x50, 7]
        self.assertEqual(cmop.query(tree(s), (5, 0, 0), 1), [0])
        self.assertEqual(cmop.query(tree(s), (30, 0, 0), 1), [9])
        self.assertEqual(sorted(cmop.query(tree(s), (15, 0, 0), 8)), [0, 9])
        # A failed bounds test ends the stream; a 16-bit jump skips bytes.
        self.assertEqual(cmop.query(tree([0x27, 50, 60, 0x31]), (0, 0, 0), 1), [])
        self.assertEqual(cmop.query(tree([0x06, 0, 1, 0xFF, 0x32]), (0, 0, 0), 1), [2])


def tree_mesh_shape(material, joint_name):
    verts = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    sinf = struct.pack("<4I", material, name_hash(joint_name), 4, 1)
    cmsh = struct.pack("<IHHH", 6, 3, 1, 0) + b"".join(struct.pack("<3f", *v) for v in verts)
    cmsh += struct.pack("<3H", 0, 1, 2)
    tree = cmop.build([([0, 0, 0], [1, 1, 0])])
    return ucfb.pack_chunk(b"shap", ucfb.pack_children([(b"sinf", sinf), (b"CMSH", cmsh), (b"CMOP", tree)]))


def hull_shape(material, joint_name):
    verts = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
    planes = [(-1, 0, 0, 0), (0, -1, 0, 0), (0, 0, -1, 0), (0.577, 0.577, 0.577, -0.577)]
    sinf = struct.pack("<4I", material, name_hash(joint_name), 5, 1)
    cmsh = struct.pack("<IHHH", 9, 4, 0, 4) + b"".join(struct.pack("<3f", *v) for v in verts)
    cmsh += b"".join(struct.pack("<4f", *pl) for pl in planes)
    return ucfb.pack_chunk(b"shap", ucfb.pack_children([(b"sinf", sinf), (b"CMSH", cmsh)]))


def raw_cshp(segments_record):
    cseg = ucfb.unwrap(segments_record, b"CSEG")
    size, _ = struct.unpack_from("<II", cseg)
    root = list(ucfb.iter_chunks(script.decompress(cseg[8:], size)))[0][1]
    return next(b for t, b in ucfb.iter_chunks(root) if t == b"cshp")


def shape_part(body, index, tag):
    shapes = [b for t, b in ucfb.iter_chunks(body) if t == b"shap"]
    return dict(ucfb.iter_chunks(shapes[index]))[tag]
