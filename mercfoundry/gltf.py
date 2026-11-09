"""Export a decoded model as binary glTF 2.0 (.glb).

- Every joint becomes a node, in the skeleton's hierarchy.
- Each rigid piece is a mesh node parented to its joint, with positions in
  that joint's space, so posing a joint moves its pieces.
- Skinned pieces get a skin. Their bone indices refer to the model's joints
  whose names start with "bone", in skeleton order (inferred from retail
  data: it fits all 97 skinned models). One bone per vertex, weight 1.
- Textures are embedded as PNG. Alpha masking (cut-outs) is used where the
  game uses it: the piece's material flags (MTRL word 5) have bit 0x10, as on
  leaves and fences (0x111), and the texture has transparent pixels. Plain
  surfaces (0x100, e.g. building walls) keep other data in their alpha and are
  drawn solid.

The game's axes are left-handed; glTF's are right-handed, so Z is negated
(points, normals, quaternion x/y) and triangle winding is reversed.
Mesh nodes are named like the OBJ export (p<index>_<joint>).
"""
import json
import math
import struct

from . import model as mdl
from .hashing import name_hash

FLOAT, USHORT, UINT, UBYTE = 5126, 5123, 5125, 5121
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963


def _flip_point(p):
    return (p[0], p[1], -p[2])


def _flip_quat(q):
    return (-q[0], -q[1], q[2], q[3])


def _normalise(q):
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return tuple(c / n for c in q)


def _matrix(rot, pos):
    """Column-major 4x4 from a quaternion and translation (glTF layout)."""
    x, y, z, w = rot
    m = [
        1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w), 0,
        2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w), 0,
        2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y), 0,
        pos[0], pos[1], pos[2], 1,
    ]
    return m


def _inverse_rigid(rot, pos):
    inv = (-rot[0], -rot[1], -rot[2], rot[3])
    t = mdl._qrot(inv, pos)
    return _matrix(inv, (-t[0], -t[1], -t[2]))


def skin_palette(joints):
    """Joint indices that skinned bone indices refer to."""
    return [i for i, j in enumerate(joints) if j.name.lower().startswith("bone")]


class _Writer:
    def __init__(self):
        self.bin = bytearray()
        self.views, self.accessors = [], []

    def view(self, data, target=None):
        while len(self.bin) % 4:
            self.bin.append(0)
        v = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            v["target"] = target
        self.bin += data
        self.views.append(v)
        return len(self.views) - 1

    def accessor(self, data, ctype, count, kind, target=None, minmax=None, normalized=False):
        a = {"bufferView": self.view(data, target), "componentType": ctype, "count": count, "type": kind}
        if minmax:
            a["min"], a["max"] = minmax
        if normalized:
            a["normalized"] = True
        self.accessors.append(a)
        return len(self.accessors) - 1


def cutout_flag(piece):
    """Whether the game alpha-tests this piece's texture (MTRL word 5, bit 0x10)."""
    words = piece.material or ()
    return len(words) > 5 and bool(words[5] & 0x10)


