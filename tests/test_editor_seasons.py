import json
import tempfile
import unittest
from pathlib import Path

from mercfoundry.editor.scene import Scene

LEVELS = "[LevelNamesList]\nLV_isle\n\n[LV_isle]\nName\t\tisle\nPath\t\t\\TEST\\\nConfigFile\t\\TEST\\isle.ini\n"
CONFIG = ("SkyTexture\t\tskytexture_peru2\nFogRGBA\t\t120 120 120 32\n\n"
          "[Winter]\nSkyTexture\t\tskytexture_nwwinter\nSunRGB\t\t32 32 32\nProcessRGBA\t\t16 32 48 128\nProcessRGBA\t\t48 64 80 80\n\n"
          "[Summer]\nSkyTexture\t\tskytexture_peru2\nSunRGB\t\t96 96 96\n")


class SkySeasonTests(unittest.TestCase):
    """The Sky panel's season sections ([Winter], [Fall], [Summer] in a new map's config)."""

    def scene(self, root):
        data = root / "DATAxbox"
        (data / "TEST").mkdir(parents=True)
        (data / "levels.ini").write_text(LEVELS, encoding="latin-1")
        (data / "TEST" / "isle.ini").write_text(CONFIG, encoding="latin-1")
        sc = Scene.__new__(Scene)
        sc.data = data
        sc.mod = root / "mod"
        sc.mod.mkdir()
        sc._sky_textures = []
        return sc

    def test_seasons_shown_and_saved(self):
        with tempfile.TemporaryDirectory() as d:
            sc = self.scene(Path(d))
            got = sc.sky("isle")
            self.assertEqual([s["name"] for s in got["seasons"]], ["Winter", "Summer"])
            self.assertEqual(got["seasons"][0]["keys"], [["SkyTexture", "skytexture_nwwinter"], ["SunRGB", "32 32 32"],
                                                         ["ProcessRGBA", "16 32 48 128"], ["ProcessRGBA", "48 64 80 80"]])
            out = sc.save_sky("isle", {"seasons": {"Summer": {"SkyTexture": "skySummer", "SunRGB": " "},
                                                   "Winter": {"ProcessRGBA": ["1 2 3 4"]}, "Fall": {}}})
            self.assertEqual(out, {"file": "sky/isle.json", "empty": False})
            saved = json.loads((sc.mod / "sky" / "isle.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, {"seasons": {"Summer": {"SkyTexture": "skySummer"}, "Winter": {"ProcessRGBA": ["1 2 3 4"]}}})
            self.assertEqual(sc.sky("isle")["mod"], saved)
            with self.assertRaises(ValueError):                 # a season the config hasn't
                sc.save_sky("isle", {"seasons": {"Fall": {"SkyTexture": "skySummer"}}})
            self.assertEqual(sc.save_sky("isle", {"seasons": {}}), {"file": "sky/isle.json", "empty": True})
            self.assertFalse((sc.mod / "sky" / "isle.json").exists())

    def test_ground_lighting_saved_with_the_sky(self):
        with tempfile.TemporaryDirectory() as d:
            sc = self.scene(Path(d))
            sc._forget_terrain = lambda name: None
            self.assertIsNone(sc.sky("isle")["lighting"]["doc"])
            # the config has no SunAngle: lighting needs one, from the panel's base or its own
            with self.assertRaises(ValueError):
                sc.save_sky("isle", {}, {"shadow": 0.5})
            out = sc.save_sky("isle", {"base": {"SunAngle": "150 270"}}, {"shadow": 0.5})
            self.assertEqual(out["lighting"], "terrain/isle.lighting.json")
            self.assertEqual(sc.sky("isle")["lighting"]["doc"], {"shadow": 0.5})
            self.assertEqual(sc._sun_angle("isle", {"SunAngle": "120 90"}), "120 90")
            with self.assertRaises(ValueError):            # refused before anything is written
                sc.save_sky("isle", {"base": {"SunAngle": "150 270"}}, {"shadow": 3})
            self.assertEqual(sc.sky("isle")["lighting"]["doc"], {"shadow": 0.5})
            sc.save_sky("isle", {"base": {"SunAngle": "150 270"}})          # lighting kept
            self.assertTrue((sc.mod / "terrain" / "isle.lighting.json").exists())
            sc.save_sky("isle", {}, None)                                     # removed
            self.assertFalse((sc.mod / "terrain" / "isle.lighting.json").exists())


if __name__ == "__main__":
    unittest.main()
