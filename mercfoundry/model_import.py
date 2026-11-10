"""Bring an edited OBJ (or glTF, via gltf_model) back into a model's records.

The OBJ is matched to the model by object name: objects named p<index>...
(as written by `model`) replace that piece's geometry and keep its joint,
material, level-of-detail mask and vertex format. Other objects become new
pieces. Pieces that were exported (same level of detail and damage state)
but are missing from the OBJ are removed. Everything else (skeleton, other
levels of detail, collision, untouched pieces) is kept byte-for-byte unless
the quantization box has to grow.

Importing an unedited export returns the original records unchanged.
"""
import math
import re
import struct
from dataclasses import dataclass, field

from . import model as mdl
from . import script, ucfb
from .hashing import name_hash

MAX_INDEX = 0xFFFF


@dataclass
class ObjPiece:
    name: str
    material: str = ""
    positions: list = field(default_factory=list)   # model space (z flipped back)
    uvs: list = field(default_factory=list)
    normals: list = field(default_factory=list)      # None entries when missing
    colors: list = field(default_factory=list)       # None entries when missing
    triangles: list = field(default_factory=list)
    bones: list = None     # per-vertex skin palette index (None entries: unknown)
    joint: int = None      # joint name hash for a new piece (else taken from its name)


def parse_obj(text: str) -> list:
    """Read OBJ text into pieces. Undoes the export's Z flip and V flip.

    Each piece's vertices follow the file's own vertex order, so an
    exported-then-imported piece keeps its original order."""
    vs, vts, vns = [], [], []
    pieces, current, lookup = [], None, {}
    material = ""
    keys = {}  # id(piece) -> list of (v, vt, vn) per vertex

    def start(name):
        nonlocal current, lookup
        current = ObjPiece(name, material)
        pieces.append(current)
        keys[id(current)] = []
        lookup = {}

    def index(token, count):
        i = int(token)
        return i - 1 if i > 0 else count + i

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        op = parts[0]
        if op == "v":
            x, y, z = (float(v) for v in parts[1:4])
            col = tuple(float(c) for c in parts[4:7]) if len(parts) >= 7 else None
            vs.append(((-x, y, -z), col))
        elif op == "vt":
            u = float(parts[1])
            v = float(parts[2]) if len(parts) > 2 else 0.0
            vts.append((u, 1 - v))
        elif op == "vn":
            x, y, z = (float(v) for v in parts[1:4])
            vns.append((-x, y, -z))
        elif op in ("o", "g"):
            name = " ".join(parts[1:]) or "unnamed"
            if op == "g" and current and current.name == name:
                continue
            start(name)
        elif op == "usemtl":
            material = " ".join(parts[1:])
            if current is None:
                start("unnamed")
            if current.triangles and current.material != material:
                start(current.name)  # one material per piece
            current.material = material
        elif op == "f":
            if current is None:
                start("unnamed")
            corners = []
            for token in parts[1:]:
                bits = token.split("/")
                vi = index(bits[0], len(vs))
                ti = index(bits[1], len(vts)) if len(bits) > 1 and bits[1] else None
                ni = index(bits[2], len(vns)) if len(bits) > 2 and bits[2] else None
                key = (vi, ti, ni)
                if key not in lookup:
                    pos, col = vs[vi]
                    lookup[key] = len(current.positions)
                    keys[id(current)].append((vi, -1 if ti is None else ti, -1 if ni is None else ni))
                    current.positions.append(pos)
                    current.colors.append(col)
                    current.uvs.append(vts[ti] if ti is not None else (0.0, 0.0))
                    current.normals.append(vns[ni] if ni is not None else None)
                corners.append(lookup[key])
            for k in range(1, len(corners) - 1):
                # The export's half turn keeps the winding.
                current.triangles.append((corners[0], corners[k], corners[k + 1]))
    out = []
    for p in pieces:
        if not p.triangles:
            continue
        k = keys[id(p)]
        order = sorted(range(len(p.positions)), key=lambda i: k[i])
        remap = {old: new for new, old in enumerate(order)}
        for attr in ("positions", "uvs", "normals", "colors"):
            values = getattr(p, attr)
            setattr(p, attr, [values[i] for i in order])
        p.triangles = [tuple(remap[i] for i in t) for t in p.triangles]
        out.append(p)
    return out


