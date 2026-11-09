import math
import struct
import tempfile
import unittest
from pathlib import Path

from mercfoundry import dsk, modbuild, sounds, ucfb, xsb, xwb
from mercfoundry.hashing import name_hash
from tests.test_dsk import build


def tone(n, freq=440.0, rate=22050, amp=9000):
    return struct.pack(f"<{n}h", *[int(amp * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)])


def make_xwb(name, waves, alignment=4, streaming=False, names=None):
    """waves: [(format, data)]."""
    count = len(waves)
    bank_data = struct.pack("<II16sIII", (1 if streaming else 0) | (0x10000 if names else 0), count,
                            name.encode(), 24, 64, alignment) + b"\0" * 4
    meta_at = 40 + len(bank_data)
    names_blob = b"".join(n.encode().ljust(64, b"\0") for n in names) if names else b""
    names_at = meta_at + 24 * count
    wave_at = names_at + len(names_blob)
    wave_at += -wave_at % alignment
    body, entries = bytearray(), bytearray()
    for fmt, data in waves:
        body += b"\0" * (-len(body) % alignment)
        entries += struct.pack("<6I", 0x10000, fmt, len(body), len(data), 0, 0)
        body += data
    body += b"\0" * (-len(body) % alignment)
    head = b"WBND" + struct.pack("<I", 3)
    head += struct.pack("<8I", 40, len(bank_data), meta_at, 24 * count,
                        names_at if names else 0, len(names_blob), wave_at, len(body))
    out = bytearray(head + bank_data + entries + names_blob)
    out += b"\0" * (wave_at - len(out))
    return bytes(out + body)


def adpcm_format(channels=1, rate=22050):
    return xwb.XBOX_ADPCM | channels << 2 | rate << 5 | 1 << 31


