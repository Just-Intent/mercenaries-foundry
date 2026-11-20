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


class TextAndMailTests(unittest.TestCase):
    def test_text_files_mail_and_search(self):
        with tempfile.TemporaryDirectory() as t:
            mod = Path(t)
            (mod / "maps").mkdir()
            (mod / "maps" / "isle.json").write_text("{}")
            (mod / "mod.json").write_text(json.dumps({"name": "Isle"}))
            content.save_text(mod, "text/names.json", None, {"faction.allies": "Blue"})
            content.save_text(mod, "text/isle.json", "isle", {"faction.china": "Red"})
            files = {f["file"]: f for f in content.text_files(mod)}
            self.assertEqual(files["text/names.json"]["strings"], {"faction.allies": "Blue"})
            self.assertEqual(files["text/isle.json"]["map"], "isle")
            with self.assertRaises(ValueError):
                content.save_text(mod, "text/x.json", "sw", {"a": "b"})          # not a new map
            with self.assertRaises(ValueError):
                content.save_text(mod, "../x.json", None, {})
            content.save_welcome_mail(mod, {"sender": "Broker", "subject": "Hi", "body": "Go.", "map": ""})
            self.assertEqual(content.welcome_mail(mod)["mail"], {"sender": "Broker", "subject": "Hi", "body": "Go."})
            with self.assertRaises(ValueError):
                content.save_welcome_mail(mod, {"subject": "no body"})
            content.save_welcome_mail(mod, None)
            self.assertIsNone(content.welcome_mail(mod)["mail"])
        hits = content.search_strings({"faction.allies": "Allied Nations", "x.y": "Pyongyang"}, "allied")
        self.assertEqual(hits, [["faction.allies", "Allied Nations"]])
        self.assertEqual(content.search_strings({"a": "b"}, "a"), [])                  # too short

class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = Path(self.tmp.name)
        (self.mod / "terrain").mkdir()
        # a straight east-west road through the middle, and a crossing one
        (self.mod / "terrain" / "isle.decals.json").write_text(json.dumps({"roads": [
            {"span": 16, "points": [[-300, 0], [300, 0]]}, {"span": 16, "points": [[0, -300], [0, 300]]}]}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_kits(self):
        content.save_kit(self.mod, "checkpoint", {"objects": [{"from": "template_x", "offset": [1, 0, 2], "yaw": 90}],
                                                  "half": [3, 4]})
        self.assertEqual(content.kits(self.mod)[0]["objects"][0]["yaw"], 90.0)
        with self.assertRaises(ValueError):
            content.save_kit(self.mod, "bad name!", {"objects": [{"from": "x", "offset": [0, 0, 0]}]})
        with self.assertRaises(ValueError):
            content.save_kit(self.mod, "empty", {"objects": []})

    def test_traffic_from_roads(self):
        flat = lambda x, z: 10.0                                                     # noqa: E731
        r = content.traffic_from_roads(self.mod, "isle", "cars", {"centre": [0, 0], "radius": 250}, [], flat,
                                       "isle_traffic", "isle-town-cars", spawn=["template_civ_80civic"])
        self.assertGreater(r["paths"], 4)
        doc = json.loads((self.mod / "encounters" / "isle_traffic.json").read_text())
        self.assertEqual(doc["traffic"]["isle-town-cars"]["spawn"], ["template_civ_80civic"])
        first = doc["paths"]["t_isle-town-cars_01"]
        self.assertEqual(first["points"][0][1], 10.3)                                # on the ground
        self.assertLessEqual(len(first["points"]), 9)
        self.assertTrue(all(abs(p[2]) > 0.5 or abs(p[0]) > 0.5 for p in first["points"]))   # off the road's middle
        # walkers next to it, a building blocking part of the east road's kerb
        w = content.traffic_from_roads(self.mod, "isle", "walkers", {"centre": [0, 0], "radius": 250},
                                       [[100, 6, 0, 20, 2]], flat, "isle_traffic", "isle-town-walkers")
        doc = json.loads((self.mod / "encounters" / "isle_traffic.json").read_text())
        self.assertEqual(set(doc["traffic"]), {"isle-town-cars", "isle-town-walkers"})
        walk = [p for k, v in doc["paths"].items() if k.startswith("p_") for p in v["points"]]
        self.assertFalse([p for p in walk if 78 <= p[0] <= 122 and 2 <= p[2] <= 10])    # not through the building
        # again: the zone's paths replaced, not added to
        again = content.traffic_from_roads(self.mod, "isle", "walkers", {"centre": [0, 0], "radius": 250},
                                           [], flat, "isle_traffic", "isle-town-walkers", max_paths=3)
        doc = json.loads((self.mod / "encounters" / "isle_traffic.json").read_text())
        self.assertEqual(sum(1 for k in doc["paths"] if k.startswith("p_")), again["paths"])
        counts = content.route_counts(self.mod)["isle"]
        self.assertEqual(counts["routes"], len(doc["paths"]))
        with self.assertRaises(ValueError):
            content.traffic_from_roads(self.mod, "nowhere", "cars", {"centre": [0, 0]}, [], flat, "l", "z")


class EffectsTests(unittest.TestCase):
    def test_effects_checked_as_the_build(self):
        table = {"littlebird_l": {"rotor": "vh_lbird.air_heli_engine", "num_rotor_blades": "5.0"}}
        with tempfile.TemporaryDirectory() as t:
            mod = Path(t)
            content.save_effects(mod, {"gaza_heli_l": {"copy": "littlebird_l", "rotor": "vh_lbird.gaza_heli_engine"}}, table)
            self.assertEqual(content.effects_doc(mod)["gaza_heli_l"]["copy"], "littlebird_l")
            for bad in ({"nothing_like_it": {"rotor": "x"}}, {"littlebird_l": {"copy": "littlebird_l"}},
                        {"x": {"copy": "missing"}}, {"littlebird_l": {"name": "y"}}, {"littlebird_l": {"rotor": [1]}}):
                with self.assertRaises(ValueError):
                    content.save_effects(mod, bad, table)
            content.save_effects(mod, {}, table)
            self.assertFalse((mod / "effects.json").exists())


if __name__ == "__main__":
    unittest.main()
