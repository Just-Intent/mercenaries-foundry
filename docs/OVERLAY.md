# Foundry Console (developer overlay)

A developer window for Mercenaries Recompiled, built as a plugin on the [plugin API](PLUGINS.md). Press **F7** in game to show or hide it, and **F8** for the inspector as a panel drawn inside the game. Tested in game (2026-11-08).

| Part | What it does |
|---|---|
| Inspector | Gameplay state, the player's position, heading, money and hit points, the terrain height under the player (read from the game's loaded heightfield), the surface there and which texture slot it comes from (`Terrain_GetSurfaceAt`; the full surface table is written to the log when it changes), and the game's last five spawns by template name (a hook on `Spawner_SpawnTemplate`). Updates four times a second. |
| Spawn | Search the game's 1,611 object templates by name, type or faction. **Loaded here only** asks the game which ones the current area has loaded (`Template_GetProperty`); only those spawn. **Spawn** (or double-click) places the selected one 8 m ahead of the player. |
| +$10,000 | Adds money through `Player_SetMoney`. |
| Nearby | World objects within 100 m, nearest first (every half second while the window is open), named where the game has a name for them, otherwise labelled by model in brackets (e.g. `[global_militaryboxstack]`). Selecting one shows its handle, flags, position, distance, heading and its properties as the game reports them (objectType, displayName, faction, hitPoints, GeometryFile, ...). Buttons below edit it: **Bring here** (3 m in front of you), **Up / Down 1 m**, **Turn -45 / +45** and **Remove**. |
| Lua | Run a line of Lua in one of the game's Lua states (Enter runs it). An expression shows its values (`2 + 3` shows `= 5`); a statement shows `ok`; errors show Lua's message. |


Script commands it adds, for the Lua box or any script:

- `Foundry_TrafficZone('<zone>')` shows the traffic manager's view of a zone.
  It reads the manager at 0x3DC970, with zones of 0x294 bytes at +0x2C and
  routes of 40 bytes at +0x18B58. It shows the zone's density, whether it's
  enabled, its spawners (template hash, then the resolved handle) and how many
  routes joined it.
- `Foundry_SetHandling('<vehicle>', '<physics key>', value)` changes a
  vehicle's handling while the game runs, through the class's own setter
  (wheeled vehicles' drivetrain, helicopters, tanks; see
  [VEHICLES.md](VEHICLES.md#changing-handling-live)), and returns what it
  did (`wheeled: physicsenginetorque 2400 -> 3000`). The vehicle is named as
  in the game's own script commands. Wheel friction is copied to the tyres
  only at setup. Compiled; not yet run in game (new native builds don't run
  on the PC it was written on).
- `Foundry_MapText('<key>', '<other key>')` shows the other key's text for
  the key, until the next level loads (a `Level_Load` hook puts the game's
  back). It repoints the key's UTF-16 text pointer in the game's string table
  (`Registry_Find(*g_StringTables)`: count at +4, text pointers at +0xC, key
  hashes at +0x10, probed as `HashTable_Find`), and returns true, or nil when
  a key isn't there. A mod's new map with texts of its own (`text/*.json` or
  `factions.json` with `"map"`) calls it from its master script, through the
  generated `foundry_maptext_<map>`. Compiled; not yet run in game.
- `Foundry_HqMode(TRUE)` sets the player up as retail's HQ briefing does
  (Briefing_Init 0x175B10): the weapon put away (0x5CA70(1) on the player as
  a human, 0x8ED00), virtual +0x290(0), and the human's run permission
  (+0x817, which the physics body's +0x65 follows) cleared, so the player
  walks. `Foundry_HqMode(FALSE)` undoes them as Briefing_Deinit does (0x5C950(1)
  takes out the last weapon, +0x290(1), +0x817 set). Returns true. The
  control mode is the script's: `Player_EnableControl(13)` (move, look and
  use only) is the one Briefing_Init sets, and `Player_DisableControl()`
  returns the mode to put back. A new map's HQ interiors call both.
- `Foundry_MapTexture('<texture>', '<other texture>')` makes every HUD and
  PDA sprite drawn from the texture draw from the other one (the same part
  of it) until the next level loads, and returns how many sprites. Sprites
  are 0x20-byte objects (constructor 0x216E60: +0 their name hash, +4 their
  texture's hash, then UVs over the texture's size) in a hash map at
  `MEM32(0x842E20) + 0x10` (+0 size, +8 sprite pointers, +0xC keys); each
  frame the HUD looks a sprite up and turns its +4 hash into a texture with
  Texture_Find (0xC2740), which draws nothing unless that texture is loaded,
  so the command first keeps the other texture resident as the game keeps its
  own HUD textures (0x205D80(hash, 0)) and switches the sprites once it's
  loaded (logged). Used by a new map's `"hud_textures"`. Compiled; not yet
  run in game.
- `Foundry_MapTemplate('<template>', '<other template>')`: until the next
  level loads, anything spawning the template by name (the game's support
  code, scripts, the shop) spawns it with the other's properties. In place:
  the template's entry in g_TemplateTable (0x4D0068: +8 property blocks,
  +0x4008 their hashes) points at the other's block, so the spawn keeps the
  template's name and hash (the extraction support code goes by them: with
  the spawner's hash swapped instead, until 0.251, the Black Hawk hovered
  5 m up). Placed once the other's layer has loaded (checked each second
  and at each spawn of the template, a "before" hook on
  Spawner_SpawnTemplate 0x174480), put back before the next level loads;
  when the other isn't in the table, the spawn's hash is swapped as before.
  Logged. Used by a new map's `"template_overrides"`.
