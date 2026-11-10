"""Read an edited glTF model (.glb or .gltf) back into the game's pieces.

Works with files from `model --glb` after editing in Blender or another
glTF tool:

- Each mesh node is placed with its full node transform, so a piece
  appears in the game where it sits in the file. Skinned meshes are placed
  with their skin, and each vertex keeps the bone with the largest weight
  (the game has one bone per vertex).
- Mesh nodes named p<index>... (as exported) replace that piece; others
  are new pieces. A new piece belongs to the joint it is parented to (the
  nearest parent node named like a game joint); a new skinned piece copies
  a skinned piece's vertex format.
- Materials are texture names, as in the export.
- Joints: a game joint moved in the file takes its new position (its
  rotation is kept; Blender may re-orient bones), and whatever hangs from it
  follows: pieces, collision, child joints. An empty node under a game joint
  with a new name becomes a new joint (place and rotation from the file),
  added at the end of the skeleton, e.g. a seat or mount for a template's
  RiderSeatpoint_* or turret properties. Moving skin bones (bone*) is not
  fully supported: animations that set a bone's translation override it.

Positions go back to the decoded model's axes (X and Z negated: the
export's half turn, undone), winding kept.
"""
import math
import re

from . import gltf, gltf_anim
from . import model as mdl
from .hashing import name_hash
from .model_import import ObjPiece

IDENTITY = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]


def _node_matrix(n):
    if "matrix" in n:
        return [float(v) for v in n["matrix"]]
    rot = gltf._normalise(n.get("rotation", (0.0, 0.0, 0.0, 1.0)))
    m = gltf._matrix(rot, n.get("translation", (0.0, 0.0, 0.0)))
    scale = n.get("scale", (1.0, 1.0, 1.0))
    for c in range(3):
        for r in range(3):
            m[c * 4 + r] *= scale[c]
    return m


def _mul(a, b):
    """Column-major 4x4 product a * b."""
    return [sum(a[k * 4 + r] * b[c * 4 + k] for k in range(4)) for c in range(4) for r in range(4)]


def _point(m, p):
    return tuple(m[r] * p[0] + m[4 + r] * p[1] + m[8 + r] * p[2] + m[12 + r] for r in range(3))


def _normal_matrix(m):
    """Inverse transpose of the 3x3 part, row-major."""
    a = [[m[c * 4 + r] for c in range(3)] for r in range(3)]
    cof = [[a[(r + 1) % 3][(c + 1) % 3] * a[(r + 2) % 3][(c + 2) % 3]
            - a[(r + 1) % 3][(c + 2) % 3] * a[(r + 2) % 3][(c + 1) % 3] for c in range(3)] for r in range(3)]
    det = sum(a[0][c] * cof[0][c] for c in range(3)) or 1.0
    return [[cof[r][c] / det for c in range(3)] for r in range(3)]


def _normal(nm, n):
    v = tuple(nm[r][0] * n[0] + nm[r][1] * n[1] + nm[r][2] * n[2] for r in range(3))
    ln = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / ln for c in v)


_LEGACY = [False]       # set per file by read() and read_skeleton() (gltf.legacy)


def _flip(p):
    """glTF to decoded model space: the export's half turn about Y (its own
    inverse); for files from before 0.67, the old Z mirror."""
    return (p[0], p[1], -p[2]) if _LEGACY[0] else (-p[0], p[1], -p[2])


def _game_quat(q):
    """A glTF rotation back in the game's axes (as gltf._flip_quat)."""
    return (-q[0], -q[1], q[2], q[3]) if _LEGACY[0] else (-q[0], q[1], -q[2], q[3])


def _values(doc, buffers, index, count=None):
    if index is None:
        return None
    a = doc["accessors"][index]
    if "sparse" in a:
        raise ValueError("sparse glTF accessors aren't supported; re-export without them")
    if "bufferView" not in a:
        return [(0.0,) * gltf_anim.SIZES[a["type"]]] * a["count"]
    return gltf_anim._accessor(doc, buffers, index)