def export(model, lod=0, damaged=False, textures=None, texture_name=None, animations=(),
           in_place=False) -> bytes:
    """Return .glb bytes. textures: {texture hash: (png bytes, has_alpha)};
    animations: decoded animation.Animation objects, matched to joints by name;
    in_place drops root motion (translation of joints without a parent)."""
    textures = textures or {}
    texture_name = texture_name or (lambda h: f"{h:08X}")
    w = _Writer()
    gl = {"asset": {"version": "2.0", "generator": "mercfoundry",
                    "extras": {"mercfoundry": {"model": model.name, "lod": lod, "damaged": bool(damaged)}}},
          "scene": 0,
          "scenes": [{"name": model.name, "nodes": []}], "nodes": [], "meshes": [],
          "materials": [], "accessors": w.accessors, "bufferViews": w.views, "buffers": []}
    root = len(gl["nodes"])
    gl["nodes"].append({"name": model.name, "children": []})
    gl["scenes"][0]["nodes"].append(root)

    # Joints, as a node tree.
    by_name = {j.name.lower(): i for i, j in enumerate(model.joints)}
    joint_node = {}
    for i, j in enumerate(model.joints):
        joint_node[i] = len(gl["nodes"])
        gl["nodes"].append({"name": j.name, "rotation": list(_flip_quat(_normalise(j.rotation))),
                            "translation": list(_flip_point(j.translation))})
    for i, j in enumerate(model.joints):
        parent = by_name.get(j.parent.lower())
        holder = gl["nodes"][joint_node[parent]] if parent is not None and parent != i else gl["nodes"][root]
        holder.setdefault("children", []).append(joint_node[i])
    joint_by_hash = {name_hash(j.name): i for i, j in enumerate(model.joints)}
    xforms = mdl.joint_transforms(model.joints)

    # Materials, one per texture and cut-out setting.
    material_of = {}

    def material(texture, cutout):
        if (texture, cutout) in material_of:
            return material_of[(texture, cutout)]
        m = {"name": texture_name(texture),
             "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 1.0}}
        if texture in textures:
            png_bytes, has_alpha = textures[texture]
            image = len(gl.setdefault("images", []))
            gl["images"].append({"name": texture_name(texture), "mimeType": "image/png",
                                 "bufferView": w.view(png_bytes)})
            gl.setdefault("samplers", [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}])
            tex = len(gl.setdefault("textures", []))
            gl["textures"].append({"source": image, "sampler": 0})
            m["pbrMetallicRoughness"]["baseColorTexture"] = {"index": tex}
            if has_alpha and cutout:
                m["alphaMode"] = "MASK"
                m["alphaCutoff"] = 0.5
        material_of[(texture, cutout)] = len(gl["materials"])
        gl["materials"].append(m)
        return material_of[(texture, cutout)]

    # Skin shared by all skinned pieces.
    palette = skin_palette(model.joints)
    skin = None

    def get_skin():
        nonlocal skin
        if skin is None:
            mats = []
            for i in palette:
                rot, pos = xforms[name_hash(model.joints[i].name)]
                mats += _inverse_rigid(_flip_quat(rot), _flip_point(pos))
            ibm = w.accessor(struct.pack(f"<{len(mats)}f", *mats), FLOAT, len(palette), "MAT4")
            gl["skins"] = [{"name": model.name, "joints": [joint_node[i] for i in palette],
                            "inverseBindMatrices": ibm}]
            skin = 0
        return skin

    chosen = {id(p) for p in model.at_lod(lod, damaged)}
    for index, p in enumerate(model.pieces):
        if id(p) not in chosen or not p.triangles:
            continue
        skinned = p.bones is not None
        if skinned:
            positions = [_flip_point(v) for v in p.positions]
            normals = [_flip_point(n) for n in p.normals]
        else:
            rot, pos = xforms.get(p.joint, ((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0)))
            inv = (-rot[0], -rot[1], -rot[2], rot[3])
            positions = [_flip_point(mdl._qrot(inv, tuple(v[k] - pos[k] for k in range(3)))) for v in p.positions]
            normals = [_flip_point(mdl._qrot(inv, n)) for n in p.normals]
        normals = [tuple(c / (math.sqrt(sum(x * x for x in n)) or 1.0) for c in n) for n in normals]
        n = len(positions)
        lo = [min(v[k] for v in positions) for k in range(3)]
        hi = [max(v[k] for v in positions) for k in range(3)]
        attrs = {
            "POSITION": w.accessor(struct.pack(f"<{3 * n}f", *[c for v in positions for c in v]),
                                   FLOAT, n, "VEC3", ARRAY_BUFFER, (lo, hi)),
            "NORMAL": w.accessor(struct.pack(f"<{3 * n}f", *[c for v in normals for c in v]),
                                 FLOAT, n, "VEC3", ARRAY_BUFFER),
            "TEXCOORD_0": w.accessor(struct.pack(f"<{2 * n}f", *[c for uv in p.uvs for c in uv]),
                                     FLOAT, n, "VEC2", ARRAY_BUFFER),
            "COLOR_0": w.accessor(bytes(c for col in p.colors for c in col), UBYTE, n, "VEC4",
                                  ARRAY_BUFFER, normalized=True),
        }
        if skinned:
            joints = []
            for b in p.bones:
                joints += [b if b < len(palette) else 0, 0, 0, 0]
            attrs["JOINTS_0"] = w.accessor(struct.pack(f"<{4 * n}H", *joints), USHORT, n, "VEC4", ARRAY_BUFFER)
            attrs["WEIGHTS_0"] = w.accessor(struct.pack(f"<{4 * n}f", *([1.0, 0.0, 0.0, 0.0] * n)),
                                            FLOAT, n, "VEC4", ARRAY_BUFFER)
        tris = [c for a, b, cc in p.triangles for c in (a, cc, b)]  # Z flip reverses winding
        indices = w.accessor(struct.pack(f"<{len(tris)}I", *tris), UINT, len(tris), "SCALAR",
                             ELEMENT_ARRAY_BUFFER)
        mesh = len(gl["meshes"])
        gl["meshes"].append({"name": mdl.piece_object_name(model, index),
                             "primitives": [{"attributes": attrs, "indices": indices,
                                             "material": material(p.texture, cutout_flag(p))}]})
        node = {"name": mdl.piece_object_name(model, index), "mesh": mesh}
        node_index = len(gl["nodes"])
        gl["nodes"].append(node)
        if skinned:
            node["skin"] = get_skin()
            gl["nodes"][root]["children"].append(node_index)
        else:
            owner = joint_by_hash.get(p.joint)
            holder = gl["nodes"][joint_node[owner]] if owner is not None else gl["nodes"][root]
            holder.setdefault("children", []).append(node_index)

    roots = {j.name.lower() for j in model.joints if j.parent.lower() not in by_name}
    for anim in animations:
        _add_animation(gl, w, anim, {j.name.lower(): joint_node[i] for i, j in enumerate(model.joints)},
                       roots if in_place else set())

    while len(w.bin) % 4:
        w.bin.append(0)
    gl["buffers"] = [{"byteLength": len(w.bin)}]
    for key in ("materials", "meshes"):
        if not gl[key]:
            del gl[key]
    text = json.dumps(gl, separators=(",", ":")).encode("utf-8")
    text += b" " * (-len(text) % 4)
    body = (struct.pack("<II", len(text), 0x4E4F534A) + text
            + struct.pack("<II", len(w.bin), 0x004E4942) + bytes(w.bin))
    return struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body


