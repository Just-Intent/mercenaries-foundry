"""Terrain heightfield decoding (tern > INFO / HEXP / HGT8).

Established from the retail loader sub_001F07A0 (RedTerrain.cpp) and the
height lookups sub_001F03C0 / sub_00146A10 in default.xbe.

Heights are a size x size grid of samples (no extra edge row/column: the
lookups mask both coordinates with size-1, 0x6432c4).  Sample (x, z) is at
world grid position x * spacing, z * spacing (before the map offset).

  height(x, z) = ((HGT8[x, z] << shift[blk]) + base[blk]) * step   metres

with blk the 16 x 16 block containing the sample.  HGT8 is RLE-packed and,
once unpacked, block-major: block (bz, bx) occupies bytes
[(bz*bpr + bx) * 256, +256), row-major inside the block.
"""

import struct

__all__ = ["parse_info", "parse_hexp", "unpack_hgt8", "pack_hgt8",
           "decode_heights", "decode_height_units"]


def parse_info(info: bytes) -> dict:
    """INFO: f32 spacing, step, min, max; i16 size, patch, block, layers,
    dtex, decals; optionally three more f32 (read only if >= 12 bytes remain)."""
    spacing, step, hmin, hmax = struct.unpack_from("<4f", info, 0)
    size, patch, block, layers, dtex, decals = struct.unpack_from("<6h", info, 16)
    extra = struct.unpack_from("<3f", info, 28) if len(info) >= 40 else (0.0, 0.0, 0.0)
    return dict(spacing=spacing, step=step, min=hmin, max=hmax, size=size,
                patch=patch, block=block, layers=layers, dtex=dtex,
                decals=decals, extra=extra)


def parse_hexp(hexp: bytes, nblocks: int) -> list:
    """HEXP: per 16x16 block, four int16: shift, base, min, max (height units)."""
    if len(hexp) < nblocks * 8:
        raise ValueError("HEXP too short")
    return [struct.unpack_from("<hhhh", hexp, i * 8) for i in range(nblocks)]


def unpack_hgt8(data: bytes, out_len: int) -> tuple:
    """PackBits-style RLE used by the loader at 0x1F0920.

    Control byte c: c < 0x80 -> c+1 literal bytes follow;
    c >= 0x80 -> next byte repeated c-0x7F times (1..128).
    Stops once out_len bytes are produced.  Returns (bytes, consumed)."""
    out = bytearray()
    i = 0
    while len(out) < out_len:
        c = data[i]; i += 1
        if c & 0x80:
            out += bytes([data[i]]) * (c - 0x7F); i += 1
        else:
            out += data[i:i + c + 1]; i += c + 1
    if len(out) != out_len:
        raise ValueError("HGT8 run overflows the height buffer")
    return bytes(out), i


def pack_hgt8(raw: bytes) -> bytes:
    """Encoder counterpart of unpack_hgt8 (runs of >=2 equal bytes as repeats)."""
    out = bytearray()
    i, n = 0, len(raw)
    lit = bytearray()

    def flush():
        while lit:
            chunk = lit[:128]
            out.append(len(chunk) - 1); out.extend(chunk)
            del lit[:128]

    while i < n:
        j = i
        while j < n and j - i < 128 and raw[j] == raw[i]:
            j += 1
        if j - i >= 2:
            flush()
            out.append(0x7F + (j - i)); out.append(raw[i])
            i = j
        else:
            lit.append(raw[i]); i += 1
    flush()
    return bytes(out)


