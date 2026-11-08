"""CMOP: the game's compact bounding-volume tree for collision shapes.

Re-derived from the retail executable's tree walker (sub_001B0990, with
the same opcode table in its two siblings at 0x1B10B0 and 0x1B1BA0).

Chunk layout: float[3] minimum corner, float scale (units per metre at
full resolution; the game uses 127 * 2**17 / extent), u32 stream size,
then a byte stream. Multi-byte values in the stream are big-endian.

The walker carries a query (centre and radius, in the current node's
units; it starts at full resolution >> 16, i.e. 8-bit cells) and reads
opcodes until a leaf or a failed bounds test ends the stream:

    00                 end
    01-04 x y z        zoom in by 2**n around origin (x, y, z) (current units)
    05 n / 06 nn / 07 nnn   jump forward n bytes (after the operands)
    09 n / 0a nn       add to the primitive index base; 0b nnnn sets it
    10-12 a b n        split on x/y/z: left child (n bytes) has max a,
                       right child (the rest) has min b
    13-1c a b n        the same on diagonal planes (y+z, y-z, x+z, x-z,
                       x+y, x-y with values x2; x+y+z, x+y-z, x-y+z,
                       x-y-z with values x3)
    20-22 v n          split at v (left max v+1, right min v)
    23-25 a b nn mm    split with 16-bit offsets to the low (nn) and
                       high (mm) children
    26-28 lo hi        bounds on x/y/z: stop unless the query overlaps
    29-2b lo(3) hi(3)  the same at full resolution
    30-4f              leaf: primitive (base + op - 0x30)
    50-53 i...         leaf with a 1-4 byte index
    60/64/68 v...      sets a value the walker carries (unused here)

A split recurses into the left child and then carries on with the right
one, so every path through the stream ends in a leaf. Primitives are
triangles (mesh shapes) or boxes (box shapes), by index.
"""
import math
import struct

FULL_SHIFT = 16


def header(data):
    """(minimum corner, scale, stream bytes)."""
    x, y, z, scale, size = struct.unpack_from("<4fI", data)
    return (x, y, z), scale, data[20:20 + size]


def _be(b):
    v = 0
    for c in b:
        v = v << 8 | c
    return v


def query(data, centre, radius):
    """Primitive indices the game would test for a sphere-ish query
    (centre in the shape's space, radius in metres). Mirrors the walker."""
    lo, scale, stream = header(data)
    full = [int(round((centre[k] - lo[k]) * scale)) - 1 for k in range(3)]
    rfull = int(round(radius * scale))
    out = []
    state = dict(c=[f >> FULL_SHIFT for f in full], r=(rfull >> FULL_SHIFT) + 1,
                 o=[0, 0, 0], base=0, shift=FULL_SHIFT)
    _walk(stream, 0, state, full, rfull, out, 0)
    return out


DIAG = {
    0x13: (lambda c: c[1] + c[2], 2), 0x14: (lambda c: c[1] - c[2] + 255, 2),
    0x15: (lambda c: c[0] + c[2], 2), 0x16: (lambda c: c[0] - c[2] + 255, 2),
    0x17: (lambda c: c[0] + c[1], 2), 0x18: (lambda c: c[0] - c[1] + 255, 2),
    0x19: (lambda c: c[0] + c[1] + c[2], 3), 0x1A: (lambda c: c[0] + c[1] - c[2] + 255, 3),
    0x1B: (lambda c: c[0] - c[1] + c[2] + 255, 3), 0x1C: (lambda c: c[0] - c[1] - c[2] + 510, 3),
}


