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

  {"hq": {"after": 2}, "ini": {"ContractFee": 50000, "MissionTitleDbKey": "..."}}

adds the number at the end of the faction's arc on that map and a
mission.ini section copied from the faction's first mission on the map with
"ini" applied. "hq" has the faction's own HQ bouncer offer it after that
many of its retail contracts (see hqsequence).  The mod supplies the script
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


def add_to_arc(script_text, faction, map_, number):
    """util_misc text with the number at the end of the arc (unchanged if
    it's there). Always at the end: campaign rules count arc positions
    (chapters, face cards, aces, the final mission; see hqsequence), so a
    number in the middle would shift every later retail contract. The HQ
    offers a mission inside the sequence with the mission file's "hq"."""
    m = _arc_match(script_text, faction, map_)
    nums = [int(v) for v in re.findall(r"\d+", m.group(2))]
    if number in nums:
        return script_text
    nums.append(number)
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


def done_mail(asset_id, doc, key):
    """(mail id, sender, subject key, body key) of a mission's follow-up mail
    ("done_mail": {"subject", "body"}, posted when the contract completes), or
    None. Its strings go in the always-loaded missioninfo text: the mail is
    read later, when the mission's own text has gone."""
    m = doc.get("done_mail")
    if not isinstance(m, dict) or not (m.get("subject") or m.get("body")):
        return None
    return (f"{asset_id}_done", m.get("sender", (doc.get("mail") or {}).get("sender", "mail.senders.techgal")),
            f"MissionInfo.FoundryMail.{key}.Subject", f"MissionInfo.FoundryMail.{key}.Body")


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
        if o.get("type") in ("escort", "deliver", "subdue", "use"):
            strings[escort_lost_key(asset_id, i)] = o.get("lost") or FAIL_TEXT[o["type"]]
        if o.get("type") == "use":
            strings[use_prompt_key(asset_id, i)] = o.get("prompt") or "Use"
    for k, c in enumerate(doc.get("fail") or [], 1):
        strings[fail_key(asset_id, k)] = c.get("text") or FAIL_TEXT.get(c.get("type"), "The mission failed.")
    for k, line in enumerate(doc.get("radio") or [], 1):
        if line.get("text"):
            # a voiced line's subtitle (vo.<bank>.<cue>, as retail's), or a line's text
            key = f"vo.{line['sound']}" if line.get("sound") else radio_key(asset_id, k)
            strings[key] = line["text"]
    done = done_mail(asset_id, doc, key)
    if done:
        strings[done[2]] = (doc.get("done_mail") or {}).get("subject") or f"Re: {doc.get('title', asset_id)}"
        strings[done[3]] = (doc.get("done_mail") or {}).get("body") or ""
    mail = None
    if doc.get("mail"):
        m = doc["mail"]
        mail_id = f"{asset_id}_initial"
        subject, body = f"mail.{mail_id}.subject", f"mail.{mail_id}.body"
        strings[subject] = m.get("subject", doc.get("title", asset_id))
        strings[body] = m.get("body", "")
        mail = (mail_id, m.get("sender", "mail.senders.techgal"), subject, body)
    return strings, ini, mail


def fail_key(asset_id, k):
    """The string key of a generated fail condition's message: sw.allies9.fail.foundry1."""
    map_, faction, number = parse_id(asset_id)
    return f"{map_}.{faction}{number}.fail.foundry{k}"


def use_prompt_key(asset_id, i):
    """The prompt a "use" objective puts on its objects (actionCaption, a key
    in the mission's text, as nw_allies8's 'nw_allies8.act.missile')."""
    map_, faction, number = parse_id(asset_id)
    return f"{map_}.{faction}{number}.act.foundry{i}"


def escort_lost_key(asset_id, i):
    """The message when everyone an escort objective protects is dead."""
    return objective_key(asset_id, i) + ".lost"


def objective_key(asset_id, i):
    """The string key of a generated objective's text: sw.allies9.obj.foundry1."""
    map_, faction, number = parse_id(asset_id)
    return f"{map_}.{faction}{number}.obj.foundry{i}"


def _lua_str(s):
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


OBJECTIVE_TYPES = ("destroy", "goto", "escort", "deliver", "subdue", "hold", "use", "verify", "fast")
# A verify objective's marker on its targets (retail's blipType: the card suits
# are the Deck of 52's).
VERIFY_BLIPS = ("human", "scope", "blip", "clubs", "diamond", "hearts", "spades")
FAIL_TYPES = ("dies", "timer", "leave", "alarm", "spotted", "hostile")
# Squad orders (retail's own commands): attack the player, hold, move to one
# of the encounter's point sets, close in on the player, or walk a path.
ORDER_TYPES = ("attack", "hold", "move", "close", "path")
FAIL_TEXT = {"dies": "A target you had to protect was killed.", "timer": "You ran out of time.",
             "leave": "You left the mission area.", "escort": "Everyone you were escorting was killed.",
             "deliver": "The vehicle was destroyed.", "subdue": "The target was killed.",
             "use": "What you had to use was destroyed.", "alarm": "The alarm was raised.",
             "spotted": "You were spotted.", "hostile": "They turned on you."}


def bonus_amount(o):
    """A bonus objective's reward in dollars (0: a main objective)."""
    try:
        return max(0, int(float(o.get("bonus") or 0)))
    except (TypeError, ValueError):
        return 0


def when_of(text, count, what, own=0):
    """("start",), ("starts" | "done", k) for objective k, or ("zone", name),
    from "start", "starts:3", "done:3" or "zone:<name>". own: an objective
    that can't name itself (0: none)."""
    r = str(text or "start")
    if r == "start":
        return ("start",)
    when, _, rest = r.partition(":")
    if when in ("starts", "done"):
        try:
            k = int(rest)
        except ValueError:
            k = 0
        if not 1 <= k <= count or k == own:
            raise ValueError(f"{what}: {r!r} names no {'other ' if own else ''}objective (1 to {count})")
        return (when, k)
    if when == "zone" and rest:
        return ("zone", rest)
    if when == "dead" and rest:
        return ("dead", rest)          # every member of the squad ("encounter:squad") is dead
    raise ValueError(f"{what}: when is start, starts:<n>, done:<n>, zone:<name> or dead:<squad>, not {r!r}")


def radio_key(asset_id, k):
    """The string key of a generated radio line's text with no voice: sw.allies9.radio.foundry1."""
    map_, faction, number = parse_id(asset_id)
    return f"{map_}.{faction}{number}.radio.foundry{k}"


def reveal_of(o, count, i):
    """When a bonus objective shows: ("start",), ("starts" | "done", k) for
    objective k (1-based, another one), or ("zone", name). From its "reveal":
    "start" (default), "starts:3", "done:3" or "zone:<name>"."""
    r = str(o.get("reveal") or "start")
    if r == "start":
        return ("start",)
    when, _, what = r.partition(":")
    if when in ("starts", "done"):
        try:
            k = int(what)
        except ValueError:
            k = 0
        if not 1 <= k <= count or k == i:
            raise ValueError(f"objective {i}: \"reveal\": {r!r} names no other objective (1 to {count})")
        return (when, k)
    if when == "zone" and what:
        return ("zone", what)
    if when == "dead" and what:
        return ("dead", what)
    raise ValueError(f"objective {i}: \"reveal\" is start, starts:<n>, done:<n>, zone:<name> or dead:<squad>, not {r!r}")


def support_table(doc):
    """Lua for retail's tSupport from a mission's "support": [{"item":
    "template_support_gunship", "count": 1}, ...]. mission.lua adds them as
    the mission starts (Support_AddItem(item, count), or (item) with no
    count) and puts the default support back when it ends."""
    rows = []
    for k, s in enumerate(doc.get("support") or [], 1):
        item = str(s.get("item") or "")
        if not item.lower().startswith("template_"):
            raise ValueError(f"support item {k}: \"item\" is a template (template_support_...), not {item!r}")
        try:
            count = int(float(s.get("count") or 0))
        except (TypeError, ValueError):
            raise ValueError(f"support item {k}: \"count\" is a number") from None
        rows.append(f"{{ {_lua_str(item)}, {count} }}" if count > 0 else _lua_str(item))
    return ",\n".join("  " + r for r in rows)


def mood_lua(asset_id, doc):
    """Lua for a mission's "mood": {faction: change} on completion. The
    contract's own faction's is retail's iMissionMoodReward (mission.lua adds
    it; retail missions give 20 to 40); the others go through
    Faction_ModifyRelation (as nw_mafia1's -10 to the SK)."""
    _, own, _ = parse_id(asset_id)
    reward, others = None, []
    for f, v in sorted((doc.get("mood") or {}).items()):
        if f not in FACTIONS:
            raise ValueError(f"mood: {f!r} isn't a faction ({', '.join(FACTIONS)})")
        try:
            n = int(float(v))
        except (TypeError, ValueError):
            raise ValueError(f"mood: {f}'s change is a number, not {v!r}") from None
        if not -100 <= n <= 100:
            raise ValueError(f"mood: {f}'s change is -100 to 100, not {n}")
        if f == own:
            reward = n
        elif n:
            others.append(f"{f} = {n}")
    head = f"iMissionMoodReward = {reward}   -- the contract's faction, on completion (mission.lua)\n" if reward else ""
    return head + f"tFoundryMood = {{ {', '.join(others)} }}   -- other factions, on completion\n"


def alive_amount(o):
    """A verify objective's extra pay for a target captured alive (0: none)."""
    if o.get("type") != "verify":
        return 0
    try:
        return max(0, int(float(o.get("alive") or 0)))
    except (TypeError, ValueError):
        return 0


