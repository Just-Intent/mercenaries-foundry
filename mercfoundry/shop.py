"""A new map's own store (maps/<name>.json "shop"): its items in the PDA
shop, sold only there (docs/SHOP.md has how the game's shop works).

    "shop": {
      "items": [
        {"id": "hermes",                                 letters, digits, _
         "from": "template_support_p_surgicalstrike",    a game shop item it's made from
         "name": "Hermes drone missile",                 the support menu's and the shop's name
         "short": "...", "long": "...",                  shop texts (default: the name)
         "price": 90000,                                 dollars, before the shop faction's mood
         "unlock": "start" | "after sw_mafia20" | "intel spades 80",
         "aircraft": "template_...",                     an airstrike's plane, or artillery's
         "helicopter": "template_...",                   a drop's delivery helicopter
         "load": "template_..." | "self",                a drop's vehicle or crate (self: the helicopter)
         "picture": "texture", "icon": "texture",        the shop's picture, the support menu's icon
         "category": "Vehicle" | "Supply" | "Weapon"}    the shop tab (default: from's)
      ]
    }

Each item is two template copies on the map (its "templates"): the support
weapon template_support_<map>_<id> (from "from", drawing "icon", its Ammo
the copy below) and its ammunition template_sam_<map>_<id> (from "from"'s
Ammo, MonetaryCost the price, and the aircraft, helicopter and load). The
catalogue (DATAxbox/DATAPOD/itemshop.ini, written loose) is the game's 63
items plus every map's own, at most 128. The pictures load with the PDA
(the datapod load list), the icons with the HUD (the hud list).

On the map, Foundry Console's Foundry_MapShop names the store's items: the
game's own and other maps' are hidden there while it's loaded (and a map's
items are hidden on every other level, where their templates aren't loaded).
The unlocks are looked at as the map loads and every 5 s: "start" at once,
"after <contract>" once that contract is done (scribble foundry_done_<id>),
"intel <suit> <n>" once the map's deck meter for the suit reaches n; each
unlocked once per save (scribble foundry_shop_<template>). Prices follow the
shop faction's mood (the game's Mafia slot: a map's factions.json names it).
"""
import re

from .hashing import name_hash

CATEGORIES = ("Vehicle", "Supply", "Weapon")
MAX_CATALOGUE = 128
CATALOGUE = "DATAxbox/DATAPOD/itemshop.ini"


def _ident(where, v, what):
    v = str(v or "")
    if not re.fullmatch(r"template_[A-Za-z0-9_]+", v):
        raise ValueError(f"{where}: {what} {v!r} isn't a template name")
    return v


def parse_unlock(where, u):
    """("start",) | ("after", contract) | ("intel", suit, n)"""
    u = str(u if u is not None else "start").strip()
    parts = u.split()
    if parts == ["start"]:
        return ("start",)
    if len(parts) == 2 and parts[0] == "after" and re.fullmatch(r"[A-Za-z0-9_]+", parts[1]):
        return ("after", parts[1])
    if len(parts) == 3 and parts[0] == "intel" and parts[1].lower() in ("spades", "clubs", "hearts", "diamonds") \
            and parts[2].isdigit():
        return ("intel", parts[1].lower(), int(parts[2]))
    raise ValueError(f"{where}: \"unlock\": {u!r}: give \"start\", \"after <contract id>\" or "
                     f"\"intel <suit> <n>\"")


def is_open(where, doc):
    """maps json "shop" "open": true: the PDA shop is open on the map whatever
    the game's lock (retail's first Mafia contract opens it), with Foundry
    Console's Foundry_MapShopOpen; the game's lock stays for saves and other
    levels."""
    shop = doc.get("shop")
    if not isinstance(shop, dict):
        return False
    v = shop.get("open", False)
    if v not in (True, False):
        raise ValueError(f"{where}: \"shop\" \"open\" is true (open here whatever the game's lock) or false")
    return v


