import unittest

from mercfoundry import challenges, luacheck

RACE = {"map": "gaza", "type": "race", "title": "Coast Road Run", "description": "To the harbour.",
        "rules": ["Stay in the vehicle."], "starter": {"template": "template_hamas_technical_empty",
                                                       "position": [1, 2, 3], "heading": 90},
        "reward": 5000, "time": 150, "checkpoints": [[10, 0, 10], [20, 0, 20], [30, 0, 30]], "laps": 2,
        "faction": {"mafia": 10}}


class ChallengeTests(unittest.TestCase):
    def test_race_as_retails(self):
        c = challenges.parse("challenges/coast_road_run.json", "coast_road_run", RACE, {"gaza"})
        self.assertEqual((c["name"], c["prefix"]), ("gaza_challenge_coast_road_run", "foundry_ch_gaza_coast_road_run"))
        layer, doc = challenges.layer(c)
        self.assertEqual(layer, "gaza_challenge_coast_road_run")
        names = [o["name"] for o in doc["objects"]]
        self.assertEqual(names, ["gaza_challenge_coast_road_run_starter"] +
                         [f"gaza_challenge_coast_road_run_chk{i}" for i in (1, 2, 3)])
        self.assertTrue(all(o["from"] == "loc_playerstart" for o in doc["objects"]))
        self.assertEqual(doc["objects"][0]["yaw"], 90.0)
        q = challenges.quadrant_script("gaza", [c])
        self.assertEqual(luacheck.check(q), [])
        # retail's instance table and its ten callbacks into the framework
        for part in ("sChallengeScriptName = 'gaza_challenge_coast_road_run'",
                     "sStarterLocation = 'gaza_challenge_coast_road_run_starter'",
                     "sStarterTemplate = 'template_hamas_technical_empty'",
                     "sQuestionBoxWindowTitleKey = 'challenge.gaza.coast_road_run.title'",
                     "sQuestionBoxInsideTextKey = '[challenge.gaza.coast_road_run.description]'",
                     "tFactionImprove = { mafia = 10 }",
                     "{ sText = '[challenge.goal.time]', vValue = foundry_ch_gaza_coast_road_run GoalText }".replace(" GoalText", "GoalText"),
                     "{ sText = '[challenge.gaza.coast_road_run.rules1]' }",
                     "sFuncNamePrefix = 'foundry_ch_gaza_coast_road_run'",
                     "function challenge_SetupEvents(sQuadrant, iChapter)",
                     "challenge_Recycler_Init(tChallengeInstance)"):
            self.assertIn(part, q)
        for cb in ("Reinit", "QuestionBox", "Start", "End", "WaitForReinitOpportunity", "SetupAiInNamedVehicle",
                   "Show", "Hide", "OnStarterSpent", "OnStarterBroken"):
            self.assertIn(f"function foundry_ch_gaza_coast_road_run_{cb}(", q)
        self.assertNotIn("iLevel =", q)                  # no levels: no level in the title
        s = challenges.challenge_script(c)
        self.assertEqual(luacheck.check(s), [])
        self.assertIn("challenge_RaceInit{", s)
        self.assertIn("iTotalCheckPointsCnt = 6,", s)     # two laps of three
        self.assertIn("sChallengeName = 'gaza_challenge_coast_road_run'", s)   # its records are the map's own
        self.assertIn("Challenge_ReadStatFloat('gaza_challenge_coast_road_run', 'BestTime')", s)
        st = challenges.strings(c)
        self.assertEqual(st["challenge.gaza.coast_road_run.title"], "Coast Road Run")
        self.assertEqual(st["challenge.gaza.coast_road_run.rules1"], "Stay in the vehicle.")

    def test_other_types_and_levels(self):
        hunt = {"map": "gaza", "type": "destroy", "starter": {"template": "t_officer", "position": [0, 0, 0]},
                "reward": {"per_level": 10000, "max_level": 3}, "levels": [300, 240, 180],
                "targets": {"template": "t_truck", "positions": [[1, 0, 1], [2, 0, 2]]}, "kinds": {"rockettruck": 2}}
        aid = {"map": "gaza", "type": "deliver", "starter": {"template": "t_truck_empty", "position": [0, 0, 0]},
               "to": {"position": [5, 0, 5]}, "radius": 8, "get_out": True}
        push = {"map": "gaza", "type": "homerun", "starter": {"template": "t_wreck", "position": [0, 0, 0]},
                "levels": [25, 50], "time": 60, "closest": False,
                "spawns": [{"template": "t_d9", "position": [3, 0, 3]}], "area": {"position": [0, 0, 0], "radius": 80}}
        cs = [challenges.parse(f"challenges/{k}.json", k, d, {"gaza"})
              for k, d in (("hunt", hunt), ("aid", aid), ("push", push))]
        q = challenges.quadrant_script("gaza", cs)
        self.assertEqual(luacheck.check(q), [])
        self.assertIn("iLevel = foundry_ch_gaza_huntLevel,", q)
        self.assertIn("if i > 3 then i = 3 end\nreturn i * 10000", q)
        self.assertIn("tSpawnTemplates = { 't_d9' },\ntSpawnLocations = { 'gaza_challenge_push_spawn1' },", q)
        for c in cs:
            self.assertEqual(luacheck.check(challenges.challenge_script(c)), [], c["id"])
        hs = challenges.challenge_script(cs[0])
        self.assertIn("challenge_Spawn('gaza_challenge_hunt', 't_truck', { 'gaza_challenge_hunt_target1', "
                      "'gaza_challenge_hunt_target2' })", hs)
        self.assertIn("tTargetOdfs = { 'rockettruck' },\ntiTargetOdfQuotas = { 2 },", hs)
        self.assertIn("Utility_WriteNumberToScribbleMemory('gaza_challenge_hunt_Level', i + 1)", hs)
        ds = challenges.challenge_script(cs[1])
        self.assertIn("g_sObject = Global_GetValue('sChallengeStarterName')   -- the starter itself", ds)
        self.assertIn("bDeliverObjectMustExitVehicle = true", ds)
        # 0.398: a person starter is carried as a passenger (retail's Press Photo Op): on foot is fine
        self.assertIn("if v and Actor_GetType(v) == 'TYPE_VEHICLE_HUMAN_PLAYER' then v = nil end", ds)
        self.assertNotIn("Utility_CloseThisScript() return end\ng_sObject", ds)
        ps = challenges.challenge_script(cs[2])
        self.assertIn("if tReturns.fDistance > g then", ps)
        self.assertIn("tAreaConstraintArgs = { sLocationName = 'gaza_challenge_push_area', fRadius = 80", ps)
        self.assertIn("challenge_Init('gaza', iChapter)", challenges.free_roam_lua("gaza"))
        # 0.398: a homerun's own thrown object (retail's Hammer Toss), the starter its vehicle
        toss = challenges.parse("challenges/toss.json", "toss", {
            "map": "gaza", "type": "homerun", "starter": {"template": "t_heli", "position": [0, 0, 0]},
            "object": {"template": "t_tank", "position": [5, 0, 5]}}, {"gaza"})
        ts = challenges.challenge_script(toss)
        self.assertEqual(luacheck.check(ts), [])
        self.assertIn("challenge_Spawn('gaza_challenge_toss', { 't_tank' }, { 'gaza_challenge_toss_object' }, { nil })", ts)
        self.assertIn("local vVehicle = Utility_GetActor(sStarter)", ts)
        self.assertIn("sMeasuringPointActor = 'gaza_challenge_toss_object',", ts)
        self.assertIn("gaza_challenge_toss_object", [o["name"] for o in challenges.layer(toss)[1]["objects"]])
        # 0.398: taxi, its districts by centre and radius (tourguide_current_poi), labels its own
        taxi = challenges.parse("challenges/cab.json", "cab", {
            "map": "gaza", "type": "taxi", "starter": {"template": "t_civic", "position": [0, 0, 0]},
            "districts": [{"name": "Gaza City", "centre": [0, 0, 0], "radius": 300, "fares": [[1, 0, 1]],
                           "destinations": [[2, 0, 2]]},
                          {"name": "Shati", "centre": [500, 0, 0], "fares": [[501, 0, 1]], "destinations": [[502, 0, 2]],
                           "riders": ["t_r"]}]}, {"gaza"})
        xs = challenges.challenge_script(taxi)
        self.assertEqual(luacheck.check(xs), [])
        self.assertIn("Utility_LoadUtilityScript('gaza_foundry_taxi')", xs)
        self.assertIn("foundry_ch_gaza_cab_d1 = { 'challenge.gaza.cab.district1' }", xs)
        self.assertIn("Utility_WriteStringToScribbleMemory('tourguide_current_poi', 'foundry_ch_gaza_cab_d2')", xs)
        self.assertEqual(challenges.strings(taxi)["challenge.gaza.cab.district2"], "Shati")
        retail = "local x\ntPOI2MapLabel = {\na = 1 }\nDebug_Assert( sPlayersVehicleODF ~= nil, 'x' )\n"
        patched = challenges.taxi_script(retail)
        self.assertIn("tPOI2MapLabel = tTaxi.tPOI2MapLabel or {", patched)
        self.assertIn("tODF2MaxSpeed.foundry_any = tTaxi.nMaxSpeed or 40 end", patched)
        with self.assertRaises(ValueError):
            challenges.taxi_script("nothing to patch")
        for bad in ({"type": "taxi"}, {"map": "sw"}, {"starter": {}}, {"checkpoints": [[0, 0, 0]]},
                    {"reward": "lots"}, {"faction": ["allies"]}):
            with self.assertRaises(ValueError, msg=bad):
                challenges.parse("challenges/x.json", "x", dict(RACE, **bad), {"gaza"})
        with self.assertRaises(ValueError):
            challenges.parse("challenges/X.json", "Bad-Name", RACE, {"gaza"})
