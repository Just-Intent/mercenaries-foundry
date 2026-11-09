"""XACT sound banks (.xsb, "SDBK" version 11, the original Xbox's): cue names
and the waves each cue plays.

Scripts name a cue as <sound bank>.<cue> (Audio_PlayVoiceover('fio.SWNfio01')).
rsm_xbox.ini (next to DATAxbox) registers the banks the game loads: sound
banks by name, and wave banks by name, resident or streamed. A sound bank
names its wave banks; every voice bank plays from the one streamed vo.xwb.

Layout (worked out from the retail banks; every registered bank reads
cleanly this way):

  0x00  "SDBK", u16 version (11), u16 CRC-16/X.25 of bytes 8 onward (checked)
  0x08  u32 wave bank names (16 bytes each), u32 track table, u32 variation
        and 3D data, u32 end
  0x18  u16 ?, u16 name table size, u16 sound count, u16 cue count, u16 ?,
        u16 wave bank count
  0x38  cues, 20 bytes: u16 flags, u16 sound (0xFFFF: none), u32 name
        offset, u32 -1, u16 0, u16 name slot, u32 0x07FFFFFF
  The u16 at 0x1A is the size of the cue name table, max(16, cue count);
  each cue's name slot is name_slot(name, size). The game finds cues by
  name through it, so a wrong slot or size makes cues unplayable.
  then  sounds, 20 bytes: u32 what (below), u16 category/volume, u16 ?,
        u8 track count, u8 ?, u8 ?, u8 kind flags, 8 bytes ?
  kind flags bit 3: simple, `what` is u16 wave + u16 wave bank
             bit 4: variation, `what` points at a variation table
             neither: `what` points at `track count` tracks
  variation table: u16 count (low 12 bits; the rest flags), u16 2*count,
        then count x (u16 wave, u16 wave bank, u16 weight from, u16 to),
        weights out of 10000 (the game picks one at random)
  track: u8 event count, u24 offset of the events
  event: u8 type, u24 time (ms), then by type:
        0 play wave (12), 1 play wave with pitch/volume variation (24):
          u32 flags, u16 wave + u16 wave bank (flags bit 10: instead a
          u32 offset of a variation table)
        3 loop/end (8, always last), 4 and 5 pitch/volume (16),
        12 and 15 (8), 8, 10, 11, 16: u32 length, then that many bytes
"""
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

SIMPLE, VARIATION = 0x08, 0x10
EVENT_SIZE = {0: 12, 1: 24, 3: 8, 4: 16, 5: 16, 12: 8, 15: 8}
EVENT_SIZED = {8, 10, 11, 16}
PLAY_EVENTS = {0, 1}
EVENT_VARIATION = 0x400


@dataclass
class Sound:
    index: int
    kind: str                       # "simple", "variation" or "tracks"
    waves: list = field(default_factory=list)       # [(wave bank name, wave index)], in play order


@dataclass
class Cue:
    index: int
    name: str
    sound: int


@dataclass
class SoundBank:
    wave_banks: list                # names
    cues: list
    sounds: list
    problems: list = field(default_factory=list)

    def cue(self, name):
        low = name.lower()
        return next((c for c in self.cues if c.name.lower() == low), None)

    def cue_waves(self, cue):
        """The distinct (wave bank, wave) a cue can play, first-heard order."""
        out = []
        for w in self.sounds[cue.sound].waves if cue.sound < len(self.sounds) else []:
            if w not in out:
                out.append(w)
        return out


def _cstr(data, at):
    end = data.find(b"\0", at)
    return data[at:end if end >= 0 else len(data)].decode("latin-1")


