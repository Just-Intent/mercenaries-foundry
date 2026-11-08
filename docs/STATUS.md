# Status (2026-11-08)

## Done
- Project set up: `mercfoundry/` Python tools, docs, MIT license, `.gitignore` that blocks game data.
- Upstream port cloned to `external/Mercenaries-Recompiled` (ignored by git; at commit `f513ae4`, v1.1.1).
- `.dsk` tool: info, list, tree, extract, pack, diff, hash, verify, names, unpack-iso.
- Decoders: Lua scripts (decompress and recompress), world layers (object properties as JSON), localized text (edit and add strings). `extract --decode`, then `pack` re-encodes edited files.
- **Checked on retail data** (US Xbox, Redump MD5 `e340535feeadc0ff9b71506c625bf288`):
  - All four archives rebuild byte-for-byte.
  - All 208 scripts, 824 world layers and 91 text tables (6,905 strings) rebuild exactly from their decoded form.
  - Editing a vehicle name and adding a new string works and changes only that record.
  - 9,618 of 9,723 hashes named from strings in the game; every record type now has a name.
  - Model geometry (`segments`) uses the script compression; all 2,954 records decompress.
- Model decoder: all 2,297 models decode (1.35 million triangles): skeleton, joint placement, levels of detail, pristine/damaged panels, normals, texture coordinates, vertex colours, texture names. `model` exports OBJ plus a preview image. 2,285 models match their stored bounds exactly; the other 12 differ only around helicopter rotors.
- Texture decoder: all 4,715 textures (DXT1, DXT2/3, swizzled A8R8G8B8, 4 cube maps). `texture` exports PNG; `model` now exports textures with the OBJ and draws them in its preview, which confirms the texture coordinates.
- Texture re-import: PNG reader, DXT1/DXT3 and swizzled A8R8G8B8 encoders with mip generation, hooked into `extract --decode --textures` and `pack`. Re-encoding the game's own textures is lossless for 97% of a sample. `rebuild` now tracks decoded-file hashes and restores originals when an edit is undone.
- Model import: `model-import` brings an edited OBJ back (edit, add and remove pieces; positions re-quantized; strips rebuilt; geometry recompressed). Unedited exports re-import byte-identical for every model, level of detail and damage state.
- `mod new` / `mod build`: a mod is a folder of text JSON, Lua scripts, object property rules, PNG textures and OBJ models; one command builds the changed archives, verifies them and writes a summary. Rebuilding the day's hand-made test mod from a project gave byte-identical assets, locked and streamed archives (english.dsk identical in content). Example project in `examples/faster-humvee`.
- Collision: `cshp` decoded for every model (2,067 box shapes with 28,294 boxes; 149 of 325 mesh shapes). New pieces from `model-import` get box collision (opt out per object with `nocoll` in the name). `model --collision` previews collision.
- glTF export (`model --glb`): joints as a node tree, rigid pieces parented to joints, skinned pieces with a skin (bone palette = joints named `bone*`), embedded textures with alpha masking. All 2,297 models export and pass a vertex-by-vertex check; three.js loads and renders them, posing bones moves the right parts, and sign lettering confirms the handedness conversion.
- Object positions: world-layer `XFRM` decoded (rotation + position in metres) for all 51,731 objects; layer and object `INFO` counts understood and recomputed. Mod object rules can move, place, turn and copy objects, matching by name, model or position.
- `find`: lists world objects by name, model, layer, property, or distance from a point or named object; `--rule` prints a mod rule that matches exactly that object. Scans all 51,731 objects in under a second.
- Paths, boundaries, regions, encounters and load lists decoded with exact rebuild (all 3,329 records; counts recomputed). `layout` exports a layer's data, mods replace it via `layout/`, `find --layout` searches it, and `verify` checks it.
- Animation decoder: all 1,835 animations (keyframe compression, per-frame translation, root motion); `model --glb --anim` exports them as glTF clips, `--in-place` drops root motion. Checked against skeleton rest poses and played back in three.js.
- Animation encoder and glTF clip import: every retail rotation channel re-encodes byte-identical; a glTF export reads back to the same values. Mods replace clips via `animations/`.
- glTF model import: `model-import` and mod `models/` take `.glb`/`.gltf`. Pieces are placed by the file's node and skin transforms, new pieces take the joint they're parented to, and skinned vertices keep their bone. Every model round-trips unchanged, including Blender-style restructured files.
- Collision trees (`CMOP`) decoded from the game's own walker and checked on all 1,402; convex hulls decoded; `cinf` totals understood on all 1,997 blocks. New pieces on Humvee-style vehicles get collision by turning the convex hull into boxes.
- Animation tables decoded (all 95 rebuild exactly; `animtable` exports one). Mods can add clips under new names (`animations/new.json`, built on a template clip and added to its load lists) and repoint handles (`animtables/`).
- Function map (`map/functions.json`, `funcmap` command): every script command's C function from the executable's Lua registration tables, source files from assert strings (80 files, 1,500 functions), 22 hand-named functions (Lua argument helpers, Lua C API, `HashName`, `Spawner_SpawnTemplate`, faction relations) and 3 globals. Exports a C header and the port's `ghidra_names.json` format.
- Function map, second pass: 573 named functions. 128 command implementations named by rule (a binding's only, unshared engine call); 47 hand-named with evidence (actor lookup by name and handle, money, AI command queue, object removal, collision toggling, objectives, string tables, the engine's hash table); 8 globals; structure fields for world objects, actors, AI and vehicles.
- Plugin guest calls (port patch `port/patches/0001`): `call_guest`, `guest_scratch`, `in_gameplay`. The test plugin `plugins/foundry-calls-test` passed all five checks in game. Five map entries now carry live-test evidence, plus `Template_GetProperty`.
- Plugin function hooks (port patch `0002` plus `port-hooks`): all 574 mapped functions hookable; before/after callbacks, argument changes, skip with a result. `plugins/foundry-hooks-test` passed in game.
- Plugin script commands (port patch `0003`): `add_lua_command`; up to 64 commands registered on every script's Lua state. `plugins/foundry-lua-test` passed in game.
- Developer overlay, first slice: the Foundry Console plugin (F7): inspector (gameplay state, position, heading, money, hit points, recent spawns) and spawn-by-name for any template, with a check for which are loaded here. `templates` command lists the game's 1,611 templates.
- Lua console in Foundry Console: runs a line in a game Lua state (expression values or ok, errors caught, stack restored). Ten more Lua API functions mapped (luaL_loadbuffer, lua_pcall, lua_settop, ...).
- Object inspector in Foundry Console: nearby objects from the world's object table (position at +0x08, rotation at +0x14), named through the named-object table, with properties from the object's own lookup. `objectnames` command.
- Object editing in Foundry Console: bring here, up/down, turn and remove, through the engine calls behind Actor_SetPosition, Actor_SetYaw and Actor_Remove.
- Plugin tool windows (port patch `0004`): Foundry Console works in exclusive fullscreen by stepping the game out of it while the console has focus, and the game ignores input then.
- Terrain decoded: heights (`HEXP`/`HGT8`), layers, baked lighting, blend weights and decals, all 14 maps rebuilding exactly. `terrain` exports heights as a 16-bit PNG; mods reshape ground via `terrain/`. Tested in game: a 30 m hill appeared in the first level (map `swn`). Foundry Console shows the terrain height under the player.
- Terrain relighting: mod builds fit each map's baked sun and reshade edited slopes and their shadows, keeping painted tint.
- 159 tests pass.

