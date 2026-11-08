"""Terrain surface chunks: PTCH, COLR, ALPH, DCAL, LTEX, DTEX, SCAL, AXIS, ROTN.

These are children of the ``tern`` chunk in a Terrain record (the height chunks
INFO/HEXP/HGT8 live in terrain.py).  Everything here was read from the retail
loader ``sub_001F07A0`` (RedTerrain.cpp) and the patch builder ``sub_0021D650``
(xboxRedTerrain.cpp); see the notes on each function.

Layout reminders (from INFO): the map is ``size`` x ``size`` cells (512, or 128
for shl) split into ``size // patch`` square patches per side (patch = 8 cells),
so 64 x 64 = 4096 patches.  A patch has (patch + 1) x (patch + 1) = 9 x 9
vertices; per-patch grids below are 81 values, row-major, index ``j * 9 + i``.
Patches are stored row-major too: patch index ``py * patches_per_side + px``.
Neighbouring patches repeat their shared edge vertices.
"""
import struct
from dataclasses import dataclass, field

PATCH = 8                 # cells per patch side (INFO u16[1]); the formats assume it
VERTS = PATCH + 1         # 9 vertices per patch side
VERTS2 = VERTS * VERTS    # 81
ALPHA_ROW = PATCH // 2 + 1                 # 5 bytes per alpha row (10 nibbles, 9 used)
ALPHA_LAYER = ALPHA_ROW * VERTS            # 45 bytes per layer per patch
COLR_PATCH = VERTS2 * 2                    # 162 bytes per patch

# nibble & 7 -> 8-bit alpha (byte table at VA 0x2FC85C, used by 0x21CB89)
ALPHA_TABLE = (0, 36, 72, 109, 145, 182, 218, 255)

# AXIS value -> texture projection (int table at VA 0x30EEB0, 24 bytes per
# entry, read by 0x21C856).  Each entry is (U row, V row): the layer texture
# coordinate is the dot product of the row with the vertex (x, height, z).
AXIS_TABLE = (
    ((1, 0, 0), (0, 0, 1)), ((1, 0, 0), (0, 1, 0)), ((0, 1, 0), (0, 0, 1)),
    ((0, 0, 1), (1, 0, 0)), ((0, 1, 0), (1, 0, 0)), ((0, 0, 1), (0, 1, 0)),
    ((-1, 0, 0), (0, 0, 1)), ((-1, 0, 0), (0, 1, 0)), ((0, -1, 0), (0, 0, 1)),
    ((0, 0, -1), (1, 0, 0)), ((0, -1, 0), (1, 0, 0)), ((0, 0, -1), (0, 1, 0)),
)


# ---------------------------------------------------------------- compression

def lz_decompress(src, pos=0, end=None):
    """Decode the per-patch LZ stream (sub_0021A750).  Returns (data, end_pos).

    Control bits come from 16-bit little-endian words, least significant bit
    first; a new word is read as soon as the 16th bit of the previous one is
    used (so it sits in the stream right there).
      1          literal byte
      0 0 a b    short match: length 2*a+b+3 (3..6), next byte d: distance 256-d
      0 1        long match: bytes b0 b1: distance 4096 - (b0 | (b1 >> 4) << 8),
                 length (b1 & 15) + 3; if b1 & 15 == 0 a further byte n follows:
                 n == 0 ends the stream, else length n + 1.
    Matches copy byte by byte from the output (overlap allowed).
    """
    out = bytearray()
    flags = src[pos] | src[pos + 1] << 8
    pos += 2
    left = 16

    def bit():
        nonlocal flags, left, pos
        b = flags & 1
        flags >>= 1
        left -= 1
        if left == 0:
            flags = src[pos] | src[pos + 1] << 8
            pos += 2
            left = 16
        return b

    while True:
        if bit():
            out.append(src[pos])
            pos += 1
            continue
        if bit() == 0:
            a = bit()
            length = a * 2 + bit() + 3
            dist = 256 - src[pos]
            pos += 1
        else:
            b0, b1 = src[pos], src[pos + 1]
            pos += 2
            dist = 4096 - (b0 | (b1 >> 4) << 8)
            length = (b1 & 15) + 3
            if length == 3:
                n = src[pos]
                pos += 1
                if n == 0:
                    break
                length = n + 1
        start = len(out) - dist
        if start < 0:
            raise ValueError("LZ match reaches before the start of the output")
        for k in range(length):
            out.append(out[start + k])
    if end is not None and pos != end:
        raise ValueError("LZ stream ended at %d, expected %d" % (pos, end))
    return bytes(out), pos