def parse(data: bytes) -> SoundBank:
    if data[:4] != b"SDBK":
        raise ValueError("not an XACT sound bank")
    version, = struct.unpack_from("<H", data, 4)
    if version != 11:
        raise ValueError(f"sound bank version {version}; only version 11 (original Xbox) is known")
    names_at, = struct.unpack_from("<I", data, 8)
    nsound, ncue, _, nbank = struct.unpack_from("<4H", data, 0x1C)
    banks = [data[names_at + 16 * i:names_at + 16 * i + 16].split(b"\0")[0].decode("latin-1")
             for i in range(nbank)]
    bank = SoundBank(banks, [], [])
    for i in range(ncue):
        _, sound, name_at = struct.unpack_from("<HHI", data, 0x38 + 20 * i)
        bank.cues.append(Cue(i, _cstr(data, name_at) if name_at < len(data) else "", sound))
    start = 0x38 + 20 * ncue

    def wave(w, b, where, out):
        if b < nbank:
            out.append((banks[b], w))
        else:
            bank.problems.append(f"sound {where}: wave bank {b} (the bank has {nbank})")

    def variation(at, where, out):
        count = struct.unpack_from("<H", data, at)[0] & 0xFFF
        for i in range(count):
            w, b, _, _ = struct.unpack_from("<4H", data, at + 4 + 8 * i)
            wave(w, b, where, out)

    for k in range(nsound):
        rec = data[start + 20 * k:start + 20 * k + 20]
        what, = struct.unpack_from("<I", rec, 0)
        flags, tracks = rec[11], rec[8]
        if flags & SIMPLE:
            s = Sound(k, "simple")
            wave(what & 0xFFFF, what >> 16, k, s.waves)
        elif flags & VARIATION:
            s = Sound(k, "variation")
            variation(what, k, s.waves)
        else:
            s = Sound(k, "tracks")
            for t in range(tracks):
                n = data[what + 4 * t]
                p = int.from_bytes(data[what + 4 * t + 1:what + 4 * t + 4], "little")
                for _ in range(n):
                    kind = data[p]
                    if kind in PLAY_EVENTS:
                        ev_flags, = struct.unpack_from("<I", data, p + 4)
                        if ev_flags & EVENT_VARIATION:
                            variation(struct.unpack_from("<I", data, p + 8)[0], k, s.waves)
                        else:
                            w, b = struct.unpack_from("<HH", data, p + 8)
                            wave(w, b, k, s.waves)
                    if kind in EVENT_SIZE:
                        p += EVENT_SIZE[kind]
                    elif kind in EVENT_SIZED:
                        p += 8 + struct.unpack_from("<I", data, p + 4)[0]
                    else:
                        bank.problems.append(f"sound {k}: unknown event type {kind}")
                        break
        bank.sounds.append(s)
    return bank


def add_cues(data: bytes, cues) -> bytes:
    """The bank with new simple cues appended: cues = [(name, wave, wave bank
    index, template sound)], each playing one wave; its sound copies the
    template sound's settings (volume, category, 3D). Everything after the
    cue and sound tables moves down, and every offset into it is moved too."""
    if not cues:
        return data
    bank = parse(data)
    if bank.problems:
        raise ValueError("can't add to a bank that doesn't read cleanly: " + "; ".join(bank.problems[:3]))
    out = bytearray(data)
    nsound, ncue = struct.unpack_from("<HH", data, 0x1C)
    cue_end = 0x38 + 20 * ncue
    sound_end = cue_end + 20 * nsound
    shift = 40 * len(cues)

    def moved(off):
        return off + shift if off >= sound_end else off + 20 * len(cues) if off >= cue_end else off

    for at in (0x08, 0x0C, 0x10, 0x14):
        struct.pack_into("<I", out, at, moved(struct.unpack_from("<I", data, at)[0]))
    for i in range(ncue):
        at = 0x38 + 20 * i + 4
        struct.pack_into("<I", out, at, moved(struct.unpack_from("<I", data, at)[0]))
    for k in range(nsound):
        at = cue_end + 20 * k
        what, = struct.unpack_from("<I", data, at)
        flags, tracks = data[at + 11], data[at + 8]
        if flags & SIMPLE:
            continue
        struct.pack_into("<I", out, at, moved(what))
        if flags & VARIATION:
            continue
        for t in range(tracks):
            entry, = struct.unpack_from("<I", data, what + 4 * t)
            p = entry >> 8
            struct.pack_into("<I", out, what + 4 * t, (entry & 0xFF) | moved(p) << 8)
            for _ in range(entry & 0xFF):
                kind = data[p]
                if kind in PLAY_EVENTS and struct.unpack_from("<I", data, p + 4)[0] & EVENT_VARIATION:
                    struct.pack_into("<I", out, p + 8, moved(struct.unpack_from("<I", data, p + 8)[0]))
                p += EVENT_SIZE[kind] if kind in EVENT_SIZE else 8 + struct.unpack_from("<I", data, p + 4)[0]
    new_cues, new_sounds, names = bytearray(), bytearray(), bytearray()
    end = len(data) + shift
    for j, (name, wave, wbank, template) in enumerate(cues):
        new_cues += struct.pack("<HHIIII", 0, nsound + j, end + len(names), 0xFFFFFFFF, 0, 0x07FFFFFF)
        names += name.encode("latin-1") + b"\0"
        rec = bytearray(data[cue_end + 20 * template:cue_end + 20 * template + 20])
        struct.pack_into("<I", rec, 0, wave | wbank << 16)
        rec[8] = 1
        rec[11] = (rec[11] & ~(SIMPLE | VARIATION)) | SIMPLE
        new_sounds += rec
    body = bytes(out)
    result = bytearray(body[:cue_end] + new_cues + body[cue_end:sound_end] + new_sounds + body[sound_end:] + names)
    struct.pack_into("<HH", result, 0x1C, nsound + len(cues), ncue + len(cues))
    # The cue name table: as many slots as cues (16 at least), each cue
    # keeping its slot; a changed size moves every cue's slot.
    size = max(struct.unpack_from("<H", data, 0x1A)[0], ncue + len(cues))
    struct.pack_into("<H", result, 0x1A, size)
    for i, c in enumerate(bank.cues + [Cue(0, n, 0) for n, *_ in cues]):
        struct.pack_into("<H", result, 0x38 + 20 * i + 14, name_slot(c.name, size))
    struct.pack_into("<I", result, 0x14, len(result))
    struct.pack_into("<H", result, 6, crc(result))
    return bytes(result)


