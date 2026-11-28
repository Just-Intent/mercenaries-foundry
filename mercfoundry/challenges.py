"""Side contracts (retail's challenges, the $ icons) on a mod's new map,
run by retail's own framework (0.397).

How retail's work (challenge.lua, challenge_tools/_common, the type modules
challenge_race/_destroy/_deliver/_homerun, sw_challenge.lua):
- Free roam (global's LoadNoMissionState, a timer: SetupChallenges) loads
  `challenge`, runs challenge_Init(quadrant, chapter), which loads
  "<quadrant>_challenge" and runs its challenge_LoadLayers, then its
  challenge_SetupEvents: g_tChallengeInstances, each passed to
  challenge_Recycler_Init (hidden while mission_accepted).
- An instance table: the starter (sStarterTemplate spawned at the location
  sStarterLocation as sStarterNameBase<n>; a vehicle is entered, a human
  talked to, a prop examined), the question box (sQuestionBoxWindowTitleKey,
  sQuestionBoxInsideTextKey, tGoals/tRules/tBonuses {sText, vValue}),
  vReward (a number or a function), iLevel, tFactionImprove, and
  sFuncNamePrefix: the framework calls <prefix>_Reinit, _QuestionBox,
  _Start, _End, _WaitForReinitOpportunity, _SetupAiInNamedVehicle, _Show,
  _Hide, _OnStarterSpent, _OnStarterBroken, which the quadrant script
  defines as one-line calls into it. Accepted, it runs sChallengeScriptName
  (Utility_RunLuaScript, its own Lua state), whose ScriptInit calls the
  type's init; the script says how it ended (challenge_CompleteStateSet(won,
  rewarded)); the framework pays (times challenge_BonusMult) and improves
  the factions.
- Records: Challenge_Read/WriteStat* by challenge name (script values,
  hashed: any name works, saved with the game); levels: scribble
  <name>_Level.
- Starters out of sight recycle (removed beyond 150 m, back within 160 m).

A mod's: challenges/<id>.json, played on its "map" (a mod's new map). The
build writes the map's <map>_challenge, one script per challenge
(<map>_challenge_<id>), and a layer of its locations (copies of
loc_playerstart: the starter's, checkpoints, spawn points, the destination,
the measuring point) that loads with the map; the map's master script runs
the framework when free roam starts there. Records and levels are under
<map>_challenge_<id>, so they're the map's own.
"""

import re

TYPES = ("race", "destroy", "deliver", "homerun")
SCRIPTS = ("challenge", "challenge_tools", "challenge_common", "util_misc", "util_debug", "spawn", "table")
TYPE_SCRIPTS = {"race": "challenge_race", "destroy": "challenge_destroy", "deliver": "challenge_deliver",
                "homerun": "challenge_homerun"}
LOCATION = "loc_playerstart"          # a location object to copy (retail's maps all have one)
BLIPS = ("blip", "car", "human", "boat", "plane", "helicopter")


def _lua(v):
    from .missions import _lua as lua
    return lua(v)


def _xyz(v, what):
    try:
        p = [float(x) for x in v]
    except (TypeError, ValueError):
        p = []
    if len(p) != 3:
        raise ValueError(f"{what} is [x, y, z]")
    return p


def _spot(v, what):
    """{"position", "heading"?} or [x, y, z] -> (x, y, z, yaw)."""
    if isinstance(v, dict):
        return (*_xyz(v.get("position"), f"{what} \"position\""), float(v.get("heading") or 0))
    return (*_xyz(v, what), 0.0)


def _num(v, what, lo=0.0):
    if not isinstance(v, (int, float)) or isinstance(v, bool) or v < lo:
        raise ValueError(f"{what} is a number (at least {lo:g})")
    return float(v)


