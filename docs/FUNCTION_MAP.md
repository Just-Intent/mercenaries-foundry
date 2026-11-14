# Function map

`map/functions.json` names the game's functions and says which source file each came from, with the evidence for every claim. Addresses are the retail executable's (`default.xbe`, US); they match the port's `sub_XXXXXXXX` names, so `Lua_Mission_Spawn` at `0x0011D560` is `sub_0011D560` in the recompiled code.

## Using it

```bash
python -m mercfoundry funcmap find spawn            # search names, script commands, files, notes
python -m mercfoundry funcmap show Lua_Mission_Spawn   # evidence, file, callers and callees by name
python -m mercfoundry funcmap header -o mercfoundry_functions.h   # #define MF_FN_<name> <address> for C/C++
python -m mercfoundry funcmap names -o ghidra_names.json          # {address: name} for the port's recompiler
```

Rebuild it from your own copy (needs `game_files/default.xbe` and the port's `analysis/disasm` folder, which its build produces):

```bash
python -m mercfoundry funcmap build
```

## Where the names come from

| Source | What it gives | Evidence |
|---|---|---|
| Lua registration tables | The C function behind every script command: `Lua_<Command>` (360, e.g. `Lua_Faction_SetRelation`), plus Lua's own library functions (`LuaLib_*`) | Retail code: six `{name, function}` tables in `.rdata`, ending in `{0, 0}`. The `Actor_` table's names get that prefix at registration (string at `0x2F0018`, used at `0x1128A2`). |
| Assert strings | The source file a function was compiled from (`RsActor.cpp`, `RedWorld.cpp`, ... 80 files) and the assert's line | Retail data: strings like `.\Source\RsActor.cpp [582]` and the code that references them. `file_evidence: direct` = the function references one; `between` = it sits between two functions from the same file in the same section (the linker keeps a file's functions together). |
| Script command wrappers | The engine function a command calls, named after the command (128, e.g. `Actor_HideGrenadeProp`) | Inferred, by rule: it is the binding's only call into engine code (not a named helper, the Lua core or library code), no other command's binding calls it, and at most three places call it at all. |
| `map/manual.json` | Hand-named engine functions, globals and structure fields, with signatures | Each entry's `evidence` says how it was established: "retail code" (read from the disassembly, with the addresses involved) or "inferred" (from how other code uses it). |

There is no RTTI or symbol table in the retail executable; every name is earned.

## Highlights so far

| Function | Address | Notes |
|---|---|---|
| `HashName` | `0x001F29F0` | The engine's name hash (FNV-1a, bytes OR 0x20): the one that names every archive record. |
| `Spawner_SpawnTemplate` | `0x00174480` | `thiscall` on `g_Spawner` (`0x004031B0`): spawns any object template by name hash at a transform. What `Mission_Spawn` uses. |
| `FactionManager_SetRelation` | `0x00095DD0` | `thiscall` on `g_FactionManager` (`0x00323338`); relation -1..1 (scripts pass -100..100). |
| `Lua_ArgString`, `Lua_ArgNumber`, `Lua_ArgBool`, `Lua_ArgVector3`, `Lua_ArgActor`, `Lua_PushActor` | `0x00112xxx` | How every script command reads its arguments and returns actors. |
| `World_FindActorByName` | `0x0008ECE0` | Any named actor by name hash (`HashName("player0")` is the player). |
| `World_ActorFromHandle` | `0x001EC220` | A script's actor handle to the actor, through `HashTable_Find` on the world's handle table. |
| `Player_SetMoney` | `0x00057F50` | Money is a float at `Actor + 0xAC4`; clamped at 0, plays the cash sound when it rises. |
| `Ai_SubmitCommand` | `0x001180D0` | Replace (0), append (1) or set the default (2) on an AI's command queue: `Ai_Command`, `Ai_CommandQueue`, `Ai_CommandDefault`. |
| `WorldObject_Remove`, `Actor_EnableCollision` | `0x001EE0E0`, `0x0004EC40` | Removing objects; counted collision on/off. |
| `HashTable_Find` | `0x001F2A20` | The engine's general lookup (linear probing on name hashes and handles). |
| `lua_tostring`, `lua_tonumber`, `lua_pushnumber`, `lua_gettop`, `luaL_error`, ... | `0x001DBxxx`-`0x001DCxxx` | The embedded Lua 5.0 C API. |
| `Config_FindKey`, `Config_ReadInt`, `Config_ReadFloat`, `Config_ReadString`, ... | `0x001EF0xx`-`0x001EF620` | The level config reader every system gets at level load: lines indexed by key hash, read token by token. |
| `Terrain_ReadLevelConfig`, `Terrain_GetSurfaceAt`, `Terrain_GetCellHeight`, `Terrain_Load` | `0x001F0500`, `0x001F28E0`, `0x00146A10`, `0x001F07A0` | Terrain surfaces from the level config, the surface and height at a point, and the terrain loader. Globals `g_TerrainHeights`, `g_TerrainSpacing`, `g_TerrainOriginX` and others give the loaded map. |
| `Impact_ChooseEffect`, `Material_FromName`, `PhysicsBody_SetMaterial` | `0x000B5460`, `0x0012B5F0`, `0x0012B7C0` | How a hit picks its effect from the object's material (`obstaclematerialtype`) or the ground's surface. |
| `Drivetrain_SetPhysicsValue` / `GetPhysicsValue`, `WheeledVehicle_GetPhysicsValue`, `TrackedVehicle_SetPhysicsValue`, `Helicopter_SetPhysicsValue` | `0x00136650`, `0x00136AD0`, `0x001330F0`, `0x00141690`, `0x00138FC0` | Vehicle handling values by `physics*` name hash: the setters store a float, the getters return one. |

