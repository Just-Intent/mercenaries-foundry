"""Models: a "Model" record (modl) plus a "segments" record with the same key.

modl (uncompressed):
    NAME   model name
    NODE   root node name
    INFO   u16 version (9), u16 piece count, u16 0, u16 flags (0x100),
           float[6] quantization box (min xyz, max xyz),
           float[6] bounds (min xyz, max xyz),
           u32 joint count, float[4] level-of-detail distances, float, u8
    SKEL   NBNS (u32 joint count), FSBI, JONT per joint:
           name\\0, quaternion (x y z w), translation (x y z), parent\\0

segments: CSEG = u32 size, u32 compressed size, compressed ucfb tree (same
scheme as scripts) of segm chunks, plus optional cshp (collision, not decoded):
    INFO   u32 vertex count, u32 index count, u32 stride (low 16 bits; bit 16 =
           vertices carry a bone index), u32 level-of-detail bitmask
    MTRL   u32 shader?, u32 joint name hash, u32 joint/state hash?, u32 texture
           name hash, then 3 words not understood
    VBUF   per vertex: s16 x3 position (relative to the quantization box),
           u16 bone index (0 unless flagged), u32 normal (11:11:10 signed),
           u8 x4 colour (BGRA), s16 x2 texture coordinates (/2048);
           28-byte vertices carry 8 more bytes, not understood
    IBUF   u16 triangle strip; repeated indices join strips

Each piece's positions are local to its joint; joints chain to the root.
Established from retail data: see docs/formats/dsk.md.
"""
import math
import struct
from dataclasses import dataclass, field

from . import script, ucfb
from .hashing import name_hash

MODEL = name_hash("Model")
SEGMENTS = name_hash("segments")


@dataclass
class Joint:
    name: str
    rotation: tuple   # quaternion x, y, z, w
    translation: tuple
    parent: str


@dataclass
class Piece:
    lod_mask: int
    joint: int          # joint name hash
    texture: int        # texture name hash
    material: tuple     # all MTRL words
    positions: list     # in model space (joint transforms applied)
    normals: list
    uvs: list
    colors: list        # (r, g, b, a) bytes
    bones: list         # per-vertex bone index, or None
    triangles: list     # (a, b, c) vertex indices


@dataclass
class Model:
    name: str
    node: str
    bounds: tuple
    lod_distances: tuple
    joints: list
    pieces: list = field(default_factory=list)

    def lods(self):
        """Level numbers used by any piece (0 = most detailed)."""
        used = 0
        for p in self.pieces:
            used |= p.lod_mask
        return [i for i in range(32) if used >> i & 1]

    def at_lod(self, lod, damaged=False):
        """Pieces drawn at a level of detail. Vehicles carry pristine and
        damaged copies of body panels (joints named segment_pristine_* and
        segment_damage_*); only one state is returned."""
        names = {name_hash(j.name): j.name.lower() for j in self.joints}
        hide = "segment_pristine" if damaged else "segment_damage"
        out = []
        for p in self.pieces:
            if not p.lod_mask >> lod & 1:
                continue
            if names.get(p.joint, "").startswith(hide):
                continue
            out.append(p)
        return out


def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def _qrot(q, v):
    x, y, z, w = q
    vx, vy, vz = v
    tx, ty, tz = 2 * (y * vz - z * vy), 2 * (z * vx - x * vz), 2 * (x * vy - y * vx)
    return (vx + w * tx + (y * tz - z * ty),
            vy + w * ty + (z * tx - x * tz),
            vz + w * tz + (x * ty - y * tx))


def _signed(v, bits):
    return v - (1 << bits) if v >= 1 << (bits - 1) else v


def unpack_normal(word):
    return (_signed(word & 0x7FF, 11) / 1023,
            _signed(word >> 11 & 0x7FF, 11) / 1023,
            _signed(word >> 22 & 0x3FF, 10) / 511)


def strip_to_triangles(indices):
    tris = []
    for i in range(2, len(indices)):
        a, b, c = indices[i - 2], indices[i - 1], indices[i]
        if a == b or b == c or a == c:
            continue
        tris.append((b, a, c) if i % 2 else (a, b, c))
    return tris


def read_joints(skel: bytes) -> list:
    out = []
    for tag, body in ucfb.iter_chunks(skel):
        if tag != b"JONT":
            continue
        end = body.index(b"\0")
        name = body[:end].decode("latin-1")
        rot = struct.unpack_from("<4f", body, end + 1)
        pos = struct.unpack_from("<3f", body, end + 17)
        parent = body[end + 29:].split(b"\0", 1)[0].decode("latin-1")
        out.append(Joint(name, rot, pos, parent))
    return out


