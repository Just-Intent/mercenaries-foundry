"""Minimal PNG reader and writer (standard library only)."""
import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def encode(width: int, height: int, pixels: bytes, channels: int = 4) -> bytes:
    """pixels: rows top to bottom, `channels` bytes per pixel (1 = grey, 2 = grey + alpha, 3 = RGB, 4 = RGBA)."""
    if len(pixels) != width * height * channels:
        raise ValueError("pixel data does not match the image size")
    colour_type = {1: 0, 2: 4, 3: 2, 4: 6}[channels]
    stride = width * channels
    raw = b"".join(b"\0" + pixels[y * stride:(y + 1) * stride] for y in range(height))
    return (SIGNATURE
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, colour_type, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 6))
            + _chunk(b"IEND", b""))


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def encode_grey16(width: int, height: int, values) -> bytes:
    """A 16-bit greyscale PNG; values: rows top to bottom, 0..65535."""
    if len(values) != width * height:
        raise ValueError("pixel data does not match the image size")
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += struct.pack(f">{width}H", *values[y * width:(y + 1) * width])
    return (SIGNATURE
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 16, 0, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + _chunk(b"IEND", b""))


def decode_grey16(data: bytes) -> tuple:
    """Read a greyscale PNG at full precision: (width, height, values 0..65535).
    16-bit greyscale keeps every value; 8-bit greyscale is scaled up (x 257)."""
    width, height, depth, ctype, _, _, rows = _read(data)
    if ctype != 0:
        raise ValueError("expected a greyscale PNG (save the heightmap as 16-bit greyscale)")
    values = []
    for line in rows:
        if depth == 16:
            values += struct.unpack(f">{width}H", bytes(line))
        else:
            values += [v * 257 for v in line]
    return width, height, values


def _read(data: bytes) -> tuple:
    """Parse and unfilter: (width, height, depth, colour type, palette, tRNS, rows)."""
    if not data.startswith(SIGNATURE):
        raise ValueError("not a PNG file")
    pos = len(SIGNATURE)
    ihdr, palette, trns, idat = None, None, None, bytearray()
    while pos + 8 <= len(data):
        size, tag = struct.unpack_from(">I4s", data, pos)
        body = data[pos + 8:pos + 8 + size]
        pos += 12 + size
        if tag == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif tag == b"PLTE":
            palette = body
        elif tag == b"tRNS":
            trns = body
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
    if not ihdr:
        raise ValueError("PNG has no header")
    width, height, depth, ctype, _, _, interlace = ihdr
    if interlace:
        raise ValueError("interlaced PNGs aren't supported; save without interlacing")
    if depth not in (8, 16):
        raise ValueError(f"PNG bit depth {depth} isn't supported; save as 8-bit")
    samples = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if samples is None:
        raise ValueError(f"unknown PNG colour type {ctype}")
    if ctype == 3 and not palette:
        raise ValueError("palette PNG has no palette")
    bpp = samples * depth // 8
    stride = width * bpp
    raw = zlib.decompress(bytes(idat))
    if len(raw) < height * (stride + 1):
        raise ValueError("PNG image data is truncated")
    rows, prev = [], bytearray(stride)
    for y in range(height):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if f == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                up_left = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, prev[i], up_left)) & 255
        elif f != 0:
            raise ValueError(f"bad PNG filter {f}")
        rows.append(line)
        prev = line
    return width, height, depth, ctype, palette, trns, rows


def decode(data: bytes) -> tuple:
    """Read a PNG. Returns (width, height, RGBA bytes).

    Handles greyscale, RGB, palette, grey+alpha and RGBA at 8 or 16 bits
    (16-bit is reduced to 8), and palette/grey/RGB transparency. Interlaced
    images and bit depths below 8 are rejected with a clear error.
    """
    width, height, depth, ctype, palette, trns, rows = _read(data)
    out = bytearray(width * height * 4)
    key = None
    if trns and ctype in (0, 2):
        key = struct.unpack(">" + "H" * (len(trns) // 2), trns)
    for y, line in enumerate(rows):
        if depth == 16:
            line = line[0::2]  # keep the high byte of each sample
        o = y * width * 4
        for x in range(width):
            if ctype == 6:
                r, g, b, a = line[x * 4:x * 4 + 4]
            elif ctype == 2:
                r, g, b = line[x * 3:x * 3 + 3]
                a = 0 if key and (r, g, b) == tuple(k >> (8 if depth == 16 else 0) for k in key) else 255
            elif ctype == 3:
                i = line[x]
                r, g, b = palette[i * 3:i * 3 + 3]
                a = trns[i] if trns and i < len(trns) else 255
            elif ctype == 4:
                r = g = b = line[x * 2]
                a = line[x * 2 + 1]
            else:
                r = g = b = line[x]
                a = 0 if key and r == key[0] >> (8 if depth == 16 else 0) else 255
            out[o:o + 4] = bytes((r, g, b, a))
            o += 4
    return width, height, bytes(out)
