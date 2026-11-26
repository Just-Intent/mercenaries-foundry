"""Whole sequences through the generated scripts (luarun + gamesim): a pilot's
trip to a new map and back, a save there and its reload, again. The scripts
come from the same generators the build uses: SW's foundry_layers, the
foundry_deck script and the new map's master script.

These are the checks that would have caught 0.300-0.302's bugs before a
game run: SW and the new map sharing one set of moods (own_moods never
reached foundry_layers), the game's cards left in on a save's return (the
leave steps came after the resume's return), and the suits a map's deck
doesn't use keeping the game's states and meters."""

import unittest

from mercfoundry import deck as deckmod, missions, modbuild
from mercfoundry.gamesim import Game
from mercfoundry.hashing import name_hash
from mercfoundry.luarun import Interpreter, LuaError, parse


def _pilot(on, to, actor):
    return {"map": on, "actor": actor, "to": to, "prompt": "Fly", "title": "Pilot", "question": "Go?"}


def scripts(own_moods=True):
    doc = {"start": {"position": [10, 0, 10]}, "own_moods": {"allies": "neutral", "china": "neutral",
                                                             "mafia": "neutral", "sk": "neutral"} if own_moods else None}
    if not own_moods:
        doc.pop("own_moods")
    decks = [{"map": "isle", "suit": "clubs", "cards": [{"rank": 2, "name": "Two", "bio": "", "bounty": 0,
                                                         "actor": None, "layer": None, "face": ""}],
              "others": None, "unlock_mail": None, "after": None, "until": None}]
    layers = missions.layers_script([], [_pilot("sw", "isle", "pilot_sw")], arrive=False, deck=True,
                                    moods=own_moods)
    master = modbuild.map_script("isle", doc, [], [_pilot("isle", "sw", "pilot_isle")], contacts=True, deck=True)
    return {name_hash("foundry_layers"): layers, name_hash("foundry_deck"): deckmod.script(decks),
            name_hash("isle"): master, name_hash("foundry_contracts"): "function foundry_ReturnFromContract() end\n"
                                                                       "function foundry_SetupContracts() end\n"}


SW_MOODS = {"allies": 0.36, "china": 0.26, "mafia": -0.05, "sk": 0.51}


class LuaRunTests(unittest.TestCase):
    def test_lua_50_idioms(self):
        lua = Interpreter()
        out = []
        lua.globals["Out"] = lambda *a: out.append(a)
        lua.run("t = { 'a', 'b', x = 3 }\nlocal n = 0\nfor k, v in t do n = n + 1 end\n"
                "function f(...) return arg.n, arg[1] end\nlocal a, b = f(5, 6)\n"
                "Out(n, a, b, 1 .. '', table.getn(t), 2 ^ 3, nil == false)\nUnknownFn(1)\n")
        self.assertEqual(out, [(3.0, 2.0, 5.0, "1", 2.0, 8.0, False)])
        self.assertEqual(lua.stubbed, {"UnknownFn": 1})

    def test_errors_name_the_function(self):
        lua = Interpreter()
        lua.run("function Bad() return nil + 1 end", "thing")
        with self.assertRaisesRegex(LuaError, "thing: Bad: line 1: attempt to perform arithmetic on a nil"):
            lua.call("Bad")

    def test_a_loop_that_never_ends_stops(self):
        lua = Interpreter()
        lua.max_steps = 1000
        lua.run("function Spin() while true do end end")
        with self.assertRaisesRegex(LuaError, "too many steps"):
            lua.call("Spin")

    def test_parses_what_the_build_writes(self):
        for name, src in scripts().items():
            parse(src)


