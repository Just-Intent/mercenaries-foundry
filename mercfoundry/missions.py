"""New contract missions.

How the game runs a mission (all in its scripts, established from retail):

  * Each faction has a mission arc per map: util_GetMissionArcTable() in the
    util_misc script lists asset numbers in play order, e.g. allies on sw
    { 1, 3, 4, 5, 6, 8 }.  'current_<faction>_mission' (scribble memory)
    holds the position in that list.
  * global's LoadMission(map, faction, n) turns the position into an asset ID
    ("sw_allies3") and runs the script of that name
    (Utility_RunLuaScript('LoadNoMissionState', 'sw_allies3')).
  * The mission script's ScriptInit() calls mission_GenericInit(), which reads
    the mission's mission.ini section (Utility_SetCurrentMissionData: contract
    fee, title and description string keys, ...) and loads the layer of the
    same name (Utility_ReadLevelFile('\\SW\\sw_allies3')).  The script ends the
    mission with mission_MissionComplete() or mission_MissionFailure().
  * DebugSkipToMission(faction, asset number) in global starts any mission in
    an arc (the port's F9 menu uses it for the retail ones).

A mod mission, missions/<map>_<faction><number>.json, e.g. sw_allies9.json:

  {"after": 8, "ini": {"ContractFee": 50000, "MissionTitleDbKey": "..."}}

adds the number to the faction's arc on that map (after asset "after", else
at the end) and a mission.ini section copied from the faction's first
mission on the map with "ini" applied.  The mod supplies the script
(scripts/sw_allies9.lua) and the layer (encounters/sw_allies9.json).
"""

import re

FACTIONS = ("allies", "china", "mafia", "sk")
MAPS = ("sw", "nw")
_ID = re.compile(r"^(sw|nw)_(allies|china|mafia|sk)(\d+)$", re.IGNORECASE)


def parse_id(asset_id):
    m = _ID.match(asset_id)
    if not m:
        raise ValueError(f"mission {asset_id!r}: name it <map>_<faction><number>, e.g. sw_allies9 "
                         f"(maps {', '.join(MAPS)}; factions {', '.join(FACTIONS)})")
    return m.group(1).lower(), m.group(2).lower(), int(m.group(3))


def arc(script_text, faction, map_):
    """The asset numbers of a faction's arc on a map, from util_misc."""
    m = _arc_match(script_text, faction, map_)
    return [int(v) for v in re.findall(r"\d+", m.group(2))]


def _arc_match(text, faction, map_):
    table = text.find("function util_GetMissionArcTable")
    start = re.compile(r"\b%s\s*=\s*\{" % faction).search(text, max(table, 0))
    if table < 0 or not start:
        raise ValueError(f"util_misc has no mission arc for {faction}")
    depth, end = 0, start.end() - 1
    for end in range(start.end() - 1, len(text)):           # the faction's matching brace
        depth += {"{": 1, "}": -1}.get(text[end], 0)
        if depth == 0:
            break
    m = re.compile(r"(\b%s\s*=\s*\{)([^}]*)(\})" % map_).search(text, start.end(), end)
    if not m:
        raise ValueError(f"util_misc has no {faction} arc for {map_}")
    return m


def add_to_arc(script_text, faction, map_, number, after=None):
    """util_misc text with the number in the arc (unchanged if it's there)."""
    m = _arc_match(script_text, faction, map_)
    nums = [int(v) for v in re.findall(r"\d+", m.group(2))]
    if number in nums:
        return script_text
    at = nums.index(after) + 1 if after is not None and after in nums else len(nums)
    if after is not None and after not in nums:
        raise ValueError(f"{faction} {map_} arc has no mission {after} to go after ({nums})")
    nums.insert(at, number)
    return script_text[:m.start(2)] + " " + ", ".join(map(str, nums)) + " " + script_text[m.end(2):]


