import unittest

from mercfoundry import sky

CONFIG = "RadarTexture rdrSW\r\n\r\nSunAngle\t\t140\t180\r\nFogRGBA\t\t\t120\t120\t120\t32\r\nSkyTexture\t\tskySummer\r\n\r\n[SoundAmbience]\r\nX 1\r\n"
XBOX = ("Scrub\t1\tshrubs\r\n\r\n[atmosphere]\r\n\r\nName\t\tsummer1\r\nSunlightRGB\t250\t175\t100\r\nRainChance\t0.0\r\n"
        "ProcessRGBA\t 1 1 1 0\r\nProcessRGBA\t 2 2 2 64\r\n\r\n[Atmosphere]\r\nName\t\tDMZ\r\nFogRGBA\t53 56 48 32\r\n")


class SkyTests(unittest.TestCase):
    def test_parse(self):
        p = sky.presets_of({"x.ini": XBOX})
        self.assertEqual(list(p), ["summer1", "dmz"])
        self.assertEqual(p["summer1"]["keys"][-2:], [("ProcessRGBA", "1 1 1 0"), ("ProcessRGBA", "2 2 2 64")])
        self.assertEqual(sky.base(CONFIG), {"SunAngle": "140\t180", "FogRGBA": "120\t120\t120\t32", "SkyTexture": "skySummer"})

    def test_apply(self):
        texts = {"SW/SW.ini": CONFIG, "SW/SW_XBOX.ini": XBOX}
        out, changes = sky.apply(texts, {
            "base": {"FogRGBA": "100 100 100 40", "Rain": "1"},
            "presets": {"Summer1": {"RainChance": "1.0", "ProcessRGBA": ["9 9 9 0"]},
                        "storm": {"copy": "dmz", "SnowChance": "0.5"}}})
        self.assertEqual(changes, ["base sky: FogRGBA, Rain", "preset summer1: RainChance, ProcessRGBA",
                                   "new preset storm (from DMZ)"])
        self.assertEqual(sky.base(out["SW/SW.ini"])["FogRGBA"], "100 100 100 40")
        self.assertIn("Rain\t\t1\r\n\r\n[SoundAmbience]", out["SW/SW.ini"])            # in the top block
        p = sky.presets_of({"x": out["SW/SW_XBOX.ini"]})
        self.assertEqual(dict(p["summer1"]["keys"])["RainChance"], "1.0")
        self.assertEqual([v for k, v in p["summer1"]["keys"] if k == "ProcessRGBA"], ["9 9 9 0"])
        self.assertEqual(p["storm"]["keys"], [("FogRGBA", "53 56 48 32"), ("SnowChance", "0.5")])
        self.assertNotIn("\n", out["SW/SW_XBOX.ini"].replace("\r\n", ""))                 # its own line endings
        for bad in ({"base": {"Scrub": "1"}}, {"presets": {"x y": {}}},
                    {"presets": {"new": {"copy": "nothere"}}}, {"presets": {"summer1": {"RainChance": "1\n[x]"}}}):
            with self.assertRaises(ValueError):
                sky.apply(texts, bad)

    def test_level_files(self):
        levels = "[LV_SW]\r\nName\t\tSW\r\nConfigFile\t\t\\SW\\SW.ini\r\n"
        have = {"SW/SW_XBOX.ini"}
        self.assertEqual(sky.level_files(levels, "sw", lambda rel: rel if rel in have else None),
                         ["SW/SW.ini", "SW/SW_XBOX.ini"])
        self.assertEqual(sky.level_files(levels, "nw", lambda rel: None), [])


if __name__ == "__main__":
    unittest.main()
