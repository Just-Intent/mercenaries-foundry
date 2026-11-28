"""A mod's own Deck of 52 on its new map (deck/<suit>.json), played as
retail's (gameflow.lua's number cards; the PDA's Deck screen in
RsDataPodDisplay.cpp; the verify screen in RsFrontEndCanvas.cpp).

How retail's deck works:
- A card's state is in scribble memory, '<suit><rank>status' ('killed',
  'captured', or 'face' for face cards not yet in play; none while at
  large), with '<suit><rank>info' ('newinfo': intel mail read),
  '<suit><rank>identified', '<suit><rank>statusdisplayed' (the PDA's) and
  the suit's intel meter 'iCurrentIntel<suit>'. The PDA reads those keys
  itself, with the card's text (cards.<suit>.<rank>.Name, .charName, .bio)
  and portrait (texture <suit><rank>_face, 128x128, the rank and suit drawn
  in its corner).
- Number cards (2-10) are actors deck_<suit>NN in layers <map>_deck_<suit>NN,
  loaded in free roam while the card is at large (gameflow_LoadLayers).
  Each gets a secondary verify objective (mission_ObjectiveVerify, blip of
  the suit, a diamond), unmarked until identified: in view, or within 30 m.
  Verified (killed or subdued), its state is written and the verify screen
  shows (Desktop_BeginVerify); then the bounty is paid: chapter x 25,000
  captured, half that killed.
- Face cards and aces come through contracts (mission.lua's completion).

A mod's deck on its new map: the map's own texts and portraits (map text
and HUD art, shown there with Foundry Console), its own card states kept
under foundry_deck_<map>_<key>, and swapped into the keys the PDA reads
while free roam is on that map (foundry_DeckEnter); free roam on SW or NW
puts the game's back (foundry_DeckLeave, from foundry_layers). The game's
own states wait under foundry_deck_keep_<key> meanwhile, saved with the
game, so a save made on the map loads right."""

import re

SUITS = ("clubs", "diamonds", "hearts", "spades")
RANKS = {**{str(n): n for n in range(2, 11)}, "J": 11, "Q": 12, "K": 13, "A": 14,
         "11": 11, "12": 12, "13": 13, "14": 14}
RANK_NAMES = {2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight", 9: "Nine",
              10: "Ten", 11: "Jack", 12: "Queen", 13: "King", 14: "Ace"}
SCRIPT = "foundry_deck"
DEFAULT_BOUNTY = 12500          # retail, chapter 1: 25,000 captured, half killed


def _lua(v):
    from .missions import _lua as lua
    return lua(v)


def face_texture(map_name, suit, rank):
    return f"{map_name.lower()}_{suit}{rank}_face"


