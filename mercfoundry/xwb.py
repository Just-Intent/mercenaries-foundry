"""XACT wave banks (.xwb, version 3, the original Xbox's): the game's audio.

DATAxbox/SOUND holds a wave bank per group of sounds (sfx/<group>/*.xwb,
vo/<speaker>/*.xwb, music/<faction>/*.xwb, ambient/ambient.xwb), each with
a sound bank (.xsb) naming the cues the game plays.

Layout (public XACT documentation; checked on every retail bank):

  0x00  "WBND", u32 version (3)
  0x08  4 regions, (u32 offset, u32 length) each: bank data, entry metadata,
        seek tables, wave data
  bank data:   u32 flags (bit 0: streaming, bit 16: wave names), u32 entry
               count, char name[16], u32 metadata entry size (24), u32 name
               entry size (64), u32 alignment (4, or 2048 for streamed
               banks), u32 compact format
  entry (24):  u32 flags (0x10000), u32 format, u32 play offset (into wave
               data), u32 play length, u32 loop start, u32 loop length
  region 2:    the waves' names, 64 bytes each, when bank flags bit 16 is set
  format:      bits 0-1 codec (0 PCM, 1 Xbox ADPCM), 2-4 channels,
               5-22 sample rate, 23-30 block align, 31 16-bit

Xbox ADPCM is IMA ADPCM in 36-byte blocks per channel: a 4-byte header
(s16 first sample, u8 step index, u8 0) and 32 bytes of 4-bit codes, 64
samples in all (the header's, then 63 codes; the last code is unused).
Stereo blocks interleave the two channels' headers, then 4-byte runs.
"""
import struct
from dataclasses import dataclass, field

PCM, XBOX_ADPCM = 0, 1
ADPCM_BLOCK = 36            # bytes per channel per block
ADPCM_SAMPLES = 64          # samples per channel per block

_STEPS = [7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80,
          88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598,
          658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
          3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289,
          16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767]
_INDEX = [-1, -1, -1, -1, 2, 4, 6, 8]


