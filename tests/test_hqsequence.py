import unittest

from mercfoundry import hqsequence as hq, missions

# Small synthetic snippets with the same anchors as the retail scripts.
UTIL = """function util_Other()
end
function util_IsPlayableIntro(quadrant, faction, mission)
return quadrant == 'sw' and faction == 'allies' and mission == 1
end
function util_IsAceMission(quadrant, faction, mission)
return
(quadrant == 'sw' and faction == 'allies' and mission == 3)
end
function util_IsFaceCardMission(faction, mission)
return faction ~= 'allies' and (mission == 3 or mission == 6)
end
function util_IsFinalMission(quadrant, faction, mission)
return (quadrant == 'nw' and faction == 'allies' and mission == 5)
end
function util_MissionSeqNumToMissionAssetNum(quadrant, faction, seqnum)
local tMissionArcs = util_GetMissionArcTable()
return tMissionArcs[faction][quadrant][seqnum]
end
"""

BOUNCER = """function ShouldBouncerBeSpawned(bouncerID)
if bouncerID ~= 'ace' then
if current_mission > util_GetLastMissionNumberInChapterArc(bouncerID, chapter) then
return false
end
end
return true
end
function BouncerUsed(ThisActor)
Actor_SetInvincible('player0', true)
end
"""

GLOBAL = """function SetupChapter(sQuadrant, SkipTo_Chapter, sFaction, iMission, bMissionAccepted, bSkipToBriefing)
local chapter
if iMission > 3 then chapter = 2 end
tFirstMissions = util_GetFirstMissionsForChapter(chapter)
end
"""

GAMEFLOW = """function gameflow_ShouldGameStateApply(tSubState, sQuadrant, iChapter)
local bInMissionState = Utility_ReadNumberFromScribbleMemory('mission_accepted')
if not gameflow_EvaluateFactionCondition(sConditionType, tConditionData, tCurrentMissions, bInMissionState, sCurrentFaction, sQuadrant, iChapter) then
end
end
"""


def plan(asset_id, hq_doc):
    return hq.plan(asset_id, {"hq": hq_doc}, missions.parse_id)


class ChapterTests(unittest.TestCase):
    def test_tables(self):
        # util_GetFirstMissionsForChapter / util_GetLastMissionNumberInChapterArc
        self.assertEqual([hq.first_in_chapter("allies", c) for c in (1, 2, 3, 4)], [2, 4, 1, 4])
        self.assertEqual([hq.first_in_chapter("china", c) for c in (1, 2, 3, 4)], [1, 4, 1, 4])
        self.assertEqual([hq.last_in_chapter("allies", c) for c in (1, 2, 3, 4)], [2, 5, 2, 4])
        self.assertEqual([hq.last_in_chapter("sk", c) for c in (1, 2, 3, 4)], [3, 6, 3, 6])
        self.assertEqual([hq.chapter_of("sw", "china", p) for p in (1, 3, 4, 6)], [1, 1, 2, 2])
        self.assertEqual([hq.chapter_of("nw", "china", p) for p in (1, 3, 4, 6)], [3, 3, 4, 4])

    def test_plan(self):
        p = plan("sw_china9", {"after": 3})          # between china 3 and 4 on sw: still chapter 1
        self.assertEqual((p["pos"], p["chapter"], p["number"]), (4, 1, 9))
        self.assertTrue(p["notes"])                   # past the chapter's last contract
        self.assertEqual(plan("sw_china9", {"after": 3, "chapter": 2})["chapter"], 2)   # at chapter 2's start
        self.assertEqual(plan("sw_china9", {"after": 0})["pos"], 1)
        self.assertEqual(plan("sw_china9", {"after": 6})["chapter"], 2)
        self.assertEqual(plan("nw_mafia9", {"after": 2})["chapter"], 3)
        self.assertEqual(plan("sw_allies9", {"after": 1})["pos"], 2)
        self.assertEqual(plan("sw_allies9", {"after": 1})["notes"], [])
        self.assertIn("ace", plan("sw_allies9", {"after": 2})["notes"][0])           # beside the ace bouncer
        self.assertEqual(plan("sw_allies9", {"after": 3})["chapter"], 2)             # after the ace: chapter 2
        self.assertEqual(plan("nw_allies9", {"after": 0})["chapter"], 3)
        self.assertEqual(plan("nw_allies9", {"after": 3})["chapter"], 4)
        self.assertIsNone(hq.plan("sw_china9", {}, missions.parse_id))
        for asset, bad in (("sw_allies9", {"after": 0}),          # the playable intro
                           ("sw_allies9", {"after": 6}),          # the campaign moves north
                           ("nw_allies9", {"after": 5}),          # after the final mission
                           ("sw_china9", {"after": 7}),
                           ("sw_china9", {"after": 4, "chapter": 1}),
                           ("sw_china9", {"after": 1, "chapter": 3}),   # chapter 3 is the north
                           ("sw_china9", {"after": -1}), ("sw_china9", {}), ("sw_china9", {"after": "x"})):
            with self.assertRaises(ValueError, msg=f"{asset} {bad}"):
                plan(asset, bad)
        with self.assertRaises(ValueError):
            hq.plan("sw_china9", {"hq": 3}, missions.parse_id)
        self.assertEqual(hq.ini_chapter(2), "MissionInfo.Chapter.Chapter2")

    def test_retail_contracts(self):
        ini = "[SW_China1]\nMissionTitleDbKey\t\tMissionInfo.MissionTitle.SWChina1\n\n[SW_China2]\nNumber 2\n"
        out = hq.retail_contracts([1, 2], ini, "sw", "china", {"MissionInfo.MissionTitle.SWChina1": "Opening"})
        self.assertEqual(out, [{"pos": 1, "number": 1, "chapter": 1, "title": "Opening"},
                               {"pos": 2, "number": 2, "chapter": 1, "title": None}])


