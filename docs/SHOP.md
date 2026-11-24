# The shop (Merchant of Menace) and support items

How the retail game's PDA shop and support menu work, from the decompiled
scripts, the recompiled engine code, the retail data and the game's text.
Written to plan: (a) a new map's own store, run by a faction of our choice and
under its own name; (b) items, prices, delivery and unlock rules set in the
editor; (c) new support items (airstrikes, vehicle drops, supply drops).

Evidence is cited as engine addresses (`0x...`, the recompiled
`sub_XXXXXXXX` in `external/Mercenaries-Recompiled/ports/mercenaries/src/recomp/gen/recomp_*.c`),
script `file:line` (the decompiled retail Lua), template names (retail
DATAxbox) and text keys (english.dsk). Property and key hashes are the game's
name hash (`mercfoundry.hashing.name_hash`). Anything not confirmed is marked so. Nothing here
was run in game for this write-up.

## Summary

- The shop is a page of the PDA (code file RsDataPodVM.cpp / RsDataPodDisplay.cpp),
  one engine object at `0x34ABC8`. Its catalogue is a plain text file,
  `DATAxbox/DATAPOD/itemshop.ini` (63 `[ShopItem]` blocks, room for 128), read
  at every level load. Only the support items a script or the engine has
  unlocked are shown.
- An item is a **support weapon template** (`template_support_*`, ItemWeapon)
  whose **ammo template** (`template_sam_*`) does the work: an
  `AmmoAirstrikeBeacon` calls an aircraft (`AirstrikeName`), an
  `AmmoProjectile` smoke/GPS grenade calls a helicopter
  (`deliveryHelicopterTemplateName`) carrying `spawnObjectTemplateName` (a
  vehicle, a crate, or `self`), or an artillery plane
  (`ArtilleryAirplaneTemplateName`).
- Ordering in the PDA costs nothing. It puts the item in the support menu
  (it replaces the previous shop order). You pay when you call it in: the
  ammo's `MonetaryCost`, scaled by the Mafia's mood, is charged as the
  helicopter or aircraft is sent.
- The shop is tied to the Mafia **in code**: faction index 4 is a constant at
  every price, refusal and donation site. Scripts only open or lock the shop
  and unlock items. The port already has a `faction_support` extension that
  lets mapped items belong to another supplier (a fixed price, refused when
  that faction is hostile). It is global, not per map.
- Unlocks are saved in scribble memory (`NumShopItemInStock`, `ShopItem<i>`,
  `ShopItemIsNew<i>`, `ShopIsLocked`). They come from contract scripts
  (`Shop_UnlockItem`), from a hard-coded engine reward table (contract counts
  per faction, face-card contracts, bounties, monuments, listening posts), from
  the chop shop, and from a cheat.

## 1. UI

### Opening the shop

- The PDA opens with BACK; d-pad right jumps straight to the shop
  (`tutorial.Shop02.body`; `interface.pause.controls.{hero,vehicle,tank,helicopter}.dpadright` = "Mafia Shop").
- Scripts can open the PDA with `Datapod_Activate()` (binding `0x113E10`:
  `sub_0017A0A0('Game','datapod')`, game-state hashes 0x4249D707 / 0xC92C594C).
  Opening it on the shop page from a script is not confirmed (no argument picks the page).
- On entering the shop page, `0xCE9F0` (a vtable method of the shop object)
  picks the page:
  - **locked** (state 0) if `Shop_SetShopLocked(true)` (byte `+0x19BC`) or no unlocked item
    is in category 0, 1 or 2. Drawn by `0xC66C0`: `interface.datapod.shoplockedline1..3`
    ("Access denied! ... sergei@menace.ru").
  - **donation** (state 5) if the Mafia's attitude to the player is hostile (`0x95EE0` level 0).
    Drawn by `0xC65B0`: `interface.datapod.donationmessage`, `makedonation`, `insufficentfunds`. See section 5.
  - otherwise **browsing** (state 1).
- The page state is `+0x19AC` (set by `0xCD6C0`, which then calls `0xCA6F0`).
  From the button handler `0xCD700` (button codes 17..36, a jump table): state 1 picks a category
  (`+0x19B0`), state 2 is the item list of that category (`+0x17A4` the selected item), state 3 the
  order confirmation, state 5 the donation page. Which buttons do what is not confirmed in game.

### Categories

`Category` in itemshop.ini is parsed by `0xCAB90`: `Vehicle` -> 0, `Supply` -> 1,
`Weapon` -> 2, `Explosive` -> 4, anything else -> 2. The tab names come from `0xC89F0`:
0 `interface.datapod.vehicle` (VEHICLES), 1 `interface.datapod.supplies` (SUPPLIES),
2 `interface.datapod.weapons` ("AIR STRIKES"), 3 `interface.datapod.explosives` (EXPLOSIVES), 4 none.
Only categories 0-2 are counted when choosing the page (`0xCE9F0` loops 0..2) and when loading a save
(`0xCDC20` clears the counts at `+0x1798..+0x17A0` only). So the usable tabs are Vehicles, Supplies and
Air Strikes. `Explosive` items land in an unnamed category 4. Category 3 can't be reached from the
ini. Retail uses only the first three.

### Item pages

