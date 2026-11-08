# Layers, encounters and missions

How missions put soldiers and vehicles into the world, and how a mod adds its own layers and contract missions. Established from retail data and scripts. Tested in game (2026-11-08).

## How the game does it

- **Layers.** A world layer is a set of records with one name: `world` (objects), `encounter`, `path`, `lineregion`, `region` and a `list`. Mission and gameflow scripts load a layer with `Utility_ReadLevelFile('<layer>')` (`'\SW\<layer>'` in gameflow). This reads the layer's list, which names what to load: `[world]` / `[encounter]` / `[path]` ... `load <layer>`, plus the models, textures and animations it needs. The opening mission (`playable_intro_mission`) reads `swn_allies0` and its `_enc_` layers, then calls `Allies0Init()` and later `Allies0Begin()` from the `allies0` script.
- **Encounters.** An encounter has named point sets (`A`, `B`, ...) and named squads. A squad records how many members it has and which point sets it uses. Objects join a squad through their own `encounter` and `squad` properties; there is no separate squad-type table.
- **Scripts** command squads by name: `Squad_Command(encounter, squad, 'TacticalMove', 'A', 'hiPri')`, `Squad_CommandQueue`, `Squad_GetAllActors`, `Squad_ReportStatus`, `Squad_SetHostileRange`. Single actors take `Ai_Command(name, ...)`.

## Adding a layer in a mod

`encounters/<layer>.json` describes a new layer. The build writes its world, encounter and list records into `assets.dsk`:

```json
{
 "map": "SWN",
 "encounters": [{
   "name": "FoundryAmbush",
   "points": {"A": [[1534.0, -1.0, 1830.0], [1538.0, -1.0, 1830.0]]},
   "squads": [{
     "name": "Squad1", "points": ["A"],
     "members": [
       {"from": "RoadblockRunner", "name": "FoundryAmbushNk01", "position": [1532.4, -1.6, 1787.8], "yaw": 0},
       {"from": "RoadblockRunner", "name": "FoundryAmbushNk02", "position": [1538.2, -1.8, 1788.8], "yaw": 0, "set": {"hitPoints": "500"}}
     ]}]}],
 "objects": [{"from": "jeep7", "name": "FoundryJeep", "position": [1540, -1, 1800], "yaw": 90}]
}
```

