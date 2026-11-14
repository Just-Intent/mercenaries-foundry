import struct
import tempfile
import unittest
from pathlib import Path

from mercfoundry import hangsnap

# A cut-down MSVC linker map in the layout mercenaries_recomp.map uses.
MAP = """\
 mercenaries_recomp

 Timestamp is d8260f29 (reproducible build file hash)

 Preferred load address is 0000000140000000

 Start         Length     Name                   Class
 0001:00000000 003e47d0H .text                   CODE

  Address         Publics by Value              Rva+Base               Lib:Object

 0000:00000000       ___safe_se_handler_count   0000000000000000     <absolute>
 0001:00001000       WinMain                    0000000140002000 f   main.obj
 0001:00456e70       sub_00011D80               0000000140457e70 f   foundry_hooks.obj
 0001:004682c0       sub_00011D80_body          00000001404692c0 f   recomp_0000.obj
 0001:01a3bdc0       sub_0028E650               0000000141a3cdc0 f   recomp_0013.obj
 0001:01a3c000       sub_0028E700               0000000141a3d000 f   recomp_0013.obj
 0003:0025b410       g_xbox_mem_offset          00000001424ee410     xbox_kernel:xbox_memory_layout.obj
 0003:0025b418       g_eax                      00000001424ee418     xbox_kernel:xbox_memory_layout.obj
 0003:0025b41c       g_ecx                      00000001424ee41c     xbox_kernel:xbox_memory_layout.obj
 0003:0025b420       g_edx                      00000001424ee420     xbox_kernel:xbox_memory_layout.obj
 0003:0025b424       g_esp                      00000001424ee424     xbox_kernel:xbox_memory_layout.obj
 0003:0025b428       g_ebx                      00000001424ee428     xbox_kernel:xbox_memory_layout.obj
 0003:0025b42c       g_esi                      00000001424ee42c     xbox_kernel:xbox_memory_layout.obj
 0003:0025b430       g_edi                      00000001424ee430     xbox_kernel:xbox_memory_layout.obj
 0003:0025b434       g_recomp_current_func      00000001424ee434     xbox_kernel:xbox_memory_layout.obj
 0003:0025b438       g_recomp_entry_trace_enabled 00000001424ee438     xbox_kernel:xbox_memory_layout.obj
 0003:0025b43c       g_recomp_recent_funcs      00000001424ee43c     xbox_kernel:xbox_memory_layout.obj
 0003:0025b44c       g_recomp_recent_func_idx   00000001424ee44c     xbox_kernel:xbox_memory_layout.obj
 0003:0025b450       g_recomp_recent_game_func_idx 00000001424ee450     xbox_kernel:xbox_memory_layout.obj
 0003:0025b454       g_recomp_recent_game_funcs 00000001424ee454     xbox_kernel:xbox_memory_layout.obj
 0003:0025b464       g_recomp_recent_asset_requests 00000001424ee464     xbox_kernel:xbox_memory_layout.obj

 entry point at        0001:01c17088

 Static symbols

 0001:00002000       static_helper              0000000140003000 f   main.obj
"""

DOC = {"functions": [
    {"address": "0x0028E650", "names": ["D3D_KickPushBuffer"]},
    {"address": "0x00011D80", "names": ["Matrix_Copy"]},
    {"address": "0x00050000", "names": []},
]}
SECTIONS = [(".text", 0x11000, 0x24461C), ("D3D", 0x288640, 0x13234), (".rdata", 0x2DBCE0, 0x2F8C0)]


def globals_block(lmap, values, recent, recent_idx, game, game_idx):
    start, size, offs = lmap.block_layout()
    b = bytearray(size)
    struct.pack_into("<Q", b, 0, 0x10000)
    for name, v in values.items():
        struct.pack_into("<I", b, offs[name], v)
    struct.pack_into(f"<{len(recent)}I", b, offs["g_recomp_recent_funcs"], *recent)
    struct.pack_into("<I", b, offs["g_recomp_recent_func_idx"], recent_idx)
    struct.pack_into(f"<{len(game)}I", b, offs["g_recomp_recent_game_funcs"], *game)
    struct.pack_into("<I", b, offs["g_recomp_recent_game_func_idx"], game_idx)
    return start, bytes(b)


