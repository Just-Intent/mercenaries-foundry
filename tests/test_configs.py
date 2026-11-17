import json
import tempfile
import unittest
from pathlib import Path

from mercfoundry import configs, modbuild

XBOX = ("; top\nmipbias -0.88\n\n[Humans]\n"
        "AnimUpdateLow\t\t-1\t\t30\t0\nAnimUpdateLow\t\t10\t\t2\t0\n\n"
        "AnimUpdateMedium\t-1\t\t30\t0\nAnimUpdateMedium\t10\t\t2\t0\nAnimUpdateLow\t\t15\t\t4\t0\n"
        "AnimUpdateMedium\t60\t\t6\t0\n\n[FrontEndCanvas]\nPressStartNudgeX\t-2\n")
RETAIL = Path("external/Mercenaries-Recompiled/build/mercenaries/bin/Release/game_files/mercenaries-retail/xbox.ini")


class ConfigTests(unittest.TestCase):
    def test_set_lines_in_sections(self):
        out, changes = configs.apply(XBOX, {
            "": {"mipbias": "-0.5"},
            "Humans": {"AnimUpdateMedium": ["-1 30 0", "10 2 0", "15 4 0", "60 6 0"],
                       "AnimUpdateLow": ["-1 30 0", "10 2 0"]},
            "frontendcanvas": {"PressStartNudgeX": "0"}})
        self.assertIn("mipbias\t\t-0.5", out)
        humans = out.split("[Humans]")[1].split("[FrontEndCanvas]")[0]
        self.assertEqual([l.split()[0] for l in humans.splitlines() if l.strip()].count("AnimUpdateLow"), 2)
        self.assertIn("AnimUpdateMedium\t\t15 4 0", humans)
        self.assertIn("PressStartNudgeX\t\t0", out)
        self.assertEqual(changes[0], "mipbias")
        for bad in ({"Nope": {"x": "1"}},
                    {"Humans": {"AnimUpdateHigh": ["10 0 0"]}},                 # every 0 vblanks
                    {"Humans": {"AnimUpdateHigh": ["50 1 0", "30 2 0"]}},       # not ascending
                    {"Humans": {"AnimUpdateHigh": [f"{d} 1 0" for d in (1, 2, 3, 4, 5)]}},
                    {"Humans": {"AnimUpdateHigh": ["far 1 0"]}},
                    {"Humans": {"AnimUpdateHigh": ["10 1 2"]}}):
            with self.assertRaises(ValueError):
                configs.apply(XBOX, bad)

    @unittest.skipUnless(RETAIL.exists(), "needs the retail game files")
    def test_retail_medium_typo_fixed(self):
        text = RETAIL.read_bytes().decode("latin-1")
        fixed, _ = configs.apply(text, {"Humans": {
            "AnimUpdateLow": ["-1 30 0", "10 2 0", "15 4 0", "60 6 0", "1000 30 0"],
            "AnimUpdateMedium": ["-1 30 0", "10 2 0", "15 4 0", "60 6 0", "1000 15 0"]}})
        humans = fixed.split("[Humans]")[1].split("[FrontEndCanvas]")[0]
        rows = [l.split() for l in humans.splitlines() if l.strip().startswith("AnimUpdate")]
        self.assertEqual(sum(r[0] == "AnimUpdateMedium" for r in rows), 5)
        self.assertEqual(sum(r[0] == "AnimUpdateLow" for r in rows), 5)
        self.assertEqual(fixed.count("\r\n") > 0, text.count("\r\n") > 0)

    def test_build_writes_the_file_beside_dataxbox(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "game" / "DATAxbox").mkdir(parents=True)
            (d / "game" / "xbox.ini").write_text(XBOX, encoding="latin-1")
            mod = d / "mod"
            (mod / "config").mkdir(parents=True)
            (mod / "config" / "xbox.ini.json").write_text(json.dumps({"": {"mipbias": "-0.5"}}), encoding="utf-8")
            data = modbuild._Data(d / "game" / "DATAxbox")
            result = modbuild.Result()
            modbuild._apply_configs(data, mod, result)
            self.assertIn("mipbias\t\t-0.5", data.files["xbox.ini"].decode("latin-1"))
            self.assertEqual(result.changes, ["config xbox.ini: mipbias"])
            (mod / "config" / "options.ini.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                modbuild._apply_configs(data, mod, modbuild.Result())


if __name__ == "__main__":
    unittest.main()
