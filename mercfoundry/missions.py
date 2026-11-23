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

import math
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
        # the dateline retail shows on entering the HQ that offers it
        # (briefing_utilities: Ui_DisplayDateline('mail.<asset>.dateline'))
        strings.setdefault(f"mail.{asset_id.lower()}.dateline", f"'{doc['title']}'")
    if "description" in doc:
        strings[f"MissionInfo.Description.{key}"] = doc["description"]
        ini["ShortDescriptionDbKey"] = f"MissionInfo.Description.{key}"
    for i, o in enumerate(doc.get("objectives") or [], 1):
        strings[objective_key(asset_id, i)] = o.get("text") or f"Objective {i}"
        if o.get("type") in ("escort", "deliver", "subdue", "use", "defend"):
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


def player_yaw(heading):
    """Actor_SetYaw's degrees for a mod's heading. A heading (mission "start",
    travel "arrive_heading", an interior's room) is the way the player faces,
    0 along +z, as placed objects' yaw and the editor's markers. Actor_SetYaw
    passes degrees * pi/180 to the actor's own SetYaw (vtable +0x68), which
    for the player is the other way round: in the 0.199 playtest Actor_SetYaw
    180 faced +z. So the player is turned to heading + 180."""
    return (float(heading or 0) + 180.0) % 360.0


def _lua_str(s):
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


OBJECTIVE_TYPES = ("destroy", "goto", "escort", "deliver", "subdue", "hold", "use", "verify", "fast", "defend",
                   "collect", "kill")
# A verify objective's marker on its targets (retail's blipType: the card suits
# are the Deck of 52's).
VERIFY_BLIPS = ("human", "scope", "blip", "clubs", "diamond", "hearts", "spades")
FAIL_TYPES = ("dies", "timer", "leave", "alarm", "spotted", "hostile", "escapes")
# Squad orders (retail's own commands): attack the player, hold, move to one
# of the encounter's point sets, close in on the player, or walk a path.
ORDER_TYPES = ("attack", "hold", "move", "close", "path")
FAIL_TEXT = {"dies": "A target you had to protect was killed.", "timer": "You ran out of time.",
             "leave": "You left the mission area.", "escort": "Everyone you were escorting was killed.",
             "deliver": "The vehicle was destroyed.", "subdue": "The target was killed.",
             "use": "What you had to use was destroyed.", "alarm": "The alarm was raised.",
             "spotted": "You were spotted.", "hostile": "They turned on you.",
             "defend": "What you had to defend was destroyed.", "escapes": "The target got away."}


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
    if when == "near" and rest:
        return ("near", near_of(rest, what))
    raise ValueError(f"{what}: when is start, starts:<n>, done:<n>, zone:<name>, dead:<squad> or "
                     f"near:<metres>:<squad>, not {r!r}")


def near_of(rest, what):
    """"20:camp:Runner" (from "near:20:camp:Runner": the player comes within
    20 m of one of the squad) checked, as is."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?):([^:]+:[^:]+)", rest)
    if not m or float(m.group(1)) <= 0:
        raise ValueError(f"{what}: near is near:<metres>:<encounter:squad>, not near:{rest}")
    return f"{float(m.group(1)):g}:{m.group(2)}"


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
    if when == "near" and what:
        return ("near", near_of(what, f"objective {i}: \"reveal\""))
    raise ValueError(f"objective {i}: \"reveal\" is start, starts:<n>, done:<n>, zone:<name>, dead:<squad> or "
                     f"near:<metres>:<squad>, not {r!r}")


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
    near_sets = {}          # "<metres>:encounter:squad" a moment waits on -> its members

    def squad_after(when):
        """', after = ...' for a squad's moment: "dead:" or "near:"."""
        if when[0] == "dead":
            return dead_after(when[1])
        if when[1] not in near_sets:
            near_sets[when[1]] = squad_members(when[1].split(":", 1)[1])
        return f", after = {_lua_str(when[1])}"
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
            elif when[0] in ("dead", "near"):
                head += f", reveal = '{when[0]}'" + squad_after(when)
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
            if kind == "subdue" and o.get("deliver"):
                # or brought into a zone alive (carried, in a vehicle or dropped there)
                zone = o["deliver"]
                if not isinstance(zone, str) or not re.fullmatch(r"[A-Za-z0-9_\-]+", zone):
                    raise ValueError(f"objective {i}: \"deliver\" names the zone to bring the captive into")
                if zone_centre:
                    zone_centre(zone)                    # checks the zone (its layer loads with the script)
                row += f", dzone = {_lua_str(zone)}"
            if kind == "use":
                row += f", act = {_lua_str(use_prompt_key(asset_id, i))}"      # the prompt on the object
                if not o.get("breakable"):
                    row += ", tough = true"      # can't be destroyed (only used): "breakable": true for retail's way
            if kind == "verify":
                blip = o.get("blip") or "human"
                if blip not in VERIFY_BLIPS:
                    raise ValueError(f"objective {i}: \"blip\" is one of {', '.join(VERIFY_BLIPS)}, not {blip!r}")
                row += f", blip = '{blip}'"
                if alive_amount(o):
                    row += f", alive = {alive_amount(o)}"                    # paid for each one taken alive
                if o.get("card") is not None:
                    # a Deck of 52 card (a face card's contract): its state, the
                    # verify screen and its bounty, as the deck's number cards
                    from .deck import RANKS, SUITS
                    rank = RANKS.get(str(o["card"]).upper())
                    suit = str(o.get("suit") or "").lower()
                    if rank is None or suit not in SUITS:
                        raise ValueError(f"objective {i}: a card is \"card\": 2 to 10, J, Q, K or A, with "
                                         f"\"suit\": one of {', '.join(SUITS)}")
                    bounty = o.get("bounty", 0)
                    if not isinstance(bounty, (int, float)) or bounty < 0:
                        raise ValueError(f"objective {i}: the card's \"bounty\" is money (paid for a kill; "
                                         "taken alive pays double)")
                    row += f", suit = '{suit}', rank = {rank}, cardpay = {int(bounty)}"
            if kind == "escort":
                # they follow the player; done when every survivor is in the zone
                if not o.get("zone"):
                    raise ValueError(f"objective {i}: an escort objective needs a \"zone\" to bring them to")
                at = zone_centre(o["zone"]) if zone_centre and o.get("marker", True) else None
                mark = f", x = {at[0]:.2f}, y = {at[1]:.2f}, z = {at[2]:.2f}" if at else ""
                row += f", zone = {_lua_str(o['zone'])}{mark}, lost = {_lua_str(escort_lost_key(asset_id, i))}"
            row = f"{{ {row} }}"
        elif kind == "kill":
            # how many of some kinds of thing die (by model: Event_OdfHitPointsLessThan, as retail's
            # destroy challenges count them), wherever they are
            kinds = [str(k).strip().lower() for k in (o.get("kinds") or []) if str(k).strip()]
            if not kinds:
                raise ValueError(f"objective {i}: a kill objective needs \"kinds\" (model names, like "
                                 f"nk_hum_soldier)")
            try:
                need = int(float(o.get("need") or 0))
            except (TypeError, ValueError):
                need = 0
            if need < 1:
                raise ValueError(f"objective {i}: a kill objective needs \"need\" (how many), not {o.get('need')!r}")
            row = f"{{ {head}, kinds = {{ {', '.join(_lua_str(k) for k in kinds)} }}, need = {need} }}"
        elif kind in ("defend", "collect"):
            targets = list(o.get("targets") or [])
            if o.get("squad"):
                targets += squad_members(o["squad"])
            if not targets:
                raise ValueError(f"objective {i}: nothing to {kind} (give \"targets\"" + (" or \"squad\")" if kind == "defend" else ")"))
            row = f"{head}, targets = {{ {', '.join(_lua_str(t) for t in targets)} }}"
            need = len(targets)
            if o.get("need"):
                # defend: this many must live; collect: done after this many
                try:
                    need = int(float(o["need"]))
                except (TypeError, ValueError):
                    need = 0
                if not 1 <= need <= len(targets):
                    raise ValueError(f"objective {i}: \"need\" is 1 to {len(targets)}, not {o['need']!r}")
            if kind == "defend":
                # keep them alive while a clock counts down; done when it runs out
                seconds = float(o.get("seconds") or 0)
                if seconds <= 0:
                    raise ValueError(f"objective {i}: a defend objective needs \"seconds\"")
                row += f", seconds = {seconds:g}, keep = {need}, lost = {_lua_str(escort_lost_key(asset_id, i))}"
            elif need < len(targets):
                row += f", need = {need}"
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
        elif kind == "escapes":
            # one of them reaches a zone (a target running for it: a chase)
            targets = list(c.get("targets") or []) + (squad_members(c["squad"]) if c.get("squad") else [])
            if not targets or not c.get("zone"):
                raise ValueError(f"fail condition {k}: \"escapes\" needs \"targets\" or \"squad\", and the \"zone\" "
                                 f"they mustn't reach")
            if zone_centre:
                zone_centre(c["zone"])        # (its layer loads with the script)
            row += f", targets = {{ {', '.join(_lua_str(t) for t in targets)} }}, zone = {_lua_str(c['zone'])}"
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
            elif when[0] in ("dead", "near"):
                row += f", from = '{when[0]}'" + squad_after(when)
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
        elif when[0] in ("dead", "near"):
            row += squad_after(when)
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
        elif when[0] in ("dead", "near"):
            row += squad_after(when)
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
        elif when[0] in ("dead", "near"):
            row += squad_after(when)
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
    waves, wave_of, arrives = [], {}, {}
    for k, w in enumerate(doc.get("waves") or [], 1):
        what = f"wave {k}"
        if w.get("objects") is not None:
            # placed objects that appear later ("objects": [names]): a face
            # card's man only once his objective is up, as retail's card
            # missions bring theirs in
            names = [str(n) for n in w["objects"] or []]
            if not names or w.get("squad"):
                raise ValueError(f"{what}: give \"squad\" (\"encounter:squad\") or \"objects\" ([names]), not both")
            enc = sq = ""
        else:
            enc, _, sq = str(w.get("squad") or "").rpartition(":")
            if not enc or not sq:
                raise ValueError(f"{what}: \"squad\" is \"encounter:squad\" (or \"objects\": [names])")
            names = list(squad_members(w["squad"]) or [])
        for n in names:
            arrives[n] = wave_layer(asset_id, k)
        when = when_of(w.get("when"), len(objs), what)
        row = f"when = '{when[0]}'"
        if when[0] in ("starts", "done"):
            row += f", after = 'foundry{when[1]}'"
        elif when[0] in ("dead", "near"):
            row += squad_after(when)
        elif when[0] == "zone":
            row += f", zone = {_lua_str(when[1])}"
            if zone_centre:
                zone_centre(when[1])
        row += f", layer = {_lua_str(wave_layer(asset_id, k))}"
        row += delay_of(w, what, "tFoundryWaves", "foundry_Wave", k, "W")
        waves.append(f"{{ {row} }}")
        if sq:
            wave_of[(enc.lower(), sq.lower())] = (wave_encounter(enc, k), wave_layer(asset_id, k))
    wave_table = ",\n".join("  " + r for r in waves)
    # who arrives with which later layer: objectives and fail conditions on
    # them wait until it's up (foundry_WaitsFor)
    arrive_table = ", ".join(f"[{_lua_str(n)}] = {_lua_str(l)}" for n, l in sorted(arrives.items()))
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
        elif when[0] in ("dead", "near"):
            row += squad_after(when)
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
    later_lua_near = []
    dead_rows = []
    for name, members in dead_sets.items():
        enc, _, sq = name.rpartition(":")
        wave = wave_of.get((enc.lower(), sq.lower()))
        dead_rows.append(f"{{ squad = {_lua_str(name)}, members = {{ {', '.join(_lua_str(m) for m in members)} }}"
                         + (f", wave = {_lua_str(wave[1])}" if wave else "") + " }")
    dead_table = ",\n".join("  " + r for r in dead_rows)
    near_rows = []
    for k, (key, members) in enumerate(near_sets.items(), 1):
        metres, name = key.split(":", 1)
        enc, _, sq = name.rpartition(":")
        wave = wave_of.get((enc.lower(), sq.lower()))
        near_rows.append(f"{{ key = {_lua_str(key)}, metres = {metres}, cb = 'foundry_Near{k}', "
                         f"members = {{ {', '.join(_lua_str(m) for m in members)} }}"
                         + (f", wave = {_lua_str(wave[1])}" if wave else "") + " }")
        later_lua_near.append(f"function foundry_Near{k}() foundry_NearHit({k}) end")
    near_table = ",\n".join("  " + r for r in near_rows)
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
        loads += f"Actor_SetYaw('player0', {player_yaw(start.get('heading')):.1f})   -- heading {float(start.get('heading') or 0):g}\n"
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
tFoundryWaveOf = {{ {arrive_table} }}   -- who arrives with which of those layers
tFoundryWaveUp = {{}}       -- a wave's layer -> its squad is there
tFoundryOrders = {{        -- squad orders, given when their moment comes
{order_table}
}}
{later_lua}tFoundryDead = {{          -- squads a moment waits on ("dead:"): watched from the start, or once their wave is there
{dead_table}
}}
tFoundryDeadOf = {{}}       -- actor (as int) -> the squad it counts for
tFoundryNear = {{          -- squads a moment waits on the player coming near ("near:")
{near_table}
}}
{"".join(c + chr(10) for c in later_lua_near)}
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
-- The player comes within so many metres of one of a squad.
function foundry_WatchNear(sWave)
for i, n in ipairs(tFoundryNear) do
if n.wave == sWave and not n.watching then
n.watching = true
for j, a in ipairs(n.members) do Event_ActorToActorProximity(n.cb, 'b', a, 'player0', n.metres, true) end
end
end
end
function foundry_NearHit(i)
local n = tFoundryNear[i]
if not n or n.hit or bFoundryOver then return end
n.hit = true
foundry_Note('player near: ' .. n.key)
foundry_Reveal('near', n.key)
foundry_Waves('near', n.key)
foundry_FailsFrom('near', n.key)
foundry_Radio('near', n.key)
foundry_Musics('near', n.key)
foundry_Weathers('near', n.key)
foundry_Orders('near', n.key)
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
foundry_WatchNear(nil)
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
foundry_WatchNear(w.layer)
end
end
for i, o in ipairs(tFoundryOrders) do
if o.pending and tFoundryWaveUp[o.wave] then o.pending = nil foundry_Order(o) end
end
-- objectives and fail conditions waiting on who just arrived
for sName, o in pairs(tFoundryActive) do
if o.waitFor and not o.done and tFoundryWaveUp[o.waitFor] then
foundry_Note(o.name .. ': its targets are here')
foundry_Arm(o)
end
end
for i, f in ipairs(tFoundryFail) do
if f.waitFor and tFoundryWaveUp[f.waitFor] then f.waitFor = nil foundry_ArmFail(f) end
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
-- on someone who arrives later ("waves"): armed once they're there
local sWave = f.targets and foundry_WaitsFor(f.targets)
if sWave then f.waitFor = sWave foundry_Note('fail condition ' .. f.kind .. ' waits for ' .. sWave) return end
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
elseif f.kind == 'escapes' then
for j, a in ipairs(f.targets) do Event_ActorInBoundary(f.cb, 'b', a, f.zone, true) end
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
for sName, o in pairs(tFoundryActive) do
if o.kind == 'defend' and o.hud then HUD_RemoveTrayDisplay(o.hud) o.hud = nil end
end
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
elseif w.kind == 'defend' then foundry_DefendLost(w)
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
-- its targets arrive with a later layer (a "waves" entry, loaded at this
-- very moment, perhaps): wired up once they're there (foundry_WavesUp)
local sWave = o.targets and foundry_WaitsFor(o.targets)
if sWave then
o.waitFor = sWave
foundry_Note(o.name .. ' waits for ' .. sWave)
else
foundry_Arm(o)
end
foundry_Reveal('starts', o.name)
foundry_Waves('starts', o.name)
foundry_FailsFrom('starts', o.name)
foundry_Radio('starts', o.name)
foundry_Musics('starts', o.name)
foundry_Weathers('starts', o.name)
foundry_Orders('starts', o.name)
end
-- A later layer not yet up that one of these arrives with, or nil.
function foundry_WaitsFor(targets)
for j, a in ipairs(targets) do
local sLayer = tFoundryWaveOf[a]
if sLayer and not tFoundryWaveUp[sLayer] then return sLayer end
end
return nil
end
-- An objective's own set-up: its markers, events and message.
function foundry_Arm(o)
o.waitFor = nil
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
-- walk up and use it (retail's action verb); destroying it fails it, so unless
-- the objective is "breakable" the targets can't be destroyed
if o.tough then for j, a in ipairs(o.targets) do if Utility_DoesActorExist(a) then Actor_SetInvincible(a, true) end end end
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
if o.dzone then foundry_DeliverArm(o) end
elseif o.kind == 'subdue' then
-- capture alive (retail's subdue verb); killing them fails it. With "deliver"
-- subduing isn't enough: the captive must be brought into the zone
local fDown = foundry_TargetDown
if o.dzone then fDown = foundry_CaptiveTaken end
mission_ObjectiveSubdue{{ callbackFunc = fDown, killCallbackFunc = foundry_TargetKilled,
                         objectiveName = o.name, objectiveDesc = o.text, actorName = o.targets,
                         objectiveType = sObjType, targetType = sTargetType }}
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'ADD', vIdx = iIdx,
                  tCounter = {{ vCurr = 0, vLim = table.getn(o.targets) }}, sObjective = o.name }}
