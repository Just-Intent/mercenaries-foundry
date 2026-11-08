# Foundry Console (developer overlay)

A developer window for Mercenaries Recompiled, built as a plugin on the [plugin API](PLUGINS.md). Press **F7** in game to show or hide it. Tested in game (2026-11-08).

| Part | What it does |
|---|---|
| Inspector | Gameplay state, the player's position, heading, money and hit points, and the game's last five spawns by template name (a hook on `Spawner_SpawnTemplate`). Updates four times a second. |
| Spawn | Search the game's 1,611 object templates by name, type or faction. **Loaded here only** asks the game which ones the current area has loaded (`Template_GetProperty`); only those spawn. **Spawn** (or double-click) places the selected one 8 m ahead of the player. |
| +$10,000 | Adds money through `Player_SetMoney`. |

## Install

Needs the port with the Foundry patches and hook wrappers ([port/README.md](../port/README.md)). Then, from this repository's root (PowerShell):

```powershell
$mods = "external\Mercenaries-Recompiled\build\mercenaries\bin\Release\mods"
cmd /c "plugins\build.cmd plugins\foundry-console `"$mods\Foundry Console`""
python -m mercfoundry templates game_files/DATAxbox -o "$mods\Foundry Console\templates.tsv"
```

The template list comes from your own game data, so it is generated at install time and never committed. Enable **Foundry Console** in the launcher's mod selector and restart the game.

## How it works

The window runs on its own thread. Everything that touches the game is queued and done in the plugin's `on_update`, on the game's thread, with `call_guest` and the [function map](FUNCTION_MAP.md): `World_FindActorByName("player0")`, the actor fields `transform`, `money` and `hitPoints`, `Template_GetProperty`, `Spawner_SpawnTemplate` and `Player_SetMoney`.

Limits: it is a separate window, so it shows over a windowed or borderless game but not exclusive fullscreen, and keys typed into it may also reach the game. Spawned objects get no ground check (they appear 1.5 m above the player's height). Next: a Lua console that runs script lines in a mission's Lua state.
