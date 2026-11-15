import unittest

from mercfoundry import encounters, missions

UTIL = """function util_Other()
return { allies = { sw = { 9, 9 } } }
end
function util_GetMissionArcTable()
return
{
allies =
{
sw = { 1, 3, 4, 5, 6, 8 },
nw = { 1, 3, 4, 2, 8 }
},
china =
{
sw = { 1, 2, 3, 4, 5, 6 },
nw = { 1, 2, 3, 4, 5, 6 }
}
}
end
"""

INI = """[MissionNamesList]

MissionName SW_China1
MissionName SW_Allies1
MissionName SW_Allies2

[SW_Allies1]
MissionTitleDbKey\t\t\tMissionInfo.MissionTitle.SWAllies1
ContractFee\t\t\t\t20000
Map\t\t\t\t\tSW
Number\t\t\t\t\t1


[SW_Allies2]
ContractFee\t\t\t\t40000
Number\t\t\t\t\t2


[SW_China1]
ContractFee\t\t\t\t10000
"""


class MissionTests(unittest.TestCase):
    def test_parse_id(self):
        self.assertEqual(missions.parse_id("SW_Allies9"), ("sw", "allies", 9))
        with self.assertRaises(ValueError):
            missions.parse_id("se_allies9")

    def test_arc(self):
        self.assertEqual(missions.arc(UTIL, "allies", "nw"), [1, 3, 4, 2, 8])
        # always at the end: retail positions never shift
        new = missions.add_to_arc(UTIL, "allies", "sw", 9)
        self.assertEqual(missions.arc(new, "allies", "sw"), [1, 3, 4, 5, 6, 8, 9])
        self.assertEqual(missions.arc(new, "allies", "nw"), [1, 3, 4, 2, 8])
        self.assertEqual(missions.arc(new, "china", "sw"), [1, 2, 3, 4, 5, 6])
        self.assertIn("sw = { 9, 9 }", new)                    # other functions untouched
        self.assertEqual(missions.add_to_arc(new, "allies", "sw", 9), new)
        self.assertEqual(missions.arc(missions.add_to_arc(UTIL, "china", "nw", 7), "china", "nw"), [1, 2, 3, 4, 5, 6, 7])

    def test_mission_ini(self):
        text, name = missions.add_to_mission_ini(INI, "sw_allies9", {"ContractFee": 50000, "BonusFee": 5})
        self.assertEqual(name, "SW_Allies9")
        lines = text.split("\n")
        self.assertEqual(lines[lines.index("MissionName SW_Allies2") + 1], "MissionName SW_Allies9")
        sec = text[text.index("[SW_Allies9]"):text.index("[SW_China1]")]
        self.assertIn("MissionTitleDbKey\t\t\tMissionInfo.MissionTitle.SWAllies1", sec)  # copied from mission 1
        self.assertIn("ContractFee\t\t\t50000", sec)
        self.assertIn("Number\t\t\t9", sec)
        self.assertIn("BonusFee\t\t\t5", sec)
        self.assertLess(text.index("[SW_Allies2]"), text.index("[SW_Allies9]"))
        with self.assertRaises(ValueError):
            missions.add_to_mission_ini(text, "sw_allies9")

    def test_text_plan(self):
        doc = {"title": "Field Test", "description": "Clear a squad.",
               "mail": {"subject": "Contract", "body": "Go."}, "strings": {"sw.allies9.obj.x": "Kill them"}}
        strings, ini, mail = missions.text_plan("sw_allies9", doc, "SW_Allies9")
        self.assertEqual(ini, {"MissionTitleDbKey": "MissionInfo.MissionTitle.SWAllies9",
                               "ShortDescriptionDbKey": "MissionInfo.Description.SWAllies9"})
        self.assertEqual(strings["MissionInfo.MissionTitle.SWAllies9"], "Field Test")
        self.assertEqual(strings["mail.sw_allies9_initial.body"], "Go.")
        self.assertEqual(strings["sw.allies9.obj.x"], "Kill them")
        self.assertEqual(mail, ("sw_allies9_initial", "mail.senders.techgal",
                                "mail.sw_allies9_initial.subject", "mail.sw_allies9_initial.body"))
        self.assertEqual(missions.text_plan("sw_allies9", {}, "SW_Allies9"), ({}, {}, None))

    def test_objectives_script(self):
        doc = {"objectives": [{"type": "goto", "text": "Reach the bridge", "zone": "bridge_zone"},
                              {"type": "destroy", "text": "Kill the squad", "squad": "Squad1"}]}
        strings, _, _ = missions.text_plan("sw_allies11", doc, "SW_Allies11")
        self.assertEqual(strings["sw.allies11.obj.foundry1"], "Reach the bridge")
        self.assertEqual(missions.text_plan("sw_allies11", doc, "SW_Allies11")[1]["BonusFee"], 0)   # no copied bonus
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: ["Nk1", "Nk2"])
        self.assertIn("{ kind = 'goto', name = 'foundry1', key = 'sw.allies11.obj.foundry1', zone = 'bridge_zone' }", lua)
        marked = missions.objectives_script("sw_allies11", doc, lambda squad: ["Nk1"], (), lambda z: [1, 2.5, -3])
        self.assertIn("zone = 'bridge_zone', x = 1.00, y = 2.50, z = -3.00 }", marked)
        self.assertIn("Objective_SetTargetVector(o.name, o.zone, o.text, sTargetType, 'location',", marked)
        self.assertIn("return 'PRI_OBJ_', 1, 'OBJECTIVE_TYPE_PRIMARY', 'TARGET_TYPE_OBJECTIVE_PRIMARY'", marked)
        unmarked = dict(doc, objectives=[dict(doc["objectives"][0], marker=False)])
        self.assertNotIn("x = 1.00", missions.objectives_script("sw_allies11", unmarked, lambda squad: [], (), lambda z: [1, 2.5, -3]))
        self.assertIn("targets = { 'Nk1', 'Nk2' }", lua)
        self.assertIn("Event_ActorInBoundary('foundry_Arrived', 'b', 'player0', o.zone, true)", lua)
        self.assertIn("mission_MissionComplete()", lua)
        self.assertNotIn("#", lua.split("\n", 1)[1])          # Lua 5.0: table.getn, no # operator
        started = missions.objectives_script("sw_allies11", {**doc, "start": {"position": [10, 20.5, -30], "heading": 90}},
                                             lambda squad: ["Nk1"])
        self.assertIn("Actor_SetPosition('player0', 10.00, 20.50, -30.00)", started)
        self.assertIn("Actor_SetYaw('player0', 90.0)", started)
        for bad in ([], [{"type": "fly"}], [{"type": "goto"}], [{"type": "destroy"}],
                    [{"type": "goto", "zone": "z", "bonus": 5000}]):        # only a bonus objective: nothing to complete
            with self.assertRaises(ValueError):
                missions.objectives_script("sw_allies11", {"objectives": bad}, lambda squad: [])

    def test_bonus_objectives(self):
        # A bonus objective runs from the start beside the main ones and pays its
        # reward on completion with the flat bonus; open ones fail at the end.
        doc = {"ini": {"BonusFee": 10000},
               "objectives": [{"type": "goto", "text": "Reach the bridge", "zone": "bridge_zone"},
                              {"type": "destroy", "text": "Kill the officers", "squad": "Officers", "bonus": 25000},
                              {"type": "goto", "text": "Scout the hill", "zone": "hill", "bonus": "5000"}]}
        self.assertEqual(missions.bonus_fee(doc), 40000)
        self.assertEqual(missions.text_plan("sw_allies11", doc, "SW_Allies11")[1]["BonusFee"], 40000)
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: ["Off1"])
        main = lua[lua.index("tFoundryObjectives = {"):lua.index("tFoundryBonus = {")]
        bonus = lua[lua.index("tFoundryBonus = {"):lua.index("tFoundryActive")]
        self.assertIn("name = 'foundry1'", main)
        self.assertNotIn("foundry2", main)
        self.assertIn("{ kind = 'destroy', name = 'foundry2', key = 'sw.allies11.obj.foundry2', bonus = 25000, "
                      "targets = { 'Off1' } }", bonus)
        self.assertIn("name = 'foundry3', key = 'sw.allies11.obj.foundry3', bonus = 5000, zone = 'hill'", bonus)
        self.assertIn("iFoundryFlatBonus = 10000", lua)
        self.assertIn("if not o.reveal then foundry_Start(o)", lua)
        self.assertIn("'BON_OBJ_', 2, 'OBJECTIVE_TYPE_SECONDARY', 'TARGET_TYPE_OBJECTIVE_SECONDARY'", lua)
        self.assertIn("sType = 'BON_OBJ_FAIL'", lua)
        self.assertIn("Utility_WriteMissionData( { fBonusFee = iFoundryFlatBonus + iFoundryEarned } )", lua)
        self.assertIn("if tFoundryActive[o.name] and not o.done then", lua)   # never shown: not failed
        self.assertNotIn("#", lua.split("\n", 1)[1])

    def test_escort_and_fail(self):
        doc = {"objectives": [{"type": "escort", "text": "Bring the pilots home", "squad": "Pilots", "zone": "lz"},
                              {"type": "goto", "text": "Leave", "zone": "out"}],
               "fail": [{"type": "dies", "squad": "Medic", "text": "The medic died."},
                        {"type": "timer", "seconds": 300, "until": "done:1"},
                        {"type": "leave", "zone": "valley"}]}
        members = {"Pilots": ["P1", "P2"], "Medic": ["Doc"]}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: members[squad], (), lambda z: [1, 2, 3])
        self.assertIn("{ kind = 'escort', name = 'foundry1', key = 'sw.allies11.obj.foundry1', targets = { 'P1', 'P2' }, "
                      "zone = 'lz', x = 1.00, y = 2.00, z = 3.00, lost = 'sw.allies11.obj.foundry1.lost' }", lua)
        self.assertIn("Ai_CommandDefault(a, 'Follow', 'player0', 'loPri')", lua)
        self.assertIn("{ kind = 'dies', reason = 'sw.allies11.fail.foundry1', targets = { 'Doc' } }", lua)
        self.assertIn("{ kind = 'timer', reason = 'sw.allies11.fail.foundry2', seconds = 300, untilObj = 'foundry1' }", lua)
        self.assertIn("{ kind = 'leave', reason = 'sw.allies11.fail.foundry3', zone = 'valley' }", lua)
        self.assertIn("Utility_LoadUtilityScript('hud')", lua)
        self.assertIn("HUD_AddTrayDisplay{ { DisplayClock = true", lua)
        self.assertIn("Event_ActorInBoundary('foundry_Left', 'b', 'player0', f.zone, false)", lua)
        self.assertIn("sReasonForFailure = sReason\nmission_MissionFailure()", lua)
        self.assertIn("foundry_EndFail(o.name)", lua)
        self.assertNotIn("#", lua.split("\n", 1)[1])
        strings, _, _ = missions.text_plan("sw_allies11", doc, "SW_Allies11")
        self.assertEqual(strings["sw.allies11.fail.foundry1"], "The medic died.")
        self.assertEqual(strings["sw.allies11.fail.foundry2"], "You ran out of time.")
        self.assertEqual(strings["sw.allies11.obj.foundry1.lost"], "Everyone you were escorting was killed.")
        for bad in ([{"type": "boom"}], [{"type": "timer"}], [{"type": "leave"}], [{"type": "dies"}],
                    [{"type": "timer", "seconds": 5, "until": "done:7"}]):
            with self.assertRaises(ValueError):
                missions.objectives_script("sw_allies11", dict(doc, fail=bad), lambda squad: members[squad])
        with self.assertRaises(ValueError):          # an escort needs somewhere to go
            missions.objectives_script("sw_allies11", {"objectives": [{"type": "escort", "squad": "Pilots"}]},
                                       lambda squad: members[squad])

    def test_radio_lines(self):
        doc = {"objectives": [{"type": "goto", "text": "A", "zone": "a"}, {"type": "goto", "text": "B", "zone": "b"}],
               "radio": [{"when": "start", "sound": "fio.S10START", "text": "Foundry here."},
                         {"when": "done:2", "sound": "fio.S10DONE"},
                         {"when": "zone:hill", "text": "Watch the ridge."}]}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: [])
        self.assertIn("{ when = 'start', sound = 'fio.S10START' }", lua)
        self.assertIn("{ when = 'done', after = 'foundry2', sound = 'fio.S10DONE' }", lua)
        self.assertIn("{ when = 'zone', zone = 'hill', text = 'sw.allies11.radio.foundry3' }", lua)
        self.assertIn("Utility_LoadUtilityScript('util_banter')", lua)
        self.assertIn("banter_Add{ r.sound, prePad = 3.5, callback = foundry_RadioDone }", lua)
        self.assertIn("foundry_Radio('start')", lua)
        self.assertIn("foundry_Radio('done', o.name)", lua)
        # the contract completes after the last voiced line
        self.assertIn("if iFoundryRadioPending > 0 then bFoundryFinishing = true return end", lua)
        self.assertNotIn("#", lua.split("\n", 1)[1])
        strings, _, _ = missions.text_plan("sw_allies11", doc, "SW_Allies11")
        self.assertEqual(strings["vo.fio.S10START"], "Foundry here.")
        self.assertEqual(strings["sw.allies11.radio.foundry3"], "Watch the ridge.")
        self.assertNotIn("vo.fio.S10DONE", strings)          # a retail line keeps its own subtitle
        for bad in ({"when": "done:5", "text": "x"}, {"when": "start"}, {"when": "start", "sound": "no-bank"}):
            with self.assertRaises(ValueError):
                missions.objectives_script("sw_allies11", dict(doc, radio=[bad]), lambda squad: [])

    def test_bonus_reveal(self):
        objs = [{"type": "goto", "text": "A", "zone": "a"},
                {"type": "goto", "text": "B", "zone": "b"},
                {"type": "goto", "text": "C", "zone": "c", "bonus": 1000, "reveal": "starts:2"},
                {"type": "goto", "text": "D", "zone": "d", "bonus": 1000, "reveal": "done:1"},
                {"type": "goto", "text": "E", "zone": "e", "bonus": 1000, "reveal": "zone:hill"},
                {"type": "goto", "text": "F", "zone": "f", "bonus": 1000}]
        lua = missions.objectives_script("sw_allies11", {"objectives": objs}, lambda squad: [])
        self.assertIn("name = 'foundry3', key = 'sw.allies11.obj.foundry3', bonus = 1000, reveal = 'starts', after = 'foundry2'", lua)
        self.assertIn("bonus = 1000, reveal = 'done', after = 'foundry1'", lua)
        self.assertIn("bonus = 1000, reveal = 'zone', rzone = 'hill'", lua)
        self.assertIn("name = 'foundry6', key = 'sw.allies11.obj.foundry6', bonus = 1000, zone = 'f'", lua)   # at the start
        self.assertIn("Event_ActorInBoundary('foundry_RevealZone', 'b', 'player0', o.rzone, true)", lua)
        self.assertIn("foundry_Reveal('starts', o.name)", lua)
        self.assertIn("foundry_Reveal('done', o.name)", lua)
        for bad in ("starts:3", "done:9", "starts:x", "zone:", "later"):
            with self.assertRaises(ValueError):
                missions.objectives_script("sw_allies11", {"objectives": objs[:2] + [dict(objs[2], reveal=bad)]},
                                           lambda squad: [])

    def test_add_mail(self):
        ini = "[Mail]\r\nId\t\tsw_allies3_initial\r\nSender\t\tx\r\n"
        out = missions.add_mail(ini, "sw_allies9_initial", "mail.senders.techgal", "s", "b")
        self.assertTrue(out.endswith("[Mail]\r\nId\t\tsw_allies9_initial\r\nSender\t\tmail.senders.techgal\r\n"
                                     "Subject\t\ts\r\nBody\t\tb\r\nFolderPath\r\nAttachment1\r\nAttachment2\r\n"
                                     "Attachment3\r\n"))
        with self.assertRaises(ValueError):
            missions.add_mail(out, "SW_ALLIES9_INITIAL", "a", "b", "c")

    def test_new_text_record(self):
        from mercfoundry import registry
        reg = registry.Registry(registry.new_record())
        reg.apply({"sw.allies9.obj.x": "Kill them", "mail.sw_allies9_initial.body": "Go."})
        self.assertEqual(registry.Registry(reg.to_bytes()).strings(),
                         {"sw.allies9.obj.x": "Kill them", "mail.sw_allies9_initial.body": "Go."})

    def test_patch_global(self):
        src = ("function LoadNoMissionState()\nlocal a = 1\nSpawnBouncers(TRUE)\nEnableBouncers(TRUE)\nend\n"
               "function Other()\nSpawnBouncers(TRUE)\nend\n")
        out = missions.patch_global(src)
        self.assertTrue(out.startswith("function LoadNoMissionState()\n"
                                       "Utility_LoadUtilityScript('foundry_contracts') foundry_ReturnFromContract()\n"
                                       "local a = 1\n"))
        self.assertIn("SpawnBouncers(TRUE)\nfoundry_SetupContracts()\nEnableBouncers(TRUE)", out)
        self.assertEqual(out.count("foundry_SetupContracts"), 1)          # only in LoadNoMissionState
        self.assertEqual(missions.patch_global(out), out)                 # once
        with self.assertRaises(ValueError):
            missions.patch_global("function Other() end\n")

    def test_map_layers(self):
        src = "function LoadNoMissionState()\nSpawnBouncers(TRUE)\nend\n"
        out = missions.patch_global_layers(src)
        self.assertIn("SpawnBouncers(TRUE)\nUtility_LoadUtilityScript('foundry_layers') foundry_LoadMapLayers()\nend", out)
        self.assertEqual(missions.patch_global_layers(out), out)
        both = missions.patch_global(out)          # with the contacts hook too
        self.assertEqual(both.count("foundry_LoadMapLayers"), 1)
        self.assertEqual(both.count("foundry_SetupContracts"), 1)
        lua = missions.layers_script([{"map": "sw", "layer": "my_sw", "check": "my_tank_1"},
                                      {"map": "nw", "layer": "paths_nw", "check": None}])
        self.assertIn("{ map = 'sw', layer = 'my_sw', check = 'my_tank_1' },", lua)
        self.assertIn("{ map = 'nw', layer = 'paths_nw', check = nil },", lua)
        self.assertIn("Utility_ReadLevelFile(l.layer)", lua)

    def test_contracts_script(self):
        contact, strings = missions.contract("sw_allies9", {"title": "Field Test", "contact": {"name": "Major X"}},
                                             "MissionInfo.MissionTitle.SWAllies9", "MissionInfo.Description.SWAllies9")
        self.assertEqual(contact["template"], "template_allies_bouncer")
        self.assertEqual(contact["location"], "loc_hq-allies-bouncer")
        self.assertEqual((contact["target_type"], contact["blip"]), ("TARGET_TYPE_HQ_ALLIES", "hqallies"))
        self.assertEqual(strings["actions.foundry.sw_allies9"], "Discuss contract: Field Test")
        self.assertEqual(strings["standardMission.foundry.sw_allies9.tgtCaption"], "Major X")
        lua = missions.contracts_script([contact])
        self.assertIn("function foundry_SetupContracts()", lua)
        self.assertIn("function foundry_Use_sw_allies9()", lua)
        self.assertIn("'foundry_Start_sw_allies9', 'foundry_SetupContracts')", lua)
        self.assertIn("local f = 'allies'", lua)
        self.assertIn("DebugSkipToMission(f, 9)", lua)
        self.assertIn("util_SetupSingleTargetActorObjective(", lua)
        self.assertIn("-Location_GetYaw(c.location) + c.yaw", lua)
        # the campaign position and counters are kept and put back
        # ...for every faction, and the chapter: the contract's appended arc
        # position makes SetupChapter switch chapter and reset other factions
        self.assertIn("local t = { 'chapter', 'SkipTo_Chapter' }", lua)
        self.assertIn("for i, g in { 'allies', 'china', 'mafia', 'sk' } do", lua)
        self.assertIn("table.insert(t, 'current_' .. g .. '_mission') table.insert(t, 'total_missions_completed' .. g)", lua)
        self.assertIn("Utility_WriteStringToScribbleMemory('foundry_active_contract', 'sw_allies9')", lua)
        self.assertIn("Utility_WriteNumberToScribbleMemory('foundry_done_' .. id, 1)", lua)
        # not while a retry is queued: the retried mission reads the arc position
        # again (mission_GenericInit), and an erased one stopped its script
        ret = lua[lua.index("function foundry_ReturnFromContract()"):]
        self.assertLess(ret.index("if Utility_ReadNumberFromScribbleMemory('SkipTo_MissionNumber') then return end"),
                        ret.index("for i, key in foundry_Kept(f) do"))
        self.assertIn("repeatable = FALSE", lua)
        # a giver placed in the editor: there, facing its heading
        placed, _ = missions.contract("sw_allies9", {"title": "Field Test", "contact": {
            "template": "template_civ_civilian01", "name": "Mr Lee", "position": [10, 2.5, -30], "heading": 90}},
            "MissionInfo.MissionTitle.SWAllies9", "MissionInfo.Description.SWAllies9")
        placed_lua = missions.contracts_script([placed])
        self.assertIn("px = 10.00, py = 2.50, pz = -30.00, heading = 90.0, side = 'civ' }", placed_lua)
        # its own marker (the HQ type moved the faction's HQ icon to it), and not hostile
        # gold like the HQ givers, but a plain blip (the faction's HQ blip moved its HQ icon)
        self.assertIn("targettype = 'TARGET_TYPE_HQ_ALLIES', blip = 'blip'", placed_lua)
        self.assertEqual(missions.marker_type({"position": [0, 0, 0], "marker": "secondary"}, "china"),
                         "TARGET_TYPE_OBJECTIVE_SECONDARY")
        self.assertIsNone(missions.marker_type({"position": [0, 0, 0], "marker": "none"}, "china"))
        with self.assertRaises(ValueError):
            missions.marker_type({"position": [0, 0, 0], "marker": "pink"}, "china")
        # its side, set again each time it wakes (a side set while dormant doesn't hold)
        self.assertIn("if c.side then foundry_GiverSide(name, c) end", placed_lua)
        self.assertIn("Event_ActorIsDormant('foundry_GiverAwake', 'b', name, FALSE)", placed_lua)
        self.assertIn("Ai_SetHostileRange(name, 0.0)", placed_lua)
        self.assertIn("targettype = 'TARGET_TYPE_HQ_ALLIES', blip = 'hqallies' }", lua)     # the HQ contact as before
        # offered only after the missions it requires
        chained, _ = missions.contract("sw_allies10", {"title": "Two", "requires": ["sw_allies9"]},
                                       "MissionInfo.MissionTitle.SWAllies10", "MissionInfo.Description.SWAllies10")
        chained_lua = missions.contracts_script([chained])
        self.assertIn("requires = { 'sw_allies9' } },", chained_lua)
        self.assertIn("or not foundry_Unlocked(c)) then", chained_lua)
        self.assertIn("template = 'template_civ_civilian01'", placed_lua)
        # a placed giver comes from its own layer: waited for (its actor can
        # appear a moment after the layer loads), never spawned by the script
        self.assertNotIn("Mission_Spawn(c.template, name, c.px", placed_lua)
        self.assertIn("bWaiting = true", placed_lua)
        self.assertIn("Event_RelativeTimer('foundry_SetupContracts', 'b', 1.0)", placed_lua)
        # its use event once at a time (re-armed when used)
        self.assertIn("if tFoundryArmed[name] ~= iActor then", placed_lua)
        self.assertIn("tFoundryArmed['foundry_contact_sw_allies9'] = nil", placed_lua)
        self.assertNotIn("px = ", lua)
        # a giver in its own layer exists already: it still gets its ring and
        # prompt, once each time its layer brings a new actor
        self.assertEqual(missions.contact_actor("sw_allies9"), "foundry_contact_sw_allies9")
        self.assertIn("if tFoundryMarked[name] ~= iActor then", placed_lua)
        self.assertLess(placed_lua.index("if Utility_DoesActorExist(name) then\n-- marked"),
                        placed_lua.index("util_SetupSingleTargetActorObjective("))
        self.assertIn("repeatable = TRUE", missions.contracts_script(
            [missions.contract("sw_allies9", {"repeatable": True}, "a", "b")[0]]))
        _, strings = missions.contract("nw_sk7", {}, "a", "b")
        self.assertEqual(strings["standardMission.foundry.nw_sk7.tgtCaption"], "South Korean contact")

    def test_contacts_at_one_hq_stand_apart(self):
        a = missions.contract("sw_allies9", {}, "a", "b")[0]
        b = missions.contract("sw_allies10", {}, "a", "b")[0]
        c = missions.contract("sw_allies11", {"contact": {"offset": [0, 0, 4]}}, "a", "b")[0]
        lua = missions.contracts_script([a, b, c])
        self.assertIn("id = 'sw_allies9', map = 'sw', faction = 'allies', number = 9, repeatable = FALSE, "
                      "template = 'template_allies_bouncer', location = 'loc_hq-allies-bouncer', dx = 3,", lua)
        self.assertIn("number = 10, repeatable = FALSE, template = 'template_allies_bouncer', "
                      "location = 'loc_hq-allies-bouncer', dx = 5.5,", lua)
        self.assertIn("dx = 0, dy = 0, dz = 4,", lua)

    def test_add_to_list(self):
        lst = "\n[model]\n\nload a\n\n[script]\n\nload global\nload util_misc\n\n[texture]\n\nload t\n"
        out = missions.add_to_list(lst, "script", "sw_allies9")
        self.assertEqual(encounters.parse_list(out)["script"], ["global", "util_misc", "sw_allies9"])
        self.assertEqual(missions.add_to_list(out, "script", "SW_ALLIES9"), out)
        self.assertEqual(encounters.parse_list(missions.add_to_list("\n[model]\n\nload a\n", "script", "x"))["script"], ["x"])


if __name__ == "__main__":
    unittest.main()