if o.dzone then foundry_DeliverArm(o) end
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
elseif o.kind == 'defend' then
-- keep them alive while a clock counts down (each marked); done when it runs out
o.dead = 0
for j, a in ipairs(o.targets) do
tFoundryWatch[Utility_GetActorAsInt(a)] = o
Event_ActorHitPointsLessThan('foundry_Died', 'b', a, 1)
foundry_Mark(o.name .. 'd' .. j, a, o.text, sObjType, sTargetType)
end
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'ADD', vIdx = iIdx, sObjective = o.name }}
o.hud = HUD_AddTrayDisplay{{ {{ DisplayClock = true, nObjStyle = HUD.STYLE.NORMAL, nStart = o.seconds, nLimit = 0,
                                fCallback = foundry_DefendDone }} }}
elseif o.kind == 'kill' then
-- so many of some kinds (models) killed, however they die (counted as retail's destroy challenges do)
o.got, o.events = 0, {{}}
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'ADD', vIdx = iIdx,
                  tCounter = {{ vCurr = 0, vLim = o.need }}, sObjective = o.name }}
for j, k in ipairs(o.kinds) do
table.insert(o.events, Event_OdfHitPointsLessThan('foundry_KindDown', 'b', k, 1, true))
end
elseif o.kind == 'collect' then
-- pick them up (each marked); done when enough are gone, looked at once a second
o.got, o.gone = 0, {{}}
for j, a in ipairs(o.targets) do foundry_Mark(o.name .. 'c' .. j, a, o.text, sObjType, sTargetType) end
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'ADD', vIdx = iIdx,
                  tCounter = {{ vCurr = 0, vLim = o.need or table.getn(o.targets) }}, sObjective = o.name }}
foundry_CollectTick()
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
end

-- A marker on one thing an objective is about (GPS, map and in the world).
function foundry_Mark(sName, aActor, sText, sObjType, sTargetType)
Objective_SetShortDescription(sName, sText)
Objective_SetType(sName, sObjType)
Objective_SetTargetActor(sName, aActor, sText, sTargetType, 'blip', true, true, true)
end

-- Defend: they live until the clock runs out (at least "keep" of them).
function foundry_DefendDone(hDisplay, eState)
if eState ~= HUD.STATE.LIMIT or bFoundryOver then return end
for sName, o in pairs(tFoundryActive) do          -- several can run at once: the clock says which
if o.kind == 'defend' and not o.done and o.hud == hDisplay then
foundry_DefendEnd(o)
foundry_Done(o)
return
end
end
end
function foundry_DefendEnd(o)
if o.hud then HUD_RemoveTrayDisplay(o.hud) o.hud = nil end
for j, a in ipairs(o.targets) do Objective_Remove(o.name .. 'd' .. j) end
end
function foundry_DefendLost(o)
o.dead = o.dead + 1
if o.done then return end
local left = table.getn(o.targets) - o.dead
foundry_Note(o.name .. ': one lost, ' .. left .. ' left')
if left >= o.keep then return end
o.done = true
foundry_DefendEnd(o)
if o.bonus then
util_ObjMsgPrint{{ sMsg = o.text, sType = 'BON_OBJ_FAIL', vIdx = 2, sObjective = o.name }}
mission_RemoveObjective(o.name)
else
foundry_Fail(o.lost)
end
end

-- Kill: a thing of a counted kind died (sOdf: its model; n: how many).
function foundry_KindDown(sOdf, fHp, bFlag, aActor, n)
if bFoundryOver then return end
for sName, o in pairs(tFoundryActive) do
if o.kind == 'kill' and not o.done then
for j, k in ipairs(o.kinds) do
if k == sOdf then
o.got = o.got + (n or 1)
foundry_Note(o.name .. ': ' .. o.got .. ' of ' .. o.need)
if o.got >= o.need then
for e, h in ipairs(o.events) do Event_CancelEvent(h) end
foundry_Done(o)
else
local sMsg, iIdx = foundry_Kind(o)
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'UPD_POS', vIdx = iIdx,
                  tCounter = {{ vCurr = o.got, vLim = o.need }}, sObjective = o.name }}
end
end
end
end
end
end

-- Subdue with "deliver": a captive alive and subdued (or riding as a
-- passenger) inside the zone counts, carried, in a vehicle or dropped
-- there; looked at each second while such an objective is on.
function foundry_DeliverArm(o)
o.got = {{}}
if not bFoundryDeliverTick then
bFoundryDeliverTick = true
Event_RelativeTimer('foundry_DeliverTick', 'b', 1.0)
end
end
function foundry_CaptiveTaken(sActor, bComplete, sObjName)
foundry_Note(sObjName .. ': ' .. sActor .. ' subdued; bring them into the zone')
end
function foundry_InZone(sActor, sZone)
local t = Boundary_GetEnclosingBoundaries(sActor)
if not t then return false end
for i, b in ipairs(t) do
if b == sZone then return true end
end
return false
end
function foundry_DeliverTick()
bFoundryDeliverTick = false
local waiting = foundry_DeliverSweep()
if waiting and not bFoundryOver then
bFoundryDeliverTick = true
Event_RelativeTimer('foundry_DeliverTick', 'b', 1.0)
end
end
-- Each "deliver" captive in its zone now counts; true while any such
-- objective is still open. Also run as the contract finishes, so a captive
-- riding in with the player as the last objective ends it isn't missed.
function foundry_DeliverSweep()
local waiting = false
for k, o in pairs(tFoundryActive) do
if o.dzone and o.got and not o.done then
waiting = true
for j, a in ipairs(o.targets) do
if not o.got[a] and Utility_DoesActorExist(a) and Actor_IsAlive(a)
and (Actor_IsSubdued(a) or Ai_IsPassenger(a)) and foundry_InZone(a, o.dzone) then
foundry_Note(o.name .. ': ' .. a .. ' brought into ' .. o.dzone)
foundry_TargetDown(a, true, o.name)
end
end
end
end
return waiting
end
-- Collect: picked up (gone from the world) counts; done after "need" (all).
function foundry_CollectTick()
local waiting = false
for sName, o in pairs(tFoundryActive) do
if o.kind == 'collect' and not o.done then
local got = 0
for j, a in ipairs(o.targets) do
if o.gone[j] or not Utility_DoesActorExist(a) then
got = got + 1
if not o.gone[j] then o.gone[j] = true Objective_Remove(o.name .. 'c' .. j) end
end
end
local all = o.need or table.getn(o.targets)
if got ~= o.got then
o.got = got
foundry_Note(o.name .. ': ' .. got .. ' of ' .. all .. ' collected')
if got >= all then
for j, a in ipairs(o.targets) do if not o.gone[j] then Objective_Remove(o.name .. 'c' .. j) end end
foundry_Done(o)
else
local sMsg, iIdx = foundry_Kind(o)
util_ObjMsgPrint{{ sMsg = o.text, sType = sMsg .. 'UPD_POS', vIdx = iIdx,
                  tCounter = {{ vCurr = got, vLim = all }}, sObjective = o.name }}
end
end
if not o.done then waiting = true end
end
end
if waiting and not bFoundryOver then Event_RelativeTimer('foundry_CollectTick', 'b', 1.0) end
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
if o.dzone then
-- "deliver": a captive counts once, flown out or brought into the zone
if o.got[sActor] then return end
o.got[sActor] = true
end
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
if o.rank then foundry_CardVerified(o, bKilled) end
foundry_TargetDown(sActor, true, o.name)
end
-- A Deck card verified ("card"): its state where the PDA reads it, and the
-- map's own when a mod's deck is shown (foundry_deck); the verify screen,
-- then its bounty (taken alive pays double), as the deck's number cards.
function foundry_CardVerified(o, bKilled)
local state = 'captured'
if bKilled then state = 'killed' end
local key = o.suit .. o.rank .. 'status'
Utility_WriteStringToScribbleMemory(key, state)
local m = Utility_ReadStringFromScribbleMemory('foundry_deck_on')
if m then
Utility_WriteStringToScribbleMemory('foundry_deck_' .. m .. '_' .. key, state)
-- the suit's intel meter fills from face cards too, as retail's
-- (gameflow_DeckCardVerified from mission_FaceCardVerified)
Utility_LoadUtilityScript('foundry_deck')
if foundry_DeckMeter then foundry_DeckMeter(o.suit) end
end
foundry_Note('card ' .. key .. ' ' .. state)
iFoundryCardPay = o.cardpay
if iFoundryCardPay and not bKilled then iFoundryCardPay = iFoundryCardPay * 2 end
Desktop_BeginVerify('foundry_CardPay', o.suit, o.rank, state, FALSE)
end
function foundry_CardPay()
if iFoundryCardPay and iFoundryCardPay > 0 then Player_AdjustMoney(iFoundryCardPay) end
iFoundryCardPay = nil
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

-- However the contract ends (done, failed, cancelled, retried), mission.lua's
-- mission_MissionCleanup calls this before closing the script: its markers and
-- use prompts outlive the script, and a contract played on a new map doesn't
-- reload the level after, so they'd stay on the targets (the world and the
-- radar). Every marker the objectives can have and every use prompt goes.
function pre_MissionCleanup()
foundry_StopTimer()
if bFoundryMusic then foundry_NormalMusic() end
foundry_NormalWeather()
for k, tList in ipairs({{ tFoundryObjectives, tFoundryBonus }}) do
for i, o in ipairs(tList) do
Objective_Remove(o.name)
if o.targets then
for j, a in ipairs(o.targets) do
Objective_Remove(o.name .. 'v' .. j)
Objective_Remove(o.name .. 'd' .. j)
Objective_Remove(o.name .. 'c' .. j)
if Utility_DoesActorExist(a) then Actor_EnableScriptedUse(a, FALSE) end
end
end
if o.vehicle and Utility_DoesActorExist(o.vehicle) then Actor_EnableScriptedUse(o.vehicle, FALSE) end
end
end
end

function foundry_Finish()
if bFoundryOver then return end
foundry_DeliverSweep()       -- a captive brought in with the player, before the bonuses settle
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