def snapshot_text(grva, block, esp, stack, threads):
    words = struct.unpack(f"<{len(block) // 4}I", block)
    out = ["base 7FF6247D0000 2D0E000", f"globals {grva:X} {len(block):X}"]
    out += ["  " + " ".join(f"{w:08X}" for w in words[i:i + 8]) for i in range(0, len(words), 8)]
    out.append(f"guest-stack {esp:08X}")
    out += ["  " + " ".join(f"{w:08X}" for w in stack[i:i + 8]) for i in range(0, len(stack), 8)]
    for tid, desc, frames in threads:
        out.append(f"thread {tid}" + (f" {desc}" if desc else ""))
        out += [f"  {f}" for f in frames]
    return "\n".join(out) + "\n"


class LinkerMapTest(unittest.TestCase):
    def setUp(self):
        self.lmap = hangsnap.LinkerMap(MAP)

    def test_header(self):
        self.assertEqual(self.lmap.base, 0x140000000)
        self.assertEqual(self.lmap.timestamp, 0xD8260F29)

    def test_symbol_rva(self):
        self.assertEqual(self.lmap.rva("g_xbox_mem_offset"), 0x24EE410)
        self.assertEqual(self.lmap.rva("static_helper"), 0x3000)
        self.assertNotIn("___safe_se_handler_count", self.lmap.symbols)

    def test_function_at(self):
        self.assertEqual(self.lmap.function_at(0x1A3CDC0 + 0x40), ("sub_0028E650", 0x40))
        self.assertEqual(self.lmap.function_at(0x1A3D000), ("sub_0028E700", 0))
        self.assertEqual(self.lmap.function_at(0x3010), ("static_helper", 0x10))
        self.assertIsNone(self.lmap.function_at(0x10))
        # data symbols never count as the containing function
        self.assertEqual(self.lmap.function_at(0x24EE420)[0], "sub_0028E700")

    def test_block_layout(self):
        start, size, offs = self.lmap.block_layout()
        self.assertEqual(start, 0x24EE410)
        self.assertEqual(size, 0x54)        # through g_recomp_recent_game_funcs
        self.assertEqual(offs["g_esp"], 0x14)
        self.assertEqual(offs["g_recomp_current_func"], 0x24)
        self.assertNotIn("g_recomp_recent_asset_requests", offs)

    def test_guest_starts(self):
        self.assertEqual(self.lmap.guest_starts(), [0x11D80, 0x28E650, 0x28E700])

    def test_not_a_map(self):
        with self.assertRaises(ValueError):
            hangsnap.LinkerMap("hello\n")

    def test_map_timestamp(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.map"
            p.write_text(MAP, encoding="latin-1")
            self.assertEqual(hangsnap.map_timestamp(p), 0xD8260F29)


class GuestNamesTest(unittest.TestCase):
    def setUp(self):
        self.guest = hangsnap.GuestNames(DOC, SECTIONS, [0x11D80, 0x28E650, 0x28E700])

    def test_guest_address(self):
        self.assertEqual(hangsnap.guest_address("sub_0028E650"), 0x28E650)
        self.assertEqual(hangsnap.guest_address("sub_00011D80_body"), 0x11D80)
        self.assertIsNone(hangsnap.guest_address("WinMain"))
        self.assertIsNone(hangsnap.guest_address("sub_123"))

    def test_label(self):
        self.assertEqual(self.guest.label(0x28E650), "0x0028E650 D3D_KickPushBuffer [D3D]")
        self.assertEqual(self.guest.label(0x11D80), "0x00011D80 Matrix_Copy")   # .text isn't shown
        self.assertEqual(self.guest.label(0x50000), "0x00050000")                 # entry with no names
        self.assertEqual(self.guest.label(0x900000), "0x00900000")                # outside the XBE

    def test_describe(self):
        self.assertEqual(self.guest.describe(0x28E660), "0x0028E660 (in 0x0028E650 D3D_KickPushBuffer [D3D] +0x10)")
        self.assertEqual(self.guest.describe(0x28E700), "0x0028E700 [D3D]")
        self.assertEqual(self.guest.describe(0x11000), "0x00011000")

    def test_is_code(self):
        self.assertTrue(self.guest.is_code(0x11D80))
        self.assertTrue(self.guest.is_code(0x28E650))
        self.assertFalse(self.guest.is_code(0x2DBCE0))
        self.assertFalse(self.guest.is_code(0x10))


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.lmap = hangsnap.LinkerMap(MAP)
        self.guest = hangsnap.GuestNames(DOC, SECTIONS, self.lmap.guest_starts())
        grva, block = globals_block(
            self.lmap, {"g_eax": 0x4203, "g_esp": 0x8BF84C, "g_recomp_current_func": 0x28E650,
                        "g_recomp_entry_trace_enabled": 1},
            recent=[0x11D80, 0x11D80, 0x28E650, 0x28E700], recent_idx=6,
            game=[0x11D80, 0x50000, 0, 0], game_idx=2)
        main = ["ntdll.dll+1623D4", "exe+1A3CE00", "exe+1A3D010", "exe+3010", "exe+2010","KERNEL32.DLL+2DDA7"]
        worker = ["ntdll.dll+161DD4", "nvwgf2umx.dll+946168"]
        self.text = snapshot_text(grva, block, 0x8BF84C, [0x28E660, 0x12345678, 0x2DBCE0, 0x11D90],
                                  [(1, "", main), (2, "", worker), (3, "", worker), (4, "audio", worker)])

    def test_parse(self):
        snap = hangsnap.parse_snapshot(self.text)
        self.assertEqual(snap["base"], 0x7FF6247D0000)
        self.assertEqual(snap["globals_rva"], 0x24EE410)
        self.assertEqual(len(snap["globals"]), 0x54)
        self.assertEqual(snap["guest_esp"], 0x8BF84C)
        self.assertEqual(snap["guest_stack"], [0x28E660, 0x12345678, 0x2DBCE0, 0x11D90])
        self.assertEqual([t[0] for t in snap["threads"]], [1, 2, 3, 4])
        self.assertEqual(snap["threads"][0][2][1], ("exe", 0x1A3CE00))
        self.assertEqual(snap["threads"][3][1], "audio")

    def test_ring(self):
        self.assertEqual(hangsnap.ring([10, 11, 12, 13], 2), [10, 11])
        self.assertEqual(hangsnap.ring([14, 15, 12, 13], 6), [12, 13, 14, 15])

    def test_group_threads(self):
        groups = hangsnap.group_threads(hangsnap.parse_snapshot(self.text)["threads"])
        self.assertEqual([g[0] for g in groups], [[1], [2, 3], [4]])

    def test_report(self):
        out = "\n".join(hangsnap.report(hangsnap.parse_snapshot(self.text), self.lmap, self.guest))
        self.assertIn("eax=00004203", out)
        self.assertIn("guest current function: 0x0028E650 D3D_KickPushBuffer [D3D]", out)
        self.assertIn("sub_0028E650+0x40  (guest 0x0028E650 D3D_KickPushBuffer [D3D])", out)
        self.assertIn("sub_0028E700+0x10  (guest 0x0028E700 [D3D])", out)
        self.assertIn("static_helper+0x10", out)
        self.assertIn("WinMain+0x10", out)
        self.assertIn("nvwgf2umx.dll+0x946168", out)
        self.assertIn("2 threads with this stack (2, 3):", out)
        # recent ring: index 6 in a 4-entry ring -> oldest at slot 2
        recent = out.split("entered most recently")[1]
        self.assertLess(recent.index("0x0028E650"), recent.index("0x00011D80 Matrix_Copy  x2"))
        game = out.split("game (not library)")[1]
        self.assertIn("0x00050000", game)
        self.assertIn("esp+0x000  0x0028E660 (in 0x0028E650 D3D_KickPushBuffer [D3D] +0x10)", out)
        self.assertIn("esp+0x00C  0x00011D90 (in 0x00011D80 Matrix_Copy +0x10)", out)
        self.assertNotIn("12345678", out.split("guest stack")[1])
        self.assertNotIn("002DBCE0", out.split("guest stack")[1])
        # the main thread (running the port's code) comes before the workers
        self.assertLess(out.index("thread 1:"), out.index("threads with this stack"))
        self.assertNotIn("tracing is off", out)


if __name__ == "__main__":
    unittest.main()
