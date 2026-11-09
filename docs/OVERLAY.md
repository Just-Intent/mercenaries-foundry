# Foundry Console (developer overlay)

A developer window for Mercenaries Recompiled, built as a plugin on the [plugin API](PLUGINS.md). Press **F7** in game to show or hide it, and **F8** for the inspector as a panel drawn inside the game. Tested in game (2026-11-08).

| Part | What it does |
|---|---|
| Inspector | Gameplay state, the player's position, heading, money and hit points, the terrain height under the player (read from the game's loaded heightfield), the surface there and which texture slot it comes from (`Terrain_GetSurfaceAt`; the full surface table is written to the log when it changes), and the game's last five spawns by template name (a hook on `Spawner_SpawnTemplate`). Updates four times a second. |
| Spawn | Search the game's 1,611 object templates by name, type or faction. **Loaded here only** asks the game which ones the current area has loaded (`Template_GetProperty`); only those spawn. **Spawn** (or double-click) places the selected one 8 m ahead of the player. |
| +$10,000 | Adds money through `Player_SetMoney`. |
| Nearby | World objects within 100 m, nearest first (every half second while the window is open), named where the game has a name for them, otherwise labelled by model in brackets (e.g. `[global_militaryboxstack]`). Selecting one shows its handle, flags, position, distance, heading and its properties as the game reports them (objectType, displayName, faction, hitPoints, GeometryFile, ...). Buttons below edit it: **Bring here** (3 m in front of you), **Up / Down 1 m**, **Turn -45 / +45** and **Remove**. |
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

## Live link with the editor

When the Foundry editor is running (`python -m mercfoundry edit`, port 8740), the console polls it from its own thread (WinHTTP, about four times a second; every two seconds while no editor answers), so the game never waits on the network. It sends the player's position and heading and gets back the editor's live edits, which it carries out on the game thread: move or turn an object (found by name, else by model within 2.5 m of where the game or the editor last had it), remove one, spawn a template under a name (as `Mission_Spawn` does, through `Spawner_SpawnTemplate`, when `Template_GetProperty` says it's loaded here), report which templates are loaded here (the console's own template scan), or teleport the player. Replies also go to `plugin-settings/plugins.log`. Teleporting follows `Lua_Actor_SetPosition` (the named object's actor, type-checked, then its set-position call); it works on foot, and the console reports when the game put the player back (in a vehicle). See [EDITOR.md](EDITOR.md).

## How it works

The window runs on its own thread. Everything that touches the game is queued and done in the plugin's `on_update`, on the game's thread, with `call_guest` and the [function map](FUNCTION_MAP.md): `World_FindActorByName("player0")`, the actor fields `transform`, `money` and `hitPoints`, `Template_GetProperty`, `Spawner_SpawnTemplate` and `Player_SetMoney`.

The object inspector reads the world's tables directly: 16,384 object slots (`g_WorldObjects`, with handles in `g_WorldObjectHandles`), each object's flags, position (+0x08) and rotation (+0x14), and the named-object table (`g_WorldNameKeys` / `g_WorldNameHandles`) read backwards to name objects by hash against `objectnames.txt`. Properties come from the object's own lookup (vtable +0x0C, by name hash).

Edits use the same engine calls as the game's `Actor_SetPosition`, `Actor_SetYaw` and `Actor_Remove`: objects backed by a game actor (most things) through the actor's vtable (+0x64 set position, +0x68 set yaw in radians, +0x10 remove), after the same type check the game makes; plain world objects (flag bit 3) get a rebuilt transform through their own setter (vtable +0x00) or `WorldObject_Remove`. Each edit looks the object up again by handle (`World_ObjectFromHandle`), so a vanished object only gives a message.

The Lua console learns the game's Lua states by hooking `LuaScript_RegisterCommands`, checks a state is still known (`LuaScript_FromState`) before each run, and then, in that state: `luaL_loadbuffer` (first wrapped as an expression whose values come back as one string, then as a plain statement), `lua_pcall`, `lua_tostring` and `lua_settop` to restore the stack. The game keeps only a few long-lived states (two script objects, `0x3717A0` and `0x371848`; the first gets a new state on reload) and mission scripts run inside them, so "Newest script" is the usual choice and the game's script commands work from the console.

While the console has focus it uses `set_tool_window` (port patch 0004): the game ignores game input, so typing doesn't move the player, and in exclusive fullscreen the game steps out to a desktop-sized borderless window so the console can show (as for the F9 panel), returning when F7 hides it. **DISPLAY: BORDERLESS** in the game's Options is the smoothest mode: no switch at all. In exclusive fullscreen at the native 640 x 480, the borderless picture shows the game's real 4:3 shape; a widescreen resolution and aspect avoid that.

**F8** shows the inspector inside the game itself, as a translucent panel at the top left, updated four times a second. The console draws the text with GDI into a premultiplied BGRA image and hands it to `set_overlay` (port patch 0005). The game composites it into every frame after the F9 overlay, so it shows in exclusive fullscreen with no switch and needs no window. Tested in game (2026-11-08).

With the panel showing, **`** (the key left of 1) types Lua into it. Enter runs the line in the newest script, Backspace deletes, Up recalls the last line, and Esc (or F8) stops typing. The panel shows the last lines of Lua output under the inspector. While typing, the console holds the keyboard through `set_text_input` (port patch 0006): the game ignores its input without leaving fullscreen, and the console reads the keys with `read_text_input`. Tested in game (2026-11-08).

Limits: spawning by template and object editing are only in the window (it can be dragged anywhere); in game, use the matching Lua commands (`Mission_Spawn`, `Actor_SetPosition`, ...). Spawned objects get no ground check (they appear 1.5 m above the player's height). Lua lines run at the plugin update point, outside the game's own script calls; `print` output doesn't appear in the console (return values instead). Most world props have no name; the list labels them by their `GeometryFile` property (looked up once per object), and only objects without one show a handle.