def _piece_index(name):
    m = re.match(r"p(\d+)(?:_|\.|$)", name)
    return int(m.group(1)) if m else None


def _face_normals(obj):
    """Fill missing normals with averaged face normals."""
    acc = [[0.0, 0.0, 0.0] for _ in obj.positions]
    for a, b, c in obj.triangles:
        pa, pb, pc = obj.positions[a], obj.positions[b], obj.positions[c]
        u = [pb[k] - pa[k] for k in range(3)]
        v = [pc[k] - pa[k] for k in range(3)]
        n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        for i in (a, b, c):
            for k in range(3):
                acc[i][k] += n[k]
    out = []
    for given, n in zip(obj.normals, acc):
        if given is not None:
            out.append(given)
            continue
        ln = math.sqrt(sum(x * x for x in n)) or 1.0
        out.append(tuple(x / ln for x in n))
    return out


def triangles_to_strip(triangles) -> list:
    """Join triangles into one strip. Joins repeat indices so the extra
    triangles are degenerate; each triangle starts at an even position so its
    winding is kept."""
    strip = []
    for a, b, c in triangles:
        if strip:
            strip += [strip[-1], a]
            if len(strip) % 2:
                strip.append(a)
        strip += [a, b, c]
    return strip


def _pack_normal(n):
    x, y, z = n
    ln = math.sqrt(x * x + y * y + z * z) or 1.0
    x, y, z = x / ln, y / ln, z / ln
    xi = max(-1023, min(1023, round(x * 1023))) & 0x7FF
    yi = max(-1023, min(1023, round(y * 1023))) & 0x7FF
    zi = max(-511, min(511, round(z * 511))) & 0x3FF
    return xi | yi << 11 | zi << 22


def _conj(q):
    return (-q[0], -q[1], -q[2], q[3])


