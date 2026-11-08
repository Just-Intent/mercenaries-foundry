# Changes to Mercenaries Recompiled

Mercenaries Foundry needs a few additions to the port's runtime. They live here as patches against the port (MIT; see its license) until they're upstreamed. All were made against port commit `f513ae4` (v1.1.1).

| Patch | What it adds |
|---|---|
| `0001-Plugin-guest-calls-...patch` | Plugin guest calls: `call_guest`, `guest_scratch` and `in_gameplay` on `MercPluginHost`. |
| `0002-Plugin-function-hooks.patch` | Plugin function hooks: `hook_guest`, and the runtime side of the hook wrappers. Also makes `read_guest`/`write_guest` fast enough for hooks on hot functions. |
| `0003-Plugin-script-commands.patch` | Plugin script commands: `add_lua_command`, trampolines at reserved guest addresses, and registration on every script's Lua state. |
| `0004-Plugin-tool-windows.patch` | `set_tool_window`: while a plugin's tool window is open the game leaves exclusive fullscreen and ignores game input, as for the F9 panel. |
| `0005-Plugin-overlays.patch` | `set_overlay`: a plugin image drawn into the game's frames each present, after the F9 overlay (the port's host-overlay composite), so it shows in exclusive fullscreen. |
| `0006-Plugin-text-input.patch` | `set_text_input` / `read_text_input`: a plugin takes the keyboard for an in-game console. The game ignores its input without a display change, and the window procedure hands typing to the plugin. |
| `0007-Launcher-sync-hook.patch` | Before a modded launch, the launcher runs `foundry-sync.cmd` beside it (if present), then reads the mod selection again. `mod sync --install-hook` writes that file, so enabled source mods are built from the player's own game data every time the selection changes. |

See [docs/PLUGINS.md](../docs/PLUGINS.md) for the API.

## Apply and build

From this repository's root in Git Bash, with the port checked out and built once in `external/Mercenaries-Recompiled`:

```bash
git -C external/Mercenaries-Recompiled am ../../port/patches/*.patch
```

Make the function map's named functions hookable. This rewrites the port's generated code (`src/recomp/gen`), so run it again whenever the port regenerates that code:

```bash
python -m mercfoundry port-hooks
```

Re-run CMake's configure step once so it sees the new `foundry_hooks.c`, then build the runtime:

```bash
external/Mercenaries-Recompiled/.venv/Scripts/cmake.exe external/Mercenaries-Recompiled/build/mercenaries
```

```bash
external/Mercenaries-Recompiled/.venv/Scripts/cmake.exe --build external/Mercenaries-Recompiled/build/mercenaries --config Release --target mercenaries_recomp -- -m -v:minimal
```

Only the runtime (`mercenaries_recomp.exe`) changes. Close the game before building. `python -m mercfoundry port-hooks --remove` restores the generated code.
