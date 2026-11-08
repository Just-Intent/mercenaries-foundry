"""Model edits as changes only (models/<name>.changes.glb), for sharing.

An edited export from `model --glb` holds the whole game model: every piece's
geometry and its textures. `mod pack` keeps only what the author changed:

- mesh nodes for edited pieces (named p<index>...) and new pieces;
- the node tree (joint names and placements), so pieces, moved joints and
  new joints are placed as in the original file;
- the removed pieces' numbers and the level of detail, in
  asset.extras.mercfoundry.

Embedded textures and the geometry of unchanged pieces are dropped. The build
fills those pieces in from the player's own copy of the model and imports
the result as it would the full file.
"""
import json
import struct

from . import gltf, gltf_anim, gltf_model
from . import model as mdl
from . import model_import

_TEXTURE_SLOTS = ("normalTexture", "occlusionTexture", "emissiveTexture")


def _glb(doc, binary: bytes) -> bytes:
    text = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    text += b" " * (-len(text) % 4)
    binary = bytes(binary) + b"\0" * (-len(binary) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text
    if binary:
        body += struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body


def _node_piece_name(doc, i):
    n = doc["nodes"][i]
    return n.get("name") or doc["meshes"][n["mesh"]].get("name") or f"node{i}"


def _compact(doc, buffers, drop_nodes, keep_channels=None):
    """A copy of the glTF without the given nodes or images, and with only the
    buffer data the rest uses. Animations are dropped, except the channels in
    keep_channels ({animation index: {channel index}}). Returns (doc, binary)."""
    doc = json.loads(json.dumps(doc))
    keep_channels = keep_channels or {}
    nodes = doc.get("nodes", [])
    keep = [i for i in range(len(nodes)) if i not in drop_nodes]
    node_map = {old: new for new, old in enumerate(keep)}
    new_nodes = []
    for old in keep:
        n = nodes[old]
        if "children" in n:
            n["children"] = [node_map[c] for c in n["children"] if c in node_map]
            if not n["children"]:
                del n["children"]
        new_nodes.append(n)
    doc["nodes"] = new_nodes
    for scene in doc.get("scenes", []):
        scene["nodes"] = [node_map[c] for c in scene.get("nodes", []) if c in node_map]
    anims = []
    for ai, anim in enumerate(doc.get("animations", [])):
        chans = [ch for ci, ch in enumerate(anim.get("channels", []))
                 if ci in keep_channels.get(ai, ()) and ch.get("target", {}).get("node") in node_map]
        if not chans:
            continue
        samplers, smap = [], {}
        for ch in chans:
            ch["target"]["node"] = node_map[ch["target"]["node"]]
            if ch["sampler"] not in smap:
                smap[ch["sampler"]] = len(samplers)
                samplers.append(anim["samplers"][ch["sampler"]])
            ch["sampler"] = smap[ch["sampler"]]
        anims.append({**anim, "channels": chans, "samplers": samplers})
    doc.pop("animations", None)

    # Meshes and skins still used, renumbered.
    mesh_map, skin_map = {}, {}
    for n in new_nodes:
        if "mesh" in n:
            n["mesh"] = mesh_map.setdefault(n["mesh"], len(mesh_map))
        if "skin" in n:
            n["skin"] = skin_map.setdefault(n["skin"], len(skin_map))
    meshes = [None] * len(mesh_map)
    for old, new in mesh_map.items():
        meshes[new] = doc["meshes"][old]
    skins = [None] * len(skin_map)
    for old, new in skin_map.items():
        s = doc["skins"][old]
        s["joints"] = [node_map[j] for j in s["joints"]]
        if "skeleton" in s:
            s["skeleton"] = node_map.get(s["skeleton"], s["joints"][0] if s["joints"] else 0)
        skins[new] = s
    doc["meshes"] = meshes
    if skins:
        doc["skins"] = skins
    else:
        doc.pop("skins", None)

    # Materials keep their names (the game texture names), not the images.
    mat_map = {}
    for mesh in meshes:
        for prim in mesh.get("primitives", []):
            if "material" in prim:
                prim["material"] = mat_map.setdefault(prim["material"], len(mat_map))
    mats = [None] * len(mat_map)
    for old, new in mat_map.items():
        m = doc["materials"][old]
        m.get("pbrMetallicRoughness", {}).pop("baseColorTexture", None)
        m.get("pbrMetallicRoughness", {}).pop("metallicRoughnessTexture", None)
        for slot in _TEXTURE_SLOTS:
            m.pop(slot, None)
        mats[new] = m
    if mats:
        doc["materials"] = mats
    else:
        doc.pop("materials", None)
    for key in ("textures", "images", "samplers"):
        doc.pop(key, None)

    # Accessors and buffer data still used.
    acc_map = {}

    def use(a):
        return acc_map.setdefault(a, len(acc_map))

    for mesh in meshes:
        for prim in mesh.get("primitives", []):
            prim["attributes"] = {k: use(v) for k, v in prim.get("attributes", {}).items()}
            if "indices" in prim:
                prim["indices"] = use(prim["indices"])
            prim.pop("targets", None)
    for s in skins:
        if "inverseBindMatrices" in s:
            s["inverseBindMatrices"] = use(s["inverseBindMatrices"])
    for anim in anims:
        for smp in anim["samplers"]:
            smp["input"], smp["output"] = use(smp["input"]), use(smp["output"])
    if anims:
        doc["animations"] = anims
    accessors = [None] * len(acc_map)
    views, view_map, binary = [], {}, bytearray()
    for old, new in acc_map.items():
        a = doc["accessors"][old]
        if "bufferView" in a:
            v_old = a["bufferView"]
            if v_old not in view_map:
                v = doc["bufferViews"][v_old]
                data = buffers[v.get("buffer", 0)]
                start = v.get("byteOffset", 0)
                chunk = data[start:start + v["byteLength"]]
                binary += b"\0" * (-len(binary) % 4)
                nv = {k: val for k, val in v.items() if k not in ("buffer", "byteOffset")}
                nv.update({"buffer": 0, "byteOffset": len(binary)})
                binary += chunk
                view_map[v_old] = len(views)
                views.append(nv)
            a["bufferView"] = view_map[v_old]
        accessors[new] = a
    doc["accessors"] = accessors
    doc["bufferViews"] = views
    doc["buffers"] = [{"byteLength": len(binary)}] if binary else []
    if not binary:
        doc.pop("buffers")
    return doc, bytes(binary)


def make_changes(model_record: bytes, segments_record: bytes, path):
    """(changes .glb bytes, Report) for an edited glTF of this model, or
    (None, Report) when it changes nothing."""
    _, _, report = gltf_model.import_gltf(model_record, segments_record, path)
    m = mdl.decode(model_record, segments_record)
    joints, _ = gltf_model.read_skeleton(path, m)
    if not report.changed and joints is None:
        return None, report
    doc, buffers = gltf_anim.load(path)
    pieces, _, settings = gltf_model.read(path, m)
    if settings:
        lod, damaged = settings["lod"], settings["damaged"]
    else:
        lod, damaged = gltf_model.guess_view(m, pieces)
    edited, added = set(report.edited), set(report.added)
    drop = set()
    for i, n in enumerate(doc.get("nodes", [])):
        if "mesh" not in n:
            continue
        name = _node_piece_name(doc, i)
        idx = model_import._piece_index(name)
        if not (idx in edited if idx is not None else name in added):
            drop.add(i)
    out, binary = _compact(doc, buffers, drop)
    extras = out.setdefault("asset", {}).setdefault("extras", {})
    extras["mercfoundry"] = {**extras.get("mercfoundry", {}), "lod": lod, "damaged": bool(damaged),
                             "changes": {"removed": sorted(report.removed)}}
    return _glb(out, binary), report


def animation_changes(path, game_clip):
    """(.glb bytes or None, {clip: (kept, total channels)}) for an animation
    file: only the clips' channels that change the built animation, the
    joint tree, and no meshes or textures. game_clip(name) returns the game's
    animation record for a clip name, or None for a new clip (kept whole)."""
    from . import animation
    doc, buffers = gltf_anim.load(path)
    nodes = doc.get("nodes", [])
    keep, summary = {}, {}
    for ai, anim in enumerate(doc.get("animations", [])):
        name = gltf_anim.clip_name(anim.get("name", ""))
        chans = anim.get("channels", [])
        usable = [ci for ci, ch in enumerate(chans)
                  if ch.get("target", {}).get("path") in ("rotation", "translation") and "node" in ch.get("target", {})]
        base_rec = game_clip(name)
        if base_rec is None:
            keep[ai] = set(usable)
            summary[name] = (len(usable), len(chans))
            continue
        tracks = gltf_anim.clips(path).get(name, {})
        base = animation.decode(base_rec)
        full = animation.encode(gltf_anim.to_animation(tracks, base), base_rec)
        kept = set(usable)
        for ci in usable:
            tgt = chans[ci]["target"]
            joint, kind = nodes[tgt["node"]].get("name", "").lower(), tgt["path"]
            trial = {j: dict(c) for j, c in tracks.items()}
            trial.get(joint, {}).pop(kind, None)
            if animation.encode(gltf_anim.to_animation(trial, base), base_rec) == full:
                tracks = trial
                kept.discard(ci)
        keep[ai] = kept
        summary[name] = (len(kept), len(chans))
    if not any(keep.values()):
        return None, summary
    drop = {i for i, n in enumerate(nodes) if "mesh" in n}
    out, binary = _compact(doc, buffers, drop, keep)
    return _glb(out, binary), summary


def is_changes(path) -> bool:
    doc, _ = gltf_anim.load(path)
    return "changes" in doc.get("asset", {}).get("extras", {}).get("mercfoundry", {})


def import_changes(model_record: bytes, segments_record: bytes, path, material_hash=None, collision=True):
    """Like gltf_model.import_gltf for a changes file: the pieces it doesn't
    hold come from the game's model."""
    doc, _ = gltf_anim.load(path)
    info = doc.get("asset", {}).get("extras", {}).get("mercfoundry", {})
    lod, damaged = int(info.get("lod", 0)), bool(info.get("damaged"))
    removed = set(info.get("changes", {}).get("removed", []))
    m = mdl.decode(model_record, segments_record)
    joints, notes = gltf_model.read_skeleton(path, m)
    if joints is not None:
        model_record = gltf_model.with_skeleton(model_record, joints)
        m = mdl.decode(model_record, segments_record)
    pieces, warnings, _ = gltf_model.read(path, m)
    replaced = {model_import._piece_index(p.name) for p in pieces} - {None}
    obj_text, _ = mdl.to_obj(m, lod, damaged=damaged)
    game = [p for p in model_import.parse_obj(obj_text)
            if model_import._piece_index(p.name) not in replaced | removed]
    warnings = list(notes) + [w for w in warnings if not w.startswith("joints aren't imported")]
    report = model_import.Report(warnings=warnings)
    return model_import.import_pieces(model_record, segments_record, game + pieces, lod, damaged,
                                      material_hash, collision, report=report)
