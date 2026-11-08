"""Texture records (type "Texture"): ucfb -> tex_ -> NAME (optional), INFO, BODY.

INFO (24 bytes): u16 width, u16 height, u16 (1), u16 mip levels, u16 kind
(1 = 2D, 2 = cube map), u16 (1), u32 Xbox D3D format, u32 0, u32 BODY size.
BODY holds the mip levels in order, largest first; cube maps hold 6 faces,
each a full mip chain.

Formats used by the retail game:
    0x0C  DXT1, 8 bytes per 4x4 block
    0x0E  DXT2/3 (the Xbox uses one code), 16 bytes per block, 4-bit alpha
    0x06  A8R8G8B8, stored swizzled (Morton order) as on the Xbox GPU
DXT blocks are stored left to right, top to bottom (not swizzled).
"""
import struct
from dataclasses import dataclass

from . import png, ucfb
from .hashing import name_hash

TEXTURE = name_hash("Texture")
DXT1, DXT3, ARGB8 = 0x0C, 0x0E, 0x06
FORMAT_NAMES = {DXT1: "DXT1", DXT3: "DXT3", ARGB8: "A8R8G8B8"}


@dataclass
class Texture:
    name: str
    width: int
    height: int
    mips: int
    format: int
    info: bytes
    body: bytes
    faces: int = 1
    layout: tuple = ()      # original chunk order and padding, for re-encoding

    @property
    def format_name(self):
        return FORMAT_NAMES.get(self.format, f"0x{self.format:02X}")

    def level_size(self, level=0):
        w, h = max(1, self.width >> level), max(1, self.height >> level)
        if self.format == DXT1:
            return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 8
        if self.format == DXT3:
            return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 16
        if self.format == ARGB8:
            return w * h * 4
        raise ValueError(f"unsupported texture format {self.format_name}")

    def face_size(self):
        return sum(self.level_size(i) for i in range(self.mips))

    def rgba(self, level=0, face=0) -> tuple:
        """Return (width, height, RGBA bytes) for a mip level (and cube face)."""
        if not 0 <= face < self.faces:
            raise ValueError(f"{self.name}: no face {face}")
        offset = face * self.face_size() + sum(self.level_size(i) for i in range(level))
        w, h = max(1, self.width >> level), max(1, self.height >> level)
        data = self.body[offset:offset + self.level_size(level)]
        if len(data) < self.level_size(level):
            raise ValueError(f"{self.name}: texture data is truncated")
        if self.format == DXT1:
            return w, h, decode_dxt(data, w, h, alpha=False)
        if self.format == DXT3:
            return w, h, decode_dxt(data, w, h, alpha=True)
        return w, h, decode_argb8_swizzled(data, w, h)

    def to_png(self, level=0, face=0) -> bytes:
        w, h, pixels = self.rgba(level, face)
        return png.encode(w, h, pixels, 4)


def decode(record: bytes) -> Texture:
    inner, pad = ucfb.unwrap(record, b"tex_", with_pad=True)
    items, pad_last = ucfb.split(inner)
    fields = dict(items)
    info = fields[b"INFO"]
    if len(info) < 24:
        raise ValueError("texture INFO is too short")
    w, h, _, mips, kind, _, fmt, _, size = struct.unpack_from("<6H3I", info)
    body = fields.get(b"BODY", b"")
    if len(body) < size:
        raise ValueError("texture BODY is shorter than INFO says")
    name = fields.get(b"NAME", b"").split(b"\0", 1)[0].decode("latin-1")
    layout = (tuple((t, None if t in (b"INFO", b"BODY") else b) for t, b in items), pad_last, pad)
    return Texture(name, w, h, mips, fmt, info, body[:size], 6 if kind == 2 else 1, layout)


def _rgb565(c):
    r, g, b = c >> 11 & 31, c >> 5 & 63, c & 31
    return (r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)


