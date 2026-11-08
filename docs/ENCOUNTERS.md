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

- `missions/sw_allies9.json`: `{"after": 8, "ini": {"ContractFee": 50000}}`. The build:
  - adds 9 to the allies `sw` arc, after mission 8 (at the end without `after`);
  - lists `sw_allies9` under `[script]` in the `sw` list;
  - adds `[SW_Allies9]` and its `MissionName` to `mission.ini` (written into the mod as `DATAxbox/mission.ini`), copied from the faction's first mission on the map, with `ini` applied.
- `scripts/sw_allies9.lua`: the mission script. A minimal one:

  ```lua
  function ScriptInit()
  Utility_LoadUtilityScript('mission')
  Utility_LoadUtilityScript('mission_objective')
  Utility_LoadUtilityScript('util_misc')
  mission_GenericInit()
  Event_ActorHitPointsLessThan('TargetDown', 'b', 'FoundryM9Nk01', 1)
  end
  function custom_InitialNotifications()   -- skips the standard opening mail
  Ui_MessageBoxPrint('[yellow]Defeat the soldier north of the HQ', 0)
  end
  function TargetDown() mission_MissionComplete() end
  ```

- `encounters/sw_allies9.json`: the mission's layer. Copied objects bring their own layer's model, texture and animation loads, so soldiers from any mission work (`"copy_loads": false` turns that off).

The build warns about any new script that no list names: the game silently ignores those.

To try a mission without playing up to it, run this in Foundry Console's Lua box. It works from anywhere, including Skip Intro, and reloads the `sw` map at the faction's HQ with the contract accepted:

```lua
Utility_WriteStringToScribbleMemory('SkipTo_Map','sw') Utility_WriteStringToScribbleMemory('SkipTo_Faction','allies') Utility_WriteNumberToScribbleMemory('SkipTo_MissionNumber',9) Utility_WriteStringToScribbleMemory('skipto_via_cheating','true') Utility_EraseValueFromScribbleMemory('mission_accepted') Utility_ResetToBaseLayerOnly('sw')
```

This is what `global`'s `DebugSkipToMission` does, plus the map switch. Use a throwaway session.

Tested in game (2026-11-08): `sw_allies9`, three NK soldiers copied from a mission-6 template placed north of the allies HQ, started from the skip line above with its message and soldiers.

Not yet decoded: path animation events (`EVNT`), how traffic zones pick encounters (`Traffic_SetZoneEncounter`), and the first `INFO` word of point sets (always 1).
