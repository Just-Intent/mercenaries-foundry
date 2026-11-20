import base64
import json
import tempfile
import unittest
from pathlib import Path

from mercfoundry.editor import content


class MapKeysTests(unittest.TestCase):
    """The editor's HQs/medevac/HUD art panel: reads and writes maps/<map>.json
    keys, checked as the build checks them, and leaves the file's other keys."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = Path(self.tmp.name)
        (self.mod / "maps").mkdir()
        (self.mod / "maps" / "isle.json").write_text(json.dumps({"from": "blank", "start": {"position": [1, 2, 3]}}))
        (self.mod / "mod.json").write_text(json.dumps({"name": "Isle", "generated": [{"file": "maps/isle.json", "keys": ["hqs"]}]}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_and_save(self):
        d = content.map_keys(self.mod, "isle")
        self.assertTrue(d["new"])
        self.assertEqual(d["generated"], ["hqs"])
        self.assertFalse(content.map_keys(self.mod, "sw")["new"])
        content.save_map_keys(self.mod, "isle", {"medevac": {"position": [5, 6, 7], "heading": 90},
                                                 "hq_respawn": {"allies": {"position": [1, 1, 1]}},
                                                 "hud_textures": {"icons01": "isle_icons01"}})
        doc = json.loads((self.mod / "maps" / "isle.json").read_text())
        self.assertEqual(doc["start"], {"position": [1, 2, 3]})          # the rest kept
        self.assertEqual(doc["medevac"]["heading"], 90)
        content.save_map_keys(self.mod, "isle", {"hud_textures": None})
        self.assertNotIn("hud_textures", json.loads((self.mod / "maps" / "isle.json").read_text()))

    def test_refuses_what_the_build_would(self):
        for bad in ({"medevac": {"position": [1, 2]}}, {"hq_respawn": {"cups": {"position": [1, 2, 3]}}},
                    {"interiors": {"allies": {"guard": {"position": [1, 2, 3]}}}}, {"start": {}}):
            with self.assertRaises(ValueError):
                content.save_map_keys(self.mod, "isle", bad)
        with self.assertRaises(ValueError):
            content.save_map_keys(self.mod, "nowhere", {"medevac": None})


class DeckFilesTests(unittest.TestCase):
    def test_deck_save_and_portrait(self):
        from mercfoundry import png
        with tempfile.TemporaryDirectory() as t:
            mod = Path(t)
            (mod / "maps").mkdir()
            (mod / "maps" / "isle.json").write_text("{}")
            doc = {"suit": "clubs", "cards": [{"card": "2", "name": "A", "where": "kept as written"}]}
            content.save_deck(mod, "deck/clubs.json", doc)
            files = content.deck_files(mod)["files"]
            self.assertEqual(files[0]["doc"]["cards"][0]["where"], "kept as written")
            self.assertIsNone(files[0]["problem"])
            with self.assertRaises(ValueError):
                content.save_deck(mod, "deck/clubs.json", {"suit": "cups", "cards": []})
            with self.assertRaises(ValueError):
                content.save_deck(mod, "../evil.json", doc)
            raw = png.encode(2, 2, bytes(16))
            out = content.save_portrait(mod, "clubs", "2", base64.b64encode(raw).decode())
            self.assertEqual(out["file"], "deck/clubs2.png")
            self.assertIn("128x128", out["warning"])
            self.assertEqual(content.deck_files(mod)["files"][0]["portraits"]["2"], "deck/clubs2.png")
            self.assertEqual(content.mod_png(mod, "deck/clubs2.png"), raw)
            self.assertIsNone(content.mod_png(mod, "../x.png"))


if __name__ == "__main__":
    unittest.main()
