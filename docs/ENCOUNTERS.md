# Layers and encounters

How missions put soldiers and vehicles into the world, and how a mod adds its own. Established from retail data and scripts. Tested in game (2026-11-08).

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

Not yet decoded: path animation events (`EVNT`), how traffic zones pick encounters (`Traffic_SetZoneEncounter`), and the first `INFO` word of point sets (always 1).
