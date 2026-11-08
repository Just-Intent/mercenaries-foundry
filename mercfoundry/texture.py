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
    fields = dict(ucfb.iter_chunks(ucfb.unwrap(record, b"tex_")))
    info = fields[b"INFO"]
    if len(info) < 24:
        raise ValueError("texture INFO is too short")
    w, h, _, mips, kind, _, fmt, _, size = struct.unpack_from("<6H3I", info)
    body = fields.get(b"BODY", b"")
    if len(body) < size:
        raise ValueError("texture BODY is shorter than INFO says")
    name = fields.get(b"NAME", b"").split(b"\0", 1)[0].decode("latin-1")
    return Texture(name, w, h, mips, fmt, info, body[:size], 6 if kind == 2 else 1)


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
