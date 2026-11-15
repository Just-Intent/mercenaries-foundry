"""Mod missions offered by a faction's own HQ bouncer, at a chosen point in
that faction's contract sequence on a map (a mission file's "hq").

How the retail campaign counts contracts (util_misc, bouncer, global,
mission, gameflow scripts):

  * 'current_<faction>_mission' is the faction's position in its arc
    (util_GetMissionArcTable), not an asset number. Campaign rules read the
    position: chapters (util_GetLastMissionNumberInChapterArc,
    util_GetFirstMissionsForChapter, global's SetupChapter), face-card
    missions at 3 and 6 (util_IsFaceCardMission), allied aces
    (util_IsAceMission), the final mission (util_IsFinalMission), the
    playable intro (util_IsPlayableIntro) and gameflow's layer rules.
  * Data, mail, layers and card intel use the asset ID (sw_china4), from
    util_MissionSeqNumToMissionAssetNum.

So a number inserted into the middle of an arc shifts every later retail
position and breaks those rules; only appending is safe. An insertion
instead runs as a sub-step at a retail position p, which never changes:
while the faction's own bouncer runs it, scribble values (foundry_ins_*)
make util_MissionSeqNumToMissionAssetNum return the mod's asset number for
(faction, p), and the position rules answer "no" for it. The campaign
values are saved and put back afterwards, as for a side contract
(foundry_contracts' foundry_Kept / foundry_ReturnFromContract).
"""

import re

MAP_CHAPTERS = {"sw": (1, 2), "nw": (3, 4)}     # bouncer.lua ShouldBouncerBeSpawned: sw has no bouncers in 3/4


def first_in_chapter(faction, chapter):
    """util_GetFirstMissionsForChapter (util_misc): the arc position a
    faction starts a chapter at."""
    if chapter == 1:
        return 2 if faction == "allies" else 1        # allies 1 is the playable intro
    if chapter == 3:
        return 1
    return 4                                          # chapters 2 and 4: everyone


def last_in_chapter(faction, chapter):
    """util_GetLastMissionNumberInChapterArc (util_misc): the bouncer's last
    position in a chapter (an allied ace follows at last + 1)."""
    if chapter in (1, 3):
        return 2 if faction == "allies" else 3
    if chapter == 2:
        return 5 if faction == "allies" else 6
    return 4 if faction == "allies" else 6


def chapter_of(map_, faction, position):
    """The chapter a retail contract at an arc position belongs to: the
    map's first chapter up to 3, else its second (global's SetupChapter:
    'iMission > 3'). Allied aces at 3 and 6 end their chapter."""
    first, second = MAP_CHAPTERS[map_]
    return first if position <= 3 else second


def plan(asset_id, doc, parse_id):
    """The insertion for a mission file's "hq", or None:

      {"after": <arc position of the retail contract it follows>, "chapter": <optional>}

    after 0 is before the faction's first contract on the map. The chapter
    is the one of the contract it follows; after an allied ace (whose
    completion starts the next chapter) the next one. Returns
    {"id", "map", "faction", "number", "after", "pos", "chapter", "notes"}.
    Raises ValueError for a point the HQ never reaches."""
    hq = doc.get("hq")
    if hq is None or hq is False:
        return None
    if not isinstance(hq, dict):
        raise ValueError('"hq" is {"after": <the arc position of the contract it follows>}')
    map_, faction, number = parse_id(asset_id)
    try:
        after = int(hq.get("after"))
    except (TypeError, ValueError):
        raise ValueError('"hq": "after" is the arc position of the retail contract it follows (0: before the first)') from None
    if after < 0:
        raise ValueError('"hq": "after" is 0 or more')
    pos = after + 1
    chapters = MAP_CHAPTERS[map_]
    if map_ == "sw" and faction == "allies" and pos == 1:
        raise ValueError('"hq": allies contract 1 on sw is the playable intro, which has no HQ bouncer; '
                         'use "after": 1 or later')
    if hq.get("chapter") is not None:
        try:
            chapter = int(hq["chapter"])
        except (TypeError, ValueError):
            raise ValueError('"hq": "chapter" is a number') from None
        if chapter not in chapters:
            raise ValueError(f'"hq": {map_} has chapters {chapters[0]} and {chapters[1]}, not {chapter}')
    else:
        chapter = chapters[0] if after == 0 else chapter_of(map_, faction, after)
        if chapter == chapters[0] and pos > last_in_chapter(faction, chapter) + 1:
            chapter = chapters[1]             # after an allied ace: the next chapter starts with it done
    lo, hi = first_in_chapter(faction, chapter), last_in_chapter(faction, chapter) + 1
    if not lo <= pos <= hi:
        raise ValueError(f'"hq": in chapter {chapter} the {faction} HQ is at contract {lo} to {hi}, so "after" '
                         f'is {lo - 1} to {hi - 1} there (not {after})')
    notes = []
    if pos == hi and faction == "allies":
        notes.append(f"it is offered beside the ace bouncer (contract {pos}); taking the ace first ends "
                     f"chapter {chapter} and skips it")
    elif pos == hi:
        notes.append(f"it is offered after the chapter's last {faction} contract; once the chapter "
                     f"ends (the allied ace) it is skipped")
    return {"id": asset_id, "map": map_, "faction": faction, "number": number, "after": after,
            "pos": pos, "chapter": chapter, "notes": notes}


