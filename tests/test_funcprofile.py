import struct
import tempfile
import unittest
from pathlib import Path

from mercfoundry import funcprofile

MAP = """ mercenaries_recomp

 Timestamp is d8260f29 (reproducible build file hash)

 Preferred load address is 0000000140000000

 Start         Length     Name                   Class
 0001:00000000 003e47d0H .text                   CODE

  Address         Publics by Value              Rva+Base               Lib:Object

 0003:0025b430       g_other_thing              00000001424ee430     xbox_kernel:xbox_memory_layout.obj
 0003:0025b434       g_recomp_current_func      00000001424ee434     xbox_kernel:xbox_memory_layout.obj
"""


def fake_exe(stamp):
    """Just enough of a PE file for its COFF timestamp."""
    d = bytearray(0x200)
    d[0:2] = b"MZ"
    struct.pack_into("<I", d, 0x3C, 0x80)
    d[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHI", d, 0x84, 0x8664, 1, stamp)
    return bytes(d)


class MapTest(unittest.TestCase):
    def test_parse(self):
        m = funcprofile.parse_map(MAP)
        self.assertEqual(m.timestamp, 0xD8260F29)
        self.assertEqual(m.base, 0x140000000)
        self.assertEqual(m.rva("g_recomp_current_func"), 0x24EE434)

    def test_missing_symbol(self):
        with self.assertRaises(ValueError):
            funcprofile.parse_map(MAP.replace("g_recomp_current_func", "g_recomp_other"))

    def test_not_a_map(self):
        with self.assertRaises(ValueError):
            funcprofile.parse_map("hello\n")

    def test_two_addresses(self):
        extra = " 0003:0025b438       g_recomp_current_func      00000001424ee438     x.obj\n"
        with self.assertRaises(ValueError):
            funcprofile.parse_map(MAP + extra)


class HandoffTest(unittest.TestCase):
    def game(self, tmp, stamp=0xD8260F29, with_map=True):
        g = Path(tmp)
        (g / funcprofile.EXE).write_bytes(fake_exe(stamp))
        if with_map:
            (g / funcprofile.MAP).write_text(MAP, encoding="utf-8")
        return g

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = self.game(tmp)
            line = funcprofile.write_handoff(g)
            self.assertIn("0x024EE434", line)
            out = funcprofile.settings_dir(g) / funcprofile.HANDOFF
            vals = funcprofile.read_handoff(out.read_text(encoding="ascii"))
            self.assertEqual(vals, {"rva": 0x24EE434, "timestamp": 0xD8260F29})
            ini = (funcprofile.settings_dir(g) / funcprofile.SETTINGS).read_text(encoding="ascii")
            self.assertIn("Enabled=0", ini)

    def test_settings_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = self.game(tmp)
            d = funcprofile.settings_dir(g)
            d.mkdir(parents=True)
            (d / funcprofile.SETTINGS).write_text("[Sampler]\nEnabled=1\n", encoding="ascii")
            funcprofile.write_handoff(g)
            self.assertIn("Enabled=1", (d / funcprofile.SETTINGS).read_text(encoding="ascii"))

    def test_mismatch_removes_old_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = self.game(tmp)
            funcprofile.write_handoff(g)
            out = funcprofile.settings_dir(g) / funcprofile.HANDOFF
            self.assertTrue(out.exists())
            (g / funcprofile.EXE).write_bytes(fake_exe(0x12345678))   # the port was rebuilt
            line = funcprofile.write_handoff(g)
            self.assertIn("sampler off", line)
            self.assertFalse(out.exists())

    def test_no_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = self.game(tmp, with_map=False)
            self.assertIn("sampler off", funcprofile.write_handoff(g))
            self.assertFalse((funcprofile.settings_dir(g) / funcprofile.HANDOFF).exists())

    def test_handoff_needs_symbol(self):
        with self.assertRaises(ValueError):
            funcprofile.read_handoff("[CurrentFunc]\nRva=0x10\nTimestamp=0x1\n")


TSV = """# Foundry Console function samples
label\taddress\tcount
# 10 s sampled
tank\t0x00141690\t300
tank\t0x001DCC10\t100
onfoot\t0x001DCC10\t400
onfoot\t0x00057F50\t90
menu\t0x00000000\t7
"""


class SamplesTest(unittest.TestCase):
    def write(self, tmp, text, name="a.tsv"):
        p = Path(tmp) / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_read_and_sum(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self.write(tmp, TSV)
            b = self.write(tmp, "tank\t0x00141690\t5\n", "b.tsv")
            s = funcprofile.read_samples([a, b])
            self.assertEqual(s["tank"][0x141690], 305)
            self.assertEqual(s["onfoot"], {0x1DCC10: 400, 0x57F50: 90})
            self.assertNotIn("menu", s)          # address 0 is skipped

    def test_bad_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                funcprofile.read_samples([self.write(tmp, "tank\t0x10\n")])

    def test_rank(self):
        s = {"tank": {0x10: 300, 0x20: 100, 0x30: 5}, "onfoot": {0x20: 400, 0x40: 90}}
        rows, n_mine, n_theirs = funcprofile.rank(s, "tank", ["onfoot"], min_count=10)
        self.assertEqual((n_mine, n_theirs), (405, 490))
        self.assertEqual([r.address for r in rows], [0x10])   # 0x20 is commoner on foot; 0x30 too few
        self.assertAlmostEqual(rows[0].rate, 1000 * 300 / 405)
        self.assertEqual(rows[0].other, 0)

    def test_rank_normalises(self):
        # Seen as often per sample under both labels: not specific.
        s = {"tank": {0x10: 100, 0x20: 100}, "onfoot": {0x10: 1000, 0x20: 1000}}
        rows, _, _ = funcprofile.rank(s, "tank", min_count=1)
        self.assertEqual(rows, [])

    def test_rank_order_and_only(self):
        s = {"a": {0x10: 50, 0x20: 50, 0x30: 50}, "b": {0x20: 10, 0x30: 1, 0x99: 500}}
        rows, _, _ = funcprofile.rank(s, "a", min_count=1)
        self.assertEqual([r.address for r in rows], [0x10, 0x30, 0x20])
        rows, _, _ = funcprofile.rank(s, "a", min_count=1, only=True)
        self.assertEqual([r.address for r in rows], [0x10])

    def test_rank_errors(self):
        with self.assertRaises(ValueError):
            funcprofile.rank({"a": {1: 1}}, "tank")
        with self.assertRaises(ValueError):
            funcprofile.rank({"a": {1: 1}}, "a")             # nothing to compare with
        with self.assertRaises(ValueError):
            funcprofile.rank({"a": {1: 1}, "b": {1: 1}}, "a", ["c"])

    def test_fold_to_function_start(self):
        doc = {"functions": [{"address": "0x00000100", "names": ["Thing_Update"], "file": "Thing.cpp",
                              "file_evidence": "direct"},
                             {"address": "0x00000200", "names": ["Caller"]}]}
        analysis = [{"start": "0x00000100", "end": "0x00000180", "section": ".text",
                     "calls_to": [], "called_by": ["0x00000200"]},
                    {"start": "0x00000200", "end": "0x00000210", "section": "D3D",
                     "calls_to": ["0x00000100"], "called_by": []}]
        d = funcprofile.Describer(doc, analysis)
        self.assertEqual(d.function_start(0x140), 0x100)
        self.assertEqual(d.function_start(0x190), 0x190)
        s = {"a": {0x100: 30, 0x140: 30}, "b": {0x200: 100}}
        rows, _, _ = funcprofile.rank(s, "a", min_count=1, fold=d.function_start)
        self.assertEqual([(r.address, r.count) for r in rows], [(0x100, 60)])
        info = d.describe(0x100)
        self.assertEqual(info["name"], "Thing_Update")
        self.assertEqual(info["callers"], ["Caller"])
        self.assertEqual(d.section(0x200), "D3D")
        lines = funcprofile.report(rows, d, "a", "b", 60, 100)
        self.assertIn("Thing_Update [Thing.cpp]", lines[2])
        self.assertIn("called by: Caller", lines[3])


if __name__ == "__main__":
    unittest.main()