def decode_dxt(data: bytes, width: int, height: int, alpha: bool) -> bytes:
    out = bytearray(width * height * 4)
    bw, bh = max(1, (width + 3) // 4), max(1, (height + 3) // 4)
    step = 16 if alpha else 8
    pos = 0
    for by in range(bh):
        for bx in range(bw):
            if alpha:
                alphas = struct.unpack_from("<Q", data, pos)[0]
                pos += 8
            c0, c1, bits = struct.unpack_from("<HHI", data, pos)
            pos += 8
            p0, p1 = _rgb565(c0), _rgb565(c1)
            if c0 > c1 or alpha:
                p2 = tuple((2 * a + b) // 3 for a, b in zip(p0, p1))
                p3 = tuple((a + 2 * b) // 3 for a, b in zip(p0, p1))
                palette = (p0 + (255,), p1 + (255,), p2 + (255,), p3 + (255,))
            else:
                p2 = tuple((a + b) // 2 for a, b in zip(p0, p1))
                palette = (p0 + (255,), p1 + (255,), p2 + (255,), (0, 0, 0, 0))
            for py in range(4):
                y = by * 4 + py
                if y >= height:
                    break
                for px in range(4):
                    x = bx * 4 + px
                    if x >= width:
                        continue
                    i = py * 4 + px
                    r, g, b, a = palette[bits >> (2 * i) & 3]
                    if alpha:
                        a = (alphas >> (4 * i) & 15) * 17
                    o = (y * width + x) * 4
                    out[o:o + 4] = bytes((r, g, b, a))
    return bytes(out)


def _morton(x, y, width, height):
    """Offset of pixel (x, y) in Xbox swizzled order (power-of-two sizes)."""
    offset, bit = 0, 0
    w, h = width, height
    xb = yb = 0
    while w > 1 or h > 1:
        if w > 1:
            offset |= (x >> xb & 1) << bit
            bit += 1
            xb += 1
            w >>= 1
        if h > 1:
            offset |= (y >> yb & 1) << bit
            bit += 1
            yb += 1
            h >>= 1
    return offset


def decode_argb8_swizzled(data: bytes, width: int, height: int) -> bytes:
    out = bytearray(width * height * 4)
    xs = [_morton(x, 0, width, height) for x in range(width)]
    ys = [_morton(0, y, width, height) for y in range(height)]
    for y in range(height):
        row = ys[y]
        for x in range(width):
            s = (row | xs[x]) * 4
            b, g, r, a = data[s:s + 4]
            o = (y * width + x) * 4
            out[o:o + 4] = bytes((r, g, b, a))
    return bytes(out)


def find(archives, name):
    key = int(name, 16) if len(name) == 8 and all(c in "0123456789abcdefABCDEF" for c in name) else name_hash(name)
    for arc in archives:
        with arc.path.open("rb") as f:
            for r in arc.records:
                if r.key == key and r.kind == TEXTURE:
                    f.seek(r.offset)
                    return f.read(r.size)
    raise KeyError(f"texture not found: {name}")


# ---------------------------------------------------------------- encoding

def _clamp(v):
    return 0 if v < 0 else 255 if v > 255 else v


def _endpoints(colours):
    """Pick two endpoint colours along the main axis of a block's colours."""
    n = len(colours)
    mean = [sum(c[k] for c in colours) / n for k in range(3)]
    cov = [[0.0] * 3 for _ in range(3)]
    for c in colours:
        d = [c[k] - mean[k] for k in range(3)]
        for i in range(3):
            for j in range(3):
                cov[i][j] += d[i] * d[j]
    axis = [1.0, 1.0, 1.0]
    for _ in range(4):
        axis = [sum(cov[i][j] * axis[j] for j in range(3)) for i in range(3)]
        norm = max(abs(a) for a in axis)
        if norm < 1e-9:
            axis = [1.0, 1.0, 1.0]
            break
        axis = [a / norm for a in axis]
    length = sum(a * a for a in axis) ** 0.5 or 1.0
    axis = [a / length for a in axis]  # unit length, so projections are distances
    proj = [sum((c[k] - mean[k]) * axis[k] for k in range(3)) for c in colours]
    lo, hi = min(proj), max(proj)
    a = tuple(_clamp(mean[k] + axis[k] * lo) for k in range(3))
    b = tuple(_clamp(mean[k] + axis[k] * hi) for k in range(3))
    return a, b


def _to565(r, g, b):
    return (round(_clamp(r) * 31 / 255) << 11 | round(_clamp(g) * 63 / 255) << 5
            | round(_clamp(b) * 31 / 255))


def _palette(c0, c1, four_colour):
    p0, p1 = _rgb565(c0), _rgb565(c1)
    if four_colour:
        return [p0, p1, tuple((2 * a + b) // 3 for a, b in zip(p0, p1)),
                tuple((a + 2 * b) // 3 for a, b in zip(p0, p1))]
    return [p0, p1, tuple((a + b) // 2 for a, b in zip(p0, p1))]


# Weight of endpoint 0 for each palette index.
_WEIGHTS = {True: (1.0, 0.0, 2 / 3, 1 / 3), False: (1.0, 0.0, 0.5)}


def _try(px, transparent, c0, c1, four_colour):
    """Assign indices for endpoints c0, c1. Returns (error, c0, c1, indices)."""
    if four_colour and c0 < c1:
        c0, c1 = c1, c0
    if not four_colour and c0 > c1:
        c0, c1 = c1, c0
    if four_colour and c0 == c1:
        p = _rgb565(c0)
        err = sum((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2 + (q[2] - p[2]) ** 2 for q in px)
        return err, c0, c1, [0] * 16
    palette = _palette(c0, c1, four_colour)
    err, idx = 0, []
    for p, t in zip(px, transparent):
        if t:
            idx.append(3)
            continue
        best, bi = None, 0
        for j, q in enumerate(palette):
            d = (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2
            if best is None or d < best:
                best, bi = d, j
        err += best
        idx.append(bi)
    return err, c0, c1, idx


def _refit(px, transparent, idx, four_colour):
    """Least-squares endpoints for fixed indices, or None if degenerate."""
    w = _WEIGHTS[four_colour]
    aa = bb = ab = 0.0
    ap = [0.0, 0.0, 0.0]
    bp = [0.0, 0.0, 0.0]
    for p, t, i in zip(px, transparent, idx):
        if t:
            continue
        a, b = w[i], 1 - w[i]
        aa += a * a
        bb += b * b
        ab += a * b
        for k in range(3):
            ap[k] += a * p[k]
            bp[k] += b * p[k]
    det = aa * bb - ab * ab
    if abs(det) < 1e-9:
        return None
    e0 = [(bb * ap[k] - ab * bp[k]) / det for k in range(3)]
    e1 = [(aa * bp[k] - ab * ap[k]) / det for k in range(3)]
    return _to565(*e0), _to565(*e1)


def _encode_colour_block(px, four_colour):
    """px: 16 (r, g, b, a). Returns 8 bytes. four_colour=False allows a
    transparent index (DXT1 punch-through alpha)."""
    transparent = [not four_colour and p[3] < 128 for p in px]
    colours = [p[:3] for p, t in zip(px, transparent) if not t] or [(0, 0, 0)]
    lo, hi = _endpoints(colours)
    best = _try(px, transparent, _to565(*hi), _to565(*lo), four_colour)
    for _ in range(2):
        ends = _refit(px, transparent, best[3], four_colour)
        if not ends:
            break
        cand = _try(px, transparent, ends[0], ends[1], four_colour)
        if cand[0] >= best[0]:
            break
        best = cand
    _, c0, c1, idx = best
    bits = 0
    for i, v in enumerate(idx):
        bits |= v << (2 * i)
    return struct.pack("<HHI", c0, c1, bits)


def _block(rgba, width, height, bx, by):
    px = []
    for py in range(4):
        y = min(by * 4 + py, height - 1)
        for qx in range(4):
            x = min(bx * 4 + qx, width - 1)
            o = (y * width + x) * 4
            px.append(tuple(rgba[o:o + 4]))
    return px


def encode_dxt(rgba: bytes, width: int, height: int, alpha: bool) -> bytes:
    out = bytearray()
    for by in range(max(1, (height + 3) // 4)):
        for bx in range(max(1, (width + 3) // 4)):
            px = _block(rgba, width, height, bx, by)
            if alpha:
                bits = 0
                for i, p in enumerate(px):
                    bits |= ((p[3] * 15 + 127) // 255) << (4 * i)
                out += struct.pack("<Q", bits) + _encode_colour_block(px, True)
            else:
                opaque = all(p[3] >= 128 for p in px)
                out += _encode_colour_block(px, opaque)
    return bytes(out)


def encode_argb8_swizzled(rgba: bytes, width: int, height: int) -> bytes:
    out = bytearray(width * height * 4)
    xs = [_morton(x, 0, width, height) for x in range(width)]
    ys = [_morton(0, y, width, height) for y in range(height)]
    for y in range(height):
        for x in range(width):
            o = (y * width + x) * 4
            r, g, b, a = rgba[o:o + 4]
            s = (ys[y] | xs[x]) * 4
            out[s:s + 4] = bytes((b, g, r, a))
    return bytes(out)


def downsample(rgba: bytes, width: int, height: int) -> tuple:
    """Halve an image with a 2x2 box filter. Returns (width, height, RGBA)."""
    nw, nh = max(1, width // 2), max(1, height // 2)
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        y0, y1 = min(2 * y, height - 1), min(2 * y + 1, height - 1)
        for x in range(nw):
            x0, x1 = min(2 * x, width - 1), min(2 * x + 1, width - 1)
            o = (y * nw + x) * 4
            for k in range(4):
                out[o + k] = (rgba[(y0 * width + x0) * 4 + k] + rgba[(y0 * width + x1) * 4 + k]
                              + rgba[(y1 * width + x0) * 4 + k] + rgba[(y1 * width + x1) * 4 + k] + 2) // 4
    return nw, nh, bytes(out)


def _power_of_two(n):
    return n > 0 and n & (n - 1) == 0


def replace_image(tex: Texture, width: int, height: int, rgba: bytes) -> Texture:
    """New texture in the same format as `tex`, with a full mip chain built
    from an RGBA image. Sizes must be powers of two; the mip count follows the
    original (shortened if the new image is smaller)."""
    if tex.faces != 1:
        raise ValueError(f"{tex.name}: replacing cube maps isn't supported yet")
    if not (_power_of_two(width) and _power_of_two(height)):
        raise ValueError(f"image is {width}x{height}; texture sizes must be powers of two")
    if len(rgba) != width * height * 4:
        raise ValueError("image data does not match its size")
    mips = max(1, min(tex.mips, max(width, height).bit_length()))
    levels, w, h, img = [], width, height, rgba
    for level in range(mips):
        if level:
            w, h, img = downsample(img, w, h)
        if tex.format == DXT1:
            levels.append(encode_dxt(img, w, h, alpha=False))
        elif tex.format == DXT3:
            levels.append(encode_dxt(img, w, h, alpha=True))
        elif tex.format == ARGB8:
            levels.append(encode_argb8_swizzled(img, w, h))
        else:
            raise ValueError(f"can't encode texture format {tex.format_name}")
    body = b"".join(levels)
    info = bytearray(tex.info)
    struct.pack_into("<HH", info, 0, width, height)
    struct.pack_into("<H", info, 6, mips)
    struct.pack_into("<I", info, 20, len(body))
    return Texture(tex.name, width, height, mips, tex.format, bytes(info), body, 1, tex.layout)


def encode(tex: Texture) -> bytes:
    """Build a texture record, keeping the original chunk order and padding."""
    if tex.layout:
        tags, pad_last, pad = tex.layout
    else:
        tags = ((b"NAME", tex.name.encode("latin-1") + bytes(1)), (b"INFO", None), (b"BODY", None))
        pad_last, pad = True, (True, True)
    items = []
    for tag, body in tags:
        if tag == b"INFO":
            body = tex.info
        elif tag == b"BODY":
            body = tex.body
        items.append((tag, body))
    return ucfb.wrap(b"tex_", ucfb.pack_children(items, pad_last), pad)


def new(name: str, width: int, height: int, rgba: bytes) -> Texture:
    """A brand-new texture: DXT3 if the image has any transparency, else
    DXT1, with a full mip chain."""
    has_alpha = any(rgba[i] < 255 for i in range(3, len(rgba), 4))
    fmt = DXT3 if has_alpha else DXT1
    mips = max(width, height).bit_length()
    info = struct.pack("<6H3I", width, height, 1, mips, 1, 1, fmt, 0, 0)
    blank = Texture(name, width, height, mips, fmt, info, b"", 1, ())
    return replace_image(blank, width, height, rgba)
