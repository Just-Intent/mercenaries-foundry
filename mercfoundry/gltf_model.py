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
- Joints aren't imported: the game skeleton stays as it is. A warning lists
  joints that were moved.

Positions go back to the game's left-handed axes (Z negated) and triangle
winding is reversed, undoing the export.
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


def _flip(p):
    return (p[0], p[1], -p[2])


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
                piece.triangles.append((a, c, b))  # undo the export's winding flip
            if piece.triangles:
                pieces.append(piece)
    settings = {}
    extras = doc.get("asset", {}).get("extras", {}).get("mercfoundry", {})
    if "lod" in extras:
        settings = {"lod": int(extras["lod"]), "damaged": bool(extras.get("damaged"))}
    return pieces, warnings, settings


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
    pieces, warnings, settings = read(path, m)
    if lod is None:
        lod, guessed = (settings["lod"], settings["damaged"]) if settings else guess_view(m, pieces)
        if damaged is None:
            damaged = guessed
    report = model_import.Report(warnings=list(warnings))
    return model_import.import_pieces(model_record, segments_record, pieces, lod, bool(damaged),
                                      material_hash, collision, report=report)