def parse(where, cid, doc, new_maps):
    """challenges/<id>.json checked: {"map", "type", "title", "description",
    "goals"?, "rules"?, "bonuses"? (texts), "starter": {"template",
    "position", "heading"?}, "reward": n | {"per_level": n, "max_level"?},
    "levels"? [per level: the time (race, deliver, destroy) or distance
    (homerun) to beat], "faction"? {faction: mood}, "honor_attitude"?,
    "dependent"? [actors], "spawns"? [{"template", "position", "heading"?}],
    and the type's: race "checkpoints" [[x, y, z]...], "time", "laps"?;
    destroy "targets" {"template", "positions"} and/or "kinds" {model:
    count}, "time", "blip"?; deliver "to", "radius"?, "time", "object"?
    {"template", "position"} (else the starter vehicle itself), "get_out"?;
    homerun "time", "to"? (the measuring point; else the prop's start),
    "closest"?, "area"? {"position", "radius"}."""
    if not isinstance(doc, dict):
        raise ValueError(f"{where}: expected {{\"map\", \"type\", ...}}")
    if not re.fullmatch(r"[a-z0-9_]+", cid):
        raise ValueError(f"{where}: a side contract's id (its file name) is lower-case letters, digits and _")
    m = str(doc.get("map") or "").lower()
    if m not in new_maps:
        raise ValueError(f"{where}: \"map\" is one of the mod's new maps ({', '.join(sorted(new_maps)) or 'none'})")
    kind = doc.get("type")
    if kind not in TYPES:
        raise ValueError(f"{where}: \"type\" is one of {', '.join(TYPES)} (taxi isn't yet)")
    name = f"{m}_challenge_{cid}"
    c = {"id": cid, "map": m, "type": kind, "name": name, "prefix": f"foundry_ch_{m}_{cid}",
         "title": str(doc.get("title") or cid), "description": str(doc.get("description") or "")}
    for k in ("goals", "rules", "bonuses"):
        v = doc.get(k) or []
        if not isinstance(v, list) or not all(isinstance(t, str) for t in v):
            raise ValueError(f"{where}: \"{k}\" is a list of texts")
        c[k] = list(v)
    st = doc.get("starter")
    if not isinstance(st, dict) or not st.get("template"):
        raise ValueError(f"{where}: \"starter\" is {{\"template\", \"position\", \"heading\"?}} (a vehicle is "
                         "entered, a person talked to, a prop examined)")
    c["starter"] = (str(st["template"]), _spot(st, f"{where}: \"starter\""))
    rw = doc.get("reward", 0)
    if isinstance(rw, dict):
        c["reward"] = ("level", _num(rw.get("per_level"), f"{where}: \"reward\" \"per_level\""),
                       int(rw.get("max_level") or 0))
    else:
        c["reward"] = ("fixed", _num(rw, f"{where}: \"reward\""), 0)
    lv = doc.get("levels") or []
    if not isinstance(lv, list):
        raise ValueError(f"{where}: \"levels\" is a list (per level: the time or distance to beat)")
    c["levels"] = [_num(x, f"{where}: a level") for x in lv]
    fac = doc.get("faction") or {}
    if not isinstance(fac, dict):
        raise ValueError(f"{where}: \"faction\" is {{faction: mood}} (retail's tFactionImprove, e.g. 20)")
    c["faction"] = {str(k).lower(): _num(v, f"{where}: \"faction\" {k}", -100) for k, v in fac.items()}
    c["honor"] = bool(doc.get("honor_attitude"))
    c["dependent"] = [str(a) for a in doc.get("dependent") or []]
    c["spawns"] = []
    for i, s in enumerate(doc.get("spawns") or [], 1):
        if not isinstance(s, dict) or not s.get("template"):
            raise ValueError(f"{where}: spawn {i} is {{\"template\", \"position\", \"heading\"?}}")
        c["spawns"].append((str(s["template"]), _spot(s, f"{where}: spawn {i}")))
    c["time"] = _num(doc.get("time", 120), f"{where}: \"time\"", 1)
    if kind == "race":
        cps = doc.get("checkpoints") or []
        if not isinstance(cps, list) or len(cps) < 2:
            raise ValueError(f"{where}: a race needs \"checkpoints\": [[x, y, z], ...] (two or more)")
        c["checkpoints"] = [_xyz(p, f"{where}: checkpoint {i}") for i, p in enumerate(cps, 1)]
        c["laps"] = int(doc.get("laps") or 1)
        c["radius"] = _num(doc.get("radius", 10), f"{where}: \"radius\"", 1)
    elif kind == "destroy":
        t = doc.get("targets")
        c["targets"] = None
        if t is not None:
            if not isinstance(t, dict) or not t.get("template") or not t.get("positions"):
                raise ValueError(f"{where}: \"targets\" is {{\"template\", \"positions\": [[x, y, z], ...]}}")
            c["targets"] = (str(t["template"]), [_spot(p, f"{where}: target") for p in t["positions"]])
        kinds = doc.get("kinds") or {}
        if not isinstance(kinds, dict):
            raise ValueError(f"{where}: \"kinds\" is {{model: how many}} (retail's tTargetOdfs and quotas)")
        c["kinds"] = {str(k): int(_num(v, f"{where}: \"kinds\" {k}", 1)) for k, v in kinds.items()}
        if not c["targets"] and not c["kinds"]:
            raise ValueError(f"{where}: a destroy side contract needs \"targets\" or \"kinds\"")
        c["blip"] = str(doc.get("blip") or "car")
        if c["blip"] not in BLIPS:
            raise ValueError(f"{where}: \"blip\" is one of {', '.join(BLIPS)}")
    elif kind == "deliver":
        c["to"] = _spot(doc.get("to"), f"{where}: \"to\"")
        c["radius"] = _num(doc.get("radius", 5), f"{where}: \"radius\"", 1)
        o = doc.get("object")
        if o is not None and (not isinstance(o, dict) or not o.get("template")):
            raise ValueError(f"{where}: \"object\" is {{\"template\", \"position\", \"heading\"?}} (left out: the "
                             "starter vehicle itself is delivered)")
        c["object"] = (str(o["template"]), _spot(o, f"{where}: \"object\"")) if o else None
        c["get_out"] = bool(doc.get("get_out"))
    elif kind == "homerun":
        c["to"] = _spot(doc.get("to"), f"{where}: \"to\"") if doc.get("to") is not None else None
        c["closest"] = bool(doc.get("closest"))
        a = doc.get("area")
        c["area"] = (_spot(a, f"{where}: \"area\""), _num(a.get("radius"), f"{where}: \"area\" \"radius\"", 1)) \
            if isinstance(a, dict) else None
    return c


