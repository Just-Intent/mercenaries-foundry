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

    def test_unlock_mail_as_retails_ace_mail(self):
        mail = {"sender": "mail.senders.techgal", "subject": "The Shadow", "body": "His file is open.",
                "vo": "fio.xfio085"}
        d = deck.parse("deck/clubs.json", dict(DOC, unlock_mail=mail), {"gaza"})
        self.assertEqual(d["unlock_mail"]["mark"], 80)                    # retail's iTargetIntel
        m = deck.unlock_mail(d)
        self.assertEqual(m[:4], ("foundry_deck_gaza_clubs_unlock", "mail.senders.techgal",
                                 "MissionInfo.FoundryMail.DeckGazaClubs.Subject", "MissionInfo.FoundryMail.DeckGazaClubs.Body"))
        self.assertEqual(m[4]["MissionInfo.FoundryMail.DeckGazaClubs.Body"], "His file is open.")
        own = deck.unlock_mail(deck.parse("deck/clubs.json", dict(DOC, unlock_mail=dict(mail, sender="Shin Bet")), {"gaza"}))
        self.assertEqual(own[1], "MissionInfo.FoundryMail.DeckGazaClubs.Sender")    # a name of the mod's own
        self.assertIsNone(deck.unlock_mail(deck.parse("deck/clubs.json", DOC, {"gaza"})))
        lua = deck.script([d], {("gaza", "clubs"): ("foundry_deck_gaza_clubs_unlock", 5)})
        self.assertEqual(luacheck.check(lua), [])
        self.assertIn("unlock = { clubs = { id = 'foundry_deck_gaza_clubs_unlock', posted = 7, mark = 80, vo = 'fio.xfio085' } }", lua)
        # posted from the meter (a number card or a face card's contract) and on coming to the map
        meter = lua[lua.index("function foundry_DeckMeter(suit)"):]
        self.assertIn("foundry_DeckUnlock(m, suit, intel, TRUE)", meter[:meter.index("\nfunction foundry_DeckUnlock(")])
        self.assertIn("if on == m then foundry_DeckMails(m) return end", lua)
        unlock = lua[lua.index("function foundry_DeckUnlock("):]
        self.assertIn("if not u or intel < u.mark then return end", unlock)
        self.assertIn("Mail_Post(u.id)", unlock)
        for bad in ({"subject": "a"}, {"subject": "a", "body": "b", "mark": 0}, {"subject": "a", "body": "b", "vo": "x"},
                    {"subject": "a", "body": "b", "when": 1}):
            with self.assertRaises(ValueError):
                deck.parse("deck/clubs.json", dict(DOC, unlock_mail=bad), {"gaza"})

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
        self.assertIn("{ suit = 'clubs', rank = 2, actor = 'gaza_card_clubs02', layer = 'gaza_card_clubs02', bounty = 40000 }", lua)
        self.assertIn("{ suit = 'clubs', rank = 3, actor = 'gaza_card_clubs03', layer = false, bounty = 12500 }", lua)
        self.assertNotIn("rank = 12", lua)
        # retail's verify flow and the PDA's keys
        self.assertIn("mission_ObjectiveVerify{", lua)
        self.assertIn("Desktop_BeginVerify('foundry_DeckPay', c.suit, c.rank, state, FALSE)", lua)
        self.assertIn("suit .. rank .. 'status'", lua)
        self.assertIn("'iCurrentIntel' .. suit", lua)
        # the meter as retail's: 2-10 their rank, J/Q/K 30; shown and the map's own
        meter = lua[lua.index("function foundry_DeckMeter(suit)"):]
        self.assertIn("if rank <= 10 then intel = intel + rank else intel = intel + 30 end", meter)
        self.assertIn("'foundry_deck_' .. m .. '_iCurrentIntel' .. suit, intel", meter)
        self.assertIn("foundry_DeckMeter(c.suit)", lua[lua.index("function foundry_DeckVerified"):])
        self.assertIn("iFoundryDeckPay = c.bounty * 2", lua)        # alive pays double

    def test_others_stand_in(self):
        doc = dict(DOC, others={"name": "Unknown", "bio": "Not on this front.", "face": "deck/other.png"})
        d = deck.parse("deck/clubs.json", doc, {"gaza"})
        s = deck.others_strings(d["others"], {"clubs"})
        self.assertEqual(s["cards.spades.14.charName"], "Unknown")
        self.assertNotIn("cards.spades.2.bio", deck.others_strings(d["others"], {"clubs", "spades"}))   # a deck of its own
        self.assertEqual(s["cards.diamonds.2.bio"], "Not on this front.")
        self.assertNotIn("cards.clubs.4.charName", s)         # the deck's own suit isn't a stand-in
        self.assertEqual(deck.other_face_texture("Gaza"), "gaza_deck_other_face")
        with self.assertRaises(ValueError):
            deck.parse("deck/clubs.json", dict(DOC, others={"colour": "red"}), {"gaza"})

    def test_medevac_and_hq_respawn_checked(self):
        for medevac, hq in (({"position": [1, 2]}, None), (None, {"cups": {"position": [1, 2, 3]}}),
                            (None, {"allies": {"heading": 3}}), (None, ["allies"])):
            with self.assertRaises(ValueError):
                modbuild._move_player_start(b"", medevac, "maps/gaza.json", hq)

    def test_two_suits_on_one_map(self):
        clubs = deck.parse("deck/clubs.json", DOC, {"gaza"})
        spades = deck.parse("deck/spades.json", dict(DOC, suit="spades"), {"gaza"})
        lua = deck.script([clubs, spades])
        self.assertEqual(luacheck.check(lua), [])
        self.assertIn("gaza = { suits = { 'clubs', 'spades' }", lua)
        self.assertIn("{ suit = 'spades', rank = 2, actor = 'gaza_card_clubs02'", lua)
        self.assertIn("Utility_WriteNumberToScribbleMemory('foundry_deck_on_' .. suit, 1)", lua)
        self.assertIn("ipairs({ 'clubs', 'diamonds', 'hearts', 'spades' })", lua)

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
