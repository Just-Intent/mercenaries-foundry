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
        # the game's mission table holds 64 (it hangs at boot past that)
        self.assertEqual(missions.mission_count(text), missions.mission_count(INI) + 1)
        self.assertEqual(missions.mission_count("MissionName\tA\n  missionname B\n; MissionName C\nMissionNames D"), 2)
        self.assertEqual(missions.MAX_MISSIONS, 64)

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
        self.assertIn("Actor_SetYaw('player0', 270.0)   -- heading 90", started)   # the player turns the other way (missions.player_yaw)
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
        self.assertIn("{ kind = 'dies', reason = 'sw.allies11.fail.foundry1', cb = 'foundry_Failed1', targets = { 'Doc' } }", lua)
        self.assertIn("{ kind = 'timer', reason = 'sw.allies11.fail.foundry2', cb = 'foundry_Failed2', seconds = 300, untilObj = 'foundry1' }", lua)
        self.assertIn("{ kind = 'leave', reason = 'sw.allies11.fail.foundry3', cb = 'foundry_Failed3', zone = 'valley' }", lua)
        self.assertIn("Utility_LoadUtilityScript('hud')", lua)
        self.assertIn("HUD_AddTrayDisplay{ { DisplayClock = true", lua)
        self.assertIn("function foundry_TimeUp(hDisplay, eState)\nif eState ~= HUD.STATE.LIMIT then return end", lua)
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

    def test_deliver_and_destroy_objects(self):
        doc = {"objectives": [{"type": "destroy", "text": "Blow up the depot", "targets": ["fuel_depot", "radar_copy1"]},
                              {"type": "deliver", "text": "Bring the truck", "vehicle": "foundry_truck", "zone": "dropoff"}]}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: [], (), lambda z: [1, 2, 3])
        self.assertIn("targets = { 'fuel_depot', 'radar_copy1' } }", lua)          # no squad needed
        self.assertIn("{ kind = 'deliver', name = 'foundry2', key = 'sw.allies11.obj.foundry2', vehicle = 'foundry_truck', "
                      "zone = 'dropoff', x = 1.00, y = 2.00, z = 3.00, lost = 'sw.allies11.obj.foundry2.lost' }", lua)
        self.assertIn("Objective_SetTargetActor(o.name, o.vehicle, o.text, sTargetType, 'blip', true, true, true)", lua)
        self.assertIn("Event_ActorInBoundary('foundry_Delivered', 'b', o.vehicle, o.zone, true)", lua)
        self.assertIn("elseif w.kind == 'deliver' then if not w.done then foundry_Fail(w.lost) end", lua)
        self.assertNotIn("#", lua.split("\n", 1)[1])
        strings, _, _ = missions.text_plan("sw_allies11", doc, "SW_Allies11")
        self.assertEqual(strings["sw.allies11.obj.foundry2.lost"], "The vehicle was destroyed.")
        for bad in ({"type": "deliver", "zone": "z"}, {"type": "deliver", "vehicle": "v"}):
            with self.assertRaises(ValueError):
                missions.objectives_script("sw_allies11", {"objectives": [bad]}, lambda squad: [])

    def test_subdue_and_hold(self):
        doc = {"objectives": [{"type": "subdue", "text": "Take the general alive", "targets": ["nk_general"]},
                              {"type": "hold", "text": "Hold the bridge", "zone": "bridge", "seconds": 90},
                              {"type": "subdue", "text": "And his aide", "targets": ["aide"], "bonus": 5000}]}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: [], (), lambda z: [1, 2, 3])
        self.assertIn("targets = { 'nk_general' }, lost = 'sw.allies11.obj.foundry1.lost' }", lua)
        self.assertIn("mission_ObjectiveSubdue{ callbackFunc = fDown, killCallbackFunc = foundry_TargetKilled,", lua)
        self.assertIn("{ kind = 'hold', name = 'foundry2', key = 'sw.allies11.obj.foundry2', zone = 'bridge', "
                      "x = 1.00, y = 2.00, z = 3.00, seconds = 90 }", lua)
        self.assertIn("Event_ActorInBoundary('foundry_HoldOut', 'b', 'player0', o.zone, false)", lua)
        self.assertIn("fCallback = foundry_HoldDone", lua)
        # the HUD clock calls back at half time, a quarter and the end: only the end counts
        self.assertIn("function foundry_HoldDone(hDisplay, eState)\nif eState ~= HUD.STATE.LIMIT then return end", lua)
        # a killed bonus target loses the bonus; a main one fails the mission
        self.assertIn("if o.bonus then\no.done = true\nutil_ObjMsgPrint{ sMsg = o.text, sType = 'BON_OBJ_FAIL'", lua)
        self.assertNotIn("#", lua.split("\n", 1)[1])
        strings, _, _ = missions.text_plan("sw_allies11", doc, "SW_Allies11")
        self.assertEqual(strings["sw.allies11.obj.foundry1.lost"], "The target was killed.")
        for bad in ({"type": "hold", "zone": "z"}, {"type": "hold", "seconds": 5}, {"type": "subdue"}):
            with self.assertRaises(ValueError):
                missions.objectives_script("sw_allies11", {"objectives": [bad]}, lambda squad: [])

    def test_defend_collect_and_escapes(self):
        from mercfoundry import luacheck
        doc = {"objectives": [{"type": "defend", "text": "Keep them alive", "squad": "Doctors", "seconds": 120, "need": 1},
                              {"type": "collect", "text": "Grab the cases", "targets": ["case1", "case2", "case3"], "need": 2},
                              {"type": "destroy", "text": "Stop him", "targets": ["runner"]}],
               "fail": [{"type": "escapes", "targets": ["runner"], "zone": "border"}]}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: ["Doc1", "Doc2"], (), lambda z: [1, 2, 3])
        self.assertEqual(luacheck.check(lua), [])
        self.assertIn("{ kind = 'defend', name = 'foundry1', key = 'sw.allies11.obj.foundry1', targets = { 'Doc1', 'Doc2' }, "
                      "seconds = 120, keep = 1, lost = 'sw.allies11.obj.foundry1.lost' }", lua)
        self.assertIn("targets = { 'case1', 'case2', 'case3' }, need = 2 }", lua)
        self.assertIn("targets = { 'runner' }, zone = 'border'", lua)
        self.assertIn("for j, a in ipairs(f.targets) do Event_ActorInBoundary(f.cb, 'b', a, f.zone, true) end", lua)
        self.assertIn("elseif w.kind == 'defend' then foundry_DefendLost(w)", lua)
        self.assertIn("function foundry_DefendDone(hDisplay, eState)\nif eState ~= HUD.STATE.LIMIT or bFoundryOver then return end", lua)
        # several at once: each clock finds its own objective
        self.assertIn("if o.kind == 'defend' and not o.done and o.hud == hDisplay then", lua)
        self.assertNotIn("tFoundryDefending", lua)
        self.assertIn("not Utility_DoesActorExist(a)", lua)
        self.assertNotIn("#", lua.split("\n", 1)[1])
        strings, _, _ = missions.text_plan("sw_allies11", doc, "SW_Allies11")
        self.assertEqual(strings["sw.allies11.obj.foundry1.lost"], "What you had to defend was destroyed.")
        for bad in ([{"type": "defend", "targets": ["a"]}],                     # no clock
                    [{"type": "defend", "seconds": 9}],                        # nothing to defend
                    [{"type": "collect"}],
                    [{"type": "collect", "targets": ["a"], "need": 2}]):
            with self.assertRaises(ValueError):
                missions.objectives_script("sw_allies11", {"objectives": bad}, lambda squad: [])
        for bad in ({"type": "escapes", "zone": "z"}, {"type": "escapes", "targets": ["a"]}):
            with self.assertRaises(ValueError):
                missions.objectives_script("sw_allies11", {"objectives": [{"type": "goto", "zone": "z"}], "fail": [bad]},
                                           lambda squad: [], (), lambda z: [1, 2, 3])

    def test_kill_and_near(self):
        from mercfoundry import luacheck
        members = {"camp:Runner": ["Run1"], "camp:Guards": ["G1", "G2"]}
        doc = {"objectives": [{"type": "kill", "text": "Kill 10 soldiers", "kinds": ["NK_Hum_Soldier", "nk_hum_heavy"],
                               "need": 10},
                              {"type": "destroy", "text": "Catch him", "squad": "camp:Runner"}],
               "orders": [{"when": "near:25:camp:Runner", "squad": "camp:Runner", "do": "path", "to": "escape"}],
               "radio": [{"when": "near:25:camp:Runner", "text": "He's running!"}],
               "fail": [{"type": "escapes", "squad": "camp:Runner", "zone": "out", "from": "near:25:camp:Runner"}]}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: members[squad], (), lambda z: [1, 2, 3])
        self.assertEqual(luacheck.check(lua), [])
        self.assertIn("kinds = { 'nk_hum_soldier', 'nk_hum_heavy' }, need = 10 }", lua)
        self.assertIn("Event_OdfHitPointsLessThan('foundry_KindDown', 'b', k, 1, true)", lua)
        # one watch for the moment, shared by the order, the radio line and the fail condition
        self.assertIn("{ key = '25:camp:Runner', metres = 25, cb = 'foundry_Near1', members = { 'Run1' } }", lua)
        self.assertEqual(lua.count("foundry_Near2"), 0)
        self.assertIn("function foundry_Near1() foundry_NearHit(1) end", lua)
        self.assertIn("Event_ActorToActorProximity(n.cb, 'b', a, 'player0', n.metres, true)", lua)
        self.assertIn("when = 'near', after = '25:camp:Runner', act = 'path'", lua)
        self.assertIn("from = 'near', after = '25:camp:Runner'", lua)
        self.assertNotIn("#", lua.split("\n", 1)[1])
        for bad in ({"type": "kill", "kinds": ["x"]}, {"type": "kill", "need": 3}):
            with self.assertRaises(ValueError):
                missions.objectives_script("sw_allies11", {"objectives": [bad]}, lambda squad: [])
        for bad in ("near:camp:Runner", "near:0:camp:Runner", "near:5:Runner"):
            with self.assertRaises(ValueError):
                missions.objectives_script("sw_allies11", {"objectives": [{"type": "goto", "zone": "z"}],
                                                           "radio": [{"when": bad, "text": "x"}]},
                                           lambda squad: members.get(squad, []))

    def test_check_mission(self):
        # The editor's Check: the build's checks on a saved mission.
        import json, tempfile
        from pathlib import Path
        from mercfoundry.modbuild import check_mission
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "missions").mkdir()
            (root / "encounters").mkdir()
            (root / "encounters" / "sw_allies11.json").write_text(json.dumps({
                "map": "SW", "boundaries": {"bridge": {"points": [[0, 0, 0], [10, 0, 0], [10, 0, 10]]}},
                "encounters": [{"name": "camp", "points": {"A": [[0, 0, 0]]},
                                "squads": [{"name": "Guards", "points": ["A"], "members": [{"name": "G1"}]}]}]}))
            mission = root / "missions" / "sw_allies11.json"
            mission.write_text(json.dumps({"objectives": [{"type": "goto", "text": "Go", "zone": "bridge"},
                                                          {"type": "destroy", "text": "Kill", "squad": "camp:Guards"}]}))
            self.assertEqual(check_mission(root, "sw_allies11")["problems"], [])
            mission.write_text(json.dumps({"objectives": [{"type": "goto", "text": "Go", "zone": "nowhere"},
                                                          {"type": "destroy", "text": "Kill"}]}))
            self.assertEqual(check_mission(root, "sw_allies11")["problems"], ["not finished: objective 2"])
            mission.write_text(json.dumps({"objectives": [{"type": "goto", "text": "Go", "zone": "nowhere"}]}))
            problems = check_mission(root, "sw_allies11")["problems"]
            self.assertTrue(any("zone 'nowhere'" in p for p in problems), problems)
            self.assertIn("save it first", check_mission(root, "sw_allies12")["problems"][0])

    def test_use_object(self):
        doc = {"objectives": [{"type": "use", "text": "Plant the charge", "prompt": "Plant charge", "targets": ["fuel_tank"]}]}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: [])
        self.assertIn("targets = { 'fuel_tank' }, lost = 'sw.allies11.obj.foundry1.lost', act = 'sw.allies11.act.foundry1', tough = true }", lua)
        self.assertIn("if o.tough then for j, a in ipairs(o.targets) do if Utility_DoesActorExist(a) then Actor_SetInvincible(a, true)", lua)
        doc2 = {"objectives": [dict(doc["objectives"][0], breakable=True)]}
        self.assertNotIn("tough = true", missions.objectives_script("sw_allies11", doc2, lambda squad: []))
        self.assertIn("mission_ObjectiveAction{ callbackFunc = foundry_TargetDown, killCallbackFunc = foundry_TargetKilled,", lua)
        self.assertIn("actionCaption = o.act,", lua)
        strings, _, _ = missions.text_plan("sw_allies11", doc, "SW_Allies11")
        self.assertEqual(strings["sw.allies11.act.foundry1"], "Plant charge")
        self.assertEqual(strings["sw.allies11.obj.foundry1.lost"], "What you had to use was destroyed.")
        with self.assertRaises(ValueError):
            missions.objectives_script("sw_allies11", {"objectives": [{"type": "use", "prompt": "x"}]}, lambda squad: [])

    def test_verify_a_deck_card(self):
        doc = {"objectives": [{"type": "verify", "text": "Verify the Jack", "targets": ["gaza_jack"], "blip": "clubs",
                               "card": "J", "suit": "clubs", "bounty": 200000}]}
        lua = missions.objectives_script("sw_mafia21", doc, lambda squad: [])
        self.assertIn("suit = 'clubs', rank = 11, cardpay = 200000", lua)
        self.assertIn("if o.rank then foundry_CardVerified(o, bKilled) end", lua)
        self.assertIn("Desktop_BeginVerify('foundry_CardPay', o.suit, o.rank, state, FALSE)", lua)
        self.assertIn("Utility_WriteStringToScribbleMemory('foundry_deck_' .. m .. '_' .. key, state)", lua)
        # the suit's intel meter fills from face cards too, as retail's
        self.assertIn("if foundry_DeckMeter then foundry_DeckMeter(o.suit) end", lua)
        from mercfoundry import luacheck
        self.assertEqual(luacheck.check(lua), [])
        for bad in ({"card": "Z", "suit": "clubs"}, {"card": "J"}, {"card": "J", "suit": "clubs", "bounty": "lots"}):
            with self.assertRaises(ValueError):
                missions.objectives_script("sw_mafia21", {"objectives": [dict(doc["objectives"][0], **bad)
                                                                       if "suit" in bad else
                                                                       {k: v for k, v in dict(doc["objectives"][0], **bad).items() if k != "suit"}]},
                                           lambda squad: [])

    def test_contract_requires_cards(self):
        doc = {"title": "The Shadow", "map": "gaza", "contact": {"interior": "allies"},
               "requires_cards": {"suit": "clubs", "cards": ["J", "Q", "K"]}}
        c, _ = missions.contract("sw_allies22", doc, "t", "d")
        self.assertEqual(c["requires_cards"], ["clubs11", "clubs12", "clubs13"])
        lua = missions.contracts_script([c])
        self.assertIn("cards = { 'clubs11', 'clubs12', 'clubs13' }", lua)
        self.assertIn("s = Utility_ReadStringFromScribbleMemory('foundry_deck_' .. c.map .. '_' .. k .. 'status')", lua)
        from mercfoundry import luacheck
        self.assertEqual(luacheck.check(lua), [])
        with self.assertRaises(ValueError):
            missions.contract("sw_allies22", dict(doc, requires_cards={"suit": "clubs", "cards": ["X"]}), "t", "d")

    def test_card_man_appears_with_his_objective(self):
        from mercfoundry import luacheck
        doc = {"objectives": [{"type": "goto", "text": "Go", "zone": "z1"},
                              {"type": "verify", "text": "Verify the Jack", "targets": ["gaza_card_clubs11"],
                               "card": "J", "suit": "clubs"}],
               "waves": [{"when": "starts:2", "objects": ["gaza_card_clubs11"]},
                         {"when": "starts:2", "squad": "enc:Runner"}],
               "fail": [{"type": "escapes", "squad": "enc:Runner", "zone": "away"}]}
        lua = missions.objectives_script("sw_mafia21", doc, lambda squad: ["runner1"])
        self.assertEqual(luacheck.check(lua), [])
        self.assertIn("tFoundryWaveOf = { ['gaza_card_clubs11'] = 'sw_mafia21_w1', ['runner1'] = 'sw_mafia21_w2' }", lua)
        # the verify objective and the escape fail wait for their wave, then arm
        start = lua[lua.index("function foundry_Start(o)"):]
        self.assertLess(start.index("local sWave = o.targets and foundry_WaitsFor(o.targets)"),
                        start.index("foundry_Waves('starts', o.name)"))
        self.assertIn("if sWave then f.waitFor = sWave", lua)
        up = lua[lua.index("function foundry_WavesUp()"):]
        self.assertIn("foundry_Arm(o)", up[:up.index("\nfunction ")])
        self.assertIn("f.waitFor = nil foundry_ArmFail(f)", up[:up.index("\nfunction ")])
        # the object moves out of the mission's layer into the wave's
        layer = {"map": "gaza", "objects": [{"name": "gaza_card_clubs11", "from": "deck_clubs03"},
                                            {"name": "crate", "from": "x"}],
                 "encounters": [{"name": "enc", "squads": [{"name": "Runner", "members": []}]}]}
        rest, waves = missions.split_waves("sw_mafia21", doc, layer)
        self.assertEqual([o["name"] for o in rest["objects"]], ["crate"])
        self.assertEqual(waves[0], ("sw_mafia21_w1", {"map": "gaza", "objects": [layer["objects"][0]]}))
        with self.assertRaises(ValueError):
            missions.split_waves("sw_mafia21", {"waves": [{"when": "start", "objects": ["nobody"]}]}, layer)

    def test_contract_requires_intel_as_retail_aces(self):
        from mercfoundry import luacheck
        doc = {"title": "The Shadow", "map": "gaza", "contact": {"interior": "allies"}, "requires_intel": "clubs"}
        c, _ = missions.contract("sw_allies22", doc, "t", "d")
        self.assertEqual(c["requires_intel"], ("clubs", 80))              # retail's iTargetIntel
        lua = missions.contracts_script([c])
        self.assertEqual(luacheck.check(lua), [])
        self.assertIn("intel = { suit = 'clubs', mark = 80 }", lua)
        self.assertIn("if c.intel and foundry_Intel(c.intel.suit, c.map) < c.intel.mark then return false end", lua)
        # counted as retail's gameflow_GetIntelTotal, from the map's own deck when it isn't shown
        meter = lua[lua.index("function foundry_Intel(suit, m)"):]
        self.assertIn("if rank <= 10 then total = total + rank elseif rank < 14 then total = total + 30 end", meter)
        self.assertIn("pre = 'foundry_deck_' .. m .. '_'", meter)
        c, _ = missions.contract("sw_allies24", dict(doc, requires_intel={"suit": "spades", "mark": 60}), "t", "d")
        self.assertEqual(c["requires_intel"], ("spades", 60))
        # a contract on a retail map puts the game's cards back first (no free roam on the way)
        sw, _ = missions.contract("sw_allies22", {"title": "t", "contact": {"position": [1, 2, 3]}}, "t", "d")
        start = missions.contracts_script([sw])
        start = start[start.index("function foundry_Start_sw_allies22()"):]
        self.assertLess(start.index("foundry_DeckLeave()"), start.index("DebugSkipToMission(f, 22)"))
        for bad in ("cups", {"suit": "clubs", "mark": 0}, {"mark": 80}):
            with self.assertRaises(ValueError):
                missions.contract("sw_allies22", dict(doc, requires_intel=bad), "t", "d")

    def test_verify_target(self):
        doc = {"ini": {"BonusFee": 1000},
               "objectives": [{"type": "verify", "text": "Verify the general", "squad": "Brass", "targets": ["nk_general"],
                               "blip": "spades", "alive": 20000}]}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: ["aide"])
        self.assertIn("targets = { 'nk_general', 'aide' }, blip = 'spades', alive = 20000 }", lua)
        self.assertIn("mission_ObjectiveVerify{ callbackFunc = foundry_Verified, objectiveName = o.name .. 'v' .. j,", lua)
        self.assertIn("Utility_LoadUtilityScript('mission_objectiveverify')", lua)
        self.assertIn("bFoundryPays = true", lua)
        self.assertIn("iFoundryFlatBonus = 1000", lua)
        self.assertEqual(missions.bonus_fee(doc), 21000)          # the most it can pay
        plain = {"objectives": [{"type": "verify", "text": "V", "targets": ["x"]}]}
        lua = missions.objectives_script("sw_allies11", plain, lambda squad: [])
        self.assertIn("blip = 'human' }", lua)
        self.assertIn("bFoundryPays = false", lua)
        for bad in ({"type": "verify", "text": "V"}, {"type": "verify", "targets": ["x"], "blip": "moon"}):
            with self.assertRaises(ValueError):
                missions.objectives_script("sw_allies11", {"objectives": [bad]}, lambda squad: [])

    def test_support(self):
        doc = {"objectives": [{"type": "goto", "text": "A", "zone": "a"}],
               "support": [{"item": "template_support_gunship", "count": 1}, {"item": "template_support_crate_SK"}]}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: [])
        self.assertIn("tSupport = {              -- support items for the mission", lua)
        self.assertIn("  { 'template_support_gunship', 1 },\n  'template_support_crate_SK'\n}", lua)
        lua = missions.objectives_script("sw_allies11", {"objectives": doc["objectives"]}, lambda squad: [])
        self.assertIn("tSupport = {              -- support items for the mission (retail's: mission.lua adds them, and resets after)\n\n}", lua)
        with self.assertRaises(ValueError):
            missions.objectives_script("sw_allies11", {**doc, "support": [{"item": "gunship"}]}, lambda squad: [])

    def test_mood(self):
        doc = {"objectives": [{"type": "goto", "text": "A", "zone": "a"}], "mood": {"allies": 30, "sk": -10, "mafia": 0}}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: [])
        self.assertIn("iMissionMoodReward = 30   -- the contract's faction", lua)
        self.assertIn("tFoundryMood = { sk = -10 }", lua)
        self.assertIn("for sFaction, n in pairs(tFoundryMood) do Faction_ModifyRelation(sFaction, HERO_FACTION, n) end\n"
                      "mission_MissionComplete()", lua)
        lua = missions.objectives_script("sw_allies11", {"objectives": doc["objectives"]}, lambda squad: [])
        self.assertNotIn("iMissionMoodReward", lua)
        self.assertIn("tFoundryMood = {  }", lua)
        for bad in ({"nk": 10}, {"sk": 200}, {"sk": "lots"}):
            with self.assertRaises(ValueError):
                missions.objectives_script("sw_allies11", {**doc, "mood": bad}, lambda squad: [])

    def test_alarm_spotted_hostile_extract(self):
        doc = {"objectives": [{"type": "subdue", "text": "Take him", "targets": ["general"], "extract": True}],
               "fail": [{"type": "alarm", "targets": ["alarm1", "alarm2"]},
                        {"type": "spotted", "faction": "sk", "until": "done:1"},
                        {"type": "hostile", "squad": "Guards", "text": "The guards saw through you."}]}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: ["g1"])
        self.assertIn("lost = 'sw.allies11.obj.foundry1.lost', extract = true }", lua)
        self.assertIn("elseif o.kind == 'subdue' and o.extract then\n-- capture alive and fly them out", lua)
        self.assertIn("mission_ObjectiveExtract{ callbackFunc = foundry_TargetDown,", lua)
        self.assertIn("{ kind = 'alarm', reason = 'sw.allies11.fail.foundry1', cb = 'foundry_Failed1', targets = { 'alarm1', 'alarm2' } }", lua)
        self.assertIn("cb = 'foundry_Failed2', faction = 'sk', untilObj = 'foundry1' }", lua)
        self.assertIn("cb = 'foundry_Failed3', targets = { 'g1' } }", lua)
        self.assertIn("function foundry_Failed3() foundry_FailIf(3) end\n", lua)
        self.assertIn("Event_AlarmUsed(f.cb, 'b', a, true)", lua)
        self.assertIn("Event_ActorSpottedTrespassing(f.cb, 'b', 'player0', f.faction, true)", lua)
        self.assertIn("Event_ActorIsHostileToward(f.cb, 'b', a, 'player0', true)", lua)
        strings, _, _ = missions.text_plan("sw_allies11", doc, "SW_Allies11")
        self.assertEqual(strings["sw.allies11.fail.foundry1"], "The alarm was raised.")
        self.assertEqual(strings["sw.allies11.fail.foundry3"], "The guards saw through you.")
        for bad in ({"type": "alarm"}, {"type": "spotted", "faction": "nk"}, {"type": "hostile"}):
            with self.assertRaises(ValueError):
                missions.objectives_script("sw_allies11", {"objectives": doc["objectives"], "fail": [bad]}, lambda squad: [])

    def test_subdue_deliver(self):
        doc = {"objectives": [{"type": "subdue", "text": "Take him", "targets": ["officer"], "extract": True,
                               "deliver": "home", "bonus": 30000},
                              {"type": "subdue", "text": "Bring her", "targets": ["clerk"], "deliver": "home"}]}
        lua = missions.objectives_script("sw_sk20", doc, lambda squad: [])
        self.assertIn("extract = true, dzone = 'home' }", lua)
        self.assertIn("if o.dzone then fDown = foundry_CaptiveTaken end", lua)
        self.assertIn("and (Actor_IsSubdued(a) or Ai_IsPassenger(a)) and foundry_InZone(a, o.dzone) then", lua)
        self.assertIn("if o.got[sActor] then return end", lua)
        self.assertIn("if bFoundryOver then return end\nfoundry_DeliverSweep()", lua)
        with self.assertRaises(ValueError):
            missions.objectives_script("sw_sk20", {"objectives": [
                {"type": "subdue", "text": "x", "targets": ["a"], "deliver": ["home"]}]}, lambda squad: [])

    def test_fail_from(self):
        doc = {"objectives": [{"type": "goto", "text": "A", "zone": "a"}, {"type": "goto", "text": "B", "zone": "b"}],
               "fail": [{"type": "timer", "seconds": 120, "from": "starts:2"},
                        {"type": "leave", "zone": "base", "from": "zone:base"},
                        {"type": "spotted", "faction": "sk", "from": "start"}]}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: [])
        self.assertIn("seconds = 120, from = 'starts', after = 'foundry2' }", lua)
        self.assertIn("zone = 'base', from = 'zone', fzone = 'base' }", lua)
        self.assertIn("cb = 'foundry_Failed3', faction = 'sk' }", lua)          # "start" is the default
        self.assertIn("if not f.from then foundry_ArmFail(f)", lua)
        self.assertIn("foundry_Waves('starts', o.name)\nfoundry_FailsFrom('starts', o.name)", lua)
        self.assertIn("foundry_Waves('done', o.name)\nfoundry_FailsFrom('done', o.name)", lua)
        self.assertIn("if f.armed or f.off or bFoundryOver then return end", lua)
        with self.assertRaises(ValueError):
            missions.objectives_script("sw_allies11", {**doc, "fail": [{"type": "timer", "seconds": 5, "from": "starts:9"}]},
                                       lambda squad: [])

    def test_together(self):
        doc = {"objectives": [{"type": "goto", "text": "A", "zone": "a", "together": True},     # the first: nothing before
                              {"type": "goto", "text": "B", "zone": "b", "together": True},
                              {"type": "goto", "text": "Bonus", "zone": "c", "bonus": 1000, "together": True},
                              {"type": "goto", "text": "D", "zone": "d"}]}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: [])
        self.assertIn("zone = 'a' }", lua)
        self.assertIn("zone = 'b', together = true }", lua)
        self.assertIn("zone = 'c' }", lua)                                  # a bonus objective isn't in a group
        self.assertIn("while tFoundryObjectives[iFoundryStep + 1] and tFoundryObjectives[iFoundryStep + 1].together do", lua)
        self.assertIn("iFoundryGroupLeft = iFoundryGroupLeft - 1\nif iFoundryGroupLeft <= 0 then foundry_NextObjective() end", lua)

    def test_mission_log(self):
        doc = {"objectives": [{"type": "goto", "text": "A", "zone": "a"}]}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: [])
        self.assertIn("bFoundryDebug = false", lua)
        self.assertIn("Utility_WriteStringToScribbleMemory('foundry_log', 'sw_allies11: ' .. table.concat(tFoundryLog, ' | '))", lua)
        self.assertIn("if table.getn(tFoundryLog) > 8 then table.remove(tFoundryLog, 1) end", lua)
        # only Lua the game's scripts use: string.len stopped sw_china9's start (its Lua has no string library)
        self.assertNotIn("string.", lua.replace("tostring", ""))
        self.assertIn("if bFoundryDebug then util_ObjMsgPrint{ sMsg = '[sw_allies11] ' .. line, sType = 'GENERAL' } end", lua)
        for step in ("foundry_Note('started')", "foundry_Note('FAILED: ' .. tostring(sReason))",
                     "foundry_Note(o.name .. ' done')", "foundry_Note('fail condition on: ' .. f.kind)"):
            self.assertIn(step, lua)
        self.assertIn("bFoundryDebug = true", missions.objectives_script("sw_allies11", {**doc, "debug": True}, lambda squad: []))
        self.assertIn("function foundry_Log()", missions.contracts_script([]))   # readable after the mission's script has gone

    def test_need(self):
        doc = {"objectives": [{"type": "destroy", "text": "Kill ten", "squad": "camp:Guards", "need": 2},
                              {"type": "escort", "text": "Bring them", "squad": "camp:Guards", "zone": "lz", "need": "2"},
                              {"type": "destroy", "text": "All", "squad": "camp:Guards", "need": 3}]}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: ["g1", "g2", "g3"])
        self.assertIn("targets = { 'g1', 'g2', 'g3' }, need = 2 }", lua)
        self.assertIn("targets = { 'g1', 'g2', 'g3' }, need = 2, zone = 'lz'", lua)
        self.assertIn("key = 'sw.allies11.obj.foundry3', targets = { 'g1', 'g2', 'g3' } }", lua)    # 3 of 3: all
        self.assertIn("local iAll = o.need or table.getn(o.targets)", lua)
        self.assertIn("if table.getn(o.targets) - o.dead < (o.need or 1) then foundry_Fail(o.lost)", lua)
        for bad in (4, "x", -1):
            with self.assertRaises(ValueError):
                missions.objectives_script("sw_allies11", {"objectives": [{"type": "destroy", "text": "K", "squad": "s", "need": bad}]},
                                           lambda squad: ["g1", "g2", "g3"])

    def test_finish_fast(self):
        doc = {"objectives": [{"type": "goto", "text": "A", "zone": "a"},
                              {"type": "fast", "text": "Quick", "seconds": 300, "bonus": 20000}]}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: [])
        self.assertIn("{ kind = 'fast', name = 'foundry2', key = 'sw.allies11.obj.foundry2', bonus = 20000, seconds = 300 }", lua)
        self.assertIn("fCallback = foundry_TooSlow", lua)
        self.assertIn("function foundry_TooSlow(hDisplay, eState)\nif eState ~= HUD.STATE.LIMIT then return end", lua)
        finish = lua[lua.index("function foundry_Finish()"):]
        self.assertLess(finish.index("foundry_Done(o)"), finish.index("bFoundryOver = true"))   # paid before the payout
        # however it ends (cancelled too), mission.lua's cleanup takes every
        # marker and use prompt away (they outlive the script; a contract on a
        # new map doesn't reload the level after)
        cleanup = lua[lua.index("function pre_MissionCleanup()"):]
        for s in ("Objective_Remove(o.name)", "Objective_Remove(o.name .. 'd' .. j)",
                  "Actor_EnableScriptedUse(a, FALSE)", "{ tFoundryObjectives, tFoundryBonus }"):
            self.assertIn(s, cleanup)
        self.assertEqual(missions.bonus_fee(doc), 20000)
        for bad in ([{"type": "fast", "seconds": 60}],                                          # not a bonus
                    [{"type": "fast", "bonus": 5}],                                            # no time
                    [{"type": "fast", "seconds": 5, "bonus": 5}, {"type": "fast", "seconds": 9, "bonus": 5}]):
            with self.assertRaises(ValueError):
                missions.objectives_script("sw_allies11", {"objectives": doc["objectives"][:1] + bad}, lambda squad: [])

    def test_on_completion(self):
        doc = {"title": "Job", "objectives": [{"type": "goto", "text": "A", "zone": "a"}],
               "radio": [{"when": "end", "sound": "fio.S10DONE"}],
               "unlocks": ["template_support_deliverH2"], "done_mail": {"subject": "Thanks", "body": "Good work."}}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: [])
        self.assertIn("{ when = 'end', sound = 'fio.S10DONE' }", lua)
        self.assertIn("if not bFoundryEndSaid then bFoundryEndSaid = true foundry_Radio('end') foundry_Musics('end') "
                      "foundry_Weathers('end') end", lua)
        self.assertIn("tFoundryUnlocks = { 'template_support_deliverH2' }", lua)
        self.assertIn("sFoundryDoneMail = 'sw_allies11_done'", lua)
        self.assertIn("for i, s in ipairs(tFoundryUnlocks) do Shop_UnlockItem(s) end", lua)
        self.assertIn("if sFoundryDoneMail then Mail_Post(sFoundryDoneMail) end", lua)
        strings, _, _ = missions.text_plan("sw_allies11", doc, "SW_Allies11")
        self.assertEqual(strings["MissionInfo.FoundryMail.SWAllies11.Subject"], "Thanks")    # always loaded
        self.assertEqual(missions.done_mail("sw_allies11", doc, "SWAllies11"),
                         ("sw_allies11_done", "mail.senders.techgal", "MissionInfo.FoundryMail.SWAllies11.Subject",
                          "MissionInfo.FoundryMail.SWAllies11.Body"))
        plain = missions.objectives_script("sw_allies11", {"objectives": doc["objectives"]}, lambda squad: [])
        self.assertIn("tFoundryUnlocks = {  }", plain)
        self.assertIn("sFoundryDoneMail = nil", plain)
        with self.assertRaises(ValueError):
            missions.objectives_script("sw_allies11", {**doc, "unlocks": ["gunship"]}, lambda squad: [])

    def test_test_from(self):
        # testing: start at a later main objective (here objective 4, the third main one)
        doc = {"objectives": [{"type": "goto", "text": "A", "zone": "a"},
                              {"type": "goto", "text": "Bonus", "zone": "b", "bonus": 100},
                              {"type": "goto", "text": "C", "zone": "c"},
                              {"type": "goto", "text": "D", "zone": "d"}], "test_from": 4}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: [])
        self.assertIn("iFoundryTestFrom = 3      -- testing", lua)
        self.assertIn("if iFoundryTestFrom then           -- testing: the main objectives before it are skipped\n"
                      "iFoundryStep = iFoundryTestFrom - 1", lua)
        self.assertIn("iFoundryTestFrom = nil", missions.objectives_script("sw_allies11", {**doc, "test_from": 0}, lambda squad: []))
        for bad in (2, 9, "x"):                       # a bonus objective, none, not a number
            with self.assertRaises(ValueError):
                missions.objectives_script("sw_allies11", {**doc, "test_from": bad}, lambda squad: [])

    def test_keep_if_hostile(self):
        doc = {"objectives": [{"type": "goto", "text": "A", "zone": "a"}]}
        self.assertIn("bFoundryKeepHostile = false", missions.objectives_script("sw_allies11", doc, lambda squad: []))
        lua = missions.objectives_script("sw_allies11", {**doc, "keep_if_hostile": True}, lambda squad: [])
        self.assertIn("bFoundryKeepHostile = true", lua)
        # mission.lua calls custom_FactionAttitudeChange instead of cancelling, when it's defined
        self.assertIn("if bFoundryKeepHostile then\nfunction custom_FactionAttitudeChange()", lua)

    def test_squad_wiped_out(self):
        layer = {"map": "SW", "encounters": [{"name": "camp", "points": {"A": [[0, 0, 0]]},
                                               "squads": [{"name": "Guards", "points": ["A"], "members": [{"name": "G1"}]},
                                                          {"name": "Ambush", "points": ["A"], "members": [{"name": "X1"}]}]}]}
        members = {"camp:Guards": ["g1", "g2"], "camp:Ambush": ["x1"]}
        doc = {"objectives": [{"type": "goto", "text": "A", "zone": "a"},
                              {"type": "goto", "text": "Bonus", "zone": "b", "bonus": 100, "reveal": "dead:camp:Guards"}],
               "waves": [{"when": "dead:camp:Guards", "squad": "camp:Ambush"}],
               "radio": [{"when": "dead:camp:Ambush", "text": "Clear."}],
               "orders": [{"when": "dead:camp:Guards", "squad": "camp:Ambush", "do": "attack"}],
               "fail": [{"type": "timer", "seconds": 60, "from": "dead:camp:Guards"}]}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: members[squad])
        self.assertIn("reveal = 'dead', after = 'camp:Guards'", lua)
        self.assertIn("{ when = 'dead', after = 'camp:Guards', layer = 'sw_allies11_w1' }", lua)
        self.assertIn("from = 'dead', after = 'camp:Guards'", lua)
        self.assertIn("{ squad = 'camp:Guards', members = { 'g1', 'g2' } }", lua)
        self.assertIn("{ squad = 'camp:Ambush', members = { 'x1' }, wave = 'sw_allies11_w1' }", lua)   # watched once it's there
        self.assertIn("foundry_WatchSquads(w.layer)", lua)
        self.assertIn("foundry_Note('started')\nfoundry_WatchSquads(nil)", lua)
        rest, _ = missions.split_waves("sw_allies11", doc, layer)
        self.assertEqual(missions.when_of("dead:camp:Guards", 2, "x"), ("dead", "camp:Guards"))
        with self.assertRaises(ValueError):
            missions.when_of("dead:", 2, "x")

    def test_delays(self):
        doc = {"objectives": [{"type": "goto", "text": "A", "zone": "a"}],
               "radio": [{"when": "done:1", "text": "Later", "delay": 10}, {"when": "end", "text": "Bye", "delay": 5}],
               "waves": [{"when": "starts:1", "squad": "camp:Ambush", "delay": "30"}],
               "orders": [{"when": "starts:1", "squad": "camp:Ambush", "do": "attack", "delay": 32}]}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: ["x1"])
        self.assertIn("text = 'sw.allies11.radio.foundry1', delay = 10, cb = 'foundry_LaterR1' }", lua)
        self.assertIn("{ when = 'end', text = 'sw.allies11.radio.foundry2' }", lua)              # no delay at the end
        self.assertIn("layer = 'sw_allies11_w1', delay = 30, cb = 'foundry_LaterW1' }", lua)
        self.assertIn("delay = 32, cb = 'foundry_LaterO1' }", lua)
        self.assertIn("function foundry_LaterR1() local x = tFoundryRadio[1] x.due = true foundry_Say(x) end", lua)
        self.assertIn("function foundry_LaterW1() local x = tFoundryWaves[1] x.due = true foundry_Wave(x) end", lua)
        self.assertIn("if not r.waiting then r.waiting = true Event_RelativeTimer(r.cb, 'b', r.delay) end", lua)
        with self.assertRaises(ValueError):
            missions.objectives_script("sw_allies11", {**doc, "radio": [{"text": "x", "delay": "soon"}]}, lambda squad: ["x1"])

    def test_music(self):
        doc = {"objectives": [{"type": "goto", "text": "A", "zone": "a"}],
               "music": [{"when": "starts:1", "play": "music.Card_Ace"}, {"when": "zone:z", "play": "normal", "delay": 5},
                         {"when": "end", "play": "music.Faction_Suc"}]}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: [])
        self.assertIn("{ when = 'starts', after = 'foundry1', play = 'music.Card_Ace' }", lua)
        self.assertIn("{ when = 'zone', zone = 'z', play = 'normal', delay = 5, cb = 'foundry_LaterM2' }", lua)
        self.assertIn("foundry_Radio('starts', o.name)\nfoundry_Musics('starts', o.name)", lua)
        self.assertIn("foundry_Radio('end') foundry_Musics('end')", lua)
        self.assertIn("if bFoundryMusic then foundry_NormalMusic() end\nfoundry_NormalWeather()\n"
                      "sReasonForFailure = sReason", lua)   # failing puts the music and weather back

    def test_weather(self):
        doc = {"objectives": [{"type": "goto", "text": "A", "zone": "a"}],
               "weather": [{"when": "starts:1", "preset": "summer1"}, {"when": "done:1", "preset": "normal", "delay": 4}]}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: [])
        self.assertIn("{ when = 'starts', after = 'foundry1', preset = 'summer1' }", lua)
        self.assertIn("preset = 'normal', delay = 4, cb = 'foundry_LaterS2' }", lua)
        self.assertIn("Mission_EnableAtmosphere(w.preset, true)", lua)
        self.assertIn("if sFoundryWeather then Mission_EnableAtmosphere(sFoundryWeather, false) end", lua)
        self.assertIn("foundry_Musics('starts', o.name)\nfoundry_Weathers('starts', o.name)", lua)
        with self.assertRaises(ValueError):
            missions.objectives_script("sw_allies11", {**doc, "weather": [{"preset": "two words"}]}, lambda squad: [])
        for bad in ("Card_Ace", "", "music.x y"):
            with self.assertRaises(ValueError):
                missions.objectives_script("sw_allies11", {**doc, "music": [{"play": bad}]}, lambda squad: [])

    def test_placed_giver_rules(self):
        placed, _ = missions.contract("sw_allies9", {"title": "T", "contact": {
            "template": "template_ace_clubs", "position": [1, 2, 3], "stay": True, "invincible": True,
            "lose_if_killed": True}}, "a", "b")
        lua = missions.contracts_script([placed])
        self.assertIn(", stay = true, invincible = true, lose = true }", lua)
        self.assertIn("Actor_SetVerifiable(name, FALSE)", lua)            # an Ace template: never verified
        self.assertIn("if c.stay then Ai_CommandQueue(name, 'Wait', 'hiPri') end", lua)
        self.assertIn("if c.lose then Event_ActorHitPointsLessThan('foundry_GiverKilled', 'b', name, 1) end", lua)
        self.assertIn("or Utility_ReadNumberFromScribbleMemory('foundry_lost_' .. c.id)) then", lua)
        # only for one placed on the map
        hq, _ = missions.contract("sw_allies9", {"title": "T", "contact": {"stay": True}}, "a", "b")
        self.assertNotIn("stay = true", missions.contracts_script([hq]).split("tFoundryContracts")[1].split("}")[0])

    def test_waves(self):
        layer = {"map": "SW", "encounters": [{"name": "camp", "points": {"A": [[0, 0, 0]], "B": [[5, 0, 5]]},
                                               "squads": [{"name": "Guards", "points": ["A"], "members": [{"name": "G1"}]},
                                                          {"name": "Ambush", "points": ["B"], "members": [{"name": "X1"}]}]}]}
        mission = {"waves": [{"when": "zone:bridge", "squad": "camp:Ambush"}]}
        rest, waves = missions.split_waves("sw_allies11", mission, layer)
        self.assertEqual([s["name"] for s in rest["encounters"][0]["squads"]], ["Guards"])     # moved out
        self.assertEqual(len(layer["encounters"][0]["squads"]), 2)                             # the input untouched
        self.assertEqual(waves[0][0], "sw_allies11_w1")
        enc = waves[0][1]["encounters"][0]
        self.assertEqual((enc["name"], sorted(enc["points"]), enc["squads"][0]["name"]), ("camp_w1", ["A", "B"], "Ambush"))
        with self.assertRaises(ValueError):
            missions.split_waves("sw_allies11", {"waves": [{"squad": "camp:Nobody"}]}, layer)
        doc = {"objectives": [{"type": "goto", "text": "A", "zone": "a"}], **mission,
               "orders": [{"when": "zone:bridge", "squad": "camp:Ambush", "do": "attack"}]}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: ["X1"])
        self.assertIn("{ when = 'zone', zone = 'bridge', layer = 'sw_allies11_w1' }", lua)
        # its order uses the wave's own encounter, and waits until the squad is there
        self.assertIn("act = 'attack', enc = 'camp_w1', squad = 'Ambush', wave = 'sw_allies11_w1' }", lua)
        self.assertIn("if o.wave and not tFoundryWaveUp[o.wave] then o.pending = true return end", lua)
        self.assertIn("Utility_ReadLevelFile(w.layer)", lua)
        self.assertIn("foundry_Waves('start')\nfoundry_Radio('start')", lua)
        self.assertNotIn("#", lua.split("\n", 1)[1])

    def test_squad_orders(self):
        doc = {"objectives": [{"type": "goto", "text": "A", "zone": "a"}],
               "orders": [{"when": "start", "squad": "camp:Guards", "do": "hold"},
                          {"when": "done:1", "squad": "camp:Guards", "do": "attack"},
                          {"when": "zone:ridge", "squad": "camp:Patrol", "do": "path", "to": "foundry_path1"},
                          {"when": "start", "squad": "camp:Guards", "do": "move", "to": "B"},
                          {"when": "start", "squad": "camp:Guards", "do": "close"}]}
        members = {"camp:Guards": ["G1", "G2"], "camp:Patrol": ["P1"]}
        lua = missions.objectives_script("sw_allies11", doc, lambda squad: members[squad])
        self.assertIn("{ when = 'start', act = 'hold', enc = 'camp', squad = 'Guards' }", lua)
        self.assertIn("{ when = 'done', after = 'foundry1', act = 'attack', enc = 'camp', squad = 'Guards' }", lua)
        self.assertIn("{ when = 'zone', zone = 'ridge', act = 'path', enc = 'camp', squad = 'Patrol', to = 'foundry_path1', "
                      "members = { 'P1' } }", lua)
        # retail's own verbs
        self.assertIn("Squad_Command(o.enc, o.squad, 'Attack', 'player0', 'hiPri')", lua)
        self.assertIn("Squad_Command(o.enc, o.squad, 'Wait', 'hiPri')", lua)
        self.assertIn("Squad_Command(o.enc, o.squad, 'TacticalMove', o.to, 'hiPri')", lua)
        self.assertIn("Squad_Command(o.enc, o.squad, 'MoveToActor', 'player0', 1.0, 1, 'hiPri')", lua)
        self.assertIn("Ai_CommandQueue(a, 'PathMove', o.to, '1.0', 'hiPri')", lua)
        self.assertIn("foundry_Orders('start')", lua)
        self.assertIn("foundry_Orders('done', o.name)", lua)
        self.assertNotIn("#", lua.split("\n", 1)[1])
        for bad in ({"squad": "camp:Guards", "do": "dance"}, {"squad": "Guards", "do": "hold"},
                    {"squad": "camp:Guards", "do": "move"}, {"squad": "camp:Guards", "do": "path"}):
            with self.assertRaises(ValueError):
                missions.objectives_script("sw_allies11", dict(doc, orders=[bad]), lambda squad: members.get(squad, ["X"]))

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
        self.assertIn("or not foundry_Unlocked(c)\nor Utility_ReadNumberFromScribbleMemory('foundry_lost_' .. c.id)) then", chained_lua)
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