def ini_chapter(chapter):
    """mission.ini's Chapter value (a string key), as retail sections have."""
    return f"MissionInfo.Chapter.Chapter{chapter}"


def _lua(s):
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def lua_block(insertions):
    """Lua for foundry_contracts: which insertion the faction's own bouncer
    offers, and starting it. Runs in global's script state (foundry_contracts
    is loaded at the start of LoadNoMissionState, before the bouncers
    spawn), where the bouncer script's functions are."""
    out = ["-- HQ sequence: mod missions the faction's own bouncer offers at an arc position.",
           "tFoundryHq = {"]
    for c in insertions:
        out.append("{ id = %s, map = %s, faction = %s, number = %d, chapter = %d, pos = %d, title = %s, desc = %s },"
                   % (_lua(c["id"]), _lua(c["map"]), _lua(c["faction"]), c["number"], c["chapter"], c["pos"],
                      _lua(c["title_key"]), _lua(c["description_key"])))
    out += [
        "}",
        "-- The insertion pending for a faction: the first not done at this map,",
        "-- chapter and arc position (current_<faction>_mission, never changed).",
        "function foundry_HqPending(f)",
        "local map = Utility_ReadStringFromScribbleMemory('SkipTo_Map')",
        "local chapter = Utility_ReadNumberFromScribbleMemory('chapter')",
        "local pos = Utility_ReadNumberFromScribbleMemory('current_' .. f .. '_mission')",
        "for i, c in ipairs(tFoundryHq) do",
        "if c.map == map and c.faction == f and c.chapter == chapter and c.pos == pos",
        "and not Utility_ReadNumberFromScribbleMemory('foundry_done_' .. c.id) then return c end",
        "end",
        "return nil",
        "end",
        "-- bouncer's BouncerUsed calls this first: with an insertion pending, ask",
        "-- yes/no instead of the briefing room (the briefing needs retail data).",
        "function foundry_HqBouncerUsed(aActor)",
        "local f, bAce = DetermineBouncerFaction(aActor)",
        "if bAce or not f then return false end",
        "local c = foundry_HqPending(f)",
        "if not c then return false end",
        "g_foundry_hq = c",
        "Utility_QuestionBox(Utility_GetStringById(c.title), Utility_GetStringById(c.desc), true, "
        "'foundry_HqYes', 'foundry_HqNo')",
        "return true",
        "end",
        "function foundry_HqNo()",
        "local c = g_foundry_hq",
        "g_foundry_hq = nil",
        "if not c then return end",
        "-- the bouncer again (a used event fires once)",
        "local b = bouncers[c.faction]",
        "Actor_EnableScriptedUse(b.actorName, TRUE, b.useCaptionStrID)",
        "b.usedHandle = Event_ActorUsed('BouncerUsed', 'b', b.actorName)",
        "end",
        "-- Yes: as Briefing_Exit starts a contract (SkipTo_* at the arc position,",
        "-- mission_accepted, the HQ respawn), with the insertion flagged so the",
        "-- position's asset is the mod's, and the campaign values kept.",
        "function foundry_HqYes()",
        "local c = g_foundry_hq",
        "g_foundry_hq = nil",
        "if not c then return end",
        "local f = c.faction",
        "local pos = Utility_ReadNumberFromScribbleMemory('current_' .. f .. '_mission')",
        "for i, key in foundry_Kept(f) do",
        "local v = Utility_ReadNumberFromScribbleMemory(key)",
        "if v then Utility_WriteNumberToScribbleMemory('foundry_kept_' .. key, v) end",
        "end",
        "Utility_WriteStringToScribbleMemory('foundry_active_contract', c.id)",
        "Utility_WriteStringToScribbleMemory('foundry_active_faction', f)",
        "Utility_WriteStringToScribbleMemory('foundry_ins_active', c.id)",
        "Utility_WriteStringToScribbleMemory('foundry_ins_faction', f)",
        "Utility_WriteNumberToScribbleMemory('foundry_ins_pos', pos)",
        "Utility_WriteNumberToScribbleMemory('foundry_ins_num', c.number)",
        "Utility_WriteStringToScribbleMemory('current_faction', f)",
        "Utility_WriteNumberToScribbleMemory('mission_accepted', 1)",
        "Utility_WriteStringToScribbleMemory('PlayerStartLocationName', 'loc_hq-' .. f .. '-respawn')",
        "Utility_WriteStringToScribbleMemory('SkipTo_Faction', f)",
        "Utility_WriteNumberToScribbleMemory('SkipTo_MissionNumber', pos)",
        "for k, b in pairs(bouncers) do",
        "Objective_Remove(b.actorName)",
        "Actor_EnableScriptedUse(b.actorName, FALSE)",
        "end",
        "Utility_ResetToBaseLayerOnly()",
        "end"]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- retail patches
