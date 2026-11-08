"""Ground painting: terrain texture layers as greyscale images.

A terrain's ground is up to 16 texture layers (LTEX, with a scale in SCAL and
a projection in AXIS).  Each 8 x 8-cell patch lists the layers it uses (PTCH
mask) and, when it uses two or more, a 9 x 9 grid of nibbles per layer (ALPH):
bits 0-2 opacity (ALPHA_TABLE), bit 3 "draw".  Layers are drawn bottom to top.

Draw bits, from the patch builder (0x21CB20): the game draws cell (j, i) of a
pass of up to four layers if any of them has the draw bit at the cell's
top-left vertex (j, i); bits in the ninth row and column aren't read.  When we
encode a patch we set a layer's bit for a cell if any corner of the cell has
some opacity and no single higher layer is fully opaque at all four corners.
On the retail maps that never leaves out a cell the game's own data draws
(retail data draws some extra cells, which only costs fill).

Images are (patches * 8 + 1) pixels square: pixel (X, Y) is the vertex at
height sample (X, Y) (the last row and column repeat the far edge), the same
orientation as the heights PNG.  Value = the layer's opacity, 0..255.
"""

import re
import struct

from . import terrain_layers as tl

MAX_LAYERS = 16


def layer_file(name, index, texture):
    return f"{name}.layer{index:02d}.{texture}.png" if texture else f"{name}.layer{index:02d}.png"


def parse_layer_file(filename):
    """(terrain name, layer index) from 'swn.layer03.sw_tt_stone.png', or None."""
    m = re.fullmatch(r"(.+?)\.layer(\d\d)(?:\.[^.]*)?\.png", filename, re.IGNORECASE)
    return (m.group(1), int(m.group(2))) if m else None


def _side(chunks):
    size, patch = struct.unpack_from("<hh", chunks[b"INFO"], 16)
    if patch != tl.PATCH:
        raise ValueError("terrain patches are not 8 cells")
    return size // patch


def layer_count(chunks):
    return struct.unpack_from("<h", chunks[b"INFO"], 22)[0]


def layer_table(chunks):
    """[{texture, scale, axis}] for the map's layers."""
    n = layer_count(chunks)
    names = tl.decode_names(chunks.get(b"LTEX", b""), n)
    scale = tl.decode_floats(chunks.get(b"SCAL", b""))
    axis = tl.decode_axis(chunks.get(b"AXIS", b""))
    return [{"texture": names[k], "scale": scale[k] if k < len(scale) else 16.0,
             "axis": axis[k] if k < len(axis) else 0} for k in range(n)]


