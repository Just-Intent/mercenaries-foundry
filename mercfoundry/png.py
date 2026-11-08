"""Minimal PNG writer (standard library only)."""
import struct
import zlib


def _chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def encode(width: int, height: int, pixels: bytes, channels: int = 4) -> bytes:
    """pixels: rows top to bottom, `channels` bytes per pixel (3 = RGB, 4 = RGBA)."""
    if len(pixels) != width * height * channels:
        raise ValueError("pixel data does not match the image size")
    colour_type = {3: 2, 4: 6}[channels]
    stride = width * channels
    raw = b"".join(b"\0" + pixels[y * stride:(y + 1) * stride] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, colour_type, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 6))
            + _chunk(b"IEND", b""))
