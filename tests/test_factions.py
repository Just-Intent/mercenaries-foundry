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

    def test_acronyms_and_people(self):
        e = factions.plan(STRINGS, {"sk": {"name": "Kingdom", "adjective": "Royal", "short": "KR", "people": "Royals"},
                                    "allies": {"name": "Coalition"}})
        self.assertEqual(e["MissionInfo.Faction.Sk"], "KR")
        self.assertEqual(e["interface.hud.disguise.lost.hostile.sk"], "[red]Hostile Act Witnessed by Royals [white][flagsk]")
        self.assertEqual(e["faction.allies"], "a Coalition")                                  # "an Allied": not AN
        self.assertEqual(e["nw.allieshq.target"], "Coalition HQ")

    def test_refused(self):
        for doc in ({"nk": {"name": "X"}}, {"china": {"adjective": "X"}}, {"china": "X"}):
            with self.assertRaises(ValueError):
                factions.plan(STRINGS, doc)


if __name__ == "__main__":
    unittest.main()