def items(where, map_name, doc):
    """maps json "shop" as a list of items (dicts with their generated names),
    checked as far as the json alone can be; [] when there's no shop."""
    shop = doc.get("shop")
    if not shop:
        return []
    if not isinstance(shop, dict) or not isinstance(shop.get("items"), list):
        raise ValueError(f"{where}: \"shop\" is {{\"items\": [...]}}")
    m = map_name.lower()
    out, seen = [], set()
    for n, it in enumerate(shop["items"], 1):
        w = f"{where}: \"shop\" item {n}"
        if not isinstance(it, dict):
            raise ValueError(f"{w}: an item is {{\"id\", \"from\", \"name\", \"price\", ...}}")
        iid = str(it.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_]+", iid):
            raise ValueError(f"{w}: \"id\" {iid!r}: letters, digits and _")
        if iid.lower() in seen:
            raise ValueError(f"{w}: two items with the id {iid!r}")
        seen.add(iid.lower())
        w = f"{where}: \"shop\" {iid}"
        src = _ident(w, it.get("from"), "\"from\"")
        name = str(it.get("name") or "").strip()
        if not name:
            raise ValueError(f"{w}: give it a \"name\"")
        try:
            price = int(it.get("price"))
        except (TypeError, ValueError):
            raise ValueError(f"{w}: \"price\" is a whole number of dollars") from None
        if price < 0:
            raise ValueError(f"{w}: \"price\" can't be below 0")
        cat = it.get("category")
        if cat is not None and str(cat) not in CATEGORIES:
            raise ValueError(f"{w}: \"category\" is one of {', '.join(CATEGORIES)}")
        load = it.get("load")
        if load is not None and str(load) != "self":
            _ident(w, load, "\"load\"")
        for k in ("aircraft", "helicopter"):
            if it.get(k) is not None:
                _ident(w, it[k], f"\"{k}\"")
        for k in ("picture", "icon"):
            if it.get(k) is not None and not re.fullmatch(r"[A-Za-z0-9_]+", str(it[k])):
                raise ValueError(f"{w}: \"{k}\" is a texture name")
        key = f"{m}_{iid.lower()}"
        out.append({
            "id": iid, "from": src, "name": name,
            "short": str(it.get("short") or name), "long": str(it.get("long") or it.get("short") or name),
            "price": price, "unlock": parse_unlock(w, it.get("unlock")),
            "aircraft": it.get("aircraft"), "helicopter": it.get("helicopter"),
            "load": None if load is None else str(load),
            "picture": it.get("picture"), "icon": it.get("icon"),
            "category": None if cat is None else str(cat),
            "weapon": f"template_support_{key}", "ammo": f"template_sam_{key}",
            "text": f"support.{m}.{iid.lower()}", "shop_text": f"shop.{m}.{iid.lower()}",
            "where": w,
        })
    return out


def catalogue_blocks(text):
    """The game's itemshop.ini as [(fields dict)], in order."""
    out = []
    for b in re.split(r"^\s*\[ShopItem\]\s*$", text, flags=re.M)[1:]:
        out.append(dict(re.findall(r"^\s*(\w+)\s+(\S+)", b, re.M)))
    return out


def template_entries(shop_items, game_catalogue, props_of, known):
    """The map's "templates" copies for its items. game_catalogue: the
    game's itemshop.ini blocks; props_of(template) -> {property (lower):
    value} of a game template (or None); known(template) -> True if a game
    template or one of the map's own copies. Fills each item's
    "category"/"picture"/"icon" defaults from its "from" and checks the
    aircraft, helicopter and load fit the kind of item it's made from."""
    by_tmpl = {b.get("Template_object_name", "").lower(): b for b in game_catalogue}
    out = []
    for it in shop_items:
        w = it["where"]
        block = by_tmpl.get(it["from"].lower())
        if block is None:
            raise ValueError(f"{w}: \"from\": {it['from']} isn't one of the game's shop items "
                             f"(DATAPOD/itemshop.ini)")
        weapon = props_of(it["from"]) or {}
        ammo_src = str(weapon.get("ammo") or "")
        ammo = props_of(ammo_src) if ammo_src else None
        if not ammo:
            raise ValueError(f"{w}: {it['from']} has no ammunition template the build can find ({ammo_src!r})")
        cat = block.get("Category", "Weapon")
        it["category"] = it["category"] or (cat if cat in CATEGORIES else "Weapon")
        it["picture"] = it["picture"] or block.get("Texture")
        it["icon"] = it["icon"] or weapon.get("hudtexturename")
        aset = {"MonetaryCost": str(it["price"])}
        if it["aircraft"]:
            if "airstrikename" in ammo:
                aset["AirstrikeName"] = it["aircraft"]
            elif "artilleryairplanetemplatename" in ammo:
                aset["ArtilleryAirplaneTemplateName"] = it["aircraft"]
            else:
                raise ValueError(f"{w}: \"aircraft\": {it['from']} is a delivery, not an airstrike or artillery "
                                 f"(give \"helicopter\" and \"load\")")
        for key, prop in (("helicopter", "deliveryhelicoptertemplatename"), ("load", "spawnobjecttemplatename")):
            if it[key]:
                if prop not in ammo:
                    raise ValueError(f"{w}: \"{key}\": {it['from']} isn't a delivery (it calls an aircraft: give "
                                     f"\"aircraft\")")
                aset[{"helicopter": "deliveryHelicopterTemplateName",
                      "load": "spawnObjectTemplateName"}[key]] = it[key]
        for key in ("aircraft", "helicopter", "load"):
            v = it[key]
            if v and v != "self" and not known(v):
                raise ValueError(f"{w}: \"{key}\": no template {v!r} (a game template, or one of the map's "
                                 f"\"templates\" copies)")
        out.append({"template": ammo_src, "as": it["ammo"], "set": aset})
        wset = {"Ammo": it["ammo"], "displayName": it["text"]}
        if it["icon"]:
            wset["HudTextureName"] = it["icon"]
        out.append({"template": it["from"], "as": it["weapon"], "set": wset})
    return out


