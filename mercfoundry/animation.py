"""Animation records (type "animation"): ucfb -> anim.

    NAME   animation name
    INFO   u16 frames, u16 joints, float translation scale (units per metre),
           u16 (event count?), u32 CJNT size, u32 unpacked size
    CJNT   joint data; compressed with the script scheme unless both sizes
           in INFO are equal
    jont   per joint: NAME, then SXLT (int16 x3 fixed translation, divided
           by the INFO scale) or DXLT (float: scale for per-frame int16 x3
           translation, metres = value x scale), and QROT (u16 x4: byte
           length of each rotation channel)
    XLTC, EVNT   not decoded

CJNT holds, joint by joint: the per-frame translation (frames x int16[3])
when the joint has DXLT, then four rotation channels (quaternion x, y, z,
w on a +-2047 scale). A channel is an int16 start value, then per frame a
signed byte change, 0x80 n to hold for n frames, or 0x81 + int16 to set a
new value. About 1.6% of joints are preceded by one stray byte; the decoder
detects it because the four start values must form a unit quaternion.
Rotations are each joint's full local rotation. Established from retail
data (all 1,835 animations, 43,153 joints decode); frame rate assumed 30.
"""
import math
import struct
from dataclasses import dataclass, field

from . import script, ucfb
from .hashing import name_hash

ANIMATION = name_hash("animation")
FPS = 30.0
QUAT_SCALE = 2047.0


@dataclass
class Track:
    joint: str
    rotations: list                 # per frame (x, y, z, w)
    translations: list = None       # per frame (x, y, z) metres, or None
    translation: tuple = None       # fixed (x, y, z) metres, or None
    dxlt: float = None              # original per-frame translation scale


@dataclass
class Animation:
    name: str
    frames: int
    scale: float
    tracks: list = field(default_factory=list)

    @property
    def duration(self):
        return max(self.frames - 1, 1) / FPS


def _channel(data, start, frames):
    """Decode one channel; return (values, end offset) or None."""
    if start + 2 > len(data):
        return None
    v = struct.unpack_from("<h", data, start)[0]
    i, out = start + 2, [v]
    while len(out) < frames:
        if i >= len(data):
            return None
        b = data[i]
        if b == 0x80:
            if i + 1 >= len(data):
                return None
            out += [v] * data[i + 1]
            i += 2
        elif b == 0x81:
            if i + 2 >= len(data):
                return None
            v = struct.unpack_from("<h", data, i + 1)[0]
            out.append(v)
            i += 3
        else:
            v += b - 256 if b > 127 else b
            out.append(v)
            i += 1
    return out[:frames], i