## Fixed from retail data
- Text values aren't NUL-terminated. The decoder had assumed they were and found no strings.
- Text header layout is now known, so new strings can be added.

## In-game tests (all passed)
- 2026-11-08, text: an `english.dsk` rebuilt by our tools, installed as `mods/Foundry Test/`, showed edited "PRESS START" and "NEW GAME" labels. The edited `veh.allies_apache` name isn't shown during play (the player spawns in it), so it's unconfirmed; it may appear in the shop.
- 2026-11-08, script, object and added text, in the opening mission (`allies0`, after the C-17 briefing):
  - A recompressed `allies0` script with two new functions ran: its message box text appeared on schedule.
  - A newly added text key (`foundry.test.scriptmsg`) resolved and displayed.
  - Raising `physicsenginetorque` from 850 to 3400 on `player_humvee` (in `swn_hq`) and the Humvee templates made the Humvee visibly faster.
- So the game accepts our compressor's output, rebuilt world layers with edited properties, and added text entries (with `num_components` raised).
- 2026-11-08, texture: `allies_avehicle_humvee_001` tinted red, re-encoded by our DXT3 encoder with a full mip chain and packed into `streamed.dsk`, showed on the player's Humvee in the opening mission.
- 2026-11-08, collision: two fins added to the Humvee, one with a generated box and one marked `nocoll`. Kind-0 boxes had no effect on the vehicle; switched to kind 2, the solid fin collided and the `nocoll` fin didn't.
- 2026-11-08, boundaries: `allies0_killbox` (layer `swn`) replaced with a 60 m square around the start via a mod `layout/` file. With Skip Intro, leaving the square triggered the restricted-area response: fighter jets killed the player with missiles. Edited boundaries are live in-game.
- 2026-11-08, object positions: in layer `swn`, `loc_drop_start` raised 5 m and a nearby rock copied four times in a ring 9 m around it (each turned differently). With Skip Intro, the Humvee spawned higher and the four new rocks appeared (it landed on one; the rock is wider than the spacing). Moving and adding objects both work.
- 2026-11-08, collision appended into an existing shape: `allies_veh_cargotruck` (one plain box shape, 8 boxes) got two fins; the solid fin's box went into its existing shape (9 boxes, still one shape). Spawned with the F9 developer menu: the solid fin collided, the `nocoll` fin didn't, and the truck drove and crashed without sinking.
- 2026-11-08, Humvee sinking, isolated by elimination: not Skip Intro (plain Humvee fine), not the 4x engine, not the enlarged bounds; the generated collision boxes. A single 5 cm box still caused it, so the cause is a third collision shape, not size or mass. No game vehicle has more than two. Importer now appends to an existing plain box shape, adds a shape only below two, and otherwise skips with a warning.
- 2026-11-08, model: `allies_veh_humvee` exported to OBJ, edited (gun barrel pieces stretched 3x, a new crate piece added with the Humvee texture), re-imported with `model-import` into `locked.dsk` and `streamed.dsk`. Both changes showed on the Humvee driven after the airdrop.
- 2026-11-08, animation: the three mercenaries' `lb_upright_run_forward` clips exported to glTF, both thighs turned 40° outward, and dropped into a mod's `animations/` folder. The run was bow-legged in game. (The first try turned them inward and gave a cross-legged run, so a thigh's local +x turn is inward on the left leg.)
- 2026-11-08, glTF model: a top hat added to `prokat_hum_american`, `_british` and `_swedish` as a new glTF node under `bone_head` (named `nocoll`, so no collision). In game the hat sat on the mercenary's head and moved with it.
- 2026-11-08, Humvee collision: boxes added as triangles to the hull mesh (tree rebuilt) didn't collide; the triangle hull isn't used against the world. Replacing the convex hull with 10 boxes plus the new one worked: the front plate stopped the Humvee at walls, the `nocoll` rear plate passed through, and it didn't sink.
- 2026-11-08, new animations: cross-legged copies of each mercenary's run added as new clips and pointed to by their animation tables; the run played cross-legged while the original clip was unchanged.
- 2026-11-08, plugin guest calls: from a native plugin, `HashName("player0")` matched, `World_FindActorByName` found the player, `Player_SetMoney` added $1,000,000 (shown on the HUD), `atof("12.5")` returned 12.5 on the x87 stack, and `Spawner_SpawnTemplate` spawned `template_allies_humvee_empty` 10 m ahead. Guessed template names (`player_humvee`) weren't loaded; vehicle templates are `template_<faction>_<vehicle>`.
- 2026-11-08, plugin hooks: hooking `Player_SetMoney` doubled the calls test's +$1,000,000 to $2,000,000 and each $2,000 cash pickup to $4,000; a `HashName` hook skipped the original for "foundry_skip_test" (returned 0x12345678) while "player0" still hashed normally; a `Spawner_SpawnTemplate` hook logged the game's spawns. Loading slowed down with the `HashName` hook because `read_guest` called VirtualQuery on every access; with guest RAM checked once and plugin buffers cached, loading was back to normal speed (retested).
- 2026-11-08, plugin script commands: a plugin added `Foundry_Add`, `Foundry_GiveMoney` and `Foundry_Log`; an edited `allies0` script called them after the first objective and showed "2 + 3 = 5, money now 500" on screen, and the log line arrived in plugins.log.
- 2026-11-08, Foundry Console: F7 opened it in game; the inspector updated, the template search and "Loaded here only" worked, and spawning from the list placed objects ahead of the player.
- 2026-11-08, Lua console: `2 + 3` in the console returned `= 5` in the game's Lua state. The game registered commands only three times (script objects 0x3717A0, twice, and 0x371848): mission scripts share a few long-lived states.
- 2026-11-08, object inspector: the nearby list showed objects within 100 m with names where they have one (loc_hospital, c_race_HQm2HQa_01_gate16b); a static rock reported objectType static, hitPoints 1, GeometryFile summer_env_rock01. Unnamed objects are labelled by model (the player's own gear at 1 m, pickups such as global_pic_ammo, props such as global_militaryboxstack with hitPoints 3000).
- 2026-11-08, object edits: on nearby props, Bring here, Up/Down 1 m, Turn ±45 and Remove all worked.
- 2026-11-08, console in fullscreen: with DISPLAY: FULLSCREEN, F7 blinked the screen, the game switched to a borderless window and the console appeared over it. At the native 640 x 480 the picture turned 4:3 (the game's real aspect; exclusive mode had the monitor stretch it).
- Lesson: the C-17 cargo-hold scene uses a separate model, `c17_humvee`, which shares the Humvee texture. Check model edits on the vehicle you drive.
- Lesson: a new game starts in `playable_intro` (C-17 briefing, then `allies0`), not the Ace of Clubs, which is Allied mission 4. Script tests belong there.

## Port build (2026-11-08)
- Built Mercenaries Recompiled `f513ae4` from the disc image with `Build-From-Iso.ps1`: about 14,000 functions translated, then compiled with MSVC 19.44 (v143) and SDK 10.0.26100.0. Output: `external/Mercenaries-Recompiled/build/mercenaries/bin/Release/` (`Mercenaries Recompiled.exe` launcher, `mercenaries_recomp.exe` runtime, linker `.map`).
- Gotcha: in Windows PowerShell 5.1, don't capture the script's output with `*>&1`. Warnings from Python then become fatal errors. Run it in its own process and redirect there: `cmd /c 'powershell -File ... > log 2>&1'`.
- The extraction step refuses to unpack over an existing `game_files/mercenaries-retail`; delete it first when rerunning.

## Toolchain (ready)
- Build Tools for Visual Studio 2022 17.14 (free) at `C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools`, with the pinned MSVC 14.44.35207 and Windows SDK 10.0.26100.0. No paid Visual Studio edition is needed.
- Python 3.12.10 alongside 3.13. The port's `.venv` has its pinned CMake 3.31.6, Ninja 1.13.0 and Capstone 5.0.6.

## Next steps
1. Overlay: draw inside the game's frames (no mode switch); edit object properties.
2. Function map: name more engine functions (actors, vehicles, AI commands, missions).
3. Collision material codes (surface types); collision for edited existing pieces.
4. Importing skeleton (joint) edits; what animation-table priorities and flags do.
5. Path junctions (`JNCT`) and squad types: where `SQD_` names like `nk_hum_ppl_tunnel01` are defined.

## Notes
- Several port tools mention replacing "source-informed" versions. Use only versions re-derived from retail (see PROVENANCE.md).
- The port has a debug-symbol tool (`tools/debug_symbols`) that recovers original source file names from strings in the executable. That's useful for the function map.