class _BitWriter:
    """Mirror of the decoder's control-word placement."""

    def __init__(self):
        self.out = bytearray(b"\0\0")
        self.word_at = 0
        self.nbits = 0

    def bit(self, b):
        if b:
            self.out[self.word_at + (self.nbits >> 3)] |= 1 << (self.nbits & 7)
        self.nbits += 1
        if self.nbits == 16:          # decoder reads the next word immediately
            self.word_at = len(self.out)
            self.out += b"\0\0"
            self.nbits = 0


def lz_compress(data):
    """Greedy encoder producing a stream lz_decompress accepts."""
    data = bytes(data)
    w = _BitWriter()
    n = len(data)
    heads = {}
    i = 0
    while i < n:
        best_len, best_dist = 0, 0
        if i + 3 <= n:
            key = data[i:i + 3]
            for j in reversed(heads.get(key, ())):
                d = i - j
                if d > 4096:
                    break
                m = 3
                while m < 256 and i + m < n and data[j + m] == data[i + m]:
                    m += 1
                if m > best_len:
                    best_len, best_dist = m, d
                    if m == 256:
                        break
        if best_len >= 3:
            if best_len <= 6 and best_dist <= 256:
                w.bit(0); w.bit(0)
                k = best_len - 3
                w.bit(k >> 1); w.bit(k & 1)
                w.out.append(256 - best_dist)
            else:
                w.bit(0); w.bit(1)
                off = 4096 - best_dist
                if 4 <= best_len <= 18:   # nibble 0 means 'length in next byte'
                    w.out += bytes((off & 255, (off >> 8) << 4 | (best_len - 3)))
                else:
                    w.out += bytes((off & 255, (off >> 8) << 4, best_len - 1))
            step = best_len
        else:
            w.bit(1)
            w.out.append(data[i])
            step = 1
        for k in range(i, min(i + step, n - 2)):
            heads.setdefault(data[k:k + 3], []).append(k)
        i += step
    w.bit(0); w.bit(1)
    w.out += b"\0\0\0"                # long match, length nibble 0, n = 0: end
    return bytes(w.out)


def read_blob(buf, pos):
    """One per-patch blob: u16 stored size, u16 unpacked size, stored bytes.

    If stored < unpacked the bytes are LZ data, else the first ``unpacked``
    bytes are used raw (0x21D750 / 0x21D7B3).  The loader steps by stored + 4
    (0x1F0A2D, 0x1F1272).  Returns (data, next_pos).
    """
    stored, size = struct.unpack_from("<HH", buf, pos)
    body = pos + 4
    if stored < size:
        data, _ = lz_decompress(buf, body, body + stored)
        if len(data) != size:
            raise ValueError("blob at %d unpacked to %d bytes, header says %d" % (pos, len(data), size))
    else:
        data = bytes(buf[body:body + size])
    return data, body + stored


def pack_blob(data, compress=True):
    data = bytes(data)
    if compress:
        packed = lz_compress(data)
        if len(packed) < len(data):
            return struct.pack("<HH", len(packed), len(data)) + packed
    return struct.pack("<HH", len(data), len(data)) + data


# ---------------------------------------------------------------- PTCH

@dataclass
class Patch:
    layer_mask: int     # bit n set: LTEX layer n is used in this patch
    decal_count: int    # number of DCAL records belonging to this patch

    @property
    def layers(self):
        return [n for n in range(16) if self.layer_mask >> n & 1]