@dataclass
class Entry:
    index: int
    flags: int
    format: int
    offset: int             # into the wave data region
    length: int
    loop_start: int = 0
    loop_length: int = 0
    name: str = ""

    @property
    def codec(self):
        return self.format & 3

    @property
    def channels(self):
        return (self.format >> 2) & 7

    @property
    def rate(self):
        return (self.format >> 5) & 0x3FFFF

    @property
    def bits(self):
        return 16 if self.format >> 31 else 8

    @property
    def seconds(self):
        if self.codec == XBOX_ADPCM:
            blocks = self.length // (ADPCM_BLOCK * max(self.channels, 1))
            return blocks * ADPCM_SAMPLES / max(self.rate, 1)
        return self.length / max(self.rate * self.channels * self.bits // 8, 1)


@dataclass
class WaveBank:
    name: str
    flags: int
    alignment: int
    regions: list            # [(offset, length)] x 4
    entries: list = field(default_factory=list)
    data: bytes = b""
    meta_size: int = 24

    @property
    def streaming(self):
        return bool(self.flags & 1)

    def wave(self, e: Entry) -> bytes:
        start = self.regions[3][0] + e.offset
        return self.data[start:start + e.length]


def parse(data: bytes) -> WaveBank:
    if data[:4] != b"WBND":
        raise ValueError("not an XACT wave bank")
    version, = struct.unpack_from("<I", data, 4)
    if version != 3:
        raise ValueError(f"wave bank version {version}; only version 3 (original Xbox) is known")
    regions = [struct.unpack_from("<II", data, 8 + 8 * i) for i in range(4)]
    flags, count = struct.unpack_from("<II", data, regions[0][0])
    name = data[regions[0][0] + 8:regions[0][0] + 24].split(b"\0")[0].decode("latin-1")
    meta_size, name_size, alignment = struct.unpack_from("<III", data, regions[0][0] + 24)
    bank = WaveBank(name, flags, alignment, regions, data=data)
    names = flags & 0x10000 and regions[2][1] >= count * name_size > 0
    for i in range(count):
        f, fmt, off, length, ls, ll = struct.unpack_from("<6I", data, regions[1][0] + i * meta_size)
        label = ""
        if names:
            at = regions[2][0] + i * name_size
            label = data[at:at + name_size].split(b"\0")[0].decode("latin-1")
        bank.entries.append(Entry(i, f, fmt, off, length, ls, ll, label))
    bank.meta_size = meta_size
    return bank


def encode_adpcm(pcm16: bytes, channels: int) -> bytes:
    """Interleaved 16-bit PCM to Xbox ADPCM (padded with silence to whole
    64-sample blocks)."""
    frames = len(pcm16) // (2 * channels)
    samples = struct.unpack(f"<{frames * channels}h", pcm16[:frames * channels * 2])
    blocks = -(-frames // ADPCM_SAMPLES)
    chans = [list(samples[c::channels]) + [0] * (blocks * ADPCM_SAMPLES - frames) for c in range(channels)]
    step_of = [0] * channels
    out = bytearray()
    for b in range(blocks):
        heads, codes = [], []
        for c in range(channels):
            block = chans[c][b * ADPCM_SAMPLES:(b + 1) * ADPCM_SAMPLES]
            pred, step = block[0], step_of[c]
            heads.append(struct.pack("<hBB", pred, step, 0))
            nibs = []
            for s in block[1:]:
                st = _STEPS[step]
                diff = s - pred
                nib = 0
                if diff < 0:
                    nib, diff = 8, -diff
                # The decoder's reconstruction, mirrored exactly.
                rec = st >> 3
                if diff >= st:
                    nib |= 4
                    diff -= st
                    rec += st
                if diff >= st >> 1:
                    nib |= 2
                    diff -= st >> 1
                    rec += st >> 1
                if diff >= st >> 2:
                    nib |= 1
                    rec += st >> 2
                pred = pred - rec if nib & 8 else pred + rec
                pred = max(-32768, min(32767, pred))
                step = max(0, min(88, step + _INDEX[nib & 7]))
                nibs.append(nib)
            nibs.append(0)                     # the 64th code is unused
            step_of[c] = step
            codes.append(bytes(nibs[i] | nibs[i + 1] << 4 for i in range(0, 64, 2)))
        out += b"".join(heads)
        for run in range(8):
            for c in range(channels):
                out += codes[c][run * 4:run * 4 + 4]
    return bytes(out)


def convert(pcm16: bytes, channels: int, rate: int, to_channels: int, to_rate: int) -> bytes:
    """16-bit PCM to another channel count (mixed or copied) and rate (linear)."""
    frames = len(pcm16) // (2 * channels)
    s = struct.unpack(f"<{frames * channels}h", pcm16[:frames * channels * 2])
    if channels != to_channels:
        mono = [sum(s[i * channels:(i + 1) * channels]) // channels for i in range(frames)]
        s = [v for v in mono for _ in range(to_channels)]
    if rate != to_rate and frames:
        n = max(1, round(frames * to_rate / rate))
        out = []
        for i in range(n):
            x = i * rate / to_rate
            j = min(int(x), frames - 1)
            k = min(j + 1, frames - 1)
            t = x - j
            for c in range(to_channels):
                out.append(int(s[j * to_channels + c] * (1 - t) + s[k * to_channels + c] * t))
        s = out
    return struct.pack(f"<{len(s)}h", *s)


def encode_like(e: Entry, wav_data: bytes) -> bytes:
    """A .wav as wave data in an entry's format (its codec, channels, rate)."""
    channels, rate, pcm = read_wav(wav_data)
    pcm = convert(pcm, channels, rate, e.channels, e.rate)
    if e.codec == XBOX_ADPCM:
        return encode_adpcm(pcm, e.channels)
    if e.codec == PCM and e.bits == 16:
        return pcm
    if e.codec == PCM and e.bits == 8:
        return bytes((v >> 8) + 128 for v in struct.unpack(f"<{len(pcm) // 2}h", pcm))
    raise ValueError(f"wave {e.index}: codec {e.codec} isn't supported")


def rebuild(bank: WaveBank, replace: dict) -> bytes:
    """The bank with some waves' data replaced ({index: wave data in the
    entry's format}). Other waves keep their bytes; the wave data is laid out
    again at the bank's alignment. Replaced waves lose their loop points
    unless the original looped the whole wave (then the new one does too)."""
    data, regions = bank.data, bank.regions
    align = max(bank.alignment, 1)
    out_waves = bytearray()
    entries = bytearray()
    for e in bank.entries:
        body = replace.get(e.index)
        new = body is not None
        if body is None:
            body = bank.wave(e)
        pad = -len(out_waves) % align
        out_waves += b"\0" * pad
        offset = len(out_waves)
        out_waves += body
        ls, ll = e.loop_start, e.loop_length
        if new:
            whole = e.loop_length and e.loop_start == 0 and e.loop_length >= e.seconds * e.rate - ADPCM_SAMPLES
            frames = (len(body) // (ADPCM_BLOCK * e.channels) * ADPCM_SAMPLES if e.codec == XBOX_ADPCM
                      else len(body) // (e.channels * e.bits // 8))
            ls, ll = 0, frames if whole else 0
        entries += struct.pack("<6I", e.flags, e.format, offset, len(body), ls, ll)
        entries += data[regions[1][0] + e.index * bank.meta_size + 24:regions[1][0] + (e.index + 1) * bank.meta_size]
    out_waves += b"\0" * (-len(out_waves) % align)
    head = bytearray(data[:regions[3][0]])
    if len(entries) != regions[1][1]:
        raise ValueError("entry table size changed")
    head[regions[1][0]:regions[1][0] + len(entries)] = entries
    struct.pack_into("<II", head, 8 + 8 * 3, regions[3][0], len(out_waves))
    return bytes(head) + bytes(out_waves)


def decode_adpcm(data: bytes, channels: int) -> bytes:
    """Xbox ADPCM to interleaved 16-bit PCM."""
    block = ADPCM_BLOCK * channels
    out = bytearray()
    for b in range(0, len(data) - block + 1, block):
        chans = []
        for c in range(channels):
            pred, step = struct.unpack_from("<hB", data, b + 4 * c)
            step = min(step, 88)
            samples = [pred]
            codes = bytearray()
            # After the headers, 4-byte runs alternate between channels.
            for run in range(8):
                start = b + 4 * channels + (run * channels + c) * 4
                codes += data[start:start + 4]
            for byte in codes:
                for nib in (byte & 15, byte >> 4):
                    if len(samples) >= ADPCM_SAMPLES:
                        break
                    s = _STEPS[step]
                    diff = s >> 3
                    if nib & 4:
                        diff += s
                    if nib & 2:
                        diff += s >> 1
                    if nib & 1:
                        diff += s >> 2
                    pred = pred - diff if nib & 8 else pred + diff
                    pred = max(-32768, min(32767, pred))
                    step = max(0, min(88, step + _INDEX[nib & 7]))
                    samples.append(pred)
            chans.append(samples)
        for i in range(ADPCM_SAMPLES):
            for c in range(channels):
                out += struct.pack("<h", chans[c][i])
    return bytes(out)


def to_pcm16(bank: WaveBank, e: Entry) -> bytes:
    raw = bank.wave(e)
    if e.codec == XBOX_ADPCM:
        return decode_adpcm(raw, e.channels)
    if e.codec == PCM and e.bits == 16:
        return raw[:len(raw) // 2 * 2]
    if e.codec == PCM and e.bits == 8:
        return b"".join(struct.pack("<h", (v - 128) << 8) for v in raw)
    raise ValueError(f"entry {e.index}: codec {e.codec} isn't supported")


def wav(pcm16: bytes, channels: int, rate: int) -> bytes:
    """A 16-bit PCM .wav file."""
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * channels * 2, channels * 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(pcm16)) + pcm16
    return b"RIFF" + struct.pack("<I", len(body)) + body


def read_wav(data: bytes):
    """(channels, rate, 16-bit PCM bytes) from a .wav (PCM 16-bit)."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a .wav file")
    pos, fmt, pcm = 12, None, None
    while pos + 8 <= len(data):
        tag, size = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        body = data[pos + 8:pos + 8 + size]
        if tag == b"fmt ":
            fmt = struct.unpack_from("<HHIIHH", body)
        elif tag == b"data":
            pcm = body
        pos += 8 + size + (size & 1)
    if not fmt or pcm is None:
        raise ValueError("the .wav has no fmt or data chunk")
    tag, channels, rate, _, _, bits = fmt
    if tag not in (1, 0xFFFE) or bits != 16:
        raise ValueError(f"the .wav must be 16-bit PCM (it's format {tag}, {bits}-bit)")
    return channels, rate, pcm