def _walk(s, p, st, full, rfull, out, depth):
    if depth > 200:
        raise ValueError("tree too deep")
    st = dict(st, c=list(st["c"]), o=list(st["o"]))
    while True:
        if p >= len(s):
            raise ValueError("ran off the end of the tree")
        op = s[p]
        c, r = st["c"], st["r"]
        if op == 0x00:
            return
        if 0x01 <= op <= 0x04:
            shift = st["shift"] - op
            b = s[p + 1:p + 4]
            o = [(b[k] + st["o"][k]) << op for k in range(3)]
            st["c"] = [(full[k] >> shift) - o[k] for k in range(3)]
            st["o"], st["r"], st["shift"] = o, (rfull >> shift) + 1, shift
            p += 4
        elif op in (0x05, 0x06, 0x07):
            n = op - 0x04
            p += 1 + n + _be(s[p + 1:p + 1 + n])
        elif op in (0x09, 0x0A):
            n = op - 0x08
            st["base"] += _be(s[p + 1:p + 1 + n])
            p += 1 + n
        elif op == 0x0B:
            st["base"] = _be(s[p + 1:p + 5])
            p += 5
        elif 0x10 <= op <= 0x1C:
            if op <= 0x12:
                q, rr, a, b = c[op - 0x10], r, s[p + 1], s[p + 2]
            else:
                fn, k = DIAG[op]
                q, a, b = fn(c), s[p + 1] * k, s[p + 2] * k
                rr = r + (r >> 1) + 1 if k == 2 else 4 * r
            skip, p = s[p + 3], p + 4
            if q + rr < b:
                continue
            if q > rr + a:
                p += skip
                continue
            _walk(s, p, st, full, rfull, out, depth + 1)
            p += skip
        elif 0x20 <= op <= 0x22:
            q, v, skip, p = c[op - 0x20], s[p + 1], s[p + 2], p + 3
            if q + r < v:
                continue
            if q > r + v + 1:
                p += skip
                continue
            _walk(s, p, st, full, rfull, out, depth + 1)
            p += skip
        elif 0x23 <= op <= 0x25:
            q = c[op - 0x23]
            a, b, s1, s2 = s[p + 1], s[p + 2], _be(s[p + 3:p + 5]), _be(s[p + 5:p + 7])
            p += 7
            if q + r < b:
                p += s1
                continue
            if q > r + a:
                p += s2
                continue
            _walk(s, p + s1, st, full, rfull, out, depth + 1)
            p += s2
        elif 0x26 <= op <= 0x28:
            q = c[op - 0x26]
            if q + r < s[p + 1] or q > s[p + 2] + r:
                return
            p += 3
        elif 0x29 <= op <= 0x2B:
            q = full[op - 0x29]
            if q + rfull < _be(s[p + 1:p + 4]) or q > _be(s[p + 4:p + 7]) + rfull:
                return
            p += 7
        elif 0x30 <= op <= 0x4F:
            out.append(st["base"] + op - 0x30)
            return
        elif 0x50 <= op <= 0x53:
            n = op - 0x4F
            out.append(st["base"] + _be(s[p + 1:p + 1 + n]))
            return
        elif op in (0x60, 0x64, 0x68):
            p += 1 + (1, 2, 4)[(op - 0x60) // 4]
        else:
            raise ValueError(f"unknown tree opcode {op:#04x} at {p}")


# ---------------------------------------------------------------- writing

MARGIN = 0.05   # retail headers sit 5 cm below the geometry


def build(prims):
    """A CMOP chunk for primitives given as (min xyz, max xyz) boxes in the
    shape's space. Uses bounds tests, axis splits, 16-bit jumps and leaves."""
    if not prims:
        raise ValueError("no primitives")
    lo = [min(p[0][k] for p in prims) - MARGIN for k in range(3)]
    hi = [max(p[1][k] for p in prims) + MARGIN for k in range(3)]
    ext = max(hi[k] - lo[k] for k in range(3)) or 1.0
    scale = 127 * 2 ** 17 / ext
    unit = scale / 2 ** FULL_SHIFT          # 8-bit cells per metre

    def cells(p):
        a = [max(0, min(255, math.floor((p[0][k] - lo[k]) * unit) - 1)) for k in range(3)]
        b = [max(0, min(255, math.ceil((p[1][k] - lo[k]) * unit) + 1)) for k in range(3)]
        return a, b

    boxes = [cells(p) for p in prims]

    def node(ids, parent_lo, parent_hi):
        lo_ = [min(boxes[i][0][k] for i in ids) for k in range(3)]
        hi_ = [max(boxes[i][1][k] for i in ids) for k in range(3)]
        out = bytearray()
        for k in (2, 1, 0):  # retail order: z, y, x
            if lo_[k] > parent_lo[k] or hi_[k] < parent_hi[k]:
                out += bytes((0x26 + k, lo_[k], hi_[k]))
        if len(ids) == 1:
            i = ids[0]
            if i < 32:
                out.append(0x30 + i)
            elif i < 256:
                out += bytes((0x50, i))
            elif i < 65536:
                out += bytes((0x51, i >> 8, i & 0xFF))
            else:
                out += bytes((0x52, i >> 16, i >> 8 & 0xFF, i & 0xFF))
            return bytes(out)
        axis = max(range(3), key=lambda k: hi_[k] - lo_[k])
        order = sorted(ids, key=lambda i: (boxes[i][0][axis] + boxes[i][1][axis], i))
        half = len(order) // 2
        left, right = order[:half], order[half:]
        left_max = max(boxes[i][1][axis] for i in left)
        right_min = min(boxes[i][0][axis] for i in right)
        lstream = node(left, lo_, hi_)
        rstream = node(right, lo_, hi_)
        if len(lstream) <= 255:
            out += bytes((0x10 + axis, left_max, right_min, len(lstream))) + lstream + rstream
        else:
            # Left child too long for the one-byte offset: jump over the
            # right child to it.
            if len(rstream) > 0xFFFF:
                raise ValueError("collision tree too large")
            jump = bytes((0x06, len(rstream) >> 8, len(rstream) & 0xFF))
            out += bytes((0x10 + axis, left_max, right_min, len(jump))) + jump + rstream + lstream
        return bytes(out)

    stream = node(list(range(len(prims))), [0, 0, 0], [255, 255, 255])
    return struct.pack("<4fI", *lo, scale, len(stream)) + stream
