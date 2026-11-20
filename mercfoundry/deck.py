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
        cards.append({"rank": rank, "name": name, "bio": str(bio), "bounty": int(bounty),
                      "actor": actor, "layer": layer, "face": str(face) if face else ""})
    if not cards:
        raise ValueError(f"{where}: no \"cards\"")
    others = doc.get("others")
    if others is not None:
        if not isinstance(others, dict) or not set(others) <= {"name", "bio", "face"}:
            raise ValueError(f"{where}: \"others\" is {{\"name\", \"bio\", \"face\"}}: what the other suits' "
                             "cards show on the map")
        others = {"name": str(others.get("name", "")), "bio": str(others.get("bio", "")),
                  "face": str(others.get("face") or "")}
    return {"map": m, "suit": suit, "cards": sorted(cards, key=lambda c: c["rank"]), "others": others}


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


def script(decks):
    """Lua for foundry_deck: every mod deck, the PDA key swap (enter/leave)
    and a map's number cards (foundry_DeckArm)."""
    rows = []
    by_map = {}
    for d in decks:                       # a map's suits (several decks: one suit each)
        by_map.setdefault(d["map"], []).append(d)
    for m, ds in by_map.items():
        cards = ",\n".join(
            "  { suit = %s, rank = %d, actor = %s, layer = %s, bounty = %d }" % (
                _lua(d["suit"]), c["rank"], _lua(c["actor"]) if c["actor"] else "false",
                _lua(c["layer"]) if c["layer"] else "false", c["bounty"])
            for d in ds for c in d["cards"] if c["rank"] <= 10 and c["actor"])
        suits = ", ".join(_lua(d["suit"]) for d in ds)
        rows.append(f" {m} = {{ suits = {{ {suits} }}, cards = {{\n{cards}\n }} }},")
    return f"""-- Generated by Mercenaries Foundry: mods' own Decks of 52 on their new maps
-- (deck/<suit>.json), played as retail's (gameflow's number cards).
tFoundryDecks = {{
{chr(10).join(rows)}
}}
-- The keys the PDA reads for a suit ('s' string, 'n' number).
function foundry_DeckKeys(suit)
local t = {{ {{ 'iCurrentIntel' .. suit, 'n' }} }}
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
if on == m then return end
if on then foundry_DeckLeave() end
for j, suit in ipairs(d.suits) do
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
end
-- Anywhere else: the map's states kept, the game's back.
function foundry_DeckLeave()
local m = Utility_ReadStringFromScribbleMemory('foundry_deck_on')
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
end
-- A map's number cards: each at large has its layer loaded and a verify
-- objective, unmarked until identified (in view, or within 30 m), as
-- retail's. Looked for every 5 s while free roam lasts (an actor that comes
-- back is a new one to arm).
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
if not Utility_ReadStringFromScribbleMemory(c.suit .. c.rank .. 'status') and c.layer
and not Utility_DoesActorExist(c.actor) and not tFoundryDeckLoaded[c.layer] then
tFoundryDeckLoaded[c.layer] = 1
Utility_ReadLevelFile(c.layer)
end
end
if hFoundryDeckTimer then Event_CancelEvent(hFoundryDeckTimer) end
foundry_DeckLook()
end
function foundry_DeckLook()
hFoundryDeckTimer = Event_RelativeTimer('foundry_DeckLook', 'b', 5.0)
local d = tFoundryDecks[sFoundryDeckMap]
if not d then return end
for i, c in ipairs(d.cards) do
if not Utility_ReadStringFromScribbleMemory(c.suit .. c.rank .. 'status') and Utility_DoesActorExist(c.actor) then
local iActor = Utility_GetActorAsInt(c.actor)
if tFoundryDeckArmed[c.actor] ~= iActor then
tFoundryDeckArmed[c.actor] = iActor
tFoundryDeckBy[iActor] = c
foundry_DeckObjective(c.suit, c)
end
end
end
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
Event_ActorInFocus('foundry_DeckFound', 'b', c.actor)
Event_ActorToActorProximity('foundry_DeckNear', 'b', 'player0', c.actor, 30, TRUE)
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
if not c or not d or c.known then return end
c.known = 1
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
Utility_WriteStringToScribbleMemory(c.suit .. c.rank .. 'status', state)
Utility_WriteStringToScribbleMemory('foundry_deck_' .. sFoundryDeckMap .. '_' .. c.suit .. c.rank .. 'status', state)
local intel = 0
for rank = 2, 13 do
local s = Utility_ReadStringFromScribbleMemory(c.suit .. rank .. 'status')
if s == 'killed' or s == 'captured' then
if rank <= 10 then intel = intel + rank else intel = intel + 30 end
end
end
Utility_WriteNumberToScribbleMemory('iCurrentIntel' .. c.suit, intel)
iFoundryDeckPay = c.bounty
if not bDead then iFoundryDeckPay = c.bounty * 2 end
Desktop_BeginVerify('foundry_DeckPay', c.suit, c.rank, state, FALSE)
end
function foundry_DeckPay()
if iFoundryDeckPay then Player_AdjustMoney(iFoundryDeckPay) end
iFoundryDeckPay = nil
end
"""