def catalogue_text(game_text, shop_items):
    """itemshop.ini: the game's blocks as they are, then each map item's."""
    nl = "\r\n" if "\r\n" in game_text else "\n"
    have = len(catalogue_blocks(game_text))
    if have + len(shop_items) > MAX_CATALOGUE:
        raise ValueError(f"the PDA shop holds {MAX_CATALOGUE} items: the game's {have} and the maps' "
                         f"{len(shop_items)} are too many")
    parts = [game_text.rstrip("\r\n"), ""]
    for it in shop_items:
        parts += ["[ShopItem]",
                  f"Template_object_name\t{it['weapon']}",
                  f"DisplayName\t\t{it['text']}",
                  f"Description\t\t{it['shop_text']}.short",
                  f"DetailedDescription\t{it['shop_text']}.long",
                  f"Category\t\t{it['category']}",
                  f"Texture\t\t\t{it['picture']}",
                  f"DefaultPrice\t\t{it['price']}",
                  ""]
    return nl.join(parts) + nl


def strings(shop_items):
    out = {}
    for it in shop_items:
        out[it["text"]] = it["name"]
        out[it["shop_text"] + ".short"] = it["short"]
        out[it["shop_text"] + ".long"] = it["long"]
    return out


def lua(prefix, map_name, shop_items, open_here=False):
    """{prefix}Shop(): the store named to Foundry Console and the unlocks
    looked at (then every 5 s)."""
    from .missions import _lua_str
    rows = []
    for it in shop_items:
        u = it["unlock"]
        if u[0] == "start":
            cond = "true"
        elif u[0] == "after":
            cond = f"Utility_ReadNumberFromScribbleMemory({_lua_str('foundry_done_' + u[1])})"
        else:
            # the live meter while the map's cards are in the PDA (the kept
            # key is only written on leaving, so it lagged a visit behind)
            key = f"foundry_deck_{map_name.lower()}_iCurrentIntel{u[1]}"
            live = (f"Utility_ReadStringFromScribbleMemory('foundry_deck_on') == {_lua_str(map_name.lower())} "
                    f"and Utility_ReadNumberFromScribbleMemory({_lua_str('iCurrentIntel' + u[1])})")
            cond = f"(({live}) or Utility_ReadNumberFromScribbleMemory({_lua_str(key)}) or 0) >= {u[2]}"
        rows.append((it["weapon"], cond))
    names = ", ".join(_lua_str(w) for w, _ in rows)
    checks = "".join(f"if {c} then {prefix}ShopUnlock({_lua_str(w)}) end\n" for w, c in rows)
    return (f"-- The map's own store (maps json \"shop\", mercfoundry/shop.py): only its\n"
            f"-- items in the PDA shop here (Foundry Console), unlocked as their rules say.\n"
            f"-- Whether an item is unlocked is asked of the shop each time (Foundry\n"
            f"-- Console): the save's player data, read after this script as a level\n"
            f"-- loads, replaces the unlocked list, so a mark kept in scribble memory\n"
            f"-- said unlocked when it wasn't (Gaza's store stayed empty, 0.277-0.285).\n"
            f"-- Without Foundry Console, once per save as before.\n"
            f"function {prefix}ShopUnlock(t)\n"
            f"local have = nil\n"
            f"if Foundry_ShopUnlocked then have = Foundry_ShopUnlocked(t) end\n"
            f"if have then return end\n"
            f"if have == nil and Utility_ReadNumberFromScribbleMemory('foundry_shop_' .. t) then return end\n"
            f"Shop_UnlockItem(t)\n"
            f"Utility_WriteNumberToScribbleMemory('foundry_shop_' .. t, 1)\n"
            f"if Foundry_Log then Foundry_Log('shop: unlocked ' .. t) end\n"
            f"end\n"
            f"function {prefix}Shop()\n"
            f"if Foundry_MapShop then Foundry_MapShop({names}) end\n"
            + (f"if Foundry_MapShopOpen then Foundry_MapShopOpen() end\n" if open_here else "") +
            # first look 2 s in: run now, before the save's list is read back,
            # every item looked locked, and each Shop_UnlockItem queued the
            # game's "new shop item" notice (0xC8560, up to 8), all shown with
            # the next real unlock (0.294)
            f"Event_RelativeTimer('{prefix}ShopUnlocks', 'b', 2.0)\n"
            f"end\n"
            f"function {prefix}ShopUnlocks()\n"
            f"{checks}"
            f"Event_RelativeTimer('{prefix}ShopUnlocks', 'b', 5.0)\n"
            f"end\n")


def add_textures_to_list(text, names):
    """A load list's text with these textures in its [texture] part (those
    not there already); None when nothing changes."""
    have = {m.lower() for m in re.findall(r"^\s*load\s+(\S+)", text, re.M | re.I)}
    add = []
    for n in names:
        if n and n.lower() not in have and n.lower() not in {a.lower() for a in add}:
            add.append(n)
    if not add:
        return None
    nl = "\r\n" if "\r\n" in text else "\n"
    low = text.lower()
    if "[texture]" in low:
        i = low.index("[texture]") + len("[texture]")
        return text[:i] + nl + "".join(f"load {t}{nl}" for t in add) + text[i:]
    return text.rstrip() + f"{nl}{nl}[texture]{nl}{nl}" + "".join(f"load {t}{nl}" for t in add)


LIST_KEYS = {"datapod": name_hash("datapod"), "hud": name_hash("hud")}