def read(path, model):
    """(pieces, warnings, settings) from a glTF file for the decoded model.
    settings is {"lod": n, "damaged": bool} when the file records it (our
    exports do), else {}."""
    doc, buffers = gltf_anim.load(path)
    _LEGACY[0] = gltf.legacy(doc)
    nodes = doc.get("nodes", [])
    warnings = []
    parent = {}
    for i, n in enumerate(nodes):
        for c in n.get("children", []):
            parent[c] = i
    world = {}

    def world_of(i, depth=0):
        if i not in world:
            m = _node_matrix(nodes[i])
            if i in parent and depth < 256:
                m = _mul(world_of(parent[i], depth + 1), m)
            world[i] = m
        return world[i]

    joint_names = {j.name.lower(): j for j in model.joints}
    palette = {model.joints[k].name.lower(): b for b, k in enumerate(gltf.skin_palette(model.joints))}
    xforms = mdl.joint_transforms(model.joints)

    # Joints moved in the file (positions only: Blender may re-orient bones).
    moved = []
    for i, n in enumerate(nodes):
        j = joint_names.get(n.get("name", "").lower())
        if j is None or "mesh" in n:
            continue
        want = _flip(xforms[name_hash(j.name)][1])
        got = _point(world_of(i), (0.0, 0.0, 0.0))
        if max(abs(a - b) for a, b in zip(want, got)) > 1e-3:
            moved.append(j.name)
    if moved:
        warnings.append(f"joints aren't imported; these were moved and keep their game positions: "
                        f"{', '.join(moved[:8])}{' ...' if len(moved) > 8 else ''}")

    def owner_joint(i):
        k = parent.get(i)
        while k is not None:
            j = joint_names.get(nodes[k].get("name", "").lower())
            if j is not None:
                return name_hash(j.name)
            k = parent.get(k)
        return None

    pieces = []
    for i, n in enumerate(nodes):
        if "mesh" not in n:
            continue
        mesh = doc["meshes"][n["mesh"]]
        name = n.get("name") or mesh.get("name") or f"node{i}"
        skin = doc["skins"][n["skin"]] if "skin" in n and doc.get("skins") else None
        if skin is not None:
            ibms = _values(doc, buffers, skin.get("inverseBindMatrices"))
            joint_mats = []
            for k, jn in enumerate(skin["joints"]):
                ibm = list(ibms[k]) if ibms else IDENTITY
                joint_mats.append(_mul(world_of(jn), ibm))
            bone_of = [palette.get(nodes[jn].get("name", "").lower()) for jn in skin["joints"]]
        else:
            m = world_of(i)
            nm = _normal_matrix(m)
        for prim in mesh.get("primitives", []):
            if prim.get("mode", 4) != 4:
                warnings.append(f"{name}: skipped a primitive that isn't triangles")
                continue
            attrs = prim.get("attributes", {})
            pos = _values(doc, buffers, attrs.get("POSITION"))
            if not pos:
                continue
            nrm = _values(doc, buffers, attrs.get("NORMAL"))
            uvs = _values(doc, buffers, attrs.get("TEXCOORD_0"))
            col = _values(doc, buffers, attrs.get("COLOR_0"))
            joints = _values(doc, buffers, attrs.get("JOINTS_0")) if skin is not None else None
            weights = _values(doc, buffers, attrs.get("WEIGHTS_0")) if skin is not None else None
            idx = _values(doc, buffers, prim.get("indices"))
            idx = [t[0] for t in idx] if idx else list(range(len(pos)))
            mat = ""
            if "material" in prim and doc.get("materials"):
                mat = doc["materials"][prim["material"]].get("name", "")
            piece = ObjPiece(name, mat)
            piece.joint = owner_joint(i) if skin is None else None
            if skin is not None:
                piece.bones = []
            cache = {}
            unknown_bone = False
            for v, p in enumerate(pos):
                if skin is not None:
                    js = joints[v] if joints else (0,)
                    ws = weights[v] if weights else (1.0,)
                    key = tuple((int(j), round(w, 6)) for j, w in zip(js, ws) if w > 0) or ((0, 1.0),)
                    if key not in cache:
                        total = sum(w for _, w in key) or 1.0
                        mm = [sum(joint_mats[j][e] * w for j, w in key) / total for e in range(16)]
                        cache[key] = (mm, _normal_matrix(mm))
                    mm, nm_v = cache[key]
                    best = max(key, key=lambda jw: jw[1])[0]
                    bone = bone_of[best] if best < len(bone_of) else None
                    unknown_bone |= bone is None
                    piece.bones.append(bone)
                    piece.positions.append(_flip(_point(mm, p)))
                    piece.normals.append(_flip(_normal(nm_v, nrm[v])) if nrm else None)
                else:
                    piece.positions.append(_flip(_point(m, p)))
                    piece.normals.append(_flip(_normal(nm, nrm[v])) if nrm else None)
                piece.uvs.append(tuple(uvs[v][:2]) if uvs else (0.0, 0.0))
                piece.colors.append(tuple(col[v][:3]) if col else None)
            if unknown_bone:
                warnings.append(f"{name}: some vertices are weighted to bones the game skin doesn't have; "
                                "they take the nearest original vertex's bone")
            for t in range(0, len(idx) - 2, 3):
                a, b, c = idx[t:t + 3]
                # The export's half turn keeps winding; the old mirror reversed it.
                piece.triangles.append((a, c, b) if _LEGACY[0] else (a, b, c))
            if piece.triangles:
                pieces.append(piece)
    settings = {}
    extras = doc.get("asset", {}).get("extras", {}).get("mercfoundry", {})
    if "lod" in extras:
        settings = {"lod": int(extras["lod"]), "damaged": bool(extras.get("damaged"))}
    return pieces, warnings, settings