def make_xsb(wave_banks, cues, sounds_):
    """cues: [(name, sound)]; sounds_: [("simple", wave, bank) | ("variation", [(wave, bank)])
    | ("tracks", [[events]])] with events (type, body bytes)."""
    ncue, nsound = len(cues), len(sounds_)
    names_at = 0x38 + 20 * ncue + 20 * nsound
    extra_at = names_at + 16 * len(wave_banks)
    extra = bytearray()
    records = []
    for s in sounds_:
        if s[0] == "simple":
            records.append(struct.pack("<IHHBBBB8x", s[1] | s[2] << 16, 0, 0, 1, 0, 1, xsb.SIMPLE))
        elif s[0] == "variation":
            at = extra_at + len(extra)
            n = len(s[1])
            extra += struct.pack("<HH", n, 2 * n)
            for i, (w, b) in enumerate(s[1]):
                extra += struct.pack("<4H", w, b, i * 10000 // n, (i + 1) * 10000 // n)
            records.append(struct.pack("<IHHBBBB8x", at, 0, 0, 1, 0, 1, xsb.VARIATION))
        else:
            tracks = s[1]
            table_at = extra_at + len(extra)
            extra += b"\0" * (4 * len(tracks))
            for t, events in enumerate(tracks):
                ev_at = extra_at + len(extra)
                for kind, body in events:
                    extra += bytes([kind]) + b"\0\0\0" + body
                struct.pack_into("<I", extra, table_at - extra_at + 4 * t, len(events) | ev_at << 8)
            records.append(struct.pack("<IHHBBBB8x", table_at, 0, 0, len(tracks), 0, 1, 0))
    name_blob = bytearray()
    cue_names_at = extra_at + len(extra)
    cue_recs = bytearray()
    for i, (name, sound) in enumerate(cues):
        cue_recs += struct.pack("<HHIIII", 0, sound, cue_names_at + len(name_blob), 0xFFFFFFFF, 0, 0x07FFFFFF)
        name_blob += name.encode() + b"\0"
    end = cue_names_at + len(name_blob)
    head = b"SDBK" + struct.pack("<HH", 11, 0) + struct.pack("<4I", names_at, extra_at, extra_at, end)
    head += struct.pack("<6H", 0, 0x10, nsound, ncue, 0, len(wave_banks))
    head += b"\0" * (0x38 - len(head))
    banks = b"".join(b.encode().ljust(16, b"\0") for b in wave_banks)
    return bytes(head + cue_recs + b"".join(records) + banks + extra + name_blob)


def registry_record(bank, cue, wave):
    """An assets.dsk sound registry record with one simple cue, as the game lists them."""
    c = ucfb.pack_chunk
    full = f"{bank}.{cue}".encode()
    xcl = (c(b"INFO", b"name\0" + full + b"\0num_components\x001\0") + c(b"FLOT", b"positional\x000\0") +
           c(b"STRG", b"bank\0" + bank.encode() + b"\0") +
           c(b"SSET", c(b"INFO", b"name\0sound_set\0num_elements\x001\0") + c(b"SNDS", c(b"REC_", b"name\0" + full + b"\0"))))
    play = c(b"INFO", b"num_elements\x001\0") + c(b"WAVE", c(b"REC_", b"name\0%s.%s\0bank\0%s\0wave_entry_index\x000\0" % (
        bank.encode(), wave.encode(), bank.encode())))
    xsl = (c(b"INFO", b"name\0" + full + b"\0num_components\x001\0") + c(b"FLOT", b"positional\x000\0volume\x00-300\0") +
           c(b"STRG", b"bank\0" + bank.encode() + b"\0category\0voiceover\0") +
           c(b"TSET", c(b"INFO", b"name\0track_set\0num_elements\x001\0") + c(b"TRKS", c(b"REC_", c(b"PLAY", play)))))
    return c(b"ucfb", c(b"xcl_", xcl) + c(b"xsl_", xsl))


class AdpcmTest(unittest.TestCase):
    def test_round_trip_mono_and_stereo(self):
        n = 2000
        mono = tone(n)
        dec = xwb.decode_adpcm(xwb.encode_adpcm(mono, 1), 1)
        a, b = struct.unpack(f"<{n}h", mono), struct.unpack(f"<{n}h", dec[:2 * n])
        self.assertLess(math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)) / n), 300)
        stereo = b"".join(struct.pack("<hh", v, -v // 2) for v in a)
        dec = xwb.decode_adpcm(xwb.encode_adpcm(stereo, 2), 2)
        c = struct.unpack(f"<{len(dec) // 2}h", dec)
        rms = lambda d: math.sqrt(sum(v * v for v in d) / n)
        self.assertLess(rms([a[i] - c[2 * i] for i in range(n)]), 300)
        self.assertLess(rms([-a[i] // 2 - c[2 * i + 1] for i in range(n)]), 300)

    def test_blocks(self):
        self.assertEqual(len(xwb.encode_adpcm(tone(64), 1)), 36)
        self.assertEqual(len(xwb.encode_adpcm(tone(65), 1)), 72)
        self.assertEqual(len(xwb.encode_adpcm(tone(64) * 2, 2)), 72)   # 64 stereo frames: 36 bytes a channel

    def test_convert(self):
        pcm = tone(1000, rate=44100)
        out = xwb.convert(b"".join(pcm[i:i + 2] * 2 for i in range(0, len(pcm), 2)), 2, 44100, 1, 22050)
        self.assertEqual(len(out), 1000)        # 500 mono frames


class WaveBankTest(unittest.TestCase):
    def setUp(self):
        self.waves = [(adpcm_format(), xwb.encode_adpcm(tone(640), 1)),
                      (adpcm_format(), xwb.encode_adpcm(tone(320, 220), 1))]
        self.data = make_xwb("fx", self.waves, names=["beep", "boop"])

    def test_parse(self):
        b = xwb.parse(self.data)
        self.assertEqual((b.name, len(b.entries)), ("fx", 2))
        self.assertEqual([e.name for e in b.entries], ["beep", "boop"])
        e = b.entries[0]
        self.assertEqual((e.codec, e.channels, e.rate), (xwb.XBOX_ADPCM, 1, 22050))
        self.assertEqual(len(xwb.to_pcm16(b, e)), 640 * 2)

    def test_rebuild_same(self):
        self.assertEqual(xwb.rebuild(xwb.parse(self.data), {}), self.data)
        streamed = make_xwb("st", self.waves, alignment=2048, streaming=True)
        self.assertEqual(xwb.rebuild(xwb.parse(streamed), {}), streamed)

    def test_rebuild_add(self):
        b = xwb.parse(self.data)
        extra = xwb.encode_adpcm(tone(200, 990), 1)
        out = xwb.parse(xwb.rebuild(b, {}, [(adpcm_format(), extra, "bip")]))
        self.assertEqual([e.name for e in out.entries], ["beep", "boop", "bip"])
        self.assertEqual(out.wave(out.entries[2]), extra)
        self.assertEqual(out.wave(out.entries[0]), b.wave(b.entries[0]))
        st = xwb.parse(make_xwb("st", self.waves, alignment=2048, streaming=True))
        out = xwb.parse(xwb.rebuild(st, {}, [(adpcm_format(), extra, "bip")]))
        self.assertEqual(len(out.entries), 3)
        self.assertEqual(out.regions[3][0] % 2048, 0)
        self.assertTrue(all(e.offset % 2048 == 0 for e in out.entries))
        self.assertEqual(out.wave(out.entries[2]), extra)

    def test_rebuild_replace(self):
        b = xwb.parse(make_xwb("st", self.waves, alignment=2048, streaming=True))
        new = xwb.encode_like(b.entries[0], xwb.wav(tone(5000, 330), 1, 22050))
        out = xwb.parse(xwb.rebuild(b, {0: new}))
        self.assertEqual(out.wave(out.entries[0]), new)
        self.assertEqual(out.wave(out.entries[1]), b.wave(b.entries[1]))
        self.assertTrue(all(e.offset % 2048 == 0 for e in out.entries))


class SoundBankTest(unittest.TestCase):
    def test_parse(self):
        play = struct.pack("<IHH", 4, 1, 0)
        data = make_xsb(["fx"], [("one", 0), ("pick", 1), ("seq", 2), ("quiet", 0xFFFF)], [
            ("simple", 0, 0),
            ("variation", [(0, 0), (1, 0)]),
            ("tracks", [[(4, b"\0" * 12), (0, play), (3, b"\0" * 4)],
                        [(16, struct.pack("<I", 8) + b"\0" * 8), (1, struct.pack("<IHH", 4, 0, 0) + b"\0" * 12)]]),
        ])
        sb = xsb.parse(data)
        self.assertEqual(sb.wave_banks, ["fx"])
        self.assertEqual([c.name for c in sb.cues], ["one", "pick", "seq", "quiet"])
        self.assertEqual(sb.cue_waves(sb.cue("ONE")), [("fx", 0)])
        self.assertEqual(sb.cue_waves(sb.cue("pick")), [("fx", 0), ("fx", 1)])
        self.assertEqual(sb.cue_waves(sb.cue("seq")), [("fx", 1), ("fx", 0)])
        self.assertEqual(sb.cue_waves(sb.cue("quiet")), [])
        self.assertEqual(sb.problems, [])

    def test_registry(self):
        waves, sounds_ = xsb.read_registry("[ResidentWaveBanks]\nbank fx\tdataxbox/sound/sfx/fx.xwb\n"
                                           "[SoundBanks]\nbank fx\tdataxbox/sound/sfx/fx.xsb\n")
        self.assertEqual(waves, {"fx": "sound/sfx/fx.xwb"})
        self.assertEqual(sounds_, {"fx": "sound/sfx/fx.xsb"})


class SoundModTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data = root / "game" / "DATAxbox"
        (self.data / "SOUND" / "sfx").mkdir(parents=True)
        self.waves = [(adpcm_format(), xwb.encode_adpcm(tone(640), 1)),
                      (adpcm_format(), xwb.encode_adpcm(tone(320, 220), 1))]
        (self.data / "SOUND" / "sfx" / "fx.xwb").write_bytes(make_xwb("fx", self.waves))
        (self.data / "SOUND" / "sfx" / "fx.xsb").write_bytes(make_xsb(
            ["fx"], [("one", 0), ("pick", 1)], [("simple", 0, 0), ("variation", [(0, 0), (1, 0)])]))
        (self.data.parent / "rsm_xbox.ini").write_text(
            "[ResidentWaveBanks]\nbank fx\tdataxbox/sound/sfx/fx.xwb\n[SoundBanks]\nbank fx\tdataxbox/sound/sfx/fx.xsb\n")
        build(self.data / "assets.dsk", [(name_hash("sounds"), sounds.REGISTRY, registry_record("fx", "one", "beep"))])
        self.mod = root / "mod"
        (self.mod / "sounds" / "fx").mkdir(parents=True)
        self.lib = xsb.Library(self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def test_export_names(self):
        out = Path(self.tmp.name) / "export"
        written = sounds.export(self.lib, "fx", out)
        self.assertEqual(sorted(p.name for p in written), ["one.wav", "pick.1.wav", "pick.2.wav"])

    def test_targets(self):
        self.assertEqual(sounds.targets(self.lib, "fx", "pick")[1], [("fx", 0), ("fx", 1)])
        self.assertEqual(sounds.targets(self.lib, "fx", "pick.2")[1], [("fx", 1)])
        with self.assertRaises(ValueError):
            sounds.targets(self.lib, "fx", "pick.3")
        with self.assertRaises(ValueError):
            sounds.targets(self.lib, "fx", "nope")

    def test_build_replaces_wave(self):
        (self.mod / "sounds" / "fx" / "pick.1.wav").write_bytes(xwb.wav(tone(4410, 880, 44100), 1, 44100))
        out = Path(self.tmp.name) / "out"
        result = modbuild.build(self.mod, self.data, out)
        self.assertEqual(result.files, ["DATAxbox/SOUND/sfx/fx.xwb"])
        built = xwb.parse((out / "DATAxbox/SOUND/sfx/fx.xwb").read_bytes())
        self.assertEqual(built.wave(built.entries[1]), self.waves[1][1])
        self.assertEqual(built.entries[0].length, -(-2205 // 64) * 36)
        self.assertTrue(any("converted" in w for w in result.warnings))
        self.assertTrue(any("also plays for fx.one" in w for w in result.warnings))

    def test_unchanged_export_is_skipped(self):
        sounds.export(self.lib, "fx", self.mod / "sounds" / "fx", ["one"])
        self.assertTrue(sounds.unchanged(self.lib, self.mod / "sounds" / "fx" / "one.wav"))
        result = modbuild.build(self.mod, self.data, Path(self.tmp.name) / "out")
        self.assertEqual(result.files, [])

    def test_sounds_only_mod_is_a_source(self):
        from mercfoundry import sync
        (self.mod / "mod.json").write_text('{"name": "Sounds"}')
        self.assertTrue(sync.is_source(self.mod))

    def test_new_cue(self):
        (self.mod / "sounds" / "fx" / "new").mkdir()
        (self.mod / "sounds" / "fx" / "new" / "extra.wav").write_bytes(xwb.wav(tone(3000, 660), 1, 22050))
        out = Path(self.tmp.name) / "out"
        result = modbuild.build(self.mod, self.data, out)
        self.assertEqual(sorted(result.files), ["DATAxbox/SOUND/sfx/fx.xsb", "DATAxbox/SOUND/sfx/fx.xwb"])
        wb = xwb.parse((out / "DATAxbox/SOUND/sfx/fx.xwb").read_bytes())
        self.assertEqual(len(wb.entries), 3)
        self.assertEqual(wb.wave(wb.entries[0]), self.waves[0][1])
        raw = (out / "DATAxbox/SOUND/sfx/fx.xsb").read_bytes()
        sb = xsb.parse(raw)
        self.assertEqual([c.name for c in sb.cues], ["one", "pick", "extra"])
        self.assertEqual(sb.cue_waves(sb.cue("extra")), [("fx", 2)])
        self.assertEqual(sb.cue_waves(sb.cue("pick")), [("fx", 0), ("fx", 1)])
        self.assertEqual(struct.unpack_from("<H", raw, 6)[0], xsb.crc(raw))
        size = struct.unpack_from("<H", raw, 0x1A)[0]
        self.assertEqual(size, 16)
        for i, c in enumerate(sb.cues):
            self.assertEqual(struct.unpack_from("<H", raw, 0x38 + 20 * i + 14)[0], xsb.name_slot(c.name, size))
        arc = dsk.Archive(out / "assets.dsk")
        root = ucfb.parse(arc.read(arc.records[0]))[0]
        self.assertEqual([c.tag for c in root.children], [b"xcl_", b"xsl_", b"xcl_", b"xsl_"])
        raw_rec = arc.read(arc.records[0])
        added = raw_rec[len(registry_record("fx", "one", "beep")):]
        self.assertNotIn(b"fx.one", added)
        self.assertIn(b"name\0fx.extra\0bank\0fx\0wave_entry_index\x002\0", added)

    def test_new_cue_that_exists(self):
        (self.mod / "sounds" / "fx" / "new").mkdir()
        (self.mod / "sounds" / "fx" / "new" / "ONE.wav").write_bytes(xwb.wav(tone(100), 1, 22050))
        with self.assertRaises(ValueError):
            modbuild.build(self.mod, self.data, Path(self.tmp.name) / "out")

    def test_name_slot_and_crc(self):
        # Retail values: XACT's name hash and CRC-16/X.25.
        self.assertEqual(xsb.name_slot("wpn_dryfire_small", 16), 15)
        self.assertEqual(xsb.name_slot("engine_stop", 16), 12)
        self.assertEqual(xsb.crc(b"\0" * 8 + b"123456789"), 0x906E)

    def test_unknown_cue(self):
        (self.mod / "sounds" / "fx" / "nope.wav").write_bytes(xwb.wav(tone(100), 1, 22050))
        with self.assertRaises(ValueError):
            modbuild.build(self.mod, self.data, Path(self.tmp.name) / "out")


if __name__ == "__main__":
    unittest.main()