def read(glb: bytes):
    """Parse a .glb into (json, binary chunk). For checks and tests."""
    magic, version, length = struct.unpack_from("<III", glb)
    if magic != 0x46546C67 or version != 2 or length != len(glb):
        raise ValueError("not a glTF 2.0 binary file")
    jlen, jtype = struct.unpack_from("<II", glb, 12)
    doc = json.loads(glb[20:20 + jlen])
    if 20 + jlen >= len(glb):
        if jtype != 0x4E4F534A:
            raise ValueError("bad glTF chunk types")
        return doc, b""          # no binary chunk (nothing but JSON)
    blen, btype = struct.unpack_from("<II", glb, 20 + jlen)
    if jtype != 0x4E4F534A or btype != 0x004E4942:
        raise ValueError("bad glTF chunk types")
    return doc, glb[28 + jlen:28 + jlen + blen]


def _add_animation(gl, w, anim, nodes, still=frozenset()):
    """Add one clip: a rotation track per matching joint, plus translation."""
    from .animation import FPS
    times = [f / FPS for f in range(anim.frames)]
    t_all = w.accessor(struct.pack(f"<{len(times)}f", *times), FLOAT, len(times), "SCALAR",
                       minmax=([times[0]], [times[-1]]))
    t_one = None
    samplers, channels, matched = [], [], 0
    for track in anim.tracks:
        node = nodes.get(track.joint.lower())
        if node is None:
            continue
        matched += 1
        quats = [_flip_quat(_normalise(q)) for q in track.rotations]
        out = w.accessor(struct.pack(f"<{4 * len(quats)}f", *[c for q in quats for c in q]),
                         FLOAT, len(quats), "VEC4")
        samplers.append({"input": t_all, "output": out, "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1, "target": {"node": node, "path": "rotation"}})
        if track.joint.lower() in still:
            continue  # root motion dropped
        if track.translations:
            pts = [_flip_point(t) for t in track.translations]
            out = w.accessor(struct.pack(f"<{3 * len(pts)}f", *[c for p in pts for c in p]),
                             FLOAT, len(pts), "VEC3")
            samplers.append({"input": t_all, "output": out, "interpolation": "LINEAR"})
        elif track.translation is not None:
            if t_one is None:
                t_one = w.accessor(struct.pack("<f", 0.0), FLOAT, 1, "SCALAR", minmax=([0.0], [0.0]))
            out = w.accessor(struct.pack("<3f", *_flip_point(track.translation)), FLOAT, 1, "VEC3")
            samplers.append({"input": t_one, "output": out, "interpolation": "STEP"})
        else:
            continue
        channels.append({"sampler": len(samplers) - 1, "target": {"node": node, "path": "translation"}})
    if channels:
        gl.setdefault("animations", []).append({"name": anim.name, "samplers": samplers, "channels": channels})
    return matched
