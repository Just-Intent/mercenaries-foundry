"""Lua script records (type "script"): ucfb -> scr_ -> NAME, INFO, BODY.

INFO byte 0 is unknown and kept as found. INFO bytes 1-4 hold the
decompressed size, or 0 when BODY is stored as plain text.

Compression (from Mercenaries Recompiled's notes on retail 0x21A750..0x21A857):
a 16-bit little-endian control word supplies bits, low bit first, and the next
control word is read as soon as the 16th bit is used. Payload bytes follow in
the order the decoder asks for them.

    1                literal: next byte
    0 0 a b  d       copy 3+2a+b bytes from distance 256-d (d=0 means 256)
    0 1  lo hi       distance 4096-((hi&0xF0)<<4)-lo, count 3+(hi&15);
                     if hi&15 == 0, one more byte e: e=0 ends the stream,
                     otherwise count is e+1
"""
import struct
from dataclasses import dataclass

from . import ucfb

MAX_SIZE = 16 * 1024 * 1024
WINDOW = 4096
MAX_MATCH = 256


class _Bits:
    def __init__(self, data):
        self.data = memoryview(data)
        self.pos = 0
        self._refill()

    def byte(self):
        if self.pos >= len(self.data):
            raise ValueError("compressed script is truncated")
        b = self.data[self.pos]
        self.pos += 1
        return b

    def _refill(self):
        self.word = self.byte() | self.byte() << 8
        self.left = 16

    def bit(self):
        b = self.word & 1
        self.word >>= 1
        self.left -= 1
        if not self.left:
            self._refill()
        return b


def decompress(data: bytes, expected: int) -> bytes:
    if not 0 <= expected <= MAX_SIZE:
        raise ValueError("invalid decompressed size")
    s = _Bits(data)
    out = bytearray()
    while True:
        if s.bit():
            if len(out) == expected:
                raise ValueError("literal runs past declared size")
            out.append(s.byte())
            continue
        if not s.bit():
            count = 3 + 2 * s.bit() + s.bit()
            distance = 256 - s.byte()
        else:
            lo, hi = s.byte(), s.byte()
            distance = 4096 - ((hi & 0xF0) << 4) - lo
            count = 3 + (hi & 15)
            if count == 3:
                ext = s.byte()
                if not ext:
                    if len(out) != expected:
                        raise ValueError("end marker disagrees with declared size")
                    return bytes(out)
                count = ext + 1
        if distance > len(out) or len(out) + count > expected:
            raise ValueError("invalid copy in compressed script")
        for _ in range(count):  # overlapping copies are allowed
            out.append(out[-distance])


class _Writer:
    def __init__(self):
        self.out = bytearray()
        self._reserve()

    def _reserve(self):
        self.at = len(self.out)
        self.out += b"\0\0"
        self.word = 0
        self.used = 0

    def bit(self, b):
        self.word |= (b & 1) << self.used
        self.used += 1
        if self.used == 16:
            struct.pack_into("<H", self.out, self.at, self.word)
            self._reserve()

    def byte(self, b):
        self.out.append(b)

    def finish(self):
        struct.pack_into("<H", self.out, self.at, self.word)
        return bytes(self.out)


def compress(data: bytes) -> bytes:
    """Compress so that decompress(result, len(data)) == data."""
    if len(data) > MAX_SIZE:
        raise ValueError("script too large")
    w = _Writer()
    heads = {}  # 3-byte prefix -> recent positions, newest last
    i, n = 0, len(data)

    def remember(pos):
        if pos + 3 <= n:
            chain = heads.setdefault(data[pos:pos + 3], [])
            chain.append(pos)
            if len(chain) > 64:
                del chain[:32]

    while i < n:
        best_len, best_dist = 0, 0
        if i + 3 <= n:
            for j in reversed(heads.get(data[i:i + 3], ())):
                dist = i - j
                if dist > WINDOW:
                    break
                length = 3
                limit = min(MAX_MATCH, n - i)
                while length < limit and data[j + length] == data[i + length]:
                    length += 1
                if length > best_len:
                    best_len, best_dist = length, dist
                    if length == limit:
                        break
        if best_len >= 3 and best_dist <= 256 and best_len <= 6:
            w.bit(0); w.bit(0)
            c = best_len - 3
            w.bit(c >> 1); w.bit(c & 1)
            w.byte((256 - best_dist) & 0xFF)
        elif best_len >= 3:
            w.bit(0); w.bit(1)
            enc = 4096 - best_dist
            lo, hi_nib = enc & 0xFF, enc >> 8
            if 4 <= best_len <= 18:
                w.byte(lo); w.byte(hi_nib << 4 | (best_len - 3))
            else:
                w.byte(lo); w.byte(hi_nib << 4); w.byte(best_len - 1)
        else:
            w.bit(1)
            w.byte(data[i])
            best_len = 1
        for k in range(i, i + best_len):
            remember(k)
        i += best_len
    w.bit(0); w.bit(1)
    w.byte(0); w.byte(0); w.byte(0)
    return w.finish()


@dataclass
class Script:
    name: str
    text: bytes          # decompressed body, exactly as stored (may end in NUL)
    info: bytes          # raw INFO chunk; byte 0 is unknown
    compressed: bool
    pad: tuple = (True, True)  # wrapper padding, see ucfb.unwrap

    @property
    def source(self) -> str:
        return self.text.rstrip(b"\0").decode("latin-1")


def decode(record: bytes) -> Script:
    inner, pad = ucfb.unwrap(record, b"scr_", with_pad=True)
    fields = dict(ucfb.iter_chunks(inner))
    for tag in (b"NAME", b"INFO", b"BODY"):
        if tag not in fields:
            raise ValueError(f"script record has no {tag.decode()} chunk")
    name = fields[b"NAME"].split(b"\0", 1)[0].decode("ascii")
    info = fields[b"INFO"]
    if len(info) < 5:
        raise ValueError("script INFO is too short")
    size = struct.unpack_from("<I", info, 1)[0]
    body = fields[b"BODY"]
    text = decompress(body, size) if size else body
    return Script(name, text, info, bool(size), pad)


def encode(script: Script, compress_body: bool = None) -> bytes:
    """Build a script record. Compression follows the original unless set."""
    use = script.compressed if compress_body is None else compress_body
    info = bytearray(script.info if len(script.info) >= 5 else b"\0" * 5)
    struct.pack_into("<I", info, 1, len(script.text) if use else 0)
    body = compress(script.text) if use else script.text
    fields = [(b"NAME", script.name.encode("ascii") + b"\0"), (b"INFO", bytes(info)), (b"BODY", body)]
    return ucfb.wrap(b"scr_", ucfb.pack_children(fields), script.pad)


def with_source(script: Script, source: str) -> Script:
    """Replace the Lua text, keeping the original's trailing-NUL convention."""
    text = source.encode("latin-1")
    if script.text.endswith(b"\0"):
        text += b"\0"
    return Script(script.name, text, script.info, script.compressed, script.pad)


def new(name: str, source: str, compress_body: bool = True) -> Script:
    """A fresh script. NUL-terminated and INFO byte 0 = 0: both unverified."""
    return Script(name, source.encode("latin-1") + b"\0", b"\0" * 5, compress_body)