def joint_transforms(joints) -> dict:
    """Model-space (rotation, translation) for each joint, keyed by name hash."""
    by_name = {j.name.lower(): j for j in joints}
    done = {}

    def resolve(name, depth=0):
        key = name.lower()
        if key in done:
            return done[key]
        j = by_name[key]
        rot, pos = j.rotation, j.translation
        parent = j.parent.lower()
        if parent and parent in by_name and parent != key and depth < 64:
            prot, ppos = resolve(parent, depth + 1)
            pos = tuple(a + b for a, b in zip(_qrot(prot, pos), ppos))
            rot = _qmul(prot, rot)
        done[key] = (rot, pos)
        return done[key]

    return {name_hash(j.name): resolve(j.name) for j in joints}


def decode(model_record: bytes, segments_record: bytes) -> Model:
    fields = dict(ucfb.iter_chunks(ucfb.unwrap(model_record, b"modl")))
    info = fields[b"INFO"]
    box = struct.unpack_from("<12f", info, 8)
    qmin, qmax = box[0:3], box[3:6]
    lods = struct.unpack_from("<4f", info, 60) if len(info) >= 76 else ()
    joints = read_joints(fields[b"SKEL"]) if b"SKEL" in fields else []
    model = Model(
        name=fields[b"NAME"].split(b"\0", 1)[0].decode("latin-1"),
        node=fields.get(b"NODE", b"").split(b"\0", 1)[0].decode("latin-1"),
        bounds=(box[6:9], box[9:12]),
        lod_distances=lods,
        joints=joints,
    )
    xforms = joint_transforms(joints)
    centre = [(qmin[k] + qmax[k]) / 2 for k in range(3)]
    half = [(qmax[k] - qmin[k]) / 2 for k in range(3)]

    cseg = ucfb.unwrap(segments_record, b"CSEG")
    size, _ = struct.unpack_from("<II", cseg)
    tree = script.decompress(cseg[8:], size)
    roots = list(ucfb.iter_chunks(tree))
    if not roots or roots[0][0] != b"ucfb":
        raise ValueError("segments record does not hold a ucfb tree")
    for tag, body in ucfb.iter_chunks(roots[0][1]):
        if tag != b"segm":
            continue
        seg = dict(ucfb.iter_chunks(body))
        nv, ni, stride_word, lod_mask = struct.unpack_from("<4I", seg[b"INFO"])
        stride = stride_word & 0xFFFF
        skinned = bool(stride_word & 0x10000)
        mtrl = struct.unpack(f"<{len(seg[b'MTRL']) // 4}I", seg[b"MTRL"])
        rot, pos = xforms.get(mtrl[1], ((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0)))
        vb = seg[b"VBUF"]
        if len(vb) < nv * stride or stride < 20:
            raise ValueError(f"{model.name}: vertex buffer too short")
        piece = Piece(lod_mask, mtrl[1], mtrl[3] if len(mtrl) > 3 else 0, mtrl,
                      [], [], [], [], [] if skinned else None, [])
        for i in range(nv):
            o = i * stride
            qx, qy, qz, bone, nrm = struct.unpack_from("<3hHI", vb, o)
            local = (centre[0] + half[0] * qx / 32767,
                     centre[1] + half[1] * qy / 32767,
                     centre[2] + half[2] * qz / 32767)
            piece.positions.append(tuple(a + b for a, b in zip(_qrot(rot, local), pos)))
            piece.normals.append(_qrot(rot, unpack_normal(nrm)))
            b, g, r, a = vb[o + 12:o + 16]
            piece.colors.append((r, g, b, a))
            u, v = struct.unpack_from("<2h", vb, o + 16)
            piece.uvs.append((u / 2048, v / 2048))
            if skinned:
                piece.bones.append(bone)
        indices = struct.unpack(f"<{ni}H", seg[b"IBUF"][:ni * 2])
        if indices and max(indices) >= nv:
            raise ValueError(f"{model.name}: index out of range")
        piece.triangles = strip_to_triangles(indices)
        model.pieces.append(piece)
    return model