def bonus_fee(doc):
    """mission.ini BonusFee for a mission with objectives: its flat bonus
    ("ini" BonusFee, paid on completion) plus every bonus objective's reward,
    the most the contract can pay. The script pays the flat bonus plus the
    rewards of the bonus objectives done."""
    flat = int(float((doc.get("ini") or {}).get("BonusFee", 0) or 0))
    return flat + sum(bonus_amount(o) + alive_amount(o) for o in doc.get("objectives") or [])


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
    fast = [i for i, o in enumerate(objs, 1) if o.get("type") == "fast"]
    if len(fast) > 1:
        raise ValueError(f"objectives {', '.join(map(str, fast))}: a mission has one \"finish fast\" bonus")
    main, bonus = [], []
    main_at = {}            # objective number -> its place among the main objectives
    later_cbs = []          # the callbacks of delayed radio lines, waves and orders

    def delay_of(item, what, table, call, k, letter):
        """', delay = N, cb = ...' for an item with "delay" (seconds after its
        moment; Event_RelativeTimer calls its own callback, which then does it)."""
        if not item.get("delay"):
            return ""
        try:
            d = float(item["delay"])
        except (TypeError, ValueError):
            d = -1
        if d <= 0:
            raise ValueError(f"{what}: \"delay\" is a number of seconds, not {item['delay']!r}")
        cb = f"foundry_Later{letter}{k}"
        later_cbs.append(f"function {cb}() local x = {table}[{k}] x.due = true {call}(x) end")
        return f", delay = {d:g}, cb = '{cb}'"
    dead_sets = {}          # "encounter:squad" a moment waits on -> its members

    def dead_after(name):
        """', after = <squad>' for a "dead:" moment (foundry_Moment('dead', squad))."""
        if name not in dead_sets:
            dead_sets[name] = squad_members(name)
        return f", after = {_lua_str(name)}"
    for i, o in enumerate(objs, 1):
        kind = o.get("type")
        if kind not in OBJECTIVE_TYPES:
            raise ValueError(f"objective {i}: type is one of {', '.join(OBJECTIVE_TYPES)}, not {kind!r}")
        head = f"kind = '{kind}', name = 'foundry{i}', key = {_lua_str(objective_key(asset_id, i))}"
        reward = bonus_amount(o)
        if reward:
            head += f", bonus = {reward}"
            when = reveal_of(o, len(objs), i)
            if when[0] in ("starts", "done"):
                head += f", reveal = '{when[0]}', after = 'foundry{when[1]}'"
            elif when[0] == "zone":
                head += f", reveal = 'zone', rzone = {_lua_str(when[1])}"
            elif when[0] == "dead":
                head += f", reveal = 'dead'" + dead_after(when[1])
        if kind in ("destroy", "escort", "subdue", "use", "verify"):
            targets = list(o.get("targets") or [])
            if o.get("squad"):
                targets += squad_members(o["squad"])
            if not targets:
                raise ValueError(f"objective {i}: nothing to {kind} (give \"targets\" or \"squad\")")
            row = f"{head}, targets = {{ {', '.join(_lua_str(t) for t in targets)} }}"
            if kind in ("destroy", "escort") and o.get("need"):
                # destroy: done after this many; escort: fails once fewer than this many can arrive
                try:
                    need = int(float(o["need"]))
                except (TypeError, ValueError):
                    need = 0
                if not 1 <= need <= len(targets):
                    raise ValueError(f"objective {i}: \"need\" is 1 to {len(targets)} (it has {len(targets)} "
                                     f"target{'s' if len(targets) > 1 else ''}), not {o['need']!r}")
                if need < len(targets):
                    row += f", need = {need}"
            if kind in ("subdue", "use"):
                row += f", lost = {_lua_str(escort_lost_key(asset_id, i))}"     # killed / destroyed instead
            if kind == "subdue" and o.get("extract"):
                row += ", extract = true"                                       # done when flown out
            if kind == "use":
                row += f", act = {_lua_str(use_prompt_key(asset_id, i))}"      # the prompt on the object
            if kind == "verify":
                blip = o.get("blip") or "human"
                if blip not in VERIFY_BLIPS:
                    raise ValueError(f"objective {i}: \"blip\" is one of {', '.join(VERIFY_BLIPS)}, not {blip!r}")
                row += f", blip = '{blip}'"
                if alive_amount(o):
                    row += f", alive = {alive_amount(o)}"                    # paid for each one taken alive
            if kind == "escort":
                # they follow the player; done when every survivor is in the zone
                if not o.get("zone"):
                    raise ValueError(f"objective {i}: an escort objective needs a \"zone\" to bring them to")
                at = zone_centre(o["zone"]) if zone_centre and o.get("marker", True) else None
                mark = f", x = {at[0]:.2f}, y = {at[1]:.2f}, z = {at[2]:.2f}" if at else ""
                row += f", zone = {_lua_str(o['zone'])}{mark}, lost = {_lua_str(escort_lost_key(asset_id, i))}"
            row = f"{{ {row} }}"
        elif kind == "fast":
            # a bonus for completing the contract within "seconds" of it being shown (a clock counts down)
            seconds = float(o.get("seconds") or 0)
            if not reward or seconds <= 0:
                raise ValueError(f"objective {i}: a finish-fast objective is a bonus (\"bonus\") with \"seconds\"")
            row = f"{{ {head}, seconds = {seconds:g} }}"
        elif kind == "hold":
            # stay in a zone for "seconds"; leaving resets the clock
            seconds = float(o.get("seconds") or 0)
            if not o.get("zone") or seconds <= 0:
                raise ValueError(f"objective {i}: a hold objective needs a \"zone\" and \"seconds\"")
            at = zone_centre(o["zone"]) if zone_centre and o.get("marker", True) else None
            mark = f", x = {at[0]:.2f}, y = {at[1]:.2f}, z = {at[2]:.2f}" if at else ""
            row = f"{{ {head}, zone = {_lua_str(o['zone'])}{mark}, seconds = {seconds:g} }}"
        elif kind == "deliver":
            # get a vehicle (a named object) into a zone; failed if it's destroyed
            if not o.get("vehicle") or not o.get("zone"):
                raise ValueError(f"objective {i}: a deliver objective needs a \"vehicle\" and a \"zone\"")
            at = zone_centre(o["zone"]) if zone_centre and o.get("marker", True) else None
            mark = f", x = {at[0]:.2f}, y = {at[1]:.2f}, z = {at[2]:.2f}" if at else ""
            row = (f"{{ {head}, vehicle = {_lua_str(o['vehicle'])}, zone = {_lua_str(o['zone'])}{mark}, "
                   f"lost = {_lua_str(escort_lost_key(asset_id, i))} }}")
        else:
            if not o.get("zone"):
                raise ValueError(f"objective {i}: a goto objective needs a \"zone\"")
            at = zone_centre(o["zone"]) if zone_centre and o.get("marker", True) else None   # "marker": false: none
            mark = f", x = {at[0]:.2f}, y = {at[1]:.2f}, z = {at[2]:.2f}" if at else ""
            row = f"{{ {head}, zone = {_lua_str(o['zone'])}{mark} }}"
        if not reward and main and o.get("together"):
            # starts with the main objective before it; the next waits for both
            row = row[:-1].rstrip() + ", together = true }"
        (bonus if reward else main).append(row)
        if not reward:
            main_at[i] = len(main)
    fails = []
    for k, c in enumerate(doc.get("fail") or [], 1):
        kind = c.get("type")
        if kind not in FAIL_TYPES:
            raise ValueError(f"fail condition {k}: type is one of {', '.join(FAIL_TYPES)}, not {kind!r}")
        row = f"kind = '{kind}', reason = {_lua_str(fail_key(asset_id, k))}, cb = 'foundry_Failed{k}'"
        if kind in ("alarm", "hostile"):
            # an alarm (a placed alarm box) sounded, or someone turned hostile to the player
            targets = list(c.get("targets") or []) + (squad_members(c["squad"]) if c.get("squad") else [])
            if not targets:
                raise ValueError(f"fail condition {k}: \"{kind}\" needs \"targets\"" + (" or \"squad\"" if kind == "hostile" else ""))
            row += f", targets = {{ {', '.join(_lua_str(t) for t in targets)} }}"
        elif kind == "spotted":
            if c.get("faction") not in FACTIONS:
                raise ValueError(f"fail condition {k}: \"spotted\" needs the \"faction\" ({', '.join(FACTIONS)})")
            row += f", faction = '{c['faction']}'"
        elif kind == "dies":
            targets = list(c.get("targets") or []) + (squad_members(c["squad"]) if c.get("squad") else [])
            if not targets:
                raise ValueError(f"fail condition {k}: nobody to protect (give \"targets\" or \"squad\")")
            row += f", targets = {{ {', '.join(_lua_str(t) for t in targets)} }}"
        elif kind == "timer":
            seconds = float(c.get("seconds") or 0)
            if seconds <= 0:
                raise ValueError(f"fail condition {k}: a timer needs \"seconds\"")
            row += f", seconds = {seconds:g}"
        else:
            if not c.get("zone"):
                raise ValueError(f"fail condition {k}: \"leave\" needs the \"zone\" the player must stay in")
            if zone_centre:
                zone_centre(c["zone"])        # (its layer loads with the script)
            row += f", zone = {_lua_str(c['zone'])}"
        if c.get("from") not in (None, "", "start"):
            # when it begins: an objective starts or is done, or the player enters a zone
            when = when_of(c["from"], len(objs), f"fail condition {k}")
            if when[0] in ("starts", "done"):
                row += f", from = '{when[0]}', after = 'foundry{when[1]}'"
            elif when[0] == "dead":
                row += ", from = 'dead'" + dead_after(when[1])
            else:
                row += f", from = 'zone', fzone = {_lua_str(when[1])}"
                if zone_centre:
                    zone_centre(when[1])        # (its layer loads with the script)
        if c.get("until"):
            m = re.fullmatch(r"done:(\d+)", str(c["until"]))
            if not m or not 1 <= int(m.group(1)) <= len(objs):
                raise ValueError(f"fail condition {k}: \"until\" is done:<objective number>, not {c['until']!r}")
            row += f", untilObj = 'foundry{int(m.group(1))}'"
        fails.append(f"{{ {row} }}")
    fail_table = ",\n".join("  " + r for r in fails)
    # each condition's own callback (the events don't say which one they're for)
    fail_cbs = "".join(f"function foundry_Failed{k}() foundry_FailIf({k}) end\n" for k in range(1, len(fails) + 1))
    radio = []
    for k, line in enumerate(doc.get("radio") or [], 1):
        # "end": as the contract completes (it waits for the line)
        when = ("end",) if line.get("when") == "end" else when_of(line.get("when"), len(objs), f"radio line {k}")
        row = f"when = '{when[0]}'"
        if when[0] in ("starts", "done"):
            row += f", after = 'foundry{when[1]}'"
        elif when[0] == "dead":
            row += dead_after(when[1])
        elif when[0] == "zone":
            row += f", zone = {_lua_str(when[1])}"
            if zone_centre:
                zone_centre(when[1])          # (its layer loads with the script)
        sound = str(line.get("sound") or "").strip()
        if sound:
            if not re.fullmatch(r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+", sound):
                raise ValueError(f"radio line {k}: \"sound\" is <bank>.<cue>, like fio.S10START, not {sound!r}")
            row += f", sound = {_lua_str(sound)}"
        elif line.get("text"):
            row += f", text = {_lua_str(radio_key(asset_id, k))}"
        else:
            raise ValueError(f"radio line {k}: give a \"sound\", a \"text\" or both")
        if when[0] != "end":
            row += delay_of(line, f"radio line {k}", "tFoundryRadio", "foundry_Say", k, "R")
        radio.append(f"{{ {row} }}")
    radio_table = ",\n".join("  " + r for r in radio)
    music = []
    for k, line in enumerate(doc.get("music") or [], 1):
        what = f"music {k}"
        play = str(line.get("play") or "").strip()
        if play != "normal" and not re.fullmatch(r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+", play):
            raise ValueError(f"{what}: \"play\" is a track (music.Card_Ace) or \"normal\", not {play!r}")
        when = ("end",) if line.get("when") == "end" else when_of(line.get("when"), len(objs), what)
        row = f"when = '{when[0]}'"
        if when[0] in ("starts", "done"):
            row += f", after = 'foundry{when[1]}'"
        elif when[0] == "dead":
            row += dead_after(when[1])
        elif when[0] == "zone":
            row += f", zone = {_lua_str(when[1])}"
            if zone_centre:
                zone_centre(when[1])
        row += f", play = {_lua_str(play)}"
        if when[0] != "end":
            row += delay_of(line, what, "tFoundryMusic", "foundry_PlayMusic", k, "M")
        music.append(f"{{ {row} }}")
    music_table = ",\n".join("  " + r for r in music)
    weather = []
    for k, line in enumerate(doc.get("weather") or [], 1):
        what = f"weather {k}"
        preset = str(line.get("preset") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", preset):
            raise ValueError(f"{what}: \"preset\" is one of the map's atmosphere presets (summer1) or \"normal\", "
                             f"not {preset!r}")
        when = ("end",) if line.get("when") == "end" else when_of(line.get("when"), len(objs), what)
        row = f"when = '{when[0]}'"
        if when[0] in ("starts", "done"):
            row += f", after = 'foundry{when[1]}'"
        elif when[0] == "dead":
            row += dead_after(when[1])
        elif when[0] == "zone":
            row += f", zone = {_lua_str(when[1])}"
            if zone_centre:
                zone_centre(when[1])
        row += f", preset = {_lua_str(preset)}"
        if when[0] != "end":
            row += delay_of(line, what, "tFoundryWeather", "foundry_SetWeather", k, "S")
        weather.append(f"{{ {row} }}")
    weather_table = ",\n".join("  " + r for r in weather)
    # Squads that appear later: their own layers, loaded at "when".
    waves, wave_of = [], {}
    for k, w in enumerate(doc.get("waves") or [], 1):
        what = f"wave {k}"
        enc, _, sq = str(w.get("squad") or "").rpartition(":")
        if not enc or not sq:
            raise ValueError(f"{what}: \"squad\" is \"encounter:squad\"")
        when = when_of(w.get("when"), len(objs), what)
        row = f"when = '{when[0]}'"
        if when[0] in ("starts", "done"):
            row += f", after = 'foundry{when[1]}'"
        elif when[0] == "dead":
            row += dead_after(when[1])
        elif when[0] == "zone":
            row += f", zone = {_lua_str(when[1])}"
            if zone_centre:
                zone_centre(when[1])
        row += f", layer = {_lua_str(wave_layer(asset_id, k))}"
        row += delay_of(w, what, "tFoundryWaves", "foundry_Wave", k, "W")
        waves.append(f"{{ {row} }}")
        wave_of[(enc.lower(), sq.lower())] = (wave_encounter(enc, k), wave_layer(asset_id, k))
    wave_table = ",\n".join("  " + r for r in waves)
    orders = []
    for k, o in enumerate(doc.get("orders") or [], 1):
        what = f"squad order {k}"
        act = o.get("do")
        if act not in ORDER_TYPES:
            raise ValueError(f"{what}: \"do\" is one of {', '.join(ORDER_TYPES)}, not {act!r}")
        enc, _, sq = str(o.get("squad") or "").rpartition(":")
        if not enc or not sq:
            raise ValueError(f"{what}: \"squad\" is \"encounter:squad\"")
        members = squad_members(o["squad"])         # (and its layer loads with the script)
        when = when_of(o.get("when"), len(objs), what)
        row = f"when = '{when[0]}'"
        if when[0] in ("starts", "done"):
            row += f", after = 'foundry{when[1]}'"
        elif when[0] == "dead":
            row += dead_after(when[1])
        elif when[0] == "zone":
            row += f", zone = {_lua_str(when[1])}"
            if zone_centre:
                zone_centre(when[1])
        wave = wave_of.get((enc.lower(), sq.lower()))
        if wave:
            enc = wave[0]            # its own layer's copy of the encounter; given once it's there
        row += f", act = '{act}', enc = {_lua_str(enc)}, squad = {_lua_str(sq)}"
        if wave:
            row += f", wave = {_lua_str(wave[1])}"
        if act in ("move", "path"):
            if not o.get("to"):
                raise ValueError(f"{what}: \"{act}\" needs \"to\" (a point set letter, or a path's name)")
            row += f", to = {_lua_str(o['to'])}"
        if act == "path":
            row += f", members = {{ {', '.join(_lua_str(m) for m in members)} }}"
        row += delay_of(o, what, "tFoundryOrders", "foundry_Order", k, "O")
        orders.append(f"{{ {row} }}")
    order_table = ",\n".join("  " + r for r in orders)
    later_lua = "".join(c + "\n" for c in later_cbs)
    dead_rows = []
    for name, members in dead_sets.items():
        enc, _, sq = name.rpartition(":")
        wave = wave_of.get((enc.lower(), sq.lower()))
        dead_rows.append(f"{{ squad = {_lua_str(name)}, members = {{ {', '.join(_lua_str(m) for m in members)} }}"
                         + (f", wave = {_lua_str(wave[1])}" if wave else "") + " }")
    dead_table = ",\n".join("  " + r for r in dead_rows)
    main_table = ",\n".join("  " + r for r in main)
    bonus_table = ",\n".join("  " + r for r in bonus)
    flat = bonus_fee(doc) - sum(bonus_amount(o) + alive_amount(o) for o in objs)
    pays = "true" if bonus or any(alive_amount(o) for o in objs) else "false"   # writes fBonusFee
    # testing: start at a later main objective (the ones before are skipped)
    test_from = "nil"
    if doc.get("test_from"):
        try:
            n = int(doc["test_from"])
        except (TypeError, ValueError):
            n = 0
        if n not in main_at:
            raise ValueError(f"\"test_from\": {doc['test_from']!r} isn't a main objective "
                             f"({', '.join(map(str, main_at)) or 'none'})")
        test_from = str(main_at[n])
    unlocks = [str(u) for u in doc.get("unlocks") or [] if u]
    for u in unlocks:
        if not u.lower().startswith("template_"):
            raise ValueError(f"unlocks: {u!r} isn't a template (template_support_...)")
    done = done_mail(asset_id, doc, "x")
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
tSupport = {{              -- support items for the mission (retail's: mission.lua adds them, and resets after)
{support_table(doc)}
}}
tFoundryBonus = {{         -- bonus objectives, shown at the start or on their "reveal"
{bonus_table}
}}
tFoundryFail = {{          -- the mission fails if one happens (until its objective is done)
{fail_table}
}}
{fail_cbs}tFoundryRadio = {{         -- radio lines: voiced (banter queue, with a subtitle) or text
{radio_table}
}}
iFoundryRadioPending = 0   -- voiced lines queued and not yet played
tFoundryMusic = {{         -- music: one of the game's tracks, or back to the faction's own ("normal")
{music_table}
}}
tFoundryWeather = {{       -- weather: one of the map's atmosphere presets, forced, or back to normal
{weather_table}
}}
tFoundryWaves = {{         -- squads that appear later: their layers, loaded when their moment comes
{wave_table}
}}
tFoundryWaveUp = {{}}       -- a wave's layer -> its squad is there
tFoundryOrders = {{        -- squad orders, given when their moment comes
{order_table}
}}
{later_lua}tFoundryDead = {{          -- squads a moment waits on ("dead:"): watched from the start, or once their wave is there
{dead_table}
}}
tFoundryDeadOf = {{}}       -- actor (as int) -> the squad it counts for
tFoundryActive = {{}}       -- started objectives by name
tFoundryWatch = {{}}        -- actor (as int) -> the fail condition or escort objective watching it
bFoundryOver = false
iFoundryStep = 0
iFoundryGroupLeft = 0       -- main objectives running together not done yet
iFoundryFlatBonus = {flat}
iFoundryEarned = 0
{mood_lua(asset_id, doc)}bFoundryPays = {pays}
bFoundryDebug = {"true" if doc.get("debug") else "false"}     -- each step shown on screen too (for testing)
iFoundryTestFrom = {test_from}      -- testing: the main objective it starts at (nil: the first)
bFoundryKeepHostile = {"true" if doc.get("keep_if_hostile") else "false"}   -- the contract goes on if its faction turns hostile
tFoundryLog = {{}}          -- the last steps, kept in scribble memory as foundry_log (foundry_Log() reads it)      -- the payout depends on what's done (bonus objectives, targets taken alive)
tFoundryVerify = {{}}       -- verify target (actor name) -> its objective
tFoundryUnlocks = {{ {", ".join(_lua_str(u) for u in unlocks)} }}   -- shop items unlocked on completion
sFoundryDoneMail = {_lua_str(done[0]) if done else "nil"}   -- the follow-up mail

function ScriptInit()
Utility_LoadUtilityScript('mission')
Utility_LoadUtilityScript('mission_objective')
Utility_LoadUtilityScript('mission_objectiveverb')
Utility_LoadUtilityScript('mission_objectiveverify')
Utility_LoadUtilityScript('util_misc')
Utility_LoadUtilityScript('hud')
Utility_LoadUtilityScript('util_banter')
mission_GenericInit()
{loads}end

-- The mission's steps, for finding out what happened in a test run: the last
-- eight are kept in scribble memory (they outlive the mission, failed or
-- done); foundry_Log() in Foundry Console's Lua box returns them, and with
-- "debug" each shows on screen as it happens.
function foundry_Note(s)
iFoundryNote = (iFoundryNote or 0) + 1
local line = iFoundryNote .. ': ' .. s
table.insert(tFoundryLog, line)
if table.getn(tFoundryLog) > 8 then table.remove(tFoundryLog, 1) end   -- short, as retail's scribble strings are
-- (only what retail scripts use: the game's Lua has no string library)
Utility_WriteStringToScribbleMemory('foundry_log', '{asset_id}: ' .. table.concat(tFoundryLog, ' | '))
if bFoundryDebug then util_ObjMsgPrint{{ sMsg = '[{asset_id}] ' .. line, sType = 'GENERAL' }} end
end
function foundry_Log()
return Utility_ReadStringFromScribbleMemory('foundry_log') or 'no mission log yet'
end

-- The contract's faction turned hostile (mission.lua's
-- mission_FactionAttitudeChange calls this instead of cancelling the
-- contract, when it's there): with "keep_if_hostile" the contract goes on.
if bFoundryKeepHostile then
function custom_FactionAttitudeChange()
foundry_Note('the faction turned hostile: the contract goes on')
end
end

-- A squad is wiped out (every member dead): the things set for that moment.
function foundry_WatchSquads(sWave)
for i, d in ipairs(tFoundryDead) do
if d.wave == sWave and not d.watching then
d.watching = true
d.left = table.getn(d.members)
for j, a in ipairs(d.members) do
tFoundryDeadOf[Utility_GetActorAsInt(a)] = d
Event_ActorHitPointsLessThan('foundry_SquadMemberDown', 'b', a, 1)
end
end
end
end
function foundry_SquadMemberDown(aActor)
local d = tFoundryDeadOf[Utility_GetActorAsInt(aActor)]
if not d or d.gone then return end
d.left = d.left - 1
if d.left > 0 then return end
d.gone = true
foundry_Note('squad wiped out: ' .. d.squad)
foundry_Reveal('dead', d.squad)
foundry_Waves('dead', d.squad)
foundry_FailsFrom('dead', d.squad)
foundry_Radio('dead', d.squad)
foundry_Musics('dead', d.squad)
foundry_Weathers('dead', d.squad)
foundry_Orders('dead', d.squad)
end

function post_InitialNotifications()   -- after the contract mail
foundry_Note('started')
foundry_WatchSquads(nil)
foundry_SetupFail()
for i, r in ipairs(tFoundryRadio) do
if r.when == 'zone' then Event_ActorInBoundary('foundry_RadioZone', 'b', 'player0', r.zone, true) end
end
for i, m in ipairs(tFoundryMusic) do
if m.when == 'zone' then Event_ActorInBoundary('foundry_MusicZone', 'b', 'player0', m.zone, true) end
end
for i, w in ipairs(tFoundryWeather) do
if w.when == 'zone' then Event_ActorInBoundary('foundry_WeatherZone', 'b', 'player0', w.zone, true) end
end
for i, o in ipairs(tFoundryOrders) do
if o.when == 'zone' then Event_ActorInBoundary('foundry_OrderZone', 'b', 'player0', o.zone, true) end
end
for i, w in ipairs(tFoundryWaves) do
if w.when == 'zone' then Event_ActorInBoundary('foundry_WaveZone', 'b', 'player0', w.zone, true) end
end
foundry_Waves('start')
foundry_Radio('start')
foundry_Musics('start')
foundry_Weathers('start')
foundry_Orders('start')
for i, o in ipairs(tFoundryBonus) do
if not o.reveal then foundry_Start(o)
elseif o.reveal == 'zone' then Event_ActorInBoundary('foundry_RevealZone', 'b', 'player0', o.rzone, true) end
end
if iFoundryTestFrom then           -- testing: the main objectives before it are skipped
iFoundryStep = iFoundryTestFrom - 1
foundry_Note('testing: starts at main objective ' .. iFoundryTestFrom)
end
foundry_NextObjective()
end

-- Bonus objectives shown later: when another objective starts or is done
-- ('starts' / 'done', its name), or when the player enters a zone.
function foundry_Reveal(sWhen, sName)
for i, o in ipairs(tFoundryBonus) do
if o.reveal == sWhen and o.after == sName and not tFoundryActive[o.name] then foundry_Start(o) end
end
end
function foundry_RevealZone(playerActor, boundaryName)
for i, o in ipairs(tFoundryBonus) do
if o.reveal == 'zone' and o.rzone == boundaryName and not tFoundryActive[o.name] then foundry_Start(o) end
end
end

-- Radio lines: when the mission starts, an objective starts or is done
-- (its name), or the player enters a zone. Voiced lines go through the
-- banter queue (no overlaps) 3.5 s late, so their subtitle isn't hidden by
-- the objective message they come with; the contract completes after the
-- last one has played.
function foundry_Radio(sWhen, sName)
for i, r in ipairs(tFoundryRadio) do
if not r.said and r.when == sWhen and r.after == sName then foundry_Say(r) end
end
end
-- Music: Audio_PlayMusic as retail missions switch to a card or action
-- theme; "normal" goes back to the faction's own (Audio_PlayFactionMusic, as
-- retail's StopCardMusic). The contract ending puts it back too.
function foundry_Musics(sWhen, sName)
for i, m in ipairs(tFoundryMusic) do
if not m.played and m.when == sWhen and m.after == sName then foundry_PlayMusic(m) end
end
end
function foundry_MusicZone(playerActor, boundaryName)
for i, m in ipairs(tFoundryMusic) do
if not m.played and m.when == 'zone' and m.zone == boundaryName then foundry_PlayMusic(m) end
end
end
function foundry_PlayMusic(m)
if m.delay and not m.due then
if not m.waiting then m.waiting = true Event_RelativeTimer(m.cb, 'b', m.delay) end
return
end
m.played = true
foundry_Note('music ' .. m.play)
if m.play == 'normal' then foundry_NormalMusic()
else
bFoundryMusic = true
Audio_PlayMusic(m.play)
end
end
function foundry_NormalMusic()
bFoundryMusic = nil
local sFaction = Utility_ReadStringFromScribbleMemory('current_faction')
if sFaction ~= nil then Audio_PlayFactionMusic(sFaction) else Audio_PlayFactionMusic('none') end
end
-- Weather: one of the map's [Atmosphere] presets, forced as retail missions
-- do (Mission_EnableAtmosphere, as the prison and the C-17); "normal" lets
-- the map's own zones decide again. The contract ending does that too.
function foundry_Weathers(sWhen, sName)
for i, w in ipairs(tFoundryWeather) do
if not w.set and w.when == sWhen and w.after == sName then foundry_SetWeather(w) end
end
end
function foundry_WeatherZone(playerActor, boundaryName)
for i, w in ipairs(tFoundryWeather) do
if not w.set and w.when == 'zone' and w.zone == boundaryName then foundry_SetWeather(w) end
end
end
function foundry_SetWeather(w)
if w.delay and not w.due then
if not w.waiting then w.waiting = true Event_RelativeTimer(w.cb, 'b', w.delay) end
return
end
w.set = true
foundry_Note('weather ' .. w.preset)
foundry_NormalWeather()
if w.preset ~= 'normal' then
sFoundryWeather = w.preset
Mission_EnableAtmosphere(w.preset, true)
end
end
function foundry_NormalWeather()
if sFoundryWeather then Mission_EnableAtmosphere(sFoundryWeather, false) end
sFoundryWeather = nil
end
function foundry_RadioZone(playerActor, boundaryName)
for i, r in ipairs(tFoundryRadio) do
if not r.said and r.when == 'zone' and r.zone == boundaryName then foundry_Say(r) end
end
end
-- Squad orders: retail's Squad_Command verbs (Attack, Wait, TacticalMove to
-- a point set, MoveToActor) and, for a path, PathMove for each member.
function foundry_Orders(sWhen, sName)
for i, o in ipairs(tFoundryOrders) do
if not o.given and o.when == sWhen and o.after == sName then foundry_Order(o) end
end
end
function foundry_OrderZone(playerActor, boundaryName)
for i, o in ipairs(tFoundryOrders) do
if not o.given and o.when == 'zone' and o.zone == boundaryName then foundry_Order(o) end
end
end
-- Squads that appear later: load their layer; the squad is there a moment
-- later (2 s), then its orders that came due meanwhile are given.
function foundry_Waves(sWhen, sName)
for i, w in ipairs(tFoundryWaves) do
if not w.loaded and w.when == sWhen and w.after == sName then foundry_Wave(w) end
end
end
function foundry_WaveZone(playerActor, boundaryName)
for i, w in ipairs(tFoundryWaves) do
if not w.loaded and w.when == 'zone' and w.zone == boundaryName then foundry_Wave(w) end
end
end
function foundry_Wave(w)
if w.delay and not w.due then             -- "delay" seconds after its moment
if not w.waiting then w.waiting = true Event_RelativeTimer(w.cb, 'b', w.delay) end
return
end
w.loaded = true
foundry_Note('squad arrives (' .. w.layer .. ')')
Utility_ReadLevelFile(w.layer)
Event_RelativeTimer('foundry_WavesUp', 'b', 2.0)
end
function foundry_WavesUp()
for i, w in ipairs(tFoundryWaves) do
if w.loaded and not tFoundryWaveUp[w.layer] then
tFoundryWaveUp[w.layer] = true
foundry_WatchSquads(w.layer)          -- its squad, if a moment waits on it
end
end
for i, o in ipairs(tFoundryOrders) do
if o.pending and tFoundryWaveUp[o.wave] then o.pending = nil foundry_Order(o) end
end
end
function foundry_Order(o)
if o.delay and not o.due then
if not o.waiting then o.waiting = true Event_RelativeTimer(o.cb, 'b', o.delay) end
return
end
o.given = true
if o.wave and not tFoundryWaveUp[o.wave] then o.pending = true return end   -- not there yet
foundry_Note('order ' .. o.act .. ' to ' .. o.enc .. ':' .. o.squad)
if o.act == 'attack' then Squad_Command(o.enc, o.squad, 'Attack', 'player0', 'hiPri')
elseif o.act == 'hold' then Squad_Command(o.enc, o.squad, 'Wait', 'hiPri')
elseif o.act == 'move' then Squad_Command(o.enc, o.squad, 'TacticalMove', o.to, 'hiPri')
elseif o.act == 'close' then Squad_Command(o.enc, o.squad, 'MoveToActor', 'player0', 1.0, 1, 'hiPri')
elseif o.act == 'path' then
for j, a in ipairs(o.members) do
if Utility_DoesActorExist(a) then Ai_CommandQueue(a, 'PathMove', o.to, '1.0', 'hiPri') end
end
end
end
function foundry_Say(r)
if r.delay and not r.due then
if not r.waiting then r.waiting = true Event_RelativeTimer(r.cb, 'b', r.delay) end
return
end
r.said = true
foundry_Note('radio ' .. (r.sound or r.text))
if r.sound then
iFoundryRadioPending = iFoundryRadioPending + 1
banter_Add{{ r.sound, prePad = 3.5, callback = foundry_RadioDone }}
else
util_ObjMsgPrint{{ sMsg = Utility_GetStringById(r.text), sType = 'GENERAL' }}
end
end
function foundry_RadioDone()
iFoundryRadioPending = iFoundryRadioPending - 1
if iFoundryRadioPending <= 0 and bFoundryFinishing then foundry_Finish() end
end

-- Fail conditions: someone protected dies, the timer runs out, or the player
-- leaves a zone. sReasonForFailure is the string mission_MissionFailure shows.
-- Each begins at the start, or at its "from": an objective starts or is done
-- (foundry_FailsFrom), or the player enters a zone.
function foundry_SetupFail()
for i, f in ipairs(tFoundryFail) do
if not f.from then foundry_ArmFail(f)
elseif f.from == 'zone' then Event_ActorInBoundary('foundry_FailZone', 'b', 'player0', f.fzone, true) end
end
end
function foundry_FailsFrom(sWhen, sName)
for i, f in ipairs(tFoundryFail) do
if f.from == sWhen and f.after == sName then foundry_ArmFail(f) end
end
end
function foundry_FailZone(playerActor, boundaryName)
for i, f in ipairs(tFoundryFail) do
if f.from == 'zone' and f.fzone == boundaryName then foundry_ArmFail(f) end
end
end
function foundry_ArmFail(f)
if f.armed or f.off or bFoundryOver then return end
f.armed = true
foundry_Note('fail condition on: ' .. f.kind)
if f.kind == 'dies' then
for j, a in ipairs(f.targets) do
tFoundryWatch[Utility_GetActorAsInt(a)] = f
Event_ActorHitPointsLessThan('foundry_Died', 'b', a, 1)
end
elseif f.kind == 'alarm' then
for j, a in ipairs(f.targets) do Event_AlarmUsed(f.cb, 'b', a, true) end
elseif f.kind == 'hostile' then
for j, a in ipairs(f.targets) do Event_ActorIsHostileToward(f.cb, 'b', a, 'player0', true) end
elseif f.kind == 'spotted' then
Event_ActorSpottedTrespassing(f.cb, 'b', 'player0', f.faction, true)
elseif f.kind == 'timer' then
tFoundryTimer = f
f.hud = HUD_AddTrayDisplay{{ {{ DisplayClock = true, nObjStyle = HUD.STYLE.NORMAL, nStart = f.seconds, nLimit = 0,
                                fCallback = foundry_TimeUp }} }}
else
Event_ActorInBoundary('foundry_Left', 'b', 'player0', f.zone, false)
end
end
function foundry_Fail(sReason)
if bFoundryOver then return end
bFoundryOver = true
foundry_Note('FAILED: ' .. tostring(sReason))
foundry_StopTimer()
if bFoundryMusic then foundry_NormalMusic() end
foundry_NormalWeather()
sReasonForFailure = sReason
mission_MissionFailure()
end
function foundry_FailIf(i)
local f = tFoundryFail[i]
if f and not f.off then foundry_Fail(f.reason) end
end
function foundry_StopTimer()
if tFoundryTimer and tFoundryTimer.hud then HUD_RemoveTrayDisplay(tFoundryTimer.hud) tFoundryTimer.hud = nil end
if tFoundryFast and tFoundryFast.hud then HUD_RemoveTrayDisplay(tFoundryFast.hud) tFoundryFast.hud = nil end
end
-- Finish fast: the clock ran out first; the bonus is lost.
function foundry_TooSlow(hDisplay, eState)
if eState ~= HUD.STATE.LIMIT then return end
local o = tFoundryFast
if not o or o.done or bFoundryOver then return end
o.done = true
if o.hud then HUD_RemoveTrayDisplay(o.hud) o.hud = nil end
foundry_Note(o.name .. ': too slow')
util_ObjMsgPrint{{ sMsg = o.text, sType = 'BON_OBJ_FAIL', vIdx = 2, sObjective = o.name }}
mission_RemoveObjective(o.name)
end
function foundry_Died(aActor)
local w = tFoundryWatch[Utility_GetActorAsInt(aActor)]
if not w then return end
if w.kind == 'escort' then foundry_EscortLost(w, aActor)
elseif w.kind == 'deliver' then if not w.done then foundry_Fail(w.lost) end
elseif not w.off then foundry_Fail(w.reason) end
end
function foundry_TimeUp(hDisplay, eState)
if eState ~= HUD.STATE.LIMIT then return end
if tFoundryTimer and not tFoundryTimer.off then foundry_Fail(tFoundryTimer.reason) end
end
function foundry_Left(playerActor, boundaryName)
for i, f in ipairs(tFoundryFail) do
if f.kind == 'leave' and f.zone == boundaryName and f.armed and not f.off then foundry_Fail(f.reason) end
end
end
-- An objective is done: the fail conditions that last until then end.
function foundry_EndFail(sName)
for i, f in ipairs(tFoundryFail) do
if f.untilObj == sName then
f.off = true
if f == tFoundryTimer then foundry_StopTimer() end
end
end
end

-- Escort: they follow the player; done when every survivor has reached the
-- zone, failed when they're all dead.
function foundry_EscortIn(aActor, boundaryName)
local k = Utility_GetActorAsInt(aActor)
local o = tFoundryWatch[k]
if not o or o.kind ~= 'escort' or o.done or o.zone ~= boundaryName or o.inZone[k] then return end
o.inZone[k] = true
o.arrived = o.arrived + 1
foundry_EscortCheck(o)
end
function foundry_EscortLost(o, aActor)
if o.done then return end
local k = Utility_GetActorAsInt(aActor)
if o.inZone[k] then o.arrived = o.arrived - 1 end
o.inZone[k] = nil
o.dead = o.dead + 1
if table.getn(o.targets) - o.dead < (o.need or 1) then foundry_Fail(o.lost) else foundry_EscortCheck(o) end
end
-- Subdue: a target was killed instead. A main objective fails the mission;
-- a bonus one is lost.
function foundry_TargetKilled(sActor, sObjName)
local o = tFoundryActive[sObjName]
if not o or o.done then return end
if o.bonus then
o.done = true
util_ObjMsgPrint{{ sMsg = o.text, sType = 'BON_OBJ_FAIL', vIdx = 2, sObjective = o.name }}
mission_RemoveObjective(o.name)
else
foundry_Fail(o.lost)
end
end

-- Hold: in the zone, a clock counts down; leaving stops it (and it starts
-- again from the top on coming back).
function foundry_HoldIn(playerActor, boundaryName)
for sName, o in pairs(tFoundryActive) do
if o.kind == 'hold' and o.zone == boundaryName and not o.done and not o.holding then
o.holding = true
tFoundryHolding = o
foundry_Note(o.name .. ': in the zone, clock running')
o.hud = HUD_AddTrayDisplay{{ {{ DisplayClock = true, nObjStyle = HUD.STYLE.NORMAL, nStart = o.seconds, nLimit = 0,
                                fCallback = foundry_HoldDone }} }}
Event_ActorInBoundary('foundry_HoldOut', 'b', 'player0', o.zone, false)
end
end
end
function foundry_HoldOut(playerActor, boundaryName)
local o = tFoundryHolding
if not o or o.done or not o.holding or o.zone ~= boundaryName then return end
o.holding = false
foundry_Note(o.name .. ': left the zone, clock stopped')
if o.hud then HUD_RemoveTrayDisplay(o.hud) o.hud = nil end
local sMsg, iIdx = foundry_Kind(o)
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'UPD_NEG', vIdx = iIdx, sObjective = o.name }}
Event_ActorInBoundary('foundry_HoldIn', 'b', 'player0', o.zone, true)
end
function foundry_HoldDone(hDisplay, eState)
if eState ~= HUD.STATE.LIMIT then return end
local o = tFoundryHolding
if not o or o.done or not o.holding then return end
if o.hud then HUD_RemoveTrayDisplay(o.hud) o.hud = nil end
foundry_Done(o)
end

-- Deliver: the vehicle is in the zone.
function foundry_Delivered(aActor, boundaryName)
local o = tFoundryWatch[Utility_GetActorAsInt(aActor)]
if o and o.kind == 'deliver' and not o.done and o.zone == boundaryName then foundry_Done(o) end
end
function foundry_EscortCheck(o)
local iAlive = table.getn(o.targets) - o.dead
if o.arrived >= iAlive and iAlive > 0 then
foundry_Done(o)
else
local sMsg, iIdx = foundry_Kind(o)
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'UPD_POS', vIdx = iIdx,
                  tCounter = {{ vCurr = o.arrived, vLim = iAlive }}, sObjective = o.name }}
