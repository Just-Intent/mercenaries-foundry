# Foundry Console (developer overlay)

A developer window for Mercenaries Recompiled, built as a plugin on the [plugin API](PLUGINS.md). Press **F7** in game to show or hide it. Tested in game (2026-11-08).

| Part | What it does |
|---|---|
| Inspector | Gameplay state, the player's position, heading, money and hit points, and the game's last five spawns by template name (a hook on `Spawner_SpawnTemplate`). Updates four times a second. |
| Spawn | Search the game's 1,611 object templates by name, type or faction. **Loaded here only** asks the game which ones the current area has loaded (`Template_GetProperty`); only those spawn. **Spawn** (or double-click) places the selected one 8 m ahead of the player. |
| +$10,000 | Adds money through `Player_SetMoney`. |
| Nearby | World objects within 100 m, nearest first (every half second while the window is open), named where the game has a name for them, otherwise labelled by model in brackets (e.g. `[global_militaryboxstack]`). Selecting one shows its handle, flags, position, distance, heading and its properties as the game reports them (objectType, displayName, faction, hitPoints, GeometryFile, ...). |
| Lua | Run a line of Lua in one of the game's Lua states (Enter runs it). An expression shows its values (`2 + 3` shows `= 5`); a statement shows `ok`; errors show Lua's message. |

## Install

Needs the port with the Foundry patches and hook wrappers ([port/README.md](../port/README.md)). Then, from this repository's root (PowerShell):

```powershell
$mods = "external\Mercenaries-Recompiled\build\mercenaries\bin\Release\mods"
cmd /c "plugins\build.cmd plugins\foundry-console `"$mods\Foundry Console`""
python -m mercfoundry templates game_files/DATAxbox -o "$mods\Foundry Console\templates.tsv"
python -m mercfoundry objectnames game_files/DATAxbox -o "$mods\Foundry Console\objectnames.txt"
```

The template and object-name lists come from your own game data, so it is generated at install time and never committed. Enable **Foundry Console** in the launcher's mod selector and restart the game.

## How it works

The window runs on its own thread. Everything that touches the game is queued and done in the plugin's `on_update`, on the game's thread, with `call_guest` and the [function map](FUNCTION_MAP.md): `World_FindActorByName("player0")`, the actor fields `transform`, `money` and `hitPoints`, `Template_GetProperty`, `Spawner_SpawnTemplate` and `Player_SetMoney`.

The object inspector reads the world's tables directly: 16,384 object slots (`g_WorldObjects`, with handles in `g_WorldObjectHandles`), each object's flags, position (+0x08) and rotation (+0x14), and the named-object table (`g_WorldNameKeys` / `g_WorldNameHandles`) read backwards to name objects by hash against `objectnames.txt`. Properties come from the object's own lookup (vtable +0x0C, by name hash).

The Lua console learns the game's Lua states by hooking `LuaScript_RegisterCommands`, checks a state is still known (`LuaScript_FromState`) before each run, and then, in that state: `luaL_loadbuffer` (first wrapped as an expression whose values come back as one string, then as a plain statement), `lua_pcall`, `lua_tostring` and `lua_settop` to restore the stack. The game keeps only a few long-lived states (two script objects, `0x3717A0` and `0x371848`; the first gets a new state on reload) and mission scripts run inside them, so "Newest script" is the usual choice and the game's script commands work from the console.

Limits: it is a separate window, so it shows over a windowed or borderless game but not exclusive fullscreen, and keys typed into it may also reach the game. Spawned objects get no ground check (they appear 1.5 m above the player's height). Lua lines run at the plugin update point, outside the game's own script calls; `print` output doesn't appear in the console (return values instead). Most world props have no name; the list labels them by their `GeometryFile` property (looked up once per object), and only objects without one show a handle.