def _rotations(data, start, sizes, frames):
    chans, p = [], start
    for k in range(4):
        got = _channel(data, p, frames)
        if got is None:
            return None
        chans.append(got[0])
        p += sizes[k]
    step = max(1, frames // 8)
    for f in range(0, frames, step):
        n = math.sqrt(sum(chans[k][f] ** 2 for k in range(4)))
        if not 2030 <= n <= 2065:
            return None
    return chans


def decode(record: bytes) -> Animation:
    body = ucfb.unwrap(record, b"anim")
    items = list(ucfb.iter_chunks(body))
    fields = dict(items)
    info = fields[b"INFO"]
    frames, joints = struct.unpack_from("<HH", info)
    scale = struct.unpack_from("<f", info, 4)[0]
    csize, usize = struct.unpack_from("<II", info, 10)
    cjnt = fields[b"CJNT"]
    data = cjnt if csize == usize else script.decompress(cjnt, usize)
    anim = Animation(fields[b"NAME"].split(b"\0", 1)[0].decode("latin-1"), frames, scale)
    o = 0
    for tag, jbody in items:
        if tag != b"jont":
            continue
        jd = dict(ucfb.iter_chunks(jbody))
        name = jd[b"NAME"].split(b"\0", 1)[0].decode("latin-1")
        sizes = struct.unpack("<4H", jd[b"QROT"])
        track = Track(name, [])
        if b"DXLT" in jd:
            s = struct.unpack("<f", jd[b"DXLT"])[0]
            track.dxlt = s
            vals = struct.unpack_from(f"<{3 * frames}h", data, o)
            track.translations = [tuple(v * s for v in vals[3 * f:3 * f + 3]) for f in range(frames)]
            o += 6 * frames
        elif b"SXLT" in jd and scale:
            track.translation = tuple(v / scale for v in struct.unpack("<3h", jd[b"SXLT"]))
        for shift in (0, 1, 2, -1, 3):
            chans = _rotations(data, o + shift, sizes, frames)
            if chans is not None:
                o += shift
                break
        else:
            raise ValueError(f"{anim.name}: can't decode rotations of joint {name}")
        track.rotations = [tuple(chans[k][f] / QUAT_SCALE for k in range(4)) for f in range(frames)]
        o += sum(sizes)
        anim.tracks.append(track)
    if len(anim.tracks) != joints:
        raise ValueError(f"{anim.name}: expected {joints} joints, found {len(anim.tracks)}")
    return anim


def find(archives, name):
    key = int(name, 16) if len(name) == 8 and all(c in "0123456789abcdefABCDEF" for c in name) else name_hash(name)
    for arc in archives:
        with arc.path.open("rb") as f:
            for r in arc.records:
                if r.key == key and r.kind == ANIMATION:
                    f.seek(r.offset)
                    return f.read(r.size)
    raise KeyError(f"animation not found: {name}")


# ----------------------------------------------------------------- encoding

HOLD_MIN = 3   # unchanged runs this long or longer use a hold code


def encode_channel(values) -> bytes:
    """values: per-frame int16. Start value, then signed byte changes, holds
    (0x80 n) for unchanged runs, and 0x81 + int16 for big jumps."""
    out = bytearray(struct.pack("<h", values[0]))
    i, n = 1, len(values)
    while i < n:
        if values[i] == values[i - 1]:
            run = 1
            while i + run < n and values[i + run] == values[i - 1]:
                run += 1
            left = run
            while left >= HOLD_MIN:
                step = min(left, 255)
                out += bytes((0x80, step))
                left -= step
            out += b"\0" * left  # short runs (and leftovers) stay literal
            i += run
            continue
        d = values[i] - values[i - 1]
        if -126 <= d <= 127:
            out += struct.pack("<b", d)
        else:
            out += b"\x81" + struct.pack("<h", values[i])
        i += 1
    return bytes(out)


def _quant_rotations(rotations):
    """Quantise as given; callers normalise imported rotations first."""
    chans = [[], [], [], []]
    for q in rotations:
        for k in range(4):
            chans[k].append(max(-32768, min(32767, round(q[k] * QUAT_SCALE))))
    return chans


def encode(anim: Animation, base: bytes = None) -> bytes:
    """Build an animation record. With `base` (the original record), keep its
    chunk layout, event chunks and the order of its joints."""
    if anim.frames < 1:
        raise ValueError("an animation needs at least one frame")
    for t in anim.tracks:
        if len(t.rotations) != anim.frames or (t.translations and len(t.translations) != anim.frames):
            raise ValueError(f"{anim.name}: joint {t.joint} has the wrong number of frames")
    scale = anim.scale or 1000.0
    data = bytearray()
    jonts = []
    for t in anim.tracks:
        parts = [(b"NAME", t.joint.encode("latin-1") + b"\0")]
        if t.translations:
            peak = max(abs(v) for p in t.translations for v in p) or 1e-6
            # Keep the original scale when every value still fits in int16;
            # otherwise fit a new one.
            fits = t.dxlt and all(-32768 <= round(v / t.dxlt) <= 32767 for p in t.translations for v in p)
            s = t.dxlt if fits else peak / 32767
            for p in t.translations:
                data += struct.pack("<3h", *(max(-32768, min(32767, round(v / s))) for v in p))
            parts.append((b"DXLT", struct.pack("<f", s)))
        else:
            fixed = t.translation or (0.0, 0.0, 0.0)
            q = [round(v * scale) for v in fixed]
            if any(abs(v) > 32767 for v in q):
                raise ValueError(f"{anim.name}: fixed translation of {t.joint} is too large for the scale")
            parts.append((b"SXLT", struct.pack("<3h", *q)))
        chans = [encode_channel(c) for c in _quant_rotations(t.rotations)]
        data += b"".join(chans)
        parts.append((b"QROT", struct.pack("<4H", *[len(c) for c in chans])))
        jonts.append((t.joint.lower(), parts))
    data = bytes(data)
    packed = script.compress(data)
    stored = packed if len(packed) < len(data) else data
    extra = 0
    items, pad_last, pad = [], True, (True, True)
    if base is not None:
        body, pad = ucfb.unwrap(base, b"anim", with_pad=True)
        items, pad_last = ucfb.split(body)
        extra = struct.unpack_from("<H", dict(items)[b"INFO"], 8)[0]
    info = struct.pack("<HHfHII", anim.frames, len(anim.tracks), scale, extra, len(stored), len(data))
    if base is None:
        items = [(b"NAME", anim.name.encode("latin-1") + b"\0"), (b"INFO", b""), (b"CJNT", b"")] +                 [(b"jont", b"")] * len(jonts)
    out, k = [], 0
    for tag, body in items:
        if tag == b"INFO":
            out.append((tag, info))
        elif tag == b"CJNT":
            out.append((tag, stored))
        elif tag == b"NAME":
            out.append((tag, anim.name.encode("latin-1") + b"\0"))
        elif tag == b"jont":
            if k < len(jonts):
                out.append((tag, ucfb.pack_children(jonts[k][1])))
            k += 1
        else:
            out.append((tag, body))
    for extra_joint in jonts[k:]:
        out.append((b"jont", ucfb.pack_children(extra_joint[1])))
    return ucfb.wrap(b"anim", ucfb.pack_children(out, pad_last), pad)
