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
    if "title" in doc:
        strings[f"MissionInfo.MissionTitle.{key}"] = doc["title"]
        ini["MissionTitleDbKey"] = f"MissionInfo.MissionTitle.{key}"
    if "description" in doc:
        strings[f"MissionInfo.Description.{key}"] = doc["description"]
        ini["ShortDescriptionDbKey"] = f"MissionInfo.Description.{key}"
    mail = None
    if doc.get("mail"):
        m = doc["mail"]
        mail_id = f"{asset_id}_initial"
        subject, body = f"mail.{mail_id}.subject", f"mail.{mail_id}.body"
        strings[subject] = m.get("subject", doc.get("title", asset_id))
        strings[body] = m.get("body", "")
        mail = (mail_id, m.get("sender", "mail.senders.techgal"), subject, body)
    return strings, ini, mail


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
            "offset": [float(v) for v in c.get("offset", [3.0, 0.0, 0.0])],
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
    for c in contracts:
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