end
end

-- The next main objective, with any marked "together" after it: they run at
-- once, and the one after waits until they're all done.
function foundry_NextObjective()
iFoundryStep = iFoundryStep + 1
local o = tFoundryObjectives[iFoundryStep]
if not o then
foundry_Finish()
return
end
local tGroup = {{ o }}
while tFoundryObjectives[iFoundryStep + 1] and tFoundryObjectives[iFoundryStep + 1].together do
iFoundryStep = iFoundryStep + 1
table.insert(tGroup, tFoundryObjectives[iFoundryStep])
end
iFoundryGroupLeft = table.getn(tGroup)
for i, g in ipairs(tGroup) do foundry_Start(g) end
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
foundry_Note((o.bonus and 'bonus ' or '') .. o.name .. ' (' .. o.kind .. ') starts')
local sMsg, iIdx, sObjType, sTargetType = foundry_Kind(o)
if o.kind == 'fast' then
-- done if the contract completes before its clock runs out (foundry_Finish)
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'ADD', vIdx = iIdx, sObjective = o.name }}
tFoundryFast = o
o.hud = HUD_AddTrayDisplay{{ {{ DisplayClock = true, nObjStyle = HUD.STYLE.NORMAL, nStart = o.seconds, nLimit = 0,
                                fCallback = foundry_TooSlow }} }}