The list (`0xC3D40`, RsDataPodDisplay) shows each unlocked item of the tab, `interface.datapod.new`
("NEW!") for items not yet viewed, and `interface.datapod.outofstock` for items whose stock slot is -1
(see "Out of stock" in section 4). The detail/confirm page (`0xC6100`) shows the item's `Texture`
picture, `DisplayName`, `Description`/`DetailedDescription`, `interface.datapod.price` and
`interface.datapod.confirmorder` ("PLEASE CONFIRM ORDER TO RECEIVE ITEM"). It calls `0xCDBD0`
(can-afford: the player's money, float at actor `+0xAC4`, is at least the shown price). Whether an
unaffordable order is refused or only marked is not confirmed. The account page (`0xC20C0`) uses
`interface.datapod.shopaccount`. The browser bar draws the hard-coded format string
`http://www.menace.ru/%s` (`.rdata 0x2E7CF8`, used by `0xBEFB0`).

### Placing an order

Button handler `0xCD700`, case `0xCD945`:
1. (port hook `recomp_mod_pda_allowed`, section 5)
2. the player's support inventory (vfunc `+0x34` of `player0`) removes the template stored at
   `+0x19B8`, the previous shop order (`0x5A4B0`).
3. If the player is on foot, `0x5C210(template, 1)` (selects it; not confirmed).
4. `0x5A220(template, flag 1, count 0)` adds the newly ordered support weapon to the support menu with
   no free uses. `+0x19B8` = this template.

So ordering is free and only one shop order sits in the support menu at a time
(`tutorial.shopicon.title`: "Your most recent shop order is stored in the Support Menu for quick
access."). The money is taken when the item is used
(`tutorial.Shop03.body`: "You are charged once your order is en route.").

### Calling it in

The support weapon's `weaponType` decides how it's used:
- `Grenade` (`HumanAnimationSet GPS`, model `global_pic_gps`): thrown like a grenade. Its ammo is
  an `AmmoProjectile` with `SpecialEffectSet SupportSmokeGrenade`, `detonateUponStop TRUE`: the
  smoke marks the drop point (`tutorial.global.shopaemail.body`: "Throw a smoke grenade ... You won't
  be charged until the delivery heli is en-route"). Used by all vehicle drops, crates and artillery.
- `Designator` with `designatorType Laser` (`global_pic_laserdesignator`, `HumanAnimationSet Laser`,
  `BurstTime 15`): hold the laser on the target. Surgical strike, bunker buster, smart bomb, guided
  missile, gunships.
- `Designator` with `designatorType GPS` (`global_pic_gps`): GPS beacon. Carpet bomb, cluster bomb,
  tank buster, cruise missile, fuel-air bomb, air superiority, strategic missile, heavy smart bomb.

All have `ClipSize 999`, `MaxAmmo 0`, `mustAimToFire FALSE`.

### Support menu on the HUD

`DATAxbox/HUD/Hud.ini`: `SupportMenu 10 -20 LEFTMIDDLE`, `EquipedSupportItem 0 -2 RIGHTBOTTOM`,
colours `supportselectionARGB`, `inactiveSupportARGB 255 192 0 0` (red), `activeSupportARGB`. Each
item's icon is the weapon template's `HudTextureName` (read in `0x312F0`; the `SupportIcon*` textures
are in streamed.dsk and listed in the `hud` load list in assets.dsk). The HUD (RsHud `0xFCAC0`) prints
the item's price with `$%i` (`0x2E7EF0`), from `0x32620`. Whether the free-use count is shown is not
confirmed.

The support menu is the player actor's list at `+0x870` (templates) / `+0x8F0` (items), count `+0x970`,
**at most 32** (`0x5A220`). A second list at `+0x97C`/`+0x9FC` (count `+0xA7C`) is used when
`Support_AddItem`'s third argument is true (the default) during play (game state `Game`, sub-state
`play`, `datapod` or 0x8DA3891F). With `false` the item goes straight into the first list. What the
second list does (a pending/announced queue) is not confirmed. `Support_ResetToDefault` and
`Support_RemoveItem` work on the first list only.

## 2. Data

### The catalogue: `DATAxbox/DATAPOD/itemshop.ini`

A loose text file (also referenced by name from the exe: `\datapod\itemshop.ini`, `0x2E8B2C`), read
by `0xCE460` (called by Level_Load `0x184890`) through `0xCAB90`. Each block:

```
[ShopItem]
Template_object_name	template_support_p_surgicalstrike   ; the support WEAPON template
DisplayName		support.airstrikes.surgicalstrike         ; text key
Description		shop.airstrikes.surgicalstrike.short      ; text key
DetailedDescription	shop.airstrikes.surgicalstrike.long   ; text key
Category		Weapon                                    ; Vehicle | Supply | Weapon (| Explosive)
Texture			shop_surgicalstrike                       ; picture (texture name)
LockedTexture		(optional; parsed, unused in retail)
DefaultPrice		50000                                     ; shown price before mood
```

All seven fields are stored as name hashes in a 32-byte entry (base `0x34ABC8 + 0x190 + i*32`:
+0 Texture, +4 LockedTexture, +8 Template_object_name, +0xC DisplayName, +0x10 Description,
+0x14 DetailedDescription, +0x18 DefaultPrice, +0x1C category). Count at `+0x1190`, **max 128**
(`0xCAB90` stops at 0x80). Retail has 63 entries: 17 air strikes and artillery, 16 crates, 30 vehicles. The
item named in `Template_object_name` must also exist as a template: it is what goes into the support
menu. In retail every `DefaultPrice` equals the item's ammo `MonetaryCost` (checked for all 63).

Retail has catalogue texts with no catalogue entry (cut items): `shop.vehicles.deliverStolenSKEW.*`,
`shop.vehicles.deliverStolenPressCar.long`, `shop.supplies.crate_costume.*`,
`shop.supplies.crate_demolitions.*`, and the texture `shop_skew` is in the `datapod` load list.

### Textures

- Shop pictures (`Texture`, e.g. `shop_mafiaSUV`, `shop_crate_west`) and the site's `shoplogo`,
  `shopflag`, `browserbar`, `databack`, `pdabuttons` are textures in **streamed.dsk**, loaded by the
  **`datapod` load list** (assets.dsk record key 0xC92C594C, a `[texture] load ...` list of 72 names).
- Support menu icons (`HudTextureName`, e.g. `SupportIconX1Y2`, `SupportIconCrate_mafia`,
  `SupportIconGunship`) are textures in streamed.dsk loaded by the **`hud` load list** (key 0xE28D61C6).

### Support weapon templates (`template_support_*`, layer template_weapons)

ItemWeapon: `GeometryFile`, `HudTextureName`, `displayName` (`support.airstrikes.*`,
`support.supplies.*`, `support.vehicles.*`), `Ammo` -> the ammo template, `weaponType`
(`Grenade` | `Designator`), `designatorType` (`Laser` | `GPS`), `HumanAnimationSet` (`GPS` | `Laser`),
`BurstTime`, `ClipSize 999`, `MaxAmmo 0`, `MaxLaunchSpeed`/`maxLaunchTime` (grenades),
`mustAimToFire FALSE`, `DisableDebugMenuSpawn TRUE`. There is no price on the weapon.

Two families: `template_support_X` (faction-given, used by contract scripts with `Support_AddItem`)
and `template_support_p_X` (the shop's: "procured" copies, same aircraft, often dearer:
p_bunkerbuster 350000 vs bunkerbuster 250000, p_carpetbomb 500000 vs 400000). Vehicle drops:
`template_support_deliver<Faction><Vehicle>` (a faction's own helicopter) and
`template_support_deliverStolen*` (the Mafia cargo copter).

### Ammo templates (`template_sam_*`)

| kind | objectType | key properties | retail example |
|---|---|---|---|
| airstrike | AmmoAirstrikeBeacon | `AirstrikeName` (airplane template), `MonetaryCost`, `AttackPosFlyByOffset`, `SpookyFlyTime` (gunship), `SmartBombRadius`, `AttackVehicleGroup`, `GeometryFile global_invisible` | `template_sam_p_surgicalstrike`: tornado, 50000; `template_sam_p_gunship`: `template_sk_support_ac130`, SpookyFlyTime 10, 75000 |
| artillery | AmmoProjectile | `ArtilleryAirplaneTemplateName`, `ArtilleryAttackRadius`, `MonetaryCost`, smoke projectile props | `template_sam_p_artillery`: `template_china_support_66`, radius 30, 50000 |
| vehicle drop | AmmoProjectile | `deliveryHelicopterTemplateName`, `spawnObjectTemplateName` (an `_empty` vehicle, or `self`), `MonetaryCost` | `template_sam_deliverH2`: `template_mafia_cargocopter` + `template_mafia_h2_empty`, 35000; `template_sam_deliverLittleBird`: `template_mafia_littlebird` + `self` |
| supply crate | AmmoProjectile | as a vehicle drop plus `deliveryQuickDrop TRUE`, `spawnObjectTemplateName template_crate_*` | `template_sam_crate_Mafia`: `template_mafia_littlebird` + `template_crate_Mafia`, 4000 |

The projectile props shared by all smoke ammo: `SpecialEffectSet SupportSmokeGrenade`,
`detonateUponStop TRUE`, `maxRange 300`, `maxAge 20`, `gravityScale 2`, `springFactor 0.5`,
`MaxLaunchSpeed 12`, `GeometryFile global_pic_fraggrenade`.

Readers: `0x16320` (projectile init: `MonetaryCost` -> `+0x1AC`, `MinMonetaryCost` -> `+0x1B0`,
`displayName`, `GeometryFile`, `DamageType`, `SpecialEffectSet`, `AiType`), `0x19670` (support
projectile: `deliveryHelicopterTemplateName`, `spawnObjectTemplateName`, `deliveryQuickDrop`,
`ArtilleryAirplaneTemplateName`, `ArtilleryAttackRadius`, `AttackVehicleGroup`), `0x17B40`
(beacon: `AirstrikeName`, `AttackPosFlyByOffset`, `SpookyFlyTime`, `SmartBombRadius`,
`AttackVehicleGroup`, `ArtilleryAttackRadius`), `0x31B20` (designator weapon: `designatorType`,
`isExtractionWeapon`), `0x1C010` (delivery: `self`, `Ai_Deliver 0x7DD10`).

### Aircraft and helicopters involved

- Airstrike planes (`objectType airplane`, layer template_weapons, `numweapons`/`weapon_a` = the
  ordnance weapon template, `flightTime`, `physics*`, `AIType` e.g. `B2`, `AC130U`, `artillery`):
  `template_allies_support_{a10,a10b,b52,ef2000,f15,f2,tornado}`,
  `template_sk_support_{ac130,b2,cruise,f117,f22}`, `template_china_support_{66,66_2,66_3,66air,frogfoot}`,
  `template_mafia_support_frog` (strategic missile).
- Delivery helicopters (template_vehicles): `template_mafia_littlebird` (crates, faction mafia),
  `template_mafia_cargocopter` (Mi-26 halo, vehicles, mafia), `template_mafia_blackhawk`,
  `template_mafia_NKLittleBird` (delivered as `self`), and the factions' own:
  `template_allies_blackhawk`, `template_allies_chinook`, `template_sk_blackhawk`,
  `template_sk_pavelow`, `template_china_mi17`, `template_allies_blackhawkextr` (extraction).
- Crates: `template_crate_*` (objectType `crate`, `winchable true`, `spawnObjectA..E` = the
  pickups inside), or a pickup directly (`template_pic_armor`, `template_pic_ammoveh`).

### Scripts and ini files

There is no shop table in the live scripts. `mafia_shop.lua` (loaded by global.lua:14) refers to a
`tMafiaShop` table no script defines and registers its own events only from inside itself: a cut
"Mafia Garage / Spare Parts" challenge (`challenge.shop.*`, `challenge.mafiashop.*`, scribble
`ShopWantedActorTemplateName` / `ShopWantedActorFactionName`, `interface.datapod.shoplookingfor`).
`mafia_shopunlockables.lua` (a per-mission and per-faction-chain unlock table,
`GetUnlockedShopItemTemplateName`) is never loaded by any script and not named in the exe. Both are
dead.

## 3. Purchases and delivery

### What is charged and when

`0x166E0` is the charge (called by `0x19590` (smoke-type projectiles, six callers), `0x1C010` (the
delivery helicopter dispatch) and `0x1C790` (the airstrike aircraft spawn)):
1. If the ammo's free-use counter `+0x1EC` > 0, decrement it and charge nothing.
2. Else base = `0x16630`: `MonetaryCost` (`+0x1AC`); if `MinMonetaryCost` (`+0x1B0`) is set, a
   value between the two by the Mafia's relation (only `template_amm_mafiabribe` sets it: 20000 / 10000).
3. price = round((1 - 0.2 x r) x base), r = the Mafia's relation to the player (`0x95B70(4, 1)`, -1..1;
   constants `0x2DC3C4` 0.2, `0x2DC08C` 1.0, `0x2DC340` 0.5, then floor).
4. `Player_SetMoney(money - price)` (`0x57F50`), and if the price > 0 an economy message with
   `interface.economyevent.support` ("Support") and the amount (`0x1002D0`).

Money can go below zero this way: no check in `0x166E0`. Whether the HUD refuses to fire an item
you can't afford is not confirmed (`0x34040`, a weapon method, calls the price `0x32620`; the port
hooks it as a "weapon allowed" check).

Free uses come from `Support_AddItem(template, count)`: `0x5A220` adds `count` to the ammo's `+0x1EC`
(also when the item is already in the menu). `count` 0 means the item is there but every use is paid.

### Delivery per kind (from the data; the AI side is not traced)

- **Airstrike** (AmmoAirstrikeBeacon): `0x1C790` spawns `AirstrikeName`
  (`Spawner_SpawnTemplate 0x174480`) toward the designated point and charges. The plane's
  `weapon_a` drops the ordnance.
- **Artillery** (AmmoProjectile with `ArtilleryAirplaneTemplateName`): an invisible "airplane" with
  `AIType artillery` and `weapon_a template_wep_artillery` shells `ArtilleryAttackRadius` around the
  smoke.
- **Vehicle drop**: `0x1C010` spawns `deliveryHelicopterTemplateName`, charges, and gives it the
  deliver order (`Ai_Deliver 0x7DD10`) with `spawnObjectTemplateName` slung below (the vehicle must be
  `winchable`). With `spawnObjectTemplateName self` the helicopter itself is the delivery (Little Bird,
  stolen Black Hawk, NK Little Bird).
- **Supply crate**: as a vehicle drop with `deliveryQuickDrop TRUE` (the crate is released rather
  than set down; not confirmed in game) and a `template_crate_*` (or a pickup) as the load.

### Script commands for money and support

| command | binding | engine | notes |
|---|---|---|---|
| `Support_AddItem(template [, count [, announce]])` | `0x113E90` | `0x5A220` on player0 | count = free uses (default 0); third arg default true |
| `Support_RemoveItem(template)` | `0x113F20` | `0x5A4B0` | first list only |
| `Support_ResetToDefault()` | `0x113F70` | `0x5A580` | empties the support menu |
| `Shop_UnlockItem(template)` | `0x113DB0` | `0xCDA60(hash, 0)` on `0x34ABC8` | name hash of the weapon template; no error if not in the catalogue |
| `Shop_SetShopLocked(bool)` | `0x113DE0` | `0xC8B20`: byte `+0x19BC` | |
| `Mission_SpawnAirstrike(ammo, x, y, z, yaw)` | `0x11D650` | `Spawner_SpawnTemplate` | spawns a beacon/ammo template at a point (aclubs_mission.lua:805, nw_china4.lua:164); whether it charges is not confirmed |
| `Player_GetMoney/SetMoney/AdjustMoney/ShowMoney` | `0x11A820/0x11A870/0x11A8D0/0x11AA00` | `0x57F50` | AdjustMoney takes an economy text key (bouncer.lua:453) |
| `Event_OdfSold(cb, 'b', model)` | | | a vehicle of that model sold at the chop shop (sw_mafia1.lua:137, nw_mafia1.lua:89) |

`util_DefaultSupport` (util_misc.lua:50, `Support_ResetToDefault()`; the older util.lua:190 also adds
`template_support_crate_allies`, count 0, false) runs at every level start (global.lua:94) and when a
contract ends (mission.lua:386). Contracts add their support from `tSupport`
(`mission_AddSupport`, mission.lua:57-66), e.g. nw_sk1.lua:40-42 (gunship, SK Humvee AT, SK crate,
one free each). The extraction item is `template_weapon_Extraction`
(mission_objectiveverify.lua:191/194).

## 4. Unlocks

### The unlocked list

`0xCDA60(hash, flag)` (Shop_UnlockItem uses flag 0):
- already unlocked: its stock slot is reset to the catalogue `DefaultPrice` (flag 0) or -1 (flag 1);
  it's marked new again if it had been out of stock.
- else find the catalogue entry by `Template_object_name` hash (none: return -1, no message), and
  append to the unlocked list (`+0x1194 + k*12`: +0 catalogue index, +4 stock/price slot =
  `DefaultPrice` or -1, +8 isNew; count `+0x1794`, max 128), and count it in its category (`+0x1798 + cat*4`).
- with flag 0, queue a "new shop item" notice (`0xC8560`: up to 8 template hashes at `+0xE37C` of the
  PDA display object `[0x34ABC8+0x24]`, drawn by `0xC95C0` with `interface.newshopitemmessageboxbody`;
  the queue is cleared at game start by `0xCCB50`) and refresh the page if it's showing.

There is no Lua command to lock an item or put it out of stock.

### Out of stock

`0xC8B60(index, flag)` sets an unlocked item's slot to -1 (out of stock) or back to `DefaultPrice`.
Only `0xCCB50` (at the start of the game state, `RsMainStateGame 0x181D60`) calls it. It reads the
current mission id (string at `0x3C2700`) and marks the three helicopter deliveries
(`deliverLittleBird`, `deliverStolenAlliesBlackhawk`, `deliverStolenNKLittleBird`) out of stock during
eight missions: sw_allies4, sw_allies8, nw_allies4, nw_allies8 and four more not identified (hashes
0x1693AD80, 0x2293C064, 0xE77FC1FD, 0xDB7FAF19). It also marks `template_support_bunkerbuster` out
of stock except in sw_sk6 (that template isn't in the retail catalogue).

### Who unlocks what

1. **Contract scripts** (`post_MissionCleanup`/completion code):
   sw_mafia1.lua:250-257 (sets `mafiaShopOpen`, `Shop_SetShopLocked(false)`, unlocks
   `crate_mafiaAllies`, `crate_mafia`, `p_surgicalstrike`, and `deliverTechnicalMG` if every target was
   destroyed); sw_mafia2.lua:200 `crate_mafiaChina`; sw_mafia3.lua:91 `strategicmissile`;
   sw_mafia4.lua:195 `p_artillery2`; sw_mafia5.lua:352 `crate_MafiaSniper`; sw_mafia6.lua:203
   `deliverStolenPressTruck`; nw_mafia1.lua:222 `deliverH2`; nw_mafia2.lua:154
   `deliverStolenSportsCar`; nw_mafia3.lua:210 `deliverStolenNKLittleBird`; nw_mafia5.lua:397
   `crate_mafiaHeavy`; nw_mafia6.lua:333 `deliverVIPCar`. (e3n.lua:44 unlocks
   `deliverCivCar`, which isn't in the catalogue.)
2. **The engine's reward table** (`0x15CF70` fills 94 entries of 24 bytes: +0 money, +4 shop item
   hash, +8 cheat id (0x19 = none), +0xC/+0x10 another unlock, +0x14 mail; `0x15CB20` grants one:
   money, `0xCDA60(item, 0)`, the cheat, `Mail_Post`). `0x15E4B0` / `0x15CC90` pick the entry from a
   key hash and an index. Callers and keys:
   - contract completion, front end `0xD69E0` (RsFrontEnd; no direct caller found), by the
     contract's faction and the counters mission.lua:216-228 writes (`num_std_missions_cmpl_<f>`,
     `num_face_missions_cmpl_<f>`):
     Allies standard (0xFB5470C1): p_surgicalstrike, p_clusterbomb, deliverStolenAlliesBlackhawk,
     deliverStolenAlliesHumvee, deliverStolenAlliesStryker, p_tankbuster.
     China standard (0xDA618F92): p_artillery, crate_MafiaAmmoveh, deliverStolenChinaAPC,
     deliverStolenNKBMP, crate_mafiaVehicle, deliverStolenTunguska.
     SK standard (0x69300E11): deliverStolenSKHumveeMG, crate_mafiaCovert, p_smartbomb,
     deliverStolenSKHumveeAT, p_cruisemissile, deliverStolenSKAPC.
     Mafia standard (0xD5E5007F): deliverStolenCivCar, deliverStolenNKJeep, deliverStolenNKBRDM,
     deliverStolenNKBTR60, crate_mafiaSpecial, deliverStolenAlliesAvenger.
     Face cards: Allies (0x32028039) p_bunkerbuster, p_airsuperiority, p_carpetbomb; China
     (0xD228ECDA) crate_MafiaArmor, p_artillery3, p_fuelairbomb; SK (0xF5A01909) p_gunship,
     p_gunship2, p_gunship3; Mafia (0x341DD437) deliverStolenNKTransport, deliverTechnicalAT,
     deliverTechnicalGL. sw_allies8 gives entry 0xF085FF47; all 52 cards 0x4051F27.
   - bounty pickups `0x34570` (RsActorItem: national treasures / dog tags / money counters),
     keys 0x7130720F (crate_mafiaC4, crate_sniperrifle, crate_stinger, crate_atrocket,
     crate_mafiaAdvanced, ...) and 0x5019688C (deliverStolenChinaJeep, deliverH3,
     deliverStolenChinaFuelTruck, ...). Which counter goes with which key is not confirmed.
   - monuments `0x2C130` (0x3DA66C89: deliverStolenZSU); listening posts `0x382C0` (0xAF31AE33:
     crate_mafiaHealth, deliverStolenBaggageCar, deliverLittleBird).
   - `Unlockable_AceOf{Clubs,Diamonds,Hearts}MissionJustWon` (`0x11D220/0x11D240/0x11D260`, key 0x32028039).
   - Two more groups duplicate the Mafia contract scripts' unlocks: 0x51BE63BE (the six sw_mafia
     items) and 0x41C69953 (the nw_mafia items). No caller of those keys was found.

   The face-card and Ace indexes are counted down by one before lookup (`0x15E4B0`). Whether the
   front-end handler runs after every contract (including a mod's) is not confirmed. If it does, a
   mod contract that goes through mission.lua's completion also raises these counters and gets the
   next retail reward.
3. **The chop shop** (`0x29380`, RsActorBuildingChopShop.cpp): selling a vehicle pays
   `Sell_BlueBookValue` (scaled), raises the Mafia's relation, and calls `0xCDA60` with the sold
   vehicle's `+0x178`. Which property fills `+0x178` is not confirmed (no retail vehicle has a
   property whose value is a support template).
