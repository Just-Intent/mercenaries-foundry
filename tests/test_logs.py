import tempfile
import unittest
import zipfile
from pathlib import Path

from mercfoundry import logs


class CollectTest(unittest.TestCase):
    def test_gathers_logs_from_the_mod_cache_and_skips_saves(self):
        with tempfile.TemporaryDirectory() as d:
            game = Path(d)
            (game / "foundry-sync.log").write_text("sync ok")
            (game / "mod-loader.ini").write_bytes(
                ("﻿[Mod.0]\r\nFolder=\"Mercenaries Gaza\"\r\nEnabled=1\r\n").encode("utf-16-le"))
            run = game / "mod-cache" / ("ab" * 32) / "runtime"
            (run / "preview-logs").mkdir(parents=True)
            (run / "preview-logs" / "preview-1.log").write_text("frame")
            cache = game / "mod-cache" / ("ab" * 32) / "storage" / "Cache"
            cache.mkdir(parents=True)
            (cache / "PoiDump.htm").write_text("<html>")
            (game / "saves").mkdir()
            (game / "saves" / "slot1.sav").write_text("save")
            (game / "hangsnap").mkdir()
            (game / "hangsnap" / "hangsnap-1.txt").write_text("stacks")
            path = logs.collect(game)
            self.assertEqual(path.parent, game / "Logs for Claude")
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
                about = z.read("about.txt").decode()
            self.assertIn("foundry-sync.log", names)
            self.assertIn("mod-cache/abababababab/preview-logs/preview-1.log", names)
            self.assertIn("mod-cache/abababababab/PoiDump.htm", names)
            self.assertIn("hangsnap/hangsnap-1.txt", names)
            self.assertFalse(any("sav" in n for n in names))
            self.assertIn("mods on: Mercenaries Gaza", about)
