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