# Text patches into the retail scripts, applied by the build when a mod
# mission uses "hq". Each is idempotent (a marker it adds) and raises
# ValueError naming its anchor if the script differs from retail.

_INS_HELPER = ("-- Mercenaries Foundry: an HQ-sequence insertion running for this faction at this arc position.\n"
               "function foundry_InsHere(faction, seqnum)\n"
               "return Utility_ReadStringFromScribbleMemory('foundry_ins_active') ~= nil\n"
               "and Utility_ReadStringFromScribbleMemory('foundry_ins_faction') == faction\n"
               "and Utility_ReadNumberFromScribbleMemory('foundry_ins_pos') == seqnum\n"
               "end\n")
_NO_RULE = "if foundry_InsHere(faction, mission) then return false end"
_ASSET = "if foundry_InsHere(faction, seqnum) then return Utility_ReadNumberFromScribbleMemory('foundry_ins_num') end"

# (function header, line put first in its body)
UTIL_MISC_HEADERS = (
    ("function util_IsPlayableIntro(quadrant, faction, mission)", _NO_RULE),
    ("function util_IsAceMission(quadrant, faction, mission)", _NO_RULE),
    ("function util_IsFaceCardMission(faction, mission)", _NO_RULE),
    ("function util_IsFinalMission(quadrant, faction, mission)", _NO_RULE),
    ("function util_MissionSeqNumToMissionAssetNum(quadrant, faction, seqnum)", _ASSET),
)


def _nl(text):
    return "\r\n" if "\r\n" in text else "\n"


def _after_header(text, header, line, script):
    at = text.find(header)
    if at < 0:
        raise ValueError(f"{script} has no {header!r} to patch (HQ sequence)")
    end = at + len(header)
    nl = _nl(text)
    return text[:end] + nl + line + text[end:]


def patch_util_misc(text):
    """util_misc: while an insertion runs, util_MissionSeqNumToMissionAssetNum
    gives its asset number for (faction, position), so LoadMission (global),
    mission_GenericInit (mission) and the briefing use the mod's script,
    mission.ini section and layer; and the position rules (ace, face card,
    final, playable intro) say no for it."""
    if "function foundry_InsHere(" in text:
        return text
    for header, line in UTIL_MISC_HEADERS:
        text = _after_header(text, header, line, "util_misc")
    first = UTIL_MISC_HEADERS[0][0]
    at = text.find(first)
    return text[:at] + _INS_HELPER.replace("\n", _nl(text)) + text[at:]


_SPAWN_OLD = "if current_mission > util_GetLastMissionNumberInChapterArc(bouncerID, chapter) then"
_SPAWN_NEW = ("if current_mission > util_GetLastMissionNumberInChapterArc(bouncerID, chapter) "
              "and not (foundry_HqPending and foundry_HqPending(bouncerID)) then")
_USED_HEAD = "function BouncerUsed(ThisActor)"
_USED_LINE = "if foundry_HqBouncerUsed and foundry_HqBouncerUsed(ThisActor) then return end"


