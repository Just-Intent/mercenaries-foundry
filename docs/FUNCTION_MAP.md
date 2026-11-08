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

## Structures

`map/manual.json` also records structure fields (exported as `MF_OFF_<Struct>_<field>` by `funcmap header`), each with the code that shows them. So far:

| Structure | Fields |
|---|---|
| `WorldObject` | vtable (+0x04 type bits, +0x0C property text by name hash: objects answer the same properties as world-layer `PROP` data, e.g. `hitpoints`), +0x24 actor, +0x28 handle, +0x30 flags (bit 0 active, bit 2 removed) |
| `Actor` | +0x98 hit points, +0xB0 world transform, +0x6C1 collision-disable count, +0xAC4 money (player) |
| `Ai` | +0x54 command queue |
| `Vehicle` | +0x8BC driver seat, +0x8C8 rider count |

## Adding a name

Add an entry to `map/manual.json` with `address` (a function start in the port's `functions.json`), `name`, optional `signature` and `notes`, and `evidence`. Keep names neutral and follow the evidence; prefer `Thing_Verb` style. Then run `funcmap build`. `build` refuses addresses that aren't function starts.

The clean-room rules in [PROVENANCE.md](PROVENANCE.md) apply: evidence comes from the retail executable and data, live tests, and public documentation (for example Lua's public C API names). Never from leaked source.
