import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mercfoundry import animation, animtable, dsk
from mercfoundry.editor import anims
from mercfoundry.hashing import name_hash
from tests.test_animation import anim_record
from tests.test_animtable import table
from tests.test_dsk import build as build_dsk


class FakeScene:
    def __init__(self, data, mod):
        self.archives = [dsk.Archive(p) for p in sorted(data.glob("*.dsk"))]
        self.mod = mod


class AnimationsPanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / "data").mkdir()
        build_dsk(d / "data" / "assets.dsk", [(name_hash("guy_table"), animtable.ANIMTABLE, table()),
                                              (name_hash("test_anim"), animation.ANIMATION, anim_record())])
        self.scene = FakeScene(d / "data", d / "mod")

    def tearDown(self):
        self.tmp.cleanup()

    def test_tables_clips_and_saving_changes(self):
        s = self.scene
        self.assertEqual([t["name"] for t in anims.tables(s)], ["guy_table"])
        self.assertEqual(anims.clip_names(s), ["test_anim"])
        doc = anims.table(s, "GUY_TABLE")
        self.assertIsNone(doc["file"])
        self.assertEqual(doc["table"]["sets"][0]["entries"][0]["meaning"], ["loops", "replaceable at equal priority"])
        self.assertEqual(doc["flags"]["0x1"][0], "loops")
        # one entry changed: only it is written
        mine = doc["table"]
        mine["sets"][0]["entries"][0].update({"anim": "other_run", "priority": 60})
        out = anims.save_table(s, "guy_table", mine)
        self.assertEqual(out, {"file": "animtables/guy_table.changes.json", "changed": 1})
        written = json.loads((s.mod / "animtables" / "guy_table.changes.json").read_text(encoding="utf-8"))
        self.assertEqual(written, {"entries": [{"set": "default", "handle": "lb_run", "anim": "other_run",
                                                "priority": 60, "flags": "0x5"}]})
        back = anims.table(s, "guy_table")
        self.assertEqual(back["file"], "animtables/guy_table.changes.json")
        self.assertEqual(back["table"]["sets"][0]["entries"][0]["anim"], "other_run")
        self.assertTrue(anims.tables(s)[0]["changed"])
        # an entry taken out is written as a removal
        less = anims.table(s, "guy_table")["table"]
        del less["sets"][0]["entries"][1]
        anims.save_table(s, "guy_table", less)
        written = json.loads((s.mod / "animtables" / "guy_table.changes.json").read_text(encoding="utf-8"))
        self.assertIn({"set": "default", "handle": "fb_fidget", "remove": True}, written["entries"])
        # back to the game's: the file goes
        self.assertEqual(anims.save_table(s, "guy_table", doc["game"]), {"file": None, "changed": 0})
        self.assertFalse((s.mod / "animtables" / "guy_table.changes.json").exists())
        # a priority the game can't hold is refused
        bad = anims.table(s, "guy_table")["table"]
        bad["sets"][0]["entries"][0]["priority"] = 999
        with self.assertRaises(ValueError):
            anims.save_table(s, "guy_table", bad)
        with self.assertRaises(KeyError):
            anims.table(s, "nobody")

    def test_whole_table_file_is_kept_whole(self):
        s = self.scene
        doc = anims.table(s, "guy_table")["game"]
        (s.mod / "animtables").mkdir(parents=True)
        (s.mod / "animtables" / "guy_table.json").write_text(json.dumps(doc), encoding="utf-8")
        mine = anims.table(s, "guy_table")
        self.assertEqual(mine["file"], "animtables/guy_table.json (the whole table)")
        mine["table"]["sets"][0]["entries"][0]["priority"] = 70
        self.assertEqual(anims.save_table(s, "guy_table", mine["table"])["file"], "animtables/guy_table.json")
        saved = json.loads((s.mod / "animtables" / "guy_table.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["sets"][0]["entries"][0]["priority"], 70)
        self.assertNotIn("meaning", saved["sets"][0]["entries"][0])


class UpdateRateTests(unittest.TestCase):
    XBOX = ("mipbias -0.88\n\n[Humans]\nAnimUpdateLow\t-1\t30\t0\nAnimUpdateLow\t10\t2\t0\n"
            "AnimUpdateMedium\t-1\t30\t0\nAnimUpdateLow\t15\t4\t0\nAnimUpdateMedium\t60\t6\t0\n"
            "AnimUpdateHigh\t-1\t30\t0\nAnimUpdateLender\t-1\t2\t0\n\n[FrontEndCanvas]\nX\t1\n")

    def test_read_and_save(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "DATAxbox").mkdir()
            (d / "xbox.ini").write_text(self.XBOX, encoding="latin-1")
            s = SimpleNamespace(data=d / "DATAxbox", mod=d / "mod")
            got = anims.update_rates(s)
            self.assertEqual(got["game"]["AnimUpdateLow"], ["-1 30 0", "10 2 0", "15 4 0"])
            self.assertEqual(got["game"]["AnimUpdateMedium"], ["-1 30 0", "60 6 0"])
            self.assertEqual(got["mod"], {})
            # another setting in the file stays; a table equal to the game's isn't written
            (s.mod / "config").mkdir(parents=True)
            (s.mod / "config" / "xbox.ini.json").write_text(json.dumps({"": {"mipbias": "-0.5"}}), encoding="utf-8")
            out = anims.save_update_rates(s, {"AnimUpdateMedium": ["-1 30 0", "15  4 0", "60 6 0"],
                                              "AnimUpdateLow": ["-1 30 0", "10 2 0", "15 4 0"]})
            self.assertEqual(out, {"file": "config/xbox.ini.json", "changed": ["AnimUpdateMedium"]})
            doc = json.loads((s.mod / "config" / "xbox.ini.json").read_text(encoding="utf-8"))
            self.assertEqual(doc, {"": {"mipbias": "-0.5"}, "Humans": {"AnimUpdateMedium": ["-1 30 0", "15 4 0", "60 6 0"]}})
            self.assertEqual(anims.update_rates(s)["mod"], {"AnimUpdateMedium": ["-1 30 0", "15 4 0", "60 6 0"]})
            # a table not sent stays; one the build would refuse is refused
            anims.save_update_rates(s, {"AnimUpdateHigh": ["-1 30 0", "30 1 1"]})
            self.assertEqual(sorted(anims.update_rates(s)["mod"]), ["AnimUpdateHigh", "AnimUpdateMedium"])
            with self.assertRaises(ValueError):
                anims.save_update_rates(s, {"AnimUpdateHigh": ["50 1 0", "30 1 0"]})
            # all back to the game's: only the other setting is left
            anims.save_update_rates(s, {k: [] for k in ("AnimUpdateMedium", "AnimUpdateHigh")})
            self.assertEqual(json.loads((s.mod / "config" / "xbox.ini.json").read_text(encoding="utf-8")),
                             {"": {"mipbias": "-0.5"}})


if __name__ == "__main__":
    unittest.main()
