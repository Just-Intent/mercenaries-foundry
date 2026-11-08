"""Pandemic "ucfb" chunk trees, as stored inside .dsk records.

A chunk is a 4-byte tag, a little-endian u32 size, then `size` bytes of data.
The next chunk starts at the following 4-byte boundary. Most records are a
single root chunk tagged b"ucfb" whose data is a list of child chunks.
"""
import struct
from dataclasses import dataclass, field


@dataclass
class Chunk:
    tag: bytes
    data: bytes
    children: list = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.tag.decode("latin-1")

    def child(self, tag: bytes):
        for c in self.children:
            if c.tag == tag:
                return c
        return None


def iter_chunks(data: bytes):
    pos = 0
    while pos < len(data):
        if pos + 8 > len(data):
            raise ValueError("truncated chunk header")
        tag, size = struct.unpack_from("<4sI", data, pos)
        end = pos + 8 + size
        if end > len(data):
            raise ValueError("chunk exceeds its container")
        yield tag, data[pos + 8:end]
        pos = (end + 3) & ~3


def _looks_like_tag(tag: bytes) -> bool:
    return all(0x20 <= b < 0x7F for b in tag)


def parse(data: bytes, depth: int = 0, max_depth: int = 12) -> list:
    """Parse chunks, recursing into any data that itself parses cleanly.

    Chunk trees carry no "has children" flag, so this guesses: data is treated
    as children only if it splits exactly into printable-tagged chunks.
    """
    out = []
    for tag, body in iter_chunks(data):
        chunk = Chunk(tag, body)
        if depth < max_depth and len(body) >= 8:
            try:
                kids = list(iter_chunks(body))
                if kids and all(_looks_like_tag(t) for t, _ in kids):
                    chunk.children = parse(body, depth + 1, max_depth)
            except ValueError:
                pass
        out.append(chunk)
    return out


def record_name(payload: bytes):
    """Best-effort asset name: ucfb -> first child -> its NAME chunk."""
    try:
        roots = list(iter_chunks(payload))
        if not roots or roots[0][0] != b"ucfb":
            return None
        for _, body in iter_chunks(roots[0][1]):
            for tag, sub in iter_chunks(body):
                if tag == b"NAME":
                    text = sub.split(b"\0", 1)[0]
                    if text and all(0x20 <= b < 0x7F for b in text):
                        return text.decode("ascii")
            break
    except (ValueError, struct.error):
        return None
    return None


def dump(chunks, indent: int = 0, preview: int = 24):
    lines = []
    for c in chunks:
        pad = "  " * indent
        if c.children:
            lines.append(f"{pad}{c.name} ({len(c.data)} bytes)")
            lines.extend(dump(c.children, indent + 1, preview))
        else:
            head = c.data[:preview]
            text = head.split(b"\0", 1)[0]
            if text and len(text) >= 3 and all(0x20 <= b < 0x7F for b in text):
                shown = repr(text.decode("ascii"))
            else:
                shown = head.hex(" ")
            more = "..." if len(c.data) > preview else ""
            lines.append(f"{pad}{c.name} ({len(c.data)} bytes) {shown}{more}")
    return lines


def pack_chunk(tag: bytes, data: bytes, pad: bool = True) -> bytes:
    """One chunk: header, data, then zero padding to 4 bytes unless pad=False."""
    if len(tag) != 4:
        raise ValueError(f"chunk tag must be 4 bytes: {tag!r}")
    out = struct.pack("<4sI", tag, len(data)) + data
    return out + b"\0" * (-len(out) % 4) if pad else out


def split(data: bytes):
    """Split container data into [(tag, body)] and whether the last child is padded.

    Parents normally include each child's padding. When the last child ends
    unaligned exactly at the end of its parent, that padding was left out;
    pack_children(..., pad_last=False) reproduces it.
    """
    items = list(iter_chunks(data))
    pad_last = True
    if items:
        pos = 0
        for _, body in items[:-1]:
            pos = (pos + 8 + len(body) + 3) & ~3
        last_end = pos + 8 + len(items[-1][1])
        pad_last = not (last_end % 4 and last_end == len(data))
    return items, pad_last


def pack_children(items, pad_last: bool = True) -> bytes:
    out = bytearray()
    for i, (tag, body) in enumerate(items):
        out += pack_chunk(tag, body, pad=pad_last or i < len(items) - 1)
    return bytes(out)


def unwrap(record: bytes, inner: bytes, with_pad: bool = False):
    """Return the data of ucfb -> <inner>, e.g. unwrap(rec, b"scr_").

    With with_pad=True also return (outer, inner) padding flags for wrap().
    """
    roots, outer_pad = split(record)
    if len(roots) != 1 or roots[0][0] != b"ucfb":
        raise ValueError("record is not a single ucfb chunk")
    kids, inner_pad = split(roots[0][1])
    if len(kids) != 1 or kids[0][0] != inner:
        raise ValueError(f"ucfb does not hold a single {inner.decode()} chunk")
    return (kids[0][1], (outer_pad, inner_pad)) if with_pad else kids[0][1]


def wrap(inner: bytes, body: bytes, pad=(True, True)) -> bytes:
    return pack_chunk(b"ucfb", pack_chunk(inner, body, pad[1]), pad[0])