4. **A cheat**: `0x15C980` unlocks every catalogue item (`0xCEAA0`) and opens the shop
   (`0x15E230` is the cheat dispatcher).

`template_support_crate_nk` and `template_support_crate_mafiaCheatgun` are in no live unlock path
(crate_nk only in the dead mafia_shopunlockables.lua; Cheatgun not confirmed, possibly the
unlock-all cheat only).

### Chapters

There is no per-chapter shop list. A chapter changes the donation price (`MafiaDonation`, global.lua:96,
`util_GetBribeCost`: 25000 / 100000 / 250000 / 1000000, util_misc.lua:359), and its contracts unlock
items.

### Saving

`0xCAEF0` (called by `Utility_SavePlayerData 0x189400`) writes to scribble memory (`0x414150`):
`NumShopItemInStock` = unlocked count, `ShopItem<i>` = template hash of each item whose slot is >= 0,
`ShopItemIsNew<i>`, `ShopIsLocked`. `0xCDC20` (from `0x189490`, Utility_LoadPlayerData) restores
them (re-unlocking by hash, so the catalogue order may change). Out-of-stock items (slot -1) are not
saved. Level load (`0xCE460`) first empties the list and clears the lock (`+0x1794 = 0`,
`+0x19BC = 0`). Then global.lua:29-33 sets the lock from the script key `mafiaShopOpen`
(written by sw_mafia1.lua:250 and playable_intro.lua:31 after a finished game). The last shop order
(`+0x19B8`) is not in these keys.