def name_slot(name: str, size: int) -> int:
    """A cue's slot in the bank's cue name table (XACT's name hash, from the
    game's lookup at 0x27F5AE): h = c + h/2 + 3h per byte, modulo the size."""
    h = 0
    for c in name.encode("latin-1"):
        h = (c + (h >> 1) + h * 3) & 0xFFFFFFFF
    return h % size


def crc(data: bytes) -> int:
    """The header's checksum: CRC-16/X.25 of everything after byte 8. A bank
    whose checksum doesn't match is refused by the game."""
    value = 0xFFFF
    for b in memoryview(data)[8:]:
        value ^= b
        for _ in range(8):
            value = (value >> 1) ^ 0x8408 if value & 1 else value >> 1
    return value ^ 0xFFFF


def read_registry(text: str):
    """rsm_xbox.ini -> ({wave bank name: DATAxbox-relative path}, {sound bank name: path})."""
    waves, sounds, section = {}, {}, ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line
            continue
        m = re.match(r"bank\s+(\S+)\s+(\S+)", line)
        if m:
            rel = "/".join(m.group(2).replace("\\", "/").split("/")[1:])   # drop "dataxbox/"
            (waves if "WaveBanks" in section else sounds)[m.group(1).lower()] = rel
    return waves, sounds


def _find(data_dir: Path, rel: str):
    """A DATAxbox-relative path, matching case the way the Xbox does."""
    p = data_dir
    for part in rel.split("/"):
        hit = next((c for c in p.iterdir() if c.name.lower() == part.lower()), None) if p.is_dir() else None
        if hit is None:
            return None
        p = hit
    return p


class Library:
    """The game's sound and wave banks, found from rsm_xbox.ini (or, without
    it, by file name under DATAxbox/SOUND)."""

    def __init__(self, data_dir):
        self.dir = Path(data_dir)
        self.wave_paths, self.sound_paths = {}, {}
        ini = self.dir.parent / "rsm_xbox.ini"
        if ini.exists():
            waves, sounds = read_registry(ini.read_text(encoding="latin-1"))
            for table, out in ((waves, self.wave_paths), (sounds, self.sound_paths)):
                for name, rel in table.items():
                    p = _find(self.dir, rel)
                    if p:
                        out[name] = p
        else:
            root = _find(self.dir, "SOUND")
            for p in sorted(root.rglob("*")) if root else []:
                table = {".xwb": self.wave_paths, ".xsb": self.sound_paths}.get(p.suffix.lower())
                if table is not None:
                    table.setdefault(p.stem.lower(), p)
        self._sound, self._wave = {}, {}

    def rel(self, path: Path) -> str:
        """"DATAxbox/SOUND/..." for a bank file, as a mod writes it."""
        return "DATAxbox/" + path.relative_to(self.dir).as_posix()

    def sound_bank(self, name) -> SoundBank:
        name = name.lower()
        if name not in self._sound:
            if name not in self.sound_paths:
                raise KeyError(name)
            self._sound[name] = parse(self.sound_paths[name].read_bytes())
        return self._sound[name]

    def wave_bank(self, name):
        from . import xwb
        name = name.lower()
        if name not in self._wave:
            if name not in self.wave_paths:
                raise KeyError(name)
            self._wave[name] = xwb.parse(self.wave_paths[name].read_bytes())
        return self._wave[name]

    def users(self, wave_bank, index):
        """Every (sound bank, cue) that can play a wave."""
        key = (wave_bank.lower(), index)
        out = []
        for sname in self.sound_paths:
            sb = self.sound_bank(sname)
            for c in sb.cues:
                if key in [(b.lower(), w) for b, w in sb.cue_waves(c)]:
                    out.append((sname, c.name))
        return out