def loc(c, what):
    return f"{c['name']}_{what}"


def layer(c):
    """(layer name, encounters doc) of a side contract's locations."""
    objs = []

    def add(what, spot):
        objs.append({"from": LOCATION, "name": loc(c, what), "position": list(spot[:3]), "yaw": spot[3]})
    add("starter", c["starter"][1])
    for i, (_, s) in enumerate(c["spawns"], 1):
        add(f"spawn{i}", s)
    if c["type"] == "race":
        for i, p in enumerate(c["checkpoints"], 1):
            add(f"chk{i}", (*p, 0.0))
    elif c["type"] == "destroy" and c["targets"]:
        for i, s in enumerate(c["targets"][1], 1):
            add(f"target{i}", s)
    elif c["type"] == "deliver":
        add("dest", c["to"])
        if c["object"]:
            add("object", c["object"][1])
    elif c["type"] == "homerun":
        if c["to"]:
            add("point", c["to"])
        if c["area"]:
            add("area", c["area"][0])
    return c["name"], {"map": c["map"].upper(), "load": "map", "objects": objs}


def keys(c):
    base = f"challenge.{c['map']}.{c['id']}"
    return {"title": f"{base}.title", "description": f"{base}.description"}


def strings(c):
    k = keys(c)
    out = {k["title"]: c["title"], k["description"]: c["description"]}
    for what in ("goals", "rules", "bonuses"):
        for i, t in enumerate(c[what], 1):
            out[f"challenge.{c['map']}.{c['id']}.{what}{i}"] = t
    return out


def _helpers(c):
    """Lua both scripts use: the level (scribble <name>_Level) and the goal."""
    P, N = c["prefix"], c["name"]
    lv = ", ".join(f"{v:g}" for v in c["levels"])
    stat = {"race": "BestTime", "deliver": "BestTime", "destroy": "BestTime",
            "homerun": "ClosestDistance" if c.get("closest") else "FarthestDistance"}[c["type"]]
    default = "nil" if c["type"] == "homerun" else f"{c['time']:g}"
    return f"""{P}tLevels = {{ {lv} }}
function {P}Level()
local i = Utility_ReadNumberFromScribbleMemory('{N}_Level')
if not i then i = 1 Utility_WriteNumberToScribbleMemory('{N}_Level', i) end
return i
end
-- the level's goal (a time, or a homerun's distance); past the levels, the record
function {P}Goal()
local g = {P}tLevels[{P}Level()]
if g then return g end
local r = Challenge_ReadStatFloat('{N}', '{stat}')
if r then return r end
return {default}
end
"""


