import struct
import unittest

from mercfoundry import collision, model, model_import as mi, ucfb
from mercfoundry.hashing import name_hash
from tests.test_model import QUAD, joint, model_record, segm, segments_record


def box_shape(material, joint_name, boxes, kind=0):
    sinf = struct.pack("<4I", material, name_hash(joint_name), kind | len(boxes) << 16, 0)
    cbox = b"".join(collision.pack_box(material, c, h) for c, h in boxes)
    return ucfb.pack_chunk(b"shap", ucfb.pack_children([(b"sinf", sinf), (b"CBOX", cbox)]))


def cshp(shapes, total_boxes):
    cinf = struct.pack("<7H", len(shapes), 0, 0, total_boxes, 0, 0, 0)
    return ucfb.pack_chunk(b"cshp", ucfb.pack_chunk(b"cinf", cinf) + b"".join(shapes))


class CollisionTests(unittest.TestCase):
    def test_parse_and_corners(self):
        block = cshp([box_shape(7, "root", [((0, 1, 0), (1, 2, 3))])], 1)
        (shape,) = collision.parse(list(ucfb.iter_chunks(block))[0][1])
        self.assertEqual((shape.material, shape.joint, shape.kind, shape.is_mesh), (7, name_hash("root"), 0, False))
        corners = shape.boxes[0].corners()
        self.assertEqual(min(c[1] for c in corners), -1)
        self.assertEqual(max(c[2] for c in corners), 3)

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
        shapes, _, _, boxes = struct.unpack_from("<4H", items[0][1])
        self.assertEqual((shapes, boxes), (2, 3))
        added = collision.parse(body)[1]
        self.assertEqual((added.material, added.joint, len(added.boxes)), (9, name_hash("arm"), 2))

    def test_new_block(self):
        body = collision.new_block(7, name_hash("root"), [((0, 0, 0), (1, 1, 1))])
        self.assertEqual(struct.unpack_from("<4H", list(ucfb.iter_chunks(body))[0][1]), (1, 0, 0, 1))
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
            lines += [f"v {x} {y} {-z}" for x, y, z in ((5, 0, 0), (6, 0, 0), (5, 2, 1))]
            lines.append(f"f {nv + 1} {nv + 3} {nv + 2}")
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