## Structures

`map/manual.json` also records structure fields (exported as `MF_OFF_<Struct>_<field>` by `funcmap header`), each with the code that shows them. So far:

| Structure | Fields |
|---|---|
| `WorldObject` | vtable (+0x04 type bits, +0x0C property text by name hash: objects answer the same properties as world-layer `PROP` data, e.g. `hitpoints`), +0x24 actor, +0x28 handle, +0x30 flags (bit 0 active, bit 2 removed) |
| `Actor` | +0x98 hit points, +0xB0 world transform, +0x6C1 collision-disable count, +0xAC4 money (player) |
| `Ai` | +0x54 command queue |
| `Vehicle` | +0x8BC driver seat, +0x8C8 rider count |

## Finding candidates by playing (passive function mapper)

Foundry Console can record which game functions run while you play, labelled with what you were doing. Comparing labels then points at functions specific to a situation: tank-only functions against on-foot ones, helicopter against car, and so on. It finds candidates to look at, not names.

How it works: the port stores the guest address of every translated function it enters in a host variable, `g_recomp_current_func`. A thread in Foundry Console reads that variable about 8,000 times a second (it never touches the game thread) and counts hits per address and label. Every 10 seconds it appends the counts to `plugin-settings/foundry-console/samples/<date>-<time>.tsv` (one file per game run; lines of label, address and count).

**Setup.** The variable's address changes with every port build, so `mod sync` (which the launcher runs before every modded launch) reads it from the exe's linker map, `mercenaries_recomp.map`, and writes it with the map's timestamp to `plugin-settings/foundry-console/sampler-address.ini`. Foundry Console reads that file at startup and samples only if the running exe's PE timestamp matches; otherwise it logs why sampling is off (`plugin-settings/plugins.log`). Without a modded launch, run it by hand:

```bash
python -m mercfoundry funcmap sampler-setup <port>/build/mercenaries/bin/Release
```

**Turning it on.** Sampling is off by default. Set `Enabled=1` in `plugin-settings/foundry-console/foundry-console.ini` (written with defaults by the first sync; `Rate` and `FlushSeconds` are there too) to sample from the start, or type in Foundry Console's Lua box (F7):

```lua
Foundry_Sample(1)      -- on (also true or 'on'); Foundry_Sample(0) stops; Foundry_Sample() reports
```

The Inspector shows `Sampler: on, label ..., N reads/s`.

**Labels.** Outside gameplay (menus, loading, the pause menu: the host's `in_gameplay`) samples are labelled `menu`. In gameplay they are labelled `gameplay` unless you set a label:

```lua
Foundry_Label('tank')    -- now driving a tank
Foundry_Label('onfoot')
Foundry_Label('')        -- back to the automatic label
```

There is no automatic on-foot or vehicle label yet: the map has no evidenced field for "the player's current vehicle" (`Lua_Utility_GetActorsVehicle` is the lead). Set labels by hand, and switch them as soon as you change what you're doing; the few seconds around a switch blur the two.

**Reading the report.**

```bash
python -m mercfoundry funcmap profile <...>/plugin-settings/foundry-console/samples        # the labels and their counts
python -m mercfoundry funcmap profile <samples folder or .tsv files> --label tank --against onfoot
python -m mercfoundry funcmap profile <samples> --label tank --against onfoot --only        # never seen on foot
```

Each line is a function (sampled addresses are folded to the function holding them, from the port's analysis): its address, how often it appeared per 1,000 samples under the label and under the others, the ratio (`only` when the others never saw it), its name if the map has one, its source file (`between` when that is the weaker evidence), and the XBE section for library code (`D3D`, `DSOUND`, `XACTENG`, ...; from `game_files/default.xbe`). Below it, the named functions that call it and that it calls. Functions with fewer than `--min-count` samples (default 20) under the label are left out, as are those no more frequent per sample under it than under the others. Without `--against` the label is compared with all others together. Record several minutes of each label: more samples give steadier ratios.

**Limits.**

- A sample is the function entered *last*, on whichever guest thread entered one last, not the one running then: a function that returned still counts until another is entered. Short leaf functions called in loops show up under their callers' time as well as their own.
- Sampling misses brief one-off events (a single explosion, entering a vehicle): it sees what runs often or long. Hook a candidate to catch the rare ones.
- Library code (`D3D`, `DSOUND`) and the Lua core rank high whenever the situation draws or plays more; that is real but rarely the code you're after.
- It finds candidates, not names. A name still needs evidence under [Adding a name](#adding-a-name): read the candidate's code, its strings, its callers, or test it live. "Runs more in a tank" is not evidence of what a function does.
- Sample files hold addresses and counts of your own play; they stay in the game folder and are never committed.

## Adding a name

Add an entry to `map/manual.json` with `address` (a function start in the port's `functions.json`), `name`, optional `signature` and `notes`, and `evidence`. Keep names neutral and follow the evidence; prefer `Thing_Verb` style. Then run `funcmap build`. `build` refuses addresses that aren't function starts.

The clean-room rules in [PROVENANCE.md](PROVENANCE.md) apply: evidence comes from the retail executable and data, live tests, and public documentation (for example Lua's public C API names). Never from leaked source.
