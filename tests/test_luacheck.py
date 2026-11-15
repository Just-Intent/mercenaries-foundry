import unittest

from mercfoundry import luacheck, missions


class LuaCheckTests(unittest.TestCase):
    def test_valid_lua_50(self):
        src = """-- a comment
--[[ a long
comment ]]
local a, b = 1, 2
t = { 1, 2; x = 3, ['y'] = [[long
string [[nested]] ]], f = function(...) return arg end }
function M.f(self, x) return x end
function g(x, ...)
  for i = 1, 10, 2 do if i == 3 then break end end
  for k, v in pairs(t) do print(k, v) end
  while a < b do a = a + 1 end
  repeat a = a - 1 until a <= 0
  if not x then return elseif x == 1 then return 1 else return -x .. 's' end
end
local function h() return { } end
g{ 1 } g'str' t.f(1):upper()
x = 0x1F + 1e3 + .5 - 2 ^ 2
"""
        self.assertEqual(luacheck.check(src), [])

    def test_syntax_errors(self):
        for src, want in [
            ("x = #t", "the length operator # came with Lua 5.1"),
            ("x = a % b", "% came with Lua 5.1"),
            ("if x then y = 1", "'end' expected (to close 'if' at line 1)"),
            ("if x y = 1 end", "'then' expected"),
            ("x = 'open", "unfinished string"),
            ("f()\n(g)()", "ambiguous syntax"),
            ("x", "a statement is a call or an assignment"),
            ("f() = 1", "cannot assign to a function call"),
            ("t = { 1 2 }", "'}' expected"),
            ("x = [[ never closed", "unfinished long string"),
        ]:
            problems = luacheck.check(src)
            self.assertEqual(len(problems), 1, src)
            self.assertIn(want, problems[0], src)

    def test_names(self):
        src = """function foundry_A() end
local function private() end
helper = function() end
function t.method() end
Event_ActorInBoundary('foundry_A', 'b', 'player0', 'zone', true)
Event_RelativeTimer('foundry_Missing', 'b', 2)
Event_RelativeTimer(sDynamic, 'b', 2)
Undefined_Native(1)
private()
local function f(x) x() end
"""
        defined, called, callbacks = luacheck.names(src)
        self.assertEqual(sorted(defined), ["foundry_A", "helper"])
        self.assertIn(("Undefined_Native", 8), called)
        self.assertNotIn("private", [n for n, _ in called])         # a local
        self.assertNotIn("x", [n for n, _ in called])               # a parameter
        self.assertEqual(callbacks, [("foundry_A", 5), ("foundry_Missing", 6)])
        problems = luacheck.unknown(src, {"Event_ActorInBoundary", "Event_RelativeTimer"})
        self.assertEqual(problems, ["line 8: calls Undefined_Native, which nothing defines (not this script, not the game's)",
                                    "line 6: an event calls back foundry_Missing, which nothing defines"])
        self.assertEqual(luacheck.known_names([src]) >= {"foundry_A", "Undefined_Native", "foundry_Missing"}, True)

    def test_library_calls_must_be_the_games(self):
        # the game's Lua is cut down: a library function counts only if the
        # retail scripts call it (string.len stopped a mission's start)
        retail = luacheck.known_names(["n = table.getn(t) table.insert(t, 1) s = tostring(n)"])
        self.assertEqual(retail, {"table.getn", "table.insert", "tostring", "n", "s"})     # (and what it assigns)
        src = "local s = 'x'\nn = string.len(s)\nm = table.getn(t)\nok = pcall(f)\nlocal string = {}\nstring.len(s)\n"
        self.assertEqual(luacheck.unknown(src, retail), [
            "line 2: calls string.len, which the game's scripts never use (its Lua may not have it)",
            "line 4: calls pcall, which the game's scripts never use (its Lua may not have it)"])

    def test_generated_mission_script(self):
        # every feature at once: the script must be Lua 5.0 the game compiles,
        # and every function and callback it names its own (or the game's)
        doc = {
            "ini": {"BonusFee": 1000}, "start": {"position": [1, 2, 3], "heading": 90},
            "mood": {"allies": 20, "sk": -10},
            "support": [{"item": "template_support_gunship", "count": 1}, {"item": "template_support_crate_SK"}],
            "objectives": [
                {"type": "goto", "text": "Go 'there'", "zone": "z1"},
                {"type": "destroy", "text": "Kill", "squad": "e:S", "targets": ["tank1"], "together": True},
                {"type": "escort", "text": "Escort", "squad": "e:S", "zone": "z2"},
                {"type": "deliver", "text": "Deliver", "vehicle": "car1", "zone": "z2"},
                {"type": "subdue", "text": "Take", "targets": ["gen"], "extract": True},
                {"type": "subdue", "text": "Take2", "targets": ["gen2"]},
                {"type": "hold", "text": "Hold", "zone": "z3", "seconds": 60},
                {"type": "use", "text": "Use", "targets": ["box"], "prompt": "Plant"},
                {"type": "verify", "text": "Verify", "targets": ["ace"], "blip": "spades", "alive": 5000},
                {"type": "goto", "text": "Bonus", "zone": "z4", "bonus": 2000, "reveal": "done:1"},
                {"type": "destroy", "text": "Bonus2", "targets": ["x"], "bonus": 1000, "reveal": "zone:z5"},
            ],
            "fail": [
                {"type": "dies", "squad": "e:S"},
                {"type": "timer", "seconds": 300, "from": "starts:3", "until": "done:4"},
                {"type": "leave", "zone": "z1", "from": "zone:z1"},
                {"type": "alarm", "targets": ["alarm1"]},
                {"type": "spotted", "faction": "sk", "from": "done:2"},
                {"type": "hostile", "targets": ["guard"]},
            ],
            "radio": [{"when": "start", "text": "Hi"}, {"when": "done:1", "sound": "fio.S10START", "text": "Go"},
                      {"when": "zone:z2", "text": "Zone"}],
            "waves": [{"when": "starts:3", "squad": "e:W"}],
            "orders": [{"when": "start", "squad": "e:S", "do": "attack"},
                       {"when": "zone:z3", "squad": "e:W", "do": "path", "to": "p1"},
                       {"when": "done:2", "squad": "e:S", "do": "move", "to": "B"},
                       {"when": "start", "squad": "e:S", "do": "close"}, {"when": "start", "squad": "e:S", "do": "hold"}],
        }
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: ["m1", "m2"], ["layer_a"], lambda z: [1, 2, 3])
        self.assertEqual(luacheck.check(lua), [])
        defined, called, callbacks = luacheck.names(lua)
        own = [n for n, _ in called + callbacks if n.startswith("foundry_")]
        self.assertEqual([n for n in own if n not in defined], [])
        self.assertEqual(luacheck.check(missions.contracts_script([])), [])
        self.assertEqual(luacheck.check(missions.layers_script([{"map": "sw", "layer": "layer_a"}])), [])


if __name__ == "__main__":
    unittest.main()