def decode_ptch(data):
    """PTCH: per patch u16 layer mask, u16 decal count (0x1F08B0)."""
    vals = struct.unpack("<%dH" % (len(data) // 2), data)
    return [Patch(vals[k], vals[k + 1]) for k in range(0, len(vals), 2)]


def encode_ptch(patches):
    return b"".join(struct.pack("<HH", p.layer_mask, p.decal_count) for p in patches)


# ---------------------------------------------------------------- COLR

def decode_colr(data, patch_count):
    """COLR: one blob per patch, 81 u16 vertex colours (X1R5G5B5)."""
    out, pos = [], 0
    for _ in range(patch_count):
        raw, pos = read_blob(data, pos)
        if len(raw) != COLR_PATCH:
            raise ValueError("COLR patch unpacked to %d bytes" % len(raw))
        out.append(list(struct.unpack("<81H", raw)))
    if pos != len(data):
        raise ValueError("COLR has %d trailing bytes" % (len(data) - pos))
    return out


def encode_colr(patch_colours, compress=True):
    return b"".join(pack_blob(struct.pack("<81H", *c), compress) for c in patch_colours)


def colr_rgb(v):
    """X1R5G5B5 -> (r, g, b) the way the game widens it (x << 3, 0x1F284B)."""
    return ((v >> 10) & 31) << 3, ((v >> 5) & 31) << 3, (v & 31) << 3


def rgb_colr(r, g, b):
    return 0x8000 | (r >> 3) << 10 | (g >> 3) << 5 | b >> 3


# ---------------------------------------------------------------- ALPH

def _unpack_layer(blk):
    grid, pads = [], []
    for j in range(VERTS):
        row = int.from_bytes(blk[j * ALPHA_ROW:(j + 1) * ALPHA_ROW], "little")
        grid.extend((row >> 4 * i) & 15 for i in range(VERTS))
        pads.append(row >> 4 * VERTS)
    return grid, pads


def _pack_layer(grid, pads=None):
    out = bytearray()
    for j in range(VERTS):
        row = 0
        for i in range(VERTS):
            row |= (grid[j * VERTS + i] & 15) << 4 * i
        row |= ((pads[j] if pads else grid[j * VERTS + VERTS - 1]) & 15) << 4 * VERTS
        out += row.to_bytes(ALPHA_ROW, "little")
    return bytes(out)


def decode_alph(data, patches):
    """ALPH: one blob per patch whose mask has two or more layers.

    Returns, per patch, {layer: 81 nibbles}.  Layers appear in ascending bit
    order.  A patch with a single layer has no blob; the game fills its alpha
    with 0xFF (0x21D773), i.e. nibble 15 everywhere, which is what is returned.
    Nibble: bit 3 = draw flag (0x21CB42), bits 0-2 = alpha (ALPHA_TABLE).
    """
    out, pos = [], 0
    for p in patches:
        layers = p.layers
        if len(layers) > 1:
            raw, pos = read_blob(data, pos)
            if len(raw) != ALPHA_LAYER * len(layers):
                raise ValueError("ALPH blob is %d bytes for %d layers" % (len(raw), len(layers)))
        else:
            raw = b"\xff" * (ALPHA_LAYER * len(layers))
        out.append({l: _unpack_layer(raw[k * ALPHA_LAYER:(k + 1) * ALPHA_LAYER])[0]
                    for k, l in enumerate(layers)})
    if pos != len(data):
        raise ValueError("ALPH has %d trailing bytes" % (len(data) - pos))
    return out


def encode_alph(patch_layers, patches, compress=True):
    """Inverse of decode_alph.  The unused tenth nibble of each row (ignored by
    the game; retail data usually repeats the ninth or holds 0) is written as a
    copy of the ninth."""
    out = bytearray()
    for grids, p in zip(patch_layers, patches):
        layers = p.layers
        if len(layers) > 1:
            out += pack_blob(b"".join(_pack_layer(grids[l]) for l in layers), compress)
    return bytes(out)


def nibble(alpha, draw=True):
    """8-bit alpha -> ALPH nibble (nearest of the eight levels)."""
    return (8 if draw else 0) | min(range(8), key=lambda k: abs(ALPHA_TABLE[k] - alpha))


# ---------------------------------------------------------------- DCAL

@dataclass
class Decal:
    x: int              # cell column inside the patch (0..7)
    y: int              # cell row inside the patch (0..7)
    texture: int        # DTEX index
    extra: bytes = b"\0\xff\xff\xff\xff"   # bytes 3..7, skipped by the loader
    uv: list = field(default_factory=list)  # 4 (u, v) int16 pairs, 2048 = one texture repeat


def decode_dcal(data):
    """DCAL: 24 bytes per decal, in patch order (see decals_by_patch)."""
    out = []
    for k in range(0, len(data), 24):
        b = data[k:k + 24]
        s = struct.unpack_from("<8h", b, 8)
        out.append(Decal(b[0], b[1], b[2], bytes(b[3:8]), [(s[n], s[n + 1]) for n in range(0, 8, 2)]))
    if len(data) % 24:
        raise ValueError("DCAL length is not a multiple of 24")
    return out


def encode_dcal(decals):
    out = bytearray()
    for d in decals:
        out += bytes((d.x & 255, d.y & 255, d.texture & 255)) + bytes(d.extra)
        out += struct.pack("<8h", *(c for pair in d.uv for c in pair))
    return bytes(out)


def decals_by_patch(decals, patches):
    """Split the DCAL list by PTCH decal counts (start = running sum, 0x1F08FA)."""
    out, k = [], 0
    for p in patches:
        out.append(decals[k:k + p.decal_count])
        k += p.decal_count
    if k != len(decals):
        raise ValueError("PTCH decal counts sum to %d, DCAL has %d" % (k, len(decals)))
    return out


# ---------------------------------------------------------------- small chunks

def decode_names(data, count=None):
    """LTEX / DTEX: NUL-terminated names (empty names allowed).  The loader reads
    INFO's layer count (LTEX) or decal-texture count (DTEX) of them (0x1F13A0)."""
    parts = bytes(data).split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    names = [p.decode("latin-1") for p in parts]
    return names if count is None else names[:count]


def encode_names(names):
    return b"".join(n.encode("latin-1") + b"\0" for n in names)


def decode_floats(data):
    """SCAL / ROTN: float32 per layer slot (16 slots in every retail map)."""
    return list(struct.unpack("<%df" % (len(data) // 4), data))


def encode_floats(vals):
    return struct.pack("<%df" % len(vals), *vals)


def decode_axis(data):
    return list(data)


def encode_axis(vals):
    return bytes(vals)


# ---------------------------------------------------------------- whole maps

def patch_grid(info):
    """(cells per side, patches per side) from an INFO body."""
    size, patch = struct.unpack_from("<HH", info, 16)
    return size, size // patch


@dataclass
class Surface:
    patches: list
    colours: list
    alpha: list
    decals: list
    layer_textures: list
    decal_textures: list
    layer_scale: list
    layer_axis: list
    layer_rotation: list


def decode_surface(chunks):
    """chunks: tag bytes -> body for one ``tern`` chunk."""
    info = chunks[b"INFO"]
    _, side = patch_grid(info)
    layer_count, dtex_count, decal_count = struct.unpack_from("<HHH", info, 22)
    patches = decode_ptch(chunks[b"PTCH"])
    if len(patches) != side * side:
        raise ValueError("PTCH has %d patches, expected %d" % (len(patches), side * side))
    decals = decode_dcal(chunks.get(b"DCAL", b""))
    if len(decals) != decal_count:
        raise ValueError("DCAL has %d records, INFO says %d" % (len(decals), decal_count))
    return Surface(
        patches=patches,
        colours=decode_colr(chunks[b"COLR"], len(patches)),
        alpha=decode_alph(chunks.get(b"ALPH", b""), patches),
        decals=decals,
        layer_textures=decode_names(chunks.get(b"LTEX", b""), layer_count),
        decal_textures=decode_names(chunks.get(b"DTEX", b""), dtex_count),
        layer_scale=decode_floats(chunks.get(b"SCAL", b"")),
        layer_axis=decode_axis(chunks.get(b"AXIS", b"")),
        layer_rotation=decode_floats(chunks.get(b"ROTN", b"")),
    )


def colour_image(colours, side):
    """Stitch COLR into one RGB image ((side*8+1) square).  Returns (w, h, rgb)."""
    w = side * PATCH + 1
    img = bytearray(w * w * 3)
    for pi, vals in enumerate(colours):
        px, py = pi % side, pi // side
        for j in range(VERTS):
            o = ((py * PATCH + j) * w + px * PATCH) * 3
            for i in range(VERTS):
                img[o + 3 * i:o + 3 * i + 3] = bytes(colr_rgb(vals[j * VERTS + i]))
    return w, w, bytes(img)


def alpha_image(alpha, side, layer):
    """One layer's 8-bit alpha over the whole map (0 where the patch lacks it)."""
    w = side * PATCH + 1
    img = bytearray(w * w)
    for pi, grids in enumerate(alpha):
        g = grids.get(layer)
        if g is None:
            continue
        px, py = pi % side, pi // side
        for j in range(VERTS):
            o = (py * PATCH + j) * w + px * PATCH
            for i in range(VERTS):
                img[o + i] = ALPHA_TABLE[g[j * VERTS + i] & 7]
    return w, w, bytes(img)
