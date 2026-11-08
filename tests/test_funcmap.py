import json
import struct
import tempfile
import unittest
from pathlib import Path

from mercfoundry import funcmap

BASE = 0x10000
TEXT_VA, RDATA_VA = 0x11000, 0x2ED000


def fake_xbe(rdata: bytes, text_size=0x1000) -> bytes:
    """A minimal XBE: header, two section headers, .text and .rdata."""
    head = bytearray(0x400)
    head[0:4] = b"XBEH"
    struct.pack_into("<I", head, 0x104, BASE)
    sec_hdr = 0x200
    struct.pack_into("<II", head, 0x11C, 2, BASE + sec_hdr)
    names = 0x300
    head[names:names + 6] = b".text\0"
    head[names + 8:names + 15] = b".rdata\0"
    text_raw = len(head)
    rdata_raw = text_raw + text_size
    struct.pack_into("<6I", head, sec_hdr, 0, TEXT_VA, text_size, text_raw, text_size, BASE + names)
    struct.pack_into("<6I", head, sec_hdr + 56, 0, RDATA_VA, len(rdata), rdata_raw, len(rdata), BASE + names + 8)
    return bytes(head) + b"\xcc" * text_size + rdata


def rdata_with_table(table_va):
    """Strings, then a {name, fn} table at table_va (relative to RDATA_VA)."""
    data = bytearray(0x1000)
    strings = {"Mission_Spawn": 0x10, "Faction_SetRelation": 0x30, "IsAlive": 0x50, "GetYaw": 0x60}
    for s, off in strings.items():
        data[off:off + len(s) + 1] = s.encode() + b"\0"
    off = table_va - RDATA_VA
    struct.pack_into("<6I", data, off, RDATA_VA + 0x10, 0x11100, RDATA_VA + 0x30, 0x11200, 0, 0)
    # Actor_ table
    off2 = 0x2EDCA8 - RDATA_VA
    struct.pack_into("<6I", data, off2, RDATA_VA + 0x50, 0x11300, RDATA_VA + 0x60, 0x11700, 0, 0)
    return bytes(data)


class FuncMapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / "default.xbe").write_bytes(fake_xbe(rdata_with_table(0x2ED308)))
        funcs = [{"start": f"0x{a:08X}", "end": f"0x{a + 0x40:08X}", "section": ".text",
                  "calls_to": [], "called_by": []} for a in (0x11100, 0x11200, 0x11300, 0x11400, 0x11500, 0x11600, 0x11700)]
        funcs[0]["calls_to"] = ["0x00011400"]
        (d / "functions.json").write_text(json.dumps(funcs))
        strings = [{"address": "0x002EE000", "string": ".\\Source\\RsActor.cpp [582]", "referenced_from": ["0x00011410"]},
                   {"address": "0x002EE020", "string": ".\\Source\\RsActor.cpp [600]", "referenced_from": ["0x00011620"]},
                   {"address": "0x002EE040", "string": "unrelated", "referenced_from": ["0x00011510"]}]
        (d / "strings.json").write_text(json.dumps(strings))
        manual = {"functions": [{"address": "0x00011400", "name": "Spawner_SpawnTemplate",
                                 "signature": "void f(void)", "evidence": "test"}],
                  "globals": [{"address": "0x004031B0", "name": "g_Spawner", "evidence": "test"}]}
        (d / "manual.json").write_text(json.dumps(manual))
        self.dir = d
        self.doc = funcmap.build(d / "default.xbe", d, d / "manual.json")
        self.by = {int(e["address"], 16): e for e in self.doc["functions"]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_lua_bindings(self):
        self.assertEqual(self.by[0x11100]["names"], ["Lua_Mission_Spawn"])
        self.assertEqual(self.by[0x11100]["commands"], ["Mission_Spawn"])
        self.assertEqual(self.by[0x11300]["names"], ["Lua_Actor_IsAlive"])
        self.assertEqual(self.by[0x11300]["commands"], ["Actor_IsAlive"])
        self.assertEqual(self.doc["counts"]["lua_commands"], 4)

    def test_source_files(self):
        self.assertEqual((self.by[0x11400]["file"], self.by[0x11400]["file_evidence"], self.by[0x11400]["lines"]),
                         ("RsActor.cpp", "direct", [582]))
        self.assertEqual(self.by[0x11500]["file_evidence"], "between")   # between two RsActor.cpp functions
        self.assertEqual(self.by[0x11600]["file_evidence"], "direct")

    def test_manual_names_and_exports(self):
        self.assertEqual(funcmap.name_of(self.by[0x11400]), "Spawner_SpawnTemplate")
        h = funcmap.header(self.doc)
        self.assertIn("#define MF_FN_Lua_Mission_Spawn 0x00011100u", h)
        self.assertIn("#define MF_FN_Spawner_SpawnTemplate 0x00011400u  /* void f(void) */", h)
        self.assertIn("#define MF_VAR_g_Spawner 0x004031B0u", h)
        self.assertEqual(funcmap.ghidra_names(self.doc)["0x00011100"], "Lua_Mission_Spawn")
        lines = funcmap.show(self.doc, "Lua_Mission_Spawn", self.dir)
        self.assertTrue(any("0x00011400 Spawner_SpawnTemplate" in ln for ln in lines))
        self.assertEqual([funcmap.name_of(e) for e in funcmap.find(self.doc, "faction")], ["Lua_Faction_SetRelation"])

    def test_manual_address_must_be_a_function(self):
        bad = {"functions": [{"address": "0x00011404", "name": "X", "evidence": "test"}]}
        (self.dir / "bad.json").write_text(json.dumps(bad))
        with self.assertRaises(ValueError):
            funcmap.build(self.dir / "default.xbe", self.dir, self.dir / "bad.json")


if __name__ == "__main__":
    unittest.main()
