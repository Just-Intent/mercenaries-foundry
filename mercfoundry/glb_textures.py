"""The textures a model's glTF file carries, as game textures.

A model's materials name its textures (gltf_model: "materials are texture
names", as in an export from `model --glb`). A material whose name is a
texture the game has keeps it. Any other material, as a model made in
Blender has, becomes a new texture named <model>_<material>:

- its base colour image (baseColorTexture), embedded or beside a .gltf,
  scaled to power-of-two sides (at most 1024, as the game's textures are) and
  tinted by its base colour factor;
- or, with no image, a small swatch of its base colour factor.

PNG images are decoded here; JPEG isn't (no decoder in the toolkit), and
such a material gets its base colour instead, with a warning.
"""
import base64
import re
from pathlib import Path

from . import gltf_anim, png
from .hashing import name_hash

MAX_SIDE = 1024


def _pow2(n):
    p = 1
    while p * 2 <= n:
        p *= 2
    return min(p if p * 2 - n > n - p else p * 2, MAX_SIDE)


def resize(w, h, rgba, nw, nh):
    """RGBA pixels scaled to nw x nh: each new pixel the average of the old
    pixels it covers (or the nearest one, scaling up)."""
    if (w, h) == (nw, nh):
        return rgba
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        y0, y1 = y * h // nh, max(y * h // nh + 1, (y + 1) * h // nh)
        for x in range(nw):
            x0, x1 = x * w // nw, max(x * w // nw + 1, (x + 1) * w // nw)
            acc = [0, 0, 0, 0]
            n = 0
            for yy in range(y0, y1):
                row = yy * w
                for xx in range(x0, x1):
                    i = (row + xx) * 4
                    acc[0] += rgba[i]
                    acc[1] += rgba[i + 1]
                    acc[2] += rgba[i + 2]
                    acc[3] += rgba[i + 3]
                    n += 1
            o = (y * nw + x) * 4
            out[o:o + 4] = bytes(v // n for v in acc)
    return bytes(out)


def _image_bytes(doc, buffers, path, image):
    if "bufferView" in image:
        view = doc["bufferViews"][image["bufferView"]]
        data = buffers[view.get("buffer", 0)]
        start = view.get("byteOffset", 0)
        return data[start:start + view["byteLength"]]
    uri = image.get("uri", "")
    if uri.startswith("data:"):
        return base64.b64decode(uri.split(",", 1)[1])
    return (Path(path).parent / uri).read_bytes() if uri else b""


def slug(name):
    return re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_").lower() or "material"


def textures(path, model_name, game_has):
    """[(material name, texture name, width, height, rgba)] for the materials
    the file's meshes use that aren't the game's textures, and warnings.
    game_has(hash): whether the game has a texture with that name hash."""
    doc, buffers = gltf_anim.load(path)
    mats = doc.get("materials") or []
    used = set()
    for mesh in doc.get("meshes") or []:
        for prim in mesh.get("primitives") or []:
            if "material" in prim:
                used.add(prim["material"])
    out, warnings, taken = [], [], set()
    for i in sorted(used):
        m = mats[i]
        name = m.get("name", "")
        stem = re.sub(r"\.\d{3}$", "", name)
        if name and (game_has(name_hash(stem)) or (re.fullmatch(r"[0-9A-Fa-f]{8}", stem) and game_has(int(stem, 16)))):
            continue                                       # the game's own texture (an exported model's)
        if re.fullmatch(r"[0-9A-Fa-f]{8}", stem):
            continue                                       # an exported texture key the game lacks (or 0): kept as it is
        if not name:
            warnings.append(f"material {i} has no name: its pieces take the base model's first texture")
            continue
        pbr = m.get("pbrMetallicRoughness") or {}
        factor = [max(0.0, min(1.0, float(v))) for v in (pbr.get("baseColorFactor") or [1, 1, 1, 1])]
        tex_name = f"{model_name}_{slug(name)}"
        k = 2
        while tex_name.lower() in taken:
            tex_name = f"{model_name}_{slug(name)}_{k}"
            k += 1
        taken.add(tex_name.lower())
        rgba = None
        ref = pbr.get("baseColorTexture")
        if ref is not None:
            image = doc["images"][doc["textures"][ref["index"]]["source"]]
            data = _image_bytes(doc, buffers, path, image)
            if data[:8] == b"\x89PNG\r\n\x1a\n":
                w, h, rgba = png.decode(data)
                nw, nh = _pow2(w), _pow2(h)
                rgba = resize(w, h, rgba, nw, nh)
                w, h = nw, nh
                if factor != [1.0, 1.0, 1.0, 1.0]:
                    tint = [round(f * 255) for f in factor]
                    rgba = bytes(rgba[j] * tint[j % 4] // 255 for j in range(len(rgba)))
            else:
                warnings.append(f"material {name}: its image isn't a PNG (JPEG can't be read here): "
                                f"it gets its base colour; export the image as PNG to keep it")
        if rgba is None:
            w = h = 8
            rgba = bytes(round(f * 255) for f in factor) * (w * h)
        out.append((name, tex_name, w, h, rgba))
    return out, warnings


def material_hash(made):
    """The import's material -> texture hash, for the materials made into
    new textures (made: textures()' list); others as the import does."""
    by_name = {}
    for material, tex_name, *_ in made:
        by_name[material] = name_hash(tex_name)
        by_name[re.sub(r"\.\d{3}$", "", material)] = name_hash(tex_name)

    def lookup(n):
        if n in by_name:
            return by_name[n]
        n = re.sub(r"\.\d{3}$", "", n)
        return int(n, 16) if re.fullmatch(r"[0-9A-Fa-f]{8}", n) else name_hash(n)
    return lookup
