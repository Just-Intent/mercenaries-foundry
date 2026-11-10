"""Structural and geometric checks for exported .glb files.

Walks the node tree like a viewer would and compares every vertex with the
model decoder's model-space positions (turned half round, as the export). Used by tests and by
`model --glb --check`.
"""
import math
import struct

from . import gltf

SIZES = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
FORMATS = {5126: "f", 5123: "H", 5125: "I", 5121: "B"}


def _accessor(doc, binary, index):
    a = doc["accessors"][index]
    v = doc["bufferViews"][a["bufferView"]]
    n = a["count"] * SIZES[a["type"]]
    fmt = FORMATS[a["componentType"]]
    size = struct.calcsize(fmt) * n
    if v["byteOffset"] + size > len(binary) or size > v["byteLength"]:
        raise ValueError(f"accessor {index} runs past its buffer")
    values = struct.unpack_from(f"<{n}{fmt}", binary, v["byteOffset"])
    k = SIZES[a["type"]]
    return [values[i:i + k] for i in range(0, n, k)]


def _mul(a, b):
    """4x4 column-major multiply."""
    return [sum(a[r + 4 * k] * b[k + 4 * c] for k in range(4)) for c in range(4) for r in range(4)]


def _node_matrix(node):
    if "matrix" in node:
        return node["matrix"]
    return gltf._matrix(tuple(node.get("rotation", (0, 0, 0, 1))), tuple(node.get("translation", (0, 0, 0))))


def _apply(m, p):
    return tuple(m[r] * p[0] + m[4 + r] * p[1] + m[8 + r] * p[2] + m[12 + r] for r in range(3))


def check(glb: bytes, model, lod=0, damaged=False) -> dict:
    """Raise ValueError on any mismatch; return a summary on success."""
    doc, binary = gltf.read(glb)
    world = {}

    def walk(i, parent):
        m = _mul(parent, _node_matrix(doc["nodes"][i]))
        world[i] = m
        for c in doc["nodes"][i].get("children", []):
            walk(c, m)

    identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    for i in doc["scenes"][0]["nodes"]:
        walk(i, identity)
    if len(world) != len(doc["nodes"]):
        raise ValueError("some nodes are not reachable from the scene")

    for skin in doc.get("skins", []):
        ibm = _accessor(doc, binary, skin["inverseBindMatrices"])
        for joint, inv in zip(skin["joints"], ibm):
            prod = _mul(world[joint], list(inv))
            if max(abs(prod[k] - identity[k]) for k in range(16)) > 1e-4:
                raise ValueError(f"inverse bind matrix of node {joint} doesn't undo its pose")

    pieces = {mdl_name: p for mdl_name, p in _pieces(model, lod, damaged)}
    worst, vertices = 0.0, 0
    for i, node in enumerate(doc["nodes"]):
        if "mesh" not in node:
            continue
        piece = pieces.pop(node["name"], None)
        if piece is None:
            raise ValueError(f"mesh node {node['name']} has no matching piece")
        prim = doc["meshes"][node["mesh"]]["primitives"][0]
        positions = _accessor(doc, binary, prim["attributes"]["POSITION"])
        indices = [i for (i,) in _accessor(doc, binary, prim["indices"])]
        if len(positions) != len(piece.positions) or len(indices) != 3 * len(piece.triangles):
            raise ValueError(f"{node['name']}: vertex or triangle count differs")
        if max(indices, default=0) >= len(positions):
            raise ValueError(f"{node['name']}: index out of range")
        m = identity if "skin" in node else world[i]
        for got, want in zip(positions, piece.positions):
            g = _apply(m, got)
            worst = max(worst, math.dist(g, gltf._flip_point(want)))
        vertices += len(positions)
    if pieces:
        raise ValueError(f"pieces missing from the file: {', '.join(sorted(pieces))[:200]}")
    if worst > 1e-3:
        raise ValueError(f"vertices are misplaced by up to {worst:.4f}")
    return {"nodes": len(doc["nodes"]), "meshes": len(doc.get("meshes", [])),
            "skins": len(doc.get("skins", [])), "vertices": vertices, "worst_error": worst}


def _pieces(model, lod, damaged):
    from . import model as mdl
    chosen = {id(p) for p in model.at_lod(lod, damaged)}
    for index, p in enumerate(model.pieces):
        if id(p) in chosen and p.triangles:
            yield mdl.piece_object_name(model, index), p