## 5. Faction mood

The Mafia is faction index 4, the player ("prokat" in scripts) index 1. The relation table is at
`0x323338` (`+0x1C + (faction*8 + other)*4`, float -1..1; scripts use -100..100). Attitude levels
(`0x95EE0`, thresholds at `0x2E6C78`): hostile <= -0.6 < unfriendly <= -0.2 < neutral <= 0.2 < friendly.

- **Prices**: shown price (`0xCAE90`) and charged price (`0x166E0`) = round((1 - 0.2 r) x price):
  20% off at the best relation, about 12% more at the edge of hostile. The support weapon's price
  (`0x32620`) reads the ammo's `MonetaryCost` (and only for `template_amm_mafiabribe`, hash
  0x5DA9C68A, the Min/Max interpolation by the Mafia's relation). Its callers apply the mood term
  (the port's fixed-price hooks sit at `0x4DF19`, `0x90FC6` and `0xFCDE8` in them; the exact
  formula at those three sites is not traced).
- **Refusal**: hostile Mafia -> the shop shows the donation page (`0xCE9F0`). The shop is also
  locked until sw_mafia1 (`mail.intro.mafiashop.body`: "If you make the Russians unhappy, though,
  you'll be locked out of the shop until you smooth things over.").
- **Donation** (`0xCD700` case `0xCD72C`): cost = scribble `MafiaDonation` (`0xCAE60`, default
  10000). If you have the money, it's taken and the Mafia's relation is set to
  `RelationOnDonation` / 100 (scribble, written as -40 by global.lua:556 and the Ace scripts), or to
  just above hostile (-0.6 + 1e-7, `0x2E8A88`) if that key is unset. The HQ doorman bribe is
  separate, in script (bouncer.lua:415-453: `Faction_SetRelation(faction,'prokat',-40)`,
  `Player_AdjustMoney(-cost)`).
- **Raising the mood**: selling vehicles at the chop shop (`0x29380`), contracts. The dead
  mafia_shop.lua would have used `Faction_ModifyRelation('mafia','prokat',15)`.

**Where the Mafia is fixed in code** (faction 4 as an immediate operand): `0xCAEA2` (catalogue price),
`0xCEA4F` (hostile page), `0xCD7B8` (donation), `0x16756` (charge), `0x32620`/`0x16630` (min/max
interpolation for the bribe item), `0x29380` (chop shop), and the mood terms in the callers of `0x32620`
(around `0x4DF19`, `0x90FC6`, `0xFCDE8`; not traced). No script or data names the shop's faction. The
scripts only gate the shop open (`mafiaShopOpen`) and unlock items.

**The port's existing extension** (`external/Mercenaries-Recompiled/docs/MOD_EXTENSIONS.md`,
`ports/mercenaries/src/mod_extensions.c`, `scripts/Mod-Extensions-Patches.py`): `modcompatibility.ini`
`[Mods] faction_support=1` with `[SupportFactions] <template>=<nk|sk|mafia|china|allies|civilian>`
(both the ammo and the catalogue `Template_object_name`). A mapped non-Mafia item:
- has a fixed price (the relation term is zeroed at `0x16772`, `0xCAEB0`, `0x4DF19`, `0x90FC6`, `0xFCDE8`);
- is refused when its faction is hostile (`recomp_mod_pda_allowed` at `0xCDBD0` and `0xCD945`;
  `recomp_mod_weapon_allowed` at `0x330A0`, `0x34040`, `0x33550`);