def welcome_mail(slug, m):
    """(mail id, sender, subject key, body key, strings, map) of a mod's
    welcome mail (mod.json "welcome_mail": {sender, subject, body, map?}),
    posted once per save the first time free roam starts on its map (sw by
    default). "sender" is a game sender (mail.senders.techgal, ...) or a
    name of your own, which becomes a string. Its strings go in the
    always-loaded missioninfo text, as done mails' do."""
    if not isinstance(m, dict) or not m.get("subject") or not m.get("body"):
        raise ValueError("mod.json: \"welcome_mail\" is {\"sender\", \"subject\", \"body\"}")
    key = "Welcome" + re.sub(r"[^A-Za-z0-9]", "", slug)
    base = f"MissionInfo.FoundryMail.{key}"
    strings = {f"{base}.Subject": str(m["subject"]), f"{base}.Body": str(m["body"])}
    sender = str(m.get("sender") or "mail.senders.techgal")
    if not re.fullmatch(r"(?i)mail\.[A-Za-z0-9_.]+", sender):
        strings[f"{base}.Sender"] = sender            # a name of the mod's own
        sender = f"{base}.Sender"
    where = str(m.get("map") or "sw").lower()
    if where not in FREE_ROAM_MAPS:
        raise ValueError(f"mod.json: \"welcome_mail\" \"map\" is {' or '.join(FREE_ROAM_MAPS)}, not {where!r}")
    return (f"foundry_welcome_{re.sub(r'[^a-z0-9]', '', slug.lower())}", sender, f"{base}.Subject", f"{base}.Body",
            strings, where)


WELCOME_DELAY = 5.0     # after free roam starts: FirstTimeInSW posts its help mails first


def welcome_lua(mails):
    """Lua (in foundry_layers) posting each welcome mail once per save: a
    scribble flag foundry_mail_<id> (saved with the game) marks it posted.
    The flag holds the mail's place in emails.ini (m[6], + 2; a save's
    inbox is positions there): a save that got it at another place (an
    older build's, now another mail) gets it again."""
    out = ["tFoundryWelcome = {"]
    out += ["{ id = %s, map = %s, posted = %d }," % (_lua(m[0]), _lua(m[5]), (m[6] if len(m) > 6 else 0) + 2)
            for m in mails]
    out += ["}",
            "function foundry_Welcome()",
            "local map = Utility_ReadStringFromScribbleMemory('SkipTo_Map')",
            "for i, m in tFoundryWelcome do",
            "if m.map == map then",
            f"Event_RelativeTimer('foundry_WelcomePost', 'b', {WELCOME_DELAY:g})",
            "return",
            "end",
            "end",
            "end",
            "function foundry_WelcomePost()",
            "local map = Utility_ReadStringFromScribbleMemory('SkipTo_Map')",
            "for i, m in tFoundryWelcome do",
            "if m.map == map then",
            "-- every time: Mail_Post does nothing for a mail already in the inbox",
            "-- (0xCE010 asks 0xCC840 first), and a trip to another map can drop it",
            "-- from the inbox until the save is loaded again",
            "Utility_WriteNumberToScribbleMemory('foundry_mail_' .. m.id, m.posted)",
            "Mail_Post(m.id)",
            "end",
            "end",
            "end"]
    return "\n".join(out) + "\n"


HQ_FACTIONS = ("allies", "china", "mafia", "sk")


def hq_flags_lua(prefix, hqs, skip=()):
    """Lua for a new map's faction HQ flags (maps/<name>.json "hqs":
    {faction: [x, y, z]}): an objective at each point with the faction's HQ
    target type and blip, as the HQ bouncers are marked on SW and NW (radar,
    PDA map, no marker in the world). {prefix}HqFlags() sets them up, and
    again 2 s later, in case anything starting the map clears them. skip:
    factions left out.
    The call is Objective_SetTargetVector(name, target, text, type, blip,
    x, y, z, gps, pda, world), as its Lua wrapper (0x114880) reads it and
    sw_sk2 calls it; util_objectives' region helper passes x, y, z before
    the blip, which no retail script calls."""
    out = [f"function {prefix}HqFlags()"]
    for f in HQ_FACTIONS:
        if f not in hqs or f in skip:
            continue
        x, y, z = (float(v) for v in hqs[f])
        name = f"foundry_hq_{f}"
        out += [f"Objective_SetShortDescription({_lua(name)}, Utility_GetStringById('standardMission.bouncer.{f}.shortDesc'))",
                f"Objective_SetLongDescription({_lua(name)}, '')",
                f"Objective_SetType({_lua(name)}, 'OBJECTIVE_TYPE_PRIMARY')",
                f"Objective_SetTargetVector({_lua(name)}, {_lua(name)}, "
                f"Utility_GetStringById('standardMission.bouncer.{f}.tgtCaption'), 'TARGET_TYPE_HQ_{f.upper()}', "
                f"'hq{f}', {x:.2f}, {y:.2f}, {z:.2f}, TRUE, TRUE, FALSE)"]
    out += [f"if not {prefix}HqFlagsAgain then",
            f"{prefix}HqFlagsAgain = true",
            f"Event_RelativeTimer('{prefix}HqFlagsLater', 'b', 2.0)",
            "end",
            "end",
            f"function {prefix}HqFlagsLater()",
            f"{prefix}HqFlagsAgain = true   -- once: no further timer",
            f"{prefix}HqFlags()",
            f"{prefix}HqFlagsAgain = nil",
            "end"]
    return "\n".join(out) + "\n"


def hq_flags(where, hqs):
    """maps/<name>.json "hqs" checked: {faction: [x, y, z]}."""
    if not isinstance(hqs, dict):
        raise ValueError(f"{where}: \"hqs\" is {{\"allies\": [x, y, z], ...}}")
    out = {}
    for f, pos in hqs.items():
        if f not in HQ_FACTIONS:
            raise ValueError(f"{where}: \"hqs\": {f!r} isn't one of {', '.join(HQ_FACTIONS)}")
        if not isinstance(pos, (list, tuple)) or len(pos) != 3:
            raise ValueError(f"{where}: \"hqs\" {f}: the HQ is [x, y, z]")
        out[f] = [float(v) for v in pos]
    return out