class PatchTests(unittest.TestCase):
    def test_util_misc(self):
        out = hq.patch_util_misc(UTIL)
        self.assertIn("function foundry_InsHere(faction, seqnum)", out)
        for head in ("function util_IsPlayableIntro(quadrant, faction, mission)",
                     "function util_IsAceMission(quadrant, faction, mission)",
                     "function util_IsFaceCardMission(faction, mission)",
                     "function util_IsFinalMission(quadrant, faction, mission)"):
            self.assertIn(head + "\nif foundry_InsHere(faction, mission) then return false end\n", out)
        self.assertIn("function util_MissionSeqNumToMissionAssetNum(quadrant, faction, seqnum)\n"
                      "if foundry_InsHere(faction, seqnum) then return Utility_ReadNumberFromScribbleMemory('foundry_ins_num') end\n"
                      "local tMissionArcs", out)
        self.assertLess(out.index("function foundry_InsHere"), out.index("function util_IsPlayableIntro"))
        self.assertEqual(hq.patch_util_misc(out), out)                     # once
        crlf = hq.patch_util_misc(UTIL.replace("\n", "\r\n"))
        self.assertNotIn("\r\r", crlf)
        self.assertNotIn("\n", crlf.replace("\r\n", ""))                   # line ends kept
        with self.assertRaises(ValueError):
            hq.patch_util_misc(UTIL.replace("function util_IsFinalMission", "function util_IsLast"))

    def test_bouncer(self):
        out = hq.patch_bouncer(BOUNCER)
        self.assertIn("if current_mission > util_GetLastMissionNumberInChapterArc(bouncerID, chapter) "
                      "and not (foundry_HqPending and foundry_HqPending(bouncerID)) then\nreturn false", out)
        self.assertIn("function BouncerUsed(ThisActor)\n"
                      "if foundry_HqBouncerUsed and foundry_HqBouncerUsed(ThisActor) then return end\n"
                      "Actor_SetInvincible", out)
        self.assertEqual(hq.patch_bouncer(out), out)
        with self.assertRaises(ValueError):
            hq.patch_bouncer(BOUNCER.replace("function BouncerUsed", "function Other"))
        with self.assertRaises(ValueError):
            hq.patch_bouncer(BOUNCER.replace("current_mission >", "current_mission >="))

    def test_global_chapter(self):
        out = hq.patch_global_chapter(GLOBAL)
        self.assertIn("chapter = Utility_ReadNumberFromScribbleMemory('chapter') end   -- Mercenaries Foundry\n"
                      "tFirstMissions = util_GetFirstMissionsForChapter(chapter)", out)
        self.assertIn("Utility_ReadStringFromScribbleMemory('foundry_active_contract')", out)   # side contracts too
        self.assertEqual(hq.patch_global_chapter(out), out)
        with self.assertRaises(ValueError):
            hq.patch_global_chapter("function SetupChapter()\nend\n")
        # with the contacts' LoadNoMissionState hooks too
        both = missions.patch_global(out + "function LoadNoMissionState()\nSpawnBouncers(TRUE)\nend\n")
        self.assertEqual(hq.patch_global_chapter(both).count("-- Mercenaries Foundry"), 1)

    def test_gameflow(self):
        out = hq.patch_gameflow(GAMEFLOW)
        self.assertIn("tCurrentMissions, (Utility_ReadStringFromScribbleMemory('foundry_ins_faction') ~= sConditionType "
                      "and bInMissionState or nil), sCurrentFaction", out)
        self.assertEqual(hq.patch_gameflow(out), out)
        with self.assertRaises(ValueError):
            hq.patch_gameflow("function gameflow_ShouldGameStateApply()\nend\n")

    def test_contracts_lua(self):
        ins = dict(plan("sw_china9", {"after": 3}), title_key="MissionInfo.MissionTitle.SWChina9",
                   description_key="MissionInfo.Description.SWChina9")
        lua = missions.contracts_script([], [ins])
        self.assertIn("{ id = 'sw_china9', map = 'sw', faction = 'china', number = 9, chapter = 1, pos = 4, "
                      "title = 'MissionInfo.MissionTitle.SWChina9', desc = 'MissionInfo.Description.SWChina9' },", lua)
        self.assertIn("function foundry_HqPending(f)", lua)
        self.assertIn("for i, c in ipairs(tFoundryHq) do", lua)
        self.assertIn("and not Utility_ReadNumberFromScribbleMemory('foundry_done_' .. c.id) then return c end", lua)
        self.assertIn("'foundry_HqYes', 'foundry_HqNo')", lua)
        yes = lua[lua.index("function foundry_HqYes()"):]
        for line in ("Utility_WriteStringToScribbleMemory('foundry_ins_active', c.id)",
                     "Utility_WriteNumberToScribbleMemory('foundry_ins_pos', pos)",
                     "Utility_WriteNumberToScribbleMemory('foundry_ins_num', c.number)",
                     "Utility_WriteStringToScribbleMemory('foundry_active_contract', c.id)",
                     "Utility_WriteNumberToScribbleMemory('mission_accepted', 1)",
                     "Utility_WriteStringToScribbleMemory('PlayerStartLocationName', 'loc_hq-' .. f .. '-respawn')",
                     "Utility_WriteNumberToScribbleMemory('SkipTo_MissionNumber', pos)"):
            self.assertIn(line, yes)
        self.assertLess(yes.index("foundry_Kept(f)"), yes.index("Utility_ResetToBaseLayerOnly()"))
        self.assertIn("b.usedHandle = Event_ActorUsed('BouncerUsed', 'b', b.actorName)", lua)   # no: bouncer again
        # completion: the arc position moved on, or JustCompletedStandardMission
        ret = lua[lua.index("function foundry_ReturnFromContract()"):lua.index("function foundry_SetupContracts()")]
        self.assertLess(ret.index("SkipTo_MissionNumber"), ret.index("local done"))          # not while a retry is queued
        self.assertLess(ret.index("== pos + 1 then done = true end"), ret.index("for i, key in foundry_Kept(f) do"))
        self.assertIn("if done then Utility_WriteNumberToScribbleMemory('foundry_done_' .. id, 1) end", ret)
        self.assertIn("'foundry_ins_active', 'foundry_ins_faction', 'foundry_ins_pos', 'foundry_ins_num'", ret)
        self.assertNotIn("#", lua.split("\n", 1)[1])
        self.assertNotIn("tFoundryHq", missions.contracts_script([]))


if __name__ == "__main__":
    unittest.main()
