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

    def test_add_to_list(self):
        lst = "\n[model]\n\nload a\n\n[script]\n\nload global\nload util_misc\n\n[texture]\n\nload t\n"
        out = missions.add_to_list(lst, "script", "sw_allies9")
        self.assertEqual(encounters.parse_list(out)["script"], ["global", "util_misc", "sw_allies9"])
        self.assertEqual(missions.add_to_list(out, "script", "SW_ALLIES9"), out)
        self.assertEqual(encounters.parse_list(missions.add_to_list("\n[model]\n\nload a\n", "script", "x"))["script"], ["x"])


if __name__ == "__main__":
    unittest.main()
