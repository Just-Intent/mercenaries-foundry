"""Model collision: cshp chunks inside a segments record's ucfb tree.

    cshp
      cinf   u16 shapes, u16 box shapes of kind 2 or 3, u16 shapes with a
             CMOP tree, u16 total boxes, then mesh counts (u16 per mesh kind,
             u16 0), and when trees or meshes exist: [u16 mesh vertices,
             u16 mesh triangles,] u32 tree size
      shap   one per shape:
        sinf u32 material, u32 joint name hash, u32 kind | count << 16,
             u32 1 for meshes, 0 for boxes
        CBOX count x 64 bytes: u32 material, float[3] half size,
             float[9] rotation (rows), float[3] centre, in joint space
        CMSH u32 flags, u16 vertices, u16 triangles, u16 planes,
             float[3] per vertex, then u16[3] per triangle (triangle
             meshes) or float[4] per plane (convex hulls: normal, offset)
        CMOP optional bounding-volume tree over the boxes or triangles
             (see cmop.py)

Kinds seen: boxes 0 and 2 (plain), 3 (with CMOP); triangle meshes 4 (with
CMOP); convex hulls 5.

cinf after its 14 fixed bytes: u16 mesh vertices + u16 mesh triangles
(summed over all CMSH) when the block has any mesh, then u32 tree memory
(sum over CMOP of their stream size + 0x30, rounded up to 16) when it has
any tree. Checked on all 1,997 retail blocks.
Established from retail data; see docs/formats/dsk.md.
"""
import math
import struct
from dataclasses import dataclass, field

from . import ucfb

BOX_SIZE = 64


@dataclass
class Box:
    material: int
    half: tuple
    rotation: tuple     # 9 floats, rows
    centre: tuple

    def corners(self):
        r = self.rotation
        rows = (r[0:3], r[3:6], r[6:9])
        out = []
        for sx in (-1, 1):
            for sy in (-1, 1):
                for sz in (-1, 1):
                    local = (sx * self.half[0], sy * self.half[1], sz * self.half[2])
                    # Rows are the box axes in joint space.
                    p = [self.centre[k] + sum(local[a] * rows[a][k] for a in range(3)) for k in range(3)]
                    out.append(tuple(p))
        return out


@dataclass
class Shape:
    material: int
    joint: int
    kind: int
    is_mesh: bool
    boxes: list = field(default_factory=list)
    vertices: list = field(default_factory=list)   # mesh, joint space
    triangles: list = field(default_factory=list)
    planes: list = field(default_factory=list)     # convex hull: (nx, ny, nz, d)
    has_tree: bool = False