def add_to_list(list_text, section, name):
    """A load list with "load <name>" added to [section] (made if missing).
    The game only runs scripts that a loaded list names: a map's own list
    (sw, nw) has every mission script under [script]."""
    lines = list_text.split("\n")
    head = next((i for i, l in enumerate(lines) if l.strip().lower() == f"[{section}]"), None)
    if head is None:
        return list_text.rstrip("\n") + f"\n\n[{section}]\n\nload {name}\n"
    end = next((i for i in range(head + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines))
    loads = [i for i in range(head + 1, end) if lines[i].strip().lower().startswith("load ")]
    if any(lines[i].strip()[5:].strip().lower() == name.lower() for i in loads):
        return list_text
    at = (loads[-1] + 1) if loads else head + 2
    lines.insert(at, f"load {name}")
    return "\n".join(lines)


def add_mail(emails_ini, mail_id, sender, subject_key, body_key):
    """DATAPOD/emails.ini with a [Mail] block (Id, Sender, Subject, Body are
    string keys).  mission_GenericInit posts '<mission>_initial' when the
    contract starts."""
    nl = "\r\n" if "\r\n" in emails_ini else "\n"
    if re.search(r"^Id\s+%s\s*$" % re.escape(mail_id), emails_ini, re.M | re.I):
        raise ValueError(f"emails.ini already has a mail {mail_id!r}")
    block = ["[Mail]", f"Id\t\t{mail_id}", f"Sender\t\t{sender}", f"Subject\t\t{subject_key}",
             f"Body\t\t{body_key}", "FolderPath", "Attachment1", "Attachment2", "Attachment3"]
    text = emails_ini.rstrip("\r\n") + nl + nl + nl.join(block) + nl
    return text


def text_plan(asset_id, doc, section):
    """(string edits, mission.ini overrides, mail) for a mission file's
    "title", "description", "mail" and "strings"."""
    key = section.replace("_", "")                     # SW_Allies9 -> SWAllies9, as retail keys
    strings = dict(doc.get("strings") or {})
    ini = {}
    if doc.get("objectives"):
        # The copied section's bonus pays for retail optional objectives; a
        # built mission's is its flat bonus ("ini" BonusFee, else none) plus
        # its bonus objectives' rewards (the script pays those earned).
        ini["BonusFee"] = bonus_fee(doc)
    if "title" in doc:
        strings[f"MissionInfo.MissionTitle.{key}"] = doc["title"]
        ini["MissionTitleDbKey"] = f"MissionInfo.MissionTitle.{key}"
    if "description" in doc:
        strings[f"MissionInfo.Description.{key}"] = doc["description"]
        ini["ShortDescriptionDbKey"] = f"MissionInfo.Description.{key}"
    for i, o in enumerate(doc.get("objectives") or [], 1):
        strings[objective_key(asset_id, i)] = o.get("text") or f"Objective {i}"
    mail = None
    if doc.get("mail"):
        m = doc["mail"]
        mail_id = f"{asset_id}_initial"
        subject, body = f"mail.{mail_id}.subject", f"mail.{mail_id}.body"
        strings[subject] = m.get("subject", doc.get("title", asset_id))
        strings[body] = m.get("body", "")
        mail = (mail_id, m.get("sender", "mail.senders.techgal"), subject, body)
    return strings, ini, mail


def objective_key(asset_id, i):
    """The string key of a generated objective's text: sw.allies9.obj.foundry1."""
    map_, faction, number = parse_id(asset_id)
    return f"{map_}.{faction}{number}.obj.foundry{i}"


def _lua_str(s):
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


OBJECTIVE_TYPES = ("destroy", "goto")


def bonus_amount(o):
    """A bonus objective's reward in dollars (0: a main objective)."""
    try:
        return max(0, int(float(o.get("bonus") or 0)))
    except (TypeError, ValueError):
        return 0


def bonus_fee(doc):
    """mission.ini BonusFee for a mission with objectives: its flat bonus
    ("ini" BonusFee, paid on completion) plus every bonus objective's reward,
    the most the contract can pay. The script pays the flat bonus plus the
    rewards of the bonus objectives done."""
    flat = int(float((doc.get("ini") or {}).get("BonusFee", 0) or 0))
    return flat + sum(bonus_amount(o) for o in doc.get("objectives") or [])


def objectives_script(asset_id, doc, squad_members, layers=(), zone_centre=None):
    """A mission script (Lua 5.0) for a mission file's "objectives":

      {"type": "destroy", "text": ..., "targets": [names]} or "squad": "Squad1"
          (the squad's members, by squad_members(name)); marked in the world
          and on the minimap, with a counter;
      {"type": "goto", "text": ..., "zone": name}: reach a zone (boundary).

    Main objectives are done one after another, then the mission completes.
    An objective with "bonus": <dollars> is a bonus objective, as retail's
    (BON_OBJ_*, secondary markers): optional, active from the start alongside
    the main ones; when the contract completes it pays the flat bonus
    (mission.ini BonusFee before the rewards) plus the rewards of the bonus
    objectives done, and the rest are shown as failed.

    layers: other layers the objectives use (the mod's map layers, which
    otherwise load only in free roam); the script loads them at its start.
    zone_centre(name): the zone's centre [x, y, z], where a goto objective
    puts its marker (GPS and minimap), or None for no marker.
    """
    objs = doc.get("objectives") or []
    if not objs:
        raise ValueError("a mission needs an objective")
    if not any(not bonus_amount(o) for o in objs):
        raise ValueError("a mission needs a main objective (one without a bonus)")
    main, bonus = [], []
    for i, o in enumerate(objs, 1):
        kind = o.get("type")
        if kind not in OBJECTIVE_TYPES:
            raise ValueError(f"objective {i}: type is one of {', '.join(OBJECTIVE_TYPES)}, not {kind!r}")
        head = f"kind = '{kind}', name = 'foundry{i}', key = {_lua_str(objective_key(asset_id, i))}"
        reward = bonus_amount(o)
        if reward:
            head += f", bonus = {reward}"
        if kind == "destroy":
            targets = list(o.get("targets") or [])
            if o.get("squad"):
                targets += squad_members(o["squad"])
            if not targets:
                raise ValueError(f"objective {i}: nothing to destroy (give \"targets\" or \"squad\")")
            row = f"{{ {head}, targets = {{ {', '.join(_lua_str(t) for t in targets)} }} }}"
        else:
            if not o.get("zone"):
                raise ValueError(f"objective {i}: a goto objective needs a \"zone\"")
            at = zone_centre(o["zone"]) if zone_centre and o.get("marker", True) else None   # "marker": false: none
            mark = f", x = {at[0]:.2f}, y = {at[1]:.2f}, z = {at[2]:.2f}" if at else ""
            row = f"{{ {head}, zone = {_lua_str(o['zone'])}{mark} }}"
        (bonus if reward else main).append(row)
    main_table = ",\n".join("  " + r for r in main)
    bonus_table = ",\n".join("  " + r for r in bonus)
    flat = bonus_fee(doc) - sum(bonus_amount(o) for o in objs)
    loads = "".join(f"Utility_ReadLevelFile({_lua_str(n)})   -- its squads or zones\n" for n in layers)
    start = doc.get("start") or {}
    if start.get("position"):     # where the player begins (on foot, as retail missions move the player)
        x, y, z = (float(v) for v in start["position"])
        loads += f"Actor_SetPosition('player0', {x:.2f}, {y:.2f}, {z:.2f})\n"
        loads += f"Actor_SetYaw('player0', {float(start.get('heading', 0)):.1f})\n"
    return f"""-- {asset_id}: written by mercfoundry from missions/{asset_id}.json "objectives".
tFoundryObjectives = {{    -- main objectives, in order
{main_table}
}}
tFoundryBonus = {{         -- bonus objectives, active from the start
{bonus_table}
}}
tFoundryActive = {{}}       -- started objectives by name
iFoundryStep = 0
iFoundryFlatBonus = {flat}
iFoundryEarned = 0

function ScriptInit()
Utility_LoadUtilityScript('mission')
Utility_LoadUtilityScript('mission_objective')
Utility_LoadUtilityScript('mission_objectiveverb')
Utility_LoadUtilityScript('util_misc')
mission_GenericInit()
{loads}end

function post_InitialNotifications()   -- after the contract mail
for i, o in ipairs(tFoundryBonus) do foundry_Start(o) end
foundry_NextObjective()
end

function foundry_NextObjective()
iFoundryStep = iFoundryStep + 1
local o = tFoundryObjectives[iFoundryStep]
if not o then
foundry_Finish()
return
end
foundry_Start(o)
end

-- Main objectives are primary (vIdx 1); bonus ones secondary (vIdx 2), as retail's.
function foundry_Kind(o)
if o.bonus then return 'BON_OBJ_', 2, 'OBJECTIVE_TYPE_SECONDARY', 'TARGET_TYPE_OBJECTIVE_SECONDARY' end
return 'PRI_OBJ_', 1, 'OBJECTIVE_TYPE_PRIMARY', 'TARGET_TYPE_OBJECTIVE_PRIMARY'
end

function foundry_Start(o)
o.text = Utility_GetStringById(o.key)
o.down = 0
tFoundryActive[o.name] = o
local sMsg, iIdx, sObjType, sTargetType = foundry_Kind(o)
if o.kind == 'destroy' then
mission_ObjectiveDestroy{{ callbackFunc = foundry_TargetDown, objectiveName = o.name, objectiveDesc = o.text,
                          actorName = o.targets, objectiveType = sObjType, targetType = sTargetType }}
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'ADD', vIdx = iIdx,
                  tCounter = {{ vCurr = 0, vLim = table.getn(o.targets) }}, sObjective = o.name }}
else
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'ADD', vIdx = iIdx, sObjective = o.name }}
if o.x then   -- a marker on the zone, as retail location objectives do (sw_sk2)
Objective_SetShortDescription(o.name, o.text)
Objective_SetType(o.name, sObjType)
Objective_SetTargetVector(o.name, o.zone, o.text, sTargetType, 'location',
                          o.x, o.y, o.z, true, true, false)   -- GPS and map; not in the world (as sw_sk2)
end
Event_ActorInBoundary('foundry_Arrived', 'b', 'player0', o.zone, true)
end
end

function foundry_Done(o)
o.done = true
local sMsg, iIdx = foundry_Kind(o)
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'CMPL', vIdx = iIdx, sObjective = o.name }}
mission_RemoveObjective(o.name)
if o.bonus then
iFoundryEarned = iFoundryEarned + o.bonus
else
foundry_NextObjective()
end
end

function foundry_TargetDown(sActor, bComplete, sObjName)
local o = tFoundryActive[sObjName]
if not o or o.done then return end
local iAll = table.getn(o.targets)
o.down = o.down + 1
if o.down >= iAll then
foundry_Done(o)
else
local sMsg, iIdx = foundry_Kind(o)
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'UPD_POS', vIdx = iIdx,
                  tCounter = {{ vCurr = o.down, vLim = iAll }}, sObjective = o.name }}
end
end

function foundry_Arrived(playerActor, boundaryName)
-- the zone's started objectives (main or bonus) that aren't done yet
for sName, o in pairs(tFoundryActive) do
if o.kind == 'goto' and o.zone == boundaryName and not o.done then foundry_Done(o) end
end
end

-- The main objectives are done: bonus objectives still open have failed, and
-- the contract pays the flat bonus plus the rewards earned.
function foundry_Finish()
for i, o in ipairs(tFoundryBonus) do
if not o.done then
util_ObjMsgPrint{{ sMsg = o.text, sType = 'BON_OBJ_FAIL', vIdx = 2, sObjective = o.name }}
mission_RemoveObjective(o.name)
end
end
if table.getn(tFoundryBonus) > 0 then
Utility_WriteMissionData( {{ fBonusFee = iFoundryFlatBonus + iFoundryEarned }} )
end
mission_MissionComplete()
end
"""


CONTRACTS_SCRIPT = "foundry_contracts"
_HOOK_START = "Utility_LoadUtilityScript('foundry_contracts') foundry_ReturnFromContract()"
_HOOK_SPAWN = "foundry_SetupContracts()"


def patch_global(global_text):
    """global with two calls into foundry_contracts in LoadNoMissionState
    (free roam, no contract accepted): first thing, to settle a side contract
    that just ended (before the function reads and erases
    JustCompletedStandardMission), and after the HQ bouncers spawn, to spawn
    the contacts."""
    if _HOOK_START in global_text:
        return global_text
    head = "function LoadNoMissionState()"
    start = global_text.find(head)
    at = global_text.find("SpawnBouncers(TRUE)", start)
    if start < 0 or at < 0:
        raise ValueError("global's LoadNoMissionState has no SpawnBouncers(TRUE) to hook after")
    nl = "\r\n" if "\r\n" in global_text else "\n"
    end = at + len("SpawnBouncers(TRUE)")
    text = global_text[:end] + nl + _HOOK_SPAWN + global_text[end:]
    body = start + len(head)
    return text[:body] + nl + _HOOK_START + text[body:]


LAYERS_SCRIPT = "foundry_layers"
_HOOK_LAYERS = "Utility_LoadUtilityScript('foundry_layers') foundry_LoadMapLayers()"
FREE_ROAM_MAPS = ("sw", "nw")         # SkipTo_Map's values; the opening map has no free roam


def layers_script(layers):
    """Lua for foundry_layers: load each mod layer marked "load": "map" when
    free roam starts on its map. A layer is loaded again only if it's gone:
    when one of its named objects no longer exists (a mission reset the
    map), or, for layers without named objects, once per script state."""
    out = ["-- Generated by Mercenaries Foundry: mod layers that load with their map in free roam.",
           "tFoundryLayers = {"]
    for l in layers:
        out.append("{ map = %s, layer = %s, check = %s }," % (
            _lua(l["map"]), _lua(l["layer"]), _lua(l["check"]) if l.get("check") else "nil"))
    out += ["}",
            "if not g_foundry_layers then g_foundry_layers = {} end",
            "function foundry_LoadMapLayers()",
            "local map = Utility_ReadStringFromScribbleMemory('SkipTo_Map')",
            "for i, l in tFoundryLayers do",
            "if l.map == map then",
            "local loaded = g_foundry_layers[l.layer]",
            "if l.check then loaded = Utility_DoesActorExist(l.check) end",
            "if not loaded then",
            "Utility_ReadLevelFile(l.layer)",
            "g_foundry_layers[l.layer] = 1",
            "end",
            "end",
            "end",
            "end"]
    return "\n".join(out) + "\n"


def patch_global_layers(global_text):
    """global with a call into foundry_layers after the HQ bouncers spawn in
    LoadNoMissionState (free roam starting)."""
    if _HOOK_LAYERS in global_text:
        return global_text
    start = global_text.find("function LoadNoMissionState()")
    at = global_text.find("SpawnBouncers(TRUE)", start)
    if start < 0 or at < 0:
        raise ValueError("global's LoadNoMissionState has no SpawnBouncers(TRUE) to hook after")
    nl = "\r\n" if "\r\n" in global_text else "\n"
    end = at + len("SpawnBouncers(TRUE)")
    return global_text[:end] + nl + _HOOK_LAYERS + global_text[end:]


FACTION_NAMES = {"allies": "Allied", "china": "Chinese", "mafia": "Mafia", "sk": "South Korean"}


def contract(asset_id, doc, title_key, description_key):
    """The contact that offers a mod mission at its faction's HQ, and the
    strings it needs ({key: text}; they go in the always-loaded actions and
    standardmissions text, where the HQ bouncers' own strings are)."""
    map_, faction, number = parse_id(asset_id)
    c = doc.get("contact") if isinstance(doc.get("contact"), dict) else {}
    title = doc.get("title", asset_id)
    prompt_key = f"actions.foundry.{asset_id}"
    name_key = f"standardMission.foundry.{asset_id}.tgtCaption"
    desc_key = f"standardMission.foundry.{asset_id}.shortDesc"
    strings = {prompt_key: c.get("prompt", f"Discuss contract: {title}"),
               name_key: c.get("name", f"{FACTION_NAMES[faction]} contact"),
               desc_key: c.get("name", f"{FACTION_NAMES[faction]} contact")}
    return {"id": asset_id, "map": map_, "faction": faction, "number": number,
            "repeatable": bool(doc.get("repeatable", False)),
            "template": c.get("template", f"template_{faction}_bouncer"),
            "location": c.get("location", f"loc_hq-{faction}-bouncer"),
            "offset": [float(v) for v in c["offset"]] if "offset" in c else None,
            "yaw": float(c.get("yaw", 0.0)),
            "prompt_key": prompt_key, "name_key": name_key, "desc_key": desc_key,
            "target_type": "TARGET_TYPE_HQ_" + faction.upper(), "blip": "hq" + faction,
            "title_key": title_key, "description_key": description_key}, strings


def _lua(s):
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def contracts_script(contracts):
    """Lua for foundry_contracts: spawn each contact on its map and start its
    mission (DebugSkipToMission, as the console skip does) when the player
    uses the contact and says yes."""
    out = ["-- Generated by Mercenaries Foundry: contacts at faction HQs that offer mod contracts.",
           "tFoundryContracts = {"]
    placed = {}
    for c in contracts:
        if c["offset"] is None:
            # Default spots: a row beside the bouncer, 2.5 m apart, so
            # several mods' contacts at one HQ don't stand in each other.
            n = placed.get(c["location"], 0)
            placed[c["location"]] = n + 1
            dx, dy, dz = 3.0 + 2.5 * n, 0.0, 0.0
        else:
            dx, dy, dz = c["offset"]
        out.append("{ id = %s, map = %s, faction = %s, number = %d, repeatable = %s, template = %s, "
                   "location = %s, dx = %g, dy = %g, dz = %g, yaw = %g, prompt = %s, name = %s, desc = %s, "
                   "targettype = %s, blip = %s }," % (
                       _lua(c["id"]), _lua(c["map"]), _lua(c["faction"]), c["number"],
                       "TRUE" if c["repeatable"] else "FALSE", _lua(c["template"]),
                       _lua(c["location"]), dx, dy, dz, c["yaw"], _lua(c["prompt_key"]), _lua(c["name_key"]),
                       _lua(c["desc_key"]), _lua(c["target_type"]), _lua(c["blip"])))
    out.append("}")
    out += [
        "Utility_LoadUtilityScript('util_objectives')",
        "-- Campaign values a side contract must not change: the faction's arc",
        "-- position and its completed-mission counters (mission_MissionComplete2).",
        "function foundry_Kept(f) return { 'current_' .. f .. '_mission', 'total_missions_completed' .. f, "
        "'num_std_missions_cmpl_' .. f } end",
        "function foundry_ContactName(c) return 'foundry_contact_' .. c.id end",
        "function foundry_IsDone(c) return Utility_ReadNumberFromScribbleMemory('foundry_done_' .. c.id) end",
        "-- First thing in LoadNoMissionState: a side contract just ended (done,",
        "-- failed or cancelled). Put the campaign back and note a completion.",
        "function foundry_ReturnFromContract()",
        "local id = Utility_ReadStringFromScribbleMemory('foundry_active_contract')",
        "if not id then return end",
        "-- Retrying after a failure: Retry() has queued the same mission (SkipTo_*)",
        "-- and mission_GenericInit will read the arc position again, so the",
        "-- contract isn't over yet.",
        "if Utility_ReadNumberFromScribbleMemory('SkipTo_MissionNumber') then return end",
        "local f = Utility_ReadStringFromScribbleMemory('foundry_active_faction')",
        "for i, key in foundry_Kept(f) do",
        "local v = Utility_ReadNumberFromScribbleMemory('foundry_kept_' .. key)",
        "if v then Utility_WriteNumberToScribbleMemory(key, v) else Utility_EraseValueFromScribbleMemory(key) end",
        "Utility_EraseValueFromScribbleMemory('foundry_kept_' .. key)",
        "end",
        "if Utility_ReadStringFromScribbleMemory('JustCompletedStandardMission') == id then",
        "Utility_WriteNumberToScribbleMemory('foundry_done_' .. id, 1)",
        "Utility_EraseValueFromScribbleMemory('JustCompletedStandardMission')",
        "end",
        "Utility_EraseValueFromScribbleMemory('foundry_active_contract')",
        "Utility_EraseValueFromScribbleMemory('foundry_active_faction')",
        "end",
        "function foundry_SetupContracts()",
        "local map = Utility_ReadStringFromScribbleMemory('SkipTo_Map')",
        "for i, c in tFoundryContracts do",
        "local name = foundry_ContactName(c)",
        "if c.map == map and foundry_IsDone(c) and not c.repeatable then",
        "if Utility_DoesActorExist(name) then Objective_Remove(name) Actor_Remove(name) end",
        "elseif c.map == map then",
        "if not Utility_DoesActorExist(name) then",
        "local x, y, z = Location_GetPosition(c.location)",
        "-- face the way the location faces, as util_Mission_SpawnAtLocation does",
        "Mission_Spawn(c.template, name, x + c.dx, y + c.dy, z + c.dz, -Location_GetYaw(c.location) + c.yaw)",
        "-- marked like the HQ bouncers: ring, name above, HQ blip",
        "util_SetupSingleTargetActorObjective(name, Utility_GetStringById(c.desc), '', 'OBJECTIVE_TYPE_PRIMARY', "
        "name, Utility_GetStringById(c.name), c.targettype, TRUE, TRUE, TRUE, c.blip)",
        "end",
        "Actor_EnableScriptedUse(name, TRUE, c.prompt)",
        "Event_ActorUsed('foundry_Use_' .. c.id, 'b', name)",
        "end",
        "end",
        "end"]
    for c in contracts:
        out += [
            f"function foundry_Use_{c['id']}()",
            f"Utility_QuestionBox(Utility_GetStringById({_lua(c['title_key'])}), "
            f"Utility_GetStringById({_lua(c['description_key'])}), true, "
            f"'foundry_Start_{c['id']}', 'foundry_SetupContracts')",
            "end",
            f"function foundry_Start_{c['id']}()",
            f"local f = {_lua(c['faction'])}",
            "for i, key in foundry_Kept(f) do",
            "local v = Utility_ReadNumberFromScribbleMemory(key)",
            "if v then Utility_WriteNumberToScribbleMemory('foundry_kept_' .. key, v) end",
            "end",
            f"Utility_WriteStringToScribbleMemory('foundry_active_contract', {_lua(c['id'])})",
            "Utility_WriteStringToScribbleMemory('foundry_active_faction', f)",
            f"Objective_Remove(foundry_ContactName(tFoundryContracts[{contracts.index(c) + 1}]))",
            f"DebugSkipToMission(f, {c['number']})",
            "end"]
    return "\n".join(out) + "\n"


def section_value(ini_text, section, key):
    lines, secs = _sections(ini_text)
    for name, a, b in secs:
        if name.lower() == section.lower():
            for line in lines[a + 1:b]:
                parts = line.split(None, 1)
                if parts and parts[0].lower() == key.lower():
                    return parts[1].strip() if len(parts) > 1 else ""
    return None


def _sections(text):
    """[(name, start line index, end line index)] of an ini's sections."""
    lines = text.split("\n")
    out = []
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            if out:
                out[-1][2] = i
            out.append([s[1:-1], i, len(lines)])
    return lines, out


def add_to_mission_ini(text, asset_id, overrides=None):
    """mission.ini text with a section for the mission and its MissionName.
    The section copies the faction's first mission on the map."""
    map_, faction, number = parse_id(asset_id)
    lines, secs = _sections(text)
    pattern = re.compile(r"^%s_%s(\d+)$" % (map_, faction), re.IGNORECASE)
    same = [s for s in secs if pattern.match(s[0])]
    if not same:
        raise ValueError(f"mission.ini has no {map_}_{faction} missions to copy")
    prefix = re.match(r"^(.*?)(\d+)$", same[0][0]).group(1)
    name = f"{prefix}{number}"
    if any(s[0].lower() == name.lower() for s in secs):
        raise ValueError(f"mission.ini already has [{name}]")
    base = same[0]
    body = lines[base[1] + 1:base[2]]
    while body and not body[-1].strip():
        body = body[:-1]
    fields = []
    for line in body:
        parts = line.split(None, 1)
        if parts:
            fields.append([parts[0], parts[1].strip() if len(parts) > 1 else ""])
    overrides = dict(overrides or {})
    overrides.setdefault("Number", number)
    for key, value in overrides.items():
        for f in fields:
            if f[0].lower() == key.lower():
                f[1] = str(value)
                break
        else:
            fields.append([key, str(value)])
    section = [f"[{name}]"] + [f"{k}\t\t\t{v}" for k, v in fields] + ["", ""]
    # the new section goes after the last section of the same faction and map
    insert_at = same[-1][2]
    while insert_at > 0 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines[insert_at:insert_at] = [""] + section[:-1]
    # MissionName after the last one of this faction and map
    names = [i for i, l in enumerate(lines) if l.strip().lower().startswith("missionname ")
             and pattern.match(l.split(None, 1)[1].strip())]
    lines.insert(names[-1] + 1, f"MissionName {name}")
    return "\n".join(lines), name