def quadrant_script(map_name, chs):
    """The map's <map>_challenge script, as retail's sw_challenge: its
    instances, challenge_SetupEvents/ShowAll/HideAll and each instance's
    callbacks into the framework."""
    M = map_name.upper()
    rows = []
    for c in chs:
        P = c["prefix"]
        k = keys(c)
        goals = []
        if c["type"] in ("race", "deliver", "destroy"):
            goals.append(f"{{ sText = '[challenge.goal.time]', vValue = {P}GoalText }}")
        goals += [f"{{ sText = '[challenge.{c['map']}.{c['id']}.goals{i}]' }}" for i in range(1, len(c["goals"]) + 1)]
        rules = [f"{{ sText = '[challenge.{c['map']}.{c['id']}.rules{i}]' }}" for i in range(1, len(c["rules"]) + 1)]
        bonuses = [f"{{ sText = '[challenge.{c['map']}.{c['id']}.bonuses{i}]' }}"
                   for i in range(1, len(c["bonuses"]) + 1)]
        kind, amount, top = c["reward"]
        if kind == "level":
            reward = (f"function {P}Value()\nlocal i = {P}Level()\n"
                      + (f"if i > {top} then i = {top} end\n" if top else "")
                      + f"return i * {amount:g}\nend\n")
        else:
            reward = f"function {P}Value() return {amount:g} end\n"
        goal_text = ("function {P}GoalText()\nlocal g = {P}Goal()\nif not g then return '<SKIP>' end\n"
                     "if g > 0 then return Utility_SecondsToClock(g) end\nreturn '<SKIP>'\nend\n").format(P=P)
        fac = ", ".join(f"{f} = {v:g}" for f, v in sorted(c["faction"].items()))
        spawns = ""
        if c["spawns"]:
            spawns = (f"tSpawnTemplates = {{ {', '.join(_lua(t) for t, _ in c['spawns'])} }},\n"
                      f"tSpawnLocations = {{ {', '.join(_lua(loc(c, f'spawn{i}')) for i in range(1, len(c['spawns']) + 1))} }},\n")
        dep = f"tDependentActors = {{ {', '.join(_lua(a) for a in c['dependent'])} }},\n" if c["dependent"] else ""
        rows.append(f"""-- {c['id']} ({c['type']}): challenges/{c['id']}.json
{_helpers(c)}{reward}{goal_text}t{P} = {{
sChallengeScriptName = {_lua(c['name'])},
vReward = {P}Value,
{f"iLevel = {P}Level," + chr(10) if c['levels'] else ""}
tFactionImprove = {{ {fac} }},
tGoals = {{ {', '.join(goals)} }},
tRules = {{ {', '.join(rules)} }},
tBonuses = {{ {', '.join(bonuses)} }},
sStarterLocation = {_lua(loc(c, 'starter'))},
sStarterTemplate = {_lua(c['starter'][0])},
sStarterNameBase = {_lua(c['name'] + '_starter_')},
sQuestionBoxWindowTitleKey = {_lua(k['title'])},
sQuestionBoxInsideTextKey = {_lua('[' + k['description'] + ']')},
bQuestionBoxPauseGame = true,
bHonorFactionAttitude = {'true' if c['honor'] else 'false'},
{spawns}{dep}sFuncNamePrefix = {_lua(P)}
}}
function {P}_Reinit() challenge_Recycler_Reinit(t{P}) end
function {P}_QuestionBox(a, b, c) challenge_Recycler_QuestionBox(t{P}, a, b, c) end
function {P}_Start() challenge_Recycler_Start(t{P}) end
function {P}_End() challenge_Recycler_End(t{P}) end
function {P}_WaitForReinitOpportunity() challenge_Recycler_WaitForReinitOpportunity(t{P}) end
function {P}_SetupAiInNamedVehicle(a, b, c) challenge_Recycler_Vehicle_Setup(t{P}, a, b) end
function {P}_Show(a, b, c) challenge_Recycler_Show(t{P}, a, b) end
function {P}_Hide(a, b, c) challenge_Recycler_Hide(t{P}, a, b) end
function {P}_OnStarterSpent() challenge_Recycler_OnStarterSpent(t{P}) end
function {P}_OnStarterBroken() challenge_Recycler_OnStarterBroken(t{P}) end
""")
    insts = ",\n".join(f"t{c['prefix']}" for c in chs)
    return f"""-- Generated by Mercenaries Foundry: {map_name}'s side contracts (challenges/*.json),
-- run by retail's challenge framework, as sw_challenge.
if {M}_CHALLENGE_LUA_LOADED then return end
{M}_CHALLENGE_LUA_LOADED = 1
-- (their layers load with the map)
function challenge_LoadLayers(sQuadrant, iChapter)
challenge_LoadLayers = nil
end
{''.join(rows)}function challenge_SetupEvents(sQuadrant, iChapter)
g_tChallengeInstances = {{
{insts}
}}
for i, tChallengeInstance in ipairs(g_tChallengeInstances) do
challenge_Recycler_Init(tChallengeInstance)
end
if Utility_ReadNumberFromScribbleMemory('mission_accepted') then
challenge_HideAll()
end
challenge_SetupEvents = nil
end
function challenge_ShowAll()
if g_iChallengeInstanceIndex == nil then g_iChallengeInstanceIndex = 1 end
if g_tChallengeInstances[g_iChallengeInstanceIndex] then
challenge_Recycler_Show(g_tChallengeInstances[g_iChallengeInstanceIndex])
g_iChallengeInstanceIndex = g_iChallengeInstanceIndex + 1
Event_RelativeTimer('challenge_ShowAll', 'b', 0.1)
else
g_iChallengeInstanceIndex = nil
end
end
function challenge_HideAll()
for i, tChallengeInstance in ipairs(g_tChallengeInstances) do
challenge_Recycler_Hide(tChallengeInstance)
end
end
"""


