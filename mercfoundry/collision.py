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
        CMSH u32 flags, u16 vertices, u16 triangles, u16, float[3] per
             vertex, u16[3] per triangle (another layout exists; not decoded)
        CMOP optional precomputed tree (OPCODE-style); not decoded

Kinds seen: boxes 0 and 2 (plain), 3 (with CMOP); meshes 4 (with CMOP), 5.
Established from retail data; see docs/formats/dsk.md.
"""
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
            _, nv, nt = struct.unpack_from("<IHH", data)
            if 10 + nv * 12 + nt * 6 == len(data):
                shape.vertices = [struct.unpack_from("<3f", data, 10 + i * 12) for i in range(nv)]
                o = 10 + nv * 12
                shape.triangles = [struct.unpack_from("<3H", data, o + i * 6) for i in range(nt)]
        shapes.append(shape)
    return shapes


def box_triangles(box):
    c = box.corners()
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    tris = []
    for a, b, cc, d in quads:
        tris += [(c[a], c[b], c[cc]), (c[a], c[cc], c[d])]
    return tris


def pack_box(material, centre, half):
    return struct.pack("<I15f", material, *half, 1, 0, 0, 0, 1, 0, 0, 0, 1, *centre)


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