def find(archives, name):
    """Look up a model by name (or 8-digit key) across archives.
    Returns (model_record, segments_record)."""
    key = int(name, 16) if len(name) == 8 and all(c in "0123456789abcdefABCDEF" for c in name) else name_hash(name)
    found = {}
    for arc in archives:
        with arc.path.open("rb") as f:
            for r in arc.records:
                if r.key == key and r.kind in (MODEL, SEGMENTS) and r.kind not in found:
                    f.seek(r.offset)
                    found[r.kind] = f.read(r.size)
    if MODEL not in found or SEGMENTS not in found:
        raise KeyError(f"model not found: {name}")
    return found[MODEL], found[SEGMENTS]


def to_obj(model: Model, lod=0, texture_name=None, damaged=False) -> tuple:
    """Return (obj text, mtl text). Z is negated for OBJ's right-handed axes."""
    texture_name = texture_name or (lambda h: f"{h:08X}")
    obj = [f"# {model.name}, level of detail {lod}", f"mtllib {model.name}.mtl"]
    mtl, seen = [], set()
    base = 1
    for n, p in enumerate(model.at_lod(lod, damaged)):
        tex = texture_name(p.texture)
        if tex not in seen:
            seen.add(tex)
            mtl += [f"newmtl {tex}", "Kd 1 1 1", f"map_Kd {tex}.png", ""]
        obj.append(f"o piece{n:03d}")
        obj.append(f"usemtl {tex}")
        obj += [f"v {x:.5f} {y:.5f} {-z:.5f}" for x, y, z in p.positions]
        obj += [f"vt {u:.5f} {1 - v:.5f}" for u, v in p.uvs]
        obj += [f"vn {x:.4f} {y:.4f} {-z:.4f}" for x, y, z in p.normals]
        for a, b, c in p.triangles:
            a, b, c = a + base, c + base, b + base  # z flip reverses winding
            obj.append(f"f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}")
        base += len(p.positions)
    return "\n".join(obj) + "\n", "\n".join(mtl)


def preview_png(model: Model, lod=0, size=512, yaw=35.0, pitch=-25.0, damaged=False) -> bytes:
    """Flat-shaded preview image, no dependencies."""
    import zlib
    tris = []
    for p in model.at_lod(lod, damaged):
        for t in p.triangles:
            tris.append([p.positions[i] for i in t])
    cy, sy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    cp, sp = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))

    def view(v):
        x, y, z = v
        x, z = x * cy - z * sy, x * sy + z * cy
        y, z = y * cp - z * sp, y * sp + z * cp
        return x, y, z

    tris = [[view(v) for v in t] for t in tris]
    pts = [v for t in tris for v in t] or [(0, 0, 0)]
    xs, ys = [v[0] for v in pts], [v[1] for v in pts]
    span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1
    scale = size * 0.9 / span
    ox, oy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    depth = [[float("inf")] * size for _ in range(size)]
    img = [[(40, 44, 52)] * size for _ in range(size)]
    light = (0.4, 0.8, -0.45)
    for t in tris:
        (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = t
        ux, uy, uz = x1 - x0, y1 - y0, z1 - z0
        vx, vy, vz = x2 - x0, y2 - y0, z2 - z0
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1
        shade = abs(nx * light[0] + ny * light[1] + nz * light[2]) / ln
        c = int(60 + 180 * shade)
        colour = (c, int(c * 0.95), int(c * 0.85))
        sx = [(x - ox) * scale + size / 2 for x in (x0, x1, x2)]
        syy = [size / 2 - (y - oy) * scale for y in (y0, y1, y2)]
        zs = (z0, z1, z2)
        area = (sx[1] - sx[0]) * (syy[2] - syy[0]) - (sx[2] - sx[0]) * (syy[1] - syy[0])
        if abs(area) < 1e-9:
            continue
        for py in range(max(0, int(min(syy))), min(size, int(max(syy)) + 1)):
            for px in range(max(0, int(min(sx))), min(size, int(max(sx)) + 1)):
                fx, fy = px + 0.5, py + 0.5
                w0 = ((sx[1] - fx) * (syy[2] - fy) - (sx[2] - fx) * (syy[1] - fy)) / area
                w1 = ((sx[2] - fx) * (syy[0] - fy) - (sx[0] - fx) * (syy[2] - fy)) / area
                w2 = 1 - w0 - w1
                if w0 < 0 or w1 < 0 or w2 < 0:
                    continue
                z = w0 * zs[0] + w1 * zs[1] + w2 * zs[2]
                if z < depth[py][px]:
                    depth[py][px] = z
                    img[py][px] = colour
    raw = b"".join(b"\0" + bytes(c for px in row for c in px) for row in img)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
