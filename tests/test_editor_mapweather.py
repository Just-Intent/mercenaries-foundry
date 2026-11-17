import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mercfoundry.editor.scene import Scene

LEVELS = "[LevelNamesList]\nLV_SW\n\n[LV_SW]\nName\t\tSW\nPath\t\t\\SW\\\nConfigFile\t\\SW\\SW.ini\n"
SW_INI = "SunAngle\t\t140 180\n\n[Atmosphere]\nName\t\tsummer1\nSkyTexture\t\tskySummer\n\n[Atmosphere]\nName\t\tPYeast\nSkyTexture\t\tskytexture_peru2\n"


class MapWeatherTests(unittest.TestCase):
    """The Map panel's weather on arrival (maps/<name>.json "weather")."""

    def test_weather_shown_saved_and_other_keys_kept(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            data = root / "DATAxbox"
            (data / "SW").mkdir(parents=True)
            (data / "levels.ini").write_text(LEVELS, encoding="latin-1")
            (data / "SW" / "SW.ini").write_text(SW_INI, encoding="latin-1")
            sc = Scene.__new__(Scene)
            sc.data = data
            sc.mod = root / "mod"
            (sc.mod / "maps").mkdir(parents=True)
            (sc.mod / "sky").mkdir()
            sc.context = [sc.mod]
            sc._modded_terrain = lambda: SimpleNamespace(files={})
            (sc.mod / "maps" / "isle.json").write_text(json.dumps({"from": "blank", "size": 1024, "weather": "gaza"}))
            (sc.mod / "sky" / "sw.json").write_text(json.dumps({"presets": {"gaza": {"copy": "summer1"}}}))
            got = sc.map_settings("isle")
            self.assertEqual(got["weather"], "gaza")
            self.assertEqual(got["presets"], ["summer1", "PYeast", "gaza"])
            # saving without "weather" (an older page) keeps it and every key the panel doesn't know
            sc.save_map_settings("isle", {"message": "Hi"})
            doc = json.loads((sc.mod / "maps" / "isle.json").read_text(encoding="utf-8"))
            self.assertEqual(doc, {"from": "blank", "size": 1024, "weather": "gaza", "message": "Hi"})
            sc.save_map_settings("isle", {"weather": "summer1", "message": "Hi"})
            self.assertEqual(json.loads((sc.mod / "maps" / "isle.json").read_text(encoding="utf-8"))["weather"], "summer1")
            sc.save_map_settings("isle", {"weather": "", "message": "Hi"})
            self.assertNotIn("weather", json.loads((sc.mod / "maps" / "isle.json").read_text(encoding="utf-8")))
            with self.assertRaises(ValueError):
                sc.save_map_settings("isle", {"weather": "bad name"})


if __name__ == "__main__":
    unittest.main()