def parse(where, doc, new_maps, mod_folder=None):
    """deck/<suit>.json checked: {"suit", "map"?, "cards": [{"card": "2".."10"
    | "J" | "Q" | "K" | "A", "name", "alias"?, "role"?, "description" | "bio",
    "bounty"?, "face"? (a PNG in the mod), "actor"? (a number card's placed
    character), "layer"? (loaded while it's at large)}]}. "map" may be left
    out when the mod has one new map."""
    if not isinstance(doc, dict):
        raise ValueError(f"{where}: expected {{\"suit\", \"cards\": [...]}}")
    suit = str(doc.get("suit") or "").lower()
    if suit not in SUITS:
        raise ValueError(f"{where}: \"suit\" is one of {', '.join(SUITS)}")
    m = doc.get("map")
    if m is None:
        if len(new_maps) != 1:
            raise ValueError(f"{where}: give \"map\" (the new map this deck is on)")
        m = next(iter(new_maps))
    m = str(m).lower()
    if m not in new_maps:
        raise ValueError(f"{where}: \"map\": {m!r} isn't a mod's new map (maps/<name>.json)")
    cards, seen = [], set()
    for i, c in enumerate(doc.get("cards") or []):
        w = f"{where}: card {i + 1}"
        rank = RANKS.get(str(c.get("card", "")).upper())
        if rank is None:
            raise ValueError(f"{w}: \"card\" is 2 to 10, J, Q, K or A")
        if rank in seen:
            raise ValueError(f"{w}: {suit} {rank} twice")
        seen.add(rank)
        w = f"{where}: {RANK_NAMES[rank]} of {suit.title()}"
        name = str(c.get("name") or "")
        if not name:
            raise ValueError(f"{w}: needs a \"name\"")
        bio = c.get("bio")
        if bio is None:
            alias, role, text = str(c.get("alias") or ""), str(c.get("role") or ""), str(c.get("description") or "")
            bio = " ".join(p for p in (f"Known as {alias}." if alias else "", f"{role}." if role else "", text) if p)
        bounty = c.get("bounty", DEFAULT_BOUNTY)
        if not isinstance(bounty, (int, float)) or bounty < 0:
            raise ValueError(f"{w}: \"bounty\" is money (paid for a kill; taken alive pays double)")
        actor = str(c.get("actor") or "")
        layer = str(c.get("layer") or "")
        if layer and not actor:
            raise ValueError(f"{w}: a \"layer\" needs the card's \"actor\" (its name in that layer)")
        face = c.get("face")
        if face is None and mod_folder is not None:
            for cand in (f"deck/{suit}{rank}.png", f"deck/{suit}_{rank}.png", f"deck/{suit}/{rank}.png"):
                if (mod_folder / cand).is_file():
                    face = cand
                    break
        # 0.393: the card's intel mail (retail's mail_cardinfo_<suit><rank>),
        # posted after a contract on the map; "where" goes in a generated one
        where_ = c.get("where")
        if where_ is not None and not isinstance(where_, str):
            raise ValueError(f"{w}: \"where\" is text (where he's to be found, for his intel mail)")
        intel = c.get("intel")
        if intel is not None:
            if intel is False:
                pass
            elif not isinstance(intel, dict) or not set(intel) <= {"subject", "body", "sender", "after"} \
                    or (set(intel) & {"subject", "body"} and not (intel.get("subject") and intel.get("body"))):
                raise ValueError(f"{w}: \"intel\" is {{\"subject\", \"body\", \"sender\"?, \"after\"?}} (or false: "
                                 "no intel mail); \"after\" alone keeps the generated text")
            elif intel.get("after") is not None and not (isinstance(intel["after"], str)
                                                        and re.fullmatch(r"[A-Za-z0-9_]+", intel["after"])):
                raise ValueError(f"{w}: the intel's \"after\" names the contract (its id) whose completion "
                                 "sends this mail")
        if rank > 10 and intel:
            raise ValueError(f"{w}: only number cards (2-10) have intel mails (face cards and aces "
                             "come through contracts)")
        cards.append({"rank": rank, "name": name, "bio": str(bio), "bounty": int(bounty),
                      "actor": actor, "layer": layer, "face": str(face) if face else "",
                      "where": str(where_ or ""), "role": str(c.get("role") or ""),
                      "alias": str(c.get("alias") or ""),
                      "intel": None if intel is False else {
                          "subject": str((intel or {}).get("subject") or ""),
                          "body": str((intel or {}).get("body") or ""),
                          "sender": str((intel or {}).get("sender") or "mail.senders.techgal"),
                          "after": str((intel or {}).get("after") or "").lower()}})
    if not cards:
        raise ValueError(f"{where}: no \"cards\"")
    others = doc.get("others")
    if others is not None:
        if not isinstance(others, dict) or not set(others) <= {"name", "bio", "face"}:
            raise ValueError(f"{where}: \"others\" is {{\"name\", \"bio\", \"face\"}}: what the other suits' "
                             "cards show on the map")
        others = {"name": str(others.get("name", "")), "bio": str(others.get("bio", "")),
                  "face": str(others.get("face") or "")}
    mail = doc.get("unlock_mail")
    if mail is not None:
        if not isinstance(mail, dict) or not mail.get("subject") or not mail.get("body") \
                or not set(mail) <= {"sender", "subject", "body", "vo", "mark"}:
            raise ValueError(f"{where}: \"unlock_mail\" is {{\"sender\"?, \"subject\", \"body\", \"vo\"?, \"mark\"?}}: "
                             "posted once per save when the suit's intel meter first reaches the mark (retail's "
                             "ch<chapter>_ace_unlock)")
        mark = mail.get("mark", INTEL_MARK)
        if not isinstance(mark, (int, float)) or not 0 < mark <= 144:
            raise ValueError(f"{where}: the unlock mail's \"mark\" is 1 to 144 (retail's: {INTEL_MARK})")
        vo = mail.get("vo")
        if vo is not None and not re.fullmatch(r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+", str(vo)):
            raise ValueError(f"{where}: the unlock mail's \"vo\" is a sound cue <bank>.<cue>, like fio.xfio085")
        mail = {"sender": str(mail.get("sender") or "mail.senders.techgal"), "subject": str(mail["subject"]),
                "body": str(mail["body"]), "vo": str(vo) if vo else "", "mark": int(mark)}
    # "after": a contract (its id) that must be done before the suit's number
    # cards appear (retail's acts: spades after the Ace of Clubs)
    after = doc.get("after")
    if after is not None and not (isinstance(after, str) and re.fullmatch(r"[A-Za-z0-9_]+", after)):
        raise ValueError(f"{where}: \"after\" names the contract (its id, like sw_allies22) that must be done "
                         "before this suit's number cards appear")
    # "until": a contract (its id) whose completion ends the suit's act: its
    # number cards still at large leave the map (not verified, no bounty), as
    # retail's finished acts' cards are gone
    until = doc.get("until")
    if until is not None and not (isinstance(until, str) and re.fullmatch(r"[A-Za-z0-9_]+", until)):
        raise ValueError(f"{where}: \"until\" names the contract (its id, like sw_allies21) whose completion "
                         "takes this suit's number cards off the map")
    return {"map": m, "suit": suit, "cards": sorted(cards, key=lambda c: c["rank"]), "others": others,
            "unlock_mail": mail, "after": after.lower() if after else None,
            "until": until.lower() if until else None}


# Retail's ace unlock (gameflow.lua): at iTargetIntel the suit's ace mail
# (ch<chapter>_ace_unlock) and Fiona's line (fio.xfio085-088, by chapter).
INTEL_MARK = 80


def unlock_mail(deck):
    """(mail id, sender, subject key, body key, strings) of a deck's
    "unlock_mail", or None. A sender that isn't a game sender key
    (mail.senders.*) becomes a string of the mod's own."""
    m = deck.get("unlock_mail")
    if not m:
        return None
    key = "Deck" + re.sub(r"[^A-Za-z0-9]", "", deck["map"].title()) + deck["suit"].title()
    base = f"MissionInfo.FoundryMail.{key}"
    strings = {f"{base}.Subject": m["subject"], f"{base}.Body": m["body"]}
    sender = m["sender"]
    if not re.fullmatch(r"(?i)mail\.[A-Za-z0-9_.]+", sender):
        strings[f"{base}.Sender"] = sender
        sender = f"{base}.Sender"
    return (f"foundry_deck_{deck['map']}_{deck['suit']}_unlock", sender, f"{base}.Subject", f"{base}.Body", strings)


def intel_mails(deck):
    """[(rank, mail id, sender, subject key, body key, strings, after)] for a
    deck's number cards at large on the map (an "actor"): each one's intel
    mail, retail's mail_cardinfo_<suit><rank> (in the Deck folder, from
    techgal). Its text is the card's "intel", or made from his name, alias,
    role, description and "where"; "intel": false gives none."""
    out = []
    key = "Deck" + re.sub(r"[^A-Za-z0-9]", "", deck["map"].title()) + deck["suit"].title()
    for c in deck["cards"]:
        if c["rank"] > 10 or not c["actor"] or c.get("intel") is None:
            continue
        i = c["intel"]
        card = f"the {RANK_NAMES[c['rank']]} of {deck['suit'].title()}"
        subject = i["subject"] or f"Intel: {c['name']}, {card}"
        body = i["body"]
        if not body:
            who = c["name"] + (f", known as {c['alias']}" if c.get("alias") and c["alias"] not in c["bio"] else "")
            parts = [f"We have a lead on {who}, {card}."]
            if c.get("role") and c["role"] not in c["bio"]:
                parts.append(f"{c['role']}.")
            if c["bio"]:
                parts.append(c["bio"])
            where_ = c.get("where", "").strip().rstrip(".")
            if where_:
                # a bare place ("bureij"): "in Bureij"
                if not re.match(r"(?i)(in|at|near|on|under|around|outside|by|between|along|inside)\b", where_):
                    where_ = "in " + (where_.title() if where_ == where_.lower() else where_)
                parts.append(f"Our sources put him {where_}.")
            parts.append("He's marked on your PDA once you're close. Verify him: alive is worth double.")
            body = " ".join(parts)
        base = f"MissionInfo.FoundryMail.{key}{c['rank']}"
        strings = {f"{base}.Subject": subject, f"{base}.Body": body}
        sender = i["sender"]
        if not re.fullmatch(r"(?i)mail\.[A-Za-z0-9_.]+", sender):
            strings[f"{base}.Sender"] = sender
            sender = f"{base}.Sender"
        out.append((c["rank"], f"foundry_deck_{deck['map']}_{deck['suit']}{c['rank']}_intel", sender,
                    f"{base}.Subject", f"{base}.Body", strings, i["after"]))
    return out


# retail's card intel mails go in the PDA's Deck folder (emails.ini FolderPath)
INTEL_FOLDER = "mail.subject.deck"
# retail's card identified within this range once its intel is read (300; 30 without)
INTEL_IDENT_DIST = 300


def other_face_texture(map_name):
    return f"{map_name.lower()}_deck_other_face"


def strings(deck):
    """The texts the PDA and verify screen show for the deck's cards."""
    out = {}
    for c in deck["cards"]:
        k = f"cards.{deck['suit']}.{c['rank']}"
        out[f"{k}.Name"] = f"{RANK_NAMES[c['rank']]} of {deck['suit'].title()}"
        out[f"{k}.charName"] = c["name"]
        out[f"{k}.bio"] = c["bio"]
    return out


def others_strings(others, used):
    """"others": the stand-in name and bio for every card of the suits no
    deck on the map uses (used: the map's decks' suits)."""
    out = {}
    for suit in SUITS:
        if suit in used:
            continue
        for rank in range(2, 15):
            out[f"cards.{suit}.{rank}.charName"] = others["name"]
            out[f"cards.{suit}.{rank}.bio"] = others["bio"]
    return out


def script(decks, mails=None, intel=None):
    """Lua for foundry_deck: every mod deck, the PDA key swap (enter/leave),
    a map's number cards (foundry_DeckArm), its suits' unlock mails
    (mails: {(map, suit): (mail id, place in emails.ini)}) and its cards'
    intel mails (intel: {(map, suit, rank): (mail id, place, after)})."""
    mails = mails or {}
    intel = intel or {}
    rows = []
    by_map = {}
    for d in decks:                       # a map's suits (several decks: one suit each)
        by_map.setdefault(d["map"], []).append(d)

    def mail_of(d, c):
        i = intel.get((d["map"], d["suit"], c["rank"]))
        if not i:
            return ""
        return ", mail = %s, posted = %d, mailafter = %s" % (_lua(i[0]), i[1] + 2, _lua(i[2]) if i[2] else "false")
    for m, ds in by_map.items():
        cards = ",\n".join(
            "  { suit = %s, rank = %d, actor = %s, layer = %s, bounty = %d, after = %s, till = %s%s }" % (
                _lua(d["suit"]), c["rank"], _lua(c["actor"]) if c["actor"] else "false",
                _lua(c["layer"]) if c["layer"] else "false", c["bounty"],
                _lua(d["after"]) if d.get("after") else "false",
                _lua(d["until"]) if d.get("until") else "false", mail_of(d, c))
            for d in ds for c in d["cards"] if c["rank"] <= 10 and c["actor"])
        suits = ", ".join(_lua(d["suit"]) for d in ds)
        unlock = ", ".join(
            "%s = { id = %s, posted = %d, mark = %d, vo = %s }" % (
                d["suit"], _lua(mails[(m, d["suit"])][0]), mails[(m, d["suit"])][1] + 2, d["unlock_mail"]["mark"],
                _lua(d["unlock_mail"]["vo"]) if d["unlock_mail"]["vo"] else "false")
            for d in ds if d.get("unlock_mail") and (m, d["suit"]) in mails)
        # each suit's act, in retail's suit order: what the PDA's page and bar
        # follow there ('chapter', foundry_DeckChapter; 0.337)
        order = ("clubs", "diamonds", "hearts", "spades")
        acts = ", ".join(
            "{ suit = %s, after = %s, till = %s }" % (
                _lua(d["suit"]), _lua(d["after"]) if d.get("after") else "false",
                _lua(d["until"]) if d.get("until") else "false")
            for d in sorted(ds, key=lambda d: order.index(d["suit"]) if d["suit"] in order else 9))
        rows.append(f" {m} = {{ suits = {{ {suits} }}, acts = {{ {acts} }}, unlock = {{ {unlock} }}, cards = {{\n{cards}\n }} }},")
    return f"""-- Generated by Mercenaries Foundry: mods' own Decks of 52 on their new maps
-- (deck/<suit>.json), played as retail's (gameflow's number cards).
tFoundryDecks = {{
{chr(10).join(rows)}
}}
-- The keys the PDA reads for a suit ('s' string, 'n' number).
function foundry_DeckKeys(suit)
local t = {{ {{ 'iCurrentIntel' .. suit, 'n' }} }}
-- the meter the PDA has shown so far (its own, 0xCB070/0xCB120 write it,
-- 0xCDD70 reads it): kept per map too, or back on SW it counted up again
local cap = {{ clubs = 'Clubs', diamonds = 'Diamonds', hearts = 'Hearts', spades = 'Spades' }}
table.insert(t, {{ 'displayedIntel' .. cap[suit], 'n' }})
for rank = 2, 14 do
table.insert(t, {{ suit .. rank .. 'status', 's' }})
table.insert(t, {{ suit .. rank .. 'info', 's' }})
table.insert(t, {{ suit .. rank .. 'identified', 'n' }})
table.insert(t, {{ suit .. rank .. 'statusdisplayed', 'n' }})
end
return t
end
function foundry_DeckCopy(from, to, kind)
local v
if kind == 's' then v = Utility_ReadStringFromScribbleMemory(from) else v = Utility_ReadNumberFromScribbleMemory(from) end
if v == nil then
Utility_EraseValueFromScribbleMemory(to)
elseif kind == 's' then
Utility_WriteStringToScribbleMemory(to, v)
else
Utility_WriteNumberToScribbleMemory(to, v)
end
end
-- Free roam on a deck's map: the game's states kept aside, the map's shown.
function foundry_DeckEnter(m)
local d = tFoundryDecks[m]
if not d then return end
local on = Utility_ReadStringFromScribbleMemory('foundry_deck_on')
if Foundry_Log then Foundry_Log('deck: entering ' .. m .. ' (in the PDA: ' .. tostring(on or 'the game') .. ')') end
if on == m then foundry_DeckMails(m) foundry_DeckChapter(m) return end
if on then foundry_DeckLeave() end
-- every suit, the map's unused ones too: they show its stand-in cards
-- ("others"), and kept the game's states and meters (0.303: Gaza's
-- PDA showed SW's diamonds and hearts intel)
for j, suit in ipairs({{ 'clubs', 'diamonds', 'hearts', 'spades' }}) do
for i, k in ipairs(foundry_DeckKeys(suit)) do
foundry_DeckCopy(k[1], 'foundry_deck_keep_' .. k[1], k[2])
foundry_DeckCopy('foundry_deck_' .. m .. '_' .. k[1], k[1], k[2])
end
if not Utility_ReadNumberFromScribbleMemory('iCurrentIntel' .. suit) then   -- retail always has one
Utility_WriteNumberToScribbleMemory('iCurrentIntel' .. suit, 0)
end
for rank = 11, 14 do       -- face cards come through contracts, as retail's
if not Utility_ReadStringFromScribbleMemory(suit .. rank .. 'status') then
Utility_WriteStringToScribbleMemory(suit .. rank .. 'status', 'face')
end
end
Utility_WriteNumberToScribbleMemory('foundry_deck_on_' .. suit, 1)   -- swapped: put back on leaving
end
Utility_WriteStringToScribbleMemory('foundry_deck_on', m)
foundry_DeckMails(m)
foundry_DeckChapter(m)
end
-- The PDA's Deck opens on the page of 'chapter' (1 clubs .. 4 spades) and
-- draws the intel bar there only (0xCB040, 0xC7950): on a deck's map it's
-- the suit whose act is on (its "after" done, its "until" not yet; once all
-- are over, the last begun), the game's kept and put back on leaving. Without
-- it Gaza's PDA stayed on clubs, with their bar, after the clubs act (0.337).
function foundry_DeckChapter(m)
local d = tFoundryDecks[m]
if not d or not d.acts then return end
local pick = nil
for i, a in ipairs(d.acts) do
local begun = not a.after or Utility_ReadNumberFromScribbleMemory('foundry_done_' .. a.after)
local over = a.till and Utility_ReadNumberFromScribbleMemory('foundry_done_' .. a.till)
if begun then
pick = a.suit
if not over then break end
end
end
if not pick then return end
local n = ({{ clubs = 1, diamonds = 2, hearts = 3, spades = 4 }})[pick]
if not Utility_ReadNumberFromScribbleMemory('foundry_deck_keep_chapter_set') then
foundry_DeckCopy('chapter', 'foundry_deck_keep_chapter', 'n')
Utility_WriteNumberToScribbleMemory('foundry_deck_keep_chapter_set', 1)
end
if Utility_ReadNumberFromScribbleMemory('chapter') ~= n then
Utility_WriteNumberToScribbleMemory('chapter', n)
if Foundry_Log then Foundry_Log('deck: ' .. m .. ' on ' .. pick .. ' (the PDA opens there, its bar only)') end
end
end
-- Anywhere else: the map's states kept, the game's back.
function foundry_DeckLeave()
local m = Utility_ReadStringFromScribbleMemory('foundry_deck_on')
if Foundry_Log then Foundry_Log('deck: leaving ' .. tostring(m or 'none')) end
if not m then return end
local old = Utility_ReadStringFromScribbleMemory('foundry_deck_suit')   -- 0.201-0.208 saves: one suit
for j, suit in ipairs({{ 'clubs', 'diamonds', 'hearts', 'spades' }}) do
if Utility_ReadNumberFromScribbleMemory('foundry_deck_on_' .. suit) or old == suit then
for i, k in ipairs(foundry_DeckKeys(suit)) do
foundry_DeckCopy(k[1], 'foundry_deck_' .. m .. '_' .. k[1], k[2])
foundry_DeckCopy('foundry_deck_keep_' .. k[1], k[1], k[2])
Utility_EraseValueFromScribbleMemory('foundry_deck_keep_' .. k[1])
end
Utility_EraseValueFromScribbleMemory('foundry_deck_on_' .. suit)
end
end
Utility_EraseValueFromScribbleMemory('foundry_deck_on')
Utility_EraseValueFromScribbleMemory('foundry_deck_suit')
if Utility_ReadNumberFromScribbleMemory('foundry_deck_keep_chapter_set') then   -- the game's chapter back
foundry_DeckCopy('foundry_deck_keep_chapter', 'chapter', 'n')
Utility_EraseValueFromScribbleMemory('foundry_deck_keep_chapter')
Utility_EraseValueFromScribbleMemory('foundry_deck_keep_chapter_set')
end
end
-- The suit's intel meter, as retail's gameflow_UpdatePDAIntelMeter: every
-- verified card of the suit (killed or captured) worth its rank for 2-10,
-- 30 for J, Q and K, 0 for the ace; where the PDA reads it, and the map's own.
function foundry_DeckMeter(suit)
local intel = 0
for rank = 2, 13 do
local s = Utility_ReadStringFromScribbleMemory(suit .. rank .. 'status')
if s == 'killed' or s == 'captured' then
if rank <= 10 then intel = intel + rank else intel = intel + 30 end
end
end
Utility_WriteNumberToScribbleMemory('iCurrentIntel' .. suit, intel)
local m = Utility_ReadStringFromScribbleMemory('foundry_deck_on')
if m then
Utility_WriteNumberToScribbleMemory('foundry_deck_' .. m .. '_iCurrentIntel' .. suit, intel)
foundry_DeckUnlock(m, suit, intel, TRUE)
end
return intel
end
-- The suit's unlock mail (deck "unlock_mail"), as retail's ace unlock: posted
-- once the meter reaches its mark, once per save (scribble foundry_mail_<id>
-- holds its place in emails.ini: a save that got another mail there gets it
-- again), with Fiona's line after the verify screen the first time. Posted
-- again on coming back to the map (Mail_Post does nothing for a mail in the
-- inbox; a trip to another map can drop it until the save loads again).
function foundry_DeckUnlock(m, suit, intel, bNow)
local d = tFoundryDecks[m]
local u = d and d.unlock[suit]
if not u or intel < u.mark then return end
local first = Utility_ReadNumberFromScribbleMemory('foundry_mail_' .. u.id) ~= u.posted
Utility_WriteNumberToScribbleMemory('foundry_mail_' .. u.id, u.posted)
Mail_Post(u.id)
if first and bNow and u.vo then
sFoundryDeckVo = u.vo
Event_RelativeTimer('foundry_DeckUnlockVo', 'b', 2)
end
end
function foundry_DeckUnlockVo()
if sFoundryDeckVo and not Audio_IsFionaDialogPlaying() then Audio_PlayVoiceover(sFoundryDeckVo) end
sFoundryDeckVo = nil
end
-- Coming to the map: any mail whose meter is at its mark (a save from before
-- the mail existed, or one dropped from the inbox).
function foundry_DeckMails(m)
local d = tFoundryDecks[m]
if not d then return end
for i, suit in ipairs(d.suits) do
foundry_DeckUnlock(m, suit, Utility_ReadNumberFromScribbleMemory('iCurrentIntel' .. suit) or 0, FALSE)
end
-- and the intel mails of cards still at large (0.393)
for i, c in ipairs(d.cards) do
if c.mail and foundry_DeckHasIntel(c) and not Utility_ReadStringFromScribbleMemory(c.suit .. c.rank .. 'status') then
Utility_WriteNumberToScribbleMemory('foundry_mail_' .. c.mail, c.posted)
Mail_Post(c.mail)
end
end
end
-- A card's intel mail read (retail's '<suit><rank>info' = 'newinfo').
function foundry_DeckHasIntel(c)
return Utility_ReadStringFromScribbleMemory(c.suit .. c.rank .. 'info') == 'newinfo'
end
-- 0.393: a contract on the map done (foundry_ReturnFromContract): the next
-- card's intel mail, as retail's mission_MissionComplete3 posts the
-- contract's card's (cards.lua; mail_cardinfo_<suit><rank>, once, while the
-- card is at large). A card whose intel names this contract ("after") goes
-- first; otherwise the lowest card at large without intel yet, of the
-- first suit in play (its "after" done, its "until" not).
function foundry_DeckIntel(m, sContract)
local d = tFoundryDecks[m]
if not d then return end
local function ok(c)
return c.mail and not foundry_DeckHasIntel(c) and not foundry_DeckOver(c)
and not Utility_ReadStringFromScribbleMemory(c.suit .. c.rank .. 'status')
and (not c.after or Utility_ReadNumberFromScribbleMemory('foundry_done_' .. c.after))
end
local pick = nil
for i, c in ipairs(d.cards) do
if not pick and c.mailafter == sContract and ok(c) then pick = c end
end
for a, act in ipairs(d.acts) do
for i, c in ipairs(d.cards) do
if not pick and c.suit == act.suit and not c.mailafter and ok(c) then pick = c end
end
end
if not pick then
if Foundry_Log then Foundry_Log('deck: no card intel to send after ' .. tostring(sContract)) end
return
end
Utility_WriteStringToScribbleMemory(pick.suit .. pick.rank .. 'info', 'newinfo')
Utility_WriteStringToScribbleMemory('foundry_deck_' .. m .. '_' .. pick.suit .. pick.rank .. 'info', 'newinfo')
Utility_WriteNumberToScribbleMemory('foundry_mail_' .. pick.mail, pick.posted)
Mail_Post(pick.mail)
Ui_MessageBoxPrint('[interface.intelemailreceived.msg]')
if Foundry_Log then Foundry_Log('deck: intel on ' .. pick.suit .. pick.rank .. ' after ' .. tostring(sContract)) end
end
-- A map's number cards: each at large has its layer loaded and, while it's
-- awake, a verify objective, unmarked until identified (in view, or within
-- 30 m), as retail's (gameflow_LoadLayers, gameflow_CardIsDormant): the
-- objective and its events are made when the card wakes and taken away when
-- it goes dormant, never kept on a dormant actor. Looked for every 5 s while
-- free roam lasts (an actor that comes back is a new one to watch).
function foundry_DeckArm(m)
local d = tFoundryDecks[m]
if not d then return end
sFoundryDeckMap = m
Utility_LoadUtilityScript('mission_objective')
Utility_LoadUtilityScript('mission_objectiveverify')
if not tFoundryDeckArmed then tFoundryDeckArmed = {{}} end
if not tFoundryDeckLoaded then tFoundryDeckLoaded = {{}} end
tFoundryDeckBy = {{}}
for i, c in ipairs(d.cards) do
-- a suit with "after" appears once that contract is done (free roam after it runs this again);
-- with "until", it's gone once that one is
if not Utility_ReadStringFromScribbleMemory(c.suit .. c.rank .. 'status') and c.layer
and (not c.after or Utility_ReadNumberFromScribbleMemory('foundry_done_' .. c.after))
and not foundry_DeckOver(c)
and not Utility_DoesActorExist(c.actor) and not tFoundryDeckLoaded[c.layer] then
tFoundryDeckLoaded[c.layer] = 1
Utility_ReadLevelFile(c.layer)
end
end
if hFoundryDeckTimer then Event_CancelEvent(hFoundryDeckTimer) end
foundry_DeckLook()
end
-- The suit's act is over ("until" done): its cards at large leave the map.
function foundry_DeckOver(c)
return c.till and Utility_ReadNumberFromScribbleMemory('foundry_done_' .. c.till)
end
function foundry_DeckLook()
hFoundryDeckTimer = Event_RelativeTimer('foundry_DeckLook', 'b', 5.0)
local d = tFoundryDecks[sFoundryDeckMap]
if not d then return end
for i, c in ipairs(d.cards) do
if foundry_DeckOver(c) and Utility_DoesActorExist(c.actor) then
-- (its layer was loaded before the act ended: it goes now, its marker with it)
if c.hWake then Event_CancelEvent(c.hWake) c.hWake = nil end
foundry_DeckSleep(c)
Actor_Remove(c.actor)
elseif not Utility_ReadStringFromScribbleMemory(c.suit .. c.rank .. 'status') and Utility_DoesActorExist(c.actor) then
local iActor = Utility_GetActorAsInt(c.actor)
if tFoundryDeckArmed[c.actor] ~= iActor then
tFoundryDeckArmed[c.actor] = iActor
tFoundryDeckBy[iActor] = c
c.awake = nil
c.hWake = Event_ActorIsDormant('foundry_DeckWake', 'b', c.actor, FALSE)   -- when it wakes
end
end
end
end
-- Woken (bDormant false): its objective; gone dormant: none (retail's
-- gameflow_CardIsDormant). Then the next change is watched.
function foundry_DeckWake(aCard, bDormant)
local c = tFoundryDeckBy[Utility_GetActorAsInt(aCard)]
if not c then return end
c.hWake = nil
if Utility_ReadStringFromScribbleMemory(c.suit .. c.rank .. 'status') then return end   -- verified meanwhile
if bDormant then
foundry_DeckSleep(c)
else
c.awake = 1
foundry_DeckObjective(c.suit, c)
end
c.hWake = Event_ActorIsDormant('foundry_DeckWake', 'b', c.actor, not bDormant)
end
function foundry_DeckSleep(c)
c.awake = nil
c.known = nil
if c.hFocus then Event_CancelEvent(c.hFocus) c.hFocus = nil end
if c.hNear then Event_CancelEvent(c.hNear) c.hNear = nil end
mission_RemoveObjective('foundry_deck_' .. c.suit .. c.rank)
end
function foundry_DeckObjective(suit, c)
local caption = Utility_GetStringById('cards.' .. suit .. '.' .. c.rank .. '.Name')
local bKnown = Utility_ReadNumberFromScribbleMemory(suit .. c.rank .. 'identified') == 1
mission_ObjectiveVerify{{
objectiveName = 'foundry_deck_' .. suit .. c.rank,
callbackFunc = foundry_DeckVerified,
actorName = c.actor,
actorCaption = caption,
objectiveDesc = caption,
objectiveType = 'OBJECTIVE_TYPE_SECONDARY',
targetType = 'TARGET_TYPE_OBJECTIVE_SECONDARY',
markInGPS = bKnown,
markInPDA = bKnown,
markInWorld = bKnown,
blipType = suit,
drawDiamond = true,
killCallbackFunc = foundry_DeckFound,
subdueCallbackFunc = foundry_DeckFound,
}}
if not bKnown then
-- with his intel read, identified within 300 m; without, in view or within 30 m (retail's)
if foundry_DeckHasIntel(c) then
c.hNear = Event_ActorToActorProximity('foundry_DeckNear', 'b', 'player0', c.actor, {INTEL_IDENT_DIST}, TRUE)
else
c.hFocus = Event_ActorInFocus('foundry_DeckFound', 'b', c.actor)
c.hNear = Event_ActorToActorProximity('foundry_DeckNear', 'b', 'player0', c.actor, 30, TRUE)
end
end
end
function foundry_DeckNear(aPlayer, aCard)
foundry_DeckFound(aCard)
end
-- Identified: marked on the GPS, the PDA and in the world (retail's
-- gameflow_UnidentifiedCardFound).
function foundry_DeckFound(aCard)
local c = tFoundryDeckBy[Utility_GetActorAsInt(aCard)]
local d = tFoundryDecks[sFoundryDeckMap]
if not c or not d or c.known or not c.awake then return end
c.known = 1
if c.hFocus then Event_CancelEvent(c.hFocus) c.hFocus = nil end
if c.hNear then Event_CancelEvent(c.hNear) c.hNear = nil end
Utility_WriteNumberToScribbleMemory(c.suit .. c.rank .. 'identified', 1)
mission_ModifyObjective{{ targets = {{ {{ actorName = c.actor, markInGPS = true, markInPDA = true, markInWorld = true }} }},
bForceRemove = true, bForceRedraw = true }}
if not Audio_IsFionaDialogPlaying() then Audio_PlayVoiceover('fio.xfio059') end
end
-- Verified: the state (shown and the map's own), the intel meter, the
-- verify screen, then the bounty (taken alive pays double).
function foundry_DeckVerified(sCard, bDead)
local c = tFoundryDeckBy[Utility_GetActorAsInt(sCard)]
local d = tFoundryDecks[sFoundryDeckMap]
if not c or not d then return end
local state = 'captured'
if bDead then state = 'killed' end
if c.hWake then Event_CancelEvent(c.hWake) c.hWake = nil end
if c.hFocus then Event_CancelEvent(c.hFocus) c.hFocus = nil end
if c.hNear then Event_CancelEvent(c.hNear) c.hNear = nil end
Utility_WriteStringToScribbleMemory(c.suit .. c.rank .. 'status', state)
Utility_WriteStringToScribbleMemory('foundry_deck_' .. sFoundryDeckMap .. '_' .. c.suit .. c.rank .. 'status', state)
foundry_DeckMeter(c.suit)
-- his intel mail goes, as retail's (Mail_Delete('mail_cardinfo_' .. suit .. rank))
if c.mail then Mail_Delete(c.mail) end
-- a contract the meter unlocks (an ace's placed giver) is offered now
if foundry_SetupContracts then foundry_SetupContracts() end
iFoundryDeckPay = c.bounty
if not bDead then iFoundryDeckPay = c.bounty * 2 end
Desktop_BeginVerify('foundry_DeckPay', c.suit, c.rank, state, FALSE)
end
function foundry_DeckPay()
if iFoundryDeckPay then Player_AdjustMoney(iFoundryDeckPay) end
iFoundryDeckPay = nil
end
"""