def _quat_from_matrix(m):
    """Unit quaternion (x, y, z, w) of a column-major 4x4's rotation, after
    taking out any scale."""
    r = [[m[c * 4 + row] for c in range(3)] for row in range(3)]
    for c in range(3):
        n = math.sqrt(sum(r[row][c] ** 2 for row in range(3))) or 1.0
        for row in range(3):
            r[row][c] /= n
    t = r[0][0] + r[1][1] + r[2][2]
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        q = ((r[2][1] - r[1][2]) / s, (r[0][2] - r[2][0]) / s, (r[1][0] - r[0][1]) / s, 0.25 * s)
    elif r[0][0] > r[1][1] and r[0][0] > r[2][2]:
        s = math.sqrt(1.0 + r[0][0] - r[1][1] - r[2][2]) * 2
        q = (0.25 * s, (r[0][1] + r[1][0]) / s, (r[0][2] + r[2][0]) / s, (r[2][1] - r[1][2]) / s)
    elif r[1][1] > r[2][2]:
        s = math.sqrt(1.0 + r[1][1] - r[0][0] - r[2][2]) * 2
        q = ((r[0][1] + r[1][0]) / s, 0.25 * s, (r[1][2] + r[2][1]) / s, (r[0][2] - r[2][0]) / s)
    else:
        s = math.sqrt(1.0 + r[2][2] - r[0][0] - r[1][1]) * 2
        q = ((r[0][2] + r[2][0]) / s, (r[1][2] + r[2][1]) / s, 0.25 * s, (r[1][0] - r[0][1]) / s)
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return tuple(c / n for c in q)


def _conj(q):
    return (-q[0], -q[1], -q[2], q[3])