elseif o.kind == 'verify' then
-- kill and photograph the body, or capture alive (retail's verify: the Deck of 52)
for j, a in ipairs(o.targets) do
tFoundryVerify[a] = o
mission_ObjectiveVerify{{ callbackFunc = foundry_Verified, objectiveName = o.name .. 'v' .. j, objectiveDesc = o.text,
                         actorName = a, objectiveType = sObjType, targetType = sTargetType, blipType = o.blip }}
end
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'ADD', vIdx = iIdx,
                  tCounter = {{ vCurr = 0, vLim = table.getn(o.targets) }}, sObjective = o.name }}
elseif o.kind == 'use' then
-- walk up and use it (retail's action verb); destroying it fails it
mission_ObjectiveAction{{ callbackFunc = foundry_TargetDown, killCallbackFunc = foundry_TargetKilled,
                         objectiveName = o.name, objectiveDesc = o.text, actorName = o.targets,
                         actionCaption = o.act, objectiveType = sObjType, targetType = sTargetType }}
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'ADD', vIdx = iIdx,
                  tCounter = {{ vCurr = 0, vLim = table.getn(o.targets) }}, sObjective = o.name }}
elseif o.kind == 'subdue' and o.extract then
-- capture alive and fly them out (retail's extract verb, as nw_allies6); killing them fails it
mission_ObjectiveExtract{{ callbackFunc = foundry_TargetDown, killCallbackFunc = foundry_TargetKilled,
                          objectiveName = o.name, objectiveDesc = o.text, actorName = o.targets,
                          objectiveType = sObjType, targetType = sTargetType, blipType = 'human' }}
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'ADD', vIdx = iIdx,
                  tCounter = {{ vCurr = 0, vLim = table.getn(o.targets) }}, sObjective = o.name }}