class GameSimTests(unittest.TestCase):
    def setUp(self):
        self.g = Game(scripts())
        for f, v in SW_MOODS.items():
            self.g.relations[(f, "prokat")] = v
        s = self.g.scribble
        s["clubs5status"], s["hearts3status"] = "killed", "captured"
        s["iCurrentIntelclubs"], s["iCurrentIntelhearts"] = "%f" % 5, "%f" % 3

    def where(self, level, moods, intel):
        g = self.g
        self.assertEqual(g.errors, [])
        self.assertEqual(g.level, level)
        self.assertEqual({k: round(v, 2) for k, v in g.moods().items()}, moods)
        self.assertEqual(g.intel(), intel)
        self.assertFalse(g.black, "the screen is still black")
        # the PDA is built from the cards as the level loads: they must be this map's by then
        self.assertEqual(g.pda_stale(), [])

    def test_trips_saves_and_reloads_keep_each_maps_own(self):
        g = self.g
        sw_intel = {"clubs": 5.0, "diamonds": 0.0, "hearts": 3.0, "spades": 0.0}
        isle0 = {k: 0.0 for k in SW_MOODS}
        g.load_level("sw"); g.advance(5)
        self.where("sw", SW_MOODS, sw_intel)
        g.money = 1500.0
        g.travel("isle"); g.advance(5)
        # its own moods, and every suit empty there (not SW's hearts)
        self.where("isle", isle0, {s: 0.0 for s in sw_intel})
        # 0.364: one wallet: the money came along (a new map restores PlayerMoney)
        self.assertEqual(g.money, 1500.0)
        g.money = 2750.0
        g.relations[("china", "prokat")] = -0.8
        g.scribble["clubs2status"] = "killed"
        g.scribble["iCurrentIntelclubs"] = "%f" % 2
        isle1 = dict(isle0, china=-0.8)
        isle_intel = {"clubs": 2.0, "diamonds": 0.0, "hearts": 0.0, "spades": 0.0}
        saved = g.save()
        g.travel("sw"); g.advance(5)
        self.where("sw", SW_MOODS, sw_intel)
        self.assertEqual(g.card("clubs", 2), None)
        # the save made there: it wakes on SW, goes back, and has its own
        self.assertEqual(g.money, 2750.0)
        g.money = 99.0
        g.load_save(saved); g.advance(10)
        self.where("isle", isle1, isle_intel)
        self.assertEqual(g.money, 2750.0)   # the save's money, not a new player's $0
        self.assertEqual(g.card("clubs", 5), None)
        g.load_save(g.save()); g.advance(10)
        self.where("isle", isle1, isle_intel)
        g.travel("sw"); g.advance(5)
        self.where("sw", SW_MOODS, sw_intel)
        self.assertEqual(g.card("clubs", 5), "killed")

    def test_without_own_moods_the_maps_share_the_games(self):
        g = Game(scripts(own_moods=False))
        for f, v in SW_MOODS.items():
            g.relations[(f, "prokat")] = v
        g.load_level("sw")
        g.travel("isle"); g.advance(5)
        self.assertEqual({k: round(v, 2) for k, v in g.moods().items()}, SW_MOODS)


if __name__ == "__main__":
    unittest.main()


class StorePopupTests(unittest.TestCase):
    """A map's store puts its items back after each level load; only the
    first unlock may show the game's "new support item" popup (0.305: each
    settlers contract's start showed the last ones again)."""

    def scripts(self):
        doc = {"start": {"position": [10, 0, 10]},
               "shop": {"items": [{"id": "crate", "from": "template_support_crate_mafiaC4", "name": "A crate",
                                   "short": "A crate.", "long": "A crate.", "price": 1000, "unlock": "start"}]}}
        out = scripts()
        out[name_hash("isle")] = modbuild.map_script("isle", doc, [], [_pilot("isle", "sw", "pilot_isle")],
                                                     contacts=True, deck=True)
        return out

    def test_one_popup_per_item(self):
        g = Game(self.scripts())
        g.load_level("sw")
        g.travel("isle"); g.advance(10)
        self.assertEqual(g.errors, [])
        self.assertEqual(len(g.popups), 1, g.popups)
        item = g.popups[0]
        self.assertIn(item, g.shop)
        g.load_level("isle"); g.advance(10)          # a contract's start loads the map again
        self.assertIn(item, g.shop)
        g.load_save(g.save()); g.advance(10)          # and a save made there, loaded
        self.assertEqual(g.level, "isle")
        self.assertIn(item, g.shop)
        self.assertEqual(len(g.popups), 1, g.popups)
