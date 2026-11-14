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
        new = missions.add_to_arc(UTIL, "allies", "sw", 9, after=4)
        self.assertEqual(missions.arc(new, "allies", "sw"), [1, 3, 4, 9, 5, 6, 8])
        self.assertEqual(missions.arc(new, "allies", "nw"), [1, 3, 4, 2, 8])
        self.assertEqual(missions.arc(new, "china", "sw"), [1, 2, 3, 4, 5, 6])
        self.assertIn("sw = { 9, 9 }", new)                    # other functions untouched
        self.assertEqual(missions.add_to_arc(new, "allies", "sw", 9), new)
        self.assertEqual(missions.arc(missions.add_to_arc(UTIL, "china", "nw", 7), "china", "nw"), [1, 2, 3, 4, 5, 6, 7])
        with self.assertRaises(ValueError):
            missions.add_to_arc(UTIL, "allies", "sw", 9, after=7)

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
        self.assertIn("{ kind = 'goto', key = 'sw.allies11.obj.foundry1', zone = 'bridge_zone' }", lua)
        marked = missions.objectives_script("sw_allies11", doc, lambda squad: ["Nk1"], (), lambda z: [1, 2.5, -3])
        self.assertIn("zone = 'bridge_zone', x = 1.00, y = 2.50, z = -3.00 }", marked)
        self.assertIn("Objective_SetTargetVector(foundry_ObjectiveName(), o.zone, sFoundryObj, "
                      "'TARGET_TYPE_OBJECTIVE_PRIMARY', 'location',", marked)
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
        for bad in ([], [{"type": "fly"}], [{"type": "goto"}], [{"type": "destroy"}]):
            with self.assertRaises(ValueError):
                missions.objectives_script("sw_allies11", {"objectives": bad}, lambda squad: [])

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
        self.assertIn("'current_' .. f .. '_mission', 'total_missions_completed' .. f, "
                      "'num_std_missions_cmpl_' .. f", lua)
        self.assertIn("Utility_WriteStringToScribbleMemory('foundry_active_contract', 'sw_allies9')", lua)
        self.assertIn("Utility_WriteNumberToScribbleMemory('foundry_done_' .. id, 1)", lua)
        self.assertIn("repeatable = FALSE", lua)
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