elseif o.kind == 'subdue' then
-- capture alive (retail's subdue verb); killing them fails it
mission_ObjectiveSubdue{{ callbackFunc = foundry_TargetDown, killCallbackFunc = foundry_TargetKilled,
                         objectiveName = o.name, objectiveDesc = o.text, actorName = o.targets,
                         objectiveType = sObjType, targetType = sTargetType }}
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'ADD', vIdx = iIdx,
                  tCounter = {{ vCurr = 0, vLim = table.getn(o.targets) }}, sObjective = o.name }}
elseif o.kind == 'hold' then
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'ADD', vIdx = iIdx, sObjective = o.name }}
if o.x then
Objective_SetShortDescription(o.name, o.text)
Objective_SetType(o.name, sObjType)
Objective_SetTargetVector(o.name, o.zone, o.text, sTargetType, 'location', o.x, o.y, o.z, true, true, false)
end
Event_ActorInBoundary('foundry_HoldIn', 'b', 'player0', o.zone, true)
elseif o.kind == 'deliver' then
tFoundryWatch[Utility_GetActorAsInt(o.vehicle)] = o
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'ADD', vIdx = iIdx, sObjective = o.name }}
Objective_SetShortDescription(o.name, o.text)
Objective_SetType(o.name, sObjType)
Objective_SetTargetActor(o.name, o.vehicle, o.text, sTargetType, 'blip', true, true, true)
if o.x then
Objective_SetTargetVector(o.name, o.zone, o.text, sTargetType, 'location', o.x, o.y, o.z, true, true, false)
end
Event_ActorInBoundary('foundry_Delivered', 'b', o.vehicle, o.zone, true)
Event_ActorHitPointsLessThan('foundry_Died', 'b', o.vehicle, 1)
elseif o.kind == 'escort' then
o.arrived, o.dead, o.inZone = 0, 0, {{}}
for j, a in ipairs(o.targets) do
tFoundryWatch[Utility_GetActorAsInt(a)] = o
Ai_CommandDefault(a, 'Follow', 'player0', 'loPri')
Event_ActorInBoundary('foundry_EscortIn', 'b', a, o.zone, true)
Event_ActorHitPointsLessThan('foundry_Died', 'b', a, 1)
end
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'ADD', vIdx = iIdx,
                  tCounter = {{ vCurr = 0, vLim = table.getn(o.targets) }}, sObjective = o.name }}
