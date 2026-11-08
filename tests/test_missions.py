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

    def test_add_to_list(self):
        lst = "\n[model]\n\nload a\n\n[script]\n\nload global\nload util_misc\n\n[texture]\n\nload t\n"
        out = missions.add_to_list(lst, "script", "sw_allies9")
        self.assertEqual(encounters.parse_list(out)["script"], ["global", "util_misc", "sw_allies9"])
        self.assertEqual(missions.add_to_list(out, "script", "SW_ALLIES9"), out)
        self.assertEqual(encounters.parse_list(missions.add_to_list("\n[model]\n\nload a\n", "script", "x"))["script"], ["x"])


if __name__ == "__main__":
    unittest.main()
