import tempfile
import unittest
from pathlib import Path

from mercfoundry import deck, luacheck, missions, modbuild


DOC = {"suit": "clubs", "faction": "nk", "note": "ignored",
       "cards": [{"card": "2", "name": "Iyad Kassab", "alias": "The Mole", "role": "Tunnel foreman",
                  "description": "Runs a digging crew.", "bounty": 40000, "actor": "gaza_card_clubs02",
                  "layer": "gaza_card_clubs02"},
                 {"card": "3", "name": "Mahmoud Sharaf", "bio": "Launch crew chief.", "actor": "gaza_card_clubs03"},
                 {"card": "Q", "name": "Ziad Abu Hamid", "description": "Commands Zeitoun.", "bounty": 300000}]}


class DeckTests(unittest.TestCase):
    def test_parse(self):
        d = deck.parse("deck/clubs.json", DOC, {"gaza"})
        self.assertEqual((d["map"], d["suit"]), ("gaza", "clubs"))       # the mod's one new map
        two, three, queen = d["cards"]
        self.assertEqual((two["rank"], two["bounty"], two["layer"]), (2, 40000, "gaza_card_clubs02"))
        self.assertEqual(two["bio"], "Known as The Mole. Tunnel foreman. Runs a digging crew.")
        self.assertEqual(three["bio"], "Launch crew chief.")
        self.assertEqual(three["bounty"], deck.DEFAULT_BOUNTY)
        self.assertEqual(queen["rank"], 12)
        for bad in ({"suit": "cups", "cards": []},
                    {"suit": "clubs", "cards": [{"card": "1", "name": "x"}]},
                    {"suit": "clubs", "cards": [{"card": "2"}]},
                    {"suit": "clubs", "cards": [{"card": "2", "name": "a"}, {"card": "2", "name": "b"}]},
                    {"suit": "clubs", "cards": [{"card": "2", "name": "a", "layer": "l"}]},
                    {"suit": "clubs", "map": "isle", "cards": [{"card": "2", "name": "a"}]}):
            with self.assertRaises(ValueError):
                deck.parse("deck/x.json", bad, {"gaza"})
        with self.assertRaises(ValueError):                              # two new maps: say which
            deck.parse("deck/x.json", {"suit": "clubs", "cards": [{"card": "2", "name": "a"}]}, {"gaza", "isle"})

    def test_face_found_by_name(self):
        with tempfile.TemporaryDirectory() as t:
            mod = Path(t)
            (mod / "deck").mkdir()
            (mod / "deck" / "clubs2.png").write_bytes(b"x")
            d = deck.parse("deck/clubs.json", DOC, {"gaza"}, mod)
            self.assertEqual(d["cards"][0]["face"], "deck/clubs2.png")
            self.assertEqual(d["cards"][1]["face"], "")
        self.assertEqual(deck.face_texture("Gaza", "clubs", 2), "gaza_clubs2_face")

    def test_strings_and_script(self):
        d = deck.parse("deck/clubs.json", DOC, {"gaza"})
        s = deck.strings(d)
        self.assertEqual(s["cards.clubs.2.charName"], "Iyad Kassab")
        self.assertEqual(s["cards.clubs.12.Name"], "Queen of Clubs")
        lua = deck.script([d])
        self.assertEqual(luacheck.check(lua), [])
        # number cards placed only; the face card comes through a contract
        self.assertIn("{ rank = 2, actor = 'gaza_card_clubs02', layer = 'gaza_card_clubs02', bounty = 40000 }", lua)
        self.assertIn("{ rank = 3, actor = 'gaza_card_clubs03', layer = false, bounty = 12500 }", lua)
        self.assertNotIn("rank = 12", lua)
        # retail's verify flow and the PDA's keys
        self.assertIn("mission_ObjectiveVerify{", lua)
        self.assertIn("Desktop_BeginVerify('foundry_DeckPay', d.suit, c.rank, state, FALSE)", lua)
        self.assertIn("suit .. rank .. 'status'", lua)
        self.assertIn("'iCurrentIntel' .. suit", lua)
        self.assertIn("iFoundryDeckPay = c.bounty * 2", lua)        # alive pays double

    def test_others_stand_in(self):
        doc = dict(DOC, others={"name": "Unknown", "bio": "Not on this front.", "face": "deck/other.png"})
        d = deck.parse("deck/clubs.json", doc, {"gaza"})
        s = deck.strings(d)
        self.assertEqual(s["cards.spades.14.charName"], "Unknown")
        self.assertEqual(s["cards.diamonds.2.bio"], "Not on this front.")
        self.assertNotIn("cards.clubs.4.charName", s)         # the deck's own suit: only its cards
        self.assertEqual(deck.other_face_texture("Gaza"), "gaza_deck_other_face")
        with self.assertRaises(ValueError):
            deck.parse("deck/clubs.json", dict(DOC, others={"colour": "red"}), {"gaza"})

    def test_map_and_free_roam_hooks(self):
        text = modbuild.map_script("gaza", {}, [], deck=True)
        self.assertIn("Utility_LoadUtilityScript('foundry_deck')\nfoundry_DeckEnter('gaza')\nfoundry_DeckArm('gaza')",
                      text)
        with_contacts = modbuild.map_script("gaza", {}, [], contacts=True, deck=True)
        free = with_contacts[with_contacts.index("function foundry_gaza_FreeRoam()"):]
        self.assertIn("foundry_DeckEnter('gaza')", free.split("\nfunction ")[0])
        layers = missions.layers_script([], deck=True)
        self.assertIn("foundry_DeckLeave()", layers)
        self.assertEqual(luacheck.check(layers), [])


if __name__ == "__main__":
    unittest.main()