def decode_height_units(chunks: dict) -> tuple:
    """Returns (info, units, consumed) with units[z][x] as integers (height / step)."""
    info = parse_info(chunks[b"INFO"])
    size, block = info["size"], info["block"]
    bpr = size // block                        # blocks per row (0x643308)
    raw, consumed = unpack_hgt8(chunks[b"HGT8"], size * size)
    hexp = parse_hexp(chunks[b"HEXP"], bpr * bpr)
    units = [[0] * size for _ in range(size)]
    bb = block * block
    for z in range(size):
        row = units[z]
        for x in range(size):
            blk = (z // block) * bpr + (x // block)
            shift, base = hexp[blk][0] & 0xFF, hexp[blk][1]
            v = raw[blk * bb + (z % block) * block + (x % block)]
            row[x] = (v << (shift & 31)) + base
    return info, units, consumed


def decode_heights(chunks: dict) -> tuple:
    """(size, heights) with heights[z][x] in metres, size x size samples."""
    info, units, _ = decode_height_units(chunks)
    step = info["step"]
    return info["size"], [[u * step for u in row] for row in units]


# ---------------------------------------------------------------- encoding

PNG_LEVELS = 16   # heightmap PNG levels per height step (0.35 m): whole steps round-trip exactly


def _hexp_entry(values):
    """(shift, base, min, max) for one block's height units, as the retail
    data does it: base = min, the smallest shift that fits max - min in a byte."""
    lo, hi = min(values), max(values)
    shift = 0
    while (hi - lo) >> shift > 255:
        shift += 1
    return shift, lo, lo, hi


def encode_height_units(chunks: dict, units) -> dict:
    """New INFO, HEXP and HGT8 bodies for heights units[z][x] (height / step).

    Blocks whose decoded heights are unchanged keep their original HEXP entry
    and bytes; if nothing changed the original chunks come back unchanged."""
    info, old, _ = decode_height_units(chunks)
    size, block = info["size"], info["block"]
    if len(units) != size or any(len(row) != size for row in units):
        raise ValueError(f"heights must be {size} x {size}")
    bpr = size // block
    bb = block * block
    raw, _ = unpack_hgt8(chunks[b"HGT8"], size * size)
    raw = bytearray(raw)
    hexp = parse_hexp(chunks[b"HEXP"], bpr * bpr)
    changed = False
    for bz in range(bpr):
        for bx in range(bpr):
            vals = [units[bz * block + z][bx * block + x] for z in range(block) for x in range(block)]
            olds = [old[bz * block + z][bx * block + x] for z in range(block) for x in range(block)]
            if vals == olds:
                continue
            changed = True
            entry = _hexp_entry(vals)
            if not all(-32768 <= v <= 32767 for v in entry):
                raise ValueError("heights out of range")
            blk = bz * bpr + bx
            hexp[blk] = entry
            shift, base = entry[0], entry[1]
            raw[blk * bb:(blk + 1) * bb] = bytes(min(255, (v - base) >> shift) for v in vals)
    if not changed:
        return {b"INFO": chunks[b"INFO"], b"HEXP": chunks[b"HEXP"], b"HGT8": chunks[b"HGT8"]}
    step = info["step"]
    lo = min(e[2] for e in hexp)
    hi = max(e[3] for e in hexp)
    new_info = bytearray(chunks[b"INFO"])
    struct.pack_into("<2f", new_info, 8, lo * step, hi * step)
    return {b"INFO": bytes(new_info),
            b"HEXP": b"".join(struct.pack("<4h", *e) for e in hexp),
            b"HGT8": pack_hgt8(bytes(raw))}


def heightmap_png(chunks: dict) -> tuple:
    """(16-bit greyscale PNG bytes, metadata) for a terrain's heights. Pixel
    value = (units - base) * PNG_LEVELS; row 0 is z = 0 (the map's -z edge)."""
    from . import png
    info, units, _ = decode_height_units(chunks)
    size = info["size"]
    base = min(min(row) for row in units)
    values = [(u - base) * PNG_LEVELS for row in units for u in row]
    if max(values) > 65535:
        raise ValueError("height range too large for a 16-bit heightmap")
    meta = {"size": size, "spacing_m": info["spacing"], "step_m": info["step"],
            "base_units": base, "levels_per_step": PNG_LEVELS,
            "note": "height_m = (pixel / levels_per_step + base_units) * step_m; "
                    "pixel (col, row) is world x = col * spacing_m - size * spacing_m / 2, "
                    "z = row * spacing_m - (size * spacing_m / 2 - spacing_m) (the game's own offsets, "
                    "read in game: x + 2048, z + 2040 on 512-cell maps)"}
    return png.encode_grey16(size, size, values), meta


def units_from_png(data: bytes, meta: dict) -> list:
    """Heights (units[z][x]) from an edited heightmap PNG and its metadata."""
    from . import png
    w, h, values = png.decode_grey16(data)
    size = meta["size"]
    if (w, h) != (size, size):
        raise ValueError(f"heightmap must be {size} x {size} pixels, not {w} x {h}")
    levels, base = meta["levels_per_step"], meta["base_units"]
    return [[int(round(values[z * size + x] / levels)) + base for x in range(size)] for z in range(size)]


def tern_chunks(record: bytes) -> dict:
    from . import ucfb
    return dict(ucfb.iter_chunks(ucfb.unwrap(record, b"tern")))


def rebuild(record: bytes, replace: dict) -> bytes:
    """The terrain record with some tern chunks' bodies replaced (others kept
    byte for byte, in their order)."""
    from . import ucfb
    body, pads = ucfb.unwrap(record, b"tern", with_pad=True)
    items, pad_last = ucfb.split(body)
    items = [(t, replace.get(t, b)) for t, b in items]
    return ucfb.wrap(b"tern", ucfb.pack_children(items, pad_last), pads)