if o.x then
Objective_SetShortDescription(o.name, o.text)
Objective_SetType(o.name, sObjType)
Objective_SetTargetVector(o.name, o.zone, o.text, sTargetType, 'location', o.x, o.y, o.z, true, true, false)
end
elseif o.kind == 'destroy' then
mission_ObjectiveDestroy{{ callbackFunc = foundry_TargetDown, objectiveName = o.name, objectiveDesc = o.text,
                          actorName = o.targets, objectiveType = sObjType, targetType = sTargetType }}
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'ADD', vIdx = iIdx,
                  tCounter = {{ vCurr = 0, vLim = o.need or table.getn(o.targets) }}, sObjective = o.name }}
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
foundry_Reveal('starts', o.name)
foundry_Waves('starts', o.name)
foundry_FailsFrom('starts', o.name)
foundry_Radio('starts', o.name)
foundry_Musics('starts', o.name)
foundry_Weathers('starts', o.name)
foundry_Orders('starts', o.name)
end

function foundry_Done(o)
o.done = true
foundry_Note(o.name .. ' done')
foundry_EndFail(o.name)
local sMsg, iIdx = foundry_Kind(o)
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'CMPL', vIdx = iIdx, sObjective = o.name }}
mission_RemoveObjective(o.name)
foundry_Reveal('done', o.name)
foundry_Waves('done', o.name)
foundry_FailsFrom('done', o.name)
foundry_Radio('done', o.name)
foundry_Musics('done', o.name)
foundry_Weathers('done', o.name)
foundry_Orders('done', o.name)
if o.bonus then
iFoundryEarned = iFoundryEarned + o.bonus
else
iFoundryGroupLeft = iFoundryGroupLeft - 1
if iFoundryGroupLeft <= 0 then foundry_NextObjective() end
end
end

function foundry_TargetDown(sActor, bComplete, sObjName)
local o = tFoundryActive[sObjName]
if not o or o.done then return end
local iAll = o.need or table.getn(o.targets)        -- "need": done after that many
o.down = o.down + 1
foundry_Note(o.name .. ': ' .. o.down .. ' of ' .. iAll .. ' down')
if o.down >= iAll then
foundry_Done(o)
else
local sMsg, iIdx = foundry_Kind(o)
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'UPD_POS', vIdx = iIdx,
                  tCounter = {{ vCurr = o.down, vLim = iAll }}, sObjective = o.name }}
end
end

-- Verify: a target was photographed dead or captured alive (bKilled false),
-- which pays the objective's "alive" extra.
function foundry_Verified(sActor, bKilled)
local o = tFoundryVerify[sActor]
if not o or o.done then return end
if not bKilled and o.alive then iFoundryEarned = iFoundryEarned + o.alive end
foundry_TargetDown(sActor, true, o.name)
end

function foundry_Arrived(playerActor, boundaryName)
-- the zone's started objectives (main or bonus) that aren't done yet
for sName, o in pairs(tFoundryActive) do
if o.kind == 'goto' and o.zone == boundaryName and not o.done then foundry_Done(o) end
end
end

-- The main objectives are done: bonus objectives shown and still open have
-- failed (ones never shown are left out), and the contract pays the flat
-- bonus plus the rewards earned.
-- After the contract completes (mission.lua calls it, as retail missions
-- unlock shop items there): the shop unlocks and the follow-up mail.
function post_MissionComplete()
if bFoundryMusic then foundry_NormalMusic() end
foundry_NormalWeather()
for i, s in ipairs(tFoundryUnlocks) do Shop_UnlockItem(s) end
if sFoundryDoneMail then Mail_Post(sFoundryDoneMail) end
end

