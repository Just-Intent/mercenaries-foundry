import tempfile
import unittest
from pathlib import Path

from mercfoundry import porthooks

GEN = """/* generated */
void sub_00011000(void)
{
    eax = 1;
    PUSH32(esp, 0); sub_00012000(); /* call 0x00012000 */
}

void sub_00012000(void)
{
    eax = 2;
}
"""


class PortHooksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.gen = Path(self.tmp.name)
        (self.gen / "recomp_dispatch.c").write_text("/* table */\n")
        (self.gen / "recomp_0000.c").write_text(GEN)

    def tearDown(self):
        self.tmp.cleanup()

    def test_apply_is_idempotent_and_removable(self):
        hooked, missing = porthooks.apply(self.gen, [0x12000, 0x99999])
        self.assertEqual((hooked, missing), ([0x12000], [0x99999]))
        text = (self.gen / "recomp_0000.c").read_text()
        self.assertIn("void sub_00012000_body(void)", text)
        self.assertIn("void sub_00011000(void)", text)                  # not hooked: untouched
        self.assertIn("sub_00012000(); /* call 0x00012000 */", text)   # callers keep the name
        wrappers = (self.gen / porthooks.HOOK_FILE).read_text()
        self.assertIn("void sub_00012000(void) { if (!g_foundry_hook_on[0]) { sub_00012000_body(); return; }", wrappers)
        self.assertIn("*count = 1u;", wrappers)
        # Again, with a different set: the old one is restored, the new one renamed.
        porthooks.apply(self.gen, [0x11000])
        text = (self.gen / "recomp_0000.c").read_text()
        self.assertIn("void sub_00011000_body(void)", text)
        self.assertIn("void sub_00012000(void)", text)
        porthooks.apply(self.gen, [], remove=True)
        self.assertEqual((self.gen / "recomp_0000.c").read_text(), GEN)
        self.assertFalse((self.gen / porthooks.HOOK_FILE).exists())

    def test_needs_the_gen_folder(self):
        with self.assertRaises(FileNotFoundError):
            porthooks.apply(self.gen / "nope", [0x12000])


if __name__ == "__main__":
    unittest.main()