class _Nearest:
    """Nearest original vertex lookup on a coarse grid."""

    def __init__(self, points):
        self.points = points
        lo = [min(p[k] for p in points) for k in range(3)] if points else [0, 0, 0]
        hi = [max(p[k] for p in points) for k in range(3)] if points else [1, 1, 1]
        self.cell = max(max(hi[k] - lo[k] for k in range(3)) / 16, 1e-6)
        self.grid = {}
        for i, p in enumerate(points):
            self.grid.setdefault(self._key(p), []).append(i)

    def _key(self, p):
        return tuple(int(math.floor(c / self.cell)) for c in p)

    def find(self, p):
        if not self.points:
            return None
        kx, ky, kz = self._key(p)
        for r in range(0, 40):
            best, bi = None, None
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    for dz in range(-r, r + 1):
                        if max(abs(dx), abs(dy), abs(dz)) != r:
                            continue
                        for i in self.grid.get((kx + dx, ky + dy, kz + dz), ()):
                            q = self.points[i]
                            d = (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2
                            if best is None or d < best:
                                best, bi = d, i
            if bi is not None:
                return bi
        return min(range(len(self.points)),
                   key=lambda i: sum((p[k] - self.points[i][k]) ** 2 for k in range(3)))


@dataclass
class Report:
    edited: list = field(default_factory=list)
    added: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    unchanged: int = 0
    box_grew: bool = False
    collision_boxes: int = 0
    warnings: list = field(default_factory=list)

    @property
    def changed(self):
        return bool(self.edited or self.added or self.removed)


def import_obj(model_record: bytes, segments_record: bytes, obj_text: str,
               lod: int = 0, damaged: bool = False, material_hash=None,
               collision: bool = True) -> tuple:
    """Return (model record, segments record, Report).

    material_hash: maps an OBJ material name to a texture hash; default
    accepts 8-digit hex or hashes the name."""
    return import_pieces(model_record, segments_record, parse_obj(obj_text), lod, damaged,
                         material_hash, collision)


def import_pieces(model_record: bytes, segments_record: bytes, objs: list,
                  lod: int = 0, damaged: bool = False, material_hash=None,
                  collision: bool = True, report=None) -> tuple:
    """Like import_obj, from already-read pieces (ObjPiece, in model space)."""
    def default_hash(n):
        n = re.sub(r"\.\d{3}$", "", n)  # Blender's duplicate suffix
        return int(n, 16) if re.fullmatch(r"[0-9A-Fa-f]{8}", n) else name_hash(n)
    material_hash = material_hash or default_hash
    model = mdl.decode(model_record, segments_record)
    report = report or Report()

    # --- raw structure of the model record (needed to compare edits)
    modl_body, modl_pad = ucfb.unwrap(model_record, b"modl", with_pad=True)
    modl_items, modl_pad_last = ucfb.split(modl_body)
    info = bytearray(dict(modl_items)[b"INFO"])
    qbox = list(struct.unpack_from("<6f", info, 8))
    bounds = list(struct.unpack_from("<6f", info, 32))
    xforms = mdl.joint_transforms(model.joints)
    lo, hi = qbox[0:3], qbox[3:6]
    old_centre = [(lo[a] + hi[a]) / 2 for a in range(3)]
    old_half = [max((hi[a] - lo[a]) / 2, 1e-12) for a in range(3)]

    def to_local(joint, p):
        rot, pos = xforms.get(joint, ((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0)))
        return mdl._qrot(_conj(rot), tuple(p[k] - pos[k] for k in range(3)))

    def signature(joint, positions, uvs, triangles, bones=None):
        """Mesh summary on the game's own grid, independent of vertex order."""
        keys = []
        for v, (p, uv) in enumerate(zip(positions, uvs)):
            loc = to_local(joint, p)
            keys.append(tuple(round((loc[a] - old_centre[a]) / old_half[a] * 32767)
                              if old_half[a] > 1e-9 else round(loc[a] - old_centre[a], 6) for a in range(3))
                        + (round(uv[0] * 2048), round(uv[1] * 2048))
                        + ((bones[v] if bones[v] is not None else -1,) if bones else ()))
        return sorted(tuple(sorted(keys[i] for i in t)) for t in triangles)

    def unchanged(piece, obj):
        # Bones count only when both sides have them and the file knows them all.
        use_bones = (piece.bones is not None and obj.bones is not None
                     and all(b is not None for b in obj.bones))
        return (len(piece.triangles) == len(obj.triangles)
                and signature(piece.joint, piece.positions, piece.uvs, piece.triangles,
                              piece.bones if use_bones else None)
                == signature(piece.joint, obj.positions, obj.uvs, obj.triangles,
                             obj.bones if use_bones else None))

    chosen = {id(p) for p in model.at_lod(lod, damaged)}
    exported = {i for i, p in enumerate(model.pieces) if id(p) in chosen}
    by_index, new_objs = {}, []
    for o in objs:
        i = _piece_index(o.name)
        if i is not None and i in exported:
            if i in by_index:  # Blender may split an object: merge parts back
                base = by_index[i]
                n = len(base.positions)
                base.positions += o.positions
                base.uvs += o.uvs
                base.normals += o.normals
                base.colors += o.colors
                if base.bones is not None or o.bones is not None:
                    base.bones = (base.bones or [None] * n) + (o.bones or [None] * len(o.positions))
                base.triangles += [(a + n, b + n, c + n) for a, b, c in o.triangles]
            else:
                by_index[i] = o
        else:
            if i is not None:
                report.warnings.append(f"{o.name}: piece {i} isn't in this level of detail; added as new")
            new_objs.append(o)

    edits = {}
    for i in sorted(exported):
        if i not in by_index and not model.pieces[i].triangles:
            report.unchanged += 1  # empty pieces can't appear in an OBJ
        elif i not in by_index:
            report.removed.append(i)
        elif unchanged(model.pieces[i], by_index[i]):
            report.unchanged += 1
        else:
            edits[i] = by_index[i]
            report.edited.append(i)
    report.added = [o.name for o in new_objs]
    if not report.changed:
        return model_record, segments_record, report

    # --- raw structure of the segments record
    cseg, cseg_pad = ucfb.unwrap(segments_record, b"CSEG", with_pad=True)
    size, _ = struct.unpack_from("<II", cseg)
    tree = script.decompress(cseg[8:], size)
    roots, tree_pad = ucfb.split(tree)
    seg_items, seg_pad_last = ucfb.split(roots[0][1])
    segm_positions = [k for k, (t, _) in enumerate(seg_items) if t == b"segm"]

    joint_names = {j.name.lower(): name_hash(j.name) for j in model.joints}
    root_hash = name_hash(model.node) if model.node.lower() in joint_names else \
        (name_hash(model.joints[0].name) if model.joints else 0)

    def normal_to_local(joint, n):
        rot, _ = xforms.get(joint, ((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0)))
        return mdl._qrot(_conj(rot), n)

    # --- plan new pieces: (segm info + material) from a template
    def template_for(texture):
        for p in model.pieces:
            if p.texture == texture:
                return p
        return model.pieces[0] if model.pieces else None

    all_lods = 0
    for p in model.pieces:
        all_lods |= p.lod_mask
    plans = []  # (index or None, obj, joint, texture, lod_mask, stride_word, mtrl words)
    for i, o in edits.items():
        p = model.pieces[i]
        seg = dict(ucfb.iter_chunks(seg_items[segm_positions[i]][1]))
        stride_word = struct.unpack_from("<I", seg[b"INFO"], 8)[0]
        plans.append((i, o, p.joint, p.texture, p.lod_mask, stride_word, list(p.material)))
    skinned_pieces = [k for k, p in enumerate(model.pieces) if p.bones is not None]
    for o in new_objs:
        m = re.match(r"(?:new_)?(.+?)(?:\.\d+)?$", o.name)
        joint = joint_names.get(m.group(1).lower(), root_hash) if m else root_hash
        if o.joint is not None:
            joint = o.joint
        texture = material_hash(o.material) if o.material else (model.pieces[0].texture if model.pieces else 0)
        if o.bones is not None and any(b is not None for b in o.bones) and skinned_pieces:
            # A new skinned piece: copy a skinned piece's vertex format and joint.
            k = next((k for k in skinned_pieces if model.pieces[k].texture == texture), skinned_pieces[0])
            t = model.pieces[k]
            seg = dict(ucfb.iter_chunks(seg_items[segm_positions[k]][1]))
            stride_word = struct.unpack_from("<I", seg[b"INFO"], 8)[0]
            words = list(t.material)
            if len(words) > 3:
                words[3] = texture
            plans.append((None, o, t.joint, texture, all_lods or 1, stride_word, words))
            continue
        if o.bones is not None and any(b is not None for b in o.bones):
            report.warnings.append(f"{o.name}: skin weights ignored (this model has no skinned pieces)")
        t = template_for(texture)
        words = list(t.material) if t else [0, 0, 0, 0, 0, 0x100, 0]
        words[1] = joint
        if len(words) > 2:
            words[2] = joint
        if len(words) > 3:
            words[3] = texture
        plans.append((None, o, joint, texture, all_lods or 1, 20, words))

    # --- quantization box must hold every local position
    new_locals = {}
    for k, (i, o, joint, *_rest) in enumerate(plans):
        new_locals[k] = [to_local(joint, p) for p in o.positions]
    pts = [p for ps in new_locals.values() for p in ps]
    nlo = [min([lo[a]] + [p[a] for p in pts]) for a in range(3)]
    nhi = [max([hi[a]] + [p[a] for p in pts]) for a in range(3)]
    grew = nlo != list(lo) or nhi != list(hi)
    report.box_grew = grew
    centre = [(nlo[a] + nhi[a]) / 2 for a in range(3)]
    half = [max((nhi[a] - nlo[a]) / 2, 1e-9) for a in range(3)]

    def quant(p):
        return tuple(max(-32768, min(32767, round((p[a] - centre[a]) / half[a] * 32767))) for a in range(3))

    def requantize(seg_body):
        """Rewrite positions of an untouched piece for the grown box."""
        items, pad_last = ucfb.split(seg_body)
        d = dict(items)
        nv, _, stride_word, _ = struct.unpack_from("<4I", d[b"INFO"])
        stride = stride_word & 0xFFFF
        vb = bytearray(d[b"VBUF"])
        for v in range(nv):
            q = struct.unpack_from("<3h", vb, v * stride)
            local = [old_centre[a] + old_half[a] * q[a] / 32767 for a in range(3)]
            struct.pack_into("<3h", vb, v * stride, *quant(local))
        return ucfb.pack_children([(t, bytes(vb) if t == b"VBUF" else b) for t, b in items], pad_last)

    # --- encode planned pieces
    def build_segm(k, plan):
        i, o, joint, texture, lod_mask, stride_word, words = plan
        stride = stride_word & 0xFFFF
        skinned = bool(stride_word & 0x10000)
        normals = _face_normals(o)
        if len(o.positions) > MAX_INDEX:
            raise ValueError(f"{o.name}: {len(o.positions)} vertices; a piece can hold at most {MAX_INDEX}")
        donor = model.pieces[i] if i is not None else None
        donor_raw = None
        if donor is not None:
            seg = dict(ucfb.iter_chunks(seg_items[segm_positions[i]][1]))
            donor_raw = seg[b"VBUF"]
            near = _Nearest(donor.positions)
        vb = bytearray()
        for v, p in enumerate(o.positions):
            q = quant(new_locals[k][v])
            n = _pack_normal(normal_to_local(joint, normals[v]))
            u, w = o.uvs[v]
            uv = (max(-32768, min(32767, round(u * 2048))), max(-32768, min(32767, round(w * 2048))))
            bone, extra, colour = 0, b"\0" * (stride - 20), (255, 255, 255, 255)
            if donor is not None and donor.positions:
                j = near.find(p)
                raw = donor_raw[j * stride:(j + 1) * stride]
                bone = struct.unpack_from("<H", raw, 6)[0] if skinned else 0
                extra = raw[20:stride]
                b_, g_, r_, a_ = raw[12:16]
                colour = (r_, g_, b_, a_)
            if skinned and o.bones is not None and o.bones[v] is not None:
                bone = o.bones[v]
            if o.colors[v] is not None:
                r_, g_, b_ = (max(0, min(255, round(c * 255))) for c in o.colors[v])
                colour = (r_, g_, b_, colour[3])
            vb += struct.pack("<3hHI", *q, bone, n)
            vb += bytes((colour[2], colour[1], colour[0], colour[3]))
            vb += struct.pack("<2h", *uv) + extra
        strip = triangles_to_strip(o.triangles)
        info_chunk = struct.pack("<4I", len(o.positions), len(strip), stride_word, lod_mask)
        mtrl = struct.pack(f"<{len(words)}I", *words)
        return ucfb.pack_children([(b"INFO", info_chunk), (b"MTRL", mtrl), (b"VBUF", bytes(vb)),
                                   (b"IBUF", struct.pack(f"<{len(strip)}H", *strip))])

    replaced = {}
    added_bodies = []
    for k, plan in enumerate(plans):
        body = build_segm(k, plan)
        if plan[0] is None:
            added_bodies.append(body)
        else:
            replaced[plan[0]] = body

    removed = set(report.removed)
    new_items = []
    seg_no = 0
    for t, b in seg_items:
        if t != b"segm":
            new_items.append((t, b))
            continue
        if seg_no in removed:
            pass
        elif seg_no in replaced:
            new_items.append((t, replaced[seg_no]))
        else:
            new_items.append((t, requantize(b) if grew else b))
        seg_no += 1
    # New pieces go after the last segm, before trailing chunks such as cshp.
    last_segm = max((k for k, (t, _) in enumerate(new_items) if t == b"segm"), default=len(new_items) - 1)
    for body in reversed(added_bodies):
        new_items.insert(last_segm + 1, (b"segm", body))

    # --- collision: one box around each new piece.
    # Vehicles break if they get a third collision shape (tested in-game), and
    # the game never gives any vehicle more than two. So boxes join an
    # existing shape where one can take them: a box shape (its tree rebuilt
    # if it has one), else a convex hull turned into boxes approximating it
    # (the mesh + boxes layout tanks use). A new box shape is added only when
    # the model has fewer than two shapes; a triangle mesh is the last resort
    # (vehicle triangle hulls don't collide with the world, tested in-game).
    if collision and any(plan[0] is None for plan in plans):
        from . import collision as coll
        wanted = []  # (piece joint, centre, half) in that joint's space
        for k, plan in enumerate(plans):
            if plan[0] is not None or not new_locals[k]:
                continue
            if "nocoll" in plan[1].name.lower():
                continue  # decoration: no collision wanted
            pts = new_locals[k]
            lo3 = [min(q[a] for q in pts) for a in range(3)]
            hi3 = [max(q[a] for q in pts) for a in range(3)]
            centre3 = tuple((lo3[a] + hi3[a]) / 2 for a in range(3))
            half3 = tuple(max((hi3[a] - lo3[a]) / 2, 0.01) for a in range(3))
            wanted.append((plan[2], centre3, half3))

        def into(target):
            """Boxes re-expressed in the target joint's space (rows = axes)."""
            trot, tpos = xforms.get(target, ((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0)))
            inv = _conj(trot)
            out = []
            for joint, centre3, half3 in wanted:
                rot, pos = xforms.get(joint, ((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0)))
                world = tuple(a + b for a, b in zip(mdl._qrot(rot, centre3), pos))
                c = mdl._qrot(inv, tuple(world[i] - tpos[i] for i in range(3)))
                rows = []
                for axis in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
                    rows += list(mdl._qrot(inv, mdl._qrot(rot, axis)))
                out.append((c, half3, tuple(rows)))
            return out

        if wanted:
            where = next((k for k, (t, _) in enumerate(new_items) if t == b"cshp"), None)
            materials = [sh.material for sh in model.collision]
            # Vehicles: hull mesh uses 6 and secondary shapes 9.
            material = 9 if 6 in materials else (max(set(materials), key=materials.count) if materials else 7)
            first_joint = wanted[0][0]
            if where is None:
                boxes = into(first_joint)
                block = coll.new_block(material, first_joint, [(c, h) for c, h, _ in boxes])
                new_items.append((b"cshp", block))
                report.collision_boxes += len(boxes)
            else:
                body = new_items[where][1]
                target = coll.plain_box_shape(body, with_tree=True)
                hull = None if target else coll.convex_hull_shape(body)
                mesh = None if target or hull else coll.triangle_mesh_shape(body)
                if target:
                    index, tjoint, _ = target
                    boxes = into(tjoint)
                    new_items[where] = (b"cshp", coll.append_boxes(body, index, boxes))
                    report.collision_boxes += len(boxes)
                elif hull:
                    index, tjoint, _ = hull
                    boxes = into(tjoint)
                    new_items[where] = (b"cshp", coll.hull_to_box_shape(body, index, boxes))
                    report.collision_boxes += len(boxes)
                    report.warnings.append("the model's convex hull collision was replaced by boxes "
                                           "approximating it, so new boxes can join it")
                elif coll.shape_count(body) < 2:
                    boxes = into(first_joint)
                    body = coll.add_box_shape(body, material, first_joint, [(c, h) for c, h, _ in boxes])
                    new_items[where] = (b"cshp", body)
                    report.collision_boxes += len(boxes)
                elif mesh:
                    # Last resort: triangles in a mesh shape. In-game, a vehicle's
                    # triangle hull did not collide with the world this way.
                    index, tjoint, _ = mesh
                    boxes = into(tjoint)
                    new_items[where] = (b"cshp", coll.append_mesh_boxes(body, index, boxes))
                    report.collision_boxes += len(boxes)
                    report.warnings.append("new boxes were added to a triangle mesh shape; on vehicles "
                                           "these don't collide with the world")
                else:
                    report.warnings.append(
                        "new pieces got no collision: this model already has two collision shapes and "
                        "neither can take more (a third shape breaks vehicle physics)")

    new_tree = ucfb.pack_children([(b"ucfb", ucfb.pack_children(new_items, seg_pad_last))], tree_pad)
    packed = script.compress(new_tree)
    new_segments = ucfb.wrap(b"CSEG", struct.pack("<II", len(new_tree), len(packed)) + packed, cseg_pad)

    # --- model header: piece count, boxes
    pieces_now = sum(1 for t, _ in new_items if t == b"segm")
    struct.pack_into("<H", info, 2, pieces_now)
    struct.pack_into("<6f", info, 8, *nlo, *nhi)
    mins, maxs = bounds[0:3], bounds[3:6]
    for k, plan in enumerate(plans):
        for p in plan[1].positions:
            for a in range(3):
                mins[a] = min(mins[a], p[a])
                maxs[a] = max(maxs[a], p[a])
    struct.pack_into("<6f", info, 32, *mins, *maxs)
    new_modl_items = [(t, bytes(info) if t == b"INFO" else b) for t, b in modl_items]
    new_model = ucfb.wrap(b"modl", ucfb.pack_children(new_modl_items, modl_pad_last), modl_pad)
    return new_model, new_segments, report


def import_into_folders(folders, name, obj_text, lod=None, damaged=None, material_hash=None,
                        collision=True):
    """Apply an edited OBJ (text) or glTF (a Path to .glb/.gltf) to every
    extracted folder that holds the model.

    Each folder is an `extract` output (records/ + manifest.json). The first
    replacement of a record keeps its original in originals/. Returns a list
    of (folder, Report)."""
    import json
    from pathlib import Path as _P
    key = int(name, 16) if re.fullmatch(r"[0-9A-Fa-f]{8}", name) else name_hash(name)
    results = []
    for folder in folders:
        folder = _P(folder)
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        files = {}
        for e in manifest["records"]:
            if int(e["key"], 16) == key and int(e["kind"], 16) in (mdl.MODEL, mdl.SEGMENTS):
                files[int(e["kind"], 16)] = folder / e["file"]
        if mdl.MODEL not in files or mdl.SEGMENTS not in files:
            continue
        originals = {k: folder / "originals" / f.name for k, f in files.items()}
        base = {k: (originals[k] if originals[k].exists() else f).read_bytes() for k, f in files.items()}
        if isinstance(obj_text, _P):
            from . import gltf_model
            new_model, new_segments, report = gltf_model.import_gltf(
                base[mdl.MODEL], base[mdl.SEGMENTS], obj_text, lod, damaged, material_hash, collision)
        else:
            new_model, new_segments, report = import_obj(base[mdl.MODEL], base[mdl.SEGMENTS], obj_text,
                                                         lod or 0, bool(damaged), material_hash, collision)
        for k, data in ((mdl.MODEL, new_model), (mdl.SEGMENTS, new_segments)):
            if data != base[k] and not originals[k].exists():
                originals[k].parent.mkdir(exist_ok=True)
                originals[k].write_bytes(base[k])
            files[k].write_bytes(data)
            if data == base[k] and originals[k].exists():
                originals[k].unlink()
        results.append((folder, report))
    if not results:
        raise KeyError(f"model {name!r} isn't in any of the given folders")
    return results