- keeps the shop open while the Mafia is hostile if any mapped supplier is not (`0xCEA5C`).

It's one mapping for the whole mod (the last enabled mod's file wins), not per map. There is no
donation to another faction, and no mood-scaled price for a non-Mafia supplier. The toolkit's
`mod sync` writes a fixed modcompatibility.ini (mercfoundry/sync.py:23-24, 298) without these sections.

## 6. Renaming and rebinding the shop per map

### What names it

- Text keys (english.dsk), all swappable on a new map with `text/*.json` + `"map"` (Foundry Console
  `Foundry_MapText`): `interface.datapod.shop` (tab "SHOP"), `interface.datapod.shoplockedline1..3`,
  `interface.datapod.donationmessage`, `makedonation`, `donate`, `confirmorder`, `itemordered`,
  `insufficentfunds`, `outofstock`, `availableinstock`, `new`, `price`, `order`, `buy`, `shopaccount`,
  `shoplookingfor`, the tab names `interface.datapod.vehicle/supplies/weapons/explosives`,
  `interface.mafiashop.newitemavailable`, `interface.newshopitemmessagebox{title,body}`,
  `interface.shopitemunlocked.{name,dialog}`, `interface.pause.controls.*.dpadright` ("Mafia Shop"),
  `interface.economyevent.support`, `tutorial.Shop01..03.*`, `tutorial.global.shop*`,
  `tutorial.shopicon.title`, `mail.intro.mafiashop.*`, `sw.mafia1.shopOpen`, `sw.mafia2/3.shopUnlock`,
  every `shop.*.short/.long` and `support.*` item name. "Merchant of Menace" appears only in text
  (`tutorial.Shop01.body`, `tutorial.global.shopopen.subject`, the tutorial mails), so it can be renamed.
- Textures: `shoplogo`, `shopflag`, `browserbar`, the `shop_*` pictures and the `SupportIcon*` icons.
  These can be swapped per map with maps json `"hud_textures"` (`Foundry_MapTexture`, which also
  points Texture_Find's entry when no HUD sprite draws it). Whether the PDA shop page draws these
  through a path the swap covers is not confirmed; the deck portraits on the same PDA do.
- The URL `http://www.menace.ru/%s` is a C string in the exe's `.rdata` (`0x2E7CF8`, read by
  `0xBEFB0`). No text key. It sits in guest memory, so a plugin could overwrite those bytes while the
  map is loaded and put them back on the next level load (24 bytes with the terminator; not tried).
- The faction names on the PDA's Factions page (`interface.datapod.mafia`, `standardcase.mafia`) are
  the faction's, not the shop's (factions.json already renames them per map).

### Who services it

- **Delivery look** (which helicopter, plane or crate) is data on each ammo template. Per map:
  maps json `"template_overrides"` (`Foundry_MapTemplate`) can give `template_mafia_littlebird` /
  `template_mafia_cargocopter` / any airstrike plane another faction's model and properties while
  the map is loaded. The map can also use its own item copies with other `deliveryHelicopterTemplateName`
  and `AirstrikeName`.