def patch_bouncer(text):
    """bouncer: a faction's bouncer also stands past the chapter's last
    contract when an insertion is pending there (ShouldBouncerBeSpawned; the
    opening-order gates after it are kept), and using it with one pending
    asks yes/no for it (BouncerUsed)."""
    if _USED_LINE in text:
        return text
    head = text.find("function ShouldBouncerBeSpawned(")
    at = text.find(_SPAWN_OLD, max(head, 0))
    if head < 0 or at < 0:
        raise ValueError(f"bouncer's ShouldBouncerBeSpawned has no {_SPAWN_OLD!r} to patch (HQ sequence)")
    text = text[:at] + _SPAWN_NEW + text[at + len(_SPAWN_OLD):]
    return _after_header(text, _USED_HEAD, _USED_LINE, "bouncer")


_CHAPTER_ANCHOR = "tFirstMissions = util_GetFirstMissionsForChapter(chapter)"
_CHAPTER_LINE = ("if (Utility_ReadStringFromScribbleMemory('foundry_ins_active') "
                 "or Utility_ReadStringFromScribbleMemory('foundry_active_contract')) "
                 "and Utility_ReadNumberFromScribbleMemory('chapter') then "
                 "chapter = Utility_ReadNumberFromScribbleMemory('chapter') end   -- Mercenaries Foundry")


def patch_global_chapter(text):
    """global's SetupChapter: while an insertion or side contract runs, keep
    the stored chapter. Otherwise its arc position decides (> 3: chapter 2
    or 4), and a changed chapter resets the other factions' positions."""
    if _CHAPTER_LINE in text:
        return text
    head = text.find("function SetupChapter(")
    at = text.find(_CHAPTER_ANCHOR, max(head, 0))
    if head < 0 or at < 0:
        raise ValueError(f"global's SetupChapter has no {_CHAPTER_ANCHOR!r} to patch (HQ sequence)")
    return text[:at] + _CHAPTER_LINE + _nl(text) + text[at:]


_FLOW_OLD = ("gameflow_EvaluateFactionCondition(sConditionType, tConditionData, tCurrentMissions, "
             "bInMissionState, sCurrentFaction, sQuadrant, iChapter)")
_FLOW_NEW = ("gameflow_EvaluateFactionCondition(sConditionType, tConditionData, tCurrentMissions, "
             "(Utility_ReadStringFromScribbleMemory('foundry_ins_faction') ~= sConditionType and bInMissionState or nil), "
             "sCurrentFaction, sQuadrant, iChapter)")


def patch_gameflow(text):
    """gameflow's gameflow_ShouldGameStateApply: for the faction running an
    insertion, its layer rules see free roam, so the world stays as it is
    between its contracts (retail contract p's in-mission state would drop
    the layers that last until p starts). Other factions and the
    inMissionState rules see a mission, as in any contract."""
    if _FLOW_NEW in text:
        return text
    head = text.find("function gameflow_ShouldGameStateApply(")
    at = text.find(_FLOW_OLD, max(head, 0))
    if head < 0 or at < 0:
        raise ValueError(f"gameflow's gameflow_ShouldGameStateApply has no {_FLOW_OLD!r} to patch (HQ sequence)")
    return text[:at] + _FLOW_NEW + text[at + len(_FLOW_OLD):]


PATCHES = (("util_misc", patch_util_misc), ("bouncer", patch_bouncer),
           ("global", patch_global_chapter), ("gameflow", patch_gameflow))


def retail_contracts(arc_numbers, ini_text, map_, faction, strings=None):
    """The faction's retail contracts on a map in arc order, for the editor's
    "after which contract": [{"pos", "number", "chapter", "title"}]."""
    out = []
    lower = {k.lower(): v for k, v in (strings or {}).items()}
    for pos, n in enumerate(arc_numbers, 1):
        section = f"{map_}_{faction}{n}"              # sections match case-insensitively
        title = None
        if ini_text:
            key = _section_value(ini_text, section, "MissionTitleDbKey")
            title = lower.get(key.lower()) if key else None
        out.append({"pos": pos, "number": n, "chapter": chapter_of(map_, faction, pos), "title": title})
    return out


def _section_value(ini_text, section, key):
    m = re.search(r"^\[%s\]\s*$(.*?)(?=^\[|\Z)" % re.escape(section), ini_text, re.M | re.S | re.I)
    if not m:
        return None
    k = re.search(r"^%s[ \t]+(\S+)" % re.escape(key), m.group(1), re.M | re.I)
    return k.group(1) if k else None
