import unittest

from mercfoundry import factions

STRINGS = {
    "interface.datapod.china": "CHINA",
    "interface.datapod.standardcase.china": "China",
    "interface.hud.disguise.china": "[flagchina] DISGUISED as Chinese [flagchina]",
    "interface.hud.disguise.lost.hostile.sk": "[red]Hostile Act Witnessed by South Koreans [white][flagsk]",
    "faction.allies": "an Allied",
    "MissionInfo.Faction.Sk": "SK",
    "nw.allieshq.target": "Allies HQ",
    "cards.hearts.10.bio": "Hong is a Chinese-born Korean.",
    "vo.fio.x1": "The Chinese want it done.",
    "tutorial.enteringHQ.body": "To enter a faction HQ, speak to the doorman.",
}


class FactionTests(unittest.TestCase):
    def test_interface_only(self):
        e = factions.plan(STRINGS, {"china": {"name": "Empire of Jade", "adjective": "Imperial"}})
        self.assertEqual(e, {"interface.datapod.china": "EMPIRE OF JADE",
                             "interface.datapod.standardcase.china": "Empire of Jade",
                             "interface.hud.disguise.china": "[flagchina] DISGUISED as Imperial [flagchina]"})

    def test_everywhere_and_articles(self):
        e = factions.plan(STRINGS, {"china": {"name": "Jade", "adjective": "Imperial"}, "everywhere": True})
        self.assertEqual(e["cards.hearts.10.bio"], "Hong is an Imperial-born Korean.")       # a -> an
        self.assertNotIn("vo.fio.x1", e)                                                      # subtitles: off
        self.assertNotIn("tutorial.enteringHQ.body", e)
        e = factions.plan(STRINGS, {"china": {"name": "Jade", "adjective": "Imperial"}, "everywhere": True, "subtitles": True})
        self.assertEqual(e["vo.fio.x1"], "The Imperial want it done.")

    def test_map_key_is_allowed(self):
        # "map": the renames go on that map only (the build's concern)
        e = factions.plan(STRINGS, {"china": {"name": "Jade", "adjective": "Imperial"}, "map": "isle"})
        self.assertEqual(e["interface.datapod.china"], "JADE")

    def test_acronyms_and_people(self):
        e = factions.plan(STRINGS, {"sk": {"name": "Kingdom", "adjective": "Royal", "short": "KR", "people": "Royals"},
                                    "allies": {"name": "Coalition"}})
        self.assertEqual(e["MissionInfo.Faction.Sk"], "KR")
        self.assertEqual(e["interface.hud.disguise.lost.hostile.sk"], "[red]Hostile Act Witnessed by Royals [white][flagsk]")
        self.assertEqual(e["faction.allies"], "a Coalition")                                  # "an Allied": not AN
        self.assertEqual(e["nw.allieshq.target"], "Coalition HQ")

    def test_north_korea(self):
        # the enemy too: DPRK as the name, KPA and the Korean People's Army
        # as "army" (default: the adjective); South Korea untouched
        strings = {"a": "North Korea and the DPRK", "b": "a KPA Soldier of the Korean People's Army",
                   "c": "North Koreans fired on South Korean troops", "d": "Destroy NK forces", "e": "an NK tank"}
        e = factions.plan(strings, {"nk": {"name": "Hamas", "people": "Hamas fighters", "army": "Qassam"},
                                    "everywhere": True})
        self.assertEqual(e["a"], "Hamas and the Hamas")
        self.assertEqual(e["b"], "a Qassam Soldier of the Qassam")
        self.assertEqual(e["c"], "Hamas fighters fired on South Korean troops")
        self.assertEqual(e["d"], "Destroy Hamas forces")
        self.assertEqual(e["e"], "a Hamas tank")

    def test_refused(self):
        for doc in ({"nkorea": {"name": "X"}}, {"china": {"adjective": "X"}}, {"china": "X"}):
            with self.assertRaises(ValueError):
                factions.plan(STRINGS, doc)


if __name__ == "__main__":
    unittest.main()


class LookTests(unittest.TestCase):
    def test_looks(self):
        doc = {"china": {"looks": {"china_hum_soldier": "sk_hum_soldier", "china_hum_spy": "china_hum_spy"}},
               "sk": {"name": "Hindis"}}
        self.assertEqual(factions.looks(doc), [("china", "china_hum_soldier", "sk_hum_soldier")])
        # looks aren't names: the text plan ignores them
        self.assertEqual(factions.plan({"x": "China"}, {"china": {"looks": {"a": "b"}}}), {})
        with self.assertRaises(ValueError):
            factions.looks({"mafia": {"looks": {"mafia_hum_soldier": " "}}})

    def test_saving_keeps_looks(self):
        import json
        import tempfile
        from pathlib import Path
        from mercfoundry.editor.scene import Scene
        with tempfile.TemporaryDirectory() as d:
            sc = Scene.__new__(Scene)
            sc.mod = Path(d)
            sc._english = lambda: {}
            sc.save_factions({"china": {"looks": {"china_hum_soldier": "sk_hum_soldier"}}, "everywhere": True})
            doc = json.loads((Path(d) / "factions.json").read_text(encoding="utf-8"))
            self.assertEqual(doc, {"china": {"looks": {"china_hum_soldier": "sk_hum_soldier"}}})   # nothing renamed
            self.assertEqual(Scene.model_role("sk_hum_heavysoldier"), "heavy")
            self.assertEqual(Scene.model_role("allies_hum_mgsoldier"), "soldier")
            self.assertEqual(Scene.model_role("nk_hum_elitesoldier"), "elite")


class VehicleJointTests(unittest.TestCase):
    def test_working_joints(self):
        jeep = ["jeepmgun", "wheel_fl", "wheel_fr", "hp_seat_lf", "hp_seat_rf", "Turret_a", "segment_damage_B",
                "hp_headlightL"]
        humvee = ["humvee", "wheel_fl", "wheel_fr", "hp_seat_lf", "hp_seat_rf", "hp_seat_lr", "turret_a",
                  "door_lf"]
        self.assertEqual(factions.working_joints(jeep), {"wheel_fl", "wheel_fr", "hp_seat_lf", "hp_seat_rf", "turret_a"})
        self.assertEqual(factions.missing_joints(jeep, humvee), [])            # a Humvee can stand in for the jeep
        self.assertEqual(factions.missing_joints(humvee, jeep), ["hp_seat_lr"])  # not the other way round