def challenge_script(c):
    """A side contract's own script (sChallengeScriptName), as retail's
    sw_challenge_race01 and the rest: the type's init, and how it ended."""
    P, N, k = c["prefix"], c["name"], keys(c)
    head = (f"-- Generated by Mercenaries Foundry: side contract {c['id']} ({c['type']}) on {c['map']}\n"
            + _helpers(c)
            + f"""-- won: the level's goal beaten moves it on; past the levels, a record pays
function {P}Won(bRecord)
local i = {P}Level()
if {P}tLevels[i] then
Utility_WriteNumberToScribbleMemory('{N}_Level', i + 1)
challenge_CompleteStateSet(true, true)
else
challenge_CompleteStateSet(true, bRecord)
end
end
function OnFail()
challenge_CompleteStateSet(false, false)
end
function OnQuit()
end
""")
    common = (f"funcOnComplete = OnComplete, funcOnFail = OnFail, funcOnQuit = OnQuit,\n"
              f"funcOnReadyToShutDown = ScriptDeinit,\n"
              f"sChallengeName = {_lua(N)}, sChallengeTitleLanguageKey = {_lua(k['title'])},\n")
    t = c["type"]
    if t == "race":
        cps = ", ".join(_lua(loc(c, f"chk{i}")) for i in range(1, len(c["checkpoints"]) + 1))
        laps = f"iTotalCheckPointsCnt = {len(c['checkpoints']) * c['laps']},\n" if c["laps"] > 1 else ""
        body = f"""function ScriptInit()
Debug_EnableCallStack(false)
Utility_LoadUtilityScript('challenge_tools')
Utility_LoadUtilityScript('challenge_race')
Utility_LoadUtilityScript('util_misc')
local v = Utility_GetActorsVehicle('player0')
if not v or Actor_GetType(v) == 'TYPE_VEHICLE_HUMAN_PLAYER' then Utility_CloseThisScript() return end
challenge_RaceInit{{
{common}guidVehicle = v,
tCheckpointLocationNames = {{ {cps} }},
{laps}fRequiredCheckpointProximity = {c['radius']:g},
fTimeLimitInSeconds = {P}Goal()
}}
end
function OnComplete(tData) {P}Won(tData.bNewRecordAcheived) end
"""
    elif t == "deliver":
        obj = ""
        if c["object"]:
            obj = (f"local x, y, z = Location_GetPosition({_lua(loc(c, 'object'))})\n"
                   f"g_sObject = challenge_GenerateUniqueName({_lua(N + '_object')})\n"
                   f"Mission_Spawn({_lua(c['object'][0])}, g_sObject, x, y, z, Location_GetYaw({_lua(loc(c, 'object'))}))\n")
        else:
            obj = "g_sObject = Global_GetValue('sChallengeStarterName')   -- the starter vehicle itself\n"
        body = f"""function ScriptInit()
Debug_EnableCallStack(false)
Utility_LoadUtilityScript('challenge_tools')
Utility_LoadUtilityScript('challenge_deliver')
Utility_LoadUtilityScript('util_misc')
local v = Utility_GetActorsVehicle('player0')
if not v or Actor_GetType(v) == 'TYPE_VEHICLE_HUMAN_PLAYER' then Utility_CloseThisScript() return end
{obj}challenge_DeliverInit{{
{common}guidVehicle = v,
sDeliverObjectName = g_sObject,
sDestLocationName = {_lua(loc(c, 'dest'))},
fDestLocationRadius = {c['radius']:g},
bDeliverObjectMustExitVehicle = {'true' if c['get_out'] else 'false'},
fTimeLimitInSeconds = {P}Goal()
}}
end
function OnComplete(tData) {P}Won(tData.bNewRecordAcheived) end
"""
        if c["object"]:
            body += "function ScriptDeinit()\nif g_sObject then Actor_Remove(g_sObject) g_sObject = nil end\nUtility_CloseThisScript()\nend\n"
    elif t == "destroy":
        spawn = ""
        if c["targets"]:
            locs = ", ".join(_lua(loc(c, f"target{i}")) for i in range(1, len(c["targets"][1]) + 1))
            spawn = f"local tTargets = challenge_Spawn({_lua(N)}, {_lua(c['targets'][0])}, {{ {locs} }})\n"
        else:
            spawn = "local tTargets = nil\n"
        kinds = ""
        if c["kinds"]:
            ks = sorted(c["kinds"].items())
            kinds = (f"tTargetOdfs = {{ {', '.join(_lua(m) for m, _ in ks)} }},\n"
                     f"tiTargetOdfQuotas = {{ {', '.join(str(n) for _, n in ks)} }},\n")
        named = ("tTargetNames = tTargets,\niNamedTargetsQuota = table.getn(tTargets),\n" if c["targets"] else "")
        body = f"""function ScriptInit()
Debug_EnableCallStack(false)
Utility_LoadUtilityScript('challenge_tools')
Utility_LoadUtilityScript('challenge_common')
Utility_LoadUtilityScript('challenge_destroy')
{spawn}challenge_DestroyInit{{
{common}bFailOnPlayerDeath = true,
{named}{kinds}sTargetBlipType = {_lua(c['blip'])},
fTimeLimitInSeconds = {P}Goal()
}}
end
function OnComplete(tData) {P}Won(true) end
"""
    else:
        point = f"sMeasuringPointActor = {_lua(loc(c, 'point'))},\n" if c["to"] else ""
        area = (f"tAreaConstraintArgs = {{ sLocationName = {_lua(loc(c, 'area'))}, fRadius = {c['area'][1]:g}, "
                f"bUseXz = true, bFailOnExit = true, fBufferSeconds = 5 }},\n" if c["area"] else "")
        cmp = "<" if c["closest"] else ">"
        rec = "bNewClosestDistanceRecordAcheived" if c["closest"] else "bNewFarthestDistanceRecordAcheived"
        body = f"""function ScriptInit()
Debug_EnableCallStack(false)
Utility_LoadUtilityScript('challenge_tools')
Utility_LoadUtilityScript('challenge_homerun')
local sProp = Global_GetValue('sChallengeStarterName')
if not sProp then Utility_CloseThisScript() return end
challenge_HomerunInit{{
{common}sPropName = sProp,
guidVehicle = Utility_GetActorsVehicle('player0'),
{point}{area}bTrackFarthestDistanceStat = {'false' if c['closest'] else 'true'},
bTrackClosestDistanceStat = {'true' if c['closest'] else 'false'},
fTimeLimitInSeconds = {c['time']:g}
}}
end
function OnComplete(tReturns)
local g = {P}tLevels[{P}Level()]
if g then
if tReturns.fDistance {cmp} g then {P}Won(true) else challenge_CompleteStateSet(true, false) end
else
{P}Won(tReturns.{rec})
end
end
"""
    if "function ScriptDeinit()" not in body:
        body += "function ScriptDeinit()\nUtility_CloseThisScript()\nend\n"
    return head + body


def free_roam_lua(map_name):
    """The map's master script: retail's SetupChallenges, for this map, a
    moment after free roam starts (as global's)."""
    P = f"foundry_{map_name}_"
    return f"""-- Side contracts (challenges/*.json) by retail's framework, as global's SetupChallenges.
function {P}Challenges()
Utility_LoadUtilityScript('challenge')
local iChapter = Utility_ReadNumberFromScribbleMemory('chapter') or 1
if challenge_Init then challenge_Init({_lua(map_name)}, iChapter) end
if challenge_SetupEvents then challenge_SetupEvents({_lua(map_name)}, iChapter) end
end
"""