- **Mood, refusal and donation** follow faction 4 in code; a script can't change that. Options:
  1. Script only: keep the Mafia's relation in step with the chosen faction's on that map
     (`Event_FactionAttitudeChange` / a timer -> `Faction_SetRelation('mafia','prokat', Faction_GetRelation(<f>,'prokat'))`).
     This also changes how the Mafia's units treat the player, so it suits a map with no Mafia units
     (or one where the Mafia slot is the shop's faction).
  2. The port's `faction_support` with the map's own item templates (unique names, so the mapping is
     in effect per map): the price is fixed (no mood), and items are refused when that faction is hostile.
  3. An engine patch (port-side, like Mod-Extensions-Patches.py): read the shop's faction from a
     variable instead of the constant 4 at the sites listed in section 5. A Foundry Console command
     (e.g. `Foundry_MapShopFaction('china')`) would then set it per map. The immediates are compiled into
     the generated C, so this can't be done by writing guest memory at run time.

### Per-map catalogue and unlocks

itemshop.ini is one file at a fixed path, read at every level load. The unlock keys are global (by
template hash). So:
- a mod adds its items to the one catalogue (63 retail + up to 65 more);
- items unlocked on SW/NW also show on the new map and the other way round. Hiding them needs new
  work: a plugin command calling `0xC8B60(index, 1)` on map load (after `0xCDC20` restores the list)
  for every item not on the map's list, or a port change that loads another catalogue per map;
- a new map's master script must set the lock itself (`Shop_SetShopLocked`); level load leaves it
  unlocked and global.lua (SW/NW) is what applies `mafiaShopOpen`.

## 7. Adding a new support item

Minimal records:

1. **Ammo template** `template_sam_<name>` (copy of the closest retail one): the kind's properties
   (section 2 table) and `MonetaryCost`. For a delivery: `deliveryHelicopterTemplateName` (an existing
   helicopter template or a copy) and `spawnObjectTemplateName` (an `_empty` vehicle template, a
   `template_crate_*` copy with its `spawnObjectA..E`, or `self`), `deliveryQuickDrop TRUE` for crates.
   For an airstrike: `AirstrikeName` and the beacon extras.
2. **Aircraft** if new: an `airplane` template with `weapon_a` -> a weapon template whose ammo is the
   bomb/missile (copy `template_allies_support_*` and its `template_wep_*`). Artillery: a copy of
   `template_china_support_66` with its `weapon_a`.
3. **Support weapon template** `template_support_<name>`: ItemWeapon with `Ammo`, `HudTextureName`,
   `displayName`, `weaponType`/`designatorType`/`HumanAnimationSet`, `GeometryFile`
   (`global_pic_gps` / `global_pic_laserdesignator`), `ClipSize 999`, `MaxAmmo 0`, `BurstTime`,
   `mustAimToFire FALSE`, `DisableDebugMenuSpawn TRUE`.
4. **Icon**: a texture for `HudTextureName`, loaded with the HUD (the `hud` list, or the map's own list
   kept resident). Reusing a retail `SupportIcon*` needs nothing.
5. **Strings**: `support.<cat>.<name>` (menu name) and, for the shop, `shop.<cat>.<name>.short/.long`.
6. **Shop entry** (only for shop items): a `[ShopItem]` block in `DATAxbox/DATAPOD/itemshop.ini`
   (`Template_object_name` = the weapon template, `Category`, `Texture`, `DefaultPrice` = the ammo's
   `MonetaryCost` so the shown and charged prices agree) and the picture in the `datapod` load list.
7. **Availability**: `Shop_UnlockItem('<weapon template>')` from a contract's completion (the
   toolkit's missions already emit `tFoundryUnlocks`) or the map script, or
   `Support_AddItem('<weapon template>', freeUses)` to put it straight in the support menu (missions'
   `"support"` already does this).

What can't be done without engine work:
- a 5th usable tab, or using category 3/4 (`Explosive`) as a shown tab;
- more than 128 catalogue entries / unlocked items, more than 32 items in the support menu;
- a delivery kind other than those the code knows (airstrike beacon, artillery plane, helicopter
  delivery of a vehicle / crate / itself); new ordnance behaviour beyond the aircraft's weapon templates;
- any shop faction other than the Mafia for mood, refusal and donation (see section 6);
- per-map catalogue contents or hiding retail unlocks on a map (plugin or port work);
- changing the hard-coded reward table (`0x15CF70`) or the helicopter out-of-stock missions (`0xCCB50`).

## What the toolkit would need

**Already possible with today's build** (maps json, missions, text, templates):
- New item templates as `"templates"` copies (weapon, ammo, aircraft, crate) with their models;
  per-map changes to the retail delivery helicopters and aircraft through `"template_overrides"`.
- Item names, descriptions and every shop text per map through `text/*.json` with `"map"`;
  `shoplogo`/`shopflag`/icons/pictures through `"hud_textures"` (PDA path to confirm in game).
- Shop unlocks on contract completion (`tFoundryUnlocks` -> `Shop_UnlockItem`) and mission support
  (`"support"` -> `Support_AddItem`).
- The script-only mood link (option 1 in section 6), written into the map's master script.

**New toolkit work**:
- Write `DATAxbox/DATAPOD/itemshop.ini` as a loose file (as emails.ini is written now): the retail
  63 blocks plus the mod's items, with a check against the 128 limit and that each
  `Template_object_name` exists and its `DefaultPrice` matches the ammo's `MonetaryCost`.
- Add shop pictures to the `datapod` load list and support icons to the `hud` list (or the map's
  list with keep-resident).
- An editor "Shop" panel: the item list (kind, aircraft/helicopter/load, price, icon, picture,
  texts, category), unlock rules (at start, on a contract, by script) and the map's shop lock.
- Optionally `[SupportFactions]` in the modcompatibility.ini that `mod sync` writes (sync.py has a
  fixed text today).

**New plugin (Foundry Console) work**:
- A per-map stock filter: after the save's unlocks are restored, mark out of stock (`0xC8B60`) every
  unlocked item that isn't on the map's list, and put them back on leaving.
- Optionally rewrite the `menace.ru` URL string (`0x2E7CF8`) while the map is loaded.

**Engine (port) work**:
- A variable shop faction instead of the constant 4 (section 5's sites), set per map by a console
  command. Without it, the shop's mood is always the Mafia's.
- Optionally: a per-map catalogue file, more categories, a script command to lock items.

## A map's own store (toolkit 0.277)

maps/<name>.json `"shop"` (mercfoundry/shop.py has the full format):

```json
"shop": {"items": [
  {"id": "hermes", "from": "template_support_p_surgicalstrike", "name": "Hermes drone missile",
   "short": "...", "long": "...", "price": 90000, "unlock": "start",
   "aircraft": "template_gaza_allies_support_a10b", "icon": "SupportIconX1Y2", "picture": "shop_hermes"},
  {"id": "taxi", "from": "template_support_deliverStolenCivCar", "name": "Taxi", "price": 15000,
   "unlock": "after sw_mafia20", "helicopter": "template_gaza_allies_chinook",
   "load": "template_gaza_civ_80civic_empty"}
]}
```

- Each item copies a game shop item ("from", the table below): its support weapon as
  `template_support_<map>_<id>` and that weapon's ammunition as `template_sam_<map>_<id>`
  (the map's own templates layer). The copy keeps how the item is used (laser, GPS beacon or smoke)
  and changes the price (`MonetaryCost` and the catalogue's `DefaultPrice`, equal as in retail),
  the plane (`"aircraft"`: `AirstrikeName`, or `ArtilleryAirplaneTemplateName` for artillery),
  the delivery helicopter and its load (`"helicopter"`, `"load"`: a vehicle's `_empty` template,
  a crate, or `self`), the menu icon (`HudTextureName`) and the shop picture. `"category"`
  (Vehicle, Supply, Weapon: the shop's three tabs) defaults to the item's.
- The catalogue is written loose (DATAxbox/DATAPOD/itemshop.ini): the game's 63 items, then every
  map's, at most 128. Names and texts are new strings `support.<map>.<id>`,
  `shop.<map>.<id>.short/.long`. Pictures are added to the `datapod` load list, icons to `hud`
  (a texture named there must be the game's or the mod's own textures/<name>.png).
- Unlocks (`"unlock"`): `start`, `after <contract id>` (scribble `foundry_done_<id>`),
  `intel <suit> <n>` (the map's deck meter, `foundry_deck_<map>_iCurrentIntel<suit>`). Looked at
  as the map loads and every 5 s after; each unlocked once per save (`foundry_shop_<template>`),
  with the game's "new item" notice.
- Foundry Console `Foundry_MapShop(template, ...)`: on the map only its items show. The game's and
  other maps' unlocked items are taken out of the PDA's list while it's loaded. On every other
  level, items whose template isn't loaded there (a map's own) are taken out. They're put back
  just before the game saves the list (`Utility_SavePlayerData` hook) and as the next level loads,
  so a save never loses an unlock. Without Foundry Console every unlocked item shows everywhere
  (a map's own item ordered on another map would name a template that isn't loaded there).
- The shop stays the Mafia slot's (prices by its mood, its refusal and donation). On a map whose
  factions.json makes the Mafia slot another faction (Gaza's Settlers), that's the store's.
- Not done: the `menace.ru` URL, a per-map shop name beyond the texts (`text/*.json` with `"map"`
  renames them), keeping a map's items from a hostile shop faction's refusal.
- Not tried in game yet (0.277): the hiding, the save hook, and the copied items' deliveries.

### The game's shop items (to copy from)

| item ("from") | tab | kind (use) | price | plane, or helicopter + load | icon | picture |
|---|---|---|---|---|---|---|
| `template_support_deliverH2` | Vehicle | drop (Grenade) | 35,000 | `template_mafia_cargocopter + template_mafia_h2_empty` | `SupportIconX6Y6` | `shop_mafiaSUV` |
| `template_support_deliverH3` | Vehicle | drop (Grenade) | 30,000 | `template_mafia_cargocopter + template_civ_h3_empty` | `SupportIconH3` | `shop_h3` |
| `template_support_p_airsuperiority` | Weapon | airstrike (Designator/GPS) | 200,000 | `template_sk_support_f22` | `SupportIconX4Y1` | `shop_airsuperiority` |
| `template_support_p_tankbuster` | Weapon | airstrike (Designator/GPS) | 250,000 | `template_allies_support_a10b` | `SupportIconX3Y1` | `shop_tankbuster` |
| `template_support_p_cruisemissile` | Weapon | airstrike (Designator/GPS) | 300,000 | `template_sk_support_cruise` | `SupportIconX2Y6` | `shop_cruisemissile` |
| `template_support_p_smartbomb` | Weapon | airstrike (Designator/Laser) | 45,000 | `template_sk_support_f117` | `SupportIconX1Y5` | `shop_smartbomb` |
| `template_support_p_surgicalstrike` | Weapon | airstrike (Designator/Laser) | 50,000 | `template_allies_support_tornado` | `SupportIconX1Y2` | `shop_surgicalstrike` |
| `template_support_p_gunship` | Weapon | airstrike (Designator/Laser) | 75,000 | `template_sk_support_ac130` | `SupportIconGunship` | `shop_gunship` |
| `template_support_p_gunship2` | Weapon | airstrike (Designator/Laser) | 150,000 | `template_sk_support_ac130` | `SupportIconGunship2` | `shop_gunship2` |
| `template_support_p_gunship3` | Weapon | airstrike (Designator/Laser) | 225,000 | `template_sk_support_ac130` | `SupportIconGunship3` | `shop_gunship3` |
| `template_support_strategicmissile` | Weapon | airstrike (Designator/GPS) | 75,000 | `template_mafia_support_frog` | `SupportIconX2Y1` | `shop_strategicmissile` |
| `template_support_p_bunkerbuster` | Weapon | airstrike (Designator/Laser) | 350,000 | `template_allies_support_f15` | `SupportIconX1Y6` | `shop_bunkerbuster` |
| `template_support_p_artillery` | Weapon | artillery (Grenade) | 50,000 | `template_china_support_66` | `SupportIconX2Y2` | `shop_artillery` |
| `template_support_p_artillery2` | Weapon | artillery (Grenade) | 100,000 | `template_china_support_66_2` | `SupportIconArtillery2` | `shop_artillery2` |
| `template_support_p_artillery3` | Weapon | artillery (Grenade) | 200,000 | `template_china_support_66_3` | `SupportIconArtillery3` | `shop_artillery3` |
| `template_support_p_clusterbomb` | Weapon | airstrike (Designator/GPS) | 65,000 | `template_allies_support_a10` | `SupportIconX1Y1` | `shop_clusterbomb` |
| `template_support_p_carpetbomb` | Weapon | airstrike (Designator/GPS) | 500,000 | `template_allies_support_b52` | `SupportIconX2Y4` | `shop_carpetbomb` |
| `template_support_crate_mafiaCovert` | Supply | drop (Grenade) | 8,000 | `template_mafia_littlebird + template_crate_Covert` | `SupportIconCrate_covert` | `shop_crate_covert` |
| `template_support_crate_mafiaAllies` | Supply | drop (Grenade) | 6,500 | `template_mafia_littlebird + template_crate_west` | `SupportIconCrate_west` | `shop_crate_west` |
| `template_support_crate_mafiaSpecial` | Supply | drop (Grenade) | 12,000 | `template_mafia_littlebird + template_crate_MafiaSpecial` | `SupportIconCrate_special` | `shop_crate_special` |
| `template_support_crate_mafiaChina` | Supply | drop (Grenade) | 6,000 | `template_mafia_littlebird + template_crate_East` | `SupportIconCrate_east` | `shop_crate_east` |
| `template_support_crate_mafiaHealth` | Supply | drop (Grenade) | 2,500 | `template_mafia_littlebird + template_crate_health` | `SupportIconCrate_east` | `shop_crate_health` |
| `template_support_crate_nk` | Supply | drop (Grenade) | 7,500 | `template_mafia_littlebird + template_crate_East` | `SupportIconCrate_east` | `shop_crate_east` |
| `template_support_crate_mafia` | Supply | drop (Grenade) | 4,000 | `template_mafia_littlebird + template_crate_Mafia` | `SupportIconCrate_mafia` | `shop_crate_mafia` |
| `template_support_crate_mafiaHeavy` | Supply | drop (Grenade) | 15,000 | `template_mafia_littlebird + template_crate_MafiaHeavy` | `SupportIconCrate_heavy` | `shop_crate_heavy` |
| `template_support_deliverStolenNKBRDM` | Vehicle | drop (Grenade) | 40,000 | `template_mafia_cargocopter + template_nk_brdm_empty` | `SupportIconX3Y2` | `shop_nkbrdm` |
| `template_support_deliverStolenNKBMP` | Vehicle | drop (Grenade) | 75,000 | `template_mafia_cargocopter + template_nk_bmp_empty` | `SupportIconX5Y6` | `shop_nkbmp` |
| `template_support_deliverStolenNKBTR60` | Vehicle | drop (Grenade) | 25,000 | `template_mafia_cargocopter + template_nk_btr60_empty` | `SupportIconX5Y5` | `shop_nkbtr60` |
| `template_support_deliverStolenNKJeep` | Vehicle | drop (Grenade) | 12,000 | `template_mafia_cargocopter + template_nk_jeep_empty` | `SupportIconX6Y3` | `shop_nkjeep` |
| `template_support_deliverStolenCivCar` | Vehicle | drop (Grenade) | 5,000 | `template_mafia_cargocopter + template_civ_80civic_empty` | `SupportIconX6Y1` | `shop_civcar` |
| `template_support_deliverStolenAlliesBlackhawk` | Vehicle | drop (Grenade) | 65,000 | `template_mafia_blackhawk + self` | `SupportIconBlackHawk` | `shop_alliesblackhawk` |
| `template_support_deliverLittleBird` | Vehicle | drop (Grenade) | 95,000 | `template_mafia_littlebird + self` | `SupportIconMafiaLilBird` | `shop_mafialilbird` |
| `template_support_deliverStolenSKHumveeAT` | Vehicle | drop (Grenade) | 35,000 | `template_mafia_cargocopter + template_sk_humveeAT_empty` | `SupportIconX6Y4` | `shop_skhumveeat` |
| `template_support_deliverStolenSKHumveeMG` | Vehicle | drop (Grenade) | 25,000 | `template_mafia_cargocopter + template_sk_humveeMG_empty` | `SupportIconX6Y5` | `shop_skhumveemg` |
| `template_support_deliverStolenSKAPC` | Vehicle | drop (Grenade) | 60,000 | `template_mafia_cargocopter + template_sk_kafv_empty` | `SupportIconX4Y2` | `shop_skapc` |
| `template_support_deliverStolenAlliesStryker` | Vehicle | drop (Grenade) | 40,000 | `template_mafia_cargocopter + template_allies_stryker_empty` | `SupportIconX5Y4` | `shop_alliesstryker` |
| `template_support_deliverStolenBaggageCar` | Vehicle | drop (Grenade) | 10,000 | `template_mafia_cargocopter + template_civ_baggagecar_empty` | `SupportIconBaggageCar` | `shop_baggagecar` |
| `template_support_deliverStolenChinaJeep` | Vehicle | drop (Grenade) | 15,000 | `template_mafia_cargocopter + template_china_jeep_empty` | `SupportIconChinaJeep` | `shop_chinajeep` |
| `template_support_deliverStolenChinaAPC` | Vehicle | drop (Grenade) | 50,000 | `template_mafia_cargocopter + template_china_apc_empty` | `SupportIconX4Y4` | `shop_chinaapc` |
| `template_support_deliverStolenAlliesHumvee` | Vehicle | drop (Grenade) | 25,000 | `template_mafia_cargocopter + template_allies_humvee_empty` | `SupportIconX6Y5` | `shop_allieshumvee` |
| `template_support_deliverTechnicalAT` | Vehicle | drop (Grenade) | 30,000 | `template_mafia_cargocopter + template_mafia_technicalAT_empty` | `SupportIconX5Y2` | `shop_mafiatechnicalat` |
| `template_support_deliverTechnicalMG` | Vehicle | drop (Grenade) | 20,000 | `template_mafia_cargocopter + template_mafia_technicalMG_empty` | `SupportIconX5Y1` | `shop_mafiatechnicalmg` |
| `template_support_deliverTechnicalGL` | Vehicle | drop (Grenade) | 35,000 | `template_mafia_cargocopter + template_mafia_technicalGL_empty` | `SupportIconX5Y1` | `shop_mafiatechnicalgl` |
| `template_support_crate_mafiaSniper` | Supply | drop (Grenade) | 12,000 | `template_mafia_littlebird + template_crate_Sniper` | `SupportIconCrate_sniper` | `shop_crate_sniper` |
| `template_support_crate_MafiaAmmoveh` | Supply | drop (Grenade) | 8,000 | `template_mafia_littlebird + template_pic_ammoveh` | `SupportIconCrate_ammoveh` | `shop_crate_ammoveh` |
| `template_support_crate_MafiaArmor` | Supply | drop (Grenade) | 13,000 | `template_mafia_littlebird + template_pic_armor` | `SupportIconCrate_armor` | `shop_crate_armor` |
| `template_support_deliverStolenZSU` | Vehicle | drop (Grenade) | 65,000 | `template_mafia_cargocopter + template_nk_zsu_empty` | `SupportIconX5Y3` | `shop_nkzsu` |
| `template_support_deliverStolenPressTruck` | Vehicle | drop (Grenade) | 20,000 | `template_mafia_cargocopter + template_civ_presstruck_empty` | `SupportIconPressTruck` | `shop_presstruck` |
| `template_support_deliverStolenAlliesAvenger` | Vehicle | drop (Grenade) | 35,000 | `template_mafia_cargocopter + template_allies_avenger_empty` | `SupportIconX6Y2` | `shop_alliesavenger` |
| `template_support_deliverStolenNKTransport` | Vehicle | drop (Grenade) | 10,000 | `template_mafia_cargocopter + template_nk_transport_empty` | `SupportIconNKTransport` | `shop_nktransport` |
| `template_support_deliverStolenSportsCar` | Vehicle | drop (Grenade) | 25,000 | `template_mafia_cargocopter + template_civ_sportscar_empty` | `SupportIconSportsCar` | `shop_sportscar` |
| `template_support_deliverStolenTunguska` | Vehicle | drop (Grenade) | 100,000 | `template_mafia_cargocopter + template_china_tunguska_empty` | `SupportIconX4Y6` | `shop_tunguska` |
| `template_support_p_fuelairbomb` | Weapon | airstrike (Designator/GPS) | 450,000 | `template_china_support_frogfoot` | `SupportIconX1Y3` | `shop_fuelairbomb` |
| `template_support_deliverStolenNKLittleBird` | Vehicle | drop (Grenade) | 115,000 | `template_mafia_NKlittlebird + self` | `SupportIconNKLilBird` | `shop_nklilbird` |
| `template_support_deliverVIPCar` | Vehicle | drop (Grenade) | 35,000 | `template_mafia_cargocopter + template_civ_50skgbcar` | `SupportIconMafiaVIPCar` | `shop_mafiavipCar` |
| `template_support_deliverStolenChinaFuelTruck` | Vehicle | drop (Grenade) | 20,000 | `template_mafia_cargocopter + template_china_fueltruck_empty` | `SupportIconChinaFuelTruck` | `shop_chinafueltruck` |
| `template_support_crate_mafiaVehicle` | Supply | drop (Grenade) | 30,000 | `template_mafia_littlebird + template_crate_ChinaVehicle` | `SupportIconCrate_vehicle` | `shop_crate_vehicle` |
| `template_support_crate_sniperrifle` | Supply | drop (Grenade) | 15,000 | `template_mafia_littlebird + template_crate_sniperRifle` | `SupportIconCrate_sniper` | `shop_crate_sniper` |
| `template_support_crate_stinger` | Supply | drop (Grenade) | 18,000 | `template_mafia_littlebird + template_crate_stinger` | `SupportIconCrate_stinger` | `shop_crate_stinger` |
| `template_support_crate_atrocket` | Supply | drop (Grenade) | 18,000 | `template_mafia_littlebird + template_crate_atrocket` | `SupportIconCrate_ATrocket` | `shop_crate_atrocket` |
| `template_support_crate_mafiaAdvanced` | Supply | drop (Grenade) | 20,000 | `template_mafia_littlebird + template_crate_advanced` | `SupportIconCrate_Prototype` | `shop_crate_prototype` |
| `template_support_crate_mafiaCheatgun` | Supply | drop (Grenade) | 1,000,000 | `template_mafia_littlebird + template_crate_mafiaCheatgun` | `SupportIconCrate_cheat` | `shop_crate_cheat` |
| `template_support_crate_mafiaC4` | Supply | drop (Grenade) | 8,000 | `template_mafia_littlebird + template_crate_c4` | `SupportIconCrate_c4` | `shop_crate_C4` |

## Appendix: engine functions

| address | what | evidence |
|---|---|---|
| `0x34ABC8` | the PDA shop object (RsDataPodVM) | `ecx` in every Shop_* binding |
| `0xCE460` | load emails.ini and itemshop.ini, reset shop | strings `0x2E8B84`, `0x2E8B2C`; called by Level_Load |
| `0xCAB90` | parse `[ShopItem]` blocks | field strings `0x2E85E4..0x2E8584`, section hash `ShopItem` |
| `0xCDA60` | unlock item (hash, out-of-stock flag) | callers Lua_Shop_UnlockItem, `0xCDC20`, `0xCEAA0`, `0x15CB20`, `0x29380` |
| `0xC8B20` | set shop locked | Lua_Shop_SetShopLocked |
| `0xC8B60` | set stock of an unlocked item | writes slot `+0x1198` |
| `0xC8BF0` | find unlocked index by template hash | |
| `0xCAE90` | shown price | faction 4 relation, 0.2 / 1.0 / 0.5 |
| `0xCDBD0` | can afford | player `+0xAC4` vs price |
| `0xCAE60` | donation cost | scribble `MafiaDonation`, default 10000 |
| `0xCD700` | shop button handler (order, donate, navigate) | Player_SetMoney, FactionManager_SetRelation, `0x5A220` |
| `0xCE9F0` | choose page on entry | `0x95EE0` hostile check, lock byte |
| `0xCD6C0` | set page state | `+0x19AC` |
| `0xCAEF0` / `0xCDC20` | save / load shop state | `NumShopItemInStock`, `ShopItem%i`, `ShopItemIsNew%i`, `ShopIsLocked` |
| `0xCCB50` | game-state start: PDA cards, helicopter out-of-stock rule | mission hashes, `0xC8B60` |
| `0xCEAA0` / `0x15C980` | unlock all (cheat) | |
| `0xC89F0` | category -> tab name | |
| `0xC3D40`, `0xC6100`, `0xC65B0`, `0xC66C0`, `0xC20C0`, `0xBEFB0` | shop drawing: list, confirm, donation, locked, account, browser bar | text-key hashes, `0x2E7CF8` |
| `0x5A220` / `0x5A4B0` / `0x5A580` | support menu add / remove / reset | Support_* bindings |
| `0x16320` | ammo init (MonetaryCost, MinMonetaryCost, ...) | property hashes |
| `0x16630` / `0x166E0` | base price / charge on use | `interface.economyevent.support` |
| `0x17B40`, `0x19670`, `0x1C010`, `0x1C790` | beacon init, support projectile init, helicopter delivery, airstrike spawn | property hashes, Ai_Deliver, Spawner_SpawnTemplate |
| `0x32620` | support weapon base price (HUD, use checks) | MonetaryCost; bribe item by faction 4 |
| `0x15CF70` / `0x15CC90` / `0x15E4B0` / `0x15CB20` | reward table fill / lookup / dispatch / grant | template hashes, `Mail_Post` |
| `0xD69E0` | contract-completion rewards (RsFrontEnd) | `num_*_missions_cmpl_*` hashes |
| `0x29380` | chop shop sale | RsActorBuildingChopShop.cpp |
| `0x95B70` / `0x95BE0` / `0x95EE0` / `0x95DD0` | relation get / level threshold / attitude level / set | table `0x323338`, thresholds `0x2E6C78` |