def read_skeleton(path, model):
    """(joints, notes): the model's skeleton as edited in the file, or
    (None, notes) when it is unchanged.

    Moved joints: a game joint's node that sits somewhere else gets the new
    position; its rotation is kept (Blender may re-orient bones on export,
    which would otherwise turn every hardpoint). New joints: an empty node
    (no mesh) whose parent is a game joint (or another new joint) and whose
    name the model doesn't have; it takes its place and rotation from the
    file and is added at the end of the skeleton."""
    doc, _ = gltf_anim.load(path)
    _LEGACY[0] = gltf.legacy(doc)
    nodes = doc.get("nodes", [])
    parent = {}
    for i, n in enumerate(nodes):
        for c in n.get("children", []):
            parent[c] = i
    world = {}

    def world_of(i, depth=0):
        if i not in world:
            m = _node_matrix(nodes[i])
            if i in parent and depth < 256:
                m = _mul(world_of(parent[i], depth + 1), m)
            world[i] = m
        return world[i]

    game = {j.name.lower(): j for j in model.joints}
    xforms = {j.name.lower(): t for j, t in zip(model.joints,
                                                 (mdl.joint_transforms(model.joints)[name_hash(j.name)]
                                                  for j in model.joints))}
    node_of = {}
    for i, n in enumerate(nodes):
        key = n.get("name", "").lower()
        if key in game and "mesh" not in n and key not in node_of:
            node_of[key] = i
    # New world positions (model space) of the game joints; rotations stay.
    new_pos = {}
    for key, (rot, pos) in xforms.items():
        new_pos[key] = _flip(_point(world_of(node_of[key]), (0.0, 0.0, 0.0))) if key in node_of else None
    joints, moved, notes = [], [], []
    world_rot = {k: t[0] for k, t in xforms.items()}

    def resolved_pos(key, depth=0):
        """New model-space position: from the file, else the old local offset
        from the parent's new position."""
        if new_pos.get(key) is not None:
            return new_pos[key]
        j = game[key]
        p = j.parent.lower()
        if p in game and p != key and depth < 64:
            new_pos[key] = tuple(a + b for a, b in zip(
                mdl._qrot(world_rot[p], j.translation), resolved_pos(p, depth + 1)))
        else:
            new_pos[key] = xforms[key][1]
        return new_pos[key]

    for j in model.joints:
        key, p = j.name.lower(), j.parent.lower()
        pos = resolved_pos(key)
        if p in game and p != key:
            local = mdl._qrot(_conj(world_rot[p]), tuple(a - b for a, b in zip(pos, resolved_pos(p))))
        else:
            local = pos
        if max(abs(a - b) for a, b in zip(local, j.translation)) > 1e-4:
            moved.append(j.name)
            joints.append(mdl.Joint(j.name, j.rotation, tuple(local), j.parent))
        else:
            joints.append(j)
    # New joints: empty nodes under game joints (or under new ones).
    added = []
    known = dict(game)
    new_world = {}
    progress = True
    while progress:
        progress = False
        for i, n in enumerate(nodes):
            key = n.get("name", "").lower()
            if not key or "mesh" in n or key in known or i not in parent:
                continue
            pkey = nodes[parent[i]].get("name", "").lower()
            if pkey not in known:
                continue
            m = world_of(i)
            pos = _flip(_point(m, (0.0, 0.0, 0.0)))
            q = _quat_from_matrix(m)
            rot = _game_quat(q)
            prot = world_rot[pkey] if pkey in world_rot else new_world[pkey][0]
            ppos = resolved_pos(pkey) if pkey in game else new_world[pkey][1]
            local_rot = mdl._qmul(_conj(prot), rot)
            local_pos = mdl._qrot(_conj(prot), tuple(a - b for a, b in zip(pos, ppos)))
            pname = known[pkey].name if pkey in known else pkey
            joint = mdl.Joint(n["name"], local_rot, local_pos, pname)
            joints.append(joint)
            known[key] = joint
            new_world[key] = (rot, pos)
            added.append(n["name"])
            progress = True
    if moved:
        notes.append(f"moved joints: {', '.join(moved[:8])}{' ...' if len(moved) > 8 else ''}")
        bones = [m for m in moved if m.lower().startswith("bone")]
        if bones:
            notes.append(f"moved skin bones ({', '.join(bones[:4])}): animations that set these bones' "
                         f"translation will override the move")
    if added:
        notes.append(f"new joints: {', '.join(added[:8])}{' ...' if len(added) > 8 else ''}")
    return (joints if moved or added else None), notes