- **Copying objects:** `from` names any existing world object, a `template_*` or a placed one. Its model and all its properties are copied. Members get `encounter`, `squad` and `path: none`, and `set` overrides any property. Names must be new and unique.
- **Assets:** copy objects whose assets are already loaded where the layer is used, for example soldiers from a layer the same mission loads. Otherwise list what to load under `"loads": {"model": [...], "texture": [...], "animation": [...]}`.
- **Positions:** world x, y, z in metres (Foundry Console's Position line). Yaw is in degrees. Lower z is north.
- **Map:** `map` is the layer's terrain tag (`SWN` for the opening map).

A layer can also have paths, for patrols and routes:

```json
"paths": {
 "foundry_patrol": {
  "points": [[1530, -1, 1855], [1545, -1, 1855], [1545, -1, 1870], [1530, -1, 1870]],
  "pauses": {"0": 3.0},
  "links": [{"path": "foundry_patrol", "their_point": 0, "our_point": 3}]
 }
}
```

- **Pauses:** seconds to wait at a point.
- **Links:** join one of this path's points (`our_point`, default its last) to a point on another path (`their_point`, default its first). Retail paths chain this way, and linking the last point back to the first makes a loop.
- **Following a path:** send an actor along it with `Ai_Command('<actor>', 'Patrol', 'foundry_patrol', '1.0', 'hiPri')`. Objects can also carry a `path` property.

Tested in game (2026-11-08): an allied soldier copied into a new layer patrolled such a loop when ordered.

A layer can also have boundaries: named polygons that scripts watch and switch on and off.

```json
"boundaries": {
 "foundry_m9_zone": {"points": [[1470, 31, 785], [1502, 31, 785], [1502, 31, 815], [1470, 31, 815]]},
 "my_killbox": {"points": [[...], [...], [...]], "properties": {"type": "killbox", "TestInside": "false"}}
}
```

- **Plain triggers.** Without properties a boundary is a trigger zone. About half of the 518 retail boundaries are like this.
- **Typed areas.** `type` gives an area behaviour. Retail uses:
  - `killbox`: restricted area; air strikes come when you're outside it with `TestInside` false;
  - `radiation`, with `RateOfDamagePerSecond`;
  - `water`;
  - `atmosphere`;
  - `ambience`, with `sound` and `width`;
  - `faction`, with `faction`.
- **Scripts:** `Event_ActorInBoundary('Func', 'b', 'player0', 'foundry_m9_zone', TRUE)` calls `Func` when the actor enters (FALSE: when it leaves). `Boundary_Enable('my_killbox', TRUE)` switches a boundary on.

Retail boundaries all have the same chunks (`lrg_`: `NAME`, `INFO` = `u16` 0, `u16` properties, `u16` points, then `PROP` name/value pairs and `PNTS`). The build writes them in that order and adds `[lineregion]` to the layer's list.

Tested in game (2026-11-08): a trigger zone in the test mission's layer showed its message when the player entered it.

Load the layer and command it from a script (a mod's `scripts/<name>.lua`). For example, in a copy of `allies0`:

```lua
function Allies0Init()
Utility_ReadLevelFile('foundry_ambush')
...
function FoundryAmbushGo()
Squad_Command('FoundryAmbush', 'Squad1', 'TacticalMove', 'A', 'hiPri')
end
```

Tested in game (2026-11-08): the layer above (three NK soldiers copied from the roadblock runner) loaded from the opening mission's script, and the soldiers appeared and attacked.

## New missions

How a contract runs, all in the game's scripts:

1. **The arc.** Each faction has an ordered list of missions per map, in `util_GetMissionArcTable()` in the `util_misc` script. Allies on `sw` is `{ 1, 3, 4, 5, 6, 8 }`. `current_<faction>_mission` (scribble memory) holds the position in that list.
2. **Launch.** `global`'s `LoadMission(map, faction, position)` turns the position into an asset ID (`sw_allies3`) and runs the script of that name.
3. **Script list.** The game only runs scripts that a loaded list names under `[script]`. Each map's own list (`sw`, `nw`) names its mission scripts.
4. **Start-up.** The mission script's `ScriptInit()` calls `mission_GenericInit()`. That reads the mission's `mission.ini` section (fee, title and description string keys) and loads the layer named after the mission.
5. **End.** The script finishes with `mission_MissionComplete()` or `mission_MissionFailure()`. Optional hooks such as `custom_InitialNotifications`, `post_InitialNotifications` and `pre_MissionComplete` change the standard flow.

A mod mission is three files named after its asset ID, `<map>_<faction><number>`:

- `missions/sw_allies9.json`:

  ```json
  {
   "after": 8,
   "title": "Foundry Field Test",
   "description": "Clear an NK squad near the Allied HQ.",
   "mail": {"sender": "mail.senders.techgal", "subject": "Contract: 'Foundry Field Test'",
            "body": "An NK squad has dug in just north of our HQ..."},
   "strings": {"sw.allies9.obj.killsquad.start": "Eliminate the NK squad north of the HQ"},
   "ini": {"ContractFee": 50000}
  }
  ```

  The build:
  - adds 9 to the allies `sw` arc, after mission 8 (at the end without `after`);
  - lists `sw_allies9` under `[script]` in the `sw` list;
  - adds `[SW_Allies9]` and its `MissionName` to `mission.ini` (written into the mod as `DATAxbox/mission.ini`), copied from the faction's first mission on the map, with `ini` applied;
  - puts `title` and `description` in the always-loaded `missioninfo` text, under the keys retail uses (`MissionInfo.MissionTitle.SWAllies9`), and points the section at them;
  - adds the opening mail, `sw_allies9_initial`, to `DATAPOD/emails.ini`. `mission_GenericInit` posts it when the contract starts.

  The mail's subject and body and the `strings` (objective texts, messages) go in a new text record named after the mission. The mission's layer loads it under `[registry]`, as retail missions do with theirs. Strings filed anywhere else aren't loaded during the mission, and `Utility_GetStringById` returns nothing.
- `scripts/sw_allies9.lua`: the mission script. One with an objective:

  ```lua
  tSquad = { 'FoundryM9Nk01', 'FoundryM9Nk02', 'FoundryM9Nk03' }
  iDown = 0
  function ScriptInit()
  Utility_LoadUtilityScript('mission')
  Utility_LoadUtilityScript('mission_objective')
  Utility_LoadUtilityScript('mission_objectiveverb')
  Utility_LoadUtilityScript('util_misc')
  mission_GenericInit()
  end
  function post_InitialNotifications()   -- after the contract mail
  sObj = Utility_GetStringById('sw.allies9.obj.killsquad.start')
  mission_ObjectiveDestroy{ callbackFunc = SoldierDown, objectiveName = 'killsquad',
                            objectiveDesc = sObj, actorName = tSquad }
  util_ObjMsgPrint{ sMsg = sObj, sType = 'PRI_OBJ_ADD', vIdx = 1,
                    tCounter = { vCurr = 0, vLim = 3 }, sObjective = 'killsquad' }
  end
  function SoldierDown(sActor, bComplete, sObjName)
  iDown = iDown + 1
  util_ObjMsgPrint{ sMsg = sObj, sType = iDown >= 3 and 'PRI_OBJ_CMPL' or 'PRI_OBJ_UPD_POS', vIdx = 1,
                    tCounter = { vCurr = iDown, vLim = 3 }, sObjective = 'killsquad' }
  if iDown >= 3 then mission_RemoveObjective('killsquad') mission_MissionComplete() end
  end
  ```

  `mission_ObjectiveDestroy` marks its targets in the world and on the minimap. `util_ObjMsgPrint` shows the objective line and counter. The `mission_objectiveverb` script also offers `mission_ObjectiveAction`, `mission_ObjectiveSubdue`, `mission_ObjectiveExtract` and `mission_ObjectiveDisable`.
- `encounters/sw_allies9.json`: the mission's layer. Copied objects bring their own layer's model, texture and animation loads, so soldiers from any mission work (`"copy_loads": false` turns that off).

The build warns about any new script that no list names: the game silently ignores those.

### Offering the mission at the HQ

The HQ's own contract order can't reach a new mission. The bouncers (`bouncer` script) offer the current arc position only while it is within the chapter's range (`util_GetLastMissionNumberInChapterArc`). Positions are also fixed in several other places: ace missions at 3 and 6 for allies (`util_IsAceMission`), chapter starts, the final mission, and gameflow's layer rules. So appending a mission puts it after the province's last contract, and inserting one would shift all of those.

Instead, each mod mission gets a contact at its faction's HQ, a side contract you can take whenever no contract is running:

- **Where it comes from.** The build patches one line into `global`'s `LoadNoMissionState`, right after `SpawnBouncers(TRUE)`. That line runs a generated script, `foundry_contracts`, listed in `sw` and `nw`. The script spawns each contact on its map.
- **How it looks.** The contact uses the faction's bouncer template and stands 3 m from `loc_hq-<faction>-bouncer`, facing the way that spot faces. It is marked like a bouncer (`util_SetupSingleTargetActorObjective` with `TARGET_TYPE_HQ_<FACTION>`): a gold ring, a name above it and an HQ blip.
- **Using it.** The prompt reads "Discuss contract: <title>". Using the contact asks yes/no with the mission's title and description (`Utility_QuestionBox`). Yes starts the mission through `DebugSkipToMission`; no leaves the contact there.
- **Campaign progress is kept.** Starting a mission through `DebugSkipToMission` makes it the faction's current arc position, and finishing it adds to `current_<faction>_mission` and the completed-mission counters (`mission_MissionComplete2`). So the contact saves `current_<faction>_mission`, `total_missions_completed<faction>` and `num_std_missions_cmpl_<faction>` before starting. A second hook, the first line of `LoadNoMissionState`, puts them back when the contract ends, however it ends. The game runs `LoadNoMissionState` when a mission script closes; it is the first argument of `LoadMission`'s `Utility_RunLuaScript`.
- **Completions are remembered.** If the mission just completed (`JustCompletedStandardMission`, read before `LoadNoMissionState` erases it), the scribble value `foundry_done_<id>` is set and the contact no longer appears. `"repeatable": true` in the mission file keeps offering it.
- **Settings.** Change the contact with `"contact": {"name": "Major Kim", "prompt": "Ask about the job", "template": ..., "location": ..., "offset": [3, 0, 0], "yaw": 0}`. Leave it out with `"contact": false`.

To reach free roam at a faction HQ from Skip Intro (Foundry Console's Lua box):

```lua
Utility_WriteStringToScribbleMemory('SkipTo_Map','sw') Utility_EraseValueFromScribbleMemory('SkipTo_Faction') Utility_EraseValueFromScribbleMemory('SkipTo_MissionNumber') Utility_EraseValueFromScribbleMemory('mission_accepted') Utility_WriteStringToScribbleMemory('PlayerStartLocationName','loc_hq-allies-respawn') Utility_ResetToBaseLayerOnly('sw')
```

Tested in game (2026-11-08): the contact stood beside the allies bouncer with a ring, its name and the right facing, offered the contract, and started it. After the contract was completed, `current_allies_mission` was still 2, as before, and the contact was gone.

To try a mission without playing up to it, run this in Foundry Console's Lua box. It works from anywhere, including Skip Intro, and reloads the `sw` map at the faction's HQ with the contract accepted:

```lua
Utility_WriteStringToScribbleMemory('SkipTo_Map','sw') Utility_WriteStringToScribbleMemory('SkipTo_Faction','allies') Utility_WriteNumberToScribbleMemory('SkipTo_MissionNumber',9) Utility_WriteStringToScribbleMemory('skipto_via_cheating','true') Utility_EraseValueFromScribbleMemory('mission_accepted') Utility_ResetToBaseLayerOnly('sw')
```

This is what `global`'s `DebugSkipToMission` does, plus the map switch. Use a throwaway session.

Tested in game (2026-11-08): `sw_allies9` (three NK soldiers copied from a mission-6 template, placed north of the allies HQ), started from the skip line above. The contract mail arrived, the objective showed its text with markers on the soldiers and a 0/3 counter, and the counter rose to 3/3 before the mission completed.

Not yet decoded: path animation events (`EVNT`), how traffic zones pick encounters (`Traffic_SetZoneEncounter`), and the first `INFO` word of point sets (always 1).