- `Foundry_MapCaptives(true)`: until the next level loads, every subdued
  human counts as a captive the way a verifiable target (IsVerifiable) does.
  It sets the game's own switch for that, the byte at 0x32079C: the game
  reads it beside each human's IsVerifiable (+0x808) at 15 places (0x3EC60
  is "IsVerifiable or the switch"; the rider code's seat checks at 0x166B90
  and 0x1676E0 among them) and never writes it (a debug setting whose menu
  is gone; the port shows it only for a live subdued human, so the dead
  and the verify prompt stay as the game has them). Turned on 15 s after the call, not during the load (logged
  "map captives: on now"); `Foundry_MapCaptives(true, 0)` at once.
  Cleared before the next level loads. Used by a new map's
  `"any_captive"`. Not yet run in game: what verifying such a captive does
  (the PDA, pay) is to be seen.
- Card probe (logged with the ground probe, every 2 s): once per card man
  in range (a name with "card" or "deck_") and once per unnamed Gaza
  character model, "card probe: <name> draws <GeometryFile>; <model>_head
  loaded | not in the texture table; <model>_body ...". For copied models
  whose own textures don't show (Gaza's card men, 0.262).
- `Foundry_MapFines(false)`: until the next level loads, no fine for a
  death the player causes (0x56FD0's $100), through a byte the port reads
  (0x87BF00). Used by a new map's `"collateral_fines": false`.
- `Foundry_FindPlaced('<model>', n)`: the n-th live object within 150 m of the
  player whose GeometryFile is that model, as handle, x, y, z (nothing if
  none): for objects scripts can't name, such as a placed charge.
  `Foundry_MoveObject(handle, x, y, z, yaw?)` moves one (degrees); true if it
  could.
- `Foundry_Log('<text>')`: writes a script's line to plugins.log. A new
  map's HQ interiors log their measurements with it (`hq dump ...` lines:
  `foundry_<map>_HqDump`, `foundry_HqDump()` in the Lua box). Retail's HQ
  briefings are measured by the plugin itself: from `Briefing_Init`, every
  2 s for 30 s and once at `Briefing_Deinit`, `hq dump retail` lines give the
  player's, chair's, exit's, starter's, boss's and the scene's positions and
  yaws (the player and chair are attached to the scene's `hp_player_chair`).
  Compiled; not yet run in game.
- `Foundry_Range()` runs the [test range](ENCOUNTERS.md#test-range)'s
  `range.lua` in the calling script state. It looks for the file in
  `mods\Foundry Test Range` first, then in the launcher's runtime copy, where
  each mod's top-level files are copied.
- `Foundry_Sample(1)` / `Foundry_Sample(0)` turns the function sampler on or
  off (`Foundry_Sample()` reports), and `Foundry_Label('tank')` labels its
  samples (`''` goes back to `gameplay` / `menu`). See
  [FUNCTION_MAP.md](FUNCTION_MAP.md#finding-candidates-by-playing-passive-function-mapper).

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

Two more live commands, written 2026-11-15 and compiled, but **not yet run in game** (new native builds don't run on the development PC; build with `plugins/build.cmd plugins/foundry-console <mods folder>` and test elsewhere):
- `lua <line>`: the line runs in the newest script's state, as the Lua box runs it; the reply is its values, "ok" or the error. Loading an already-built layer live is `Utility_ReadLevelFile('<layer>')` (the game stalls while it reads it).
- `heights <check col> <row> <units> <col row units>...` (up to 64 a line): terrain height samples in height units. The check is one sample's height before the change: the console refuses the line unless the game has that height there (within the block's rounding), since it can't tell which map the game is on otherwise. Each 16 x 16 block touched is decoded from the game's buffers (`*0x64326C` bytes, `*0x643274` {shift, base}), changed, and encoded again with the smallest shift that fits; collision reads those buffers directly. Then every cached drawn patch (64 slots at `*(0x643370 + 0x14)`: keys at +8, dirty bytes at +0xC8) over a changed cell or a neighbour is emptied (key 0xFFFF, as the game's own 0x1F0080 does), so the next frame builds it again. The cache is checked first (keys within the map's patches); if it doesn't look right, only the heights change and the ground redraws when the area does. See [FEASIBILITY.md](FEASIBILITY.md#research-2026-11-15-live-layers-and-live-terrain).
- `paint <check col> <row> <units> <patch col> <row> <mask hex> [<alpha hex>]` (written 0.132, compiled, not run in game): a ground patch's paint. The editor sends each patch a paint stroke touched (with its neighbours on shared edges) and the server encodes it as the build does (`terrain_paint.patch_alpha`). Each patch has a 16-byte record (`*0x64314C`, row by row: +0 a pointer to its ALPH blob, +8 its layer mask); the console writes the blob raw (u16 stored = unpacked, then 45 bytes a layer) where the old one was if it fits, else in a block from the game's allocator (0x1F7230, kept until the level unloads), then the mask, then empties the patch's drawn cache slot. Only layers the map's ground already uses somewhere are taken (a layer its LTEX doesn't define might crash the drawing), and the same height check as `heights` refuses an edit made on another map. Command lines are now up to 1600 characters.

## How it works

The window runs on its own thread. Everything that touches the game is queued and done in the plugin's `on_update`, on the game's thread, with `call_guest` and the [function map](FUNCTION_MAP.md): `World_FindActorByName("player0")`, the actor fields `transform`, `money` and `hitPoints`, `Template_GetProperty`, `Spawner_SpawnTemplate` and `Player_SetMoney`.

The object inspector reads the world's tables directly: 16,384 object slots (`g_WorldObjects`, with handles in `g_WorldObjectHandles`), each object's flags, position (+0x08) and rotation (+0x14), and the named-object table (`g_WorldNameKeys` / `g_WorldNameHandles`) read backwards to name objects by hash against `objectnames.txt`. Properties come from the object's own lookup (vtable +0x0C, by name hash).

Edits use the same engine calls as the game's `Actor_SetPosition`, `Actor_SetYaw` and `Actor_Remove`: objects backed by a game actor (most things) through the actor's vtable (+0x64 set position, +0x68 set yaw in radians, +0x10 remove), after the same type check the game makes; plain world objects (flag bit 3) get a rebuilt transform through their own setter (vtable +0x00) or `WorldObject_Remove`. Each edit looks the object up again by handle (`World_ObjectFromHandle`), so a vanished object only gives a message.

The Lua console learns the game's Lua states by hooking `LuaScript_RegisterCommands`, checks a state is still known (`LuaScript_FromState`) before each run, and then, in that state: `luaL_loadbuffer` (first wrapped as an expression whose values come back as one string, then as a plain statement), `lua_pcall`, `lua_tostring` and `lua_settop` to restore the stack. The game keeps only a few long-lived states (two script objects, `0x3717A0` and `0x371848`; the first gets a new state on reload) and mission scripts run inside them, so "Newest script" is the usual choice and the game's script commands work from the console.

While the console has focus it uses `set_tool_window` (port patch 0004): the game ignores game input, so typing doesn't move the player, and in exclusive fullscreen the game steps out to a desktop-sized borderless window so the console can show (as for the F9 panel), returning when F7 hides it. **DISPLAY: BORDERLESS** in the game's Options is the smoothest mode: no switch at all. In exclusive fullscreen at the native 640 x 480, the borderless picture shows the game's real 4:3 shape; a widescreen resolution and aspect avoid that.

**F8** shows the inspector inside the game itself, as a translucent panel at the top left, updated four times a second. The console draws the text with GDI into a premultiplied BGRA image and hands it to `set_overlay` (port patch 0005). The game composites it into every frame after the F9 overlay, so it shows in exclusive fullscreen with no switch and needs no window. Tested in game (2026-11-08).

With the panel showing, **`** (the key left of 1) types Lua into it. Enter runs the line in the newest script, Backspace deletes, Up recalls the last line, and Esc (or F8) stops typing. The panel shows the last lines of Lua output under the inspector. While typing, the console holds the keyboard through `set_text_input` (port patch 0006): the game ignores its input without leaving fullscreen, and the console reads the keys with `read_text_input`. Tested in game (2026-11-08).

A line starting with **/** is a panel command instead of Lua, for what the window does when it can't show (exclusive fullscreen): `/spawn <template>` (the whole name, or a part only one template has; 8 m ahead, as the window's Spawn, if it's loaded here), `/near` (the eight nearest objects, numbered), `/pick <n>`, then `/up`, `/down` (1 m), `/left`, `/right` (45 degrees), `/here` (3 m ahead of the player) and `/remove` on the picked object, `/money [n]` (default 10,000), `/objectives` (the game's objectives as it holds them: how many of its 16 are used, and each one's targets with their type, GPS / PDA / world bits and position; names it doesn't know show as #hash) and `/help`. They use the window's own spawn and edit code. Compiled; not yet run in game.

Limits: Spawned objects get no ground check (they appear 1.5 m above the player's height). Lua lines run at the plugin update point, outside the game's own script calls; `print` output doesn't appear in the console (return values instead). Most world props have no name; the list labels them by their `GeometryFile` property (looked up once per object), and only objects without one show a handle.