def with_skeleton(model_record, joints):
    """The Model record with its skeleton (SKEL) and joint count (INFO at 56)
    replaced."""
    import struct
    from . import ucfb
    body, pads = ucfb.unwrap(model_record, b"modl", with_pad=True)
    items, pad_last = ucfb.split(body)
    fields = dict(items)
    skel_items, skel_pad = ucfb.split(fields[b"SKEL"])
    old_jont = [b for t, b in skel_items if t == b"JONT"]
    by_name = {}
    for b in old_jont:
        by_name[b[:b.index(b"\0")].decode("latin-1").lower()] = b
    old = {j.name.lower(): j for j in mdl.read_joints(fields[b"SKEL"])}
    jonts = []
    for j in joints:
        o = old.get(j.name.lower())
        if o is not None and o == j:
            jonts.append(by_name[j.name.lower()])          # unchanged: keep its bytes
        else:
            jonts.append(j.name.encode("latin-1") + b"\0" + struct.pack("<4f3f", *j.rotation, *j.translation)
                         + j.parent.encode("latin-1") + b"\0")
    # Retail SKEL is NBNS, FSBI, then the JONTs; the joints stay last.
    new_skel = []
    for t, b in skel_items:
        if t == b"NBNS":
            new_skel.append((t, struct.pack("<I", len(joints))))
        elif t != b"JONT":
            new_skel.append((t, b))
    new_skel += [(b"JONT", b) for b in jonts]
    info = bytearray(fields[b"INFO"])
    struct.pack_into("<I", info, 56, len(joints))
    out = []
    for t, b in items:
        if t == b"SKEL":
            out.append((t, ucfb.pack_children(new_skel, skel_pad)))
        elif t == b"INFO":
            out.append((t, bytes(info)))
        else:
            out.append((t, b))
    return ucfb.wrap(b"modl", ucfb.pack_children(out, pad_last), pads)


def guess_view(model, pieces):
    """(lod, damaged) whose exported pieces best match the p<index> names
    present; for files that lost the export settings."""
    present = set()
    for p in pieces:
        m = re.match(r"p(\d+)(?:_|\.|$)", p.name)
        if m:
            present.add(int(m.group(1)))
    if not present:
        return 0, False
    best, best_score = (0, False), None
    for lod in model.lods() or [0]:
        for damaged in (False, True):
            ids = {id(p) for p in model.at_lod(lod, damaged)}
            shown = {k for k, p in enumerate(model.pieces) if id(p) in ids and p.triangles}
            score = len(present & shown) - len(present - shown)
            if best_score is None or score > best_score:
                best, best_score = (lod, damaged), score
    return best


def import_gltf(model_record, segments_record, path, lod=None, damaged=None, material_hash=None,
                collision=True):
    """Return (model record, segments record, Report), like import_obj.
    lod/damaged default to what the file records, else a best guess from
    the piece names in it."""
    from . import model_import
    m = mdl.decode(model_record, segments_record)
    joints, notes = read_skeleton(path, m)
    if joints is not None:
        # The new skeleton goes in first: pieces are then read against it (a
        # piece under a new joint belongs to it) and stored in its joint space.
        model_record = with_skeleton(model_record, joints)
        m = mdl.decode(model_record, segments_record)
    pieces, warnings, settings = read(path, m)
    warnings = list(notes) + [w for w in warnings if not w.startswith("joints aren't imported")]
    if lod is None:
        lod, guessed = (settings["lod"], settings["damaged"]) if settings else guess_view(m, pieces)
        if damaged is None:
            damaged = guessed
    report = model_import.Report(warnings=list(warnings))
    return model_import.import_pieces(model_record, segments_record, pieces, lod, bool(damaged),
                                      material_hash, collision, report=report)