function foundry_Finish()
if bFoundryOver then return end
if not bFoundryEndSaid then bFoundryEndSaid = true foundry_Radio('end') foundry_Musics('end') foundry_Weathers('end') end   -- the lines for the end
if iFoundryRadioPending > 0 then bFoundryFinishing = true return end   -- after the last radio line
local o = tFoundryFast
if o and not o.done then          -- finished in time
if o.hud then HUD_RemoveTrayDisplay(o.hud) o.hud = nil end
foundry_Done(o)
end
bFoundryOver = true
foundry_Note('COMPLETE, earned ' .. iFoundryEarned .. ' in bonuses')
foundry_StopTimer()
for i, o in ipairs(tFoundryBonus) do
if tFoundryActive[o.name] and not o.done then
util_ObjMsgPrint{{ sMsg = o.text, sType = 'BON_OBJ_FAIL', vIdx = 2, sObjective = o.name }}
mission_RemoveObjective(o.name)
end
end
if bFoundryPays then
Utility_WriteMissionData( {{ fBonusFee = iFoundryFlatBonus + iFoundryEarned }} )
end
for sFaction, n in pairs(tFoundryMood) do Faction_ModifyRelation(sFaction, HERO_FACTION, n) end
mission_MissionComplete()
end
"""


CONTRACTS_SCRIPT = "foundry_contracts"
# In the contracts script too (always loaded), so the log reads after the
# mission's own script has gone.
_LOG_READER = """function foundry_Log()
return Utility_ReadStringFromScribbleMemory('foundry_log') or 'no mission log yet'
end
"""
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


def layers_script(layers, pilots=(), arrive=False):
    """Lua for foundry_layers: load each mod layer marked "load": "map" when
    free roam starts on its map. A layer is loaded again only if it's gone:
    when one of its named objects no longer exists (a mission reset the
    map), or, for layers without named objects, once per script state.
    pilots: travel pilots on the free-roam maps (travel_lua), armed after
    the layers load; and a player back from another map is put where its
    pilot said (foundry_Arrive)."""
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
            "end"]
    if arrive or pilots:
        out += ["foundry_Arrive()"]
    if pilots:
        out += ["foundry_TravelArm()"]
    out += ["end"]
    text = "\n".join(out) + "\n"
    if arrive or pilots:
        text += ARRIVE_LUA
    if pilots:
        text += travel_lua("foundry_Travel", list(pilots))
    return text


def travel_lua(prefix, pilots, by_map=True):
    """Lua for travel pilots: characters you use to go to another map (as
    retail's quadrant pilot: a question, then Utility_SavePlayerData and
    Utility_ResetToBaseLayerOnly). pilots: [{map, actor, to, prompt, title,
    question, arrive?}]; arrive [x, y, z] is where the player is put on a
    free-roam map (foundry_Arrive, run when free roam starts there).
    by_map: arm only the pilots on the map free roam says it is (SkipTo_Map);
    a new map's own script arms all of its own. {prefix}Arm() arms them,
    retrying while a pilot's layer hasn't brought it yet."""
    P = prefix
    rows = []
    for t in pilots:
        arrive = t.get("arrive")
        rows.append("{ map = %s, actor = %s, to = %s, prompt = %s, title = %s, question = %s%s }," % (
            _lua(t["map"]), _lua(t["actor"]), _lua(t["to"]), _lua(t["prompt"]), _lua(t["title"]),
            _lua(t["question"]),
            ", ax = %.2f, ay = %.2f, az = %.2f, ah = %.1f" % (*(float(v) for v in arrive),
                                                              float(t.get("arrive_heading") or 0))
            if arrive else ""))
    out = [f"{P}List = {{"] + rows + ["}",
           f"if not {P}Armed then {P}Armed = {{}} end",
           f"{P}Waits = 0",
           f"function {P}Arm()",
           "local map = Utility_ReadStringFromScribbleMemory('SkipTo_Map')" if by_map else "local map = nil",
           "local waiting = false",
           f"for i, t in {P}List do",
           "if not map or t.map == map then",
           "if Utility_DoesActorExist(t.actor) then",
           "local iActor = Utility_GetActorAsInt(t.actor)",
           f"if {P}Armed[t.actor] ~= iActor then",
           f"{P}Armed[t.actor] = iActor",
           "Actor_EnableScriptedUse(t.actor, TRUE, t.prompt)",
           f"Event_ActorUsed('{P}Ask' .. i, 'b', t.actor)",
           "end",
           "else",
           "waiting = true   -- its layer hasn't brought it yet",
           "end",
           "end",
           "end",
           f"if waiting and {P}Waits < 15 then",
           f"{P}Waits = {P}Waits + 1",
           f"Event_RelativeTimer('{P}Arm', 'b', 1.0)",
           "else",
           f"{P}Waits = 0",
           "end",
           "end"]
    for i in range(1, len(pilots) + 1):
        out += [f"function {P}Ask{i}()",
                f"local t = {P}List[{i}]",
                f"{P}Armed[t.actor] = nil   -- used: re-armed if the player says no",
                f"Utility_QuestionBox(t.title, t.question, true, '{P}Go{i}', '{P}Arm')",
                "end",
                f"function {P}Go{i}()",
                f"local t = {P}List[{i}]",
                "Utility_SavePlayerData()",
                "if t.ax then",
                "Utility_WriteNumberToScribbleMemory('foundry_arrive_x', t.ax)",
                "Utility_WriteNumberToScribbleMemory('foundry_arrive_y', t.ay)",
                "Utility_WriteNumberToScribbleMemory('foundry_arrive_z', t.az)",
                "Utility_WriteNumberToScribbleMemory('foundry_arrive_h', t.ah)",
                "end",
                "if t.to == 'sw' or t.to == 'nw' then Utility_WriteStringToScribbleMemory('SkipTo_Map', t.to) end",
                "Utility_ResetToBaseLayerOnly(t.to)",
                "end"]
    return "\n".join(out) + "\n"


ARRIVE_LUA = """-- Back from another map: put the player where the pilot that brought them
-- said. Free roam puts the player at the HQ they last worked for
-- (Mission_PlayerRespawn) after this runs, so the move waits a moment, and
-- is made twice in case the first is overridden too.
function foundry_Arrive()
local x = Utility_ReadNumberFromScribbleMemory('foundry_arrive_x')
if x then
tFoundryArrive = { x, Utility_ReadNumberFromScribbleMemory('foundry_arrive_y'),
Utility_ReadNumberFromScribbleMemory('foundry_arrive_z'),
Utility_ReadNumberFromScribbleMemory('foundry_arrive_h') }
Utility_EraseValueFromScribbleMemory('foundry_arrive_x')
Utility_EraseValueFromScribbleMemory('foundry_arrive_y')
Utility_EraseValueFromScribbleMemory('foundry_arrive_z')
Utility_EraseValueFromScribbleMemory('foundry_arrive_h')
Cinematic_FadeOut(0)     -- black until the player is where they belong (not at the HQ first)
Event_RelativeTimer('foundry_ArriveNow', 'b', 0.5)
Event_RelativeTimer('foundry_ArriveLast', 'b', 2.0)
end
end
function foundry_ArriveNow()
if tFoundryArrive then
Actor_SetPosition('player0', tFoundryArrive[1], tFoundryArrive[2], tFoundryArrive[3])
if tFoundryArrive[4] then Actor_SetYaw('player0', tFoundryArrive[4]) end
end
end
function foundry_ArriveLast()
foundry_ArriveNow()
tFoundryArrive = nil
Cinematic_FadeIn(0.5)
end
"""


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


GIVER_MARKERS = ("gold", "primary", "secondary", "none")


def marker_type(contact, faction):
    """The target type a contact is marked with. At the HQ: the faction's, as
    the bouncers. A placed giver's "marker": "gold" (the HQ bouncers' ring,
    default), "primary" or "secondary" (objective markers) or "none"."""
    if not contact.get("position"):
        return "TARGET_TYPE_HQ_" + faction.upper()
    m = contact.get("marker") or "gold"
    if m not in GIVER_MARKERS:
        raise ValueError(f"the mission giver's \"marker\" is one of {', '.join(GIVER_MARKERS)}, not {m!r}")
    return {"gold": "TARGET_TYPE_HQ_" + faction.upper(), "primary": "TARGET_TYPE_OBJECTIVE_PRIMARY",
            "secondary": "TARGET_TYPE_OBJECTIVE_SECONDARY", "none": None}[m]


def wave_layer(asset_id, k):
    """The layer a mission's k-th later squad ("waves") goes in."""
    return f"{asset_id}_w{k}"


def wave_encounter(enc, k):
    """The encounter name of that squad in its own layer (a copy of its own)."""
    return f"{enc}_w{k}"


def split_waves(asset_id, mission_doc, layer_doc):
    """(the mission's layer without its later squads, [(layer, doc)]) for the
    mission file's "waves": [{"when": ..., "squad": "encounter:squad"}].
    Each squad moves to a layer of its own, with a copy of its encounter (same
    point sets) named encounter_w<k>; the script loads that layer at "when".
    The squad must be in the mission's own layer."""
    import copy
    waves = mission_doc.get("waves") or []
    if not waves:
        return layer_doc, []
    doc = copy.deepcopy(layer_doc)
    out = []
    for k, w in enumerate(waves, 1):
        enc, _, sq = str(w.get("squad") or "").rpartition(":")
        e = next((e for e in doc.get("encounters", []) if e.get("name", "").lower() == enc.lower()), None)
        s = next((s for s in (e or {}).get("squads", []) if s.get("name", "").lower() == sq.lower()), None)
        if not s:
            raise ValueError(f"missions/{asset_id}.json: wave {k}: no squad {w.get('squad')!r} in the mission's own layer "
                             f"(encounters/{asset_id}.json)")
        e["squads"] = [x for x in e["squads"] if x is not s]
        out.append((wave_layer(asset_id, k), {
            "map": doc.get("map"),
            "encounters": [{"name": wave_encounter(e["name"], k), "points": copy.deepcopy(e.get("points") or {}),
                            "squads": [s]}]}))
    return doc, out


def contact_actor(asset_id):
    """The actor name of a mission's contact (foundry_ContactName in the
    contracts script): a placed giver's object in its layer has this name."""
    return f"foundry_contact_{asset_id}"


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
            # Placed on the map instead (the editor's mission giver): x, y, z and
            # the way it faces (degrees, as the player start's heading).
            "position": [float(v) for v in c["position"]] if c.get("position") else None,
            "heading": float(c.get("heading", 0.0)),
            "prompt_key": prompt_key, "name_key": name_key, "desc_key": desc_key,
            # A contact at the HQ is marked like the bouncers; a giver placed on
            # the map gets an objective marker of its own (the HQ type moves the
            # faction's HQ icon on the map to it).
            "target_type": marker_type(c, faction),
            # the faction's HQ blip would move the faction's HQ icon on the map
            "blip": "blip" if c.get("position") else "hq" + faction,
            # A placed giver's side (Ai_SetFaction): civilian unless set, so a
            # character from another faction doesn't attack the player.
            "side": str(c.get("side") or "civ"),
            # Missions that must be done before this one is offered.
            "requires": [str(r).lower() for r in doc.get("requires") or []],
            # A placed giver's rules: stays where it's put (Ai_CommandQueue
            # 'Wait'), can't be hurt (Actor_SetInvincible), and whether killing
            # it loses the contract for good.
            "stay": bool(c.get("stay")) and bool(c.get("position")),
            "invincible": bool(c.get("invincible")) and bool(c.get("position")),
            "lose": bool(c.get("lose_if_killed")) and bool(c.get("position")),
            "title_key": title_key, "description_key": description_key}, strings


