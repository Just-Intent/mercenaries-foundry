# Plugins that call the game

With the [port patches](../port/README.md), a native plugin for Mercenaries Recompiled can call the game's own functions and hook them, using the addresses and signatures in the [function map](FUNCTION_MAP.md). Tested in game (2026-11-08): a plugin gave the player money through `Player_SetMoney` and spawned a Humvee through `Spawner_SpawnTemplate`; another doubled every money gain (cash pickups included) by hooking `Player_SetMoney`, made `HashName` return its own value for one string, and logged the game's spawns.

## What the host adds

Four functions appended to `MercPluginHost` (older plugins are unaffected; check with `MERC_PLUGIN_HOST_HAS(host, call_guest)`):

| Function | Use |
|---|---|
| `call_guest(ctx, &call)` | Call a game function: `address`, `this_ptr` (ECX, for `__thiscall`), up to 16 32-bit `args` in source order. Results: `eax`, `edx`, and `st0` (the x87 top, for float and double results). Returns 0 and fills `error` if refused. |
| `guest_scratch(ctx, &address, &bytes)` | 12 KB of game memory for this update: put strings, matrices and output structures there and pass their addresses. |
| `in_gameplay(ctx)` | 1 while normal gameplay is running (not menus or loading). |
| `hook_guest(ctx, address, before, after, userdata)` | Run `before` and/or `after` whenever the game calls a function (see Hooks). |

Rules:
- Calls only work inside `on_update`. That runs on the game's own thread at its input checkpoint, the same place the port's developer tools call the game.
- Each call gets a private stack below the game's; the whole guest CPU state is saved and restored around it.
- Pass floats by their bit pattern and pointers as guest addresses. Read and write game memory with `read_guest`/`write_guest`.
- Calling something with the wrong arguments can crash the game, as in any native mod. Prefer functions whose map entry has "retail code" or "live test" evidence.

## Example

```c
#include "mercenaries_plugin.h"
#include "mercfoundry_functions.h"   /* python -m mercfoundry funcmap header */

static uint32_t call1(const MercPluginHost *h, uint32_t fn, uint32_t self, uint32_t arg) {
    MercGuestCall c = {sizeof c, fn, self, 1, {arg}};
    return h->call_guest(h->context, &c) ? c.eax : 0;
}

/* in on_update, once h->in_gameplay(h->context): */
uint32_t scratch, size;
h->guest_scratch(h->context, &scratch, &size);
h->write_guest(h->context, scratch, "player0", 8);
uint32_t player = call1(h, MF_FN_World_FindActorByName, 0, call1(h, MF_FN_HashName, 0, scratch));
float money; h->read_guest(h->context, player + MF_OFF_Actor_money, &money, 4);
money += 1000.0f;
uint32_t bits; memcpy(&bits, &money, 4);
call1(h, MF_FN_Player_SetMoney, player, bits);   /* __thiscall: player in ECX */
```

Spawning an object: check the template is loaded with `Template_GetProperty(templateHash, HashName("name"))` (non-zero when loaded), then call `Spawner_SpawnTemplate` on `MF_VAR_g_Spawner` with the template hash, a name string, a row-major 4x4 transform (right, up, forward, position, w = 1) and `0, 0`. Vehicle templates are named `template_<faction>_<vehicle>`, e.g. `template_allies_humvee_empty`.

## Hooks

`hook_guest` works on every function the map names (574 so far), once `python -m mercfoundry port-hooks` has been run on the port's generated code and the port rebuilt (see [port/README.md](../port/README.md)). It returns 0 for other addresses. Register hooks from init or `on_update`; they last until shutdown.

Callbacks get a `MercHookContext`:

| Field | In `before` | In `after` |
|---|---|---|
| `address` | the hooked function | |
| `ecx` | `this` for methods; may be changed | |
| `esp` | `[esp]` = return address, `[esp + 4]` = first argument; change arguments with `write_guest` | |
| `skip`, `eax`, `edx`, `pop_bytes` | set `skip = 1` to not run the original: give the result in `eax`/`edx` and the bytes of arguments it would pop itself in `pop_bytes` (4 x arguments for `__thiscall`/`__stdcall`, 0 for `__cdecl`) | |
| `eax`, `edx`, `st0` | | the result (`eax`/`edx` may be changed; `st0` is the x87 top, for float results) |

```c
static void MERC_PLUGIN_CALL double_money(void *ud, MercHookContext *c) {
    float current, next; uint32_t bits;
    host->read_guest(host->context, c->ecx + MF_OFF_Actor_money, &current, 4);
    host->read_guest(host->context, c->esp + 4, &next, 4);
    if (next > current) {
        next = current + 2 * (next - current);
        memcpy(&bits, &next, 4);
        host->write_guest(host->context, c->esp + 4, &bits, 4);
    }
}
/* in MercenariesPlugin_Init: */
host->hook_guest(host->context, MF_FN_Player_SetMoney, double_money, NULL, NULL);
```

Hooks run only on the game's main thread (calls from other threads run the original untouched), never re-enter themselves, and may use `read_guest`, `write_guest`, `call_guest` and `log`. Each hooked call costs a callback, so keep hooks on very frequent functions (like `HashName`, called constantly while loading) small. An unhooked wrapper costs one check.

## Building one

`plugins/build.cmd <plugin folder> [mods folder]` generates `mercfoundry_functions.h` from the map, compiles the plugin as an x64 DLL with Build Tools for Visual Studio 2022 and, given a mods folder, installs it with its `mod.ini`. Enable the plugin in the launcher's mod selector; it logs to `plugin-settings/plugins.log`. `plugins/foundry-calls-test` runs five guest-call checks (string argument, actor lookup, money, float result, spawn); `plugins/foundry-hooks-test` hooks money, `HashName` (with a skip) and spawning. Both log each step.
