"""Read animation clips from glTF (.glb or .gltf) back into game animations.

Clips are matched to game animations by name (Blender's "Armature|" prefix
and ".001" suffixes are ignored). Each clip is resampled to the game's 30
frames per second and converted back to the game's left-handed axes. A
joint the clip doesn't animate keeps the original animation's motion
(stretched to the new length), so root motion survives an `--in-place`
export.
"""
import base64
import json
import math
import re
import struct
from pathlib import Path

from . import animation, gltf

COMPONENT = {5126: ("f", 4), 5123: ("H", 2), 5125: ("I", 4), 5121: ("B", 1), 5122: ("h", 2), 5120: ("b", 1)}
NORMALISE = {5121: 255.0, 5123: 65535.0, 5120: 127.0, 5122: 32767.0}
SIZES = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def load(path):
    """(json document, [buffer bytes]) for a .glb or .gltf file."""
    path = Path(path)
    raw = path.read_bytes()
    if raw[:4] == b"glTF":
        doc, binary = gltf.read(raw)
        buffers = [binary]
    else:
        doc = json.loads(raw.decode("utf-8"))
        buffers = []
        for b in doc.get("buffers", []):
            uri = b.get("uri", "")
            if uri.startswith("data:"):
                buffers.append(base64.b64decode(uri.split(",", 1)[1]))
            else:
                buffers.append((path.parent / uri).read_bytes())
    return doc, buffers


def _accessor(doc, buffers, index):
    a = doc["accessors"][index]
    view = doc["bufferViews"][a["bufferView"]]
    fmt, size = COMPONENT[a["componentType"]]
    n = SIZES[a["type"]]
    stride = view.get("byteStride") or size * n
    base = view.get("byteOffset", 0) + a.get("byteOffset", 0)
    data = buffers[view.get("buffer", 0)]
    out = []
    for i in range(a["count"]):
        vals = struct.unpack_from(f"<{n}{fmt}", data, base + i * stride)
        if a.get("normalized") and a["componentType"] in NORMALISE:
            vals = tuple(max(v / NORMALISE[a["componentType"]], -1.0) for v in vals)
        out.append(vals)
    return out


def clip_name(name):
    name = name.rsplit("|", 1)[-1]
    return re.sub(r"\.\d{3}$", "", name)


def _sample(times, values, t, interp, rotation):
    if t <= times[0]:
        return values[0]
    if t >= times[-1]:
        return values[-1]
    lo, hi = 0, len(times) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if times[mid] <= t:
            lo = mid
        else:
            hi = mid
    if interp == "STEP" or times[hi] == times[lo]:
        return values[lo]
    f = (t - times[lo]) / (times[hi] - times[lo])
    a, b = values[lo], values[hi]
    if rotation and sum(x * y for x, y in zip(a, b)) < 0:
        b = tuple(-x for x in b)
    return tuple(x + (y - x) * f for x, y in zip(a, b))


def _normalise(q):
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return tuple(c / n for c in q)


def clips(path):
    """{clip name: {joint name (lower): {"rotation"|"translation": (times, values, interp)}}}"""
    doc, buffers = load(path)
    nodes = doc.get("nodes", [])
    out = {}
    for anim in doc.get("animations", []):
        tracks = {}
        for ch in anim.get("channels", []):
            target = ch.get("target", {})
            path_ = target.get("path")
            if path_ not in ("rotation", "translation") or "node" not in target:
                continue
            node_name = nodes[target["node"]].get("name", "").lower()
            sampler = anim["samplers"][ch["sampler"]]
            times = [t[0] for t in _accessor(doc, buffers, sampler["input"])]
            values = _accessor(doc, buffers, sampler["output"])
            interp = sampler.get("interpolation", "LINEAR")
            if interp == "CUBICSPLINE":  # (in-tangent, value, out-tangent) per key
                values = values[1::3]
                interp = "LINEAR"
            tracks.setdefault(node_name, {})[path_] = (times, values, interp)
        out[clip_name(anim.get("name", ""))] = tracks
    return out


def _stretch(seq, frames):
    """Resample a per-frame list to a new frame count."""
    if len(seq) == frames:
        return list(seq)
    if len(seq) == 1:
        return list(seq) * frames
    out = []
    for f in range(frames):
        x = f * (len(seq) - 1) / max(frames - 1, 1)
        i = min(int(x), len(seq) - 2)
        t = x - i
        out.append(tuple(a + (b - a) * t for a, b in zip(seq[i], seq[i + 1])))
    return out


def to_animation(tracks, base: animation.Animation) -> animation.Animation:
    """Build a game animation from one clip, using `base` for anything the
    clip doesn't cover."""
    duration = 0.0
    for chans in tracks.values():
        for times, _, _ in chans.values():
            if times:
                duration = max(duration, times[-1])
    frames = max(1, round(duration * animation.FPS) + 1) if duration > 0 else base.frames
    times = [f / animation.FPS for f in range(frames)]
    new = animation.Animation(base.name, frames, base.scale)
    for t in base.tracks:
        chans = tracks.get(t.joint.lower(), {})
        kept = [False] * frames
        if "rotation" in chans:
            ts, vs, interp = chans["rotation"]
            rots = [_normalise(gltf._flip_quat(_sample(ts, vs, x, interp, True))) for x in times]
            if frames == base.frames:
                # Frames left as exported keep the game's exact stored values
                # (they aren't quite unit length, so normalising shifts them).
                for f, q in enumerate(rots):
                    orig = t.rotations[f]
                    ref = _normalise(orig)
                    if min(max(abs(a - b) for a, b in zip(q, ref)),
                           max(abs(a + b) for a, b in zip(q, ref))) < 2e-4:
                        rots[f], kept[f] = orig, True
        elif frames == base.frames:
            rots, kept = list(t.rotations), [True] * frames
        else:
            rots = [_normalise(q) for q in _stretch(t.rotations, frames)]
        for i in range(1, len(rots)):  # keep sign continuity for new values
            if not kept[i] and sum(a * b for a, b in zip(rots[i], rots[i - 1])) < 0:
                rots[i] = tuple(-c for c in rots[i])
        track = animation.Track(t.joint, rots, dxlt=t.dxlt)
        if "translation" in chans:
            ts, vs, interp = chans["translation"]
            pts = [gltf._flip_point(_sample(ts, vs, x, interp, False)) for x in times]
            if t.translations is None and all(max(abs(a - b) for a, b in zip(p, pts[0])) < 1e-5 for p in pts):
                track.translation = pts[0]
            else:
                track.translations = pts
        elif t.translations:
            track.translations = _stretch(t.translations, frames)
        else:
            track.translation = t.translation
        new.tracks.append(track)
    return new