def _lines(where, lines):
    """Subtitled lines [{speaker?, text, seconds?, sound?}] as Lua rows."""
    rows = []
    for k, l in enumerate(lines or [], 1):
        if isinstance(l, str):
            l = {"text": l}
        if not isinstance(l, dict) or not l.get("text"):
            raise ValueError(f"{where} line {k}: a line is {{\"speaker\", \"text\", \"seconds\"}}")
        secs = float(l.get("seconds") or max(3.0, len(str(l["text"])) / 15.0))
        text = (f"{l['speaker']}: " if l.get("speaker") else "") + str(l["text"])
        sound = l.get("sound")
        if sound and not re.fullmatch(r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+", str(sound)):
            raise ValueError(f"{where} line {k}: \"sound\" is <bank>.<cue>, like fio.S10START, not {sound!r}")
        rows.append("{ text = %s, seconds = %g%s }" % (_lua_str(text), secs,
                                                       f", sound = {_lua(sound)}" if sound else ""))
    return "{ " + ", ".join(rows) + " }"


def interior_guard(map_name, faction):
    """The actor name of a new map's HQ guard (the interior's way in)."""
    return f"foundry_hqguard_{map_name.lower()}_{faction}"


def interiors(where, map_name, doc):
    """maps/<name>.json "interiors" checked: {faction: {guard: {template?,
    position, heading?, name?, prompt?, side?, marker?}, room: {position,
    heading?}, leaders: [actor], starter?: actor | false, accept_prompt?,
    bribe?: true | false | {cost}, exit: {object?, prompt?, delay?},
    greeting: [line], idle: [line]}}."""
    raw = doc.get("interiors") or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: \"interiors\" is {{faction: {{guard, room, leaders, ...}}}}")
    out = {}
    for f, i in raw.items():
        w = f"{where}: interiors {f}"
        if f not in HQ_FACTIONS:
            raise ValueError(f"{w}: a faction is one of {', '.join(HQ_FACTIONS)}")
        g, room = i.get("guard") or {}, i.get("room") or {}
        for part, d in (("guard", g), ("room", room)):
            p = d.get("position")
            if not isinstance(p, (list, tuple)) or len(p) != 3:
                raise ValueError(f"{w}: \"{part}\" needs a \"position\" [x, y, z]")
        leaders = [str(a) for a in i.get("leaders") or []]
        exit_ = i.get("exit") or {}
        if not isinstance(exit_, dict):
            raise ValueError(f"{w}: \"exit\" is {{\"object\": <name>, \"prompt\": ...}}")
        exit_pos = exit_.get("position")
        if exit_pos is not None and (not isinstance(exit_pos, (list, tuple)) or len(exit_pos) != 3):
            raise ValueError(f"{w}: the exit's \"position\" is [x, y, z]")
        door = str(exit_.get("object") or ("" if exit_pos else (leaders[0] if leaders else "")))
        if not door and not exit_pos:
            raise ValueError(f"{w}: give an \"exit\" {{\"object\": <name>}} or {{\"position\": [x, y, z]}}, "
                             f"or \"leaders\" (the first one shows the way out)")
        marker = str(g.get("marker") or "hq")
        if marker not in ("hq", "none"):
            raise ValueError(f"{w}: the guard's \"marker\" is hq or none, not {marker!r}")
        # the second in command (retail's "starter"): using them accepts the
        # contract; the second leader unless set, none with false
        starter = i.get("starter", leaders[1] if len(leaders) > 1 else None)
        starter = str(starter) if starter else ""
        bribe = i.get("bribe", True)
        if bribe is not True and bribe is not False and not (isinstance(bribe, dict) and set(bribe) <= {"cost"}
                                                             and isinstance(bribe.get("cost", 0), (int, float))):
            raise ValueError(f"{w}: \"bribe\" is true, false or {{\"cost\": <money>}}")
        bribe_cost = int(bribe["cost"]) if isinstance(bribe, dict) and "cost" in bribe else None
        # The camera inside: retail never shows the follow camera during a
        # briefing (the player sits; the camera plays the room's authored
        # shot), and a follow camera in a room on the map is pushed behind
        # its walls in tight spots. A fixed spot in the room: "camera"
        # {"position"}, else 1.2 m behind and 1.8 m above where the player
        # stands, along their heading.
        cam = i.get("camera") or {}
        if not isinstance(cam, dict) or (cam.get("position") is not None and (
                not isinstance(cam["position"], (list, tuple)) or len(cam["position"]) != 3)):
            raise ValueError(f"{w}: \"camera\" is {{\"position\": [x, y, z]}} (a spot inside the room)")
        if cam.get("position"):
            cam_pos = [float(v) for v in cam["position"]]
        else:
            h = math.radians(float(room.get("heading") or 0))
            rp = [float(v) for v in room["position"]]
            cam_pos = [rp[0] - 1.2 * math.sin(h), rp[1] + 1.8, rp[2] - 1.2 * math.cos(h)]
        # Retail's HQ visit (docs/HQS.md): the room's ambience with the music
        # stopped, the HUD hidden (its lines are voiced: keep the HUD for
        # message-box lines), a greeting by the faction's mood on every visit,
        # the boss nagging while the contract waits, and the bouncer's banter
        # and mood animations.
        ambience = i.get("ambience")
        if ambience is not None and not re.fullmatch(r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+", str(ambience)):
            raise ValueError(f"{w}: \"ambience\" is a sound cue <bank>.<cue>, like ambient.hq_un_war")
        moods = i.get("greetings") or {}
        if not isinstance(moods, dict) or set(moods) - {"friendly", "neutral", "unfriendly"}:
            raise ValueError(f"{w}: \"greetings\" is {{\"friendly\": [line], \"neutral\": [...], \"unfriendly\": [...]}}")
        banter = i.get("banter")
        if banter is True:
            banter = [f"{BANTER_BANKS[f]}{n}" for n in range(1, 6)] if f in BANTER_BANKS else []
        if banter in (None, False):
            banter = []
        if not isinstance(banter, list) or not all(
                isinstance(b, str) and re.fullmatch(r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+", b) for b in banter):
            raise ValueError(f"{w}: \"banter\" is true (the faction's retail bouncer lines) or [<bank>.<cue>, ...]")
        guard_template = str(g.get("template") or f"template_{f}_bouncer")
        guard_moods = i.get("guard_moods", guard_template.lower().endswith("_bouncer"))
        respawn = f in (doc.get("hq_respawn") or {})
        chair = _interior_chair(w, f, i.get("chair"))
        follow = _interior_follow(w, cam, room)
        exit_delay = exit_.get("delay", 5)
        exit_distance = exit_.get("distance", 3)
        if not isinstance(exit_distance, (int, float)) or exit_distance <= 0:
            raise ValueError(f"{w}: the exit's \"distance\" is metres (how near you use it; 3 unless set)")
        if not isinstance(exit_delay, (int, float)) or exit_delay < 0:
            raise ValueError(f"{w}: the exit's \"delay\" is seconds (5 unless set)")
        out[f] = {"guard": interior_guard(map_name, f),
                  "template": str(g.get("template") or f"template_{f}_bouncer"),
                  "gpos": [float(v) for v in g["position"]], "gheading": float(g.get("heading") or 0),
                  "name": str(g.get("name") or f"{FACTION_NAMES[f]} HQ"),
                  "prompt": str(g.get("prompt") or "Enter HQ"), "side": str(g.get("side") or "civ"),
                  "marker": marker,
                  "room": [float(v) for v in room["position"]], "rheading": float(room.get("heading") or 0),
                  "camera": cam_pos,
                  "leaders": leaders, "door": door, "door_prompt": str(exit_.get("prompt") or "Leave"),
                  "exit_delay": float(exit_delay), "exit_distance": float(exit_distance),
                  "exit_position": [float(v) for v in exit_pos] if exit_pos else None,
                  "starter": starter, "accept_prompt": str(i.get("accept_prompt") or "Accept contract"),
                  "bribe": bribe is not False, "bribe_cost": bribe_cost,
                  "talk_prompt": str(i.get("talk_prompt") or "Talk"),
                  "greeting": _lines(f"{w} greeting", i.get("greeting")),
                  "greetings": {m: _lines(f"{w} greetings {m}", moods[m]) for m in ("friendly", "neutral", "unfriendly")
                                if moods.get(m)},
                  "nags": _lines(f"{w} nags", i.get("nags")),
                  "ambience": str(ambience) if ambience else None, "hide_hud": bool(i.get("hide_hud")),
                  "banter": list(banter), "guard_moods": bool(guard_moods), "respawn": respawn,
                  "chair": chair, "follow": follow,
                  "idle": _lines(f"{w} idle", i.get("idle") or [{"text": "Nothing for you right now. Come back later."}])}
    return out


# Retail's HQ chairs (briefing_interactions.lua, INTERACTIONTYPE_THREESTAGE):
# the player's sit-down, seated loop and stand-up clips and the first and
# last clips' lengths, by faction. They load with the briefing's layers
# (<faction>_briefing's [animation]); a map with a chair loads them itself.
CHAIR_STYLES = {
    "allies": ("allies_interact_sitdown_chair_player", "allies_interact_sitting_chair_player",
               "allies_interact_standup_chair_player", 2.533, 1.833),
    "china": ("china_interact_sitdown_chair_player", "china_interact_sitting_chair_player",
              "china_interact_standup_chair_player", 2.667, 2.0),
    "mafia": ("mafia_sitdownchair_player", "mafia_sittingchair_player", "mafia_getupchair_player", 2.667, 2.333),
    "sk": ("sk_interact_sitdown_chair_player", "sk_interact_sitting_chair_player",
           "sk_interact_getup_chair_player", 3.0, 1.667),
}


# Retail's chairs, placed by the build when an interior names none: its
# briefing attaches the chair and the player to the same hardpoint
# (Briefing_AddActor(chair, hp_player_chair), Actor_AttachToActor(player,
# scene, hp_player_chair)), and the sit clip carries the player into the seat
# (its bone_root ends 0.44-0.46 m behind the standing spot, where china's,
# sk's and the centred mafia chair's seats are). The allies' chair is a rig:
# its seat starts 0.94 m to the side and its own clips swing it under the
# player. Mafia's is attached at its own hp_chair: the seat's centre is put
# 0.44 m behind the spot.
CHAIR_TEMPLATES = {"allies": "template_hq-allies-chair", "china": "template_hq-china-chair",
                   "mafia": "template_hq-mafia-chair", "sk": "template_hq-sk-chair"}
CHAIR_BEHIND = {"mafia": 0.44}
# The allies' rig, left at rest (its own clips swing the seat under the
# player in retail's scene; on a map they left it 1 m off in the 0.241
# playtest): the seat's centre at rest, in the rig's frame (x, z), facing the
# rig's +x; and where allies_interact_sitdown_chair_player leaves the hips
# (bone_root), in the player's frame. The chair is turned to face the way the
# player does and moved so its seat is under the hips.
ALLIES_SEAT_REST = (0.94, -0.42)
ALLIES_HIPS = (-0.07, -0.64)
CHAIR_RIG_CLIPS = {}
# The allies' rig with its seat (k_chair) where allies_interact_sitting_chair_chair
# holds it: a placed copy doesn't play the rig's clips (0.241), so the build
# draws the chair as it is while the player sits.
SEATED_ALLIES_CHAIR = "foundry_allieshq_chair_seated"
SEATED_ALLIES_POSE = ((-0.002, -0.703, 0.001, 0.711), (-0.047, 0.72, -0.902))


def _turn(yaw_deg, x, z):
    """A prop's frame turned to yaw (0 faces +z): its (x, z) in the world."""
    a = math.radians(yaw_deg)
    return (x * math.cos(a) + z * math.sin(a), -x * math.sin(a) + z * math.cos(a))


def chair_placement(chair):
    """(template, position, yaw) of the chair the build places for an
    interior's "chair" with no "object": retail's chair of its style at the
    player's spot reading the player's yaw (retail's, measured). The allies'
    rig is drawn seated (SEATED_ALLIES_CHAIR); mafia's, attached at its own
    hardpoint in retail, 0.44 m behind (not yet measured)."""
    x, y, z = chair["position"]
    # The player's frame, in a prop's yaw. Measured (0.242-0.245 hq dumps): a
    # prop placed at yaw Y reads Actor_GetYaw 180 - Y (90 read 90, 0 read
    # 180), and the player turned with Actor_SetYaw(heading + 180) reads
    # heading + 180. Retail's chair reads the player's yaw: Y = -heading.
    frame = (-chair["heading"]) % 360.0
    if chair["style"] == "allies":
        # measured in retail's allies HQ briefing (0.244's hq dump): the chair
        # and the player at one spot, with the same yaw (the player's
        # Actor_SetYaw: heading + 180). Drawn by SEATED_ALLIES_CHAIR, the rig
        # with its seat where its sitting clip holds it
        return CHAIR_TEMPLATES["allies"], [x, y, z], frame
    h = math.radians(chair["heading"])
    back = CHAIR_BEHIND.get(chair["style"], 0.0)
    return (CHAIR_TEMPLATES[chair["style"]], [x - back * math.sin(h), y, z - back * math.cos(h)], frame)


def _interior_chair(w, faction, c):
    """An interior's "chair": {"object": <placed chair>, "position": [x, y, z]
    (where the player sits: their feet at the seat's spot), "heading"? (the
    way they face, seated), "style"? (whose HQ animations: allies, china,
    mafia, sk; the interior's faction), "auto"? (seated on coming in, as
    retail; true), "sit_prompt"?, "stand_prompt"?}, or None."""
    if c is None or c is False:
        return None
    if not isinstance(c, dict):
        raise ValueError(f"{w}: \"chair\" is {{\"object\": <name>, \"position\": [x, y, z], \"heading\": <deg>}}")
    # "object": a chair the mod placed itself; none: the build places retail's
    # (chair_placement), named foundry_hqchair_<map>_<faction> by the maps step
    obj = c.get("object") or ""
    p = c.get("position")
    if not isinstance(p, (list, tuple)) or len(p) != 3:
        raise ValueError(f"{w}: the chair needs a \"position\" [x, y, z] (where the player sits)")
    style = str(c.get("style") or faction)
    if style not in CHAIR_STYLES:
        raise ValueError(f"{w}: the chair's \"style\" is one of {', '.join(CHAIR_STYLES)} (whose HQ animations)")
    sit, loop, stand, d1, d3 = CHAIR_STYLES[style]
    return {"object": str(obj), "place": not obj, "position": [float(v) for v in p],
            "heading": float(c.get("heading") or 0),
            "style": style, "auto": bool(c.get("auto", True)), "clips": [sit, loop, stand], "durations": [d1, d3],
            # the allies' rig turns under the player with clips of its own (only on retail's chair)
            "rig": list(CHAIR_RIG_CLIPS.get(style, ())) if not obj else [],
            "sit_prompt": str(c.get("sit_prompt") or "actions.sitdown"),
            "stand_prompt": str(c.get("stand_prompt") or "actions.standup")}


def _interior_follow(w, cam, room):
    """The camera inside, as retail's once the player stands (its follow
    camera, which retail's rooms keep inside their walls): "camera"
    {"mode": "follow" (unless "fixed"), "distance"? (2.5 m), "height"? (1.7 m
    above the player's feet), "bounds"? [[x, z], [x, z]] (the floor's
    corners: the camera stays 0.3 m inside them)}. None for "fixed" (the
    0.231 camera at "position")."""
    mode = str(cam.get("mode") or "follow")
    if mode not in ("follow", "fixed"):
        raise ValueError(f"{w}: the camera's \"mode\" is follow (behind the player, as retail's) or fixed")
    if mode == "fixed":
        return None
    # retail's follow camera stands about 4 m back and 0.8-1.9 m up, looking
    # at the player's head; in a 12 x 10 m room 3 m back and 2.1 m up, looking
    # at the chest (1.3 m), keeps them whole in view under a 3.5 m ceiling
    dist, height = cam.get("distance", 3.0), cam.get("height", 2.1)
    if not isinstance(dist, (int, float)) or not 0.5 <= dist <= 8 or not isinstance(height, (int, float)) \
            or not 0 <= height <= 6:
        raise ValueError(f"{w}: the camera's \"distance\" is 0.5 to 8 m and \"height\" 0 to 6 m")
    b = cam.get("bounds")
    if isinstance(b, (list, tuple)) and any(p is None for p in b):
        b = None                    # one corner set so far (the editor's): not kept in yet
    if b is not None:
        if not (isinstance(b, (list, tuple)) and len(b) == 2 and all(
                isinstance(p, (list, tuple)) and len(p) in (2, 3) for p in b)):
            raise ValueError(f"{w}: the camera's \"bounds\" is the floor's corners [[x, z], [x, z]]")
        # [x, z] or [x, y, z] (the editor's points)
        xs = sorted(float(p[0]) for p in b)
        zs = sorted(float(p[-1]) for p in b)
        b = [xs[0] + 0.3, zs[0] + 0.3, xs[1] - 0.3, zs[1] - 0.3]
        if b[0] > b[2] or b[1] > b[3]:
            raise ValueError(f"{w}: the camera's \"bounds\" is too small a floor (under 0.6 m)")
    h = math.radians(float(room.get("heading") or 0))
    look = cam.get("look", 1.3)
    if not isinstance(look, (int, float)) or not 0 <= look <= 3:
        raise ValueError(f"{w}: the camera's \"look\" is 0 to 3 m (the height on the player it looks at)")
    return {"distance": float(dist), "height": float(height), "look": float(look), "bounds": b,
            "facing": [math.sin(h), math.cos(h)]}


def _follow_row(fw):
    if not fw:
        return "false"
    b = fw["bounds"]
    return ("{ dist = %.2f, up = %.2f, look = %.2f, fx = %.4f, fz = %.4f%s }"
            % (fw["distance"], fw["height"], fw.get("look", 1.3), *fw["facing"],
               ", x0 = %.2f, z0 = %.2f, x1 = %.2f, z1 = %.2f" % tuple(b) if b else ""))


def _chair_row(c):
    if not c:
        return "false"
    h = math.radians(c["heading"])
    return ("{ obj = %s, x = %.2f, y = %.2f, z = %.2f, yaw = %.1f, fx = %.4f, fz = %.4f, sit = %s, loop = %s, "
            "stand = %s, dsit = %.3f, dstand = %.3f, auto = %s, sitprompt = %s, standprompt = %s%s }"
            % (_lua(c["object"]), *c["position"], player_yaw(c["heading"]), math.sin(h), math.cos(h),
               *(_lua(x) for x in c["clips"]), *c["durations"], "TRUE" if c["auto"] else "FALSE",
               _lua_str(c["sit_prompt"]), _lua_str(c["stand_prompt"]),
               (", rig1 = %s, rig2 = %s, rig3 = %s" % tuple(_lua(x) for x in c["rig"])) if c.get("rig") else ""))


def chair_clips(inside):
    """The animations a map's interiors' chairs play (for its load list)."""
    return sorted({clip for i in inside.values() if i.get("chair")
                   for clip in i["chair"]["clips"] + i["chair"].get("rig", [])})


# Retail's bouncer banter (bouncer.lua BouncerUsed): <bank>.<cue prefix>, 1-5
BANTER_BANKS = {"allies": "aso.bouaso0", "sk": "sks.bousks0", "china": "cso.boucso0", "mafia": "mso.boumso0"}


def interiors_lua(prefix, inside, flagged=()):
    """Lua for a new map's HQ interiors, made to play as retail's HQs
    (bouncer.lua, briefing.lua, briefing_utilities.lua):

    - Using the HQ guard fades to black and puts the player in the room
      (where they stood is kept). A guard whose faction is hostile to the
      player offers a bribe instead, as the bouncers do (util_GetBribeCost
      for the chapter, or the interior's "bribe" cost; paid, the faction's
      mood goes to util_GetPostBribeMood and the guard lets you in).
    - Inside, the player has retail's briefing controls: control mode 13
      (move, look and use; no firing or weapon switching; Briefing_Init
      sets the same), and with Foundry Console the weapon put away and
      walking pace (Foundry_HqMode, the rest of Briefing_Init's player
      set-up). Both are put back on leaving or taking the contract.
    - The leaders stand where they are and never fight: each is under
      cinematic control (as retail's briefing boss and starter) and looks
      at the player, set once per actor (one that comes back is a new
      actor), with no hostile range and invincible.
    - The greeting plays on the first visit (per save: scribble
      foundry_hq_greeted_<faction>), then the faction's next contract here ("contact":
      {"interior": faction}, from foundry_contracts' table) has its
      briefing; then the second in command ("starter") gets a gold ring and
      the accept prompt, as retail's starter_trigger; using them starts the
      contract. With no starter, a yes/no box. With no contract, the idle
      lines. Using the first leader (when it isn't the way out) plays the
      briefing again.
    - The way out gets its ring and prompt after the exit's delay (retail:
      5 s); using it fades and puts the player back by the guard.

    {prefix}HqArm() arms the guards and leaders and keeps looking (each
    second for 15 s, then every 5 s, for as long as free roam lasts): a
    guard far from the player may not exist yet, and one that comes back
    (dormant, reloaded) is a new actor to arm again. A guard is armed
    again too when its faction turns hostile or stops being hostile.
    flagged: factions with an HQ point flag ("hqs"): their guard gets a
    ring and name in the world only, not a second HQ blip; others carry
    the HQ flag themselves, as the bouncers do."""
    P = prefix
    rows = []
    for f, i in inside.items():
        rows.append(
            "%s = { guard = %s, prompt = %s, name = %s, side = %s, marker = %s, ttype = %s, blip = %s, rx = %.2f, ry = %.2f, rz = %.2f, "
            "rh = %.1f, leaders = { %s }, door = %s, doorprompt = %s, doordist = %.1f, exitdelay = %.1f, talkprompt = %s,\n"
            " starter = %s, acceptprompt = %s, bribe = %s, bribecost = %s,%s cx = %.2f, cy = %.2f, cz = %.2f,\n"
            " amb = %s, hidehud = %s, banter = { %s }, moods = %s, respawn = %s,\n"
            " chair = %s,\n follow = %s,\n"
            " greeting = %s,\n gmood = { %s },\n nags = %s,\n idle = %s },"
            % (f, _lua(i["guard"]), _lua_str(i["prompt"]), _lua_str(i["name"]), _lua(i["side"]),
               ("'ring'" if f in flagged else "'hq'") if i["marker"] == "hq" else "false",
               _lua(f"TARGET_TYPE_HQ_{f.upper()}"), _lua(f"hq{f}"), *i["room"], player_yaw(i["rheading"]),
               ", ".join(_lua(a) for a in i["leaders"]), _lua(i["door"]), _lua_str(i["door_prompt"]),
               i.get("exit_distance", 3.0), i.get("exit_delay", 5.0), _lua_str(i["talk_prompt"]),
               _lua(i["starter"]) if i.get("starter") else "false",
               _lua_str(i.get("accept_prompt") or "Accept contract"),
               "TRUE" if i.get("bribe", True) else "FALSE",
               str(int(i["bribe_cost"])) if i.get("bribe_cost") is not None else "nil",
               (" dx = %.2f, dy = %.2f, dz = %.2f," % tuple(i["exit_position"])) if i.get("exit_position") else "",
               *(i.get("camera") or [i["room"][0], i["room"][1] + 1.8, i["room"][2]]),
               _lua(i["ambience"]) if i.get("ambience") else "nil",
               "TRUE" if i.get("hide_hud") else "FALSE",
               ", ".join(_lua(b) for b in i.get("banter") or []),
               "TRUE" if i.get("guard_moods") else "FALSE",
               "TRUE" if i.get("respawn") else "FALSE",
               _chair_row(i.get("chair")), _follow_row(i.get("follow")),
               i["greeting"],
               ", ".join(f"{m} = {lines}" for m, lines in (i.get("greetings") or {}).items()),
               i.get("nags") or "{ }",
               i["idle"]))
    return f"""-- HQ interiors (Mercenaries Foundry: maps json "interiors"), played as
-- retail's HQs: bouncer, briefing and briefing_utilities
-- util_objectives' helpers call util_misc's util_SetDefault; on SW global
-- has loaded it, on a new map nothing else does
Utility_LoadUtilityScript('util_misc')
Utility_LoadUtilityScript('util_objectives')
{P}Hq = {{
{chr(10).join(rows)}
}}
if not {P}HqArmed then {P}HqArmed = {{}} end
if not {P}HqLeadArmed then {P}HqLeadArmed = {{}} end
if not {P}HqGuardMode then {P}HqGuardMode = {{}} end
if not {P}HqUsed then {P}HqUsed = {{}} end
{P}HqWaits = 0
-- The last steps inside, for finding out what happened: {P}HqLog() in
-- Foundry Console's Lua box returns them.
{P}HqSteps = {{}}
function {P}HqTrace(s)
table.insert({P}HqSteps, s)
if table.getn({P}HqSteps) > 10 then table.remove({P}HqSteps, 1) end
Utility_WriteStringToScribbleMemory('{P}hq_log', table.concat({P}HqSteps, ' | '))
end
function {P}HqLog()
return Utility_ReadStringFromScribbleMemory('{P}hq_log') or 'no HQ steps yet'
end
-- Measured numbers for the room, to plugins.log (Foundry Console's
-- Foundry_Log) and returned: the player's and objects' positions and yaws as
-- the game has them, the seat spot and yaw the chair was asked for, where the
-- exit's ring is drawn, the follow camera's target and look-at, the ground's
-- height. On entering, 2 s into the seated loop, after standing, when the way
-- out is armed and every 2 s for the first 10 s; foundry_HqDump() any time.
function {P}HqDump(tag)
local h = {P}HqIn and {P}Hq[{P}HqIn]
local function r(v) if not v then return 'nil' end return Utility_MathFloor(v * 100 + 0.5) / 100 end
local function v3(t) if not t then return 'none' end return '(' .. r(t[1]) .. ', ' .. r(t[2]) .. ', ' .. r(t[3]) .. ')' end
local function at(n)
if not n or n == '' or not Utility_DoesActorExist(n) then return tostring(n) .. '=none' end
local x, y, z = Actor_GetPosition(n)
return n .. '=(' .. r(x) .. ', ' .. r(y) .. ', ' .. r(z) .. ') yaw ' .. r(Actor_GetYaw(n))
end
local s = 'hq dump ' .. (tag or '') .. ' [' .. tostring({P}HqIn) .. '] ' .. at('player0')
if h then
s = s .. ' | room spot (' .. r(h.rx) .. ', ' .. r(h.ry) .. ', ' .. r(h.rz) .. ') setyaw ' .. r(h.rh)
if h.chair then
s = s .. ' | chair ' .. at(h.chair.obj) .. ', seat spot (' .. r(h.chair.x) .. ', ' .. r(h.chair.y) .. ', ' .. r(h.chair.z)
.. ') setyaw ' .. r(h.chair.yaw) .. ', seated ' .. tostring({P}HqSeated)
end
s = s .. ' | exit ' .. at(h.door) .. ', ringed ' .. at({P}HqDoorObj) .. ' at ' .. v3({P}HqRingAt)
s = s .. ' | camera on ' .. tostring({P}HqCamOn) .. ', target ' .. v3({P}HqCamWant) .. ', look ' .. v3({P}HqLookAt)
for i, a in h.leaders do s = s .. ' | ' .. at(a) end
end
local x, y, z = Actor_GetPosition('player0')
if x and Utility_GetTerrainHeightAtXZ then s = s .. ' | ground ' .. r(Utility_GetTerrainHeightAtXZ(x, z)) end
if Foundry_Log then Foundry_Log(s) end
return s
end
function foundry_HqDump() return {P}HqDump('asked') end
function {P}HqDumpSeated() if {P}HqSeated then {P}HqDump('seated +2 s') end end
function {P}HqDumpWalk()
{P}HqDumpN = ({P}HqDumpN or 0) + 1
if not {P}HqIn or {P}HqDumpN > 5 then return end
{P}HqDump(({P}HqDumpTag or 'in the room') .. ' +' .. ({P}HqDumpN * 2) .. ' s')
Event_RelativeTimer('{P}HqDumpWalk', 'b', 2.0)
end
function {P}HqRules(name)
Ai_SetHostileRange(name, 0.0)
Actor_SetVerifiable(name, FALSE)
Actor_SetInvincible(name, true)
end
function {P}HqArm()
-- one look-timer at a time: a new start replaces the one waiting
if {P}HqTimer then Event_CancelEvent({P}HqTimer) {P}HqTimer = nil end
{P}HqWaits = 0
{P}HqLook()
end
-- 'bribe' while the faction is hostile to the player (and the interior
-- takes bribes), else 'enter'
function {P}HqGuardWants(f)
local h = {P}Hq[f]
if h.bribe and Faction_GetAttitude(f, 'prokat') == 0 then return 'bribe' end
return 'enter'
end
function {P}HqLook()
-- the next look first, so an error in this one doesn't stop the looking
{P}HqWaits = {P}HqWaits + 1
if {P}HqWaits <= 15 then
{P}HqTimer = Event_RelativeTimer('{P}HqLook', 'b', 1.0)
else
{P}HqTimer = Event_RelativeTimer('{P}HqLook', 'b', 5.0)
end
for f, h in {P}Hq do
if Utility_DoesActorExist(h.guard) then
local iActor = Utility_GetActorAsInt(h.guard)
Ai_SetFaction(h.guard, h.side)   -- again each look: a side set while it slept doesn't hold
{P}HqRules(h.guard)
local sWants = {P}HqGuardWants(f)
if {P}HqArmed[h.guard] ~= iActor then
{P}HqArmed[h.guard] = iActor
{P}HqGuardMode[f] = nil
Ai_CommandQueue(h.guard, 'Wait', 'hiPri')
if h.marker == 'hq' then
util_SetupSingleTargetActorObjective(h.guard, h.name, '', 'OBJECTIVE_TYPE_PRIMARY', h.guard, h.name, h.ttype, TRUE, TRUE, TRUE, h.blip)   -- (the game's Lua has no string library)
elseif h.marker == 'ring' then
-- the bouncers' gold ring and caption (the HQ target type), in the world
-- only: the HQ point already has the flag on the radar and PDA map
util_SetupSingleTargetActorObjective(h.guard, h.name, '', 'OBJECTIVE_TYPE_PRIMARY', h.guard, h.name, h.ttype, FALSE, FALSE, TRUE, h.blip)
end
end
if {P}HqGuardMode[f] ~= sWants and {P}HqIn ~= f and {P}HqBribing ~= f then
{P}HqGuardMode[f] = sWants
if sWants == 'bribe' then
{P}HqUse(h.guard, 'action.bribe.text', '{P}HqBribe_' .. f)
else
{P}HqUse(h.guard, h.prompt, '{P}HqEnter_' .. f)
end
end
end
for i, a in h.leaders do
{P}HqLeader(a)
end
if h.moods and {P}HqIn ~= f then {P}HqMood(f, h) end
end
end
-- The guard's mood within 15 m (retail's BouncerMoodReaction): friendly,
-- neutral or unfriendly by the faction's attitude, every 5 s or so; none when
-- hostile.
if not {P}HqMoodWait then {P}HqMoodWait = {{}} end
function {P}HqMood(f, h)
if not Utility_DoesActorExist(h.guard) then return end
{P}HqMoodWait[f] = ({P}HqMoodWait[f] or 0) - 1
if {P}HqMoodWait[f] > 0 then return end
local px, py, pz = Actor_GetPosition('player0')
local gx, gy, gz = Actor_GetPosition(h.guard)
if not px or not gx then return end
local dx, dy, dz = px - gx, py - gy, pz - gz
if dx * dx + dy * dy + dz * dz > 225 then return end
local att = Faction_GetAttitude(f, 'prokat')
local anim = nil
if att == 3 then anim = 'fb_mood_friendly' elseif att == 2 then anim = 'fb_mood_neutral' elseif att == 1 then anim = 'fb_mood_unfriendly' end
if not anim then return end
Actor_PlayHumanAnimationByHandle(Utility_GetActor(h.guard), anim)
-- looked for each second at first, then each 5 s: about every 5 s either way
if {P}HqWaits <= 15 then {P}HqMoodWait[f] = 5 else {P}HqMoodWait[f] = 1 end
end
-- A leader stands and never fights: cinematic control (retail's briefing
-- boss and starter), once per actor; the rules each look.
function {P}HqLeader(a)
if not Utility_DoesActorExist(a) then return end
local iActor = Utility_GetActorAsInt(a)
{P}HqRules(a)
if {P}HqLeadArmed[a] ~= iActor then
{P}HqLeadArmed[a] = iActor
Ai_CommandQueue(a, 'Wait', 'hiPri')
Actor_EnableCinematicControl(a, TRUE)
Actor_LookAtActor(a, 'player0')
end
end
-- Using an actor: one event at a time per actor (the one waiting is cancelled).
function {P}HqUse(name, prompt, cb, dist)
if Utility_DoesActorExist(name) then
if {P}HqUsed[name] then Event_CancelEvent({P}HqUsed[name]) end
if dist then
-- (actor, on, prompt, flag, use distance), as retail's briefing exit and starter
Actor_EnableScriptedUse(name, TRUE, prompt, FALSE, dist)
else
Actor_EnableScriptedUse(name, TRUE, prompt)
end
{P}HqUsed[name] = Event_ActorUsed(cb, 'b', name)
end
end
function {P}HqUnuse(name)
if {P}HqUsed[name] then Event_CancelEvent({P}HqUsed[name]) {P}HqUsed[name] = nil end
if Utility_DoesActorExist(name) then Actor_EnableScriptedUse(name, FALSE, '') end
end
-- The faction's next contract inside: in foundry_contracts' table, on this
-- map, not done (or repeatable), unlocked and not lost.
function {P}HqContract(f)
if not tFoundryContracts then return nil end
for i, c in tFoundryContracts do
if c.interior == f and c.map == sFoundryOnMap and not (foundry_IsDone(c) and not c.repeatable)
and foundry_Unlocked(c) and not Utility_ReadNumberFromScribbleMemory('foundry_lost_' .. c.id) then
return c
end
end
return nil
end
-- A hostile faction's guard: a bribe, as retail's bouncers (BouncerBribeRequested).
function {P}HqBribe(f)
local h = {P}Hq[f]
{P}HqBribing = f
{P}HqUnuse(h.guard)
{P}HqGuardMode[f] = nil
{P}HqBribeCost = h.bribecost or util_GetBribeCost(Utility_ReadNumberFromScribbleMemory('chapter')) or 25000
local sDialog = Utility_GetStringById('interface.factionbribeq1.dialog') .. '[n][n]'
.. Utility_GetStringById('interface.factionbribeq2.dialog') .. ': ' .. Utility_FormatCurrency({P}HqBribeCost)
local sYes = '{P}HqBribeShort'
if Player_GetMoney() >= {P}HqBribeCost then sYes = '{P}HqBribeYes' end
Utility_QuestionBox(Utility_GetStringById('interface.factionbribeq.name'), sDialog, true, sYes, '{P}HqBribeDone')
end
function {P}HqBribeYes()
Utility_MessageBox(Utility_GetStringById('interface.factionbribeyea.name'),
Utility_GetStringById('interface.factionbribeyea.dialog'), true, '{P}HqBribePaid')
end
function {P}HqBribePaid()
Faction_SetRelation({P}HqBribing, 'prokat', util_GetPostBribeMood())
Player_AdjustMoney(-{P}HqBribeCost, 'interface.economyevent.bribe')
{P}HqBribeDone()
end
function {P}HqBribeShort()
Utility_MessageBox(Utility_GetStringById('interface.factionbribenay.name'),
Utility_GetStringById('interface.factionbribenay.dialog'), true, '{P}HqBribeDone')
end
-- the guard again: 'enter' if the bribe was paid, else the bribe
function {P}HqBribeDone()
{P}HqBribing = nil
{P}HqArm()
end
-- Retail's briefing controls (Briefing_Init): control mode 13, and with
-- Foundry Console the weapon put away and walking pace; put back by
-- {P}HqRelease. The HUD stays: retail's briefing hides it (its lines are
-- voiced), but here the lines are message boxes and the rings HUD markers.
function {P}HqHold()
if {P}HqHeld then return end
{P}HqHeld = 1
{P}HqControl = Player_DisableControl()
Player_EnableControl(13)
if Foundry_HqMode then Foundry_HqMode(TRUE) end
-- the room's atmosphere, as retail's briefing: the music stopped, the
-- room's ambience cue, the HUD hidden if asked (retail's lines are voiced)
local h = {P}HqIn and {P}Hq[{P}HqIn]
Audio_StopMusic()
if h and h.amb then Briefing_SetAmbientSoundCue(h.amb, 30, 40) {P}HqAmbOn = 1 end
if h and h.hidehud then Ui_EnableHud(FALSE) {P}HqHudOff = 1 end
end
-- The camera inside: a fixed spot in the room (retail's briefing shows the
-- room's own shot, never the follow camera, which a room on the map pushes
-- behind its walls): on the speaker while lines play, else on the player.
function {P}HqCam(sLook)
local h = {P}HqIn and {P}Hq[{P}HqIn]
if not h then return end
if h.follow then {P}HqFollowOn(h) return end
if not {P}HqCamOn then
Camera_DetachFromActor()
Camera_SetMode('RS_SCRIPT_VIEW')
Camera_SetPosition(h.cx, h.cy, h.cz)
{P}HqCamOn = 1
end
if not sLook or not Utility_DoesActorExist(sLook) then sLook = 'player0' end
Camera_SetInterestActor(sLook)
end
-- The follow camera inside (retail's, once the player stands; its rooms keep
-- it inside their walls, ours mightn't): the scripted camera behind the way
-- the player last walked (at first the way they face coming in, or seated),
-- "distance" back and "height" up, kept inside the floor's "bounds", moved
-- each 0.05 s. (No math library in the game's Lua: lengths by Newton's method.)
function {P}HqFollowOn(h)
if {P}HqFollowT then return end
Camera_DetachFromActor()
Camera_SetMode('RS_SCRIPT_VIEW')
{P}HqCamOn = 1
{P}HqFx, {P}HqFz = h.follow.fx, h.follow.fz
if {P}HqSeated and h.chair then {P}HqFx, {P}HqFz = h.chair.fx, h.chair.fz end
{P}HqLastX = nil
{P}HqCamAt, {P}HqLookAt = nil, nil
{P}HqFollow()
end
function {P}HqLen(d2)
local l = d2
if l < 1 then l = 1 end
for i = 1, 8 do l = (l + d2 / l) * 0.5 end
return l
end
function {P}HqFollow()
{P}HqFollowT = nil
local h = {P}HqIn and {P}Hq[{P}HqIn]
if not h or not h.follow or not {P}HqCamOn then return end
local fw = h.follow
local x, y, z = Actor_GetPosition('player0')
if x then
-- A trailing camera (0.248): it keeps its own side of the player and is
-- only pulled in or out to "distance", so turning on the spot doesn't swing
-- it round the room (following his facing or walking direction did: the
-- 0.247 dumps had his yaw swing 180 degrees in 2 s). Seated, and at first,
-- it goes behind the way he faces. Eased a little each tick and set
-- directly: restarted glides (0.245-0.247) stuttered.
local c = {P}HqCamAt
local cy, ly = y + fw.up, y + fw.look
local tx, tz
if not c or {P}HqSeated then
tx, tz = x - {P}HqFx * fw.dist, z - {P}HqFz * fw.dist
else
local dx, dz = c[1] - x, c[3] - z
local l = {P}HqLen(dx * dx + dz * dz)
if l < 0.05 then dx, dz, l = -{P}HqFx, -{P}HqFz, 1 end
tx, tz = x + dx / l * fw.dist, z + dz / l * fw.dist
end
if fw.x0 then
if tx < fw.x0 then tx = fw.x0 elseif tx > fw.x1 then tx = fw.x1 end
if tz < fw.z0 then tz = fw.z0 elseif tz > fw.z1 then tz = fw.z1 end
end
{P}HqCamWant = {{ tx, cy, tz }}
local l = {P}HqLookAt
if not c then
c = {{ tx, cy, tz }}
l = {{ x, ly, z }}
else
c[1] = c[1] + (tx - c[1]) * 0.15   c[2] = c[2] + (cy - c[2]) * 0.15   c[3] = c[3] + (tz - c[3]) * 0.15
l[1] = l[1] + (x - l[1]) * 0.25   l[2] = l[2] + (ly - l[2]) * 0.25   l[3] = l[3] + (z - l[3]) * 0.25
end
{P}HqCamAt, {P}HqLookAt = c, l
Camera_SetPosition(c[1], c[2], c[3])
Camera_SetInterestPosition(l[1], l[2], l[3])
end
{P}HqFollowT = Event_RelativeTimer('{P}HqFollow', 'b', 0.033)
end
function {P}HqCamOff()
if {P}HqFollowT then Event_CancelEvent({P}HqFollowT) {P}HqFollowT = nil end
if not {P}HqCamOn then return end
{P}HqCamOn = nil
Camera_SetMode('RS_ACTOR_VIEW')
end
-- The chair (retail's three-stage chair interaction, TrivialInteraction):
-- the sit-down clip, then the seated loop with use-only control and the
-- chair's "Stand up" prompt, then the stand-up clip and control mode 13
-- again, with "Sit down" on the chair. The player is put on the seat's spot
-- (retail attaches them to the briefing scene's hp_player_chair); the camera
-- stays the room's fixed one.
function {P}HqSit()
local h = {P}HqIn and {P}Hq[{P}HqIn]
if not h or not h.chair or {P}HqSeated then return end
local c = h.chair
-- no chair, no seat: nothing would take the "Stand up" prompt
if not Utility_DoesActorExist(c.obj) then {P}HqTrace('no chair ' .. c.obj) return end
{P}HqUnuse(c.obj)
{P}HqSeated = 1
{P}HqTrace('sit')
Player_DisableControl()
Actor_EnableCinematicControl('player0', TRUE)
Actor_EnableCollision('player0', FALSE)
Actor_SetPosition('player0', c.x, c.y, c.z)
Actor_SetYaw('player0', c.yaw)
{P}HqFx, {P}HqFz = c.fx, c.fz          -- the follow camera behind the seat
Actor_PlayHumanAnimation('player0', c.sit, RS_AFLG_NOHEAD)
if c.rig1 then Actor_PlayAnimation(c.obj, c.rig1) end      -- retail's allies chair turns under the player
{P}HqSitTimer = Event_RelativeTimer('{P}HqSitting', 'b', c.dsit)
end
function {P}HqSitting()
{P}HqSitTimer = nil
local h = {P}HqIn and {P}Hq[{P}HqIn]
if not h or not {P}HqSeated then return end
Player_EnableUseOnlyControl()
Actor_PlayHumanAnimation('player0', h.chair.loop, RS_AFLG_LOOP + RS_AFLG_NOHEAD)
if h.chair.rig2 then Actor_PlayAnimation(h.chair.obj, h.chair.rig2, RS_AFLG_LOOP) end
{P}HqUse(h.chair.obj, h.chair.standprompt, '{P}HqStand')
Event_RelativeTimer('{P}HqDumpSeated', 'b', 2.0)
end
function {P}HqStand()
local h = {P}HqIn and {P}Hq[{P}HqIn]
if not h or not {P}HqSeated then return end
{P}HqUnuse(h.chair.obj)
{P}HqTrace('stand')
Player_DisableControl()
Actor_PlayHumanAnimation('player0', h.chair.stand, RS_AFLG_NOHEAD)
if h.chair.rig3 then Actor_StopAnimation(h.chair.obj) Actor_PlayAnimation(h.chair.obj, h.chair.rig3) end
{P}HqSitTimer = Event_RelativeTimer('{P}HqStood', 'b', h.chair.dstand)
end
function {P}HqStood()
{P}HqSitTimer = nil
local h = {P}HqIn and {P}Hq[{P}HqIn]
{P}HqUnseat()
-- "Sit down" again, within 1 m only: at the default reach it took E from
-- the way out by the door (0.247 playtest: sat down again trying to leave)
if h and h.chair then {P}HqUse(h.chair.obj, h.chair.sitprompt, '{P}HqSit', 1.0) end
{P}HqDump('stood up')
-- and every 2 s for 10 s after: the walking (the camera following)
{P}HqDumpN = 0
{P}HqDumpTag = 'after standing'
Event_RelativeTimer('{P}HqDumpWalk', 'b', 2.0)
end
-- Out of the chair at once (leaving, or taking the contract while seated).
function {P}HqUnseat()
if {P}HqSitTimer then Event_CancelEvent({P}HqSitTimer) {P}HqSitTimer = nil end
if not {P}HqSeated then return end
{P}HqSeated = nil
local h = {P}HqIn and {P}Hq[{P}HqIn]
if h and h.chair and h.chair.rig1 and Utility_DoesActorExist(h.chair.obj) then Actor_StopAnimation(h.chair.obj) end
Actor_StopAnimation('player0')
Actor_EnableCinematicControl('player0', FALSE)
Actor_EnableCollision('player0', TRUE)
Player_EnableControl(13)
end
function {P}HqRelease()
{P}HqUnseat()
{P}HqCamOff()
{P}HqNagStop()
if {P}HqAmbOn then Briefing_SetAmbientSoundCue('', 0, 0) {P}HqAmbOn = nil end
-- the HUD back whatever hid it, as retail's Briefing_Exit does (the 0.241
-- playtest lost it for good after a visit)
Ui_EnableHud(TRUE)
{P}HqHudOff = nil
if not {P}HqHeld then return end
{P}HqHeld = nil
if Foundry_HqMode then Foundry_HqMode(FALSE) end
Player_EnableControl({P}HqControl or 0)
{P}HqControl = nil
end
function {P}HqEnter(f)
local h = {P}Hq[f]
{P}HqTrace('enter ' .. f)
{P}HqIn = f
{P}HqUnuse(h.guard)
{P}HqBack = {{ Actor_GetPosition('player0') }}
-- the guard's banter first, as retail's bouncer (BouncerUsed), then in
local n = table.getn(h.banter)
if n > 0 and Utility_DoesActorExist(h.guard) then
local cue = h.banter[util_GetRandomIntInRange(1, n)]
{P}HqTrace('banter ' .. cue)
Audio_PlayVoiceoverCB(cue, '{P}HqGoIn', h.guard)
else
{P}HqGoIn()
end
end
function {P}HqGoIn()
Cinematic_FadeOut(0.5)
Event_RelativeTimer('{P}HqInside', 'b', 0.6)
end
function {P}HqInside()
local h = {P}Hq[{P}HqIn]
{P}HqTrace('inside')
if h.chair and h.chair.auto then
-- by the chair, to be seated at once (retail's AutoInteractWithChair)
Actor_SetPosition('player0', h.chair.x, h.chair.y, h.chair.z)
Actor_SetYaw('player0', h.chair.yaw)
else
Actor_SetPosition('player0', h.rx, h.ry, h.rz)
Actor_SetYaw('player0', h.rh)
end
for i, a in h.leaders do
{P}HqLeader(a)
if Utility_DoesActorExist(a) then Actor_LookAtActor(a, 'player0') end
end
{P}HqHold()
{P}HqCam(h.leaders[1])
Event_RelativeTimer('{P}HqLit', 'b', 0.5)
end
function {P}HqLit()
local h = {P}Hq[{P}HqIn]
Cinematic_FadeIn(0.5)
{P}HqDump('entered')
{P}HqDumpN = 0
{P}HqDumpTag = nil
Event_RelativeTimer('{P}HqDumpWalk', 'b', 2.0)
{P}HqDoorTimer = Event_RelativeTimer('{P}HqDoor', 'b', h.exitdelay)
if h.chair then
if h.chair.auto then {P}HqSit() else {P}HqUse(h.chair.obj, h.chair.sitprompt, '{P}HqSit') end
end
-- the greeting on the first visit (per save, as retail's); later visits go
-- straight to the next briefing, or the idle lines
local sKey = 'foundry_hq_greeted_' .. {P}HqIn
if Utility_ReadNumberFromScribbleMemory(sKey) == 1 or table.getn(h.greeting) == 0 then
{P}HqTrace('lit: again, no greeting')
{P}HqMoodGreeting()
else
Utility_WriteNumberToScribbleMemory(sKey, 1)
{P}HqTrace('lit: greeting')
{P}HqSay(h.greeting, {P}HqMoodGreeting)
end
end
-- Every visit: a greeting by the faction's mood toward the player (retail's
-- ch<n>_<faction>_greetings: friendly, neutral, unfriendly), then the offer.
function {P}HqMoodGreeting()
local f = {P}HqIn
if not f then return end
local h = {P}Hq[f]
local att = Faction_GetAttitude(f, 'prokat')
local lines = nil
if att == 3 then lines = h.gmood.friendly
elseif att == 2 then lines = h.gmood.neutral
else lines = h.gmood.unfriendly end
if lines and table.getn(lines) > 0 then
{P}HqTrace('mood greeting ' .. att)
{P}HqSay(lines, {P}HqOffer)
else
{P}HqOffer()
end
end
-- The way out: its ring and prompt (retail's EnableExit).
function {P}HqDoor()
{P}HqDoorTimer = nil
local h = {P}HqIn and {P}Hq[{P}HqIn]
if not h then return end
if h.door ~= '' and Utility_DoesActorExist(h.door) then
-- Retail's exit is a cube its briefing spawns (Briefing_AddActor), ringed
-- with Objective_SetTargetActor; the engine draws no world ring at a point
-- (retail never asks it to), and none showed on our placed cube (0.240-
-- 0.244). So the way out spawns a cube from the placed one, as retail
-- spawns its objects (Mission_Spawn), and rings and uses that.
local door = h.door
if h.dx then
local px, py, pz = Actor_GetPosition('player0')
local ry = h.dy
if py and py > ry then ry = py end
local spawned = h.door .. '_out'
if not Utility_DoesActorExist(spawned) then Mission_Spawn(h.door, spawned, h.dx, ry, h.dz, 0) end
if Utility_DoesActorExist(spawned) then door = spawned {P}HqDoorSpawned = spawned end
{P}HqRingAt = {{ h.dx, ry, h.dz }}
end
{P}HqDoorObj = door
{P}HqUse(door, h.doorprompt, '{P}HqLeave', h.doordist)
-- the faction's HQ ring type (the guards' and givers' gold rings draw;
-- retail's briefing-scene TARGET_TYPE_UNDEFINED drew none on a map)
Objective_SetTargetActor('{P}obj_exit', door, '', h.ttype, 'blip', TRUE, TRUE, TRUE, 0.5)
{P}HqTrace('door ' .. door .. ' armed')
{P}HqDump('exit armed')
elseif h.dx then
-- a point ("exit" "position"): its ring, and coming near asks to leave
local px, py, pz = Actor_GetPosition('player0')
local ry = h.dy
if py and py > ry then ry = py + 0.02 end
Objective_SetTargetVector('{P}obj_exit', '{P}obj_exit', h.doorprompt, h.ttype, 'blip', h.dx, ry, h.dz, FALSE, FALSE, TRUE)
{P}HqRingAt = {{ h.dx, ry, h.dz }}
{P}HqDump('exit armed (point)')
{P}HqDoorNear()
{P}HqTrace('door point armed (no ' .. h.door .. '), ring at y ' .. ry)
else
{P}HqTrace('door ' .. h.door .. ' not found (give "exit" a "position")')
end
end
function {P}HqDoorNear()
local h = {P}HqIn and {P}Hq[{P}HqIn]
if not h or not h.dx then return end
{P}HqDoorEv = Event_ActorToVectorProximity('{P}HqAtDoor', 'b', 'player0', h.dx, h.dy, h.dz, h.doordist, TRUE)
end
function {P}HqAtDoor()
{P}HqDoorEv = nil
local h = {P}HqIn and {P}Hq[{P}HqIn]
if not h then return end
Utility_QuestionBox(h.doorprompt, h.doorprompt .. '?', true, '{P}HqLeave', '{P}HqDoorLater')
end
-- not now: asked again after stepping away and back
function {P}HqDoorLater()
local h = {P}HqIn and {P}Hq[{P}HqIn]
if not h then return end
{P}HqDoorEv = Event_ActorToVectorProximity('{P}HqDoorNear', 'b', 'player0', h.dx, h.dy, h.dz, h.doordist + 1, FALSE)
end
-- Using the first leader (when the way out is something else): the briefing again.
function {P}HqTalk()
{P}HqOffer()
end
function {P}HqOffer()
local f = {P}HqIn
if not f then return end
local h = {P}Hq[f]
local c = {P}HqContract(f)
if c then
{P}HqTrace('briefing ' .. c.id)
{P}HqOffered = c
-- (no dateline: retail shows it only in its briefing scene, which the
-- world's reload clears; in a room on the map it stayed on screen)
{P}HqSay(c.briefing or {{}}, {P}HqAsk)
else
{P}HqTrace('no contract: idle lines')
{P}HqSay(h.idle, {P}HqTalkArm)
end
end
-- After the briefing: the second in command takes the contract (retail's
-- starter_trigger: a ring and the accept prompt), or a yes/no box.
function {P}HqAsk()
local c = {P}HqOffered
if not c or not {P}HqIn then return end
local h = {P}Hq[{P}HqIn]
{P}HqTalkArm()
{P}HqNagStart()
if h.starter and Utility_DoesActorExist(h.starter) then
if not {P}HqStarterOn then
{P}HqStarterOn = 1
{P}HqUse(h.starter, h.acceptprompt, '{P}HqYes', 1.5)
{P}HqTrace('starter ' .. h.starter .. ' armed')
Objective_SetTargetActor('{P}obj_starter', h.starter, '', h.ttype, 'blip', TRUE, TRUE, TRUE, 0.5)
end
else
Utility_QuestionBox(Utility_GetStringById(c.title), Utility_GetStringById(c.descr), true, '{P}HqYes', '{P}HqTalkArm')
end
end
-- The boss nags while the contract waits (retail: after 4 s, then every 8 s,
-- up to the nag lines there are, 3 in retail).
function {P}HqNagStart()
{P}HqNagStop()
local h = {P}HqIn and {P}Hq[{P}HqIn]
if not h or table.getn(h.nags) == 0 then return end
{P}HqNagN = 0
{P}HqNagTimer = Event_RelativeTimer('{P}HqNag', 'b', 4.0)
end
function {P}HqNag()
{P}HqNagTimer = nil
local h = {P}HqIn and {P}Hq[{P}HqIn]
if not h or not {P}HqOffered then return end
if {P}HqLines then {P}HqNagTimer = Event_RelativeTimer('{P}HqNag', 'b', 2.0) return end   -- something is being said
{P}HqNagN = ({P}HqNagN or 0) + 1
local l = h.nags[{P}HqNagN]
if not l then return end
{P}HqSay({{ l }}, nil)
{P}HqNagTimer = Event_RelativeTimer('{P}HqNag', 'b', l.seconds + 8.0)
end
function {P}HqNagStop()
if {P}HqNagTimer then Event_CancelEvent({P}HqNagTimer) {P}HqNagTimer = nil end
end
function {P}HqYes()
{P}HqTrace('accept')
local c = {P}HqOffered
local h = {P}HqIn and {P}Hq[{P}HqIn]
if not c or not h then return end
-- a death in the contract respawns by this HQ (retail's onUseStarter)
if h.respawn then Utility_WriteStringToScribbleMemory('PlayerStartLocationName', 'loc_hq-' .. {P}HqIn .. '-respawn') end
{P}HqTidy(h)
{P}HqIn = nil
{P}HqOffered = nil
{P}HqRelease()
foundry_Start(c)
end
function {P}HqTalkArm()
local h = {P}HqIn and {P}Hq[{P}HqIn]
if h and h.leaders[1] and h.leaders[1] ~= h.door and h.leaders[1] ~= h.starter then {P}HqUse(h.leaders[1], h.talkprompt, '{P}HqTalk') end
end
-- Inside's prompts, rings and lines taken away.
function {P}HqTidy(h)
Ui_MessageBoxClear()
{P}HqLines = nil
{P}HqNagStop()
if {P}HqDoorTimer then Event_CancelEvent({P}HqDoorTimer) {P}HqDoorTimer = nil end
if {P}HqDoorEv then Event_CancelEvent({P}HqDoorEv) {P}HqDoorEv = nil end
Objective_Remove('{P}obj_exit')
Objective_Remove('{P}obj_starter')
{P}HqStarterOn = nil
for i, a in h.leaders do {P}HqUnuse(a) end
if h.starter then {P}HqUnuse(h.starter) end
if h.chair then {P}HqUnuse(h.chair.obj) end
{P}HqUnuse(h.door)
if {P}HqDoorSpawned then
{P}HqUnuse({P}HqDoorSpawned)
if Utility_DoesActorExist({P}HqDoorSpawned) then Actor_Remove({P}HqDoorSpawned) end
{P}HqDoorSpawned = nil
end
{P}HqDoorObj = nil
end
function {P}HqLeave()
{P}HqTrace('leave')
if not {P}HqIn then return end
{P}HqTidy({P}Hq[{P}HqIn])
{P}HqOffered = nil
Cinematic_FadeOut(0.5)
Event_RelativeTimer('{P}HqOutside', 'b', 0.6)
end
function {P}HqOutside()
local f = {P}HqIn
local h = {P}Hq[f]
local b = {P}HqBack
if b and b[1] then Actor_SetPosition('player0', b[1], b[2], b[3]) end
{P}HqRelease()
{P}HqIn = nil
Cinematic_FadeIn(0.5)
{P}HqArmed[h.guard] = nil
{P}HqArm()
end
-- Lines one after another (a message each, for its seconds; a sound cue too
-- if it has one), then fDone.
function {P}HqSay(lines, fDone)
{P}HqLines = lines
{P}HqLine = 0
{P}HqDone = fDone
local h = {P}HqIn and {P}Hq[{P}HqIn]
if h and table.getn(lines) > 0 then {P}HqCam(h.leaders[1]) end
{P}HqNext()
end
function {P}HqNext()
if not {P}HqLines or not {P}HqIn then return end
{P}HqLine = {P}HqLine + 1
local l = {P}HqLines[{P}HqLine]
Ui_MessageBoxClear()
if not l then
{P}HqLines = nil
{P}HqCam('player0')
if {P}HqDone then {P}HqDone() end
return
end
Ui_MessageBoxPrint(l.text)
if l.sound then Audio_PlayVoiceover(l.sound) end
Event_RelativeTimer('{P}HqNext', 'b', l.seconds)
end
""" + "".join(f"function {P}HqEnter_{f}() {P}HqEnter({_lua(f)}) end\n"
              f"function {P}HqBribe_{f}() {P}HqBribe({_lua(f)}) end\n" for f in inside)


def layers_script(layers, pilots=(), arrive=False, welcome=(), deck=False):
    """Lua for foundry_layers: load each mod layer marked "load": "map" when
    free roam starts on its map. A layer is loaded again only if it's gone:
    when one of its named objects no longer exists (a mission reset the
    map), or, for layers without named objects, once per script state.
    pilots: travel pilots on the free-roam maps (travel_lua), armed after
    the layers load; and a player back from another map is put where its
    pilot said (foundry_Arrive). welcome: mods' welcome mails
    (welcome_mail), posted once per save (foundry_Welcome). deck: a mod has
    its own Deck of 52 on a new map: here the game's cards are put back in
    the PDA (foundry_deck's foundry_DeckLeave)."""
    out = ["-- Generated by Mercenaries Foundry: mod layers that load with their map in free roam.",
           "tFoundryLayers = {"]
    for l in layers:
        out.append("{ map = %s, layer = %s, check = %s }," % (
            _lua(l["map"]), _lua(l["layer"]), _lua(l["check"]) if l.get("check") else "nil"))
    out += ["}",
            "if not g_foundry_layers then g_foundry_layers = {} end",
            # A save made during a contract played on a new map: the game
            # resumes the level SkipTo_Map names, the contract's arc map (sw),
            # so the player woke there. Back to the new map, the contract
            # started again from its beginning (as retail restarts a mission
            # a save was made in).
            "function foundry_ResumeNewMap()",
            "local m = Utility_ReadStringFromScribbleMemory('foundry_play_map')",
            "if not m then return false end",
            "local f = Utility_ReadStringFromScribbleMemory('current_faction')",
            "local n = f and Utility_ReadNumberFromScribbleMemory('current_' .. f .. '_mission')",
            "if not n or not Utility_ReadNumberFromScribbleMemory('mission_accepted') then",
            "Utility_EraseValueFromScribbleMemory('foundry_play_map')",
            "Utility_EraseValueFromScribbleMemory('foundry_play_arc')",
            "return false",
            "end",
            "Utility_WriteStringToScribbleMemory('SkipTo_Faction', f)",
            "Utility_WriteNumberToScribbleMemory('SkipTo_MissionNumber', n)",
            "Utility_EraseValueFromScribbleMemory('skipto_via_cheating')",
            "Utility_WriteStringToScribbleMemory('foundry_map_start', 'resumed a saved contract on ' .. m)",
            "Utility_ResetToBaseLayerOnly(m)",
            "return true",
            "end",
            "function foundry_LoadMapLayers()",
            "if foundry_ResumeNewMap() then return end",
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
    if welcome:
        out += ["foundry_Welcome()"]
    if deck:
        out += ["Utility_LoadUtilityScript('foundry_deck')", "foundry_DeckLeave()   -- the game's cards back in the PDA"]
    out += ["end"]
    text = "\n".join(out) + "\n"
    if arrive or pilots:
        text += ARRIVE_LUA
    if pilots:
        text += travel_lua("foundry_Travel", list(pilots))
    if welcome:
        text += welcome_lua(list(welcome))
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
                                                              player_yaw(t.get("arrive_heading")))
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


def map_mission_lua(map_name, free_roam=()):
    """Lua for a mod's new map's master script: free roam there with the mod's
    contracts (foundry_contracts: its placed givers), and a mission played
    there, started as global's InitializeMap and LoadMission start one
    (SkipTo_* from the contract; the arc map stays SkipTo_Map). When the
    mission's script closes (done, or failed and not retried), the game calls
    back foundry_<map>_FreeRoam, as global's LoadNoMissionState. free_roam:
    more calls FreeRoam makes (the HQ flags and interiors). Returns
    (start lines for ScriptInit, functions)."""
    P = f"foundry_{map_name}_"
    start = [f"sFoundryOnMap = {_lua(map_name)}",
             f"if not {P}Mission() then {P}FreeRoam() end"]
    funcs = f"""-- Missions played on {map_name} (Mercenaries Foundry)
function {P}Mission()
if Utility_ReadStringFromScribbleMemory('foundry_play_map') ~= {_lua(map_name)} then return false end
local f = Utility_ReadStringFromScribbleMemory('SkipTo_Faction')
local n = Utility_ReadNumberFromScribbleMemory('SkipTo_MissionNumber')
if not f or not n then return false end
-- where the start got to, for Foundry Console (foundry_MapStart())
Utility_WriteStringToScribbleMemory('foundry_map_start', 'began ' .. f .. ' ' .. n)
Utility_LoadUtilityScript('util_debug')
Utility_LoadUtilityScript('util_misc')
Utility_LoadUtilityScript('gameflow')
gameflow_GenericInit()
-- The arc's map: loading this map sets SkipTo_Map to it (tried in game:
-- 'gaza'), so the contract keeps its arc's in a key of its own, and
-- SkipTo_Map is the arc's again while the mission runs (mission.lua reads
-- it for the mission's id, its layer and its mission.ini data).
local q = Utility_ReadStringFromScribbleMemory('foundry_play_arc')
local tArcs = util_GetMissionArcTable()
if not q or not tArcs[f] or not tArcs[f][q] then
Utility_WriteStringToScribbleMemory('foundry_map_start', 'no ' .. f .. ' arc for ' .. (q or 'no map'))
Ui_PrintHudMessage('Mercenaries Foundry: no ' .. f .. ' arc for that contract, so it does not start')
Utility_EraseValueFromScribbleMemory('SkipTo_Faction')
Utility_EraseValueFromScribbleMemory('SkipTo_MissionNumber')
return false
end
Utility_WriteStringToScribbleMemory('SkipTo_Map', q)
Utility_EraseValueFromScribbleMemory('SkipTo_Faction')
Utility_EraseValueFromScribbleMemory('SkipTo_MissionNumber')
if not Utility_ReadNumberFromScribbleMemory('mission_accepted') then
if Utility_ReadStringFromScribbleMemory('skipto_via_cheating') then
Utility_EraseValueFromScribbleMemory('skipto_via_cheating')
n = util_MissionAssetNumToMissionSeqNum(q, f, n)
end
Utility_WriteStringToScribbleMemory('current_faction', f)
Utility_WriteNumberToScribbleMemory('current_' .. f .. '_mission', n)
Utility_WriteNumberToScribbleMemory('mission_accepted', 1)
end
local a = n and util_MissionSeqNumToMissionAssetNum(q, f, n)
if not a then
-- not in the arc util_misc has: nothing to run, so free roam
Utility_WriteStringToScribbleMemory('foundry_map_start', 'not in the ' .. f .. ' ' .. q .. ' arc')
Ui_PrintHudMessage('Mercenaries Foundry: that contract is not in the ' .. f .. ' ' .. q .. ' arc')
Utility_EraseValueFromScribbleMemory('mission_accepted')
return false
end
util_DefaultSupport()
Utility_WriteStringToScribbleMemory('full_mission_id', q .. '_' .. f .. n)
Utility_WriteStringToScribbleMemory('foundry_map_start', 'running ' .. q .. '_' .. f .. a)
local ok = Utility_RunLuaScript({_lua(P + 'FreeRoam')}, q .. '_' .. f .. a)
-- (only noted: what it gives back when it works isn't known yet)
Utility_WriteStringToScribbleMemory('foundry_map_start', 'ran ' .. q .. '_' .. f .. a .. ': ' .. tostring(ok))
return true
end
function foundry_MapStart()
return Utility_ReadStringFromScribbleMemory('foundry_map_start') or 'no contract start on a new map yet'
end
function {P}FreeRoam()
if Utility_ReadStringFromScribbleMemory('foundry_play_arc') then
-- back from a mission played here: this map's free roam again
Utility_WriteStringToScribbleMemory('SkipTo_Map', {_lua(map_name)})
end
Utility_EraseValueFromScribbleMemory('foundry_play_map')
Utility_EraseValueFromScribbleMemory('foundry_play_arc')
Utility_LoadUtilityScript('foundry_contracts')
foundry_ReturnFromContract()
foundry_SetupContracts()
{"".join(l + chr(10) for l in free_roam)}end
"""
    return start, funcs


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
        if w.get("objects") is not None:
            # placed objects that appear later: moved to the wave's own layer
            want = {str(n).lower() for n in w["objects"] or []}
            objs = doc.get("objects") or []
            moved = [o for o in objs if str(o.get("name") or "").lower() in want]
            missing = want - {str(o.get("name") or "").lower() for o in moved}
            if missing:
                raise ValueError(f"missions/{asset_id}.json: wave {k}: no object named {', '.join(sorted(missing))} in "
                                 f"the mission's own layer (encounters/{asset_id}.json \"objects\")")
            doc["objects"] = [o for o in objs if o not in moved]
            wdoc = {"map": doc.get("map"), "objects": moved}
            for key in ("copy_loads",):
                if key in doc:
                    wdoc[key] = doc[key]
            out.append((wave_layer(asset_id, k), wdoc))
            continue
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


def play_map(asset_id, doc):
    """The mod's new map a mission is played on ("map"), or None (its arc's
    map, SW or NW, as usual)."""
    m = str(doc.get("map") or "").strip().lower()
    return m if m and m != parse_id(asset_id)[0] else None


def _required_cards(spec):
    """"requires_cards": {"suit": "clubs", "cards": ["J", "Q", "K"]} as the
    PDA's keys (clubs11, ...)."""
    if not spec:
        return []
    from .deck import RANKS, SUITS
    if not isinstance(spec, dict) or str(spec.get("suit") or "").lower() not in SUITS \
            or not isinstance(spec.get("cards"), list) or not spec["cards"]:
        raise ValueError("\"requires_cards\" is {\"suit\": clubs/diamonds/hearts/spades, \"cards\": [\"J\", ...]}")
    out = []
    for c in spec["cards"]:
        rank = RANKS.get(str(c).upper())
        if rank is None:
            raise ValueError(f"\"requires_cards\": {c!r} isn't a card (2 to 10, J, Q, K or A)")
        out.append(f"{str(spec['suit']).lower()}{rank}")
    return out


# Retail's intel meter (gameflow.lua): a verified card of the suit (killed or
# captured) is worth its rank for 2-10, iFaceIntel (30) for J, Q and K and
# iAceIntel (0) for the ace; the ace's contract unlocks at iTargetIntel (80).
INTEL_MARK = 80


def _required_intel(spec):
    """"requires_intel": "clubs" or {"suit": "clubs", "mark": 80} as
    (suit, mark), or None."""
    if not spec:
        return None
    from .deck import SUITS
    if isinstance(spec, str):
        spec = {"suit": spec}
    suit = str(spec.get("suit") or "").lower() if isinstance(spec, dict) else ""
    mark = spec.get("mark", INTEL_MARK) if isinstance(spec, dict) else None
    if suit not in SUITS or not isinstance(mark, (int, float)) or not 0 < mark <= 144:
        raise ValueError("\"requires_intel\" is a suit (clubs/diamonds/hearts/spades) or {\"suit\", \"mark\": "
                         "1 to 144 (retail's: 80)}")
    return (suit, int(mark))


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
    interior = str(c.get("interior") or "").lower() or None
    if interior:
        if interior not in HQ_FACTIONS:
            raise ValueError(f"\"contact\" \"interior\" is one of {', '.join(HQ_FACTIONS)}, not {interior!r}")
        if not play_map(asset_id, doc):
            raise ValueError("a contact in an HQ interior is on a mod's new map: give the mission a \"map\"")
        if c.get("position"):
            raise ValueError("\"contact\": an HQ interior's contract has no \"position\" (the HQ guard is its way in)")
    return {"id": asset_id, "map": (play_map(asset_id, doc) if c.get("position") or interior else None) or map_,
            "faction": faction, "number": number,
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
            # Deck cards that must be verified first ("requires_cards": {suit,
            # cards}): the contract's map's deck (a mod's own), else the game's.
            "requires_cards": _required_cards(doc.get("requires_cards")),
            # Retail's ace unlock ("requires_intel": a suit, or {suit, mark}):
            # offered once the suit's intel meter reaches the mark (80).
            "requires_intel": _required_intel(doc.get("requires_intel")),
            # A placed giver's rules: stays where it's put (Ai_CommandQueue
            # 'Wait'), can't be hurt (Actor_SetInvincible), and whether killing
            # it loses the contract for good.
            "stay": bool(c.get("stay")) and bool(c.get("position")),
            "invincible": bool(c.get("invincible")) and bool(c.get("position")),
            "lose": bool(c.get("lose_if_killed")) and bool(c.get("position")),
            # Played on a mod's new map ("map"): the contract takes the player
            # there (and a giver placed for it stands there); the mission stays in
            # its arc map's arc, so the game's mission code runs it as usual.
            "play": play_map(asset_id, doc),
            # Offered inside a new map's HQ (maps json "interiors"), after its
            # briefing lines, instead of by a giver of its own.
            "interior": interior,
            "briefing": _lines(f"missions/{asset_id}.json: contact briefing", c.get("briefing")) if interior else None,
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
        + (", cards = { %s }" % ", ".join(_lua(k) for k in c["requires_cards"]) if c.get("requires_cards") else "")
        + (", intel = { suit = %s, mark = %d }" % (_lua(c["requires_intel"][0]), c["requires_intel"][1])
           if c.get("requires_intel") else "")
                       + "".join(f", {k} = true" for k in ("stay", "invincible", "lose") if c.get(k))
                       + (", play = %s, arc = %s" % (_lua(c["play"]), _lua(parse_id(c["id"])[0])) if c.get("play") else "")
                       + (", interior = %s, title = %s, descr = %s,\n  briefing = %s" % (
                           _lua(c["interior"]), _lua(c["title_key"]), _lua(c["description_key"]), c["briefing"])
                          if c.get("interior") else "")))
    out.append("}")
    out += [
        "-- util_objectives' helpers call util_misc's util_SetDefault: on SW global",
        "-- has loaded it, on a new map's free roam nothing else does",
        "Utility_LoadUtilityScript('util_misc')",
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
        "if c.requires then",
        "for i, id in c.requires do",
        "if not Utility_ReadNumberFromScribbleMemory('foundry_done_' .. id) then return false end",
        "end",
        "end",
        "if c.cards then        -- Deck cards verified first: the map's own deck's while it's shown elsewhere",
        "local on = Utility_ReadStringFromScribbleMemory('foundry_deck_on')",
        "for i, k in c.cards do",
        "local s = Utility_ReadStringFromScribbleMemory(k .. 'status')",
        "if on ~= c.map and c.map ~= 'sw' and c.map ~= 'nw' then",
        "s = Utility_ReadStringFromScribbleMemory('foundry_deck_' .. c.map .. '_' .. k .. 'status')",
        "end",
        "if s ~= 'killed' and s ~= 'captured' then return false end",
        "end",
        "end",
        "if c.intel and foundry_Intel(c.intel.suit, c.map) < c.intel.mark then return false end",
        "return true",
        "end",
        "-- A suit's intel meter as retail's gameflow_GetIntelTotal: 2-10 their rank,",
        "-- J, Q, K 30, the ace 0; the map's own deck's while it's shown elsewhere.",
        "function foundry_Intel(suit, m)",
        "local on = Utility_ReadStringFromScribbleMemory('foundry_deck_on')",
        "local pre = ''",
        "if m and on ~= m and m ~= 'sw' and m ~= 'nw' then pre = 'foundry_deck_' .. m .. '_' end",
        "local total = 0",
        "for rank = 2, 14 do",
        "local s = Utility_ReadStringFromScribbleMemory(pre .. suit .. rank .. 'status')",
        "if s == 'killed' or s == 'captured' then",
        "if rank <= 10 then total = total + rank elseif rank < 14 then total = total + 30 end",
        "end",
        "end",
        "return total",
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
        "-- a mod's new map's own script sets sFoundryOnMap (SkipTo_Map stays the",
        "-- free-roam map the player came from)",
        "local map = sFoundryOnMap or Utility_ReadStringFromScribbleMemory('SkipTo_Map')",
        "local bWaiting = false",
        "for i, c in tFoundryContracts do",
        "local name = foundry_ContactName(c)",
        "if c.interior then",
        "-- offered inside its HQ (the map's interiors), not by a contact",
        "elseif c.map == map and ((foundry_IsDone(c) and not c.repeatable) or not foundry_Unlocked(c)",
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
            "Utility_WriteStringToScribbleMemory('foundry_active_faction', f)"]
        if not c.get("interior"):
            out.append(f"Objective_Remove(foundry_ContactName(tFoundryContracts[{contracts.index(c) + 1}]))")
        if c.get("play"):
            # As retail's HQ starter, then the level is the new map, whose own
            # script starts the mission (map_mission_lua); the arc map stays
            # SkipTo_Map, so the game's mission code finds it in its arc.
            # mission_accepted is set before the map loads, as the starter
            # sets it: the pause menu is built when a level loads, with SAVE
            # GAME only while it is unset (CANCEL CONTRACT while set). A
            # contract not in its arc goes the old way, for the map's script
            # to report.
            arc = _lua(parse_id(c['id'])[0])
            out += [
                "Utility_LoadUtilityScript('util_misc')",
                "Utility_WriteStringToScribbleMemory('SkipTo_Faction', f)",
                f"local n = util_MissionAssetNumToMissionSeqNum({arc}, f, {c['number']})",
                "if n then",
                "Utility_WriteNumberToScribbleMemory('SkipTo_MissionNumber', n)",
                "Utility_EraseValueFromScribbleMemory('skipto_via_cheating')",
                "Utility_WriteStringToScribbleMemory('current_faction', f)",
                "Utility_WriteNumberToScribbleMemory('current_' .. f .. '_mission', n)",
                "Utility_WriteNumberToScribbleMemory('mission_accepted', 1)",
                "else",
                f"Utility_WriteNumberToScribbleMemory('SkipTo_MissionNumber', {c['number']})",
                "Utility_WriteStringToScribbleMemory('skipto_via_cheating', 'true')",
                "Utility_EraseValueFromScribbleMemory('mission_accepted')",
                "end",
                f"Utility_WriteStringToScribbleMemory('SkipTo_Map', {_lua(parse_id(c['id'])[0])})",
                f"Utility_WriteStringToScribbleMemory('foundry_play_map', {_lua(c['play'])})",
                f"Utility_WriteStringToScribbleMemory('foundry_play_arc', {_lua(parse_id(c['id'])[0])})",
                f"Utility_WriteStringToScribbleMemory('foundry_map_start', {_lua('accepted ' + c['id'] + ', loading ' + c['play'])})",
                f"Utility_ResetToBaseLayerOnly({_lua(c['play'])})",
                "end"]
        else:
            # on a retail map: the game's cards back first (a mod's deck is
            # shown on its map; free roam on SW/NW puts it back, a contract
            # there starts without free roam)
            out += ["if Utility_ReadStringFromScribbleMemory('foundry_deck_on') then",
                    "Utility_LoadUtilityScript('foundry_deck')",
                    "if foundry_DeckLeave then foundry_DeckLeave() end",
                    "end",
                    f"DebugSkipToMission(f, {c['number']})", "end"]
    # foundry_Start(c): start a contract from its row (an HQ interior's)
    out.append("tFoundryStart = { " + ", ".join(f"[{_lua(c['id'])}] = foundry_Start_{c['id']}" for c in contracts) + " }")
    out.append("function foundry_Start(c) tFoundryStart[c.id]() end")
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


# The game reads mission.ini's MissionName list into a table of 64 at boot
# (RsMissionDataManager, 0x15F650: 16 rows of 4 entries at 0x3C0900). Past 64
# its loop stops advancing and asks Config_FindKey for the same MissionName
# forever: the game hangs before the main menu (Gaza's 12 plus the Editor
# Test's 3 on retail's 52, playtest 0.217).
MAX_MISSIONS = 64


def mission_count(text):
    """How many contracts mission.ini lists (its MissionName lines)."""
    return sum(1 for line in text.splitlines() if line.split()[:1] and line.split()[0].lower() == "missionname")


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