def _lua(s):
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def contracts_script(contracts, insertions=()):
    """Lua for foundry_contracts: spawn each contact on its map and start its
    mission (DebugSkipToMission, as the console skip does) when the player
    uses the contact and says yes. insertions: missions the faction's own HQ
    bouncer offers in its contract sequence (hqsequence.plan, with the
    title and description keys)."""
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
                   "targettype = %s, blip = %s%s }," % (
                       _lua(c["id"]), _lua(c["map"]), _lua(c["faction"]), c["number"],
                       "TRUE" if c["repeatable"] else "FALSE", _lua(c["template"]),
                       _lua(c["location"]), dx, dy, dz, c["yaw"], _lua(c["prompt_key"]), _lua(c["name_key"]),
                       _lua(c["desc_key"]), _lua(c["target_type"]) if c["target_type"] else "nil", _lua(c["blip"]),
                       (", px = %.2f, py = %.2f, pz = %.2f, heading = %.1f, side = %s" % (
                           *c["position"], c["heading"], _lua(c["side"])) if c["position"] else "")
                       + (", requires = { %s }" % ", ".join(_lua(r) for r in c["requires"]) if c["requires"] else "")
                       + "".join(f", {k} = true" for k in ("stay", "invincible", "lose") if c.get(k))))
    out.append("}")
    out += [
        "Utility_LoadUtilityScript('util_objectives')",
        "-- Campaign values a side contract must not change: every faction's arc",
        "-- position and completed-mission counters (mission_MissionComplete2), and",
        "-- the chapter. The contract runs at an appended arc position (> 3), so",
        "-- global's SetupChapter takes it for chapter 2 or 4 and, in chapter 1 or",
        "-- 3, resets the other factions to that chapter's first missions.",
        "function foundry_Kept(f)",
        "local t = { 'chapter', 'SkipTo_Chapter' }",
        "for i, g in { 'allies', 'china', 'mafia', 'sk' } do",
        "table.insert(t, 'current_' .. g .. '_mission') table.insert(t, 'total_missions_completed' .. g)",
        "table.insert(t, 'num_std_missions_cmpl_' .. g)",
        "end",
        "return t",
        "end",
        "tFoundryMarked = tFoundryMarked or {}   -- contact name -> the actor its marker was set up on",
        "tFoundryArmed = tFoundryArmed or {}     -- contact name -> the actor its use event is waiting on",
        "tFoundryGivers = tFoundryGivers or {}   -- a placed giver's actor (as int) -> its contract",
        "-- A placed giver's side, and no fighting. A character in a layer sleeps",
        "-- (dormant) until the player comes near, and a side set while it sleeps",
        "-- doesn't hold: set it again each time it wakes (as nw_sk4 does for its",
        "-- mole: Event_ActorIsDormant(..., FALSE)).",
        "-- Its rules: never a card to verify (an Ace template is verifiable),",
        "-- and, as set, it stays put (Wait with no time, as retail holds its",
        "-- helicopters) and can't be hurt.",
        "function foundry_GiverRules(name, c)",
        "Ai_SetFaction(name, c.side)",
        "Ai_SetHostileRange(name, 0.0)",
        "Actor_SetVerifiable(name, FALSE)",
        "if c.stay then Ai_CommandQueue(name, 'Wait', 'hiPri') end",
        "if c.invincible then Actor_SetInvincible(name, true) end",
        "end",
        "function foundry_GiverSide(name, c)",
        "foundry_GiverRules(name, c)",
        "tFoundryGivers[Utility_GetActorAsInt(name)] = c",
        "Event_ActorIsDormant('foundry_GiverAwake', 'b', name, FALSE)",
        "if c.lose then Event_ActorHitPointsLessThan('foundry_GiverKilled', 'b', name, 1) end",
        "end",
        "-- Killed, with \"lose_if_killed\": the contract is gone for good.",
        "function foundry_GiverKilled(aActor)",
        "local c = tFoundryGivers[Utility_GetActorAsInt(aActor)]",
        "if not c or not c.lose then return end",
        "Utility_WriteNumberToScribbleMemory('foundry_lost_' .. c.id, 1)",
        "Objective_Remove(foundry_ContactName(c))",
        "end",
        "function foundry_GiverAwake(aActor)",
        "local c = tFoundryGivers[Utility_GetActorAsInt(aActor)]",
        "if not c then return end",
        "local name = foundry_ContactName(c)",
        "foundry_GiverRules(name, c)",
        "Event_ActorIsDormant('foundry_GiverAsleep', 'b', name, TRUE)",
        "end",
        "function foundry_GiverAsleep(aActor)",
        "local c = tFoundryGivers[Utility_GetActorAsInt(aActor)]",
        "if c then Event_ActorIsDormant('foundry_GiverAwake', 'b', foundry_ContactName(c), FALSE) end",
        "end",
        "iFoundryWaits = iFoundryWaits or 0      -- seconds spent waiting for placed givers",
        "-- Offered only once the missions it requires are done.",
        "function foundry_Unlocked(c)",
        "if not c.requires then return true end",
        "for i, id in c.requires do",
        "if not Utility_ReadNumberFromScribbleMemory('foundry_done_' .. id) then return false end",
        "end",
        "return true",
        "end",
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
        "-- An HQ-sequence insertion (foundry_HqYes) ran at a retail arc position:",
        "-- completing it moved that position on by one (mission_MissionComplete2).",
        "local done = Utility_ReadStringFromScribbleMemory('JustCompletedStandardMission') == id",
        "local pos = Utility_ReadNumberFromScribbleMemory('foundry_ins_pos')",
        "if Utility_ReadStringFromScribbleMemory('foundry_ins_active') == id and pos "
        "and Utility_ReadNumberFromScribbleMemory('current_' .. f .. '_mission') == pos + 1 then done = true end",
        "for i, key in foundry_Kept(f) do",
        "local v = Utility_ReadNumberFromScribbleMemory('foundry_kept_' .. key)",
        "if v then Utility_WriteNumberToScribbleMemory(key, v) else Utility_EraseValueFromScribbleMemory(key) end",
        "Utility_EraseValueFromScribbleMemory('foundry_kept_' .. key)",
        "end",
        "if done then Utility_WriteNumberToScribbleMemory('foundry_done_' .. id, 1) end",
        "if Utility_ReadStringFromScribbleMemory('JustCompletedStandardMission') == id then",
        "Utility_EraseValueFromScribbleMemory('JustCompletedStandardMission')",
        "end",
        "Utility_EraseValueFromScribbleMemory('foundry_active_contract')",
        "Utility_EraseValueFromScribbleMemory('foundry_active_faction')",
        "for i, key in { 'foundry_ins_active', 'foundry_ins_faction', 'foundry_ins_pos', 'foundry_ins_num' } do",
        "Utility_EraseValueFromScribbleMemory(key)",
        "end",
        "end",
        "-- A placed giver stands in its own layer (<id>_giver), loaded with the",
        "-- map just before this runs; its actor can appear a moment later, so",
        "-- this looks again each second (up to 15 s) until it's there.",
        "function foundry_SetupContracts()",
        "local map = Utility_ReadStringFromScribbleMemory('SkipTo_Map')",
        "local bWaiting = false",
        "for i, c in tFoundryContracts do",
        "local name = foundry_ContactName(c)",
        "if c.map == map and ((foundry_IsDone(c) and not c.repeatable) or not foundry_Unlocked(c)",
        "or Utility_ReadNumberFromScribbleMemory('foundry_lost_' .. c.id)) then",
        "if Utility_DoesActorExist(name) then Objective_Remove(name) Actor_Remove(name) end",
        "elseif c.map == map then",
        "if not Utility_DoesActorExist(name) then",
        "if c.px then",
        "bWaiting = true   -- its layer hasn't brought it yet",
        "else",
        "local x, y, z = Location_GetPosition(c.location)",
        "-- face the way the location faces, as util_Mission_SpawnAtLocation does",
        "Mission_Spawn(c.template, name, x + c.dx, y + c.dy, z + c.dz, -Location_GetYaw(c.location) + c.yaw)",
        "end",
        "end",
        "if Utility_DoesActorExist(name) then",
        "-- marked like the HQ bouncers (ring, name above, HQ blip), once per",
        "-- appearance: a layer's giver is a new actor each time its layer loads",
        "local iActor = Utility_GetActorAsInt(name)",
        "if tFoundryMarked[name] ~= iActor then",
        "tFoundryMarked[name] = iActor",
        "if c.side then foundry_GiverSide(name, c) end   -- a placed giver: not hostile",
        "if c.targettype then",
        "util_SetupSingleTargetActorObjective(name, Utility_GetStringById(c.desc), '', 'OBJECTIVE_TYPE_PRIMARY', "
        "name, Utility_GetStringById(c.name), c.targettype, TRUE, TRUE, TRUE, c.blip)",
        "end",
        "end",
        "-- one use event per giver at a time (this runs again while waiting and",
        "-- after the player says no, which re-arms it: foundry_Use_*)",
        "if tFoundryArmed[name] ~= iActor then",
        "tFoundryArmed[name] = iActor",
        "Actor_EnableScriptedUse(name, TRUE, c.prompt)",
        "Event_ActorUsed('foundry_Use_' .. c.id, 'b', name)",
        "end",
        "end",
        "end",
        "end",
        "if bWaiting and iFoundryWaits < 15 then",
        "iFoundryWaits = iFoundryWaits + 1",
        "Event_RelativeTimer('foundry_SetupContracts', 'b', 1.0)",
        "else",
        "iFoundryWaits = 0",
        "end",
        "end"]
    for c in contracts:
        out += [
            f"function foundry_Use_{c['id']}()",
            f"tFoundryArmed[{_lua(contact_actor(c['id']))}] = nil   -- used: re-armed if the player says no",
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
    if insertions:
        from . import hqsequence
        out.append(hqsequence.lua_block(insertions).rstrip("\n"))
    out.append(_LOG_READER.rstrip("\n"))
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