def _owner(v, side):
    """(patch, index) that a vertex row/column v takes its value from."""
    p = min(v // tl.PATCH, side - 1)
    return p, v - p * tl.PATCH


def layer_images(chunks):
    """(width, {layer: bytes}) opacity images for every layer of the map."""
    side = _side(chunks)
    w = side * tl.PATCH + 1
    patches = tl.decode_ptch(chunks[b"PTCH"])
    alpha = tl.decode_alph(chunks.get(b"ALPH", b""), patches)
    images = {l: bytearray(w * w) for l in range(layer_count(chunks))}
    owners = [_owner(v, side) for v in range(w)]
    for Y in range(w):
        py, j = owners[Y]
        for X in range(w):
            px, i = owners[X]
            grids = alpha[py * side + px]
            n = j * tl.VERTS + i
            for l, g in grids.items():
                if l in images:
                    images[l][Y * w + X] = tl.ALPHA_TABLE[g[n] & 7]
    return w, {l: bytes(img) for l, img in images.items()}


def _level(v):
    return min(range(8), key=lambda k: abs(tl.ALPHA_TABLE[k] - v))


LEVEL = [_level(v) for v in range(256)]


def encode_patch(windows, layers):
    """{layer: 81 nibbles} for one patch from per-layer 9 x 9 opacity windows."""
    q = {l: [LEVEL[v] for v in windows[l]] for l in layers}
    out = {}
    for k, l in enumerate(layers):
        above = layers[k + 1:]
        grid = []
        for j in range(tl.VERTS):
            for i in range(tl.VERTS):
                a = q[l][j * tl.VERTS + i]
                if j < tl.PATCH and i < tl.PATCH:
                    corners = [(j + dj) * tl.VERTS + i + di for dj in (0, 1) for di in (0, 1)]
                    covered = any(all(q[h][c] == 7 for c in corners) for h in above)
                    draw = any(q[l][c] for c in corners) and not covered
                else:
                    draw = a > 0
                grid.append(a | (8 if draw else 0))
        out[l] = grid
    return out


def _alph_blobs(data, patches):
    """Original ALPH blob bytes, by patch index (multi-layer patches only)."""
    out, pos = {}, 0
    for n, p in enumerate(patches):
        if len(p.layers) > 1:
            _, end = tl.read_blob(data, pos)
            out[n] = data[pos:end]
            pos = end
    return out


def apply(chunks, images=None, table=None):
    """New tern chunk bodies for painted layers.

    images: {layer: bytes (w * w opacity)} replacing those layers' images
    (others keep theirs).  table: a new layer_table() list (textures, scale,
    axis; may add layers up to 16).  Returns (replace, patches changed)."""
    images = images or {}
    side = _side(chunks)
    w = side * tl.PATCH + 1
    replace = {}
    count = layer_count(chunks)
    if table is not None and [dict(t) for t in table] != layer_table(chunks):
        if not 1 <= len(table) <= MAX_LAYERS:
            raise ValueError(f"a terrain has 1 to {MAX_LAYERS} layers, not {len(table)}")
        if len(table) < count:
            raise ValueError("layers can't be removed (paint them out instead)")
        for t in table:
            if not 0 <= int(t["axis"]) < len(tl.AXIS_TABLE):
                raise ValueError(f"layer {t['texture']}: axis must be 0..{len(tl.AXIS_TABLE) - 1}")
        new_count = len(table)
        scale = tl.decode_floats(chunks.get(b"SCAL", b""))
        axis = tl.decode_axis(chunks.get(b"AXIS", b""))
        scale += [16.0] * (new_count - len(scale))
        axis += [0] * (new_count - len(axis))
        for k, t in enumerate(table):
            scale[k], axis[k] = float(t["scale"]), int(t["axis"])
        names = tl.decode_names(chunks.get(b"LTEX", b""))     # may hold spare names past the count
        names += [""] * (new_count - len(names))
        for k, t in enumerate(table):
            names[k] = t["texture"]
        new = {b"LTEX": tl.encode_names(names),
               b"SCAL": tl.encode_floats(scale), b"AXIS": tl.encode_axis(axis)}
        if new_count != count:
            info = bytearray(chunks[b"INFO"])
            struct.pack_into("<h", info, 22, new_count)
            new[b"INFO"] = bytes(info)
        for tag, body in new.items():
            if chunks.get(tag) != body:
                replace[tag] = body
        count = new_count
    for l, img in images.items():
        if not 0 <= l < count:
            raise ValueError(f"layer {l} doesn't exist (the map has {count}; add it to the layer table first)")
        if len(img) != w * w:
            raise ValueError(f"layer images must be {w} x {w} pixels")
    if not images:
        return replace, 0
    _, old = layer_images(chunks)
    patches = tl.decode_ptch(chunks[b"PTCH"])
    blobs = _alph_blobs(chunks.get(b"ALPH", b""), patches)
    alph, new_patches, changed = bytearray(), [], 0
    for py in range(side):
        for px in range(side):
            n = py * side + px
            p = patches[n]

            def window(img):
                return [img[(py * tl.PATCH + j) * w + px * tl.PATCH + i]
                        for j in range(tl.VERTS) for i in range(tl.VERTS)]

            if not any(window(img) != window(old.get(l, bytes(w * w))) for l, img in images.items()):
                new_patches.append(p)
                if n in blobs:
                    alph += blobs[n]
                continue
            changed += 1
            windows = {l: window(images[l] if l in images else old.get(l, bytes(w * w))) for l in range(count)}
            layers = [l for l in range(count) if any(LEVEL[v] for v in windows[l])]
            mask = sum(1 << l for l in layers)
            new_patches.append(tl.Patch(mask, p.decal_count))
            if len(layers) > 1:
                grids = encode_patch(windows, layers)
                alph += tl.pack_blob(b"".join(tl._pack_layer(grids[l]) for l in layers))
    if changed:
        replace[b"PTCH"] = tl.encode_ptch(new_patches)
        replace[b"ALPH"] = bytes(alph)
    return replace, changed