class TravelTests(unittest.TestCase):
    def test_travel_lua_parses_and_arms_each_pilot(self):
        from mercfoundry import luacheck
        pilot = {"map": "sw", "actor": "foundry_travel_isle_1", "to": "isle", "prompt": "Travel to Isle",
                 "title": "Isle", "question": "Travel to Isle?"}
        lua = missions.layers_script([{"map": "sw", "layer": "foundry_travel_isle_1", "check": "foundry_travel_isle_1"}],
                                     [pilot], True)
        self.assertEqual(luacheck.check(lua), [])
        self.assertIn("foundry_Arrive()\nfoundry_TravelArm()\nend", lua)
        self.assertIn("Utility_QuestionBox(t.title, t.question, true, 'foundry_TravelGo1', 'foundry_TravelArm')", lua)
        back = dict(pilot, map="isle", actor="foundry_return_isle", to="sw", arrive=[1, 2, 3])
        lua = missions.travel_lua("foundry_isle_Travel", [back], by_map=False)
        self.assertEqual(luacheck.check(lua), [])
        self.assertIn("ax = 1.00, ay = 2.00, az = 3.00, ah = 180.0", lua)
        self.assertIn("Utility_ResetToBaseLayerOnly(t.to)", lua)


class NewMapMissionTests(unittest.TestCase):
    def test_contract_takes_the_player_to_the_new_map(self):
        from mercfoundry import luacheck
        doc = {"title": "Isle job", "map": "isle", "contact": {"position": [1, 2, 3], "heading": 90}}
        self.assertEqual(missions.play_map("sw_china12", doc), "isle")
        self.assertIsNone(missions.play_map("sw_china12", {"map": "sw"}))
        c, _ = missions.contract("sw_china12", doc, "t", "d")
        self.assertEqual((c["map"], c["play"]), ("isle", "isle"))       # the giver stands there
        lua = missions.contracts_script([c])
        self.assertEqual(luacheck.check(lua), [])
        start = lua[lua.index("function foundry_Start_sw_china12()"):]
        self.assertIn("Utility_WriteStringToScribbleMemory('foundry_play_map', 'isle')\n"
                      "Utility_WriteStringToScribbleMemory('foundry_play_arc', 'sw')\n"
                      "Utility_WriteStringToScribbleMemory('foundry_map_start', 'accepted sw_china12, loading isle')\n"
                      "Utility_ResetToBaseLayerOnly('isle')", start)
        self.assertNotIn("DebugSkipToMission", start[:start.index("\ntFoundryStart")])
        # accepted before the map loads, as retail's starter: the pause menu
        # built with the level then offers no SAVE GAME during the contract
        self.assertIn("local n = util_MissionAssetNumToMissionSeqNum('sw', f, 12)\n"
                      "if n then\n"
                      "Utility_WriteNumberToScribbleMemory('SkipTo_MissionNumber', n)\n"
                      "Utility_EraseValueFromScribbleMemory('skipto_via_cheating')\n"
                      "Utility_WriteStringToScribbleMemory('current_faction', f)\n"
                      "Utility_WriteNumberToScribbleMemory('current_' .. f .. '_mission', n)\n"
                      "Utility_WriteNumberToScribbleMemory('mission_accepted', 1)\n"
                      "else", start)
        self.assertLess(start.index("'mission_accepted', 1"), start.index("Utility_ResetToBaseLayerOnly('isle')"))
        self.assertIn("local map = sFoundryOnMap or Utility_ReadStringFromScribbleMemory('SkipTo_Map')", lua)
        # an HQ contact for it stays at the HQ, and still takes the player to the island
        c2, _ = missions.contract("sw_china12", {"map": "isle"}, "t", "d")
        self.assertEqual((c2["map"], c2["play"]), ("sw", "isle"))

    def test_the_new_maps_script_runs_it(self):
        from mercfoundry import luacheck
        start, funcs = missions.map_mission_lua("isle")
        self.assertEqual(start, ["sFoundryOnMap = 'isle'", "if not foundry_isle_Mission() then foundry_isle_FreeRoam() end"])
        self.assertEqual(luacheck.check(funcs), [])
        self.assertIn("local a = n and util_MissionSeqNumToMissionAssetNum(q, f, n)", funcs)
        self.assertIn("local ok = Utility_RunLuaScript('foundry_isle_FreeRoam', q .. '_' .. f .. a)", funcs)
        # not in the arc: free roam instead, said on screen and noted for foundry_MapStart()
        self.assertIn("'not in the ' .. f .. ' ' .. q .. ' arc'", funcs)
        self.assertIn("function foundry_MapStart()", funcs)
        # loading the map sets SkipTo_Map to it (tried in game): the arc's map
        # is the contract's own key, put back in SkipTo_Map for the mission
        self.assertIn("local q = Utility_ReadStringFromScribbleMemory('foundry_play_arc')", funcs)
        self.assertNotIn("local q = Utility_ReadStringFromScribbleMemory('SkipTo_Map')", funcs)
        self.assertLess(funcs.index("Utility_WriteStringToScribbleMemory('SkipTo_Map', q)"),
                        funcs.index("Utility_RunLuaScript("))
        self.assertLess(funcs.index("if not q or not tArcs[f] or not tArcs[f][q] then"),
                        funcs.index("util_MissionAssetNumToMissionSeqNum("))
        free = funcs[funcs.index("function foundry_isle_FreeRoam()"):]
        self.assertIn("Utility_WriteStringToScribbleMemory('SkipTo_Map', 'isle')", free)
        self.assertIn("Utility_EraseValueFromScribbleMemory('foundry_play_arc')", free)