def parse(cshp: bytes) -> list:
    shapes = []
    for tag, body in ucfb.iter_chunks(cshp):
        if tag != b"shap":
            continue
        parts = dict(ucfb.iter_chunks(body))
        material, joint, kw, mesh_flag = struct.unpack("<4I", parts[b"sinf"][:16])
        shape = Shape(material, joint, kw & 0xFFFF, bool(mesh_flag), has_tree=b"CMOP" in parts)
        data = parts.get(b"CBOX")
        if data:
            for i in range(len(data) // BOX_SIZE):
                f = struct.unpack_from("<I15f", data, i * BOX_SIZE)
                shape.boxes.append(Box(f[0], f[1:4], f[4:13], f[13:16]))
        data = parts.get(b"CMSH")
        if data and len(data) >= 10:
            _, nv, nt, npl = struct.unpack_from("<IHHH", data)
            shape.vertices = [struct.unpack_from("<3f", data, 10 + i * 12) for i in range(nv)]
            o = 10 + nv * 12
            if o + nt * 6 + npl * 16 == len(data):
                shape.triangles = [struct.unpack_from("<3H", data, o + i * 6) for i in range(nt)]
                o += nt * 6
                shape.planes = [struct.unpack_from("<4f", data, o + i * 16) for i in range(npl)]
        shapes.append(shape)
    return shapes


def set_shape_flags(cshp_body: bytes, flags: dict) -> bytes:
    """A cshp with some shapes' flags changed ({shape index: flags}): the sinf
    word and every box's copy of it (retail boxes always repeat the shape's
    value). Other bytes are kept."""
    items, pad_last = ucfb.split(cshp_body)
    out, index = [], 0
    for tag, body in items:
        if tag == b"shap":
            if index in flags:
                parts, shap_pad = ucfb.split(body)
                new_parts = []
                for t, b in parts:
                    if t == b"sinf":
                        b = struct.pack("<I", flags[index]) + b[4:]
                    elif t == b"CBOX":
                        b = bytearray(b)
                        for i in range(len(b) // BOX_SIZE):
                            struct.pack_into("<I", b, i * BOX_SIZE, flags[index])
                        b = bytes(b)
                    new_parts.append((t, b))
                body = ucfb.pack_children(new_parts, shap_pad)
            index += 1
        out.append((tag, body))
    missing = [i for i in flags if i >= index]
    if missing:
        raise ValueError(f"the model has {index} collision shapes; no shape {missing[0]}")
    return ucfb.pack_children(out, pad_last)


def set_segments_flags(segments_record: bytes, flags: dict) -> bytes:
    """A segments record with collision shape flags changed (see set_shape_flags)."""
    from . import script
    cseg, cseg_pad = ucfb.unwrap(segments_record, b"CSEG", with_pad=True)
    size, _ = struct.unpack_from("<II", cseg)
    tree = script.decompress(cseg[8:], size)
    roots, tree_pad = ucfb.split(tree)
    items, pad_last = ucfb.split(roots[0][1])
    if not any(t == b"cshp" for t, _ in items):
        raise ValueError("the model has no collision")
    items = [(t, set_shape_flags(b, flags) if t == b"cshp" else b) for t, b in items]
    new_tree = ucfb.pack_children([(b"ucfb", ucfb.pack_children(items, pad_last))], tree_pad)
    if new_tree == tree:
        return segments_record
    packed = script.compress(new_tree)
    return ucfb.wrap(b"CSEG", struct.pack("<II", len(new_tree), len(packed)) + packed, cseg_pad)


def box_triangles(box):
    c = box.corners()
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    tris = []
    for a, b, cc, d in quads:
        tris += [(c[a], c[b], c[cc]), (c[a], c[cc], c[d])]
    return tris


IDENTITY = (1, 0, 0, 0, 1, 0, 0, 0, 1)


def pack_box(material, centre, half, rows=IDENTITY):
    return struct.pack("<I15f", material, *half, *rows, *centre)


def shape_count(cshp_body: bytes) -> int:
    return sum(1 for tag, _ in ucfb.iter_chunks(cshp_body) if tag == b"shap")


def _bounds(points):
    return ([min(p[a] for p in points) for a in range(3)], [max(p[a] for p in points) for a in range(3)])


def plain_box_shape(cshp_body: bytes, with_tree: bool = False):
    """First box shape: (index among shapes, joint, material), or None.
    Plain shapes (no CMOP tree) come first; with_tree also accepts box
    shapes with a tree, which append_boxes rebuilds."""
    found = []
    index = 0
    for tag, body in ucfb.iter_chunks(cshp_body):
        if tag != b"shap":
            continue
        parts = dict(ucfb.iter_chunks(body))
        material, joint, kw, _ = struct.unpack("<4I", parts[b"sinf"][:16])
        if b"CBOX" in parts and (kw & 0xFFFF) in (0, 2, 3):
            found.append((b"CMOP" in parts, index, joint, material))
        index += 1
    for tree, index, joint, material in sorted(found):
        if with_tree or not tree:
            return index, joint, material
    return None


def append_boxes(cshp_body: bytes, shape_index: int, boxes) -> bytes:
    """Add boxes to an existing plain box shape, keeping the shape count.

    boxes: [(centre, half, rows)] in that shape's joint space; they take the
    shape's material."""
    return rewrite_box_shape(cshp_body, shape_index, (), boxes)


def rewrite_box_shape(cshp_body: bytes, shape_index: int, drop=(), boxes=()) -> bytes:
    """A box shape with the boxes at the indices in drop taken out and boxes
    ([(centre, half, rows)] in its joint space, taking its material) added;
    its counts and search tree follow."""
    drop = set(drop)
    items, pad_last = ucfb.split(cshp_body)
    if not items or items[0][0] != b"cinf":
        raise ValueError("collision block has no cinf header")
    cinf = bytearray(items[0][1])
    total, = struct.unpack_from("<H", cinf, 6)
    if total - len(drop) + len(boxes) > 0xFFFF:
        raise ValueError("too many collision boxes")
    struct.pack_into("<H", cinf, 6, total - len(drop) + len(boxes))
    items[0] = (b"cinf", bytes(cinf))
    seen = -1
    for k, (tag, body) in enumerate(items):
        if tag != b"shap":
            continue
        seen += 1
        if seen != shape_index:
            continue
        parts, part_pad = ucfb.split(body)
        d = dict(parts)
        material, joint, kw, flag = struct.unpack("<4I", d[b"sinf"][:16])
        if b"CBOX" not in d:
            raise ValueError("can only add boxes to a box shape")
        count = kw >> 16
        old = d[b"CBOX"]
        kept = b"".join(old[i * BOX_SIZE:(i + 1) * BOX_SIZE] for i in range(len(old) // BOX_SIZE) if i not in drop)
        count = len(kept) // BOX_SIZE + len(boxes)
        sinf = struct.pack("<4I", material, joint, (kw & 0xFFFF) | count << 16, flag) + d[b"sinf"][16:]
        cbox = kept + b"".join(pack_box(material, c, h, r) for c, h, r in boxes)
        tree = None
        if b"CMOP" in d:
            from . import cmop
            all_boxes = parse(ucfb.pack_chunk(b"shap", ucfb.pack_children(
                [(t, sinf if t == b"sinf" else cbox if t == b"CBOX" else b) for t, b in parts])))[0].boxes
            tree = cmop.build([_bounds(b.corners()) for b in all_boxes])
        parts = [(t, sinf if t == b"sinf" else cbox if t == b"CBOX" else tree if t == b"CMOP" else b)
                 for t, b in parts]
        items[k] = (b"shap", ucfb.pack_children(parts, part_pad))
        items[0] = (b"cinf", _cinf_tail(items[0][1], items))
        return ucfb.pack_children(items, pad_last)
    raise ValueError(f"no shape {shape_index}")


# Generated box shapes use kind 2: the kind drivable vehicles use for boxes
# (kind 0 appears only on static objects such as train cars), and common on
# static props too.
GENERATED_KIND = 2


def add_box_shape(cshp_body: bytes, material: int, joint: int, boxes, kind: int = GENERATED_KIND) -> bytes:
    """Append a box shape without a tree and update cinf counts.

    boxes: [(centre, half)] in joint space."""
    items, pad_last = ucfb.split(cshp_body)
    if not items or items[0][0] != b"cinf":
        raise ValueError("collision block has no cinf header")
    cinf = bytearray(items[0][1])
    shapes, kind23 = struct.unpack_from("<2H", cinf, 0)
    total, = struct.unpack_from("<H", cinf, 6)
    if total + len(boxes) > 0xFFFF:
        raise ValueError("too many collision boxes")
    struct.pack_into("<H", cinf, 0, shapes + 1)
    if kind in (2, 3):
        struct.pack_into("<H", cinf, 2, kind23 + 1)
    struct.pack_into("<H", cinf, 6, total + len(boxes))
    sinf = struct.pack("<4I", material, joint, kind | len(boxes) << 16, 0)
    cbox = b"".join(pack_box(material, c, h) for c, h in boxes)
    shap = ucfb.pack_children([(b"sinf", sinf), (b"CBOX", cbox)])
    items[0] = (b"cinf", bytes(cinf))
    items.append((b"shap", shap))
    return ucfb.pack_children(items, pad_last)


def new_block(material: int, joint: int, boxes, kind: int = GENERATED_KIND) -> bytes:
    """A collision block for a model that has none."""
    cinf = struct.pack("<3H4H", 0, 0, 0, 0, 0, 0, 0)
    return add_box_shape(ucfb.pack_children([(b"cinf", cinf)]), material, joint, boxes, kind)


def _cinf_tail(cinf: bytes, items) -> bytes:
    """Recompute cinf's optional mesh totals and tree size from the shapes."""
    verts = tris = tree = 0
    has_mesh = False
    for tag, body in items:
        if tag != b"shap":
            continue
        parts = dict(ucfb.iter_chunks(body))
        if b"CMSH" in parts:
            has_mesh = True
            _, nv, nt = struct.unpack_from("<IHH", parts[b"CMSH"])
            verts += nv
            tris += nt
        if b"CMOP" in parts:
            n, = struct.unpack_from("<I", parts[b"CMOP"], 16)
            tree += (n + 0x30 + 15) // 16 * 16
    out = cinf[:14]
    if has_mesh:
        out += struct.pack("<HH", verts, tris)
    if tree:
        out += struct.pack("<I", tree)
    return out


def triangle_mesh_shape(cshp_body: bytes):
    """First triangle-mesh shape: (index among shapes, joint, material), or
    None. Triangles can be added to it, with its tree rebuilt."""
    index = 0
    for tag, body in ucfb.iter_chunks(cshp_body):
        if tag != b"shap":
            continue
        shape = parse(ucfb.pack_chunk(b"shap", body))[0]
        if shape.is_mesh and shape.triangles and not shape.planes:
            return index, shape.joint, shape.material
        index += 1
    return None


def box_mesh(centre, half, rows=IDENTITY):
    """8 corners and 12 triangles, wound like retail hulls (the cross product
    of (b - a, c - a) points into the box)."""
    r = (rows[0:3], rows[3:6], rows[6:9])
    corners = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                local = (sx * half[0], sy * half[1], sz * half[2])
                corners.append(tuple(centre[k] + sum(local[a] * r[a][k] for a in range(3)) for k in range(3)))
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    tris = []
    for a, b, c, d in quads:
        tris += [(a, b, c), (a, c, d)]
    # Orient every triangle so its cross product points inward.
    mid = centre
    out = []
    for a, b, c in tris:
        pa, pb, pc = corners[a], corners[b], corners[c]
        u = [pb[k] - pa[k] for k in range(3)]
        v = [pc[k] - pa[k] for k in range(3)]
        n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        t = [(pa[k] + pb[k] + pc[k]) / 3 - mid[k] for k in range(3)]
        out.append((a, b, c) if sum(x * y for x, y in zip(n, t)) < 0 else (a, c, b))
    return corners, out


def append_mesh_boxes(cshp_body: bytes, shape_index: int, boxes) -> bytes:
    """Add boxes to a triangle-mesh shape as closed boxes of triangles and
    rebuild its tree. boxes: [(centre, half, rows)] in that shape's joint
    space. Keeps the shape count, so it suits vehicles that already have
    two shapes."""
    from . import cmop
    items, pad_last = ucfb.split(cshp_body)
    if not items or items[0][0] != b"cinf":
        raise ValueError("collision block has no cinf header")
    seen = -1
    for k, (tag, body) in enumerate(items):
        if tag != b"shap":
            continue
        seen += 1
        if seen != shape_index:
            continue
        parts, part_pad = ucfb.split(body)
        d = dict(parts)
        shape = parse(ucfb.pack_chunk(b"shap", body))[0]
        if not (shape.is_mesh and shape.triangles and not shape.planes):
            raise ValueError("can only add triangles to a triangle-mesh shape")
        flags, = struct.unpack_from("<I", d[b"CMSH"])
        verts, tris = list(shape.vertices), list(shape.triangles)
        for centre, half, rows in boxes:
            corners, faces = box_mesh(centre, half, rows)
            base = len(verts)
            verts += corners
            tris += [(a + base, b + base, c + base) for a, b, c in faces]
        if len(verts) > 0xFFFF or len(tris) > 0xFFFF:
            raise ValueError("collision mesh too large")
        cmsh = struct.pack("<IHHH", flags, len(verts), len(tris), 0)
        cmsh += b"".join(struct.pack("<3f", *v) for v in verts)
        cmsh += b"".join(struct.pack("<3H", *t) for t in tris)
        tree = cmop.build([_bounds([verts[i] for i in t]) for t in tris]) if b"CMOP" in d else None
        if b"CMOP" not in d:
            tree = None  # keep the shape's kind: no tree before, none after
        new_parts = [(t, cmsh if t == b"CMSH" else tree if t == b"CMOP" else b) for t, b in parts]
        items[k] = (b"shap", ucfb.pack_children(new_parts, part_pad))
        items[0] = (b"cinf", _cinf_tail(items[0][1], items))
        return ucfb.pack_children(items, pad_last)
    raise ValueError(f"no shape {shape_index}")


def hull_boxes(vertices, planes, slice_length=0.5):
    """Boxes approximating a convex hull: slices along its longest axis, each
    as wide as the hull's cross-section at the slice's middle. Planes are
    (n, d) with n . p + d <= 0 inside."""
    lo = [min(v[k] for v in vertices) for k in range(3)]
    hi = [max(v[k] for v in vertices) for k in range(3)]
    axis = max(range(3), key=lambda k: hi[k] - lo[k])
    u, w = [k for k in range(3) if k != axis]
    n = max(1, math.ceil((hi[axis] - lo[axis]) / slice_length))
    step = (hi[axis] - lo[axis]) / n
    boxes = []
    for i in range(n):
        a0 = lo[axis] + i * step
        mid = a0 + step / 2
        # Cross-section: n_u*x + n_w*y <= -(d + n_axis*mid); corners are
        # feasible intersections of two constraint lines.
        lines = [(p[u], p[w], -(p[3] + p[axis] * mid)) for p in planes]
        pts = []
        for j in range(len(lines)):
            for k in range(j + 1, len(lines)):
                a1, b1, c1 = lines[j]
                a2, b2, c2 = lines[k]
                det = a1 * b2 - a2 * b1
                if abs(det) < 1e-9:
                    continue
                x = (c1 * b2 - c2 * b1) / det
                y = (a1 * c2 - a2 * c1) / det
                if all(a * x + b * y <= c + 1e-5 for a, b, c in lines):
                    pts.append((x, y))
        if not pts:
            continue
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        centre, half = [0.0] * 3, [0.0] * 3
        centre[axis], half[axis] = mid, step / 2
        centre[u], half[u] = (min(xs) + max(xs)) / 2, (max(xs) - min(xs)) / 2
        centre[w], half[w] = (min(ys) + max(ys)) / 2, (max(ys) - min(ys)) / 2
        boxes.append((tuple(centre), tuple(half), IDENTITY))
    return boxes


def convex_hull_shape(cshp_body: bytes):
    """First convex-hull shape: (index among shapes, joint, material), or None."""
    for index, shape in enumerate(parse(cshp_body)):
        if shape.planes:
            return index, shape.joint, shape.material
    return None


def hull_to_box_shape(cshp_body: bytes, shape_index: int, extra_boxes=()) -> bytes:
    """Replace a convex hull with a plain box shape (kind 2, same material
    and joint) of boxes approximating it, plus extra_boxes [(centre, half,
    rows)] in that joint's space. Keeps the shape count: the layout tanks
    and helicopters use (triangle hull mesh + box shape)."""
    items, pad_last = ucfb.split(cshp_body)
    if not items or items[0][0] != b"cinf":
        raise ValueError("collision block has no cinf header")
    shapes = parse(cshp_body)
    shape = shapes[shape_index]
    if not shape.planes:
        raise ValueError("not a convex hull shape")
    boxes = hull_boxes(shape.vertices, shape.planes) + list(extra_boxes)
    sinf = struct.pack("<4I", shape.material, shape.joint, GENERATED_KIND | len(boxes) << 16, 0)
    cbox = b"".join(pack_box(shape.material, c, h, r) for c, h, r in boxes)
    seen = -1
    for k, (tag, body) in enumerate(items):
        if tag != b"shap":
            continue
        seen += 1
        if seen == shape_index:
            items[k] = (b"shap", ucfb.pack_children([(b"sinf", sinf), (b"CBOX", cbox)]))
    cinf = bytearray(items[0][1])
    kind23, = struct.unpack_from("<H", cinf, 2)
    total, = struct.unpack_from("<H", cinf, 6)
    hulls, = struct.unpack_from("<H", cinf, 10)
    struct.pack_into("<H", cinf, 2, kind23 + 1)
    struct.pack_into("<H", cinf, 6, total + len(boxes))
    struct.pack_into("<H", cinf, 10, max(hulls - 1, 0))
    items[0] = (b"cinf", bytes(cinf))
    items[0] = (b"cinf", _cinf_tail(items[0][1], items))
    return ucfb.pack_children(items, pad_last)
